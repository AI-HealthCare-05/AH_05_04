"""PostgreSQL application service for protected authorization control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ai_worker.adapters.postgresql_protected_retrieval import (
    PostgresqlProtectedAuditJournal,
    PostgresqlTrustedClock,
    _assemble_dataset_binding,
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
    ProtectedDatasetState,
    ProtectedPrincipal,
    ProtectedPrincipalRole,
    ProtectedSecurityError,
    audit_entry_sha256,
    new_event_id,
)
from ai_worker.tasks.evaluation.protected_retrieval_control import (
    ApprovalSourceNotFoundError,
    ControlCommandKind,
    ControlCommandResult,
    DisableIdentityCommand,
    ExpireAuthorizationCommand,
    FreezeApprovalSourceEvidence,
    FreezeDatasetCommand,
    GrantAuthorizationCommand,
    IngestApprovalCommand,
    RegisterDatasetCommand,
    RegisterIdentityCommand,
    RevokeAuthorizationCommand,
    TransitionDatasetCommand,
    TrustedApprovalSource,
    control_command_sha256,
    verify_authorization_approval,
    verify_freeze_approval,
)
from infra.python.protected_retrieval_role_policy import validate_protected_control_connection


@dataclass(frozen=True)
class _ControlExecutor:
    actor: ActorIdentity
    principal: ProtectedApprovalPrincipal
    database_login: str


type _SuccessReason = Literal[
    "APPROVAL_VERIFIED",
    "AUTHORIZED",
    "REVOKED",
    "EXPIRED",
    "IDENTITY_REGISTERED",
    "IDENTITY_DISABLED",
    "DATASET_REGISTERED",
    "DATASET_TRANSITIONED",
    "DATASET_FROZEN",
]

_DATASET_TRANSITIONS = frozenset(
    {
        (ProtectedDatasetState.ACCESS_AUTHORIZED, ProtectedDatasetState.AUTHORING),
        (ProtectedDatasetState.AUTHORING, ProtectedDatasetState.REVIEW_READY),
        (ProtectedDatasetState.REVIEW_READY, ProtectedDatasetState.AUTHORING),
    }
)


def _validate_dataset_transition_invariants(
    command: TransitionDatasetCommand,
) -> ProtectedAuditReason | None:
    if (command.from_state, command.to_state) not in _DATASET_TRANSITIONS:
        return ProtectedAuditReason.DATASET_STATE_MISMATCH
    if command.to_state is ProtectedDatasetState.REVIEW_READY:
        if command.authored_count == 0 or not command.review_complete:
            return ProtectedAuditReason.DATASET_STATE_MISMATCH
    elif command.to_state is ProtectedDatasetState.AUTHORING and command.review_complete:
        return ProtectedAuditReason.DATASET_STATE_MISMATCH
    return None


_POLICY_DENIAL_REASONS = frozenset(
    {
        ProtectedAuditReason.ACTION_NOT_GRANTED,
        ProtectedAuditReason.APPROVAL_ACTION_MISMATCH,
        ProtectedAuditReason.APPROVAL_EVIDENCE_MISMATCH,
        ProtectedAuditReason.APPROVAL_GRANT_BINDING_MISMATCH,
        ProtectedAuditReason.APPROVAL_NOT_VERIFIED,
        ProtectedAuditReason.AUTHORIZATION_EXPIRED,
        ProtectedAuditReason.AUTHORIZATION_NOT_FOUND,
        ProtectedAuditReason.AUTHORIZATION_REVOKED,
        ProtectedAuditReason.AUTHORIZATION_REVISION_MISMATCH,
        ProtectedAuditReason.CONTROL_COMMAND_CONFLICT,
        ProtectedAuditReason.DATASET_BINDING_MISMATCH,
        ProtectedAuditReason.DATASET_STATE_MISMATCH,
        ProtectedAuditReason.FREEZE_EVIDENCE_INCOMPLETE,
        ProtectedAuditReason.GRANT_SUBJECT_MISMATCH,
        ProtectedAuditReason.ISSUER_ROLE_DENIED,
        ProtectedAuditReason.SELF_APPROVAL_DENIED,
    }
)


def _policy_denial_reason(error: ProtectedSecurityError) -> ProtectedAuditReason:
    reason = ProtectedAuditReason(error.reason_code)
    if reason not in _POLICY_DENIAL_REASONS:
        raise error
    return reason


def _success_reason(command_kind: ControlCommandKind) -> _SuccessReason:
    reasons: dict[ControlCommandKind, _SuccessReason] = {
        ControlCommandKind.INGEST_APPROVAL: "APPROVAL_VERIFIED",
        ControlCommandKind.GRANT: "AUTHORIZED",
        ControlCommandKind.REVOKE: "REVOKED",
        ControlCommandKind.EXPIRE: "EXPIRED",
        ControlCommandKind.REGISTER_IDENTITY: "IDENTITY_REGISTERED",
        ControlCommandKind.DISABLE_IDENTITY: "IDENTITY_DISABLED",
        ControlCommandKind.REGISTER_DATASET: "DATASET_REGISTERED",
        ControlCommandKind.TRANSITION_DATASET: "DATASET_TRANSITIONED",
        ControlCommandKind.FREEZE_DATASET: "DATASET_FROZEN",
    }
    return reasons[command_kind]


@dataclass(frozen=True, slots=True)
class _LockedIdentityRow:
    database_login: str
    actor_id: str
    actor_namespace: str
    principal_role: str | None
    identity_plane: str
    approval_role: str | None
    enabled: bool

    @classmethod
    def from_row(cls, row: object) -> _LockedIdentityRow:
        mapping = getattr(row, "_mapping", None)
        if mapping is None:
            raise ProtectedSecurityError("INTERNAL_ERROR")
        return cls(
            database_login=str(mapping["database_login"]),
            actor_id=str(mapping["actor_id"]),
            actor_namespace=str(mapping["actor_namespace"]),
            principal_role=str(mapping["principal_role"]) if mapping["principal_role"] is not None else None,
            identity_plane=str(mapping["identity_plane"]),
            approval_role=str(mapping["approval_role"]) if mapping["approval_role"] is not None else None,
            enabled=bool(mapping["enabled"]),
        )


def _evaluate_identity_executor_denial(
    executor_row: _LockedIdentityRow | None,
    prepared_executor: _ControlExecutor,
) -> ProtectedAuditReason | None:
    if (
        executor_row is None
        or not executor_row.enabled
        or executor_row.identity_plane != "CONTROL"
        or (executor_row.actor_id, executor_row.actor_namespace)
        != (prepared_executor.actor.actor_id, prepared_executor.actor.namespace)
        or executor_row.database_login != prepared_executor.database_login
    ):
        return ProtectedAuditReason.AUTHORIZATION_NOT_FOUND
    if executor_row.approval_role != ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER.value:
        return ProtectedAuditReason.ISSUER_ROLE_DENIED
    return None


def _evaluate_register_denial(
    executor_row: _LockedIdentityRow | None,
    prepared_executor: _ControlExecutor,
    command: RegisterIdentityCommand,
    conflict_count: int,
) -> ProtectedAuditReason | None:
    executor_denial = _evaluate_identity_executor_denial(executor_row, prepared_executor)
    if executor_denial is not None:
        return executor_denial
    if command.database_login == prepared_executor.database_login or (
        command.actor_namespace,
        command.actor_id,
    ) == (prepared_executor.actor.namespace, prepared_executor.actor.actor_id):
        return ProtectedAuditReason.SELF_APPROVAL_DENIED
    if conflict_count > 0:
        return ProtectedAuditReason.CONTROL_COMMAND_CONFLICT
    return None


def _evaluate_disable_denial(
    executor_row: _LockedIdentityRow | None,
    prepared_executor: _ControlExecutor,
    target_row: _LockedIdentityRow | None,
    command: DisableIdentityCommand,
) -> ProtectedAuditReason | None:
    executor_denial = _evaluate_identity_executor_denial(executor_row, prepared_executor)
    if executor_denial is not None:
        return executor_denial
    if command.database_login == prepared_executor.database_login or (
        command.expected_actor_namespace,
        command.expected_actor_id,
    ) == (prepared_executor.actor.namespace, prepared_executor.actor.actor_id):
        return ProtectedAuditReason.SELF_APPROVAL_DENIED
    if (
        target_row is None
        or not target_row.enabled
        or target_row.actor_id != command.expected_actor_id
        or target_row.actor_namespace != command.expected_actor_namespace
    ):
        return ProtectedAuditReason.AUTHORIZATION_NOT_FOUND
    return None


class _ControlSession(_ProtectedSession):
    def __init__(
        self,
        session: AsyncSession,
        schema: str,
        clock: PostgresqlTrustedClock,
    ) -> None:
        super().__init__(session, schema)
        self._clock = clock

    async def refresh_clock(self) -> None:
        self._clock = await PostgresqlTrustedClock.from_session(self._session)

    async def resolve_executor(self) -> _ControlExecutor:
        result = await self._execute(
            f"""
            SELECT database_login, actor_id, actor_namespace, approval_role
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
            database_login=str(row.database_login),
        )

    async def lock_dataset_executor(self, expected: _ControlExecutor) -> _ControlExecutor:
        query = f"""
            SELECT database_login, actor_id, actor_namespace, identity_plane,
                   principal_role, approval_role, enabled
            FROM {self._schema}.protected_identity
            WHERE database_login = :database_login
               OR (actor_id = :actor_id AND actor_namespace = :actor_namespace)
            ORDER BY database_login
            FOR UPDATE
        """
        params: dict[str, object] = {
            "database_login": expected.database_login,
            "actor_id": expected.actor.actor_id,
            "actor_namespace": expected.actor.namespace,
        }
        await self._execute(query, params)
        result = await self._execute(query, params)
        rows = tuple(result)
        executor_rows = tuple(row for row in rows if row.database_login == expected.database_login)
        if len(executor_rows) != 1:
            raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
        if any(
            row.enabled
            and row.identity_plane == "DATA"
            and row.principal_role == ProtectedPrincipalRole.HOLDOUT_AUTHOR.value
            for row in rows
        ):
            raise ProtectedSecurityError("SELF_APPROVAL_DENIED")
        locked = await self.resolve_executor()
        if locked != expected:
            raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
        if locked.principal.role is not ProtectedApprovalRole.DATASET_CUSTODIAN:
            raise ProtectedSecurityError("ISSUER_ROLE_DENIED")
        return locked

    async def check_dataset_transition(self, command: TransitionDatasetCommand) -> ProtectedAuditReason | None:
        dataset_row = (
            await self._execute(
                f"""
                SELECT state, state_revision, authored_count, review_complete
                FROM {self._schema}.protected_dataset
                WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version
                FOR UPDATE
                """,
                {"dataset_id": command.dataset_id, "dataset_version": command.dataset_version},
            )
        ).one_or_none()
        if dataset_row is None:
            return ProtectedAuditReason.DATASET_STATE_MISMATCH
        if (
            dataset_row.state != command.from_state.value
            or dataset_row.state_revision != command.expected_state_revision
        ):
            return ProtectedAuditReason.DATASET_STATE_MISMATCH
        return None

    async def verified_entries(self, *, lock_head: bool) -> tuple[ProtectedAuditEntry, ...]:
        journal = PostgresqlProtectedAuditJournal(self._session, self._schema_name, self._clock)
        return await journal._verified_entries(lock_head=lock_head)

    async def replay(
        self,
        *,
        request_id: str,
        command_kind: ControlCommandKind,
        command_sha256: str,
        executor: ActorIdentity,
        lock_head: bool,
    ) -> ControlCommandResult | None:
        entries = await self.verified_entries(lock_head=lock_head)
        matching = [
            entry for entry in entries if isinstance(entry, ControlCommandAuditEntry) and entry.event_id == request_id
        ]
        if not matching:
            return None
        if len(matching) != 1:
            raise ProtectedSecurityError("AUDIT_UNAVAILABLE")
        entry = matching[0]
        if (
            entry.command_kind != command_kind.value
            or entry.command_sha256 != command_sha256
            or entry.executed_by != executor
        ):
            raise ProtectedSecurityError("CONTROL_COMMAND_CONFLICT")
        if entry.outcome is ControlAuditOutcome.DENIED:
            raise ProtectedSecurityError(entry.reason_code.value)
        reason_code = _success_reason(command_kind)
        if entry.reason_code.value != reason_code:
            raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
        if command_kind in {
            ControlCommandKind.GRANT,
            ControlCommandKind.REVOKE,
            ControlCommandKind.EXPIRE,
        }:
            references = [
                candidate
                for candidate in entries
                if isinstance(candidate, AuthorizationAuditEntry)
                and candidate.event_id == entry.authorization_audit_event_id
            ]
            if len(references) != 1:
                raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
            reference = references[0]
            expected_authorization_reason = {
                ControlCommandKind.GRANT: ProtectedAuditReason.APPROVAL_VERIFIED,
                ControlCommandKind.REVOKE: ProtectedAuditReason.REVOKED,
                ControlCommandKind.EXPIRE: ProtectedAuditReason.EXPIRED,
            }[command_kind]
            if (
                reference.grant_id != entry.target_id
                or reference.action.value != command_kind.value
                or reference.effective_revision != entry.result_effective_revision
                or reference.reason_code is not expected_authorization_reason
                or reference.sequence >= entry.sequence
            ):
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
                sequence, event_id, event_kind, entry_body,
                previous_entry_sha256, entry_sha256, recorded_at, control_entry
            ) VALUES (
                :sequence, CAST(:event_id AS uuid), 'CONTROL', CAST(:entry_body AS jsonb),
                :previous_entry_sha256, :entry_sha256, :recorded_at, true
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
        history = tuple(
            item for item in entries if isinstance(item, AuthorizationAuditEntry) and item.grant_id == grant.grant_id
        )
        self._validate_authorization_transition(history, grant, action, effective_revision)
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
            approval_source_event_id=(
                evidence.source_event_id if evidence is not None else grant.approval_source_event_id
            ),
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
                sequence, event_id, event_kind, entry_body,
                previous_entry_sha256, entry_sha256, recorded_at, control_entry
            ) VALUES (
                :sequence, CAST(:event_id AS uuid), 'AUTHORIZATION', CAST(:entry_body AS jsonb),
                :previous_entry_sha256, :entry_sha256, :recorded_at, true
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

    @staticmethod
    def _authorization_matches_immutable_grant(
        entry: AuthorizationAuditEntry, grant: ProtectedAuthorizationGrant
    ) -> bool:
        return (
            entry.grant_revision == grant.revision
            and entry.subject == grant.subject
            and entry.issuer == grant.issuer
            and entry.dataset_id == grant.dataset_id
            and entry.dataset_version == grant.dataset_version
            and entry.manifest_sha256 == grant.manifest_sha256
            and entry.protected_artifact_sha256 == grant.protected_artifact_sha256
            and entry.hmac_key_version == grant.hmac_key_version
            and entry.actions == grant.actions
            and entry.control_implementation == grant.control_implementation
            and entry.valid_from == grant.valid_from
            and entry.expires_at == grant.expires_at
        )

    @classmethod
    def _authorization_matches_grant(cls, entry: AuthorizationAuditEntry, grant: ProtectedAuthorizationGrant) -> bool:
        return (
            cls._authorization_matches_immutable_grant(entry, grant)
            and entry.approval_source_event_id == grant.approval_source_event_id
            and entry.approval_source_raw_sha256 == grant.approval_source_raw_sha256
        )

    @classmethod
    def _validate_authorization_transition(
        cls,
        history: tuple[AuthorizationAuditEntry, ...],
        grant: ProtectedAuthorizationGrant,
        action: AuthorizationAuditAction,
        effective_revision: int,
    ) -> None:
        if action is AuthorizationAuditAction.GRANT:
            if history or effective_revision != grant.revision:
                raise ProtectedSecurityError("AUDIT_TRANSITION_INVALID")
            return
        if (
            len(history) != 1
            or history[0].action is not AuthorizationAuditAction.GRANT
            or history[0].effective_revision != grant.revision
            or not cls._authorization_matches_grant(history[0], grant)
            or effective_revision != grant.revision + 1
        ):
            raise ProtectedSecurityError("AUDIT_TRANSITION_INVALID")

    @staticmethod
    def _require_terminal_state_binding(
        terminal: AuthorizationAuditAction,
        *,
        effective_revision: int,
        revoked: bool,
        grant_revision: int,
    ) -> None:
        if effective_revision != grant_revision + 1:
            raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
        if terminal is AuthorizationAuditAction.REVOKE and not revoked:
            raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
        if terminal is AuthorizationAuditAction.EXPIRE and revoked:
            raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")

    @classmethod
    def authorization_terminal_reason(
        cls,
        entries: tuple[ProtectedAuditEntry, ...],
        grant: ProtectedAuthorizationGrant,
        effective_revision: int,
        revoked: bool,
    ) -> ProtectedAuditReason | None:
        history = tuple(
            item for item in entries if isinstance(item, AuthorizationAuditEntry) and item.grant_id == grant.grant_id
        )
        if not history or history[0].action is not AuthorizationAuditAction.GRANT:
            raise ProtectedSecurityError("AUDIT_TRANSITION_INVALID")
        if not cls._authorization_matches_grant(history[0], grant):
            raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
        if len(history) == 1:
            if effective_revision != grant.revision or revoked:
                raise ProtectedSecurityError("AUDIT_TRANSITION_INVALID")
            return None
        if len(history) != 2:
            raise ProtectedSecurityError("AUDIT_TRANSITION_INVALID")
        terminal = history[1].action
        if (
            not cls._authorization_matches_immutable_grant(history[1], grant)
            or history[1].effective_revision != grant.revision + 1
        ):
            raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
        cls._require_terminal_state_binding(
            terminal,
            effective_revision=effective_revision,
            revoked=revoked,
            grant_revision=grant.revision,
        )
        if terminal is AuthorizationAuditAction.REVOKE:
            return ProtectedAuditReason.AUTHORIZATION_REVOKED
        if terminal is AuthorizationAuditAction.EXPIRE:
            return ProtectedAuditReason.AUTHORIZATION_EXPIRED
        raise ProtectedSecurityError("AUDIT_TRANSITION_INVALID")

    async def require_grant_dataset(
        self,
        grant: ProtectedAuthorizationGrant,
        expected_state_revision: int,
    ) -> None:
        result = await self._execute(
            f"""
            SELECT binding, dataset_id, dataset_version, manifest_sha256,
                   protected_artifact_sha256, hmac_key_version, state,
                   state_revision, authored_count, review_complete
            FROM {self._schema}.protected_dataset
            WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version
            FOR UPDATE
            """,
            {"dataset_id": grant.dataset_id, "dataset_version": grant.dataset_version},
        )
        row = result.one_or_none()
        if row is None:
            raise ProtectedSecurityError("DATASET_STATE_MISMATCH")
        entries: tuple[ProtectedAuditEntry, ...] = ()
        if row.state == ProtectedDatasetState.FROZEN.value:
            entries = await self.verified_entries(lock_head=False)
        dataset = _assemble_dataset_binding(
            binding_value=row.binding,
            dataset_id=row.dataset_id,
            dataset_version=row.dataset_version,
            manifest_sha256=row.manifest_sha256,
            protected_artifact_sha256=row.protected_artifact_sha256,
            hmac_key_version=row.hmac_key_version,
            state=row.state,
            state_revision=row.state_revision,
            authored_count=row.authored_count,
            review_complete=row.review_complete,
            audit_entries=entries,
        )
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
            return _policy_denial_reason(error)
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

    async def locked_grant(self, grant_id: str) -> tuple[ProtectedAuthorizationGrant, int, bool] | None:
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
            return None
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

    async def revoke_denial_reason(
        self,
        *,
        locked: tuple[ProtectedAuthorizationGrant, int, bool],
        entries: tuple[ProtectedAuditEntry, ...],
        command: RevokeAuthorizationCommand,
        executor: _ControlExecutor,
    ) -> tuple[ProtectedAuditReason | None, ApprovalSourceEvidence | None]:
        grant, effective_revision, revoked = locked
        reason = self.authorization_terminal_reason(entries, grant, effective_revision, revoked)
        if reason is None and effective_revision != command.expected_effective_revision:
            reason = ProtectedAuditReason.AUTHORIZATION_REVISION_MISMATCH
        if reason is not None:
            return reason, None
        try:
            return None, await self.require_revoke_approval(grant, command, executor)
        except ProtectedSecurityError as error:
            return _policy_denial_reason(error), None

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
                executor = await control.resolve_executor()
                return await control.replay(
                    request_id=request_id,
                    command_kind=command_kind,
                    command_sha256=command_sha256,
                    executor=executor.actor,
                    lock_head=True,
                )

    async def _fetch_approval(
        self,
        command: IngestApprovalCommand,
    ) -> tuple[ApprovalSourceEvidence | None, ProtectedAuditReason | None]:
        try:
            evidence = await self._approval_source.fetch(command.source_event_id)
        except ApprovalSourceNotFoundError:
            return None, ProtectedAuditReason.APPROVAL_NOT_VERIFIED
        except Exception:
            raise ProtectedSecurityError("INTERNAL_ERROR") from None
        if (
            evidence.source_event_id != command.source_event_id
            or evidence.canonical_raw_sha256 != command.expected_raw_sha256
        ):
            return evidence, ProtectedAuditReason.APPROVAL_EVIDENCE_MISMATCH
        return evidence, None

    async def _fetch_freeze_approval(
        self,
        command: FreezeDatasetCommand,
    ) -> tuple[FreezeApprovalSourceEvidence | None, ProtectedAuditReason | None]:
        try:
            evidence = await self._approval_source.fetch_freeze(command.approval_source_event_id)
        except ApprovalSourceNotFoundError:
            return None, ProtectedAuditReason.APPROVAL_NOT_VERIFIED
        except Exception:
            raise ProtectedSecurityError("INTERNAL_ERROR") from None
        if (
            evidence.source_event_id != command.approval_source_event_id
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
                    executor=executor.actor,
                    lock_head=True,
                )
                if replay is not None:
                    return replay
                await control.refresh_clock()
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
                    existing_result = await control._execute(
                        f"SELECT evidence, canonical_raw_sha256 FROM {control._schema}.approval_evidence "
                        "WHERE source_event_id = :source_event_id",
                        {"source_event_id": command.source_event_id},
                    )
                    existing = existing_result.one_or_none()
                    if existing is None:
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
                                "recorded_at": control._clock.now_utc(),
                            },
                        )
                    else:
                        persisted = _model(ApprovalSourceEvidence, existing.evidence, "APPROVAL_NOT_VERIFIED")
                        if (
                            persisted != evidence
                            or persisted.source_event_id != command.source_event_id
                            or existing.canonical_raw_sha256 != persisted.canonical_raw_sha256
                        ):
                            denial_reason = ProtectedAuditReason.CONTROL_COMMAND_CONFLICT
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
                    executor=executor.actor,
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
                        denial_reason = _policy_denial_reason(error)
                await control.refresh_clock()
                if denial_reason is None and not grant.valid_from <= control._clock.now_utc() < grant.expires_at:
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
                locked = await control.locked_grant(command.grant_id)
                replay = await control.replay(
                    request_id=command.request_id,
                    command_kind=command_kind,
                    command_sha256=digest,
                    executor=executor.actor,
                    lock_head=True,
                )
                if replay is not None:
                    return replay
                entries = await control.verified_entries(lock_head=False)
                denial_reason: ProtectedAuditReason | None
                if locked is None:
                    denial_reason = ProtectedAuditReason.AUTHORIZATION_NOT_FOUND
                    grant = None
                    effective_revision = command.expected_effective_revision
                    evidence = None
                else:
                    grant, effective_revision, revoked = locked
                    denial_reason, evidence = await control.revoke_denial_reason(
                        locked=(grant, effective_revision, revoked),
                        entries=entries,
                        command=command,
                        executor=executor,
                    )
                await control.refresh_clock()
                if denial_reason is not None:
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=executor.actor,
                        target_kind=ControlAuditTargetKind.AUTHORIZATION_GRANT,
                        target_id=command.grant_id,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.DENIED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=denial_reason,
                    )
                    denial = ProtectedSecurityError(denial_reason.value)
                else:
                    assert grant is not None
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
            target_id=command.grant_id,
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
                locked = await control.locked_grant(command.grant_id)
                replay = await control.replay(
                    request_id=command.request_id,
                    command_kind=command_kind,
                    command_sha256=digest,
                    executor=executor.actor,
                    lock_head=True,
                )
                if replay is not None:
                    return replay
                entries = await control.verified_entries(lock_head=False)
                await control.refresh_clock()
                denial_reason: ProtectedAuditReason | None
                if locked is None:
                    denial_reason = ProtectedAuditReason.AUTHORIZATION_NOT_FOUND
                    grant = None
                    effective_revision = command.expected_effective_revision
                else:
                    grant, effective_revision, revoked = locked
                    denial_reason = control.authorization_terminal_reason(entries, grant, effective_revision, revoked)
                    if denial_reason is None and effective_revision != command.expected_effective_revision:
                        denial_reason = ProtectedAuditReason.AUTHORIZATION_REVISION_MISMATCH
                    elif denial_reason is None and (
                        effective_revision != grant.revision or control._clock.now_utc() < grant.expires_at
                    ):
                        denial_reason = ProtectedAuditReason.AUTHORIZATION_EXPIRED
                if denial_reason is not None:
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=executor.actor,
                        target_kind=ControlAuditTargetKind.AUTHORIZATION_GRANT,
                        target_id=command.grant_id,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.DENIED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=denial_reason,
                    )
                    denial = ProtectedSecurityError(denial_reason.value)
                else:
                    assert grant is not None
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
            target_id=command.grant_id,
            effective_revision=new_revision,
            authorization_audit_event_id=authorization_audit.event_id,
            reason_code="EXPIRED",
        )

    async def register_identity(self, command: RegisterIdentityCommand) -> ControlCommandResult:
        command_kind = ControlCommandKind.REGISTER_IDENTITY
        digest = control_command_sha256(command_kind, command)
        async with self._sessions() as preparation:
            async with preparation.begin():
                prepared_executor = await self._authenticated_executor(preparation)
        denial: ProtectedSecurityError | None = None
        async with self._sessions() as session:
            async with session.begin():
                await self._validate_connection(session)
                clock = await PostgresqlTrustedClock.from_session(session)
                control = _ControlSession(session, self._schema, clock)

                # 1. Lock existing protected_identity rows across all planes in deterministic database_login ASC order
                raw_locked_rows = (
                    await control._execute(
                        f"""
                        SELECT database_login, actor_id, actor_namespace, principal_role, identity_plane, approval_role, enabled
                        FROM {control._schema}.protected_identity
                        WHERE database_login = session_user::name
                           OR database_login = :database_login
                           OR (actor_namespace = :actor_namespace AND actor_id = :actor_id)
                        ORDER BY database_login ASC
                        FOR UPDATE
                        """,
                        {
                            "database_login": command.database_login,
                            "actor_namespace": command.actor_namespace,
                            "actor_id": command.actor_id,
                        },
                    )
                ).all()
                locked_rows = [_LockedIdentityRow.from_row(r) for r in raw_locked_rows]

                executor_row = next(
                    (r for r in locked_rows if r.database_login == prepared_executor.database_login),
                    None,
                )
                conflict_rows = [
                    r
                    for r in locked_rows
                    if r.database_login != prepared_executor.database_login
                    and (
                        r.database_login == command.database_login
                        or (
                            (r.actor_namespace, r.actor_id, r.identity_plane)
                            == (command.actor_namespace, command.actor_id, command.identity_plane)
                        )
                    )
                ]

                # 2. Replay check with audit_head lock before any new denial evaluation
                replay = await control.replay(
                    request_id=command.request_id,
                    command_kind=command_kind,
                    command_sha256=digest,
                    executor=prepared_executor.actor,
                    lock_head=True,
                )
                if replay is not None:
                    return replay

                # 3. Denial precedence check
                denial_reason = _evaluate_register_denial(
                    executor_row=executor_row,
                    prepared_executor=prepared_executor,
                    command=command,
                    conflict_count=len(conflict_rows),
                )

                # 4. Atomic insert with ON CONFLICT DO NOTHING (2nd-stage collision defense)
                if denial_reason is None:
                    insert_result = await control._execute(
                        f"""
                        INSERT INTO {control._schema}.protected_identity (
                            database_login, actor_id, actor_namespace, principal_role,
                            identity_plane, approval_role, enabled
                        ) VALUES (
                            :database_login, :actor_id, :actor_namespace, :principal_role,
                            :identity_plane, :approval_role, :enabled
                        )
                        ON CONFLICT DO NOTHING
                        RETURNING database_login
                        """,
                        {
                            "database_login": command.database_login,
                            "actor_id": command.actor_id,
                            "actor_namespace": command.actor_namespace,
                            "principal_role": command.principal_role.value
                            if command.principal_role is not None
                            else None,
                            "identity_plane": command.identity_plane,
                            "approval_role": command.approval_role.value if command.approval_role is not None else None,
                            "enabled": command.enabled,
                        },
                    )
                    inserted_login = insert_result.scalar_one_or_none()
                    if inserted_login is None:
                        denial_reason = ProtectedAuditReason.CONTROL_COMMAND_CONFLICT

                # 5. CONTROL audit append in the same transaction
                await control.refresh_clock()
                entries = await control.verified_entries(lock_head=False)
                if denial_reason is not None:
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=prepared_executor.actor,
                        target_kind=ControlAuditTargetKind.PROTECTED_IDENTITY,
                        target_id=command.database_login,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.DENIED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=denial_reason,
                    )
                    denial = ProtectedSecurityError(denial_reason.value)
                else:
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=prepared_executor.actor,
                        target_kind=ControlAuditTargetKind.PROTECTED_IDENTITY,
                        target_id=command.database_login,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.SUCCEEDED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=ProtectedAuditReason.IDENTITY_REGISTERED,
                    )

        if denial is not None:
            raise denial
        return ControlCommandResult(
            request_id=command.request_id,
            command_kind=command_kind,
            target_id=command.database_login,
            effective_revision=None,
            authorization_audit_event_id=None,
            reason_code="IDENTITY_REGISTERED",
        )

    async def disable_identity(self, command: DisableIdentityCommand) -> ControlCommandResult:
        command_kind = ControlCommandKind.DISABLE_IDENTITY
        digest = control_command_sha256(command_kind, command)
        async with self._sessions() as preparation:
            async with preparation.begin():
                prepared_executor = await self._authenticated_executor(preparation)
        denial: ProtectedSecurityError | None = None
        async with self._sessions() as session:
            async with session.begin():
                await self._validate_connection(session)
                clock = await PostgresqlTrustedClock.from_session(session)
                control = _ControlSession(session, self._schema, clock)

                # 1. Lock executor and target in deterministic database_login ASC order
                logins = sorted(set([prepared_executor.database_login, command.database_login]))
                raw_locked_rows = (
                    await control._execute(
                        f"""
                        SELECT database_login, actor_id, actor_namespace, principal_role, identity_plane, approval_role, enabled
                        FROM {control._schema}.protected_identity
                        WHERE database_login = ANY(:logins)
                        ORDER BY database_login ASC
                        FOR UPDATE
                        """,
                        {"logins": logins},
                    )
                ).all()
                locked_rows = [_LockedIdentityRow.from_row(r) for r in raw_locked_rows]

                executor_row = next(
                    (r for r in locked_rows if r.database_login == prepared_executor.database_login),
                    None,
                )
                target_row = next(
                    (r for r in locked_rows if r.database_login == command.database_login),
                    None,
                )

                # 2. Replay check with audit_head lock before any new denial evaluation
                replay = await control.replay(
                    request_id=command.request_id,
                    command_kind=command_kind,
                    command_sha256=digest,
                    executor=prepared_executor.actor,
                    lock_head=True,
                )
                if replay is not None:
                    return replay

                # 3. Denial precedence check
                denial_reason = _evaluate_disable_denial(
                    executor_row=executor_row,
                    prepared_executor=prepared_executor,
                    target_row=target_row,
                    command=command,
                )

                # 4. Defensive UPDATE with RETURNING database_login
                if denial_reason is None:
                    update_result = await control._execute(
                        f"""
                        UPDATE {control._schema}.protected_identity
                        SET enabled = false
                        WHERE database_login = :database_login
                          AND enabled = true
                        RETURNING database_login
                        """,
                        {"database_login": command.database_login},
                    )
                    disabled_login = update_result.scalar_one_or_none()
                    if disabled_login != command.database_login:
                        denial_reason = ProtectedAuditReason.AUTHORIZATION_NOT_FOUND

                # 5. CONTROL audit append in the same transaction
                await control.refresh_clock()
                entries = await control.verified_entries(lock_head=False)
                if denial_reason is not None:
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=prepared_executor.actor,
                        target_kind=ControlAuditTargetKind.PROTECTED_IDENTITY,
                        target_id=command.database_login,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.DENIED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=denial_reason,
                    )
                    denial = ProtectedSecurityError(denial_reason.value)
                else:
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=prepared_executor.actor,
                        target_kind=ControlAuditTargetKind.PROTECTED_IDENTITY,
                        target_id=command.database_login,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.SUCCEEDED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=ProtectedAuditReason.IDENTITY_DISABLED,
                    )

        if denial is not None:
            raise denial
        return ControlCommandResult(
            request_id=command.request_id,
            command_kind=command_kind,
            target_id=command.database_login,
            effective_revision=None,
            authorization_audit_event_id=None,
            reason_code="IDENTITY_DISABLED",
        )

    async def register_dataset(self, command: RegisterDatasetCommand) -> ControlCommandResult:
        command_kind = ControlCommandKind.REGISTER_DATASET
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

        target_id = f"{command.dataset_id}:{command.dataset_version}"
        denial: ProtectedSecurityError | None = None
        result: ControlCommandResult | None = None
        async with self._sessions() as session:
            async with session.begin():
                await self._validate_connection(session)
                clock = await PostgresqlTrustedClock.from_session(session)
                control = _ControlSession(session, self._schema, clock)

                executor, denial_reason = await self._lock_dataset_control_executor(control, prepared_executor)

                replay = await control.replay(
                    request_id=command.request_id,
                    command_kind=command_kind,
                    command_sha256=digest,
                    executor=executor.actor,
                    lock_head=True,
                )
                if replay is not None:
                    return replay

                if denial_reason is None:
                    existing_row = (
                        await control._execute(
                            f"""
                            SELECT state_revision
                            FROM {control._schema}.protected_dataset
                            WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version
                            FOR UPDATE
                            """,
                            {"dataset_id": command.dataset_id, "dataset_version": command.dataset_version},
                        )
                    ).one_or_none()
                    if existing_row is not None:
                        denial_reason = ProtectedAuditReason.CONTROL_COMMAND_CONFLICT

                await control.refresh_clock()
                entries = await control.verified_entries(lock_head=False)
                if denial_reason is not None:
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=executor.actor,
                        target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
                        target_id=target_id,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.DENIED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=denial_reason,
                    )
                    denial = ProtectedSecurityError(denial_reason.value)
                else:
                    insert_result = await control._execute(
                        f"""
                        INSERT INTO {control._schema}.protected_dataset (
                            dataset_id, dataset_version, binding, manifest_sha256,
                            protected_artifact_sha256, hmac_key_version, state,
                            state_revision, authored_count, review_complete, lock_marker
                        ) VALUES (
                            :dataset_id, :dataset_version, CAST(:binding AS jsonb), :manifest_sha256,
                            :protected_artifact_sha256, :hmac_key_version, 'ACCESS_AUTHORIZED',
                            1, 0, false, 0
                        )
                        ON CONFLICT (dataset_id, dataset_version) DO NOTHING
                        RETURNING state_revision
                        """,
                        {
                            "dataset_id": command.dataset_id,
                            "dataset_version": command.dataset_version,
                            "binding": _json_value(command.binding),
                            "manifest_sha256": command.manifest_sha256,
                            "protected_artifact_sha256": command.protected_artifact_sha256,
                            "hmac_key_version": command.hmac_key_version,
                        },
                    )
                    inserted_row = insert_result.one_or_none()
                    if inserted_row is None:
                        await control.append_control(
                            entries=entries,
                            request_id=command.request_id,
                            command_kind=command_kind,
                            executor=executor.actor,
                            target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
                            target_id=target_id,
                            command_sha256=digest,
                            outcome=ControlAuditOutcome.DENIED,
                            effective_revision=None,
                            authorization_audit_event_id=None,
                            reason_code=ProtectedAuditReason.CONTROL_COMMAND_CONFLICT,
                        )
                        denial = ProtectedSecurityError("CONTROL_COMMAND_CONFLICT")
                    else:
                        effective_revision = 1
                        await control.append_control(
                            entries=entries,
                            request_id=command.request_id,
                            command_kind=command_kind,
                            executor=executor.actor,
                            target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
                            target_id=target_id,
                            command_sha256=digest,
                            outcome=ControlAuditOutcome.SUCCEEDED,
                            effective_revision=effective_revision,
                            authorization_audit_event_id=None,
                            reason_code=ProtectedAuditReason.DATASET_REGISTERED,
                        )
                        result = ControlCommandResult(
                            request_id=command.request_id,
                            command_kind=command_kind,
                            target_id=target_id,
                            effective_revision=effective_revision,
                            authorization_audit_event_id=None,
                            reason_code=ProtectedAuditReason.DATASET_REGISTERED.value,
                        )

        if denial is not None:
            raise denial
        assert result is not None
        return result

    async def _lock_dataset_control_executor(
        self, control: _ControlSession, prepared_executor: _ControlExecutor
    ) -> tuple[_ControlExecutor, ProtectedAuditReason | None]:
        try:
            executor = await control.lock_dataset_executor(prepared_executor)
            return executor, None
        except ProtectedSecurityError as err:
            if err.reason_code in (
                ProtectedAuditReason.ISSUER_ROLE_DENIED.value,
                ProtectedAuditReason.SELF_APPROVAL_DENIED.value,
            ):
                executor = await control.resolve_executor()
                return executor, ProtectedAuditReason(err.reason_code)
            raise

    async def transition_dataset(self, command: TransitionDatasetCommand) -> ControlCommandResult:
        if command.to_state is ProtectedDatasetState.FROZEN:
            raise ProtectedSecurityError("ROLE_ACTION_STATE_DENIED")

        command_kind = ControlCommandKind.TRANSITION_DATASET
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

        target_id = f"{command.dataset_id}:{command.dataset_version}"
        denial: ProtectedSecurityError | None = None
        result: ControlCommandResult | None = None
        async with self._sessions() as session:
            async with session.begin():
                await self._validate_connection(session)
                clock = await PostgresqlTrustedClock.from_session(session)
                control = _ControlSession(session, self._schema, clock)

                executor, denial_reason = await self._lock_dataset_control_executor(control, prepared_executor)
                replay = await control.replay(
                    request_id=command.request_id,
                    command_kind=command_kind,
                    command_sha256=digest,
                    executor=executor.actor,
                    lock_head=True,
                )
                if replay is not None:
                    return replay

                if denial_reason is None:
                    denial_reason = _validate_dataset_transition_invariants(command)
                if denial_reason is None:
                    denial_reason = await control.check_dataset_transition(command)

                await control.refresh_clock()
                entries = await control.verified_entries(lock_head=False)
                if denial_reason is not None:
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=executor.actor,
                        target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
                        target_id=target_id,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.DENIED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=denial_reason,
                    )
                    denial = ProtectedSecurityError(denial_reason.value)
                else:
                    new_revision = command.expected_state_revision + 1
                    update_result = await control._execute(
                        f"""
                        UPDATE {control._schema}.protected_dataset
                        SET state = :to_state,
                            state_revision = :new_revision,
                            authored_count = :authored_count,
                            review_complete = :review_complete
                        WHERE dataset_id = :dataset_id
                          AND dataset_version = :dataset_version
                          AND state = :from_state
                          AND state_revision = :expected_revision
                        RETURNING state_revision
                        """,
                        {
                            "dataset_id": command.dataset_id,
                            "dataset_version": command.dataset_version,
                            "to_state": command.to_state.value,
                            "new_revision": new_revision,
                            "authored_count": command.authored_count,
                            "review_complete": command.review_complete,
                            "from_state": command.from_state.value,
                            "expected_revision": command.expected_state_revision,
                        },
                        fallback="DATASET_STATE_MISMATCH",
                    )
                    updated_row = update_result.one_or_none()
                    if updated_row is None:
                        await control.append_control(
                            entries=entries,
                            request_id=command.request_id,
                            command_kind=command_kind,
                            executor=executor.actor,
                            target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
                            target_id=target_id,
                            command_sha256=digest,
                            outcome=ControlAuditOutcome.DENIED,
                            effective_revision=None,
                            authorization_audit_event_id=None,
                            reason_code=ProtectedAuditReason.DATASET_STATE_MISMATCH,
                        )
                        denial = ProtectedSecurityError("DATASET_STATE_MISMATCH")
                    else:
                        await control.append_control(
                            entries=entries,
                            request_id=command.request_id,
                            command_kind=command_kind,
                            executor=executor.actor,
                            target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
                            target_id=target_id,
                            command_sha256=digest,
                            outcome=ControlAuditOutcome.SUCCEEDED,
                            effective_revision=new_revision,
                            authorization_audit_event_id=None,
                            reason_code=ProtectedAuditReason.DATASET_TRANSITIONED,
                        )
                        result = ControlCommandResult(
                            request_id=command.request_id,
                            command_kind=command_kind,
                            target_id=target_id,
                            effective_revision=new_revision,
                            authorization_audit_event_id=None,
                            reason_code=ProtectedAuditReason.DATASET_TRANSITIONED.value,
                        )

        if denial is not None:
            raise denial
        assert result is not None
        return result

    async def _assert_dataset_custodian_preflight(self, control: _ControlSession, prepared: _ControlExecutor) -> None:
        if prepared.principal.role is not ProtectedApprovalRole.DATASET_CUSTODIAN:
            raise ProtectedSecurityError("ISSUER_ROLE_DENIED")
        result = await control._execute(
            f"""
            SELECT 1 FROM {control._schema}.protected_identity
            WHERE actor_id = :actor_id AND actor_namespace = :actor_namespace
              AND identity_plane = 'DATA'
              AND principal_role = 'HOLDOUT_AUTHOR'
              AND enabled = true
            """,
            {"actor_id": prepared.actor.actor_id, "actor_namespace": prepared.actor.namespace},
        )
        if result.first() is not None:
            raise ProtectedSecurityError("SELF_APPROVAL_DENIED")

    async def _check_freeze_mutation(
        self,
        control: _ControlSession,
        command: FreezeDatasetCommand,
        executor: _ControlExecutor,
        evidence: FreezeApprovalSourceEvidence | None,
        fetch_denial: ProtectedAuditReason | None,
    ) -> ProtectedAuditReason | None:
        dataset_row = (
            await control._execute(
                f"""
                SELECT dataset_id, dataset_version, binding, manifest_sha256,
                       protected_artifact_sha256, hmac_key_version, state,
                       state_revision, authored_count, review_complete, lock_marker
                FROM {control._schema}.protected_dataset
                WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version
                FOR UPDATE
                """,
                {"dataset_id": command.dataset_id, "dataset_version": command.dataset_version},
            )
        ).one_or_none()
        if dataset_row is None:
            return ProtectedAuditReason.DATASET_STATE_MISMATCH
        if (
            dataset_row.state != ProtectedDatasetState.REVIEW_READY.value
            or dataset_row.state_revision != command.expected_state_revision
        ):
            return ProtectedAuditReason.DATASET_STATE_MISMATCH

        entries = await control.verified_entries(lock_head=False)
        dataset = _assemble_dataset_binding(
            binding_value=dataset_row.binding,
            dataset_id=dataset_row.dataset_id,
            dataset_version=dataset_row.dataset_version,
            manifest_sha256=dataset_row.manifest_sha256,
            protected_artifact_sha256=dataset_row.protected_artifact_sha256,
            hmac_key_version=dataset_row.hmac_key_version,
            state=dataset_row.state,
            state_revision=dataset_row.state_revision,
            authored_count=dataset_row.authored_count,
            review_complete=dataset_row.review_complete,
            audit_entries=entries,
        )
        if (
            dataset.authored_count != 40
            or not dataset.review_complete
            or dataset.leakage_axis_intersections != (0, 0, 0, 0)
        ):
            return ProtectedAuditReason.FREEZE_EVIDENCE_INCOMPLETE

        if fetch_denial is not None:
            return fetch_denial
        if evidence is None:
            return ProtectedAuditReason.APPROVAL_NOT_VERIFIED

        try:
            verify_freeze_approval(
                dataset,
                evidence,
                approval_source_event_id=command.approval_source_event_id,
                expected_raw_sha256=command.expected_raw_sha256,
                executor=executor.principal,
            )
        except ProtectedSecurityError as err:
            return _policy_denial_reason(err)
        return None

    async def freeze_dataset(self, command: FreezeDatasetCommand) -> ControlCommandResult:
        command_kind = ControlCommandKind.FREEZE_DATASET
        digest = control_command_sha256(command_kind, command)
        async with self._sessions() as preparation:
            async with preparation.begin():
                await self._validate_connection(preparation)
                clock = await PostgresqlTrustedClock.from_session(preparation)
                prep_control = _ControlSession(preparation, self._schema, clock)
                prepared_executor = await prep_control.resolve_executor()
                await self._assert_dataset_custodian_preflight(prep_control, prepared_executor)

        replay = await self._read_replay(
            request_id=command.request_id,
            command_kind=command_kind,
            command_sha256=digest,
        )
        if replay is not None:
            return replay

        evidence, fetch_denial = await self._fetch_freeze_approval(command)

        target_id = f"{command.dataset_id}:{command.dataset_version}"
        denial: ProtectedSecurityError | None = None
        result: ControlCommandResult | None = None
        async with self._sessions() as session:
            async with session.begin():
                await self._validate_connection(session)
                clock = await PostgresqlTrustedClock.from_session(session)
                control = _ControlSession(session, self._schema, clock)

                executor, lock_denial = await self._lock_dataset_control_executor(control, prepared_executor)
                replay = await control.replay(
                    request_id=command.request_id,
                    command_kind=command_kind,
                    command_sha256=digest,
                    executor=executor.actor,
                    lock_head=True,
                )
                if replay is not None:
                    return replay

                denial_reason = lock_denial
                if denial_reason is None:
                    denial_reason = await self._check_freeze_mutation(
                        control, command, executor, evidence, fetch_denial
                    )

                await control.refresh_clock()
                entries = await control.verified_entries(lock_head=False)
                if denial_reason is not None:
                    await control.append_control(
                        entries=entries,
                        request_id=command.request_id,
                        command_kind=command_kind,
                        executor=executor.actor,
                        target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
                        target_id=target_id,
                        command_sha256=digest,
                        outcome=ControlAuditOutcome.DENIED,
                        effective_revision=None,
                        authorization_audit_event_id=None,
                        reason_code=denial_reason,
                    )
                    denial = ProtectedSecurityError(denial_reason.value)
                else:
                    new_revision = command.expected_state_revision + 1
                    update_result = await control._execute(
                        f"""
                        UPDATE {control._schema}.protected_dataset
                        SET state = 'FROZEN',
                            state_revision = :new_revision
                        WHERE dataset_id = :dataset_id
                          AND dataset_version = :dataset_version
                          AND state = 'REVIEW_READY'
                          AND state_revision = :expected_revision
                        RETURNING state_revision
                        """,
                        {
                            "dataset_id": command.dataset_id,
                            "dataset_version": command.dataset_version,
                            "new_revision": new_revision,
                            "expected_revision": command.expected_state_revision,
                        },
                        fallback="DATASET_STATE_MISMATCH",
                    )
                    updated_row = update_result.one_or_none()
                    if updated_row is None:
                        await control.append_control(
                            entries=entries,
                            request_id=command.request_id,
                            command_kind=command_kind,
                            executor=executor.actor,
                            target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
                            target_id=target_id,
                            command_sha256=digest,
                            outcome=ControlAuditOutcome.DENIED,
                            effective_revision=None,
                            authorization_audit_event_id=None,
                            reason_code=ProtectedAuditReason.DATASET_STATE_MISMATCH,
                        )
                        denial = ProtectedSecurityError("DATASET_STATE_MISMATCH")
                    else:
                        await control.append_control(
                            entries=entries,
                            request_id=command.request_id,
                            command_kind=command_kind,
                            executor=executor.actor,
                            target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
                            target_id=target_id,
                            command_sha256=digest,
                            outcome=ControlAuditOutcome.SUCCEEDED,
                            effective_revision=new_revision,
                            authorization_audit_event_id=None,
                            reason_code=ProtectedAuditReason.DATASET_FROZEN,
                        )
                        result = ControlCommandResult(
                            request_id=command.request_id,
                            command_kind=command_kind,
                            target_id=target_id,
                            effective_revision=new_revision,
                            authorization_audit_event_id=None,
                            reason_code=ProtectedAuditReason.DATASET_FROZEN.value,
                        )
        if denial is not None:
            raise denial
        assert result is not None
        return result


__all__ = ["PostgresqlProtectedAuthorizationControlService"]
