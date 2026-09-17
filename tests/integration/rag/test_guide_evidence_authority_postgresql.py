"""Real PostgreSQL integration tests for the production authority reader (#709).

#713 writer가 실제로 남긴 historical REQUEST authority를 production
`GuideEvidenceAuthorityReaderPort` 구현이 exact artifact identity로 읽어
`assemble_sync_guide_evidence_authority(...)`까지 연결되는지 검증한다.

production ai_worker 코드는 backend를 import하지 않는다. 여기서 #713 Repository를 쓰는 것은
테스트가 양쪽 계약을 맞대기 위해서다.
"""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_guide_evidence_authority import SqlAlchemyGuideEvidenceAuthorityReader
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.guide_evidence_authority import (
    SyncGuideEvidenceAuthorityDecision,
    SyncGuideEvidenceAuthorityReason,
    SyncGuideEvidenceAuthorityRequest,
    SyncGuideEvidenceAuthoritySelection,
    assemble_sync_guide_evidence_authority,
)
from ai_worker.tasks.rag.guide_evidence_handoff import ObservedDecisionOutcome, RequestDecisionStage
from ai_worker.tasks.rag.source_member_identity import SourceMemberIdentity, SourceMemberKind
from app.core import config
from app.repositories.rag_request_authority_repository import (
    RagRequestAuthorityRepository,
    RequestGuardAuthorityRecord,
    RequestMemberDecisionRecord,
    RequestSourceDecisionRecord,
)
from rag_runtime.request_authority import (
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
    RequestAuthorityMemberIdentity,
    RequestAuthorityMemberKind,
)

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio

_USER_ID = UUID("70900000-0000-4000-8000-000000000101")
_SNAPSHOT_ID = UUID("70900000-0000-4000-8000-000000000102")
_MEMBER_ID = UUID("70900000-0000-4000-8000-000000000103")

_OPERATION = "GUIDE_SYNC_ANSWER"
_SOURCE_CODE = "MFDS"
_SOURCE_VERSION = "2026.09.01"

_SHARED_ENDPOINT_IDENTITY = RequestAuthorityMemberIdentity(
    member_kind=RequestAuthorityMemberKind.ENDPOINT_OPERATION,
    endpoint_code="MFDS_DUR",
)
_KERNEL_ENDPOINT_IDENTITY = SourceMemberIdentity(
    member_kind=SourceMemberKind.ENDPOINT_OPERATION,
    endpoint_code="MFDS_DUR",
)


def _alembic_config() -> Config:
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    return alembic_config


@pytest_asyncio.fixture
async def database(monkeypatch):
    name = "guide_authority_709_" + uuid4().hex
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


async def _seed_user(engine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                'INSERT INTO "user" (id, email, hashed_password, name, is_active, account_status, '
                "token_version, is_admin) "
                "VALUES (:id, :email, 'synthetic-hash', '테스트', true, 'ACTIVE', 0, false)"
            ),
            {"id": str(_USER_ID), "email": f"authority709-{uuid4().hex[:8]}@example.com"},
        )


async def _persist_authority(
    engine,
    *,
    member_outcome: RequestAuthorityDecisionOutcome,
) -> tuple[ImmutableArtifactRef, ImmutableArtifactRef, ImmutableArtifactRef]:
    """#713 writer로 Guard -> Source PASS -> Member 한 체인을 실제로 남기고 commit한다."""
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        repository = RagRequestAuthorityRepository(session)
        guard_ref = await repository.record_request_guard_authority(
            RequestGuardAuthorityRecord(
                user_id=_USER_ID,
                request_operation_code=_OPERATION,
                decision_stage=RequestAuthorityDecisionStage.REQUEST,
            )
        )
        source_ref = await repository.record_request_source_decision(
            RequestSourceDecisionRecord(
                request_guard_ref=guard_ref,
                user_id=_USER_ID,
                request_operation_code=_OPERATION,
                decision_stage=RequestAuthorityDecisionStage.REQUEST,
                source_snapshot_id=_SNAPSHOT_ID,
                source_code=_SOURCE_CODE,
                source_version=_SOURCE_VERSION,
                actual_decision_outcome=RequestAuthorityDecisionOutcome.PASS,
            )
        )
        member_ref = await repository.record_request_member_decision(
            RequestMemberDecisionRecord(
                request_guard_ref=guard_ref,
                user_id=_USER_ID,
                request_operation_code=_OPERATION,
                decision_stage=RequestAuthorityDecisionStage.REQUEST,
                source_snapshot_id=_SNAPSHOT_ID,
                source_snapshot_member_id=_MEMBER_ID,
                member_identity=_SHARED_ENDPOINT_IDENTITY,
                actual_decision_outcome=member_outcome,
            )
        )
        await session.commit()

    def _worker_ref(ref) -> ImmutableArtifactRef:
        return ImmutableArtifactRef(
            artifact_code=ref.artifact_code,
            version=ref.version,
            content_sha256=ref.content_sha256,
        )

    return _worker_ref(guard_ref), _worker_ref(source_ref), _worker_ref(member_ref)


