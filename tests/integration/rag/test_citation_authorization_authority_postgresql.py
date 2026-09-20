from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_citation_authorization_authority import (
    SqlAlchemyCitationAuthorizationAuthorityStore,
    SqlAlchemyCitationEligibilityReader,
)
from ai_worker.tasks.rag.citation_authorization import (
    AuthorizationReason,
    AuthorizationVerificationDecision,
    verify_citation_authorization_receipt,
)
from ai_worker.tasks.rag.citation_authorization_authority import (
    CitationAuthorityAggregate,
    CitationAuthorizationAuthorityError,
)
from ai_worker.tests.rag.test_citation_authorization_authority_issuer import (
    APPROVAL_ID,
    MEMBER_ID,
    NOW,
    SNAPSHOT_ID,
    _ApprovalReader,
    _AuthorityReader,
    _EligibilityReader,
    _endpoint_selection,
    _guard_observation,
    _GuardReader,
    _PinReader,
    _request,
    _Store,
)
from app.core import config
from app.models.rag_citation_authorization import (
    RagCitationAuthorizationMemberDecision,
    RagCitationAuthorizationReceipt,
    RagCitationAuthorizationReceiptSelection,
    RagCitationAuthorizationSourceDecision,
)
from app.models.rag_request_authority import RagRequestGuardAuthority
from app.models.rag_request_guard_runtime_binding import RagRequestGuardRuntimeBinding
from app.models.rag_runtime import RagRuntimeBundleStatus, RagRuntimeExecutionManifest, RagRuntimeReleaseBundle
from app.models.rag_source import (
    RagSnapshotVerificationStatus,
    RagSource,
    RagSourceApprovalStatus,
    RagSourceEndpoint,
    RagSourceEndpointLifecycleStatus,
    RagSourceLifecycleStatus,
    RagSourceOperation,
    RagSourceSnapshot,
    RagSourceSnapshotMember,
    RagSourceSnapshotMemberKind,
    RagSourceUsageStatus,
)
from app.models.rag_source_use_approval import RagSourceUseApproval
from app.models.users import User
from rag_runtime.request_authority import compute_request_guard_authority_ref
from rag_runtime.request_guard_runtime_binding import compute_request_guard_runtime_binding_ref
from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import SourceUseApprovalIdentity, SourceUseApprovalObservation, SourceUsePurpose

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio


def _alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "backend/alembic"))
    return cfg


@pytest_asyncio.fixture
async def database(monkeypatch: pytest.MonkeyPatch) -> AsyncEngine:
    name = "citation_authority_869_" + uuid4().hex
    original = config.database_url
    cluster = create_async_engine(original, isolation_level="AUTOCOMMIT", hide_parameters=True)
    engine = create_async_engine(make_url(original).set(database=name), hide_parameters=True)
    try:
        async with cluster.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        monkeypatch.setattr(config, "DB_NAME", name)
        await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
        yield engine
    finally:
        await engine.dispose()
        async with cluster.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await cluster.dispose()


async def _aggregate() -> CitationAuthorityAggregate:
    selection = _endpoint_selection()
    guard = _guard_observation()
    store = _Store()
    await issue(
        selection=selection,
        guard=guard,
        store=store,
        eligibility_reader=_endpoint_eligibility(),
    )
    assert store.aggregate is not None
    return store.aggregate


def _endpoint_eligibility(*, runtime_status: str = "ENABLED"):
    endpoint_id = UUID("90000000-0000-4000-8000-000000000001")
    operation_id = UUID("90000000-0000-4000-8000-000000000002")
    from ai_worker.tasks.rag.citation_authorization_authority import CitationMemberEligibilityObservation

    return _EligibilityReader(
        member=CitationMemberEligibilityObservation(
            source_snapshot_id=SNAPSHOT_ID,
            source_snapshot_member_id=MEMBER_ID,
            member_kind="ENDPOINT_OPERATION",
            endpoint_id=endpoint_id,
            operation_id=operation_id,
            ingestion_artifact_id=None,
            endpoint_code="endpoint",
            operation_code="operation",
            endpoint_lifecycle_status="VERIFIED",
            endpoint_runtime_status=runtime_status,
            endpoint_acquisition_status="APPROVED",
            operation_runtime_status="ENABLED",
            operation_acquisition_status="APPROVED",
            artifact_fk_exists=False,
            snapshot_endpoint_id=endpoint_id,
            snapshot_operation_id=operation_id,
        )
    )


async def issue(*, selection, guard, store, approval_reader=None, eligibility_reader=None, pin_reader=None):
    from ai_worker.tasks.rag.citation_authorization_authority import issue_citation_authorization

    return await issue_citation_authorization(
        validated_selection=selection,
        request=_request(selection, guard),
        evaluation_time=NOW,
        guard_reader=_GuardReader(guard),
        request_authority_reader=_AuthorityReader(selection),
        pin_reader=pin_reader or _PinReader(),
        approval_reader=approval_reader or _ApprovalReader(),
        eligibility_reader=eligibility_reader or _endpoint_eligibility(),
        store=store,
    )


