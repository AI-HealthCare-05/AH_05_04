"""Real PostgreSQL tests for protected authorization-control commands."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    AsyncTransaction,
    async_sessionmaker,
    create_async_engine,
)

from ai_worker.adapters import postgresql_protected_retrieval_control as control_adapter
from ai_worker.adapters.postgresql_protected_retrieval import (
    PostgresqlProtectedAuditJournal,
    PostgresqlTrustedClock,
    _assemble_dataset_binding,
)
from ai_worker.adapters.postgresql_protected_retrieval_control import (
    PostgresqlProtectedAuthorizationControlService,
)
from ai_worker.tasks.evaluation.protected_retrieval import (
    ActorIdentity,
    ApprovalSourceEvidence,
    AuthorizationAuditAction,
    AuthorizationAuditEntry,
    ControlAuditOutcome,
    ControlCommandAuditEntry,
    ControlImplementationBinding,
    OpaqueLogicalRef,
    OpaqueRefNamespace,
    ProtectedAction,
    ProtectedApprovalPrincipal,
    ProtectedApprovalRole,
    ProtectedAuditEntry,
    ProtectedAuditEventKind,
    ProtectedAuditReason,
    ProtectedAuthorizationGrant,
    ProtectedDatasetBinding,
    ProtectedDatasetState,
    ProtectedPrincipal,
    ProtectedPrincipalRole,
    ProtectedSecurityError,
    audit_entry_sha256,
    authorization_grant_approval_sha256,
)
from ai_worker.tasks.evaluation.protected_retrieval_control import (
    ApprovalSourceNotFoundError,
    ControlCommandResult,
    DisableIdentityCommand,
    ExpireAuthorizationCommand,
    FreezeApprovalLocator,
    FreezeApprovalSourceEvidence,
    FreezeDatasetCommand,
    GrantAuthorizationCommand,
    IngestApprovalCommand,
    RegisterDatasetCommand,
    RegisterIdentityCommand,
    RevokeAuthorizationCommand,
    TransitionDatasetCommand,
)
from tests.migration.test_protected_retrieval_migration import _login_url, _ProtectedDatabase

pytest_plugins = ("tests.migration.test_protected_retrieval_migration",)


class _ApprovalSource:
    def __init__(self, evidence: ApprovalSourceEvidence) -> None:
        self.evidence = evidence
        self.calls: list[str] = []

    async def fetch(self, source_event_id: str) -> ApprovalSourceEvidence:
        self.calls.append(source_event_id)
        return self.evidence

    async def fetch_freeze(
        self,
        source_event_id: str,
        locator: FreezeApprovalLocator | None = None,
    ) -> FreezeApprovalSourceEvidence:
        del source_event_id, locator
        raise ApprovalSourceNotFoundError


class _FailingApprovalSource:
    async def fetch(self, source_event_id: str) -> ApprovalSourceEvidence:
        del source_event_id
        raise RuntimeError("synthetic connector detail that must not escape")

    async def fetch_freeze(
        self,
        source_event_id: str,
        locator: FreezeApprovalLocator | None = None,
    ) -> FreezeApprovalSourceEvidence:
        del source_event_id, locator
        raise ApprovalSourceNotFoundError


class _MissingApprovalSource:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, source_event_id: str) -> ApprovalSourceEvidence:
        self.calls.append(source_event_id)
        raise ApprovalSourceNotFoundError

    async def fetch_freeze(
        self,
        source_event_id: str,
        locator: FreezeApprovalLocator | None = None,
    ) -> FreezeApprovalSourceEvidence:
        del locator
        self.calls.append(source_event_id)
        raise ApprovalSourceNotFoundError


class _DatasetApprovalSource:
    def __init__(self, freeze_evidence: FreezeApprovalSourceEvidence | None = None) -> None:
        self.freeze_evidence = freeze_evidence
        self.freeze_calls: list[str] = []
        self.freeze_locators: list[FreezeApprovalLocator | None] = []

    async def fetch(self, source_event_id: str) -> ApprovalSourceEvidence:
        del source_event_id
        raise ApprovalSourceNotFoundError

    async def fetch_freeze(
        self,
        source_event_id: str,
        locator: FreezeApprovalLocator | None = None,
    ) -> FreezeApprovalSourceEvidence:
        self.freeze_calls.append(source_event_id)
        self.freeze_locators.append(locator)
        if self.freeze_evidence is None:
            raise ApprovalSourceNotFoundError
        return self.freeze_evidence

    @property
    def evidence(self) -> FreezeApprovalSourceEvidence:
        assert self.freeze_evidence is not None
        return self.freeze_evidence


def _evidence(
    source_event_id: str,
    raw_sha256: str,
    *,
    grant: ProtectedAuthorizationGrant | None = None,
    action: AuthorizationAuditAction = AuthorizationAuditAction.GRANT,
) -> ApprovalSourceEvidence:
    issuer = (
        grant.issuer
        if grant is not None
        else ProtectedApprovalPrincipal(
            actor=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
            role=ProtectedApprovalRole.DATASET_CUSTODIAN,
        )
    )
    return ApprovalSourceEvidence(
        source_event_id=source_event_id,
        authorization_action=action,
        approved_grant_payload_sha256=(authorization_grant_approval_sha256(grant) if grant is not None else "b" * 64),
        issuer=issuer,
        state="APPROVED",
        recorded_at=datetime(2026, 9, 11, 1, 2, 3, tzinfo=UTC),
        target_commit_oid=grant.control_implementation.commit_oid if grant is not None else "c" * 40,
        target_artifact_sha256=(grant.control_implementation.artifact_sha256 if grant is not None else "d" * 64),
        canonical_raw_sha256=raw_sha256,
        implementation_participants=(
            grant.control_implementation.participants
            if grant is not None
            else (ActorIdentity(actor_id="synthetic-implementer", namespace="GITHUB_LOGIN"),)
        ),
    )


def _grant_scenario(
    *,
    expired: bool = False,
) -> tuple[ProtectedDatasetBinding, ProtectedAuthorizationGrant, ApprovalSourceEvidence]:
    dataset = ProtectedDatasetBinding(
        dataset_id=f"control-dataset-{uuid4()}",
        dataset_version="1.0.0",
        manifest_sha256="1" * 64,
        protected_artifact_sha256="2" * 64,
        hmac_key_version="synthetic-key-v1",
        state=ProtectedDatasetState.AUTHORING,
        state_revision=1,
        authored_count=0,
        review_complete=False,
    )
    now = datetime.now(UTC)
    grant = ProtectedAuthorizationGrant(
        grant_id=str(uuid4()),
        revision=1,
        subject=ProtectedPrincipal(
            actor=ActorIdentity(actor_id="synthetic-author", namespace="SERVICE_IDENTITY"),
            role=ProtectedPrincipalRole.HOLDOUT_AUTHOR,
        ),
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        manifest_sha256=dataset.manifest_sha256,
        protected_artifact_sha256=dataset.protected_artifact_sha256,
        hmac_key_version=dataset.hmac_key_version,
        actions=(ProtectedAction.READ, ProtectedAction.WRITE),
        issuer=ProtectedApprovalPrincipal(
            actor=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
            role=ProtectedApprovalRole.DATASET_CUSTODIAN,
        ),
        control_implementation=ControlImplementationBinding(
            commit_oid="3" * 40,
            artifact_sha256="4" * 64,
            participants=(ActorIdentity(actor_id="synthetic-implementer", namespace="GITHUB_LOGIN"),),
        ),
        approval_source_event_id=f"grant-approval-{uuid4()}",
        approval_source_raw_sha256="5" * 64,
        valid_from=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10) if expired else now + timedelta(minutes=10),
    )
    return (
        dataset,
        grant,
        _evidence(
            grant.approval_source_event_id,
            grant.approval_source_raw_sha256,
            grant=grant,
        ),
    )


async def _insert_dataset(database: _ProtectedDatabase, dataset: ProtectedDatasetBinding) -> None:
    engine = create_async_engine(database.url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    f'''INSERT INTO "{database.schema}".protected_dataset (
                        dataset_id, dataset_version, binding, manifest_sha256,
                        protected_artifact_sha256, hmac_key_version, state,
                        state_revision, authored_count, review_complete
                    ) VALUES (
                        :dataset_id, :dataset_version, CAST(:binding AS jsonb), :manifest_sha256,
                        :artifact_sha256, :key_version, :state, :state_revision,
                        :authored_count, :review_complete
                    )'''
                ),
                {
                    "dataset_id": dataset.dataset_id,
                    "dataset_version": dataset.dataset_version,
                    "binding": dataset.model_dump_json(),
                    "manifest_sha256": dataset.manifest_sha256,
                    "artifact_sha256": dataset.protected_artifact_sha256,
                    "key_version": dataset.hmac_key_version,
                    "state": dataset.state.value,
                    "state_revision": dataset.state_revision,
                    "authored_count": dataset.authored_count,
                    "review_complete": dataset.review_complete,
                },
            )
    finally:
        await engine.dispose()


async def _insert_grant(
    database: _ProtectedDatabase,
    grant: ProtectedAuthorizationGrant,
    *,
    record_grant_audit: bool = True,
) -> None:
    engine = create_async_engine(database.url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    f'''INSERT INTO "{database.schema}".authorization_grant (
                        grant_id, revision, effective_revision, grant_body,
                        subject_actor_id, subject_namespace, subject_role,
                        dataset_id, dataset_version, manifest_sha256,
                        protected_artifact_sha256, hmac_key_version, actions,
                        valid_from, expires_at
                    ) VALUES (
                        CAST(:grant_id AS uuid), :revision, :revision, CAST(:grant_body AS jsonb),
                        :actor_id, :actor_namespace, :subject_role,
                        :dataset_id, :dataset_version, :manifest_sha256,
                        :artifact_sha256, :key_version, :actions, :valid_from, :expires_at
                    )'''
                ),
                {
                    "grant_id": grant.grant_id,
                    "revision": grant.revision,
                    "grant_body": grant.model_dump_json(),
                    "actor_id": grant.subject.actor.actor_id,
                    "actor_namespace": grant.subject.actor.namespace,
                    "subject_role": grant.subject.role.value,
                    "dataset_id": grant.dataset_id,
                    "dataset_version": grant.dataset_version,
                    "manifest_sha256": grant.manifest_sha256,
                    "artifact_sha256": grant.protected_artifact_sha256,
                    "key_version": grant.hmac_key_version,
                    "actions": [action.value for action in grant.actions],
                    "valid_from": grant.valid_from,
                    "expires_at": grant.expires_at,
                },
            )
            if not record_grant_audit:
                return
            head = (
                await connection.execute(
                    text(
                        f'''SELECT sequence, entry_sha256 FROM "{database.schema}".audit_head
                        WHERE singleton FOR UPDATE'''
                    )
                )
            ).one()
            entry = AuthorizationAuditEntry(
                event_kind=ProtectedAuditEventKind.AUTHORIZATION,
                sequence=head.sequence + 1,
                event_id=str(uuid4()),
                grant_id=grant.grant_id,
                grant_revision=grant.revision,
                effective_revision=grant.revision,
                subject=grant.subject,
                issuer=grant.issuer,
                dataset_id=grant.dataset_id,
                dataset_version=grant.dataset_version,
                manifest_sha256=grant.manifest_sha256,
                protected_artifact_sha256=grant.protected_artifact_sha256,
                hmac_key_version=grant.hmac_key_version,
                actions=grant.actions,
                control_implementation=grant.control_implementation,
                approval_source_event_id=grant.approval_source_event_id,
                approval_source_raw_sha256=grant.approval_source_raw_sha256,
                valid_from=grant.valid_from,
                expires_at=grant.expires_at,
                action=AuthorizationAuditAction.GRANT,
                reason_code=ProtectedAuditReason.APPROVAL_VERIFIED,
                recorded_at=datetime.now(UTC),
                previous_entry_sha256=head.entry_sha256,
                entry_sha256="0" * 64,
            )
            entry = entry.model_copy(update={"entry_sha256": audit_entry_sha256(entry)})
            await connection.execute(
                text(
                    f'''INSERT INTO "{database.schema}".audit_entry (
                        sequence, event_id, event_kind, operation_key, entry_body,
                        previous_entry_sha256, entry_sha256, recorded_at, control_entry
                    ) VALUES (
                        :sequence, CAST(:event_id AS uuid), 'AUTHORIZATION', NULL, CAST(:entry_body AS jsonb),
                        :previous_entry_sha256, :entry_sha256, :recorded_at, true
                    )'''
                ),
                {
                    "sequence": entry.sequence,
                    "event_id": entry.event_id,
                    "entry_body": entry.model_dump_json(),
                    "previous_entry_sha256": entry.previous_entry_sha256,
                    "entry_sha256": entry.entry_sha256,
                    "recorded_at": entry.recorded_at,
                },
            )
            await connection.execute(
                text(
                    f'''UPDATE "{database.schema}".audit_head
                    SET sequence = :sequence, entry_sha256 = :entry_sha256 WHERE singleton'''
                ),
                {"sequence": entry.sequence, "entry_sha256": entry.entry_sha256},
            )
    finally:
        await engine.dispose()


def _service(
    database: _ProtectedDatabase,
    source: _ApprovalSource | _FailingApprovalSource | _MissingApprovalSource | _DatasetApprovalSource,
    *,
    login: str | None = None,
) -> PostgresqlProtectedAuthorizationControlService:
    engine = create_async_engine(
        _login_url(database, login or database.control_login),
        connect_args={"server_settings": {"application_name": "protected-retrieval-control-test"}},
    )
    return PostgresqlProtectedAuthorizationControlService(
        engine,
        schema=database.schema,
        data_access_role=database.access,
        control_role=database.control,
        approval_source=source,
    )


@pytest.mark.asyncio
async def test_ingest_rejects_unauthorized_login_before_source_use(
    protected_database: _ProtectedDatabase,
) -> None:
    evidence = _evidence(f"approval-{uuid4()}", "a" * 64)
    source = _ApprovalSource(evidence)
    service = _service(protected_database, source, login=protected_database.actor_login)
    try:
        command = IngestApprovalCommand(
            request_id=str(uuid4()),
            source_event_id=evidence.source_event_id,
            expected_raw_sha256=evidence.canonical_raw_sha256,
        )

        with pytest.raises(ProtectedSecurityError, match="AUTHORIZATION_NOT_FOUND"):
            await service.ingest_approval(command)

        assert source.calls == []
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_ingest_connector_failure_is_retryable_and_not_a_policy_denial(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    service = _service(database, _FailingApprovalSource())
    request_id = str(uuid4())
    try:
        with pytest.raises(ProtectedSecurityError, match="^INTERNAL_ERROR$") as captured:
            await service.ingest_approval(
                IngestApprovalCommand(
                    request_id=request_id,
                    source_event_id=f"approval-{uuid4()}",
                    expected_raw_sha256="a" * 64,
                )
            )
        assert "synthetic connector" not in str(captured.value)

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                audit_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".audit_entry
                        WHERE event_id = CAST(:request_id AS uuid)'''
                    ),
                    {"request_id": request_id},
                )
            assert audit_count == 0
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_ingest_missing_source_commits_one_policy_denial_and_replays(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    source = _MissingApprovalSource()
    service = _service(database, source)
    command = IngestApprovalCommand(
        request_id=str(uuid4()),
        source_event_id=f"approval-{uuid4()}",
        expected_raw_sha256="a" * 64,
    )
    try:
        for _ in range(2):
            with pytest.raises(ProtectedSecurityError, match="^APPROVAL_NOT_VERIFIED$"):
                await service.ingest_approval(command)
        assert source.calls == [command.source_event_id]

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                denial_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".audit_entry
                        WHERE event_id = CAST(:request_id AS uuid)
                          AND entry_body->>'reason_code' = 'APPROVAL_NOT_VERIFIED' '''
                    ),
                    {"request_id": command.request_id},
                )
            assert denial_count == 1
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_missing_grant_denial_is_committed_once_and_replayed(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    evidence = _evidence(f"approval-{uuid4()}", "a" * 64)
    service = _service(database, _ApprovalSource(evidence))
    command = ExpireAuthorizationCommand(
        request_id=str(uuid4()),
        grant_id=str(uuid4()),
        expected_effective_revision=1,
    )
    try:
        for _ in range(2):
            with pytest.raises(ProtectedSecurityError, match="^AUTHORIZATION_NOT_FOUND$"):
                await service.expire(command)
        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                denial_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".audit_entry
                        WHERE event_id = CAST(:request_id AS uuid)
                          AND entry_body->>'reason_code' = 'AUTHORIZATION_NOT_FOUND' '''
                    ),
                    {"request_id": command.request_id},
                )
            assert denial_count == 1
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_ingest_persists_once_replays_and_rejects_changed_command(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    evidence = _evidence(f"approval-{uuid4()}", "a" * 64)
    source = _ApprovalSource(evidence)
    service = _service(database, source)
    command = IngestApprovalCommand(
        request_id=str(uuid4()),
        source_event_id=evidence.source_event_id,
        expected_raw_sha256=evidence.canonical_raw_sha256,
    )
    try:
        first = await service.ingest_approval(command)
        replay = await service.ingest_approval(command)

        assert replay == first
        assert first.target_id == evidence.source_event_id
        assert first.reason_code == "APPROVAL_VERIFIED"

        changed = command.model_copy(update={"expected_raw_sha256": "e" * 64})
        with pytest.raises(ProtectedSecurityError, match="CONTROL_COMMAND_CONFLICT"):
            await service.ingest_approval(changed)
        assert source.calls == [command.source_event_id]

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                evidence_count = await connection.scalar(
                    text(
                        f'SELECT count(*) FROM "{database.schema}".approval_evidence '
                        "WHERE source_event_id = :source_event_id"
                    ),
                    {"source_event_id": evidence.source_event_id},
                )
                audit_rows = (
                    await connection.execute(
                        text(
                            f'''SELECT event_kind, entry_body->>'command_kind', entry_body->>'outcome'
                            FROM "{database.schema}".audit_entry
                            WHERE event_id = CAST(:request_id AS uuid)'''
                        ),
                        {"request_id": command.request_id},
                    )
                ).all()
            assert evidence_count == 1
            assert audit_rows == [("CONTROL", "INGEST_APPROVAL", "SUCCEEDED")]
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_ingest_source_mismatch_leaves_no_evidence(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    evidence = _evidence(f"approval-{uuid4()}", "a" * 64)
    source = _ApprovalSource(evidence.model_copy(update={"source_event_id": f"other-{uuid4()}"}))
    service = _service(database, source)
    command = IngestApprovalCommand(
        request_id=str(uuid4()),
        source_event_id=evidence.source_event_id,
        expected_raw_sha256=evidence.canonical_raw_sha256,
    )
    try:
        for _ in range(2):
            with pytest.raises(ProtectedSecurityError, match="APPROVAL_EVIDENCE_MISMATCH") as captured:
                await service.ingest_approval(command)
        assert evidence.issuer.actor.actor_id not in str(captured.value)
        assert source.calls == [command.source_event_id]

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                count = await connection.scalar(
                    text(
                        f'SELECT count(*) FROM "{database.schema}".approval_evidence '
                        "WHERE source_event_id = :source_event_id"
                    ),
                    {"source_event_id": evidence.source_event_id},
                )
                denial_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".audit_entry
                        WHERE event_id = CAST(:request_id AS uuid)
                          AND entry_body->>'outcome' = 'DENIED'
                          AND entry_body->>'reason_code' = 'APPROVAL_EVIDENCE_MISMATCH' '''
                    ),
                    {"request_id": command.request_id},
                )
            assert count == 0
            assert denial_count == 1
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_ingest_rejects_changed_evidence_for_an_immutable_source_and_audits_denial(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    evidence = _evidence(f"approval-{uuid4()}", "a" * 64)
    source = _ApprovalSource(evidence)
    service = _service(database, source)
    try:
        await service.ingest_approval(
            IngestApprovalCommand(
                request_id=str(uuid4()),
                source_event_id=evidence.source_event_id,
                expected_raw_sha256=evidence.canonical_raw_sha256,
            )
        )
        changed = evidence.model_copy(update={"canonical_raw_sha256": "b" * 64})
        source.evidence = changed
        command = IngestApprovalCommand(
            request_id=str(uuid4()),
            source_event_id=evidence.source_event_id,
            expected_raw_sha256=changed.canonical_raw_sha256,
        )
        for _ in range(2):
            with pytest.raises(ProtectedSecurityError, match="^CONTROL_COMMAND_CONFLICT$"):
                await service.ingest_approval(command)

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                denial_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".audit_entry
                        WHERE event_id = CAST(:request_id AS uuid)
                          AND entry_body->>'outcome' = 'DENIED'
                          AND entry_body->>'reason_code' = 'CONTROL_COMMAND_CONFLICT' '''
                    ),
                    {"request_id": command.request_id},
                )
            assert denial_count == 1
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_grant_commits_mutation_and_ordered_audits_together(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    dataset, grant, evidence = _grant_scenario()
    await _insert_dataset(database, dataset)
    source = _ApprovalSource(evidence)
    service = _service(database, source)
    try:
        await service.ingest_approval(
            IngestApprovalCommand(
                request_id=str(uuid4()),
                source_event_id=evidence.source_event_id,
                expected_raw_sha256=evidence.canonical_raw_sha256,
            )
        )
        command = GrantAuthorizationCommand(
            request_id=str(uuid4()),
            grant=grant,
            expected_dataset_state_revision=dataset.state_revision,
        )

        result = await service.grant(command)
        replay = await service.grant(command)

        assert replay == result
        assert result.target_id == grant.grant_id
        assert result.effective_revision == 1
        assert result.reason_code == "AUTHORIZED"
        assert result.authorization_audit_event_id is not None

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                stored = (
                    await connection.execute(
                        text(
                            f'''SELECT revision, effective_revision, grant_body
                            FROM "{database.schema}".authorization_grant
                            WHERE grant_id = CAST(:grant_id AS uuid)'''
                        ),
                        {"grant_id": grant.grant_id},
                    )
                ).one()
                audit_kinds = list(
                    await connection.scalars(
                        text(
                            f'''SELECT event_kind FROM "{database.schema}".audit_entry
                            WHERE entry_body->>'target_id' = :grant_id
                               OR entry_body->>'grant_id' = :grant_id
                            ORDER BY sequence'''
                        ),
                        {"grant_id": grant.grant_id},
                    )
                )
            assert stored.revision == stored.effective_revision == grant.revision
            assert ProtectedAuthorizationGrant.model_validate_json(json.dumps(stored.grant_body)) == grant
            assert audit_kinds == ["AUTHORIZATION", "CONTROL"]
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_grant_rejects_dataset_key_body_mismatch_without_mutation(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    dataset, grant, evidence = _grant_scenario()
    await _insert_dataset(database, dataset)
    admin_engine = create_async_engine(database.url)
    try:
        async with admin_engine.begin() as connection:
            mismatched = dataset.model_copy(update={"dataset_id": f"other-{uuid4()}"})
            await connection.execute(
                text(
                    f'''UPDATE "{database.schema}".protected_dataset
                    SET binding = CAST(:binding AS jsonb)
                    WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version'''
                ),
                {
                    "binding": mismatched.model_dump_json(),
                    "dataset_id": dataset.dataset_id,
                    "dataset_version": dataset.dataset_version,
                },
            )
    finally:
        await admin_engine.dispose()

    source = _ApprovalSource(evidence)
    service = _service(database, source)
    try:
        await service.ingest_approval(
            IngestApprovalCommand(
                request_id=str(uuid4()),
                source_event_id=evidence.source_event_id,
                expected_raw_sha256=evidence.canonical_raw_sha256,
            )
        )
        with pytest.raises(ProtectedSecurityError, match="DATASET_BINDING_MISMATCH"):
            await service.grant(
                GrantAuthorizationCommand(
                    request_id=str(uuid4()),
                    grant=grant,
                    expected_dataset_state_revision=dataset.state_revision,
                )
            )

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".authorization_grant
                        WHERE grant_id = CAST(:grant_id AS uuid)'''
                    ),
                    {"grant_id": grant.grant_id},
                )
            assert count == 0
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_grant_replay_is_bound_to_the_original_control_executor(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    dataset, grant, evidence = _grant_scenario()
    await _insert_dataset(database, dataset)
    source = _ApprovalSource(evidence)
    service = _service(database, source)
    alternate_service = _service(database, source, login=database.denied_login)
    admin_engine = create_async_engine(database.url)
    command = GrantAuthorizationCommand(
        request_id=str(uuid4()), grant=grant, expected_dataset_state_revision=dataset.state_revision
    )
    try:
        await service.ingest_approval(
            IngestApprovalCommand(
                request_id=str(uuid4()),
                source_event_id=evidence.source_event_id,
                expected_raw_sha256=evidence.canonical_raw_sha256,
            )
        )
        await service.grant(command)
        async with admin_engine.begin() as connection:
            await connection.exec_driver_sql(f'GRANT "{database.control}" TO "{database.denied_login}"')
            await connection.execute(
                text(
                    f'''INSERT INTO "{database.schema}".protected_identity (
                        database_login, actor_id, actor_namespace, approval_role, identity_plane
                    ) VALUES (:login, :actor_id, 'GITHUB_LOGIN', 'DATASET_CUSTODIAN', 'CONTROL')'''
                ),
                {"login": database.denied_login, "actor_id": f"alternate-{uuid4()}"},
            )

        with pytest.raises(ProtectedSecurityError, match="^CONTROL_COMMAND_CONFLICT$"):
            await alternate_service.grant(command)
    finally:
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(f'''DELETE FROM "{database.schema}".protected_identity WHERE database_login = :login'''),
                {"login": database.denied_login},
            )
            await connection.exec_driver_sql(f'REVOKE "{database.control}" FROM "{database.denied_login}"')
        await admin_engine.dispose()
        await alternate_service.close()
        await service.close()


@pytest.mark.asyncio
async def test_revoke_requires_approval_and_increments_effective_revision_once(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    dataset, grant, grant_evidence = _grant_scenario()
    await _insert_dataset(database, dataset)
    source = _ApprovalSource(grant_evidence)
    service = _service(database, source)
    try:
        await service.ingest_approval(
            IngestApprovalCommand(
                request_id=str(uuid4()),
                source_event_id=grant_evidence.source_event_id,
                expected_raw_sha256=grant_evidence.canonical_raw_sha256,
            )
        )
        await service.grant(
            GrantAuthorizationCommand(
                request_id=str(uuid4()),
                grant=grant,
                expected_dataset_state_revision=dataset.state_revision,
            )
        )
        revoke_evidence = _evidence(
            f"revoke-approval-{uuid4()}",
            "6" * 64,
            grant=grant,
            action=AuthorizationAuditAction.REVOKE,
        )
        source.evidence = revoke_evidence
        await service.ingest_approval(
            IngestApprovalCommand(
                request_id=str(uuid4()),
                source_event_id=revoke_evidence.source_event_id,
                expected_raw_sha256=revoke_evidence.canonical_raw_sha256,
            )
        )
        command = RevokeAuthorizationCommand(
            request_id=str(uuid4()),
            grant_id=grant.grant_id,
            approval_source_event_id=revoke_evidence.source_event_id,
            expected_raw_sha256=revoke_evidence.canonical_raw_sha256,
            expected_effective_revision=1,
        )

        result = await service.revoke(command)
        assert await service.revoke(command) == result
        assert result.effective_revision == 2
        assert result.reason_code == "REVOKED"
        with pytest.raises(ProtectedSecurityError, match="^AUTHORIZATION_REVOKED$"):
            await service.expire(
                ExpireAuthorizationCommand(
                    request_id=str(uuid4()),
                    grant_id=grant.grant_id,
                    expected_effective_revision=2,
                )
            )

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            f'''SELECT effective_revision, revoked_at FROM "{database.schema}".authorization_grant
                            WHERE grant_id = CAST(:grant_id AS uuid)'''
                        ),
                        {"grant_id": grant.grant_id},
                    )
                ).one()
            assert row.effective_revision == 2
            assert row.revoked_at is not None
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_expire_uses_database_time_and_increments_once(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    dataset, grant, evidence = _grant_scenario(expired=True)
    await _insert_dataset(database, dataset)
    await _insert_grant(database, grant)
    service = _service(database, _ApprovalSource(evidence))
    command = ExpireAuthorizationCommand(
        request_id=str(uuid4()),
        grant_id=grant.grant_id,
        expected_effective_revision=1,
    )
    try:
        result = await service.expire(command)
        assert await service.expire(command) == result
        assert result.effective_revision == 2
        assert result.reason_code == "EXPIRED"
        with pytest.raises(ProtectedSecurityError, match="^AUTHORIZATION_EXPIRED$"):
            await service.revoke(
                RevokeAuthorizationCommand(
                    request_id=str(uuid4()),
                    grant_id=grant.grant_id,
                    approval_source_event_id=f"unused-{uuid4()}",
                    expected_raw_sha256="7" * 64,
                    expected_effective_revision=2,
                )
            )

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            f'''SELECT effective_revision, revoked_at FROM "{database.schema}".authorization_grant
                            WHERE grant_id = CAST(:grant_id AS uuid)'''
                        ),
                        {"grant_id": grant.grant_id},
                    )
                ).one()
                actions = list(
                    await connection.scalars(
                        text(
                            f'''SELECT entry_body->>'action' FROM "{database.schema}".audit_entry
                            WHERE event_kind = 'AUTHORIZATION'
                              AND entry_body->>'grant_id' = :grant_id
                            ORDER BY sequence'''
                        ),
                        {"grant_id": grant.grant_id},
                    )
                )
            assert row == (2, None)
            assert actions == ["GRANT", "EXPIRE"]
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_early_expire_commits_one_denial_and_replays_reason(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    dataset, grant, evidence = _grant_scenario()
    await _insert_dataset(database, dataset)
    await _insert_grant(database, grant)
    service = _service(database, _ApprovalSource(evidence))
    command = ExpireAuthorizationCommand(
        request_id=str(uuid4()),
        grant_id=grant.grant_id,
        expected_effective_revision=1,
    )
    try:
        for _ in range(2):
            with pytest.raises(ProtectedSecurityError, match="AUTHORIZATION_EXPIRED"):
                await service.expire(command)

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                effective_revision = await connection.scalar(
                    text(
                        f'''SELECT effective_revision FROM "{database.schema}".authorization_grant
                        WHERE grant_id = CAST(:grant_id AS uuid)'''
                    ),
                    {"grant_id": grant.grant_id},
                )
                denial_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".audit_entry
                        WHERE event_id = CAST(:request_id AS uuid)
                          AND entry_body->>'outcome' = 'DENIED'
                          AND entry_body->>'reason_code' = 'AUTHORIZATION_EXPIRED' '''
                    ),
                    {"request_id": command.request_id},
                )
            assert effective_revision == 1
            assert denial_count == 1
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_expire_rejects_an_orphan_grant_without_authorization_history(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    dataset, grant, evidence = _grant_scenario(expired=True)
    await _insert_dataset(database, dataset)
    await _insert_grant(database, grant, record_grant_audit=False)
    service = _service(database, _ApprovalSource(evidence))
    command = ExpireAuthorizationCommand(
        request_id=str(uuid4()), grant_id=grant.grant_id, expected_effective_revision=1
    )
    try:
        with pytest.raises(ProtectedSecurityError, match="^AUDIT_TRANSITION_INVALID$"):
            await service.expire(command)
        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                revision = await connection.scalar(
                    text(
                        f'''SELECT effective_revision FROM "{database.schema}".authorization_grant
                        WHERE grant_id = CAST(:grant_id AS uuid)'''
                    ),
                    {"grant_id": grant.grant_id},
                )
                command_audits = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".audit_entry
                        WHERE event_id = CAST(:request_id AS uuid)'''
                    ),
                    {"request_id": command.request_id},
                )
            assert revision == 1
            assert command_audits == 0
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_concurrent_identical_grant_converges_to_one_effect(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    dataset, grant, evidence = _grant_scenario()
    await _insert_dataset(database, dataset)
    source = _ApprovalSource(evidence)
    service = _service(database, source)
    try:
        await service.ingest_approval(
            IngestApprovalCommand(
                request_id=str(uuid4()),
                source_event_id=evidence.source_event_id,
                expected_raw_sha256=evidence.canonical_raw_sha256,
            )
        )
        command = GrantAuthorizationCommand(
            request_id=str(uuid4()),
            grant=grant,
            expected_dataset_state_revision=dataset.state_revision,
        )

        first, second = await asyncio.gather(service.grant(command), service.grant(command))
        assert first == second

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                grant_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".authorization_grant
                        WHERE grant_id = CAST(:grant_id AS uuid)'''
                    ),
                    {"grant_id": grant.grant_id},
                )
                control_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".audit_entry
                        WHERE event_id = CAST(:request_id AS uuid)'''
                    ),
                    {"request_id": command.request_id},
                )
            assert grant_count == 1
            assert control_count == 1
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_grant_refreshes_database_time_after_waiting_for_dataset_lock(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    dataset, original_grant, _ = _grant_scenario()
    grant = original_grant.model_copy(update={"expires_at": datetime.now(UTC) + timedelta(seconds=1)})
    evidence = _evidence(
        grant.approval_source_event_id,
        grant.approval_source_raw_sha256,
        grant=grant,
    )
    await _insert_dataset(database, dataset)
    source = _ApprovalSource(evidence)
    service = _service(database, source)
    lock_engine = create_async_engine(database.url)
    try:
        await service.ingest_approval(
            IngestApprovalCommand(
                request_id=str(uuid4()),
                source_event_id=evidence.source_event_id,
                expected_raw_sha256=evidence.canonical_raw_sha256,
            )
        )
        async with lock_engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(
                text(
                    f'''SELECT dataset_id FROM "{database.schema}".protected_dataset
                    WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version FOR UPDATE'''
                ),
                {"dataset_id": dataset.dataset_id, "dataset_version": dataset.dataset_version},
            )
            pending = asyncio.create_task(
                service.grant(
                    GrantAuthorizationCommand(
                        request_id=str(uuid4()),
                        grant=grant,
                        expected_dataset_state_revision=dataset.state_revision,
                    )
                )
            )
            await asyncio.sleep(1.2)
            await transaction.commit()
            with pytest.raises(ProtectedSecurityError, match="^AUTHORIZATION_EXPIRED$"):
                await pending
    finally:
        await lock_engine.dispose()
        await service.close()


@pytest.mark.asyncio
async def test_grant_policy_denial_commits_once_without_grant(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    dataset, grant, evidence = _grant_scenario()
    await _insert_dataset(database, dataset)
    source = _ApprovalSource(evidence)
    service = _service(database, source)
    try:
        await service.ingest_approval(
            IngestApprovalCommand(
                request_id=str(uuid4()),
                source_event_id=evidence.source_event_id,
                expected_raw_sha256=evidence.canonical_raw_sha256,
            )
        )
        command = GrantAuthorizationCommand(
            request_id=str(uuid4()),
            grant=grant,
            expected_dataset_state_revision=dataset.state_revision + 1,
        )
        for _ in range(2):
            with pytest.raises(ProtectedSecurityError, match="DATASET_BINDING_MISMATCH"):
                await service.grant(command)

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                grant_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".authorization_grant
                        WHERE grant_id = CAST(:grant_id AS uuid)'''
                    ),
                    {"grant_id": grant.grant_id},
                )
                denial_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".audit_entry
                        WHERE event_id = CAST(:request_id AS uuid)
                          AND entry_body->>'outcome' = 'DENIED'
                          AND entry_body->>'reason_code' = 'DATASET_BINDING_MISMATCH' '''
                    ),
                    {"request_id": command.request_id},
                )
            assert grant_count == 0
            assert denial_count == 1
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_grant_internal_failure_rolls_back_without_policy_denial_audit(
    protected_database: _ProtectedDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = protected_database
    dataset, grant, evidence = _grant_scenario()
    await _insert_dataset(database, dataset)
    service = _service(database, _ApprovalSource(evidence))
    try:
        await service.ingest_approval(
            IngestApprovalCommand(
                request_id=str(uuid4()),
                source_event_id=evidence.source_event_id,
                expected_raw_sha256=evidence.canonical_raw_sha256,
            )
        )

        async def fail_dataset_validation(
            control: control_adapter._ControlSession,
            candidate: ProtectedAuthorizationGrant,
            expected_state_revision: int,
        ) -> None:
            del control, candidate, expected_state_revision
            raise ProtectedSecurityError("INTERNAL_ERROR")

        monkeypatch.setattr(
            control_adapter._ControlSession,
            "require_grant_dataset",
            fail_dataset_validation,
        )
        command = GrantAuthorizationCommand(
            request_id=str(uuid4()),
            grant=grant,
            expected_dataset_state_revision=dataset.state_revision,
        )

        with pytest.raises(ProtectedSecurityError, match="^INTERNAL_ERROR$"):
            await service.grant(command)

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                grant_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".authorization_grant
                        WHERE grant_id = CAST(:grant_id AS uuid)'''
                    ),
                    {"grant_id": grant.grant_id},
                )
                request_audit_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".audit_entry
                        WHERE event_id = CAST(:request_id AS uuid)'''
                    ),
                    {"request_id": command.request_id},
                )
            assert grant_count == 0
            assert request_audit_count == 0
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_revoke_revision_denial_commits_once_without_mutation(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    dataset, grant, evidence = _grant_scenario()
    await _insert_dataset(database, dataset)
    await _insert_grant(database, grant)
    service = _service(database, _ApprovalSource(evidence))
    command = RevokeAuthorizationCommand(
        request_id=str(uuid4()),
        grant_id=grant.grant_id,
        approval_source_event_id=f"missing-{uuid4()}",
        expected_raw_sha256="7" * 64,
        expected_effective_revision=2,
    )
    try:
        for _ in range(2):
            with pytest.raises(ProtectedSecurityError, match="AUTHORIZATION_REVISION_MISMATCH"):
                await service.revoke(command)

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            f'''SELECT effective_revision, revoked_at FROM "{database.schema}".authorization_grant
                            WHERE grant_id = CAST(:grant_id AS uuid)'''
                        ),
                        {"grant_id": grant.grant_id},
                    )
                ).one()
                denial_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{database.schema}".audit_entry
                        WHERE event_id = CAST(:request_id AS uuid)
                          AND entry_body->>'outcome' = 'DENIED'
                          AND entry_body->>'reason_code' = 'AUTHORIZATION_REVISION_MISMATCH' '''
                    ),
                    {"request_id": command.request_id},
                )
            assert row == (1, None)
            assert denial_count == 1
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_grant_audit_failure_leaves_no_mutation(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    dataset, grant, evidence = _grant_scenario()
    await _insert_dataset(database, dataset)
    source = _ApprovalSource(evidence)
    service = _service(database, source)
    admin_engine = create_async_engine(database.url)
    original_head: tuple[int, str | None] | None = None
    try:
        await service.ingest_approval(
            IngestApprovalCommand(
                request_id=str(uuid4()),
                source_event_id=evidence.source_event_id,
                expected_raw_sha256=evidence.canonical_raw_sha256,
            )
        )
        async with admin_engine.begin() as connection:
            original_head = tuple(
                (
                    await connection.execute(
                        text(f'''SELECT sequence, entry_sha256 FROM "{database.schema}".audit_head WHERE singleton''')
                    )
                ).one()
            )
            await connection.execute(
                text(f'''UPDATE "{database.schema}".audit_head SET entry_sha256 = :digest WHERE singleton'''),
                {"digest": "f" * 64},
            )

        with pytest.raises(ProtectedSecurityError, match="AUDIT_HASH_MISMATCH"):
            await service.grant(
                GrantAuthorizationCommand(
                    request_id=str(uuid4()),
                    grant=grant,
                    expected_dataset_state_revision=dataset.state_revision,
                )
            )

        async with admin_engine.connect() as connection:
            grant_count = await connection.scalar(
                text(
                    f'''SELECT count(*) FROM "{database.schema}".authorization_grant
                    WHERE grant_id = CAST(:grant_id AS uuid)'''
                ),
                {"grant_id": grant.grant_id},
            )
        assert grant_count == 0
    finally:
        if original_head is not None:
            async with admin_engine.begin() as connection:
                await connection.execute(
                    text(
                        f'''UPDATE "{database.schema}".audit_head
                        SET sequence = :sequence, entry_sha256 = :entry_sha256 WHERE singleton'''
                    ),
                    {"sequence": original_head[0], "entry_sha256": original_head[1]},
                )
        await admin_engine.dispose()
        await service.close()


@pytest.mark.asyncio
async def test_register_identity_requires_product_safety_reviewer(
    protected_database: _ProtectedDatabase,
) -> None:
    evidence = _evidence(f"approval-{uuid4()}", "a" * 64)
    source = _ApprovalSource(evidence)
    # database.control_login has approval_role=DATASET_CUSTODIAN
    service = _service(protected_database, source)
    request_id = str(uuid4())
    command = RegisterIdentityCommand(
        request_id=request_id,
        database_login="test_sub_login",
        actor_id="test-sub-actor",
        actor_namespace="SERVICE_IDENTITY",
        identity_plane="DATA",
        principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
    )
    try:
        with pytest.raises(ProtectedSecurityError, match="ISSUER_ROLE_DENIED"):
            await service.register_identity(command)

        # Replay should reproduce the same denial
        with pytest.raises(ProtectedSecurityError, match="ISSUER_ROLE_DENIED"):
            await service.register_identity(command)

        # Verify denied audit entry committed
        admin_engine = create_async_engine(protected_database.url)
        try:
            async with admin_engine.connect() as connection:
                audit_row = (
                    await connection.execute(
                        text(
                            f'''SELECT event_kind, entry_body FROM "{protected_database.schema}".audit_entry
                            WHERE event_id = CAST(:request_id AS uuid)'''
                        ),
                        {"request_id": request_id},
                    )
                ).one()
                assert audit_row.event_kind == "CONTROL"
                body = (
                    json.loads(audit_row.entry_body) if isinstance(audit_row.entry_body, str) else audit_row.entry_body
                )
                assert body["outcome"] == "DENIED"
                assert body["reason_code"] == "ISSUER_ROLE_DENIED"
                assert body["target_kind"] == "PROTECTED_IDENTITY"
                assert body["target_id"] == "test_sub_login"
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_register_and_disable_identity_lifecycle(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    admin_engine = create_async_engine(database.url)
    reviewer_login = f"pr368_rev_{uuid4().hex[:8]}"
    try:
        async with admin_engine.begin() as connection:
            await connection.exec_driver_sql(
                f"CREATE ROLE \"{reviewer_login}\" LOGIN PASSWORD '{database.password}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            )
            await connection.exec_driver_sql(f'GRANT "{database.control}" TO "{reviewer_login}"')
            await connection.execute(
                text(
                    f'''INSERT INTO "{database.schema}".protected_identity (
                        database_login, actor_id, actor_namespace, approval_role, identity_plane
                    ) VALUES (
                        :login, 'synthetic-safety-reviewer', 'GITHUB_LOGIN',
                        'PRODUCT_SAFETY_REVIEWER', 'CONTROL'
                    )'''
                ),
                {"login": reviewer_login},
            )

        evidence = _evidence(f"approval-{uuid4()}", "a" * 64)
        source = _ApprovalSource(evidence)
        service = _service(database, source, login=reviewer_login)
        try:
            # Self-registration denied
            self_reg = RegisterIdentityCommand(
                request_id=str(uuid4()),
                database_login=reviewer_login,
                actor_id="someone-else",
                actor_namespace="GITHUB_LOGIN",
                identity_plane="DATA",
                principal_role=ProtectedPrincipalRole.HOLDOUT_AUTHOR,
            )
            with pytest.raises(ProtectedSecurityError, match="SELF_APPROVAL_DENIED"):
                await service.register_identity(self_reg)

            self_actor_reg = RegisterIdentityCommand(
                request_id=str(uuid4()),
                database_login="another_login",
                actor_id="synthetic-safety-reviewer",
                actor_namespace="GITHUB_LOGIN",
                identity_plane="DATA",
                principal_role=ProtectedPrincipalRole.HOLDOUT_AUTHOR,
            )
            with pytest.raises(ProtectedSecurityError, match="SELF_APPROVAL_DENIED"):
                await service.register_identity(self_actor_reg)

            # Happy path: register new data identity
            target_login = f"pr368_runner_{uuid4().hex[:8]}"
            reg_id = str(uuid4())
            reg_cmd = RegisterIdentityCommand(
                request_id=reg_id,
                database_login=target_login,
                actor_id="synthetic-runner-actor",
                actor_namespace="SERVICE_IDENTITY",
                identity_plane="DATA",
                principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
            )
            reg_result = await service.register_identity(reg_cmd)
            assert reg_result.command_kind == "REGISTER_IDENTITY"
            assert reg_result.target_id == target_login
            assert reg_result.reason_code == "IDENTITY_REGISTERED"

            # Replay returns exact same result
            replayed_result = await service.register_identity(reg_cmd)
            assert replayed_result == reg_result

            # Verify row in DB
            async with admin_engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            f'''SELECT actor_id, actor_namespace, principal_role, identity_plane, enabled
                            FROM "{database.schema}".protected_identity WHERE database_login = :login'''
                        ),
                        {"login": target_login},
                    )
                ).one()
                assert row == ("synthetic-runner-actor", "SERVICE_IDENTITY", "PROTECTED_RUNNER", "DATA", True)

            # Conflict: same request_id but different command payload
            diff_cmd = RegisterIdentityCommand(
                request_id=reg_id,
                database_login=target_login,
                actor_id="different-actor",
                actor_namespace="SERVICE_IDENTITY",
                identity_plane="DATA",
                principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
            )
            with pytest.raises(ProtectedSecurityError, match="CONTROL_COMMAND_CONFLICT"):
                await service.register_identity(diff_cmd)

            # Duplicate database_login registration with new request_id -> CONTROL_COMMAND_CONFLICT
            dup_cmd = RegisterIdentityCommand(
                request_id=str(uuid4()),
                database_login=target_login,
                actor_id="another-actor",
                actor_namespace="SERVICE_IDENTITY",
                identity_plane="DATA",
                principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
            )
            with pytest.raises(ProtectedSecurityError, match="CONTROL_COMMAND_CONFLICT"):
                await service.register_identity(dup_cmd)

            # Self-disable denied
            self_dis = DisableIdentityCommand(
                request_id=str(uuid4()),
                database_login=reviewer_login,
                expected_actor_id="synthetic-safety-reviewer",
                expected_actor_namespace="GITHUB_LOGIN",
            )
            with pytest.raises(ProtectedSecurityError, match="SELF_APPROVAL_DENIED"):
                await service.disable_identity(self_dis)

            # Disable non-existent identity -> AUTHORIZATION_NOT_FOUND
            missing_dis = DisableIdentityCommand(
                request_id=str(uuid4()),
                database_login="non_existent_login",
                expected_actor_id="non-existent",
                expected_actor_namespace="GITHUB_LOGIN",
            )
            with pytest.raises(ProtectedSecurityError, match="AUTHORIZATION_NOT_FOUND"):
                await service.disable_identity(missing_dis)

            # Disable with mismatched actor_id -> AUTHORIZATION_NOT_FOUND
            mismatch_dis = DisableIdentityCommand(
                request_id=str(uuid4()),
                database_login=target_login,
                expected_actor_id="wrong-actor",
                expected_actor_namespace="SERVICE_IDENTITY",
            )
            with pytest.raises(ProtectedSecurityError, match="AUTHORIZATION_NOT_FOUND"):
                await service.disable_identity(mismatch_dis)

            # Happy path: disable identity
            dis_id = str(uuid4())
            dis_cmd = DisableIdentityCommand(
                request_id=dis_id,
                database_login=target_login,
                expected_actor_id="synthetic-runner-actor",
                expected_actor_namespace="SERVICE_IDENTITY",
            )
            dis_result = await service.disable_identity(dis_cmd)
            assert dis_result.command_kind == "DISABLE_IDENTITY"
            assert dis_result.target_id == target_login
            assert dis_result.reason_code == "IDENTITY_DISABLED"

            # Replay returns exact same result
            replayed_dis = await service.disable_identity(dis_cmd)
            assert replayed_dis == dis_result

            # Verify enabled = false in DB
            async with admin_engine.connect() as connection:
                enabled_val = await connection.scalar(
                    text(
                        f'''SELECT enabled FROM "{database.schema}".protected_identity
                        WHERE database_login = :login'''
                    ),
                    {"login": target_login},
                )
                assert enabled_val is False

            # Subsequent attempt to disable already disabled identity -> AUTHORIZATION_NOT_FOUND
            subsequent_dis = DisableIdentityCommand(
                request_id=str(uuid4()),
                database_login=target_login,
                expected_actor_id="synthetic-runner-actor",
                expected_actor_namespace="SERVICE_IDENTITY",
            )
            with pytest.raises(ProtectedSecurityError, match="AUTHORIZATION_NOT_FOUND"):
                await service.disable_identity(subsequent_dis)
        finally:
            await service.close()
    finally:
        async with admin_engine.begin() as connection:
            await connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{reviewer_login}"')
        await admin_engine.dispose()


class _FailIfConnectedEngine:
    def __getattr__(self, name: str) -> object:
        pytest.fail(f"Database was accessed via {name} during preflight rejection")


def _dataset_binding(**updates: object) -> ProtectedDatasetBinding:
    base: dict[str, object] = {
        "dataset_id": str(uuid4()),
        "dataset_version": "1.0.0",
        "manifest_sha256": "a" * 64,
        "protected_artifact_sha256": "b" * 64,
        "hmac_key_version": "v1",
        "state": ProtectedDatasetState.ACCESS_AUTHORIZED,
        "state_revision": 1,
        "authored_count": 0,
        "review_complete": False,
        "leakage_axis_intersections": (0, 0, 0, 0),
        "freeze_receipt_ref": None,
        "execution_authorization_ref": None,
        "retriever_binding_ref": None,
    }
    base.update(updates)
    return ProtectedDatasetBinding.model_validate(base)


def _register_dataset_command(
    binding: ProtectedDatasetBinding | None = None,
    **updates: object,
) -> RegisterDatasetCommand:
    b = binding or _dataset_binding()
    base: dict[str, object] = {
        "request_id": str(uuid4()),
        "dataset_id": b.dataset_id,
        "dataset_version": b.dataset_version,
        "binding": b,
        "manifest_sha256": b.manifest_sha256,
        "protected_artifact_sha256": b.protected_artifact_sha256,
        "hmac_key_version": b.hmac_key_version,
    }
    base.update(updates)
    return RegisterDatasetCommand.model_validate(base)


def _transition_command(
    dataset: ProtectedDatasetBinding,
    *,
    from_state: ProtectedDatasetState,
    to_state: ProtectedDatasetState,
    revision: int,
    authored_count: int,
    review_complete: bool,
    **updates: object,
) -> TransitionDatasetCommand:
    base: dict[str, object] = {
        "request_id": str(uuid4()),
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.dataset_version,
        "from_state": from_state,
        "to_state": to_state,
        "expected_state_revision": revision,
        "authored_count": authored_count,
        "review_complete": review_complete,
    }
    base.update(updates)
    return TransitionDatasetCommand.model_validate(base)


def _transition_to_frozen_command() -> TransitionDatasetCommand:
    dataset = _dataset_binding()
    return TransitionDatasetCommand(
        request_id=str(uuid4()),
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        from_state=ProtectedDatasetState.REVIEW_READY,
        to_state=ProtectedDatasetState.FROZEN,
        expected_state_revision=2,
        authored_count=40,
        review_complete=True,
    )


async def _owner_read_dataset_row(
    database: _ProtectedDatabase,
    dataset_id: str,
    dataset_version: str,
) -> object:
    admin_engine = create_async_engine(database.url)
    try:
        async with admin_engine.begin() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT dataset_id, dataset_version, binding, manifest_sha256,
                           protected_artifact_sha256, hmac_key_version, state,
                           state_revision, authored_count, review_complete, lock_marker
                    FROM "{database.schema}".protected_dataset
                    WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version
                    """
                ),
                {"dataset_id": dataset_id, "dataset_version": dataset_version},
            )
            return result.one()
    finally:
        await admin_engine.dispose()


