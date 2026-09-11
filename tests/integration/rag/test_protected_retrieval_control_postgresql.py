"""Real PostgreSQL tests for protected authorization-control commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ai_worker.adapters.postgresql_protected_retrieval_control import (
    PostgresqlProtectedAuthorizationControlService,
)
from ai_worker.tasks.evaluation.protected_retrieval import (
    ActorIdentity,
    ApprovalSourceEvidence,
    AuthorizationAuditAction,
    ControlImplementationBinding,
    ProtectedAction,
    ProtectedApprovalPrincipal,
    ProtectedApprovalRole,
    ProtectedAuthorizationGrant,
    ProtectedDatasetBinding,
    ProtectedDatasetState,
    ProtectedPrincipal,
    ProtectedPrincipalRole,
    ProtectedSecurityError,
    authorization_grant_approval_sha256,
)
from ai_worker.tasks.evaluation.protected_retrieval_control import (
    GrantAuthorizationCommand,
    IngestApprovalCommand,
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


def _evidence(
    source_event_id: str,
    raw_sha256: str,
    *,
    grant: ProtectedAuthorizationGrant | None = None,
) -> ApprovalSourceEvidence:
    issuer = grant.issuer if grant is not None else ProtectedApprovalPrincipal(
        actor=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
        role=ProtectedApprovalRole.DATASET_CUSTODIAN,
    )
    return ApprovalSourceEvidence(
        source_event_id=source_event_id,
        authorization_action=AuthorizationAuditAction.GRANT,
        approved_grant_payload_sha256=(
            authorization_grant_approval_sha256(grant) if grant is not None else "b" * 64
        ),
        issuer=issuer,
        state="APPROVED",
        recorded_at=datetime(2026, 9, 11, 1, 2, 3, tzinfo=UTC),
        target_commit_oid=grant.control_implementation.commit_oid if grant is not None else "c" * 40,
        target_artifact_sha256=(
            grant.control_implementation.artifact_sha256 if grant is not None else "d" * 64
        ),
        canonical_raw_sha256=raw_sha256,
        implementation_participants=(
            grant.control_implementation.participants
            if grant is not None
            else (ActorIdentity(actor_id="synthetic-implementer", namespace="GITHUB_LOGIN"),)
        ),
    )


def _grant_scenario() -> tuple[ProtectedDatasetBinding, ProtectedAuthorizationGrant, ApprovalSourceEvidence]:
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
        valid_from=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=10),
    )
    return dataset, grant, _evidence(
        grant.approval_source_event_id,
        grant.approval_source_raw_sha256,
        grant=grant,
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


def _service(
    database: _ProtectedDatabase,
    source: _ApprovalSource,
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
        with pytest.raises(ProtectedSecurityError, match="APPROVAL_EVIDENCE_MISMATCH") as captured:
            await service.ingest_approval(command)
        assert evidence.issuer.actor.actor_id not in str(captured.value)

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
            assert count == 0
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