async def _seed_dependencies(session: AsyncSession) -> None:
    guard = _guard_observation()
    legacy = compute_request_guard_authority_ref(
        user_id=guard.user_id,
        request_operation_code=guard.request_operation_code,
        decision_stage=guard.decision_stage,
    )
    guard_ref = compute_request_guard_runtime_binding_ref(guard)
    endpoint_id = UUID("90000000-0000-4000-8000-000000000001")
    operation_id = UUID("90000000-0000-4000-8000-000000000002")
    source_id = UUID("90000000-0000-4000-8000-000000000003")
    manifest_id = UUID("90000000-0000-4000-8000-000000000004")
    session.add(
        User(id=guard.user_id, email=f"869-{uuid4().hex[:8]}@example.com", hashed_password="x" * 60, name="869")
    )
    await session.flush()
    session.add(
        RagRuntimeExecutionManifest(
            id=manifest_id,
            manifest_key="869",
            manifest_version="1",
            manifest_hash="1" * 64,
            schema_version="1",
            git_commit_sha="8690000",
        )
    )
    session.add(
        RagSource(
            id=source_id,
            source_code="knowledge-source",
            display_name="Knowledge",
            lifecycle_status=RagSourceLifecycleStatus.ACTIVE,
        )
    )
    await session.flush()
    session.add(
        RagRuntimeReleaseBundle(
            id=guard.bundle_id,
            bundle_key="869",
            bundle_version="1",
            bundle_status=RagRuntimeBundleStatus.BUILDING,
            execution_manifest_id=manifest_id,
            bundle_manifest_hash=guard.bundle_manifest_hash,
            environment_code=guard.environment.value,
            catalog_version="1",
            catalog_manifest_hash="2" * 64,
        )
    )
    session.add(
        RagRequestGuardAuthority(
            id=uuid4(),
            artifact_code=legacy.artifact_code,
            artifact_version=legacy.version,
            artifact_content_sha256=legacy.content_sha256,
            user_id=guard.user_id,
            request_operation_code=guard.request_operation_code,
            decision_stage=guard.decision_stage.value,
        )
    )
    session.add(
        RagSourceEndpoint(
            id=endpoint_id,
            source_id=source_id,
            endpoint_code="endpoint",
            display_name="Endpoint",
            lifecycle_status=RagSourceEndpointLifecycleStatus.VERIFIED,
            runtime_status=RagSourceUsageStatus.ENABLED,
            acquisition_status=RagSourceApprovalStatus.APPROVED,
        )
    )
    await session.flush()
    session.add(
        RagSourceOperation(
            id=operation_id,
            endpoint_id=endpoint_id,
            operation_code="operation",
            display_name="Operation",
            runtime_status=RagSourceUsageStatus.ENABLED,
            acquisition_status=RagSourceApprovalStatus.APPROVED,
        )
    )
    await session.flush()
    session.add(
        RagSourceSnapshot(
            id=SNAPSHOT_ID,
            operation_id=operation_id,
            source_version="2026-09-01",
            raw_manifest_checksum="3" * 64,
            canonical_checksum="4" * 64,
            schema_version="1",
            parser_version="1",
            normalization_version="1",
            canonicalization_spec_version="1",
            record_count=1,
            rejected_record_count=0,
            verification_status=RagSnapshotVerificationStatus.PENDING,
            collected_at=NOW,
        )
    )
    await session.flush()
    session.add(
        RagSourceSnapshotMember(
            id=MEMBER_ID,
            source_snapshot_id=SNAPSHOT_ID,
            member_kind=RagSourceSnapshotMemberKind.ENDPOINT_OPERATION,
            endpoint_id=endpoint_id,
            operation_id=operation_id,
            locator="member",
            content_sha256="5" * 64,
        )
    )
    session.add(
        RagSourceUseApproval(
            id=APPROVAL_ID,
            source_snapshot_id=SNAPSHOT_ID,
            source_code="knowledge-source",
            source_version="2026-09-01",
            environment=guard.environment.value,
            purpose="PATIENT_CITATION",
            approval_version="approval-v1",
            valid_from=NOW - timedelta(days=1),
            expires_at=NOW + timedelta(days=1),
            actor_id=guard.user_id,
            evidence_ref="evidence://869",
        )
    )
    session.add(
        RagRequestGuardRuntimeBinding(
            request_guard_decision_id=guard.request_guard_decision_id,
            artifact_code=guard_ref.artifact_code,
            artifact_version=guard_ref.version,
            artifact_content_sha256=guard_ref.content_sha256,
            actual_decision_outcome=guard.actual_decision_outcome.value,
            user_id=guard.user_id,
            request_operation_code=guard.request_operation_code,
            decision_stage=guard.decision_stage.value,
            environment_code=guard.environment.value,
            bundle_id=guard.bundle_id,
            bundle_manifest_hash=guard.bundle_manifest_hash,
            request_scope_codes=list(guard.request_scope_codes),
            scope_manifest_hash=guard.scope_manifest_hash,
            legacy_request_guard_artifact_code=legacy.artifact_code,
            legacy_request_guard_artifact_version=legacy.version,
            legacy_request_guard_content_sha256=legacy.content_sha256,
        )
    )
    await session.flush()