async def _registered_dataset_service(
    database: _ProtectedDatabase,
) -> tuple[PostgresqlProtectedAuthorizationControlService, ProtectedDatasetBinding]:
    service = _service(database, _DatasetApprovalSource())
    command = _register_dataset_command()
    await service.register_dataset(command)
    return service, command.binding


@pytest.mark.asyncio
async def test_custodian_registers_dataset_and_new_session_reads_initial_state(
    protected_database: _ProtectedDatabase,
) -> None:
    service = _service(protected_database, _DatasetApprovalSource())
    command = _register_dataset_command()
    try:
        result = await service.register_dataset(command)
        assert result.reason_code == "DATASET_REGISTERED"
        assert result.effective_revision == 1
        assert result.authorization_audit_event_id is None

        persisted = await _owner_read_dataset_row(protected_database, command.dataset_id, command.dataset_version)
        assert persisted.state == "ACCESS_AUTHORIZED"  # type: ignore[attr-defined]
        assert persisted.state_revision == 1  # type: ignore[attr-defined]
        assert persisted.binding == command.binding.model_dump(mode="json")  # type: ignore[attr-defined]
        assert persisted.authored_count == 0  # type: ignore[attr-defined]
        assert persisted.review_complete is False  # type: ignore[attr-defined]

        # Replay returns identical result
        replay = await service.register_dataset(command)
        assert replay == result
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_custodian_transitions_only_the_approved_dataset_dag(
    protected_database: _ProtectedDatabase,
) -> None:
    service, registered = await _registered_dataset_service(protected_database)
    try:
        authoring = await service.transition_dataset(
            _transition_command(
                registered,
                from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
                to_state=ProtectedDatasetState.AUTHORING,
                revision=1,
                authored_count=0,
                review_complete=False,
            )
        )
        assert authoring.effective_revision == 2
        assert authoring.reason_code == "DATASET_TRANSITIONED"

        review_ready = await service.transition_dataset(
            _transition_command(
                registered,
                from_state=ProtectedDatasetState.AUTHORING,
                to_state=ProtectedDatasetState.REVIEW_READY,
                revision=2,
                authored_count=40,
                review_complete=True,
            )
        )
        assert review_ready.effective_revision == 3
        assert review_ready.reason_code == "DATASET_TRANSITIONED"

        back_to_authoring = await service.transition_dataset(
            _transition_command(
                registered,
                from_state=ProtectedDatasetState.REVIEW_READY,
                to_state=ProtectedDatasetState.AUTHORING,
                revision=3,
                authored_count=35,
                review_complete=False,
            )
        )
        assert back_to_authoring.effective_revision == 4
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_transition_to_frozen_is_rejected_before_database_access() -> None:
    service = PostgresqlProtectedAuthorizationControlService(
        _FailIfConnectedEngine(),  # type: ignore[arg-type]
        schema="synthetic_schema",
        data_access_role="synthetic_data_role",
        control_role="synthetic_control_role",
        approval_source=_MissingApprovalSource(),
    )
    with pytest.raises(ProtectedSecurityError) as captured:
        await service.transition_dataset(_transition_to_frozen_command())
    assert captured.value.reason_code == "ROLE_ACTION_STATE_DENIED"


