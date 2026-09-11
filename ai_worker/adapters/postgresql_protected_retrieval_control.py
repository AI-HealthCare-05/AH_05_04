"""PostgreSQL application service for protected authorization control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ai_worker.adapters.postgresql_protected_retrieval import (
    PostgresqlProtectedAuditJournal,
    PostgresqlTrustedClock,
    _json_value,
    _model,
    _ProtectedSession,
)
from ai_worker.tasks.evaluation.protected_retrieval import (
    ActorIdentity,
    ApprovalSourceEvidence,
    AuthorizationAuditAction,
    AuthorizationAuditEntry,
    ControlAuditOutcome,
    ControlAuditTargetKind,
    ControlCommandAuditEntry,
    ProtectedApprovalPrincipal,
    ProtectedApprovalRole,
    ProtectedAuditEntry,
    ProtectedAuditEventKind,
    ProtectedAuditReason,
    ProtectedAuthorizationGrant,
    ProtectedDatasetBinding,
    ProtectedPrincipal,
    ProtectedSecurityError,
    audit_entry_sha256,
    new_event_id,
)
from ai_worker.tasks.evaluation.protected_retrieval_control import (
    ControlCommandKind,
    ControlCommandResult,
    ExpireAuthorizationCommand,
    GrantAuthorizationCommand,
    IngestApprovalCommand,
    RevokeAuthorizationCommand,
    TrustedApprovalSource,
    control_command_sha256,
    verify_authorization_approval,
)
from infra.python.protected_retrieval_role_policy import validate_protected_control_connection


@dataclass(frozen=True)
class _ControlExecutor:
    actor: ActorIdentity
    principal: ProtectedApprovalPrincipal


type _SuccessReason = Literal["APPROVAL_VERIFIED", "AUTHORIZED", "REVOKED", "EXPIRED"]


def _success_reason(command_kind: ControlCommandKind) -> _SuccessReason:
    reasons: dict[ControlCommandKind, _SuccessReason] = {
        ControlCommandKind.INGEST_APPROVAL: "APPROVAL_VERIFIED",
        ControlCommandKind.GRANT: "AUTHORIZED",
        ControlCommandKind.REVOKE: "REVOKED",
        ControlCommandKind.EXPIRE: "EXPIRED",
    }
    return reasons[command_kind]


class _ControlSession(_ProtectedSession):
    def __init__(
        self,
        session: AsyncSession,
        schema: str,
        clock: PostgresqlTrustedClock,
    ) -> None:
        super().__init__(session, schema)
        self._clock = clock

    async def resolve_executor(self) -> _ControlExecutor:
        result = await self._execute(
            f"""
            SELECT actor_id, actor_namespace, approval_role
            FROM {self._schema}.protected_identity
            WHERE database_login = session_user::name
              AND identity_plane = 'CONTROL'
              AND enabled
            """
        )
        row = result.one_or_none()
        if row is None:
            raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
        actor = _model(
            ActorIdentity,
            {"actor_id": row.actor_id, "namespace": row.actor_namespace},
            "AUTHORIZATION_NOT_FOUND",
        )
        try:
            role = ProtectedApprovalRole(row.approval_role)
        except ValueError:
            raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND") from None
        return _ControlExecutor(
            actor=actor,
            principal=ProtectedApprovalPrincipal(actor=actor, role=role),
        )

    async def verified_entries(self, *, lock_head: bool) -> tuple[ProtectedAuditEntry, ...]:
        journal = PostgresqlProtectedAuditJournal(self._session, self._schema_name, self._clock)
        return await journal._verified_entries(lock_head=lock_head)

    async def replay(
        self,
        *,
        request_id: str,
        command_kind: ControlCommandKind,
        command_sha256: str,
        lock_head: bool,
    ) -> ControlCommandResult | None:
        entries = await self.verified_entries(lock_head=lock_head)
        matching = [
            entry
            for entry in entries
            if isinstance(entry, ControlCommandAuditEntry) and entry.event_id == request_id
        ]
        if not matching:
            return None
        if len(matching) != 1:
            raise ProtectedSecurityError("AUDIT_UNAVAILABLE")
        entry = matching[0]
        if entry.command_kind != command_kind.value or entry.command_sha256 != command_sha256:
            raise ProtectedSecurityError("CONTROL_COMMAND_CONFLICT")
        if entry.outcome is ControlAuditOutcome.DENIED:
            raise ProtectedSecurityError(entry.reason_code.value)
        reason_code = _success_reason(command_kind)
        if entry.reason_code.value != reason_code:
            raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
        return ControlCommandResult(
            request_id=request_id,
            command_kind=command_kind,
            target_id=entry.target_id,
            effective_revision=entry.result_effective_revision,
            authorization_audit_event_id=entry.authorization_audit_event_id,
            reason_code=reason_code,
        )

    async def append_control(
        self,
        *,
        entries: tuple[ProtectedAuditEntry, ...],
        request_id: str,
        command_kind: ControlCommandKind,
        executor: ActorIdentity,
        target_kind: ControlAuditTargetKind,
        target_id: str,
        command_sha256: str,
        outcome: ControlAuditOutcome,
        effective_revision: int | None,
        authorization_audit_event_id: str | None,
        reason_code: ProtectedAuditReason,
    ) -> ControlCommandAuditEntry:
        previous = entries[-1].entry_sha256 if entries else None
        entry = ControlCommandAuditEntry(
            event_kind=ProtectedAuditEventKind.CONTROL,
            sequence=len(entries) + 1,
            event_id=request_id,
            command_kind=command_kind.value,
            executed_by=executor,
            target_kind=target_kind,
            target_id=target_id,
            command_sha256=command_sha256,
            outcome=outcome,
            result_effective_revision=effective_revision,
            authorization_audit_event_id=authorization_audit_event_id,
            reason_code=reason_code,
            recorded_at=self._clock.now_utc(),
            previous_entry_sha256=previous,
            entry_sha256="0" * 64,
        )
        entry = entry.model_copy(update={"entry_sha256": audit_entry_sha256(entry)})
        await self._execute(
            f"""
            INSERT INTO {self._schema}.audit_entry (
                sequence, event_id, event_kind, operation_key, entry_body,
                previous_entry_sha256, entry_sha256, recorded_at
            ) VALUES (
                :sequence, CAST(:event_id AS uuid), 'CONTROL', NULL, CAST(:entry_body AS jsonb),
                :previous_entry_sha256, :entry_sha256, :recorded_at
            )
            """,
            {
                "sequence": entry.sequence,
                "event_id": entry.event_id,
                "entry_body": _json_value(entry),
                "previous_entry_sha256": entry.previous_entry_sha256,
                "entry_sha256": entry.entry_sha256,
                "recorded_at": entry.recorded_at,
            },
            fallback="AUDIT_CAS_CONFLICT",
        )
        updated = await self._execute(
            f"""
            UPDATE {self._schema}.audit_head
            SET sequence = :sequence, entry_sha256 = :entry_sha256
            WHERE singleton AND sequence = :previous_sequence
              AND entry_sha256 IS NOT DISTINCT FROM :previous_entry_sha256
            RETURNING sequence
            """,
            {
                "sequence": entry.sequence,
                "entry_sha256": entry.entry_sha256,
                "previous_sequence": entry.sequence - 1,
                "previous_entry_sha256": entry.previous_entry_sha256,
            },
            fallback="AUDIT_CAS_CONFLICT",
        )
        if updated.scalar_one_or_none() != entry.sequence:
            raise ProtectedSecurityError("AUDIT_CAS_CONFLICT")
        return entry

    async def append_authorization(
        self,
        *,
        entries: tuple[ProtectedAuditEntry, ...],
        grant: ProtectedAuthorizationGrant,
        action: AuthorizationAuditAction,
        effective_revision: int,
        reason_code: ProtectedAuditReason,
        evidence: ApprovalSourceEvidence | None = None,
    ) -> AuthorizationAuditEntry:
        previous = entries[-1].entry_sha256 if entries else None
        entry = AuthorizationAuditEntry(
            event_kind=ProtectedAuditEventKind.AUTHORIZATION,
            sequence=len(entries) + 1,
            event_id=new_event_id(),
            grant_id=grant.grant_id,
            grant_revision=grant.revision,
            effective_revision=effective_revision,
            subject=grant.subject,
            issuer=grant.issuer,
            dataset_id=grant.dataset_id,
            dataset_version=grant.dataset_version,
            manifest_sha256=grant.manifest_sha256,
            protected_artifact_sha256=grant.protected_artifact_sha256,
            hmac_key_version=grant.hmac_key_version,
            actions=grant.actions,
            control_implementation=grant.control_implementation,
            approval_source_event_id=(evidence.source_event_id if evidence is not None else grant.approval_source_event_id),
            approval_source_raw_sha256=(
                evidence.canonical_raw_sha256 if evidence is not None else grant.approval_source_raw_sha256
            ),
            valid_from=grant.valid_from,
            expires_at=grant.expires_at,
            action=action,
            reason_code=reason_code,
            recorded_at=self._clock.now_utc(),
            previous_entry_sha256=previous,
            entry_sha256="0" * 64,
        )
        entry = entry.model_copy(update={"entry_sha256": audit_entry_sha256(entry)})
        await self._execute(
            f"""
            INSERT INTO {self._schema}.audit_entry (
                sequence, event_id, event_kind, operation_key, entry_body,
                previous_entry_sha256, entry_sha256, recorded_at
            ) VALUES (
                :sequence, CAST(:event_id AS uuid), 'AUTHORIZATION', NULL, CAST(:entry_body AS jsonb),
                :previous_entry_sha256, :entry_sha256, :recorded_at
            )
            """,
            {
                "sequence": entry.sequence,
                "event_id": entry.event_id,
                "entry_body": _json_value(entry),
                "previous_entry_sha256": entry.previous_entry_sha256,
                "entry_sha256": entry.entry_sha256,
                "recorded_at": entry.recorded_at,
            },
            fallback="AUDIT_CAS_CONFLICT",
        )
        updated = await self._execute(
            f"""
            UPDATE {self._schema}.audit_head
            SET sequence = :sequence, entry_sha256 = :entry_sha256
            WHERE singleton AND sequence = :previous_sequence
              AND entry_sha256 IS NOT DISTINCT FROM :previous_entry_sha256
            RETURNING sequence
            """,
            {
                "sequence": entry.sequence,
                "entry_sha256": entry.entry_sha256,
                "previous_sequence": entry.sequence - 1,
                "previous_entry_sha256": entry.previous_entry_sha256,
            },
            fallback="AUDIT_CAS_CONFLICT",
        )
        if updated.scalar_one_or_none() != entry.sequence:
            raise ProtectedSecurityError("AUDIT_CAS_CONFLICT")
        return entry

    async def require_grant_dataset(
        self,
        grant: ProtectedAuthorizationGrant,
        expected_state_revision: int,
    ) -> None:
        dataset_value = await self._session.scalar(
            text(
                f"SELECT binding FROM {self._schema}.protected_dataset "
                "WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version FOR UPDATE"
            ),
            {"dataset_id": grant.dataset_id, "dataset_version": grant.dataset_version},
        )
        if dataset_value is None:
            raise ProtectedSecurityError("DATASET_STATE_MISMATCH")
        dataset = _model(ProtectedDatasetBinding, dataset_value, "INTERNAL_ERROR")
        if (
            dataset.dataset_id != grant.dataset_id
            or dataset.dataset_version != grant.dataset_version
            or dataset.state_revision != expected_state_revision
            or dataset.manifest_sha256 != grant.manifest_sha256
            or dataset.protected_artifact_sha256 != grant.protected_artifact_sha256
            or dataset.hmac_key_version != grant.hmac_key_version
        ):
            raise ProtectedSecurityError("DATASET_BINDING_MISMATCH")

    async def require_grant_subject(self, grant: ProtectedAuthorizationGrant) -> None:
        result = await self._execute(
            f"""
            SELECT actor_id, actor_namespace, principal_role
            FROM {self._schema}.protected_identity
            WHERE actor_id = :actor_id AND actor_namespace = :actor_namespace
              AND identity_plane = 'DATA' AND principal_role = :principal_role AND enabled
            """,
            {
                "actor_id": grant.subject.actor.actor_id,
                "actor_namespace": grant.subject.actor.namespace,
                "principal_role": grant.subject.role.value,
            },
        )
        row = result.one_or_none()
        if row is None:
            raise ProtectedSecurityError("GRANT_SUBJECT_MISMATCH")
        persisted = _model(
            ProtectedPrincipal,
            {
                "actor": {"actor_id": row.actor_id, "namespace": row.actor_namespace},
                "role": row.principal_role,
            },
            "GRANT_SUBJECT_MISMATCH",
        )
        if persisted != grant.subject:
            raise ProtectedSecurityError("GRANT_SUBJECT_MISMATCH")

    async def lock_grant_scope(self, grant: ProtectedAuthorizationGrant) -> int:
        rows = await self._execute(
            f"""
            SELECT revision
            FROM {self._schema}.authorization_grant
            WHERE subject_actor_id = :actor_id
              AND subject_namespace = :actor_namespace
              AND subject_role = :subject_role
              AND dataset_id = :dataset_id
              AND dataset_version = :dataset_version
            FOR UPDATE
            """,
            {
                "actor_id": grant.subject.actor.actor_id,
                "actor_namespace": grant.subject.actor.namespace,
                "subject_role": grant.subject.role.value,
                "dataset_id": grant.dataset_id,
                "dataset_version": grant.dataset_version,
            },
        )
        revisions = [row.revision for row in rows]
        return max(revisions, default=0) + 1

    async def require_grant_approval(
        self,
        grant: ProtectedAuthorizationGrant,
        executor: _ControlExecutor,
    ) -> None:
        result = await self._execute(
            f"""
            SELECT evidence, canonical_raw_sha256
            FROM {self._schema}.approval_evidence
            WHERE source_event_id = :source_event_id
            """,
            {"source_event_id": grant.approval_source_event_id},
        )
        row = result.one_or_none()
        if row is None:
            raise ProtectedSecurityError("APPROVAL_NOT_VERIFIED")
        evidence = _model(ApprovalSourceEvidence, row.evidence, "APPROVAL_NOT_VERIFIED")
        if row.canonical_raw_sha256 != evidence.canonical_raw_sha256 or executor.principal != evidence.issuer:
            raise ProtectedSecurityError("APPROVAL_EVIDENCE_MISMATCH")
        verify_authorization_approval(
            grant,
            evidence,
            AuthorizationAuditAction.GRANT,
            grant.approval_source_raw_sha256,
        )
        if not grant.valid_from <= self._clock.now_utc() < grant.expires_at:
            raise ProtectedSecurityError("AUTHORIZATION_EXPIRED")

    async def grant_denial_reason(
        self,
        grant: ProtectedAuthorizationGrant,
        expected_state_revision: int,
        executor: _ControlExecutor,
    ) -> ProtectedAuditReason | None:
        if executor.principal != grant.issuer:
            return ProtectedAuditReason.APPROVAL_EVIDENCE_MISMATCH
        try:
            await self.require_grant_dataset(grant, expected_state_revision)
            await self.require_grant_subject(grant)
        except ProtectedSecurityError as error:
            return ProtectedAuditReason(error.reason_code)
        return None

    async def insert_grant(self, grant: ProtectedAuthorizationGrant) -> None:
        await self._execute(
            f"""
            INSERT INTO {self._schema}.authorization_grant (
                grant_id, revision, effective_revision, grant_body,
                subject_actor_id, subject_namespace, subject_role,
                dataset_id, dataset_version, manifest_sha256,
                protected_artifact_sha256, hmac_key_version, actions,
                valid_from, expires_at
            ) VALUES (
                CAST(:grant_id AS uuid), :revision, :revision, CAST(:grant_body AS jsonb),
                :subject_actor_id, :subject_namespace, :subject_role,
                :dataset_id, :dataset_version, :manifest_sha256,
                :protected_artifact_sha256, :hmac_key_version, :actions,
                :valid_from, :expires_at
            )
            """,
            {
                "grant_id": grant.grant_id,
                "revision": grant.revision,
                "grant_body": _json_value(grant),
                "subject_actor_id": grant.subject.actor.actor_id,
                "subject_namespace": grant.subject.actor.namespace,
                "subject_role": grant.subject.role.value,
                "dataset_id": grant.dataset_id,
                "dataset_version": grant.dataset_version,
                "manifest_sha256": grant.manifest_sha256,
                "protected_artifact_sha256": grant.protected_artifact_sha256,
                "hmac_key_version": grant.hmac_key_version,
                "actions": [action.value for action in grant.actions],
                "valid_from": grant.valid_from,
                "expires_at": grant.expires_at,
            },
            fallback="AUTHORIZATION_REVISION_MISMATCH",
        )

    async def locked_grant(self, grant_id: str) -> tuple[ProtectedAuthorizationGrant, int, bool]:
        result = await self._execute(
            f"""
            SELECT grant_body, revision, effective_revision, revoked_at
            FROM {self._schema}.authorization_grant
            WHERE grant_id = CAST(:grant_id AS uuid)
            FOR UPDATE
            """,
            {"grant_id": grant_id},
        )
        row = result.one_or_none()
        if row is None:
            raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
        grant = _model(ProtectedAuthorizationGrant, row.grant_body, "INTERNAL_ERROR")
        if grant.grant_id != grant_id or grant.revision != row.revision:
            raise ProtectedSecurityError("AUTHORIZATION_REVISION_MISMATCH")
        return grant, row.effective_revision, row.revoked_at is not None

    async def require_revoke_approval(
        self,
        grant: ProtectedAuthorizationGrant,
        command: RevokeAuthorizationCommand,
        executor: _ControlExecutor,
    ) -> ApprovalSourceEvidence:
        result = await self._execute(
            f"""
            SELECT evidence, canonical_raw_sha256
            FROM {self._schema}.approval_evidence
            WHERE source_event_id = :source_event_id
            """,
            {"source_event_id": command.approval_source_event_id},
        )
        row = result.one_or_none()
        if row is None:
            raise ProtectedSecurityError("APPROVAL_NOT_VERIFIED")
        evidence = _model(ApprovalSourceEvidence, row.evidence, "APPROVAL_NOT_VERIFIED")
        if (
            evidence.source_event_id != command.approval_source_event_id
            or evidence.canonical_raw_sha256 != command.expected_raw_sha256
            or row.canonical_raw_sha256 != command.expected_raw_sha256
            or executor.principal != evidence.issuer
        ):
            raise ProtectedSecurityError("APPROVAL_EVIDENCE_MISMATCH")
        verify_authorization_approval(
            grant,
            evidence,
            AuthorizationAuditAction.REVOKE,
            command.expected_raw_sha256,
        )
        return evidence

    async def update_revoked(self, grant_id: str, expected_revision: int) -> int:
        new_revision = expected_revision + 1
        result = await self._execute(
            f"""
            UPDATE {self._schema}.authorization_grant
            SET effective_revision = :new_revision, revoked_at = :revoked_at
            WHERE grant_id = CAST(:grant_id AS uuid)
              AND effective_revision = :expected_revision
              AND revoked_at IS NULL
            RETURNING effective_revision
            """,
            {
                "grant_id": grant_id,
                "expected_revision": expected_revision,
                "new_revision": new_revision,
                "revoked_at": self._clock.now_utc(),
            },
        )
        if result.scalar_one_or_none() != new_revision:
            raise ProtectedSecurityError("AUTHORIZATION_REVISION_MISMATCH")
        return new_revision

    async def update_expired(self, grant_id: str, expected_revision: int) -> int:
        new_revision = expected_revision + 1
        result = await self._execute(
            f"""
            UPDATE {self._schema}.authorization_grant
            SET effective_revision = :new_revision
            WHERE grant_id = CAST(:grant_id AS uuid)
              AND effective_revision = :expected_revision
              AND revoked_at IS NULL
            RETURNING effective_revision
            """,
            {
                "grant_id": grant_id,
                "expected_revision": expected_revision,
                "new_revision": new_revision,
            },
        )
        if result.scalar_one_or_none() != new_revision:
            raise ProtectedSecurityError("AUTHORIZATION_REVISION_MISMATCH")
        return new_revision


class PostgresqlProtectedAuthorizationControlService:
    """Fail-closed C1 application service for approval and grant control."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        schema: str,
        data_access_role: str,
        control_role: str,
        approval_source: TrustedApprovalSource,
    ) -> None:
        self._engine = engine
        self._schema = schema
        self._data_access_role = data_access_role
        self._control_role = control_role
        self._approval_source = approval_source
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _validate_connection(self, session: AsyncSession) -> None:
        try:
            connection = await session.connection()
            await validate_protected_control_connection(
                connection,
                schema=self._schema,
                data_access=self._data_access_role,
                control=self._control_role,
            )
        except ValueError:
            raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND") from None
        except SQLAlchemyError:
            raise ProtectedSecurityError("INTERNAL_ERROR") from None

    async def _authenticated_executor(self, session: AsyncSession) -> _ControlExecutor:
        await self._validate_connection(session)
        clock = await PostgresqlTrustedClock.from_session(session)
        return await _ControlSession(session, self._schema, clock).resolve_executor()

    async def validate(self) -> None:
        async with self._sessions() as session:
            async with session.begin():
                await self._authenticated_executor(session)

    async def close(self) -> None:
        await self._engine.dispose()

    async def _read_replay(
        self,
        *,
        request_id: str,
        command_kind: ControlCommandKind,
        command_sha256: str,
    ) -> ControlCommandResult | None:
        async with self._sessions() as session:
            async with session.begin():
                await self._validate_connection(session)
                clock = await PostgresqlTrustedClock.from_session(session)
                control = _ControlSession(session, self._schema, clock)
                await control.resolve_executor()
                return await control.replay(
                    request_id=request_id,
                    command_kind=command_kind,
                    command_sha256=command_sha256,
                    lock_head=False,
                )

    async def _fetch_approval(
        self,
        command: IngestApprovalCommand,
    ) -> tuple[ApprovalSourceEvidence | None, ProtectedAuditReason | None]:
        try:
            evidence = await self._approval_source.fetch(command.source_event_id)
        except Exception:
            return None, ProtectedAuditReason.APPROVAL_NOT_VERIFIED
        if (
            evidence.source_event_id != command.source_event_id
            or evidence.canonical_raw_sha256 != command.expected_raw_sha256
        ):
            return evidence, ProtectedAuditReason.APPROVAL_EVIDENCE_MISMATCH
        return evidence, None

    async def ingest_approval(self, command: IngestApprovalCommand) -> ControlCommandResult:
        async with self._sessions() as preparation:
            async with preparation.begin():
                prepared_executor = await self._authenticated_executor(preparation)
        command_kind = ControlCommandKind.INGEST_APPROVAL
        digest = control_command_sha256(command_kind, command)
        replay = await self._read_replay(
            request_id=command.request_id,
            command_kind=command_kind,
            command_sha256=digest,
        )
        if replay is not None:
            return replay
        evidence, denial_reason = await self._fetch_approval(command)

        denial: ProtectedSecurityError | None = None
        async with self._sessions() as session:
            async with session.begin():
                await self._validate_connection(session)
                clock = await PostgresqlTrustedClock.from_session(session)
                control = _ControlSession(session, self._schema, clock)
                executor = await control.resolve_executor()
                if executor != prepared_executor:
                    raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
                if evidence is not None and executor.principal != evidence.issuer:
                    denial_reason = ProtectedAuditReason.APPROVAL_EVIDENCE_MISMATCH
                replay = await control.replay(
                    request_id=command.request_id,
                    command_kind=command_kind,
                    command_sha256=digest,
                    lock_head=True,
                )
                if replay is not None:
                    return replay
                entries = await control.verified_entries(lock_head=False)
                if denial_reason is not None:
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=executor.actor,
                        target_kind=ControlAuditTargetKind.APPROVAL_SOURCE_EVENT,
                        target_id=command.source_event_id,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.DENIED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=denial_reason,
                    )
                    denial = ProtectedSecurityError(denial_reason.value)
                else:
                    assert evidence is not None
                    existing_value = await session.scalar(
                        text(
                            f"SELECT evidence FROM {control._schema}.approval_evidence "
                            "WHERE source_event_id = :source_event_id"
                        ),
                        {"source_event_id": command.source_event_id},
                    )
                    if existing_value is None:
                        await control._execute(
                            f"""
                            INSERT INTO {control._schema}.approval_evidence (
                                source_event_id, evidence, canonical_raw_sha256, recorded_at
                            ) VALUES (
                                :source_event_id, CAST(:evidence AS jsonb), :canonical_raw_sha256, :recorded_at
                            )
                            """,
                            {
                                "source_event_id": evidence.source_event_id,
                                "evidence": _json_value(evidence),
                                "canonical_raw_sha256": evidence.canonical_raw_sha256,
                                "recorded_at": clock.now_utc(),
                            },
                        )
                    else:
                        persisted = _model(ApprovalSourceEvidence, existing_value, "APPROVAL_NOT_VERIFIED")
                        if persisted != evidence:
                            raise ProtectedSecurityError("CONTROL_COMMAND_CONFLICT")
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=executor.actor,
                        target_kind=ControlAuditTargetKind.APPROVAL_SOURCE_EVENT,
                        target_id=command.source_event_id,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.SUCCEEDED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=ProtectedAuditReason.APPROVAL_VERIFIED,
                    )
        if denial is not None:
            raise denial
        return ControlCommandResult(
            request_id=command.request_id,
            command_kind=command_kind,
            target_id=command.source_event_id,
            effective_revision=None,
            authorization_audit_event_id=None,
            reason_code="APPROVAL_VERIFIED",
        )

    async def grant(self, command: GrantAuthorizationCommand) -> ControlCommandResult:
        command_kind = ControlCommandKind.GRANT
        digest = control_command_sha256(command_kind, command)
        async with self._sessions() as preparation:
            async with preparation.begin():
                prepared_executor = await self._authenticated_executor(preparation)
        replay = await self._read_replay(
            request_id=command.request_id,
            command_kind=command_kind,
            command_sha256=digest,
        )
        if replay is not None:
            return replay

        grant = command.grant
        denial: ProtectedSecurityError | None = None
        async with self._sessions() as session:
            async with session.begin():
                await self._validate_connection(session)
                clock = await PostgresqlTrustedClock.from_session(session)
                control = _ControlSession(session, self._schema, clock)
                executor = await control.resolve_executor()
                if executor != prepared_executor:
                    raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
                denial_reason = await control.grant_denial_reason(
                    grant,
                    command.expected_dataset_state_revision,
                    executor,
                )
                next_revision = await control.lock_grant_scope(grant)

                replay = await control.replay(
                    request_id=command.request_id,
                    command_kind=command_kind,
                    command_sha256=digest,
                    lock_head=True,
                )
                if replay is not None:
                    return replay
                if denial_reason is None and grant.revision != next_revision:
                    denial_reason = ProtectedAuditReason.AUTHORIZATION_REVISION_MISMATCH
                if denial_reason is None:
                    try:
                        await control.require_grant_approval(grant, executor)
                    except ProtectedSecurityError as error:
                        denial_reason = ProtectedAuditReason(error.reason_code)
                entries = await control.verified_entries(lock_head=False)
                if denial_reason is not None:
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=executor.actor,
                        target_kind=ControlAuditTargetKind.AUTHORIZATION_GRANT,
                        target_id=grant.grant_id,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.DENIED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=denial_reason,
                    )
                    denial = ProtectedSecurityError(denial_reason.value)
                else:
                    await control.insert_grant(grant)
                    authorization_audit = await control.append_authorization(
                        entries=entries,
                        grant=grant,
                        action=AuthorizationAuditAction.GRANT,
                        effective_revision=grant.revision,
                        reason_code=ProtectedAuditReason.APPROVAL_VERIFIED,
                    )
                    entries = await control.verified_entries(lock_head=False)
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=executor.actor,
                        target_kind=ControlAuditTargetKind.AUTHORIZATION_GRANT,
                        target_id=grant.grant_id,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.SUCCEEDED,
                        effective_revision=grant.revision,
                        authorization_audit_event_id=authorization_audit.event_id,
                        reason_code=ProtectedAuditReason.AUTHORIZED,
                    )
        if denial is not None:
            raise denial
        return ControlCommandResult(
            request_id=command.request_id,
            command_kind=command_kind,
            target_id=grant.grant_id,
            effective_revision=grant.revision,
            authorization_audit_event_id=authorization_audit.event_id,
            reason_code="AUTHORIZED",
        )

    async def revoke(self, command: RevokeAuthorizationCommand) -> ControlCommandResult:
        command_kind = ControlCommandKind.REVOKE
        digest = control_command_sha256(command_kind, command)
        async with self._sessions() as preparation:
            async with preparation.begin():
                prepared_executor = await self._authenticated_executor(preparation)
        replay = await self._read_replay(
            request_id=command.request_id,
            command_kind=command_kind,
            command_sha256=digest,
        )
        if replay is not None:
            return replay

        denial: ProtectedSecurityError | None = None
        async with self._sessions() as session:
            async with session.begin():
                await self._validate_connection(session)
                clock = await PostgresqlTrustedClock.from_session(session)
                control = _ControlSession(session, self._schema, clock)
                executor = await control.resolve_executor()
                if executor != prepared_executor:
                    raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
                grant, effective_revision, revoked = await control.locked_grant(command.grant_id)
                replay = await control.replay(
                    request_id=command.request_id,
                    command_kind=command_kind,
                    command_sha256=digest,
                    lock_head=True,
                )
                if replay is not None:
                    return replay
                denial_reason = None
                if revoked:
                    denial_reason = ProtectedAuditReason.AUTHORIZATION_REVOKED
                elif effective_revision != command.expected_effective_revision:
                    denial_reason = ProtectedAuditReason.AUTHORIZATION_REVISION_MISMATCH
                evidence = None
                if denial_reason is None:
                    try:
                        evidence = await control.require_revoke_approval(grant, command, executor)
                    except ProtectedSecurityError as error:
                        denial_reason = ProtectedAuditReason(error.reason_code)
                entries = await control.verified_entries(lock_head=False)
                if denial_reason is not None:
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=executor.actor,
                        target_kind=ControlAuditTargetKind.AUTHORIZATION_GRANT,
                        target_id=grant.grant_id,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.DENIED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=denial_reason,
                    )
                    denial = ProtectedSecurityError(denial_reason.value)
                else:
                    assert evidence is not None
                    new_revision = await control.update_revoked(command.grant_id, effective_revision)
                    authorization_audit = await control.append_authorization(
                        entries=entries,
                        grant=grant,
                        action=AuthorizationAuditAction.REVOKE,
                        effective_revision=new_revision,
                        reason_code=ProtectedAuditReason.REVOKED,
                        evidence=evidence,
                    )
                    entries = await control.verified_entries(lock_head=False)
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=executor.actor,
                        target_kind=ControlAuditTargetKind.AUTHORIZATION_GRANT,
                        target_id=grant.grant_id,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.SUCCEEDED,
                        effective_revision=new_revision,
                        authorization_audit_event_id=authorization_audit.event_id,
                        reason_code=ProtectedAuditReason.REVOKED,
                    )
        if denial is not None:
            raise denial
        return ControlCommandResult(
            request_id=command.request_id,
            command_kind=command_kind,
            target_id=grant.grant_id,
            effective_revision=new_revision,
            authorization_audit_event_id=authorization_audit.event_id,
            reason_code="REVOKED",
        )

    async def expire(self, command: ExpireAuthorizationCommand) -> ControlCommandResult:
        command_kind = ControlCommandKind.EXPIRE
        digest = control_command_sha256(command_kind, command)
        async with self._sessions() as preparation:
            async with preparation.begin():
                prepared_executor = await self._authenticated_executor(preparation)
        replay = await self._read_replay(
            request_id=command.request_id,
            command_kind=command_kind,
            command_sha256=digest,
        )
        if replay is not None:
            return replay

        denial: ProtectedSecurityError | None = None
        async with self._sessions() as session:
            async with session.begin():
                await self._validate_connection(session)
                clock = await PostgresqlTrustedClock.from_session(session)
                control = _ControlSession(session, self._schema, clock)
                executor = await control.resolve_executor()
                if executor != prepared_executor:
                    raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
                grant, effective_revision, revoked = await control.locked_grant(command.grant_id)
                replay = await control.replay(
                    request_id=command.request_id,
                    command_kind=command_kind,
                    command_sha256=digest,
                    lock_head=True,
                )
                if replay is not None:
                    return replay
                denial_reason = None
                if revoked:
                    denial_reason = ProtectedAuditReason.AUTHORIZATION_REVOKED
                elif effective_revision != command.expected_effective_revision:
                    denial_reason = ProtectedAuditReason.AUTHORIZATION_REVISION_MISMATCH
                elif effective_revision != grant.revision or clock.now_utc() < grant.expires_at:
                    denial_reason = ProtectedAuditReason.AUTHORIZATION_EXPIRED
                entries = await control.verified_entries(lock_head=False)
                if denial_reason is not None:
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=executor.actor,
                        target_kind=ControlAuditTargetKind.AUTHORIZATION_GRANT,
                        target_id=grant.grant_id,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.DENIED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=denial_reason,
                    )
                    denial = ProtectedSecurityError(denial_reason.value)
                else:
                    new_revision = await control.update_expired(command.grant_id, effective_revision)
                    authorization_audit = await control.append_authorization(
                        entries=entries,
                        grant=grant,
                        action=AuthorizationAuditAction.EXPIRE,
                        effective_revision=new_revision,
                        reason_code=ProtectedAuditReason.EXPIRED,
                    )
                    entries = await control.verified_entries(lock_head=False)
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=executor.actor,
                        target_kind=ControlAuditTargetKind.AUTHORIZATION_GRANT,
                        target_id=grant.grant_id,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.SUCCEEDED,
                        effective_revision=new_revision,
                        authorization_audit_event_id=authorization_audit.event_id,
                        reason_code=ProtectedAuditReason.EXPIRED,
                    )
        if denial is not None:
            raise denial
        return ControlCommandResult(
            request_id=command.request_id,
            command_kind=command_kind,
            target_id=grant.grant_id,
            effective_revision=new_revision,
            authorization_audit_event_id=authorization_audit.event_id,
            reason_code="EXPIRED",
        )


__all__ = ["PostgresqlProtectedAuthorizationControlService"]
