"""PostgreSQL protected-retrieval adapter integration tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
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
    ControlImplementationBinding,
    OpaqueLogicalRef,
    OpaqueRefNamespace,
    OperationAuditOutcome,
    ProtectedAction,
    ProtectedApprovalPrincipal,
    ProtectedApprovalRole,
    ProtectedAuditReason,
    ProtectedAuthorizationGrant,
    ProtectedDatasetBinding,
    ProtectedDatasetState,
    ProtectedOperationRequest,
    ProtectedOperationResult,
    ProtectedPrincipal,
    ProtectedPrincipalRole,
    ProtectedSecurityError,
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
    dataset = ProtectedDatasetBinding(
        dataset_id=f"synthetic-{uuid4()}",
        dataset_version="1.0.0",
        manifest_sha256="a" * 64,
        protected_artifact_sha256=sha256(payload).hexdigest(),
        hmac_key_version="synthetic-key-v1",
        state=ProtectedDatasetState.FROZEN if is_run else ProtectedDatasetState.AUTHORING,
        state_revision=1,
        authored_count=40 if is_run else 0,
        review_complete=is_run,
        leakage_axis_intersections=(0, 0, 0, 0) if is_run else None,
        freeze_receipt_ref=runner_ref if is_run else None,
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