@pytest.mark.asyncio
async def test_dataset_registration_rejects_non_custodian_or_author_overlap(
    protected_database: _ProtectedDatabase,
) -> None:
    admin_engine = create_async_engine(protected_database.url)
    try:
        # Update existing DATA identity to have actor = synthetic-custodian
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    f"""
                    UPDATE "{protected_database.schema}".protected_identity
                    SET actor_id = 'synthetic-custodian', actor_namespace = 'GITHUB_LOGIN'
                    WHERE database_login = :login
                    """
                ),
                {"login": protected_database.actor_login},
            )

        service = _service(protected_database, _DatasetApprovalSource())
        try:
            with pytest.raises(ProtectedSecurityError, match="SELF_APPROVAL_DENIED"):
                await service.register_dataset(_register_dataset_command())
        finally:
            await service.close()
    finally:
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    f"""
                    UPDATE "{protected_database.schema}".protected_identity
                    SET actor_id = 'synthetic-author', actor_namespace = 'SERVICE_IDENTITY'
                    WHERE database_login = :login
                    """
                ),
                {"login": protected_database.actor_login},
            )
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_dataset_registration_rejects_conflicting_duplicate(
    protected_database: _ProtectedDatabase,
) -> None:
    service, registered = await _registered_dataset_service(protected_database)
    try:
        # Different request_id with already registered dataset_id:version -> CONTROL_COMMAND_CONFLICT
        conflicting_cmd = _register_dataset_command(
            binding=_dataset_binding(dataset_id=registered.dataset_id, dataset_version=registered.dataset_version)
        )
        with pytest.raises(ProtectedSecurityError, match="CONTROL_COMMAND_CONFLICT"):
            await service.register_dataset(conflicting_cmd)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_dataset_transition_rejects_invalid_state_transitions(
    protected_database: _ProtectedDatabase,
) -> None:
    service, registered = await _registered_dataset_service(protected_database)
    try:
        # 1. Wrong expected revision
        with pytest.raises(ProtectedSecurityError, match="DATASET_STATE_MISMATCH"):
            await service.transition_dataset(
                _transition_command(
                    registered,
                    from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
                    to_state=ProtectedDatasetState.AUTHORING,
                    revision=99,
                    authored_count=0,
                    review_complete=False,
                )
            )

        # 2. Unsupported DAG transition (ACCESS_AUTHORIZED -> REVIEW_READY)
        with pytest.raises(ProtectedSecurityError, match="DATASET_STATE_MISMATCH"):
            await service.transition_dataset(
                _transition_command(
                    registered,
                    from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
                    to_state=ProtectedDatasetState.REVIEW_READY,
                    revision=1,
                    authored_count=40,
                    review_complete=True,
                )
            )

        # 3. Transition to AUTHORING first
        await service.transition_dataset(
            _transition_command(
                registered,
                from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
                to_state=ProtectedDatasetState.AUTHORING,
                revision=1,
                authored_count=0,
                review_complete=False,
            )
        )

        # 4. REVIEW_READY entry with authored_count == 0 -> DATASET_STATE_MISMATCH
        with pytest.raises(ProtectedSecurityError, match="DATASET_STATE_MISMATCH"):
            await service.transition_dataset(
                _transition_command(
                    registered,
                    from_state=ProtectedDatasetState.AUTHORING,
                    to_state=ProtectedDatasetState.REVIEW_READY,
                    revision=2,
                    authored_count=0,
                    review_complete=True,
                )
            )
    finally:
        await service.close()