async def test_complete_aggregate_roundtrip_and_historical_replay(database: AsyncEngine) -> None:
    aggregate = await _aggregate()
    sessions = async_sessionmaker(database, expire_on_commit=False)
    async with sessions.begin() as session:
        await _seed_dependencies(session)
        store = SqlAlchemyCitationAuthorizationAuthorityStore(session)
        await store.append(aggregate)
        observed = await store.read_complete(request_sha256=aggregate.receipt.request_sha256)
        assert observed == aggregate

    async with sessions.begin() as session:
        store = SqlAlchemyCitationAuthorizationAuthorityStore(session)
        outcome = await issue(
            selection=_endpoint_selection(),
            guard=_guard_observation(),
            store=store,
            eligibility_reader=SqlAlchemyCitationEligibilityReader(session),
        )
        assert outcome.receipt == aggregate.receipt


async def test_caller_rollback_removes_all_four_authority_tables(database: AsyncEngine) -> None:
    aggregate = await _aggregate()
    invalid = replace(
        aggregate,
        member_decisions=(replace(aggregate.member_decisions[0], source_snapshot_member_id=uuid4()),),
    )
    sessions = async_sessionmaker(database, expire_on_commit=False)
    async with sessions() as session:
        transaction = await session.begin()
        await _seed_dependencies(session)
        with pytest.raises(CitationAuthorizationAuthorityError):
            await SqlAlchemyCitationAuthorizationAuthorityStore(session).append(invalid)
        await transaction.rollback()

    async with sessions() as session:
        counts = [
            await session.scalar(select(func.count()).select_from(model))
            for model in (
                RagCitationAuthorizationSourceDecision,
                RagCitationAuthorizationMemberDecision,
                RagCitationAuthorizationReceipt,
                RagCitationAuthorizationReceiptSelection,
            )
        ]
    assert counts == [0, 0, 0, 0]


@pytest.mark.parametrize("scenario", ("full_pass", "approval_expired", "member_disabled", "pin_absent"))
async def test_acceptance_scenarios_on_migrated_postgresql(database: AsyncEngine, scenario: str) -> None:
    sessions = async_sessionmaker(database, expire_on_commit=False)
    async with sessions.begin() as session:
        await _seed_dependencies(session)

    selection = _endpoint_selection()
    guard = _guard_observation()
    request = _request(selection, guard)
    approval_reader = None
    eligibility_reader = _endpoint_eligibility(
        runtime_status="DISABLED" if scenario == "member_disabled" else "ENABLED"
    )
    pin_reader = _PinReader(present=scenario != "pin_absent")
    if scenario == "approval_expired":
        approval_reader = _ApprovalReader(
            SourceUseApprovalObservation(
                id=APPROVAL_ID,
                identity=SourceUseApprovalIdentity(
                    source_snapshot_id=SNAPSHOT_ID,
                    source_code="knowledge-source",
                    source_version="2026-09-01",
                    environment=RuntimeEnvironmentCode.TEST,
                    purpose=SourceUsePurpose.PATIENT_CITATION,
                    approval_version="approval-v1",
                ),
                valid_from=NOW - timedelta(days=2),
                expires_at=NOW - timedelta(days=1),
                revoked_at=None,
                revoked_by=None,
                revoked_reason=None,
                actor_id=guard.user_id,
                evidence_ref="evidence://869-expired",
            )
        )

    async with sessions.begin() as session:
        outcome = await issue(
            selection=selection,
            guard=guard,
            store=SqlAlchemyCitationAuthorizationAuthorityStore(session),
            approval_reader=approval_reader,
            eligibility_reader=eligibility_reader,
            pin_reader=pin_reader,
        )

    async with sessions() as session:
        counts = [
            await session.scalar(select(func.count()).select_from(model))
            for model in (
                RagCitationAuthorizationSourceDecision,
                RagCitationAuthorizationMemberDecision,
                RagCitationAuthorizationReceipt,
                RagCitationAuthorizationReceiptSelection,
            )
        ]

    if scenario == "pin_absent":
        assert outcome.receipt is None
        assert counts == [0, 0, 0, 0]
        return

    assert outcome.receipt is not None
    verification = verify_citation_authorization_receipt(request, outcome.receipt)
    if scenario == "full_pass":
        assert verification.decision is AuthorizationVerificationDecision.AUTHORIZED
        assert verification.reasons == ()
    else:
        assert verification.decision is AuthorizationVerificationDecision.REJECTED
        assert verification.reasons == (AuthorizationReason.SELECTION_NOT_AUTHORIZED,)
    assert counts == [1, 1, 1, 1]