def _assembly_request(
    guard_ref: ImmutableArtifactRef,
    source_ref: ImmutableArtifactRef,
    member_ref: ImmutableArtifactRef,
) -> SyncGuideEvidenceAuthorityRequest:
    return SyncGuideEvidenceAuthorityRequest(
        user_id=_USER_ID,
        request_guard_ref=guard_ref,
        request_operation_code=_OPERATION,
        selections=(
            SyncGuideEvidenceAuthoritySelection(
                source_snapshot_id=_SNAPSHOT_ID,
                source_snapshot_member_id=_MEMBER_ID,
                source_code=_SOURCE_CODE,
                source_version=_SOURCE_VERSION,
                member_identity=_KERNEL_ENDPOINT_IDENTITY,
                request_source_decision_ref=source_ref,
                request_member_decision_ref=member_ref,
            ),
        ),
    )


def _reader(engine) -> SqlAlchemyGuideEvidenceAuthorityReader:
    """실제 조회는 쓰기와 분리된 새 session에서 수행한다."""
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return SqlAlchemyGuideEvidenceAuthorityReader(sessions)


async def test_persisted_authority_chain_assembles_authenticated_binding(database) -> None:
    await _seed_user(database)
    guard_ref, source_ref, member_ref = await _persist_authority(
        database, member_outcome=RequestAuthorityDecisionOutcome.PASS
    )

    reader = _reader(database)
    outcome = await assemble_sync_guide_evidence_authority(_assembly_request(guard_ref, source_ref, member_ref), reader)

    assert outcome.decision is SyncGuideEvidenceAuthorityDecision.AUTHENTICATED
    assert outcome.reasons == ()
    assert len(outcome.bindings) == 1

    binding = outcome.bindings[0]
    assert binding.request_guard_ref == guard_ref
    assert binding.request_operation_code == _OPERATION
    assert binding.source_snapshot_id == _SNAPSHOT_ID
    assert binding.source_snapshot_member_id == _MEMBER_ID
    assert binding.source_code == _SOURCE_CODE
    assert binding.source_version == _SOURCE_VERSION
    assert binding.member_kind is SourceMemberKind.ENDPOINT_OPERATION
    assert binding.endpoint_code == "MFDS_DUR"
    assert binding.operation_code is None
    assert binding.request_source_decision_ref == source_ref
    assert binding.request_member_decision_ref == member_ref
    assert binding.observed_source_decision_outcome is ObservedDecisionOutcome.PASS
    assert binding.observed_member_decision_outcome is ObservedDecisionOutcome.PASS
    assert binding.request_decision_stage is RequestDecisionStage.REQUEST


async def test_persisted_member_fail_rejects_with_no_bindings(database) -> None:
    await _seed_user(database)
    guard_ref, source_ref, member_ref = await _persist_authority(
        database, member_outcome=RequestAuthorityDecisionOutcome.FAIL
    )

    reader = _reader(database)
    outcome = await assemble_sync_guide_evidence_authority(_assembly_request(guard_ref, source_ref, member_ref), reader)

    assert outcome.decision is SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.MEMBER_DECISION_NOT_PASS,)
    assert outcome.bindings == ()


async def test_reader_returns_each_persisted_observation_exactly(database) -> None:
    await _seed_user(database)
    guard_ref, source_ref, member_ref = await _persist_authority(
        database, member_outcome=RequestAuthorityDecisionOutcome.PASS
    )
    reader = _reader(database)

    guard = await reader.read_request_guard(request_guard_ref=guard_ref)
    assert guard is not None
    assert guard.artifact_ref == guard_ref
    assert guard.user_id == _USER_ID
    assert guard.request_operation_code == _OPERATION
    assert guard.decision_stage == "REQUEST"

    source = await reader.read_source_decision(request_source_decision_ref=source_ref)
    assert source is not None
    assert source.artifact_ref == source_ref
    assert source.request_guard_ref == guard_ref
    assert source.source_snapshot_id == _SNAPSHOT_ID
    assert source.source_code == _SOURCE_CODE
    assert source.source_version == _SOURCE_VERSION
    assert source.actual_decision_outcome is ObservedDecisionOutcome.PASS

    member = await reader.read_member_decision(request_member_decision_ref=member_ref)
    assert member is not None
    assert member.artifact_ref == member_ref
    assert member.request_guard_ref == guard_ref
    assert member.source_snapshot_member_id == _MEMBER_ID
    assert member.member_identity == _KERNEL_ENDPOINT_IDENTITY
    assert member.actual_decision_outcome is ObservedDecisionOutcome.PASS


async def test_wrong_exact_artifact_hash_reads_as_not_found(database) -> None:
    await _seed_user(database)
    guard_ref, _, _ = await _persist_authority(database, member_outcome=RequestAuthorityDecisionOutcome.PASS)
    reader = _reader(database)

    unknown = ImmutableArtifactRef(
        artifact_code=guard_ref.artifact_code,
        version=guard_ref.version,
        content_sha256="f" * 64,
    )
    assert await reader.read_request_guard(request_guard_ref=unknown) is None