def _freeze_evidence(
    dataset: ProtectedDatasetBinding,
    *,
    source_event_id: str | None = None,
    canonical_raw_sha256: str | None = None,
    **updates: object,
) -> FreezeApprovalSourceEvidence:
    event_id = source_event_id or str(uuid4())
    payload: dict[str, object] = {
        "source_event_id": event_id,
        "action": ProtectedAction.FREEZE,
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.dataset_version,
        "manifest_sha256": dataset.manifest_sha256,
        "protected_artifact_sha256": dataset.protected_artifact_sha256,
        "authored_count": 40,
        "review_complete": True,
        "leakage_axis_intersections": (0, 0, 0, 0),
        "issuer": ProtectedApprovalPrincipal(
            actor=ActorIdentity(actor_id="synthetic-reviewer-actor", namespace="SERVICE_IDENTITY"),
            role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
        ),
        "state": "APPROVED",
        "recorded_at": datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC),
        "target_commit_oid": "a" * 40,
        "target_artifact_sha256": "b" * 64,
        "canonical_raw_sha256": canonical_raw_sha256 or ("c" * 64),
        "implementation_participants": (ActorIdentity(actor_id="synthetic-author", namespace="SERVICE_IDENTITY"),),
    }
    payload.update(updates)
    return FreezeApprovalSourceEvidence(**payload)  # type: ignore[arg-type]


def _freeze_command(
    dataset: ProtectedDatasetBinding,
    evidence: FreezeApprovalSourceEvidence | None = None,
    *,
    request_id: str | None = None,
    revision: int | None = None,
    **updates: object,
) -> FreezeDatasetCommand:
    source_id = evidence.source_event_id if evidence is not None else str(uuid4())
    raw_sha = evidence.canonical_raw_sha256 if evidence is not None else ("c" * 64)
    payload: dict[str, object] = {
        "request_id": request_id or str(uuid4()),
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.dataset_version,
        "expected_state_revision": revision if revision is not None else dataset.state_revision,
        "approval_source_event_id": source_id,
        "expected_raw_sha256": raw_sha,
    }
    payload.update(updates)
    return FreezeDatasetCommand(**payload)  # type: ignore[arg-type]


async def _review_ready_dataset_service(
    database: _ProtectedDatabase,
    dataset_updates: dict[str, object] | None = None,
) -> tuple[PostgresqlProtectedAuthorizationControlService, ProtectedDatasetBinding]:
    service, dataset = await _registered_dataset_service(database)
    await service.transition_dataset(
        _transition_command(
            dataset,
            from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
            to_state=ProtectedDatasetState.AUTHORING,
            revision=1,
            authored_count=0,
            review_complete=False,
        )
    )
    result = await service.transition_dataset(
        _transition_command(
            dataset,
            from_state=ProtectedDatasetState.AUTHORING,
            to_state=ProtectedDatasetState.REVIEW_READY,
            revision=2,
            authored_count=40,
            review_complete=True,
        )
    )
    dataset_binding = dataset.model_copy(
        update={
            "state": ProtectedDatasetState.REVIEW_READY,
            "state_revision": result.effective_revision,
            "authored_count": 40,
            "review_complete": True,
        }
    )
    if dataset_updates:
        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.begin() as connection:
                for k, v in dataset_updates.items():
                    if k == "state":
                        val = v.value if isinstance(v, ProtectedDatasetState) else v
                        await connection.execute(
                            text(f'UPDATE "{database.schema}".protected_dataset SET state = :v WHERE dataset_id = :id'),
                            {"v": val, "id": dataset.dataset_id},
                        )
                    elif k == "authored_count":
                        await connection.execute(
                            text(
                                f'UPDATE "{database.schema}".protected_dataset SET authored_count = :v WHERE dataset_id = :id'
                            ),
                            {"v": v, "id": dataset.dataset_id},
                        )
                    elif k == "review_complete":
                        await connection.execute(
                            text(
                                f'UPDATE "{database.schema}".protected_dataset SET review_complete = :v WHERE dataset_id = :id'
                            ),
                            {"v": v, "id": dataset.dataset_id},
                        )
                    elif k == "leakage_axis_intersections":
                        binding_dict = dataset_binding.model_dump(mode="json")
                        binding_dict["leakage_axis_intersections"] = list(v) if isinstance(v, (tuple, list)) else v
                        await connection.execute(
                            text(
                                f'UPDATE "{database.schema}".protected_dataset SET binding = CAST(:v AS jsonb) WHERE dataset_id = :id'
                            ),
                            {"v": json.dumps(binding_dict), "id": dataset.dataset_id},
                        )
        finally:
            await admin_engine.dispose()
    return service, dataset_binding


async def _freezable_dataset_service(
    database: _ProtectedDatabase,
) -> tuple[PostgresqlProtectedAuthorizationControlService, ProtectedDatasetBinding, _DatasetApprovalSource]:
    service, dataset = await _review_ready_dataset_service(database)
    evidence = _freeze_evidence(dataset)
    source = _DatasetApprovalSource(evidence)
    await service.close()
    service = _service(database, source)
    return service, dataset, source


