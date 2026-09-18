"""PostgreSQL protected-retrieval adapter integration tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.adapters.postgresql_protected_retrieval import (
    PostgresqlApprovalEvidenceVerifier,
    PostgresqlAuthorizationGuard,
    PostgresqlAuthorizationLedger,
    PostgresqlProtectedArtifactOperation,
    PostgresqlProtectedAuditJournal,
    PostgresqlTrustedClock,
)
from ai_worker.core.config import Config
from ai_worker.core.runtime_assembly import create_protected_retrieval_service
from ai_worker.tasks.evaluation.protected_retrieval import (
    ActorIdentity,
    ControlAuditOutcome,
    ControlAuditTargetKind,
    ControlCommandAuditEntry,
    ControlImplementationBinding,
    OpaqueLogicalRef,
    OpaqueRefNamespace,
    OperationAuditOutcome,
    ProtectedAction,
    ProtectedApprovalPrincipal,
    ProtectedApprovalRole,
    ProtectedAuditEventKind,
    ProtectedAuditReason,
    ProtectedAuthorizationGrant,
    ProtectedDatasetBinding,
    ProtectedDatasetState,
    ProtectedOperationRequest,
    ProtectedOperationResult,
    ProtectedPrincipal,
    ProtectedPrincipalRole,
    ProtectedSecurityError,
    audit_entry_sha256,
    execute_protected_operation,
)
from provider_contracts.observability import DeploymentEnvironment
from tests.migration.test_protected_retrieval_migration import (
    _login_url,
    _ProtectedDatabase,
)

pytest_plugins = ("tests.migration.test_protected_retrieval_migration",)


def test_postgresql_adapter_surface_is_explicit() -> None:
    assert PostgresqlTrustedClock
    assert PostgresqlApprovalEvidenceVerifier
    assert PostgresqlAuthorizationLedger
    assert PostgresqlProtectedAuditJournal
    assert PostgresqlAuthorizationGuard
    assert PostgresqlProtectedArtifactOperation


@dataclass(frozen=True)
class _ReadScenario:
    payload: bytes
    dataset: ProtectedDatasetBinding
    principal: ProtectedPrincipal
    grant: ProtectedAuthorizationGrant
    request: ProtectedOperationRequest


def _read_scenario(action: ProtectedAction = ProtectedAction.READ) -> _ReadScenario:
    payload = b"approved synthetic protected envelope"
    runner_ref = OpaqueLogicalRef(namespace=OpaqueRefNamespace.REQUEST, value=str(uuid4()))
    is_run = action is ProtectedAction.RUN
    freeze_ref = OpaqueLogicalRef(namespace=OpaqueRefNamespace.AUDIT_EVENT, value=str(uuid4())) if is_run else None
    dataset = ProtectedDatasetBinding(
        dataset_id=str(uuid4()),
        dataset_version="1.0.0",
        manifest_sha256="a" * 64,
        protected_artifact_sha256=sha256(payload).hexdigest(),
        hmac_key_version="synthetic-key-v1",
        state=ProtectedDatasetState.FROZEN if is_run else ProtectedDatasetState.AUTHORING,
        state_revision=1,
        authored_count=40 if is_run else 0,
        review_complete=is_run,
        leakage_axis_intersections=(0, 0, 0, 0) if is_run else None,
        freeze_receipt_ref=freeze_ref,
        execution_authorization_ref=runner_ref if is_run else None,
        retriever_binding_ref=runner_ref if is_run else None,
    )
    principal = ProtectedPrincipal(
        actor=ActorIdentity(actor_id="synthetic-author", namespace="SERVICE_IDENTITY"),
        role=ProtectedPrincipalRole.PROTECTED_RUNNER if is_run else ProtectedPrincipalRole.HOLDOUT_AUTHOR,
    )
    request = ProtectedOperationRequest(
        request_id=str(uuid4()),
        operation_key=f"synthetic-read-{uuid4()}",
        dataset=dataset,
        target_ref=OpaqueLogicalRef(namespace=OpaqueRefNamespace.HOLDOUT_SET, value=str(uuid4())),
        action=action,
        principal=principal,
    )
    now = datetime.now(UTC)
    grant = ProtectedAuthorizationGrant(
        grant_id=str(uuid4()),
        revision=1,
        subject=principal,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        manifest_sha256=dataset.manifest_sha256,
        protected_artifact_sha256=dataset.protected_artifact_sha256,
        hmac_key_version=dataset.hmac_key_version,
        actions=(action,),
        issuer=ProtectedApprovalPrincipal(
            actor=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
            role=ProtectedApprovalRole.DATASET_CUSTODIAN,
        ),
        control_implementation=ControlImplementationBinding(
            commit_oid="c" * 40,
            artifact_sha256="d" * 64,
            participants=(ActorIdentity(actor_id="synthetic-implementer", namespace="GITHUB_LOGIN"),),
        ),
        approval_source_event_id=f"synthetic-approval-{uuid4()}",
        approval_source_raw_sha256="e" * 64,
        valid_from=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=10),
    )
    return _ReadScenario(payload=payload, dataset=dataset, principal=principal, grant=grant, request=request)


async def _insert_read_scenario(database: _ProtectedDatabase, scenario: _ReadScenario, payload: bytes) -> None:
    engine = create_async_engine(database.url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    f'''
                    INSERT INTO "{database.schema}".protected_dataset (
                        dataset_id, dataset_version, binding, manifest_sha256,
                        protected_artifact_sha256, hmac_key_version, state,
                        state_revision, authored_count, review_complete
                    ) VALUES (
                        :dataset_id, :dataset_version, CAST(:binding AS jsonb), :manifest_sha256,
                        :artifact_sha256, :key_version, :state, :state_revision,
                        :authored_count, :review_complete
                    )
                    '''
                ),
                {
                    "dataset_id": scenario.dataset.dataset_id,
                    "dataset_version": scenario.dataset.dataset_version,
                    "binding": scenario.dataset.model_dump_json(),
                    "manifest_sha256": scenario.dataset.manifest_sha256,
                    "artifact_sha256": scenario.dataset.protected_artifact_sha256,
                    "key_version": scenario.dataset.hmac_key_version,
                    "state": scenario.dataset.state.value,
                    "state_revision": scenario.dataset.state_revision,
                    "authored_count": scenario.dataset.authored_count,
                    "review_complete": scenario.dataset.review_complete,
                },
            )
            if (
                scenario.dataset.state is ProtectedDatasetState.FROZEN
                and scenario.dataset.freeze_receipt_ref is not None
            ):
                head_row = (
                    await connection.execute(
                        text(f'SELECT sequence, entry_sha256 FROM "{database.schema}".audit_head WHERE singleton')
                    )
                ).one()
                current_sequence, previous_entry_sha256 = head_row
                next_sequence = current_sequence + 1
                now = datetime.now(UTC)
                entry = ControlCommandAuditEntry(
                    event_kind=ProtectedAuditEventKind.CONTROL,
                    sequence=next_sequence,
                    event_id=scenario.dataset.freeze_receipt_ref.value,
                    command_kind="FREEZE_DATASET",
                    executed_by=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
                    target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
                    target_id=f"{scenario.dataset.dataset_id}:{scenario.dataset.dataset_version}",
                    command_sha256="0" * 64,
                    outcome=ControlAuditOutcome.SUCCEEDED,
                    result_effective_revision=scenario.dataset.state_revision,
                    authorization_audit_event_id=None,
                    reason_code=ProtectedAuditReason.DATASET_FROZEN,
                    recorded_at=now,
                    previous_entry_sha256=previous_entry_sha256,
                    entry_sha256="0" * 64,
                )
                entry = entry.model_copy(update={"entry_sha256": audit_entry_sha256(entry)})
                await connection.execute(
                    text(
                        f'''
                        INSERT INTO "{database.schema}".audit_entry (
                            sequence, event_id, event_kind, entry_body,
                            previous_entry_sha256, entry_sha256, recorded_at, control_entry
                        ) VALUES (
                            :sequence, CAST(:event_id AS uuid), 'CONTROL', CAST(:entry_body AS jsonb),
                            :previous_entry_sha256, :entry_sha256, :recorded_at, true
                        )
                        '''
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
                        f'''
                        UPDATE "{database.schema}".audit_head
                        SET sequence = :sequence, entry_sha256 = :entry_sha256
                        WHERE singleton
                        '''
                    ),
                    {
                        "sequence": entry.sequence,
                        "entry_sha256": entry.entry_sha256,
                    },
                )
            await connection.execute(
                text(
                    f'''
                    INSERT INTO "{database.schema}".authorization_grant (
                        grant_id, revision, effective_revision, grant_body,
                        subject_actor_id, subject_namespace, subject_role,
                        dataset_id, dataset_version, manifest_sha256,
                        protected_artifact_sha256, hmac_key_version, actions,
                        valid_from, expires_at
                    ) VALUES (
                        CAST(:grant_id AS uuid), :revision, :revision, CAST(:grant_body AS jsonb),
                        :actor_id, :actor_namespace, :subject_role,
                        :dataset_id, :dataset_version, :manifest_sha256,
                        :artifact_sha256, :key_version, :actions,
                        :valid_from, :expires_at
                    )
                    '''
                ),
                {
                    "grant_id": scenario.grant.grant_id,
                    "revision": scenario.grant.revision,
                    "grant_body": scenario.grant.model_dump_json(),
                    "actor_id": scenario.principal.actor.actor_id,
                    "actor_namespace": scenario.principal.actor.namespace,
                    "subject_role": scenario.principal.role.value,
                    "dataset_id": scenario.dataset.dataset_id,
                    "dataset_version": scenario.dataset.dataset_version,
                    "manifest_sha256": scenario.dataset.manifest_sha256,
                    "artifact_sha256": scenario.dataset.protected_artifact_sha256,
                    "key_version": scenario.dataset.hmac_key_version,
                    "actions": [scenario.request.action.value],
                    "valid_from": scenario.grant.valid_from,
                    "expires_at": scenario.grant.expires_at,
                },
            )
            await connection.execute(
                text(
                    f'''
                    INSERT INTO "{database.schema}".protected_artifact (
                        target_ref, dataset_id, dataset_version, envelope, envelope_sha256, hmac_key_version
                    ) VALUES (
                        CAST(:target_ref AS uuid), :dataset_id, :dataset_version,
                        :envelope, :envelope_sha256, :key_version
                    )
                    '''
                ),
                {
                    "target_ref": scenario.request.target_ref.value,
                    "dataset_id": scenario.dataset.dataset_id,
                    "dataset_version": scenario.dataset.dataset_version,
                    "envelope": payload,
                    "envelope_sha256": sha256(payload).hexdigest(),
                    "key_version": scenario.dataset.hmac_key_version,
                },
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [ProtectedAction.READ, ProtectedAction.RUN])
async def test_read_rejects_artifact_not_bound_to_capability_before_callback(
    protected_database: _ProtectedDatabase,
    action: ProtectedAction,
) -> None:
    database = protected_database
    scenario = _read_scenario(action)
    forged_payload = b"unapproved but internally self-consistent synthetic envelope"
    await _insert_read_scenario(database, scenario, forged_payload)
    callback_payloads: list[bytes] = []
    if action is ProtectedAction.RUN:
        await _set_actor_role(database, ProtectedPrincipalRole.PROTECTED_RUNNER)
    runtime = None
    try:
        runtime = await create_protected_retrieval_service(_protected_config(database))
        with pytest.raises(ProtectedSecurityError, match="OPERATION_OUTCOME_UNKNOWN"):
            await runtime.execute(
                scenario.request,
                on_read=lambda payload: _capture_payload(callback_payloads, payload),
            )
        assert callback_payloads == []
    finally:
        if runtime is not None:
            await runtime.close()
        if action is ProtectedAction.RUN:
            await _set_actor_role(database, ProtectedPrincipalRole.HOLDOUT_AUTHOR)


async def _capture_payload(captured: list[bytes], payload: bytes) -> None:
    captured.append(payload)


async def _set_actor_role(database: _ProtectedDatabase, role: ProtectedPrincipalRole) -> None:
    engine = create_async_engine(database.url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    f'''
                    UPDATE "{database.schema}".protected_identity
                    SET principal_role = :role
                    WHERE database_login = :database_login
                    '''
                ),
                {"role": role.value, "database_login": database.actor_login},
            )
    finally:
        await engine.dispose()


def _protected_config(database: _ProtectedDatabase) -> Config:
    url = _login_url(database, database.actor_login)
    return Config(
        _env_file=None,
        ENV=DeploymentEnvironment.LOCAL,
        DB_HOST="127.0.0.1",
        DB_NAME="test",
        DB_USER="worker",
        DB_PASSWORD="synthetic-worker-password",
        CLOVA_OCR_INVOKE_URL="https://clova.test/ocr",
        CLOVA_OCR_SECRET="synthetic-clova-secret",
        STORAGE_DIR="/tmp/protected-retrieval-test",
        PROTECTED_RETRIEVAL_ENABLED=True,
        PROTECTED_DB_HOST=url.host,
        PROTECTED_DB_PORT=url.port,
        PROTECTED_DB_NAME=url.database,
        PROTECTED_DB_USER=database.actor_login,
        PROTECTED_DB_PASSWORD=database.password,
        PROTECTED_DB_CONTROL_USER=database.control_login,
        PROTECTED_DB_CONTROL_PASSWORD=database.password,
        PROTECTED_DB_SCHEMA=database.schema,
        PROTECTED_DB_ACCESS_ROLE=database.access,
        PROTECTED_DB_CONTROL_ROLE=database.control,
        PROTECTED_APPROVAL_REPOSITORY="AI-HealthCare-05/AH_05_04",
        PROTECTED_APPROVAL_BRANCH="develop",
        PROTECTED_APPROVAL_GITHUB_TOKEN="synthetic-github-token",
    )  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_runtime_persists_unknown_and_blocks_retry_after_callback_failure(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    scenario = _read_scenario()
    await _insert_read_scenario(database, scenario, scenario.payload)
    callback_payloads: list[bytes] = []

    async def fail_after_read(payload: bytes) -> None:
        callback_payloads.append(payload)
        raise RuntimeError("synthetic callback failure")

    runtime = await create_protected_retrieval_service(_protected_config(database))
    try:
        with pytest.raises(ProtectedSecurityError, match="OPERATION_OUTCOME_UNKNOWN"):
            await runtime.execute(scenario.request, on_read=fail_after_read)

        with pytest.raises(ProtectedSecurityError, match="RECONCILIATION_REQUIRED"):
            await runtime.execute(
                scenario.request,
                on_read=lambda payload: _capture_payload(callback_payloads, payload),
            )

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                outcomes = list(
                    await connection.scalars(
                        text(
                            f'''
                            SELECT entry_body->>'outcome'
                            FROM "{database.schema}".audit_entry
                            WHERE operation_key = :operation_key
                            ORDER BY sequence
                            '''
                        ),
                        {"operation_key": scenario.request.operation_key},
                    )
                )
                capability = (
                    await connection.execute(
                        text(
                            f'''
                            SELECT consumed_at IS NOT NULL, operated_at IS NOT NULL
                            FROM "{database.schema}".operation_capability
                            WHERE operation_key = :operation_key
                            '''
                        ),
                        {"operation_key": scenario.request.operation_key},
                    )
                ).one()
            assert outcomes == ["INTENT", "UNKNOWN", "DENIED"]
            assert capability == (True, False)
            assert callback_payloads == [scenario.payload]
        finally:
            await admin_engine.dispose()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_runtime_persists_unknown_and_blocks_retry_after_connection_loss(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    scenario = _read_scenario()
    await _insert_read_scenario(database, scenario, scenario.payload)
    callback_payloads: list[bytes] = []

    async def terminate_execution_connection(payload: bytes) -> None:
        callback_payloads.append(payload)
        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.begin() as connection:
                terminated = await connection.scalar(
                    text(
                        """
                        SELECT bool_or(pg_terminate_backend(pid))
                        FROM pg_stat_activity
                        WHERE usename = :database_login
                          AND application_name = 'protected-retrieval-data'
                          AND pid <> pg_backend_pid()
                        """
                    ),
                    {"database_login": database.actor_login},
                )
            assert terminated is True
        finally:
            await admin_engine.dispose()

    runtime = await create_protected_retrieval_service(_protected_config(database))
    try:
        with pytest.raises(ProtectedSecurityError, match="OPERATION_OUTCOME_UNKNOWN"):
            await runtime.execute(scenario.request, on_read=terminate_execution_connection)

        with pytest.raises(ProtectedSecurityError, match="RECONCILIATION_REQUIRED"):
            await runtime.execute(
                scenario.request,
                on_read=lambda payload: _capture_payload(callback_payloads, payload),
            )

        admin_engine = create_async_engine(database.url)
        try:
            async with admin_engine.connect() as connection:
                outcomes = list(
                    await connection.scalars(
                        text(
                            f'''
                            SELECT entry_body->>'outcome'
                            FROM "{database.schema}".audit_entry
                            WHERE operation_key = :operation_key
                            ORDER BY sequence
                            '''
                        ),
                        {"operation_key": scenario.request.operation_key},
                    )
                )
            assert outcomes == ["INTENT", "UNKNOWN", "DENIED"]
            assert callback_payloads == [scenario.payload]
        finally:
            await admin_engine.dispose()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_runtime_startup_rejects_data_login_with_control_membership(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    admin_engine = create_async_engine(database.url)
    try:
        async with admin_engine.begin() as connection:
            await connection.exec_driver_sql(f'GRANT "{database.control}" TO "{database.actor_login}"')
        with pytest.raises(ValueError, match="identity or schema boundary is unsafe"):
            await create_protected_retrieval_service(_protected_config(database))
    finally:
        async with admin_engine.begin() as connection:
            await connection.exec_driver_sql(f'REVOKE "{database.control}" FROM "{database.actor_login}"')
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_author_write_is_atomic_opaque_and_idempotent(
    protected_database: _ProtectedDatabase,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = protected_database
    payload = b"synthetic protected envelope"
    dataset = ProtectedDatasetBinding(
        dataset_id=f"synthetic-{uuid4()}",
        dataset_version="1.0.0",
        manifest_sha256="a" * 64,
        protected_artifact_sha256=sha256(payload).hexdigest(),
        hmac_key_version="synthetic-key-v1",
        state=ProtectedDatasetState.AUTHORING,
        state_revision=1,
        authored_count=0,
        review_complete=False,
    )
    principal = ProtectedPrincipal(
        actor=ActorIdentity(actor_id="synthetic-author", namespace="SERVICE_IDENTITY"),
        role=ProtectedPrincipalRole.HOLDOUT_AUTHOR,
    )
    target_ref = OpaqueLogicalRef(
        namespace=OpaqueRefNamespace.HOLDOUT_SET,
        value=str(uuid4()),
    )
    request = ProtectedOperationRequest(
        request_id=str(uuid4()),
        operation_key=f"synthetic-write-{uuid4()}",
        dataset=dataset,
        target_ref=target_ref,
        action=ProtectedAction.WRITE,
        principal=principal,
    )
    now = datetime.now(UTC)
    grant = ProtectedAuthorizationGrant(
        grant_id=str(uuid4()),
        revision=1,
        subject=principal,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        manifest_sha256=dataset.manifest_sha256,
        protected_artifact_sha256=dataset.protected_artifact_sha256,
        hmac_key_version=dataset.hmac_key_version,
        actions=(ProtectedAction.WRITE,),
        issuer=ProtectedApprovalPrincipal(
            actor=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
            role=ProtectedApprovalRole.DATASET_CUSTODIAN,
        ),
        control_implementation=ControlImplementationBinding(
            commit_oid="c" * 40,
            artifact_sha256="d" * 64,
            participants=(ActorIdentity(actor_id="synthetic-implementer", namespace="GITHUB_LOGIN"),),
        ),
        approval_source_event_id=f"synthetic-approval-{uuid4()}",
        approval_source_raw_sha256="e" * 64,
        valid_from=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=10),
    )

    admin_engine = create_async_engine(database.url)
    actor_engine = create_async_engine(_login_url(database, database.actor_login))
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    f"""
                    INSERT INTO "{database.schema}".protected_dataset (
                        dataset_id, dataset_version, binding, manifest_sha256,
                        protected_artifact_sha256, hmac_key_version, state,
                        state_revision, authored_count, review_complete
                    ) VALUES (
                        :dataset_id, :dataset_version, CAST(:binding AS jsonb), :manifest_sha256,
                        :artifact_sha256, :key_version, :state, :state_revision,
                        :authored_count, :review_complete
                    )
                    """
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
            await connection.execute(
                text(
                    f"""
                    INSERT INTO "{database.schema}".authorization_grant (
                        grant_id, revision, effective_revision, grant_body,
                        subject_actor_id, subject_namespace, subject_role,
                        dataset_id, dataset_version, manifest_sha256,
                        protected_artifact_sha256, hmac_key_version, actions,
                        valid_from, expires_at
                    ) VALUES (
                        CAST(:grant_id AS uuid), :revision, :revision, CAST(:grant_body AS jsonb),
                        :actor_id, :actor_namespace, :subject_role,
                        :dataset_id, :dataset_version, :manifest_sha256,
                        :artifact_sha256, :key_version, :actions,
                        :valid_from, :expires_at
                    )
                    """
                ),
                {
                    "grant_id": grant.grant_id,
                    "revision": grant.revision,
                    "grant_body": grant.model_dump_json(),
                    "actor_id": principal.actor.actor_id,
                    "actor_namespace": principal.actor.namespace,
                    "subject_role": principal.role.value,
                    "dataset_id": dataset.dataset_id,
                    "dataset_version": dataset.dataset_version,
                    "manifest_sha256": dataset.manifest_sha256,
                    "artifact_sha256": dataset.protected_artifact_sha256,
                    "key_version": dataset.hmac_key_version,
                    "actions": [ProtectedAction.WRITE.value],
                    "valid_from": grant.valid_from,
                    "expires_at": grant.expires_at,
                },
            )

        session_factory = async_sessionmaker(actor_engine, expire_on_commit=False)
        async with session_factory() as diagnostic_session:
            diagnostic_transaction = await diagnostic_session.begin()
            diagnostic_clock = await PostgresqlTrustedClock.from_session(diagnostic_session)
            diagnostic_ledger = PostgresqlAuthorizationLedger(diagnostic_session, database.schema)
            diagnostic_journal = PostgresqlProtectedAuditJournal(diagnostic_session, database.schema, diagnostic_clock)
            assert await diagnostic_journal.operation_history(request) == ()
            assert await diagnostic_ledger.find_for(request) == grant
            assert await diagnostic_ledger.require_dataset(request) == dataset
            async with PostgresqlAuthorizationGuard(diagnostic_session, database.schema).hold(
                request, grant
            ) as diagnostic_guard:
                with pytest.raises(ProtectedSecurityError, match="AUDIT_TRANSITION_INVALID"):
                    await diagnostic_journal.append_operation(
                        request,
                        grant,
                        OperationAuditOutcome.SUCCEEDED,
                        ProtectedAuditReason.COMPLETED,
                        result=ProtectedOperationResult(
                            result_ref=OpaqueLogicalRef(
                                namespace=OpaqueRefNamespace.RUN_RESULT,
                                value=str(uuid4()),
                            ),
                            reason_code="PROTECTED_OPERATION_SUCCEEDED",
                        ),
                    )
                intent = await diagnostic_journal.append_operation(
                    request,
                    grant,
                    OperationAuditOutcome.INTENT,
                    ProtectedAuditReason.AUTHORIZED,
                )
                assert intent.outcome is OperationAuditOutcome.INTENT
                capability = await diagnostic_guard.issue_capability(request, grant)
                forged_capability = capability.model_copy(update={"request_id": str(uuid4())})
                with pytest.raises(ProtectedSecurityError, match="CAPABILITY_BINDING_MISMATCH"):
                    await diagnostic_guard.consume(forged_capability)
                await diagnostic_guard.consume(capability)
                forged_target_capability = capability.model_copy(
                    update={
                        "target_ref": OpaqueLogicalRef(
                            namespace=OpaqueRefNamespace.HOLDOUT_SET,
                            value=str(uuid4()),
                        )
                    }
                )
                with pytest.raises(ProtectedSecurityError, match="CAPABILITY_BINDING_MISMATCH"):
                    await PostgresqlProtectedArtifactOperation(
                        diagnostic_session,
                        database.schema,
                        write_payload=payload,
                    ).execute(request, forged_target_capability)
                diagnostic_result = await PostgresqlProtectedArtifactOperation(
                    diagnostic_session,
                    database.schema,
                    write_payload=payload,
                ).execute(request, capability)
                with pytest.raises(ProtectedSecurityError, match="AUDIT_BINDING_MISMATCH"):
                    await diagnostic_journal.append_operation(
                        request.model_copy(update={"request_id": str(uuid4())}),
                        grant,
                        OperationAuditOutcome.SUCCEEDED,
                        ProtectedAuditReason.COMPLETED,
                        capability,
                        diagnostic_result,
                    )
                terminal = await diagnostic_journal.append_operation(
                    request,
                    grant,
                    OperationAuditOutcome.SUCCEEDED,
                    ProtectedAuditReason.COMPLETED,
                    capability,
                    diagnostic_result,
                )
                assert terminal.outcome is OperationAuditOutcome.SUCCEEDED
                with pytest.raises(ProtectedSecurityError, match="CAPABILITY_ALREADY_CONSUMED"):
                    await diagnostic_guard.consume(capability)
            await diagnostic_transaction.rollback()

        async with session_factory() as session, session.begin():
            clock = await PostgresqlTrustedClock.from_session(session)
            ledger = PostgresqlAuthorizationLedger(session, database.schema)
            journal = PostgresqlProtectedAuditJournal(session, database.schema, clock)
            result = await execute_protected_operation(
                request,
                ledger=ledger,
                guard=PostgresqlAuthorizationGuard(session, database.schema),
                journal=journal,
                operation=PostgresqlProtectedArtifactOperation(
                    session,
                    database.schema,
                    write_payload=payload,
                ),
                clock=clock,
            )

        async with session_factory() as session, session.begin():
            replay_clock = await PostgresqlTrustedClock.from_session(session)
            replay = await execute_protected_operation(
                request,
                ledger=PostgresqlAuthorizationLedger(session, database.schema),
                guard=PostgresqlAuthorizationGuard(session, database.schema),
                journal=PostgresqlProtectedAuditJournal(session, database.schema, replay_clock),
                operation=PostgresqlProtectedArtifactOperation(
                    session,
                    database.schema,
                    write_payload=b"must-not-replace-the-envelope",
                ),
                clock=replay_clock,
            )

        assert replay == result
        assert payload.decode() not in repr(result)
        assert payload.decode() not in caplog.text

        mismatched = request.model_copy(
            update={
                "request_id": str(uuid4()),
                "operation_key": f"synthetic-mismatch-{uuid4()}",
                "principal": principal.model_copy(
                    update={"actor": ActorIdentity(actor_id="claimed-other", namespace="SERVICE_IDENTITY")}
                ),
            }
        )
        async with session_factory() as session, session.begin():
            mismatch_clock = await PostgresqlTrustedClock.from_session(session)
            with pytest.raises(ProtectedSecurityError, match="GUARD_BINDING_MISMATCH"):
                await execute_protected_operation(
                    mismatched,
                    ledger=PostgresqlAuthorizationLedger(session, database.schema),
                    guard=PostgresqlAuthorizationGuard(session, database.schema),
                    journal=PostgresqlProtectedAuditJournal(session, database.schema, mismatch_clock),
                    operation=PostgresqlProtectedArtifactOperation(session, database.schema),
                    clock=mismatch_clock,
                )

        async with admin_engine.connect() as connection:
            stored = await connection.execute(
                text(
                    f"""
                    SELECT artifact.envelope,
                           (SELECT array_agg(entry.entry_body->>'outcome' ORDER BY entry.sequence)
                            FROM "{database.schema}".audit_entry AS entry
                            WHERE entry.operation_key = :operation_key),
                           (SELECT count(*) FROM "{database.schema}".operation_capability
                            WHERE operation_key = :operation_key)
                    FROM "{database.schema}".protected_artifact AS artifact
                    WHERE artifact.target_ref = CAST(:target_ref AS uuid)
                    """
                ),
                {"operation_key": request.operation_key, "target_ref": target_ref.value},
            )
            envelope, outcomes, capability_count = stored.one()
            assert envelope == payload
            assert outcomes == ["INTENT", "SUCCEEDED"]
            assert capability_count == 1

        async with admin_engine.begin() as connection:
            await connection.execute(
                text(f'UPDATE "{database.schema}".audit_head SET sequence = 0, entry_sha256 = NULL WHERE singleton')
            )
        async with session_factory() as session, session.begin():
            tamper_clock = await PostgresqlTrustedClock.from_session(session)
            tamper_journal = PostgresqlProtectedAuditJournal(session, database.schema, tamper_clock)
            with pytest.raises(ProtectedSecurityError, match="AUDIT_TAIL_TRUNCATED"):
                await tamper_journal.operation_history(request)
    finally:
        await actor_engine.dispose()
        await admin_engine.dispose()


def _dataset(**updates: object) -> ProtectedDatasetBinding:
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
        "leakage_axis_intersections": None,
        "freeze_receipt_ref": None,
        "execution_authorization_ref": None,
        "retriever_binding_ref": None,
    }
    base.update(updates)
    return ProtectedDatasetBinding.model_validate(base)


async def _insert_dataset(database: _ProtectedDatabase, dataset: ProtectedDatasetBinding) -> None:
    admin_engine = create_async_engine(database.url)
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    f"""
                    INSERT INTO "{database.schema}".protected_dataset (
                        dataset_id, dataset_version, binding, manifest_sha256,
                        protected_artifact_sha256, hmac_key_version, state,
                        state_revision, authored_count, review_complete
                    ) VALUES (
                        :dataset_id, :dataset_version, CAST(:binding AS jsonb), :manifest_sha256,
                        :artifact_sha256, :key_version, :state, :state_revision,
                        :authored_count, :review_complete
                    )
                    """
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
        await admin_engine.dispose()


async def _owner_update_lifecycle(
    database: _ProtectedDatabase,
    dataset: ProtectedDatasetBinding,
    *,
    state: ProtectedDatasetState,
    state_revision: int,
    authored_count: int,
    review_complete: bool,
) -> None:
    admin_engine = create_async_engine(database.url)
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    f"""
                    UPDATE "{database.schema}".protected_dataset
                    SET state = :state, state_revision = :state_revision,
                        authored_count = :authored_count, review_complete = :review_complete
                    WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version
                    """
                ),
                {
                    "dataset_id": dataset.dataset_id,
                    "dataset_version": dataset.dataset_version,
                    "state": state.value,
                    "state_revision": state_revision,
                    "authored_count": authored_count,
                    "review_complete": review_complete,
                },
            )
    finally:
        await admin_engine.dispose()


async def _append_freeze_audit_fixture(
    database: _ProtectedDatabase,
    dataset: ProtectedDatasetBinding,
    *,
    event_id: str,
    revision: int,
    custom_target_id: str | None = None,
    custom_reason: ProtectedAuditReason | None = None,
    custom_command_kind: str | None = None,
    tamper_entry_hash: bool = False,
    tamper_previous_hash: bool = False,
) -> None:
    admin_engine = create_async_engine(database.url)
    try:
        async with admin_engine.begin() as connection:
            head_row = (
                await connection.execute(
                    text(f'SELECT sequence, entry_sha256 FROM "{database.schema}".audit_head WHERE singleton')
                )
            ).one()
            current_sequence, previous_entry_sha256 = head_row
            next_sequence = current_sequence + 1
            now = datetime.now(UTC)
            prev_hash = "f" * 64 if tamper_previous_hash else previous_entry_sha256
            entry = ControlCommandAuditEntry(
                event_kind=ProtectedAuditEventKind.CONTROL,
                sequence=next_sequence,
                event_id=event_id,
                command_kind=cast(Any, custom_command_kind or "FREEZE_DATASET"),
                executed_by=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
                target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
                target_id=custom_target_id
                if custom_target_id is not None
                else f"{dataset.dataset_id}:{dataset.dataset_version}",
                command_sha256="0" * 64,
                outcome=ControlAuditOutcome.SUCCEEDED,
                result_effective_revision=revision,
                authorization_audit_event_id=None,
                reason_code=custom_reason or ProtectedAuditReason.DATASET_FROZEN,
                recorded_at=now,
                previous_entry_sha256=prev_hash,
                entry_sha256="0" * 64,
            )
            computed_hash = audit_entry_sha256(entry)
            entry = entry.model_copy(update={"entry_sha256": "e" * 64 if tamper_entry_hash else computed_hash})
            await connection.execute(
                text(
                    f"""
                    INSERT INTO "{database.schema}".audit_entry (
                        sequence, event_id, event_kind, entry_body,
                        previous_entry_sha256, entry_sha256, recorded_at, control_entry
                    ) VALUES (
                        :sequence, CAST(:event_id AS uuid), 'CONTROL', CAST(:entry_body AS jsonb),
                        :previous_entry_sha256, :entry_sha256, :recorded_at, true
                    )
                    """
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
                    f"""
                    UPDATE "{database.schema}".audit_head
                    SET sequence = :sequence, entry_sha256 = :entry_sha256
                    WHERE singleton
                    """
                ),
                {
                    "sequence": entry.sequence,
                    "entry_sha256": entry.entry_sha256,
                },
            )
    finally:
        await admin_engine.dispose()


async def _load_dataset_through_data_adapter(
    database: _ProtectedDatabase,
    dataset: ProtectedDatasetBinding,
) -> ProtectedDatasetBinding:
    actor_engine = create_async_engine(_login_url(database, database.actor_login))
    session_factory = async_sessionmaker(actor_engine, expire_on_commit=False)
    try:
        async with session_factory() as session, session.begin():
            ledger = PostgresqlAuthorizationLedger(session, database.schema)
            request = ProtectedOperationRequest(
                request_id=str(uuid4()),
                operation_key=f"synthetic-read-{uuid4()}",
                dataset=dataset,
                target_ref=OpaqueLogicalRef(namespace=OpaqueRefNamespace.HOLDOUT_SET, value=str(uuid4())),
                action=ProtectedAction.READ,
                principal=ProtectedPrincipal(
                    actor=ActorIdentity(actor_id="synthetic-author", namespace="SERVICE_IDENTITY"),
                    role=ProtectedPrincipalRole.HOLDOUT_AUTHOR,
                ),
            )
            return await ledger.require_dataset(request)
    finally:
        await actor_engine.dispose()


@pytest.mark.asyncio
async def test_dataset_read_overlays_lifecycle_columns_without_updating_binding(
    protected_database: _ProtectedDatabase,
) -> None:
    stored = _dataset(state=ProtectedDatasetState.ACCESS_AUTHORIZED, state_revision=1)
    await _insert_dataset(protected_database, stored)
    await _owner_update_lifecycle(
        protected_database,
        stored,
        state=ProtectedDatasetState.AUTHORING,
        state_revision=2,
        authored_count=17,
        review_complete=False,
    )
    loaded = await _load_dataset_through_data_adapter(protected_database, stored)
    assert loaded.state is ProtectedDatasetState.AUTHORING
    assert loaded.state_revision == 2
    assert loaded.authored_count == 17
    assert loaded.review_complete is False
    assert loaded.freeze_receipt_ref is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column", "forged_value"),
    [
        ("manifest_sha256", "c" * 64),
        ("protected_artifact_sha256", "d" * 64),
        ("hmac_key_version", "tampered-key-v2"),
    ],
)
async def test_dataset_read_rejects_column_binding_mismatches(
    protected_database: _ProtectedDatabase,
    column: str,
    forged_value: str,
) -> None:
    stored = _dataset()
    await _insert_dataset(protected_database, stored)
    admin_engine = create_async_engine(protected_database.url)
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    f"""
                    UPDATE "{protected_database.schema}".protected_dataset
                    SET {column} = :val
                    WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version
                    """
                ),
                {
                    "val": forged_value,
                    "dataset_id": stored.dataset_id,
                    "dataset_version": stored.dataset_version,
                },
            )
    finally:
        await admin_engine.dispose()

    with pytest.raises(ProtectedSecurityError, match="DATASET_BINDING_MISMATCH"):
        await _load_dataset_through_data_adapter(protected_database, stored)


async def _reset_audit_journal(database: _ProtectedDatabase) -> None:
    admin_engine = create_async_engine(database.url)
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DELETE FROM "{database.schema}".audit_entry'))
            await connection.execute(
                text(f'UPDATE "{database.schema}".audit_head SET sequence = 0, entry_sha256 = NULL WHERE singleton')
            )
    finally:
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_frozen_dataset_requires_verified_freeze_audit_receipt(
    protected_database: _ProtectedDatabase,
) -> None:
    await _reset_audit_journal(protected_database)
    stored = _dataset(
        state=ProtectedDatasetState.FROZEN,
        state_revision=2,
        authored_count=40,
        review_complete=True,
    )
    await _insert_dataset(protected_database, stored)

    # Missing freeze audit -> AUDIT_BINDING_MISMATCH
    with pytest.raises(ProtectedSecurityError, match="AUDIT_BINDING_MISMATCH"):
        await _load_dataset_through_data_adapter(protected_database, stored)

    # Valid freeze audit appended -> succeeds and sets freeze_receipt_ref
    event_id = str(uuid4())
    await _append_freeze_audit_fixture(
        protected_database,
        stored,
        event_id=event_id,
        revision=2,
    )
    loaded = await _load_dataset_through_data_adapter(protected_database, stored)
    assert loaded.state is ProtectedDatasetState.FROZEN
    assert loaded.state_revision == 2
    assert loaded.authored_count == 40
    assert loaded.review_complete is True
    assert loaded.freeze_receipt_ref is not None
    assert loaded.freeze_receipt_ref.namespace is OpaqueRefNamespace.AUDIT_EVENT
    assert loaded.freeze_receipt_ref.value == event_id


@pytest.mark.asyncio
async def test_frozen_dataset_rejects_tampered_or_mismatched_freeze_audit(
    protected_database: _ProtectedDatabase,
) -> None:
    # 1. Mismatched revision
    await _reset_audit_journal(protected_database)
    rev_stored = _dataset(state=ProtectedDatasetState.FROZEN, state_revision=3, authored_count=40, review_complete=True)
    await _insert_dataset(protected_database, rev_stored)
    await _append_freeze_audit_fixture(
        protected_database,
        rev_stored,
        event_id=str(uuid4()),
        revision=2,  # mismatched revision
    )
    with pytest.raises(ProtectedSecurityError, match="AUDIT_BINDING_MISMATCH"):
        await _load_dataset_through_data_adapter(protected_database, rev_stored)

    # 2. Tampered entry hash
    await _reset_audit_journal(protected_database)
    hash_stored = _dataset(
        state=ProtectedDatasetState.FROZEN, state_revision=2, authored_count=40, review_complete=True
    )
    await _insert_dataset(protected_database, hash_stored)
    await _append_freeze_audit_fixture(
        protected_database,
        hash_stored,
        event_id=str(uuid4()),
        revision=2,
        tamper_entry_hash=True,
    )
    with pytest.raises(ProtectedSecurityError, match="AUDIT_HASH_MISMATCH"):
        await _load_dataset_through_data_adapter(protected_database, hash_stored)
