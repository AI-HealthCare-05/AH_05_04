"""PostgreSQL protected-retrieval adapter integration tests."""

from __future__ import annotations

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
    ProtectedPrincipal,
    ProtectedPrincipalRole,
    execute_protected_operation,
)
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
                intent = await diagnostic_journal.append_operation(
                    request,
                    grant,
                    OperationAuditOutcome.INTENT,
                    ProtectedAuditReason.AUTHORIZED,
                )
                assert intent.outcome is OperationAuditOutcome.INTENT
                capability = await diagnostic_guard.issue_capability(request, grant)
                await diagnostic_guard.consume(capability)
                diagnostic_result = await PostgresqlProtectedArtifactOperation(
                    diagnostic_session,
                    database.schema,
                    write_payload=payload,
                ).execute(request, capability)
                terminal = await diagnostic_journal.append_operation(
                    request,
                    grant,
                    OperationAuditOutcome.SUCCEEDED,
                    ProtectedAuditReason.COMPLETED,
                    capability,
                    diagnostic_result,
                )
                assert terminal.outcome is OperationAuditOutcome.SUCCEEDED
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
    finally:
        await actor_engine.dispose()
        await admin_engine.dispose()