async def _load_dataset_in_new_data_session(
    database: _ProtectedDatabase,
    dataset: ProtectedDatasetBinding,
) -> ProtectedDatasetBinding:
    actor_engine = create_async_engine(_login_url(database, database.actor_login))
    session_factory = async_sessionmaker(actor_engine)
    try:
        async with session_factory() as session:
            clock = await PostgresqlTrustedClock.from_session(session)
            entries = await PostgresqlProtectedAuditJournal(session, database.schema, clock)._verified_entries(
                lock_head=False
            )
            row = (
                await session.execute(
                    text(
                        f"""
                        SELECT dataset_id, dataset_version, binding, manifest_sha256,
                               protected_artifact_sha256, hmac_key_version, state,
                               state_revision, authored_count, review_complete
                        FROM "{database.schema}".protected_dataset
                        WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version
                        """
                    ),
                    {"dataset_id": dataset.dataset_id, "dataset_version": dataset.dataset_version},
                )
            ).one()
            return _assemble_dataset_binding(
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
    finally:
        await actor_engine.dispose()


@pytest.mark.parametrize(
    ("dataset_updates", "expected_reason"),
    [
        ({"authored_count": 39}, "FREEZE_EVIDENCE_INCOMPLETE"),
        ({"review_complete": False}, "FREEZE_EVIDENCE_INCOMPLETE"),
        ({"leakage_axis_intersections": (1, 0, 0, 0)}, "FREEZE_EVIDENCE_INCOMPLETE"),
        ({"leakage_axis_intersections": (0, 1, 0, 0)}, "FREEZE_EVIDENCE_INCOMPLETE"),
        ({"leakage_axis_intersections": (0, 0, 1, 0)}, "FREEZE_EVIDENCE_INCOMPLETE"),
        ({"leakage_axis_intersections": (0, 0, 0, 1)}, "FREEZE_EVIDENCE_INCOMPLETE"),
        ({"state": ProtectedDatasetState.AUTHORING}, "DATASET_STATE_MISMATCH"),
    ],
)
@pytest.mark.asyncio
async def test_freeze_rejects_incomplete_dataset_evidence(
    protected_database: _ProtectedDatabase,
    dataset_updates: dict[str, object],
    expected_reason: str,
) -> None:
    service, dataset = await _review_ready_dataset_service(protected_database, dataset_updates)
    try:
        with pytest.raises(ProtectedSecurityError) as captured:
            await service.freeze_dataset(_freeze_command(dataset))
        assert captured.value.reason_code == expected_reason
        persisted = await _owner_read_dataset_row(protected_database, dataset.dataset_id, dataset.dataset_version)
        assert persisted.state != "FROZEN"  # type: ignore[attr-defined]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_freeze_commits_lifecycle_and_audit_receipt_atomically(
    protected_database: _ProtectedDatabase,
) -> None:
    service, dataset, source = await _freezable_dataset_service(protected_database)
    try:
        command = _freeze_command(dataset, source.evidence)
        result = await service.freeze_dataset(command)
        assert result.reason_code == "DATASET_FROZEN"
        assert result.effective_revision == dataset.state_revision + 1
        assert result.authorization_audit_event_id is None

        replay = await service.freeze_dataset(command)
        assert replay == result
        loaded = await _load_dataset_in_new_data_session(protected_database, dataset)
        assert loaded.state is ProtectedDatasetState.FROZEN
        assert loaded.freeze_receipt_ref == OpaqueLogicalRef(
            namespace=OpaqueRefNamespace.AUDIT_EVENT,
            value=command.request_id,
        )
        assert source.freeze_calls == [command.approval_source_event_id]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_freeze_audit_failure_rolls_back_without_mutation(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    service, dataset, source = await _freezable_dataset_service(database)
    command = _freeze_command(dataset, source.evidence)
    admin_engine = create_async_engine(database.url)
    original_head: tuple[int, str | None] | None = None
    try:
        async with admin_engine.begin() as connection:
            original_head = tuple(
                (
                    await connection.execute(
                        text(f'''SELECT sequence, entry_sha256 FROM "{database.schema}".audit_head WHERE singleton''')
                    )
                ).one()
            )
            await connection.execute(
                text(f'''UPDATE "{database.schema}".audit_head SET entry_sha256 = :digest WHERE singleton'''),
                {"digest": "f" * 64},
            )

        with pytest.raises(ProtectedSecurityError, match="AUDIT_HASH_MISMATCH"):
            await service.freeze_dataset(command)

        persisted = await _owner_read_dataset_row(database, dataset.dataset_id, dataset.dataset_version)
        assert persisted.state == "REVIEW_READY"  # type: ignore[attr-defined]
        assert persisted.state_revision == dataset.state_revision  # type: ignore[attr-defined]
    finally:
        if original_head is not None:
            async with admin_engine.begin() as connection:
                await connection.execute(
                    text(
                        f'''UPDATE "{database.schema}".audit_head
                        SET sequence = :sequence, entry_sha256 = :entry_sha256 WHERE singleton'''
                    ),
                    {"sequence": original_head[0], "entry_sha256": original_head[1]},
                )
        await admin_engine.dispose()
        await service.close()


@pytest.mark.asyncio
async def test_freeze_rejects_evidence_mismatch(
    protected_database: _ProtectedDatabase,
) -> None:
    service, dataset, source = await _freezable_dataset_service(protected_database)
    original_evidence = source.evidence
    try:
        # 1. Missing evidence -> APPROVAL_NOT_VERIFIED
        source.freeze_evidence = None
        cmd_missing = _freeze_command(dataset, evidence=None, approval_source_event_id=str(uuid4()))
        with pytest.raises(ProtectedSecurityError, match="APPROVAL_NOT_VERIFIED"):
            await service.freeze_dataset(cmd_missing)
        source.freeze_evidence = original_evidence

        # 2. Mismatched source ID in command -> APPROVAL_EVIDENCE_MISMATCH
        cmd_mismatch_source = _freeze_command(dataset, original_evidence, approval_source_event_id=str(uuid4()))
        with pytest.raises(ProtectedSecurityError, match="APPROVAL_EVIDENCE_MISMATCH"):
            await service.freeze_dataset(cmd_mismatch_source)

        # 3. Mismatched expected raw sha in command -> APPROVAL_EVIDENCE_MISMATCH
        cmd_mismatch_hash = _freeze_command(dataset, original_evidence, expected_raw_sha256="d" * 64)
        with pytest.raises(ProtectedSecurityError, match="APPROVAL_EVIDENCE_MISMATCH"):
            await service.freeze_dataset(cmd_mismatch_hash)

        # 4. Evidence with mismatched dataset_id
        bad_evidence = _freeze_evidence(dataset, dataset_id=str(uuid4()))
        source.freeze_evidence = bad_evidence
        with pytest.raises(ProtectedSecurityError, match="APPROVAL_EVIDENCE_MISMATCH"):
            await service.freeze_dataset(_freeze_command(dataset, bad_evidence))

        # 5. Evidence with issuer actor == executor actor
        bad_issuer = _freeze_evidence(
            dataset,
            issuer=ProtectedApprovalPrincipal(
                actor=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
                role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
            ),
        )
        source.freeze_evidence = bad_issuer
        with pytest.raises(ProtectedSecurityError, match="SELF_APPROVAL_DENIED"):
            await service.freeze_dataset(_freeze_command(dataset, bad_issuer))

        # Ensure dataset was not modified
        persisted = await _owner_read_dataset_row(protected_database, dataset.dataset_id, dataset.dataset_version)
        assert persisted.state == "REVIEW_READY"  # type: ignore[attr-defined]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_freeze_rejects_non_custodian_or_author_overlap(
    protected_database: _ProtectedDatabase,
) -> None:
    admin_engine = create_async_engine(protected_database.url)
    try:
        # Update existing DATA identity to have actor = synthetic-custodian (creating author overlap)
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    f"""
                    UPDATE "{protected_database.schema}".protected_identity
                    SET actor_id = 'synthetic-custodian', actor_namespace = 'GITHUB_LOGIN'
                    WHERE database_login = :login
                    """
                ),
                {"login": protected_database.actor_login},
            )

        dataset = _dataset_binding()
        evidence = _freeze_evidence(dataset)
        source = _DatasetApprovalSource(evidence)
        service = _service(protected_database, source)
        try:
            with pytest.raises(ProtectedSecurityError, match="SELF_APPROVAL_DENIED"):
                await service.freeze_dataset(_freeze_command(dataset, evidence))
            assert source.freeze_calls == []
        finally:
            await service.close()
    finally:
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    f"""
                    UPDATE "{protected_database.schema}".protected_identity
                    SET actor_id = 'synthetic-author', actor_namespace = 'SERVICE_IDENTITY'
                    WHERE database_login = :login
                    """
                ),
                {"login": protected_database.actor_login},
            )
        await admin_engine.dispose()


async def _dataset_row_count(
    database: _ProtectedDatabase,
    command: RegisterDatasetCommand,
) -> int:
    admin_engine = create_async_engine(database.url)
    try:
        async with admin_engine.connect() as connection:
            return await connection.scalar(
                text(
                    f'''SELECT count(*) FROM "{database.schema}".protected_dataset
                    WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version'''
                ),
                {"dataset_id": command.dataset_id, "dataset_version": command.dataset_version},
            )
    finally:
        await admin_engine.dispose()


async def _request_control_audit_count(
    database: _ProtectedDatabase,
    request_id: str,
) -> int:
    admin_engine = create_async_engine(database.url)
    try:
        async with admin_engine.connect() as connection:
            return await connection.scalar(
                text(
                    f'''SELECT count(*) FROM "{database.schema}".audit_entry
                    WHERE event_id = CAST(:request_id AS uuid)'''
                ),
                {"request_id": request_id},
            )
    finally:
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_identical_dataset_register_converges_to_one_effect(
    protected_database: _ProtectedDatabase,
) -> None:
    first = _service(protected_database, _DatasetApprovalSource())
    second = _service(protected_database, _DatasetApprovalSource())
    command = _register_dataset_command()
    try:
        first_result, second_result = await asyncio.gather(
            first.register_dataset(command),
            second.register_dataset(command),
        )
        assert first_result == second_result
        assert await _dataset_row_count(protected_database, command) == 1
        assert await _request_control_audit_count(protected_database, command.request_id) == 1
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_competing_dataset_transitions_commit_one_winner(
    protected_database: _ProtectedDatabase,
) -> None:
    service, dataset = await _registered_dataset_service(protected_database)
    second_service = _service(protected_database, _DatasetApprovalSource())
    try:
        cmd1 = _transition_command(
            dataset,
            from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
            to_state=ProtectedDatasetState.AUTHORING,
            revision=1,
            authored_count=0,
            review_complete=False,
        )
        cmd2 = _transition_command(
            dataset,
            from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
            to_state=ProtectedDatasetState.AUTHORING,
            revision=1,
            authored_count=0,
            review_complete=False,
        )
        results = await asyncio.gather(
            service.transition_dataset(cmd1),
            second_service.transition_dataset(cmd2),
            return_exceptions=True,
        )
        successes = [r for r in results if isinstance(r, ControlCommandResult)]
        failures = [r for r in results if isinstance(r, ProtectedSecurityError)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert successes[0].reason_code == "DATASET_TRANSITIONED"
        assert successes[0].effective_revision == 2
        assert failures[0].reason_code == "DATASET_STATE_MISMATCH"

        persisted = await _owner_read_dataset_row(protected_database, dataset.dataset_id, dataset.dataset_version)
        assert persisted.state == "AUTHORING"  # type: ignore[attr-defined]
        assert persisted.state_revision == 2  # type: ignore[attr-defined]

        winner_cmd = cmd1 if results[0] == successes[0] else cmd2
        loser_cmd = cmd2 if winner_cmd == cmd1 else cmd1
        assert await _request_control_audit_count(protected_database, winner_cmd.request_id) == 1
        assert await _request_control_audit_count(protected_database, loser_cmd.request_id) == 1
    finally:
        await service.close()
        await second_service.close()


@pytest.mark.asyncio
async def test_competing_freeze_commits_one_winner_and_replays(
    protected_database: _ProtectedDatabase,
) -> None:
    service, dataset, source = await _freezable_dataset_service(protected_database)
    second_service = _service(protected_database, source)
    try:
        cmd1 = _freeze_command(dataset, source.evidence)
        cmd2 = _freeze_command(dataset, source.evidence)
        results = await asyncio.gather(
            service.freeze_dataset(cmd1),
            second_service.freeze_dataset(cmd2),
            return_exceptions=True,
        )
        successes = [r for r in results if isinstance(r, ControlCommandResult)]
        failures = [r for r in results if isinstance(r, ProtectedSecurityError)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert successes[0].reason_code == "DATASET_FROZEN"
        assert successes[0].effective_revision == dataset.state_revision + 1
        assert failures[0].reason_code == "DATASET_STATE_MISMATCH"

        persisted = await _owner_read_dataset_row(protected_database, dataset.dataset_id, dataset.dataset_version)
        assert persisted.state == "FROZEN"  # type: ignore[attr-defined]
        assert persisted.state_revision == dataset.state_revision + 1  # type: ignore[attr-defined]

        winner_cmd = cmd1 if results[0] == successes[0] else cmd2
        replay = await service.freeze_dataset(winner_cmd)
        assert replay == successes[0]
    finally:
        await service.close()
        await second_service.close()


@pytest.mark.asyncio
async def test_dataset_transition_refreshes_database_time_after_waiting_for_lock(
    protected_database: _ProtectedDatabase,
) -> None:
    service, dataset = await _registered_dataset_service(protected_database)
    lock_engine = create_async_engine(protected_database.url)
    try:
        command = _transition_command(
            dataset,
            from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
            to_state=ProtectedDatasetState.AUTHORING,
            revision=1,
            authored_count=0,
            review_complete=False,
        )
        async with lock_engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(
                text(
                    f'''SELECT dataset_id FROM "{protected_database.schema}".protected_dataset
                    WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version FOR UPDATE'''
                ),
                {"dataset_id": dataset.dataset_id, "dataset_version": dataset.dataset_version},
            )
            pending = asyncio.create_task(service.transition_dataset(command))
            await asyncio.sleep(0.5)
            lock_release_time = await connection.scalar(text("SELECT clock_timestamp()"))
            await transaction.commit()
            result = await pending
            assert result.reason_code == "DATASET_TRANSITIONED"

            admin_engine = create_async_engine(protected_database.url)
            try:
                async with admin_engine.connect() as admin_conn:
                    recorded_at = await admin_conn.scalar(
                        text(
                            f'''SELECT recorded_at FROM "{protected_database.schema}".audit_entry
                            WHERE event_id = CAST(:request_id AS uuid)'''
                        ),
                        {"request_id": command.request_id},
                    )
                assert recorded_at is not None
                assert recorded_at >= lock_release_time
            finally:
                await admin_engine.dispose()
    finally:
        await lock_engine.dispose()
        await service.close()


@pytest.mark.asyncio
async def test_dataset_freeze_policy_denial_commits_once_without_mutation(
    protected_database: _ProtectedDatabase,
) -> None:
    service, dataset, source = await _freezable_dataset_service(protected_database)
    try:
        command = _freeze_command(dataset, source.evidence, expected_raw_sha256="0" * 64)
        for _ in range(2):
            with pytest.raises(ProtectedSecurityError, match="APPROVAL_EVIDENCE_MISMATCH"):
                await service.freeze_dataset(command)

        persisted = await _owner_read_dataset_row(protected_database, dataset.dataset_id, dataset.dataset_version)
        assert persisted.state == "REVIEW_READY"  # type: ignore[attr-defined]
        assert persisted.state_revision == dataset.state_revision  # type: ignore[attr-defined]

        admin_engine = create_async_engine(protected_database.url)
        try:
            async with admin_engine.connect() as connection:
                denial_count = await connection.scalar(
                    text(
                        f'''SELECT count(*) FROM "{protected_database.schema}".audit_entry
                        WHERE event_id = CAST(:request_id AS uuid)
                          AND entry_body->>'outcome' = 'DENIED'
                          AND entry_body->>'reason_code' = 'APPROVAL_EVIDENCE_MISMATCH' '''
                    ),
                    {"request_id": command.request_id},
                )
                assert denial_count == 1
        finally:
            await admin_engine.dispose()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_dataset_transition_internal_failure_rolls_back_without_policy_denial_audit(
    protected_database: _ProtectedDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, dataset = await _registered_dataset_service(protected_database)
    try:

        async def fail_check_dataset_transition(
            self_session: control_adapter._ControlSession,
            candidate: TransitionDatasetCommand,
        ) -> ProtectedAuditReason | None:
            del self_session, candidate
            raise ProtectedSecurityError("INTERNAL_ERROR")

        monkeypatch.setattr(
            control_adapter._ControlSession,
            "check_dataset_transition",
            fail_check_dataset_transition,
        )
        command = _transition_command(
            dataset,
            from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
            to_state=ProtectedDatasetState.AUTHORING,
            revision=1,
            authored_count=0,
            review_complete=False,
        )
        with pytest.raises(ProtectedSecurityError, match="^INTERNAL_ERROR$"):
            await service.transition_dataset(command)

        persisted = await _owner_read_dataset_row(protected_database, dataset.dataset_id, dataset.dataset_version)
        assert persisted.state == "ACCESS_AUTHORIZED"  # type: ignore[attr-defined]
        assert persisted.state_revision == 1  # type: ignore[attr-defined]

        assert await _request_control_audit_count(protected_database, command.request_id) == 0
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_dataset_transition_denied_replay_returns_fixed_error_without_duplicate_audit(
    protected_database: _ProtectedDatabase,
) -> None:
    service, dataset = await _registered_dataset_service(protected_database)
    try:
        command = _transition_command(
            dataset,
            from_state=ProtectedDatasetState.REVIEW_READY,
            to_state=ProtectedDatasetState.AUTHORING,
            revision=99,
            authored_count=0,
            review_complete=False,
        )
        with pytest.raises(ProtectedSecurityError) as exc_info1:
            await service.transition_dataset(command)
        assert exc_info1.value.reason_code == "DATASET_STATE_MISMATCH"

        with pytest.raises(ProtectedSecurityError) as exc_info2:
            await service.transition_dataset(command)
        assert exc_info2.value.reason_code == "DATASET_STATE_MISMATCH"

        assert await _request_control_audit_count(protected_database, command.request_id) == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_dataset_command_conflict_rejects_payload_mutation_on_same_request_id(
    protected_database: _ProtectedDatabase,
) -> None:
    service, dataset = await _registered_dataset_service(protected_database)
    try:
        shared_request_id = str(uuid4())
        cmd1 = _transition_command(
            dataset,
            request_id=shared_request_id,
            from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
            to_state=ProtectedDatasetState.AUTHORING,
            revision=1,
            authored_count=0,
            review_complete=False,
        )
        result1 = await service.transition_dataset(cmd1)
        assert result1.reason_code == "DATASET_TRANSITIONED"

        cmd2 = _transition_command(
            dataset,
            request_id=shared_request_id,
            from_state=ProtectedDatasetState.AUTHORING,
            to_state=ProtectedDatasetState.REVIEW_READY,
            revision=2,
            authored_count=40,
            review_complete=True,
        )
        with pytest.raises(ProtectedSecurityError, match="CONTROL_COMMAND_CONFLICT"):
            await service.transition_dataset(cmd2)

        persisted = await _owner_read_dataset_row(protected_database, dataset.dataset_id, dataset.dataset_version)
        assert persisted.state == "AUTHORING"  # type: ignore[attr-defined]
        assert persisted.state_revision == 2  # type: ignore[attr-defined]

        assert await _request_control_audit_count(protected_database, shared_request_id) == 1
    finally:
        await service.close()


@asynccontextmanager
async def _reviewer(
    database: _ProtectedDatabase,
    admin_engine: AsyncEngine,
    actor_id: str | None = None,
):
    reviewer_login = f"pr512_rev_{uuid4().hex[:8]}"
    actor = actor_id or f"safety-reviewer-{uuid4().hex[:6]}"
    async with admin_engine.begin() as connection:
        await connection.exec_driver_sql(
            f"CREATE ROLE \"{reviewer_login}\" LOGIN PASSWORD '{database.password}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
        await connection.exec_driver_sql(f'GRANT "{database.control}" TO "{reviewer_login}"')
        await connection.execute(
            text(
                f'''INSERT INTO "{database.schema}".protected_identity (
                    database_login, actor_id, actor_namespace, approval_role, identity_plane, enabled
                ) VALUES (
                    :login, :actor_id, 'GITHUB_LOGIN',
                    'PRODUCT_SAFETY_REVIEWER', 'CONTROL', true
                )'''
            ),
            {"login": reviewer_login, "actor_id": actor},
        )
    evidence = _evidence(f"approval-{uuid4()}", "a" * 64)
    source = _ApprovalSource(evidence)
    service = _service(database, source, login=reviewer_login)
    try:
        yield reviewer_login, actor, service
    finally:
        await service.close()
        async with admin_engine.begin() as connection:
            await connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{reviewer_login}"')


async def _wait_for_blocked_by(
    admin_engine: AsyncEngine,
    blocking_pids: int | tuple[int, ...] | None = None,
    *,
    blocking_pid: int | None = None,
    exclude_pids: tuple[int, ...] = (),
    timeout: float = 10.0,
) -> int:
    target = blocking_pids if blocking_pids is not None else blocking_pid
    assert target is not None
    blockers = (target,) if isinstance(target, int) else target

    async def _probe() -> int:
        while True:
            async with admin_engine.connect() as conn:
                waiting_pids = await conn.scalars(
                    text(
                        "SELECT pid FROM pg_stat_activity "
                        "WHERE EXISTS ("
                        "   SELECT 1 FROM unnest(pg_blocking_pids(pg_stat_activity.pid)) AS b(pid) "
                        "   WHERE b.pid = ANY(:blockers)"
                        ") "
                        "AND wait_event_type = 'Lock' "
                        "ORDER BY pid"
                    ),
                    {"blockers": list(blockers)},
                )
                for pid in waiting_pids:
                    if pid not in exclude_pids:
                        is_waiting = await conn.scalar(
                            text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE NOT granted AND pid = :pid)"),
                            {"pid": pid},
                        )
                        if is_waiting:
                            return int(pid)
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_probe(), timeout=timeout)


async def _verify_audit_chain(admin_engine: AsyncEngine, schema: str) -> tuple[ProtectedAuditEntry, ...]:
    async with AsyncSession(admin_engine) as session:
        clock = await PostgresqlTrustedClock.from_session(session)
        journal = PostgresqlProtectedAuditJournal(session, schema, clock)
        return await journal._verified_entries(lock_head=False)


@pytest.mark.asyncio
async def test_concurrent_register_and_executor_disable_disable_first(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    admin_engine = create_async_engine(database.url)
    try:
        async with _reviewer(database, admin_engine) as (rev_a_login, rev_a_actor, service_a):
            async with _reviewer(database, admin_engine) as (rev_b_login, _, service_b):
                target_login = f"pr512_runner_{uuid4().hex[:8]}"
                reg_cmd = RegisterIdentityCommand(
                    request_id=str(uuid4()),
                    database_login=target_login,
                    actor_id="runner-actor-1",
                    actor_namespace="SERVICE_IDENTITY",
                    identity_plane="DATA",
                    principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
                )
                dis_cmd = DisableIdentityCommand(
                    request_id=str(uuid4()),
                    database_login=rev_a_login,
                    expected_actor_id=rev_a_actor,
                    expected_actor_namespace="GITHUB_LOGIN",
                )

                aux_engine = create_async_engine(database.url)
                aux_conn = await aux_engine.connect()
                aux_trans: AsyncTransaction | None = await aux_conn.begin()
                tasks: list[asyncio.Task[object]] = []
                try:
                    await aux_conn.execute(
                        text(f'SELECT sequence FROM "{database.schema}".audit_head WHERE singleton FOR UPDATE')
                    )
                    aux_pid = await aux_conn.scalar(text("SELECT pg_backend_pid()"))
                    assert aux_pid is not None

                    task_b: asyncio.Task[object] = asyncio.create_task(service_b.disable_identity(dis_cmd))
                    tasks.append(task_b)

                    dis_pid = await _wait_for_blocked_by(admin_engine, blocking_pid=int(aux_pid), timeout=10.0)
                    assert dis_pid is not None

                    task_a: asyncio.Task[object] = asyncio.create_task(service_a.register_identity(reg_cmd))
                    tasks.append(task_a)

                    reg_pid = await _wait_for_blocked_by(admin_engine, blocking_pid=dis_pid, timeout=10.0)
                    assert reg_pid is not None

                    assert aux_trans is not None
                    await aux_trans.rollback()
                    aux_trans = None

                    dis_result = await asyncio.wait_for(task_b, timeout=10.0)
                    assert isinstance(dis_result, ControlCommandResult)
                    assert dis_result.command_kind == "DISABLE_IDENTITY"
                    assert dis_result.reason_code == "IDENTITY_DISABLED"
                    assert dis_result.target_id == rev_a_login

                    with pytest.raises(ProtectedSecurityError) as exc_info:
                        await asyncio.wait_for(task_a, timeout=10.0)
                    assert exc_info.value.reason_code == "AUTHORIZATION_NOT_FOUND"

                    async with admin_engine.connect() as connection:
                        exists = await connection.scalar(
                            text(
                                f'SELECT EXISTS (SELECT 1 FROM "{database.schema}".protected_identity '
                                "WHERE database_login = :target)"
                            ),
                            {"target": target_login},
                        )
                        assert exists is False

                        rev_a_enabled = await connection.scalar(
                            text(
                                f'SELECT enabled FROM "{database.schema}".protected_identity '
                                "WHERE database_login = :login"
                            ),
                            {"login": rev_a_login},
                        )
                        assert rev_a_enabled is False

                        dis_audit = (
                            await connection.execute(
                                text(
                                    f'SELECT entry_body FROM "{database.schema}".audit_entry '
                                    "WHERE event_id = CAST(:req_id AS uuid)"
                                ),
                                {"req_id": dis_cmd.request_id},
                            )
                        ).one()
                        dis_body = (
                            json.loads(dis_audit.entry_body)
                            if isinstance(dis_audit.entry_body, str)
                            else dis_audit.entry_body
                        )
                        assert dis_body["outcome"] == "SUCCEEDED"
                        assert dis_body["reason_code"] == "IDENTITY_DISABLED"
                        assert dis_body["target_id"] == rev_a_login

                        reg_audit = (
                            await connection.execute(
                                text(
                                    f'SELECT entry_body FROM "{database.schema}".audit_entry '
                                    "WHERE event_id = CAST(:req_id AS uuid)"
                                ),
                                {"req_id": reg_cmd.request_id},
                            )
                        ).one()
                        reg_body = (
                            json.loads(reg_audit.entry_body)
                            if isinstance(reg_audit.entry_body, str)
                            else reg_audit.entry_body
                        )
                        assert reg_body["outcome"] == "DENIED"
                        assert reg_body["reason_code"] == "AUTHORIZATION_NOT_FOUND"
                        assert reg_body["target_id"] == target_login

                    entries = await _verify_audit_chain(admin_engine, database.schema)
                    entry_map = {e.event_id: e for e in entries}
                    assert dis_cmd.request_id in entry_map
                    assert reg_cmd.request_id in entry_map
                    assert entry_map[dis_cmd.request_id].sequence < entry_map[reg_cmd.request_id].sequence
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if aux_trans is not None:
                        await aux_trans.rollback()
                    await aux_conn.close()
                    await aux_engine.dispose()
    finally:
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_register_and_executor_disable_register_first(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    admin_engine = create_async_engine(database.url)
    try:
        async with _reviewer(database, admin_engine) as (rev_a_login, rev_a_actor, service_a):
            async with _reviewer(database, admin_engine) as (rev_b_login, _, service_b):
                target_login = f"pr512_runner_{uuid4().hex[:8]}"
                reg_cmd = RegisterIdentityCommand(
                    request_id=str(uuid4()),
                    database_login=target_login,
                    actor_id="runner-actor-2",
                    actor_namespace="SERVICE_IDENTITY",
                    identity_plane="DATA",
                    principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
                )
                dis_cmd = DisableIdentityCommand(
                    request_id=str(uuid4()),
                    database_login=rev_a_login,
                    expected_actor_id=rev_a_actor,
                    expected_actor_namespace="GITHUB_LOGIN",
                )

                aux_engine = create_async_engine(database.url)
                aux_conn = await aux_engine.connect()
                aux_trans: AsyncTransaction | None = await aux_conn.begin()
                tasks: list[asyncio.Task[object]] = []
                try:
                    await aux_conn.execute(
                        text(f'SELECT sequence FROM "{database.schema}".audit_head WHERE singleton FOR UPDATE')
                    )
                    aux_pid = await aux_conn.scalar(text("SELECT pg_backend_pid()"))
                    assert aux_pid is not None

                    task_a: asyncio.Task[object] = asyncio.create_task(service_a.register_identity(reg_cmd))
                    tasks.append(task_a)

                    reg_pid = await _wait_for_blocked_by(admin_engine, blocking_pid=int(aux_pid), timeout=10.0)
                    assert reg_pid is not None

                    task_b: asyncio.Task[object] = asyncio.create_task(service_b.disable_identity(dis_cmd))
                    tasks.append(task_b)

                    dis_pid = await _wait_for_blocked_by(admin_engine, blocking_pid=reg_pid, timeout=10.0)
                    assert dis_pid is not None

                    assert aux_trans is not None
                    await aux_trans.rollback()
                    aux_trans = None

                    reg_result = await asyncio.wait_for(task_a, timeout=10.0)
                    assert isinstance(reg_result, ControlCommandResult)
                    assert reg_result.command_kind == "REGISTER_IDENTITY"
                    assert reg_result.reason_code == "IDENTITY_REGISTERED"
                    assert reg_result.target_id == target_login

                    dis_result = await asyncio.wait_for(task_b, timeout=10.0)
                    assert isinstance(dis_result, ControlCommandResult)
                    assert dis_result.command_kind == "DISABLE_IDENTITY"
                    assert dis_result.reason_code == "IDENTITY_DISABLED"
                    assert dis_result.target_id == rev_a_login

                    async with admin_engine.connect() as connection:
                        t_enabled = await connection.scalar(
                            text(
                                f'SELECT enabled FROM "{database.schema}".protected_identity '
                                "WHERE database_login = :target"
                            ),
                            {"target": target_login},
                        )
                        assert t_enabled is True

                        rev_a_enabled = await connection.scalar(
                            text(
                                f'SELECT enabled FROM "{database.schema}".protected_identity '
                                "WHERE database_login = :login"
                            ),
                            {"login": rev_a_login},
                        )
                        assert rev_a_enabled is False

                        reg_audit = (
                            await connection.execute(
                                text(
                                    f'SELECT entry_body FROM "{database.schema}".audit_entry '
                                    "WHERE event_id = CAST(:req_id AS uuid)"
                                ),
                                {"req_id": reg_cmd.request_id},
                            )
                        ).one()
                        reg_body = (
                            json.loads(reg_audit.entry_body)
                            if isinstance(reg_audit.entry_body, str)
                            else reg_audit.entry_body
                        )
                        assert reg_body["outcome"] == "SUCCEEDED"
                        assert reg_body["reason_code"] == "IDENTITY_REGISTERED"
                        assert reg_body["target_id"] == target_login

                        dis_audit = (
                            await connection.execute(
                                text(
                                    f'SELECT entry_body FROM "{database.schema}".audit_entry '
                                    "WHERE event_id = CAST(:req_id AS uuid)"
                                ),
                                {"req_id": dis_cmd.request_id},
                            )
                        ).one()
                        dis_body = (
                            json.loads(dis_audit.entry_body)
                            if isinstance(dis_audit.entry_body, str)
                            else dis_audit.entry_body
                        )
                        assert dis_body["outcome"] == "SUCCEEDED"
                        assert dis_body["reason_code"] == "IDENTITY_DISABLED"
                        assert dis_body["target_id"] == rev_a_login

                    entries = await _verify_audit_chain(admin_engine, database.schema)
                    entry_map = {e.event_id: e for e in entries}
                    assert reg_cmd.request_id in entry_map
                    assert dis_cmd.request_id in entry_map
                    assert entry_map[reg_cmd.request_id].sequence < entry_map[dis_cmd.request_id].sequence
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if aux_trans is not None:
                        await aux_trans.rollback()
                    await aux_conn.close()
                    await aux_engine.dispose()
    finally:
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_register_identical_database_login(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    admin_engine = create_async_engine(database.url)
    try:
        async with _reviewer(database, admin_engine) as (rev_a, _, service_a):
            async with _reviewer(database, admin_engine) as (rev_b, _, service_b):
                shared_target = f"pr512_shared_{uuid4().hex[:8]}"
                cmd1 = RegisterIdentityCommand(
                    request_id=str(uuid4()),
                    database_login=shared_target,
                    actor_id="actor-one",
                    actor_namespace="SERVICE_IDENTITY",
                    identity_plane="DATA",
                    principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
                )
                cmd2 = RegisterIdentityCommand(
                    request_id=str(uuid4()),
                    database_login=shared_target,
                    actor_id="actor-two",
                    actor_namespace="SERVICE_IDENTITY",
                    identity_plane="DATA",
                    principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
                )

                t1: asyncio.Task[object] = asyncio.create_task(service_a.register_identity(cmd1))
                t2: asyncio.Task[object] = asyncio.create_task(service_b.register_identity(cmd2))
                tasks = [t1, t2]
                try:
                    results = await asyncio.wait_for(asyncio.gather(t1, t2, return_exceptions=True), timeout=10.0)

                    successes = [r for r in results if isinstance(r, ControlCommandResult)]
                    conflicts = [
                        r
                        for r in results
                        if isinstance(r, ProtectedSecurityError) and r.reason_code == "CONTROL_COMMAND_CONFLICT"
                    ]
                    assert len(successes) == 1, f"Expected 1 success, got {results}"
                    assert len(conflicts) == 1, f"Expected 1 conflict, got {results}"
                    assert successes[0].reason_code == "IDENTITY_REGISTERED"
                    assert successes[0].target_id == shared_target

                    async with admin_engine.connect() as connection:
                        rows = (
                            await connection.execute(
                                text(
                                    f'SELECT database_login FROM "{database.schema}".protected_identity '
                                    "WHERE database_login = :login"
                                ),
                                {"login": shared_target},
                            )
                        ).all()
                        assert len(rows) == 1

                        audit_rows = (
                            await connection.execute(
                                text(
                                    f'SELECT event_id, entry_body FROM "{database.schema}".audit_entry '
                                    "WHERE event_id IN (CAST(:req1 AS uuid), CAST(:req2 AS uuid))"
                                ),
                                {"req1": cmd1.request_id, "req2": cmd2.request_id},
                            )
                        ).all()
                        assert len(audit_rows) == 2
                        body_map = {
                            str(r.event_id): (
                                json.loads(r.entry_body) if isinstance(r.entry_body, str) else r.entry_body
                            )
                            for r in audit_rows
                        }
                        assert cmd1.request_id in body_map
                        assert cmd2.request_id in body_map
                        outcomes = {body_map[cmd1.request_id]["outcome"], body_map[cmd2.request_id]["outcome"]}
                        assert outcomes == {"SUCCEEDED", "DENIED"}

                        succeeded_body = next(b for b in body_map.values() if b["outcome"] == "SUCCEEDED")
                        denied_body = next(b for b in body_map.values() if b["outcome"] == "DENIED")
                        assert succeeded_body["reason_code"] == "IDENTITY_REGISTERED"
                        assert succeeded_body["target_id"] == shared_target
                        assert denied_body["reason_code"] == "CONTROL_COMMAND_CONFLICT"
                        assert denied_body["target_id"] == shared_target

                    entries = await _verify_audit_chain(admin_engine, database.schema)
                    assert len(entries) >= 2
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_register_identical_actor_namespace_plane(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    admin_engine = create_async_engine(database.url)
    try:
        async with _reviewer(database, admin_engine) as (rev_a, _, service_a):
            async with _reviewer(database, admin_engine) as (rev_b, _, service_b):
                shared_actor = f"runner-{uuid4().hex[:6]}"
                target_login_1 = f"pr512_t1_{uuid4().hex[:8]}"
                target_login_2 = f"pr512_t2_{uuid4().hex[:8]}"
                cmd1 = RegisterIdentityCommand(
                    request_id=str(uuid4()),
                    database_login=target_login_1,
                    actor_id=shared_actor,
                    actor_namespace="SERVICE_IDENTITY",
                    identity_plane="DATA",
                    principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
                )
                cmd2 = RegisterIdentityCommand(
                    request_id=str(uuid4()),
                    database_login=target_login_2,
                    actor_id=shared_actor,
                    actor_namespace="SERVICE_IDENTITY",
                    identity_plane="DATA",
                    principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
                )

                t1: asyncio.Task[object] = asyncio.create_task(service_a.register_identity(cmd1))
                t2: asyncio.Task[object] = asyncio.create_task(service_b.register_identity(cmd2))
                tasks = [t1, t2]
                try:
                    results = await asyncio.wait_for(asyncio.gather(t1, t2, return_exceptions=True), timeout=10.0)

                    successes = [r for r in results if isinstance(r, ControlCommandResult)]
                    conflicts = [
                        r
                        for r in results
                        if isinstance(r, ProtectedSecurityError) and r.reason_code == "CONTROL_COMMAND_CONFLICT"
                    ]
                    assert len(successes) == 1, f"Expected 1 success, got {results}"
                    assert len(conflicts) == 1, f"Expected 1 conflict, got {results}"
                    assert successes[0].reason_code == "IDENTITY_REGISTERED"

                    async with admin_engine.connect() as connection:
                        rows = (
                            await connection.execute(
                                text(
                                    f'SELECT database_login FROM "{database.schema}".protected_identity '
                                    "WHERE actor_id = :actor AND actor_namespace = :ns AND identity_plane = :plane"
                                ),
                                {"actor": shared_actor, "ns": "SERVICE_IDENTITY", "plane": "DATA"},
                            )
                        ).all()
                        assert len(rows) == 1

                        audit_rows = (
                            await connection.execute(
                                text(
                                    f'SELECT event_id, entry_body FROM "{database.schema}".audit_entry '
                                    "WHERE event_id IN (CAST(:req1 AS uuid), CAST(:req2 AS uuid))"
                                ),
                                {"req1": cmd1.request_id, "req2": cmd2.request_id},
                            )
                        ).all()
                        assert len(audit_rows) == 2
                        body_map = {
                            str(r.event_id): (
                                json.loads(r.entry_body) if isinstance(r.entry_body, str) else r.entry_body
                            )
                            for r in audit_rows
                        }
                        assert cmd1.request_id in body_map
                        assert cmd2.request_id in body_map
                        outcomes = {body_map[cmd1.request_id]["outcome"], body_map[cmd2.request_id]["outcome"]}
                        assert outcomes == {"SUCCEEDED", "DENIED"}

                        succeeded_body = next(b for b in body_map.values() if b["outcome"] == "SUCCEEDED")
                        denied_body = next(b for b in body_map.values() if b["outcome"] == "DENIED")
                        assert succeeded_body["reason_code"] == "IDENTITY_REGISTERED"
                        assert denied_body["reason_code"] == "CONTROL_COMMAND_CONFLICT"

                    entries = await _verify_audit_chain(admin_engine, database.schema)
                    assert len(entries) >= 2
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_register_identity_dual_conflict_disjoint_rows(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    admin_engine = create_async_engine(database.url)
    try:
        async with _reviewer(database, admin_engine) as (rev_login, _, service):
            target_login_a = f"pr512_disjoint_a_{uuid4().hex[:8]}"
            target_login_b = f"pr512_disjoint_b_{uuid4().hex[:8]}"
            actor_a = f"actor-disjoint-a-{uuid4().hex[:6]}"
            actor_b = f"actor-disjoint-b-{uuid4().hex[:6]}"

            async with admin_engine.begin() as connection:
                await connection.execute(
                    text(
                        f'''INSERT INTO "{database.schema}".protected_identity (
                            database_login, actor_id, actor_namespace, principal_role, identity_plane, approval_role, enabled
                        ) VALUES
                        (:l_a, :a_a, 'SERVICE_IDENTITY', 'PROTECTED_RUNNER', 'DATA', NULL, true),
                        (:l_b, :a_b, 'SERVICE_IDENTITY', 'PROTECTED_RUNNER', 'DATA', NULL, true)'''
                    ),
                    {"l_a": target_login_a, "a_a": actor_a, "l_b": target_login_b, "a_b": actor_b},
                )

            cross_cmd = RegisterIdentityCommand(
                request_id=str(uuid4()),
                database_login=target_login_a,
                actor_id=actor_b,
                actor_namespace="SERVICE_IDENTITY",
                identity_plane="DATA",
                principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
            )

            with pytest.raises(ProtectedSecurityError, match="CONTROL_COMMAND_CONFLICT"):
                await service.register_identity(cross_cmd)

            async with admin_engine.connect() as connection:
                audit_row = (
                    await connection.execute(
                        text(
                            f'SELECT entry_body FROM "{database.schema}".audit_entry '
                            "WHERE event_id = CAST(:req_id AS uuid)"
                        ),
                        {"req_id": cross_cmd.request_id},
                    )
                ).one()
                body = (
                    json.loads(audit_row.entry_body) if isinstance(audit_row.entry_body, str) else audit_row.entry_body
                )
                assert body["outcome"] == "DENIED"
                assert body["reason_code"] == "CONTROL_COMMAND_CONFLICT"
    finally:
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_cross_disable_no_deadlock(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    admin_engine = create_async_engine(database.url)
    try:
        async with _reviewer(database, admin_engine) as (rev_a, actor_a, service_a):
            async with _reviewer(database, admin_engine) as (rev_b, actor_b, service_b):
                dis_a_to_b = DisableIdentityCommand(
                    request_id=str(uuid4()),
                    database_login=rev_b,
                    expected_actor_id=actor_b,
                    expected_actor_namespace="GITHUB_LOGIN",
                )
                dis_b_to_a = DisableIdentityCommand(
                    request_id=str(uuid4()),
                    database_login=rev_a,
                    expected_actor_id=actor_a,
                    expected_actor_namespace="GITHUB_LOGIN",
                )

                aux_engine = create_async_engine(database.url)
                aux_conn = await aux_engine.connect()
                aux_trans: AsyncTransaction | None = await aux_conn.begin()
                tasks: list[asyncio.Task[object]] = []
                try:
                    await aux_conn.execute(
                        text(f'SELECT sequence FROM "{database.schema}".audit_head WHERE singleton FOR UPDATE')
                    )
                    aux_pid = await aux_conn.scalar(text("SELECT pg_backend_pid()"))
                    assert aux_pid is not None

                    task_a: asyncio.Task[object] = asyncio.create_task(service_a.disable_identity(dis_a_to_b))
                    tasks.append(task_a)

                    pid_a = await _wait_for_blocked_by(admin_engine, blocking_pid=int(aux_pid), timeout=10.0)
                    assert pid_a is not None

                    task_b: asyncio.Task[object] = asyncio.create_task(service_b.disable_identity(dis_b_to_a))
                    tasks.append(task_b)

                    pid_b = await _wait_for_blocked_by(admin_engine, blocking_pid=pid_a, timeout=10.0)
                    assert pid_b is not None

                    assert aux_trans is not None
                    await aux_trans.rollback()
                    aux_trans = None

                    results = await asyncio.wait_for(
                        asyncio.gather(task_a, task_b, return_exceptions=True), timeout=10.0
                    )
                    r_a, r_b = results
                    assert isinstance(r_a, ControlCommandResult)
                    assert r_a.command_kind == "DISABLE_IDENTITY"
                    assert r_a.reason_code == "IDENTITY_DISABLED"
                    assert r_a.target_id == rev_b

                    assert isinstance(r_b, ProtectedSecurityError)
                    assert r_b.reason_code == "AUTHORIZATION_NOT_FOUND"

                    async with admin_engine.connect() as connection:
                        states = (
                            await connection.execute(
                                text(
                                    f'SELECT database_login, enabled FROM "{database.schema}".protected_identity '
                                    "WHERE database_login IN (:a, :b)"
                                ),
                                {"a": rev_a, "b": rev_b},
                            )
                        ).all()
                        state_map = {row[0]: row[1] for row in states}
                        assert state_map[rev_a] is True
                        assert state_map[rev_b] is False

                        audit_rows = (
                            await connection.execute(
                                text(
                                    f'SELECT event_id, entry_body FROM "{database.schema}".audit_entry '
                                    "WHERE event_id IN (CAST(:id1 AS uuid), CAST(:id2 AS uuid))"
                                ),
                                {"id1": dis_a_to_b.request_id, "id2": dis_b_to_a.request_id},
                            )
                        ).all()
                        assert len(audit_rows) == 2
                        body_map = {
                            str(r.event_id): (
                                json.loads(r.entry_body) if isinstance(r.entry_body, str) else r.entry_body
                            )
                            for r in audit_rows
                        }
                        assert body_map[dis_a_to_b.request_id]["outcome"] == "SUCCEEDED"
                        assert body_map[dis_a_to_b.request_id]["reason_code"] == "IDENTITY_DISABLED"
                        assert body_map[dis_b_to_a.request_id]["outcome"] == "DENIED"
                        assert body_map[dis_b_to_a.request_id]["reason_code"] == "AUTHORIZATION_NOT_FOUND"

                    entries = await _verify_audit_chain(admin_engine, database.schema)
                    entry_map = {e.event_id: e for e in entries}
                    assert dis_a_to_b.request_id in entry_map
                    assert dis_b_to_a.request_id in entry_map
                    assert entry_map[dis_a_to_b.request_id].sequence < entry_map[dis_b_to_a.request_id].sequence
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if aux_trans is not None:
                        await aux_trans.rollback()
                    await aux_conn.close()
                    await aux_engine.dispose()
    finally:
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_replay_identical_payload_multi_session(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    admin_engine = create_async_engine(database.url)
    try:
        async with _reviewer(database, admin_engine) as (rev_login, rev_actor, service_1):
            evidence = _evidence(f"approval-{uuid4()}", "a" * 64)
            source_2 = _ApprovalSource(evidence)
            service_2 = _service(database, source_2, login=rev_login)
            try:
                shared_req_id = str(uuid4())
                target_login = f"pr512_replay_{uuid4().hex[:8]}"
                cmd = RegisterIdentityCommand(
                    request_id=shared_req_id,
                    database_login=target_login,
                    actor_id="runner-replay",
                    actor_namespace="SERVICE_IDENTITY",
                    identity_plane="DATA",
                    principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
                )

                aux_engine = create_async_engine(database.url)
                aux_conn = await aux_engine.connect()
                aux_trans: AsyncTransaction | None = await aux_conn.begin()
                tasks: list[asyncio.Task[object]] = []
                try:
                    await aux_conn.execute(
                        text(f'SELECT sequence FROM "{database.schema}".audit_head WHERE singleton FOR UPDATE')
                    )
                    aux_pid = await aux_conn.scalar(text("SELECT pg_backend_pid()"))
                    assert aux_pid is not None

                    task_1: asyncio.Task[object] = asyncio.create_task(service_1.register_identity(cmd))
                    tasks.append(task_1)

                    pid_1 = await _wait_for_blocked_by(admin_engine, blocking_pid=int(aux_pid), timeout=10.0)
                    assert pid_1 is not None

                    task_2: asyncio.Task[object] = asyncio.create_task(service_2.register_identity(cmd))
                    tasks.append(task_2)

                    pid_2 = await _wait_for_blocked_by(admin_engine, blocking_pid=pid_1, timeout=10.0)
                    assert pid_2 is not None

                    assert aux_trans is not None
                    await aux_trans.rollback()
                    aux_trans = None

                    results = await asyncio.wait_for(
                        asyncio.gather(task_1, task_2, return_exceptions=True), timeout=10.0
                    )
                    r1, r2 = results
                    assert isinstance(r1, ControlCommandResult)
                    assert isinstance(r2, ControlCommandResult)
                    assert r1 == r2
                    assert r1.command_kind == "REGISTER_IDENTITY"
                    assert r1.reason_code == "IDENTITY_REGISTERED"

                    async with admin_engine.connect() as connection:
                        count = await connection.scalar(
                            text(
                                f'SELECT count(*) FROM "{database.schema}".protected_identity WHERE database_login = :login'
                            ),
                            {"login": target_login},
                        )
                        assert count == 1

                        audit_count = await connection.scalar(
                            text(
                                f'SELECT count(*) FROM "{database.schema}".audit_entry '
                                "WHERE event_id = CAST(:req_id AS uuid)"
                            ),
                            {"req_id": shared_req_id},
                        )
                        assert audit_count == 1

                    entries = await _verify_audit_chain(admin_engine, database.schema)
                    assert len(entries) >= 1
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if aux_trans is not None:
                        await aux_trans.rollback()
                    await aux_conn.close()
                    await aux_engine.dispose()
            finally:
                await service_2.close()
    finally:
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_replay_different_payload_multi_session(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    admin_engine = create_async_engine(database.url)
    try:
        async with _reviewer(database, admin_engine) as (rev_login, rev_actor, service_1):
            evidence = _evidence(f"approval-{uuid4()}", "a" * 64)
            source_2 = _ApprovalSource(evidence)
            service_2 = _service(database, source_2, login=rev_login)
            try:
                shared_req_id = str(uuid4())
                target_1 = f"pr512_diff1_{uuid4().hex[:8]}"
                target_2 = f"pr512_diff2_{uuid4().hex[:8]}"
                cmd1 = RegisterIdentityCommand(
                    request_id=shared_req_id,
                    database_login=target_1,
                    actor_id="runner-diff-1",
                    actor_namespace="SERVICE_IDENTITY",
                    identity_plane="DATA",
                    principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
                )
                cmd2 = RegisterIdentityCommand(
                    request_id=shared_req_id,
                    database_login=target_2,
                    actor_id="runner-diff-2",
                    actor_namespace="SERVICE_IDENTITY",
                    identity_plane="DATA",
                    principal_role=ProtectedPrincipalRole.PROTECTED_RUNNER,
                )

                aux_engine = create_async_engine(database.url)
                aux_conn = await aux_engine.connect()
                aux_trans: AsyncTransaction | None = await aux_conn.begin()
                tasks: list[asyncio.Task[object]] = []
                try:
                    await aux_conn.execute(
                        text(f'SELECT sequence FROM "{database.schema}".audit_head WHERE singleton FOR UPDATE')
                    )
                    aux_pid = await aux_conn.scalar(text("SELECT pg_backend_pid()"))
                    assert aux_pid is not None

                    task_1: asyncio.Task[object] = asyncio.create_task(service_1.register_identity(cmd1))
                    tasks.append(task_1)

                    pid_1 = await _wait_for_blocked_by(admin_engine, blocking_pid=int(aux_pid), timeout=10.0)
                    assert pid_1 is not None

                    task_2: asyncio.Task[object] = asyncio.create_task(service_2.register_identity(cmd2))
                    tasks.append(task_2)

                    pid_2 = await _wait_for_blocked_by(admin_engine, blocking_pid=pid_1, timeout=10.0)
                    assert pid_2 is not None

                    assert aux_trans is not None
                    await aux_trans.rollback()
                    aux_trans = None

                    results = await asyncio.wait_for(
                        asyncio.gather(task_1, task_2, return_exceptions=True), timeout=10.0
                    )

                    successes = [r for r in results if isinstance(r, ControlCommandResult)]
                    conflicts = [
                        r
                        for r in results
                        if isinstance(r, ProtectedSecurityError) and r.reason_code == "CONTROL_COMMAND_CONFLICT"
                    ]
                    assert len(successes) == 1, f"Expected 1 success, got {results}"
                    assert len(conflicts) == 1, f"Expected 1 conflict, got {results}"

                    async with admin_engine.connect() as connection:
                        inserted_targets = (
                            (
                                await connection.execute(
                                    text(
                                        f'SELECT database_login FROM "{database.schema}".protected_identity '
                                        "WHERE database_login IN (:t1, :t2)"
                                    ),
                                    {"t1": target_1, "t2": target_2},
                                )
                            )
                            .scalars()
                            .all()
                        )
                        assert len(inserted_targets) == 1
                        assert inserted_targets[0] == successes[0].target_id

                        audit_entries = (
                            await connection.execute(
                                text(
                                    f'SELECT event_id, entry_body FROM "{database.schema}".audit_entry '
                                    "WHERE event_id = CAST(:req_id AS uuid)"
                                ),
                                {"req_id": shared_req_id},
                            )
                        ).all()
                        assert len(audit_entries) == 1
                        body = (
                            json.loads(audit_entries[0].entry_body)
                            if isinstance(audit_entries[0].entry_body, str)
                            else audit_entries[0].entry_body
                        )
                        assert body["outcome"] == "SUCCEEDED"

                    entries = await _verify_audit_chain(admin_engine, database.schema)
                    assert len(entries) >= 1
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if aux_trans is not None:
                        await aux_trans.rollback()
                    await aux_conn.close()
                    await aux_engine.dispose()
            finally:
                await service_2.close()
    finally:
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_author_and_custodian_identities_can_coexist_for_same_actor(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    admin_engine = create_async_engine(database.url)
    try:
        async with _reviewer(database, admin_engine) as (reviewer_login, _, reviewer_service):
            shared_actor = f"dual-actor-{uuid4().hex[:6]}"
            custodian_login = f"pr512_cust_{uuid4().hex[:8]}"
            author_login = f"pr512_auth_{uuid4().hex[:8]}"

            # 1. Register CONTROL plane DATASET_CUSTODIAN
            cmd_custodian = RegisterIdentityCommand(
                request_id=str(uuid4()),
                database_login=custodian_login,
                actor_id=shared_actor,
                actor_namespace="GITHUB_LOGIN",
                identity_plane="CONTROL",
                approval_role=ProtectedApprovalRole.DATASET_CUSTODIAN,
            )
            res_cust = await reviewer_service.register_identity(cmd_custodian)
            assert res_cust.reason_code == "IDENTITY_REGISTERED"

            # 2. Register DATA plane HOLDOUT_AUTHOR for the same actor
            cmd_author = RegisterIdentityCommand(
                request_id=str(uuid4()),
                database_login=author_login,
                actor_id=shared_actor,
                actor_namespace="GITHUB_LOGIN",
                identity_plane="DATA",
                principal_role=ProtectedPrincipalRole.HOLDOUT_AUTHOR,
            )
            res_auth = await reviewer_service.register_identity(cmd_author)
            assert res_auth.reason_code == "IDENTITY_REGISTERED"

            # 3. Assert both rows exist in protected_identity
            async with admin_engine.connect() as connection:
                rows = (
                    await connection.execute(
                        text(
                            f"""
                            SELECT database_login, actor_id, actor_namespace, identity_plane,
                                   principal_role, approval_role, enabled
                            FROM "{database.schema}".protected_identity
                            WHERE actor_id = :actor_id AND actor_namespace = 'GITHUB_LOGIN'
                            ORDER BY database_login ASC
                            """
                        ),
                        {"actor_id": shared_actor},
                    )
                ).all()
                assert len(rows) == 2
                cust_row = next(r for r in rows if r.identity_plane == "CONTROL")
                auth_row = next(r for r in rows if r.identity_plane == "DATA")
                assert cust_row.approval_role == "DATASET_CUSTODIAN"
                assert cust_row.principal_role is None
                assert auth_row.principal_role == "HOLDOUT_AUTHOR"
                assert auth_row.approval_role is None
                assert cust_row.enabled is True
                assert auth_row.enabled is True

            # 4. Assert audit chain is verified and contains both events
            entries = await _verify_audit_chain(admin_engine, database.schema)
            assert len(entries) >= 2
    finally:
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_register_author_and_custodian_dataset_command_author_first(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    admin_engine = create_async_engine(database.url)
    try:
        async with _reviewer(database, admin_engine) as (rev_login, _, rev_service):
            shared_actor = f"dual-actor-{uuid4().hex[:6]}"
            custodian_login = f"pr512_cust_{uuid4().hex[:8]}"
            author_login = f"pr512_auth_{uuid4().hex[:8]}"

            async with admin_engine.begin() as connection:
                await connection.exec_driver_sql(
                    f"CREATE ROLE \"{custodian_login}\" LOGIN PASSWORD '{database.password}' "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                )
                await connection.exec_driver_sql(f'GRANT "{database.control}" TO "{custodian_login}"')
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO "{database.schema}".protected_identity (
                            database_login, actor_id, actor_namespace, approval_role, identity_plane, enabled
                        ) VALUES (
                            :login, :actor_id, 'GITHUB_LOGIN', 'DATASET_CUSTODIAN', 'CONTROL', true
                        )
                        """
                    ),
                    {"login": custodian_login, "actor_id": shared_actor},
                )
                await connection.exec_driver_sql(
                    f"CREATE ROLE \"{author_login}\" LOGIN PASSWORD '{database.password}' "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                )
                await connection.exec_driver_sql(f'GRANT "{database.access}" TO "{author_login}"')

            cust_service = _service(database, _DatasetApprovalSource(), login=custodian_login)
            try:
                cmd_dataset = _register_dataset_command()
                cmd_author = RegisterIdentityCommand(
                    request_id=str(uuid4()),
                    database_login=author_login,
                    actor_id=shared_actor,
                    actor_namespace="GITHUB_LOGIN",
                    identity_plane="DATA",
                    principal_role=ProtectedPrincipalRole.HOLDOUT_AUTHOR,
                )

                read_replay_done = asyncio.Event()
                resume_dataset = asyncio.Event()
                orig_read_replay = cust_service._read_replay

                async def _wrapped_read_replay(*args: Any, **kwargs: Any) -> Any:
                    res = await orig_read_replay(*args, **kwargs)
                    read_replay_done.set()
                    await resume_dataset.wait()
                    return res

                cust_service._read_replay = _wrapped_read_replay  # type: ignore[assignment]

                aux_engine = create_async_engine(database.url)
                aux_conn = await aux_engine.connect()
                aux_trans: AsyncTransaction | None = await aux_conn.begin()
                tasks: list[asyncio.Task[object]] = []
                try:
                    # Dataset command starts -> finishes _read_replay and pauses at barrier before mutation transaction
                    task_dataset: asyncio.Task[object] = asyncio.create_task(cust_service.register_dataset(cmd_dataset))
                    tasks.append(task_dataset)
                    await asyncio.wait_for(read_replay_done.wait(), timeout=10.0)

                    # 1. aux locks audit_head
                    await aux_conn.execute(
                        text(f'SELECT sequence FROM "{database.schema}".audit_head WHERE singleton FOR UPDATE')
                    )
                    aux_pid = await aux_conn.scalar(text("SELECT pg_backend_pid()"))
                    assert aux_pid is not None

                    # 2. Author registration starts -> acquires identity row lock for shared_actor, blocks on aux's audit_head
                    task_author: asyncio.Task[object] = asyncio.create_task(rev_service.register_identity(cmd_author))
                    tasks.append(task_author)

                    author_pid = await _wait_for_blocked_by(admin_engine, blocking_pid=int(aux_pid), timeout=10.0)
                    assert author_pid is not None

                    # 3. Resume dataset command -> enters mutation transaction, attempts to lock identity rows,
                    # and must block specifically on author_pid (which holds the actor identity row lock)
                    resume_dataset.set()

                    cust_pid = await _wait_for_blocked_by(
                        admin_engine,
                        blocking_pid=author_pid,
                        timeout=10.0,
                    )
                    assert cust_pid is not None

                    async with admin_engine.connect() as probe_conn:
                        blocking_pids = await probe_conn.scalar(
                            text("SELECT pg_blocking_pids(:pid)"),
                            {"pid": cust_pid},
                        )
                        assert blocking_pids is not None
                        assert author_pid in blocking_pids

                    # 4. Release audit_head
                    assert aux_trans is not None
                    await aux_trans.rollback()
                    aux_trans = None

                    # Author registration succeeds
                    res_author = await asyncio.wait_for(task_author, timeout=10.0)
                    assert isinstance(res_author, ControlCommandResult)
                    assert res_author.reason_code == "IDENTITY_REGISTERED"

                    # Custodian unblocks, sees newly inserted HOLDOUT_AUTHOR, and fails with SELF_APPROVAL_DENIED
                    with pytest.raises(ProtectedSecurityError) as exc_info:
                        await asyncio.wait_for(task_dataset, timeout=10.0)
                    assert exc_info.value.reason_code == "SELF_APPROVAL_DENIED"

                    # Assert no 40P01 deadlock
                    assert "40P01" not in str(exc_info.value)

                    entries = await _verify_audit_chain(admin_engine, database.schema)
                    entry_map = {e.event_id: e for e in entries}
                    assert cmd_author.request_id in entry_map
                    assert cmd_dataset.request_id in entry_map

                    author_entry = entry_map[cmd_author.request_id]
                    assert isinstance(author_entry, ControlCommandAuditEntry)
                    assert author_entry.outcome == ControlAuditOutcome.SUCCEEDED
                    assert author_entry.reason_code == ProtectedAuditReason.IDENTITY_REGISTERED

                    cust_entry = entry_map[cmd_dataset.request_id]
                    assert isinstance(cust_entry, ControlCommandAuditEntry)
                    assert cust_entry.outcome == ControlAuditOutcome.DENIED
                    assert cust_entry.reason_code == ProtectedAuditReason.SELF_APPROVAL_DENIED

                    assert author_entry.sequence < cust_entry.sequence
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if aux_trans is not None:
                        await aux_trans.rollback()
                    await aux_conn.close()
                    await aux_engine.dispose()
            finally:
                await cust_service.close()
                async with admin_engine.begin() as connection:
                    await connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{custodian_login}"')
                    await connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{author_login}"')
    finally:
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_register_author_and_custodian_dataset_command_dataset_first(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    admin_engine = create_async_engine(database.url)
    try:
        async with _reviewer(database, admin_engine) as (rev_login, _, rev_service):
            shared_actor = f"dual-actor-{uuid4().hex[:6]}"
            custodian_login = f"pr512_cust_{uuid4().hex[:8]}"
            author_login = f"pr512_auth_{uuid4().hex[:8]}"

            async with admin_engine.begin() as connection:
                await connection.exec_driver_sql(
                    f"CREATE ROLE \"{custodian_login}\" LOGIN PASSWORD '{database.password}' "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                )
                await connection.exec_driver_sql(f'GRANT "{database.control}" TO "{custodian_login}"')
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO "{database.schema}".protected_identity (
                            database_login, actor_id, actor_namespace, approval_role, identity_plane, enabled
                        ) VALUES (
                            :login, :actor_id, 'GITHUB_LOGIN', 'DATASET_CUSTODIAN', 'CONTROL', true
                        )
                        """
                    ),
                    {"login": custodian_login, "actor_id": shared_actor},
                )
                await connection.exec_driver_sql(
                    f"CREATE ROLE \"{author_login}\" LOGIN PASSWORD '{database.password}' "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                )
                await connection.exec_driver_sql(f'GRANT "{database.access}" TO "{author_login}"')

            cust_service = _service(database, _DatasetApprovalSource(), login=custodian_login)
            try:
                dataset = _dataset_binding(
                    dataset_id=str(uuid4()),
                    state=ProtectedDatasetState.ACCESS_AUTHORIZED,
                    state_revision=1,
                )
                await _insert_dataset(database, dataset)

                cmd_dataset_1 = _transition_command(
                    dataset,
                    from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
                    to_state=ProtectedDatasetState.AUTHORING,
                    revision=1,
                    authored_count=0,
                    review_complete=False,
                )
                cmd_author = RegisterIdentityCommand(
                    request_id=str(uuid4()),
                    database_login=author_login,
                    actor_id=shared_actor,
                    actor_namespace="GITHUB_LOGIN",
                    identity_plane="DATA",
                    principal_role=ProtectedPrincipalRole.HOLDOUT_AUTHOR,
                )

                aux_engine = create_async_engine(database.url)
                aux_conn = await aux_engine.connect()
                aux_trans: AsyncTransaction | None = await aux_conn.begin()
                tasks: list[asyncio.Task[object]] = []
                try:
                    # Aux locks protected_dataset row
                    await aux_conn.execute(
                        text(
                            f'''SELECT dataset_id FROM "{database.schema}".protected_dataset
                            WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version FOR UPDATE'''
                        ),
                        {"dataset_id": dataset.dataset_id, "dataset_version": dataset.dataset_version},
                    )
                    aux_pid = await aux_conn.scalar(text("SELECT pg_backend_pid()"))
                    assert aux_pid is not None

                    # 1. Custodian dataset command starts first -> acquires identity row locks, blocks on protected_dataset
                    task_dataset: asyncio.Task[object] = asyncio.create_task(
                        cust_service.transition_dataset(cmd_dataset_1)
                    )
                    tasks.append(task_dataset)

                    cust_pid = await _wait_for_blocked_by(admin_engine, blocking_pid=int(aux_pid), timeout=10.0)
                    assert cust_pid is not None

                    # 2. Author registration starts -> blocked waiting for identity row lock held by custodian
                    task_author: asyncio.Task[object] = asyncio.create_task(rev_service.register_identity(cmd_author))
                    tasks.append(task_author)

                    author_pid = await _wait_for_blocked_by(
                        admin_engine,
                        blocking_pids=(int(aux_pid), cust_pid),
                        exclude_pids=(cust_pid,),
                        timeout=10.0,
                    )
                    assert author_pid is not None

                    # 3. Release protected_dataset
                    assert aux_trans is not None
                    await aux_trans.rollback()
                    aux_trans = None

                    # Custodian dataset command succeeds
                    res_dataset = await asyncio.wait_for(task_dataset, timeout=10.0)
                    assert isinstance(res_dataset, ControlCommandResult)
                    assert res_dataset.reason_code == "DATASET_TRANSITIONED"

                    # Author registration unblocks and succeeds (cross-plane identity coexistence)
                    res_author = await asyncio.wait_for(task_author, timeout=10.0)
                    assert isinstance(res_author, ControlCommandResult)
                    assert res_author.reason_code == "IDENTITY_REGISTERED"

                    # 4. Subsequent dataset command by custodian for the same actor must fail with SELF_APPROVAL_DENIED
                    cmd_dataset_2 = _transition_command(
                        dataset,
                        from_state=ProtectedDatasetState.AUTHORING,
                        to_state=ProtectedDatasetState.REVIEW_READY,
                        revision=2,
                        authored_count=10,
                        review_complete=False,
                    )
                    with pytest.raises(ProtectedSecurityError) as exc_info:
                        await cust_service.transition_dataset(cmd_dataset_2)
                    assert exc_info.value.reason_code == "SELF_APPROVAL_DENIED"

                    # Assert no 40P01 deadlock
                    assert "40P01" not in str(exc_info.value)

                    entries = await _verify_audit_chain(admin_engine, database.schema)
                    assert len(entries) >= 3
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if aux_trans is not None:
                        await aux_trans.rollback()
                    await aux_conn.close()
                    await aux_engine.dispose()
            finally:
                await cust_service.close()
                async with admin_engine.begin() as connection:
                    await connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{custodian_login}"')
                    await connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{author_login}"')
    finally:
        await admin_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("first_task", ["transition", "grant"])
async def test_concurrent_grant_and_dataset_transition_deadlock_prevention(
    protected_database: _ProtectedDatabase,
    first_task: str,
) -> None:
    database = protected_database
    dataset_uuid = str(uuid4())
    dataset = _dataset_binding(
        dataset_id=dataset_uuid,
        state=ProtectedDatasetState.ACCESS_AUTHORIZED,
        state_revision=1,
    )
    await _insert_dataset(database, dataset)
    now = datetime.now(UTC)
    grant = ProtectedAuthorizationGrant(
        grant_id=str(uuid4()),
        revision=1,
        subject=ProtectedPrincipal(
            actor=ActorIdentity(actor_id="synthetic-author", namespace="SERVICE_IDENTITY"),
            role=ProtectedPrincipalRole.HOLDOUT_AUTHOR,
        ),
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        manifest_sha256=dataset.manifest_sha256,
        protected_artifact_sha256=dataset.protected_artifact_sha256,
        hmac_key_version=dataset.hmac_key_version,
        actions=(ProtectedAction.READ, ProtectedAction.WRITE),
        issuer=ProtectedApprovalPrincipal(
            actor=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
            role=ProtectedApprovalRole.DATASET_CUSTODIAN,
        ),
        control_implementation=ControlImplementationBinding(
            commit_oid="3" * 40,
            artifact_sha256="4" * 64,
            participants=(ActorIdentity(actor_id="synthetic-implementer", namespace="GITHUB_LOGIN"),),
        ),
        approval_source_event_id=f"grant-approval-{uuid4()}",
        approval_source_raw_sha256="5" * 64,
        valid_from=now - timedelta(minutes=20),
        expires_at=now + timedelta(minutes=10),
    )
    evidence = _evidence(
        grant.approval_source_event_id,
        grant.approval_source_raw_sha256,
        grant=grant,
    )
    source = _ApprovalSource(evidence)
    grant_service = _service(database, source)
    custodian_service = _service(database, _DatasetApprovalSource())
    admin_engine = create_async_engine(database.url)
    aux_engine = create_async_engine(database.url)
    tasks: list[asyncio.Task[object]] = []
    try:
        await grant_service.ingest_approval(
            IngestApprovalCommand(
                request_id=str(uuid4()),
                source_event_id=evidence.source_event_id,
                expected_raw_sha256=evidence.canonical_raw_sha256,
            )
        )
        transition_cmd = _transition_command(
            dataset,
            from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
            to_state=ProtectedDatasetState.AUTHORING,
            revision=1,
            authored_count=0,
            review_complete=False,
        )
        grant_cmd = GrantAuthorizationCommand(
            request_id=str(uuid4()),
            grant=grant,
            expected_dataset_state_revision=dataset.state_revision,
        )

        async with aux_engine.connect() as aux_conn:
            aux_trans = await aux_conn.begin()
            try:
                aux_pid = await aux_conn.scalar(text("SELECT pg_backend_pid()"))
                # Pre-lock protected_dataset for this dataset in aux transaction
                await aux_conn.execute(
                    text(
                        f'''SELECT dataset_id FROM "{database.schema}".protected_dataset
                        WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version FOR UPDATE'''
                    ),
                    {"dataset_id": dataset.dataset_id, "dataset_version": dataset.dataset_version},
                )

                if first_task == "transition":
                    # Start transition task first
                    task_trans = asyncio.create_task(custodian_service.transition_dataset(transition_cmd))
                    tasks.append(task_trans)
                    trans_pid = await _wait_for_blocked_by(admin_engine, blocking_pid=aux_pid)

                    # Start grant task second
                    task_grant = asyncio.create_task(grant_service.grant(grant_cmd))
                    tasks.append(task_grant)
                    grant_pid = await _wait_for_blocked_by(
                        admin_engine, blocking_pids=(aux_pid, trans_pid), exclude_pids=(trans_pid,)
                    )
                else:
                    # Start grant task first
                    task_grant = asyncio.create_task(grant_service.grant(grant_cmd))
                    tasks.append(task_grant)
                    grant_pid = await _wait_for_blocked_by(admin_engine, blocking_pid=aux_pid)

                    # Start transition task second
                    task_trans = asyncio.create_task(custodian_service.transition_dataset(transition_cmd))
                    tasks.append(task_trans)
                    trans_pid = await _wait_for_blocked_by(
                        admin_engine, blocking_pids=(aux_pid, grant_pid), exclude_pids=(grant_pid,)
                    )

                assert trans_pid != grant_pid
                assert trans_pid != aux_pid
                assert grant_pid != aux_pid
            finally:
                # Release aux lock so both commands contend in aligned lock order
                await aux_trans.rollback()

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Assert NO deadlock (40P01) occurred
            for r in results:
                if isinstance(r, Exception):
                    assert "40P01" not in str(r)
                    assert "INTERNAL_ERROR" not in str(r)

            successes = [r for r in results if isinstance(r, ControlCommandResult)]
            assert len(successes) >= 1
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await aux_engine.dispose()
        await admin_engine.dispose()
        await grant_service.close()
        await custodian_service.close()
