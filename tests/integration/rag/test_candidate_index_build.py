"""RAG-07B Candidate Index build port over real persistence (Issue #168).

These tests drive ``execute_candidate_index_build`` -- the production execution boundary that is
the only place ``backend`` imports RAG-07A (#167) -- end to end against real PostgreSQL, proving
that the same fixture always reproduces the same ``content_hash`` (determinism) and that a
re-run reuses the row instead of duplicating it (idempotency).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Table, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from ai_worker.tasks.rag.candidate_index import (
    CandidateIndexBuildConfig,
    CandidateIndexBuildMode,
    CandidateIndexBuildSuccess,
    build_candidate_index,
)
from ai_worker.tasks.rag.catalog.approval import CatalogApprovalReceipt, CatalogSourceApproval
from ai_worker.tasks.rag.catalog.build import CatalogProductInput
from ai_worker.tasks.rag.catalog.export import CatalogExportArtifacts
from ai_worker.tasks.rag.catalog.normalize import CATALOG_NORMALIZATION_VERSION
from ai_worker.tasks.rag.catalog.service import (
    CatalogBuildDecision,
    CatalogBuildRequest,
    build_catalog_candidate,
)
from ai_worker.tasks.rag.catalog.types import (
    CandidateCatalogSourceRef,
    CandidateRecordStatus,
    CatalogFreshnessStatus,
    CatalogVerificationStatus,
)
from app.core import config
from app.core.db.databases import Base
from app.models.rag_candidate_index import RagCandidateIndexStatus
from app.models.rag_catalog import RagCatalogSet
from app.repositories.rag_candidate_index_repository import RagCandidateIndexRepository
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)
from app.services.rag_candidate_index_build import execute_candidate_index_build

pytestmark = pytest.mark.asyncio

TEST_SCHEMA = "rag_candidate_index_build_test"
TEST_DATABASE_URL = URL.create(
    drivername="postgresql+asyncpg",
    username=config.DB_USER,
    password=config.DB_PASSWORD,
    host="127.0.0.1",
    port=config.DB_EXPOSE_PORT,
    database=config.DB_NAME,
)
test_engine = create_async_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
    # public·test_extensions도 검색 경로에 둔다: rag_candidate_index_member.embedding이 쓰는
    # pgvector의 vector 타입은 (배포 환경에 따라) 그중 한 곳에 설치되어 있어, 이 스키마만으로는
    # 타입을 찾지 못한다.
    connect_args={"server_settings": {"search_path": f"{TEST_SCHEMA},public,test_extensions"}},
)
session_factory = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)

_NOW = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)

_ROOT_TABLES = (
    "rag_candidate_index_version",
    "rag_candidate_index_member",
    "rag_catalog_set",
    "rag_source_snapshot",
)


def _hash(char: str) -> str:
    return char * 64


def _required_tables() -> list[Table]:
    """Root tables and their transitive FK closure only (see ``test_runtime_bundle_build.py``)."""
    seen: dict[str, Table] = {}
    stack = [Base.metadata.tables[name] for name in _ROOT_TABLES]
    while stack:
        table = stack.pop()
        if table.name in seen:
            continue
        seen[table.name] = table
        stack.extend(key.column.table for key in table.foreign_keys)
    return [seen[name] for name in sorted(seen)]


@pytest_asyncio.fixture(scope="module", autouse=True)
async def isolated_schema() -> AsyncIterator[None]:
    admin_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await connection.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))
        # CI의 test-rag job은 alembic migration 없이 이 pytest만 단독으로 돌리므로, vector 확장이
        # 이미 설치돼 있다고 가정할 수 없다. 다른 스키마에 이미 있어도 안전한 idempotent 호출이다.
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    tables = _required_tables()
    async with test_engine.begin() as connection:
        await connection.run_sync(lambda sync_connection: Base.metadata.create_all(sync_connection, tables=tables))
    try:
        yield
    finally:
        await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await admin_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_candidate_index_tables() -> AsyncIterator[None]:
    """Candidate Index rows only; Source/Catalog Set rows are seeded under per-test unique keys."""
    yield
    async with test_engine.begin() as connection:
        await connection.execute(
            text(f"TRUNCATE TABLE {TEST_SCHEMA}.rag_candidate_index_member, {TEST_SCHEMA}.rag_candidate_index_version")
        )


async def _seed_source_snapshot():
    suffix = uuid4().hex[:10]
    async with session_factory.begin() as session:
        repository = RagSourceCatalogRepository(session)
        source = await repository.create_source(
            RagSourceCreate(source_code=f"MFDS_CANDIDATE_{suffix}", display_name="MFDS Source")
        )
        endpoint = await repository.create_endpoint(
            RagSourceEndpointCreate(source_id=source.id, endpoint_code="PRODUCT_LIST", display_name="Product List")
        )
        operation = await repository.create_operation(
            RagSourceOperationCreate(
                endpoint_id=endpoint.id, operation_code="LIST_PRODUCTS", display_name="List Products"
            )
        )
        return await repository.create_snapshot(
            RagSourceSnapshotCreate(
                operation_id=operation.id,
                source_version=f"api:2026-09-10:{suffix}",
                raw_manifest_checksum=_hash("a"),
                canonical_checksum=_hash("b"),
                schema_version="schema-v1",
                parser_version="parser-v1",
                normalization_version="normalization-v1",
                canonicalization_spec_version="canonical-v1",
                record_count=1,
                rejected_record_count=0,
                collected_at=_NOW,
            )
        )


class _NoOpCatalogRepository:
    async def save_build(self, *, members: object, artifacts: object) -> None:
        return None


class _ApprovingVerifier:
    async def verify(
        self,
        *,
        catalog_version: str,
        export_checksum: str,
        source_refs: tuple[CandidateCatalogSourceRef, ...],
    ) -> CatalogApprovalReceipt:
        return CatalogApprovalReceipt(
            receipt_id="synthetic-candidate-index-approval",
            catalog_version=catalog_version,
            export_checksum=export_checksum,
            verification_status=CatalogVerificationStatus.APPROVED,
            is_complete=True,
            sources=tuple(
                CatalogSourceApproval(
                    source_ref=ref,
                    receipt_id="synthetic-source-approval",
                    verification_status=CatalogVerificationStatus.APPROVED,
                    freshness_status=CatalogFreshnessStatus.CURRENT,
                )
                for ref in source_refs
            ),
        )


async def _build_catalog_export(*, snapshot, catalog_version: str) -> CatalogExportArtifacts:
    request = CatalogBuildRequest(
        catalog_version=catalog_version,
        source_refs=(CandidateCatalogSourceRef(snapshot_id=str(snapshot.id), source_version=snapshot.source_version),),
        products=(
            CatalogProductInput(
                source_snapshot_id=str(snapshot.id),
                source_record_key="ITEM_SEQ:200012345",
                code_system="MFDS_ITEM_SEQ",
                canonical_code="200012345",
                product_name="테스트정",
                product_status=CandidateRecordStatus.ACTIVE,
                strength_text="500mg",
                dosage_form="정제",
                manufacturer_name="테스트제약",
            ),
        ),
        components=(),
        aliases=(),
    )
    result = await build_catalog_candidate(
        request=request, repository=_NoOpCatalogRepository(), approval_verifier=_ApprovingVerifier()
    )
    assert result.decision is CatalogBuildDecision.ACTIVATION_CANDIDATE
    assert result.export is not None
    return result.export


async def _seed_catalog_set(session, artifacts: CatalogExportArtifacts) -> RagCatalogSet:
    catalog = artifacts.catalog
    catalog_set = RagCatalogSet(
        catalog_version=catalog.catalog_version,
        schema_version=catalog.schema_version,
        normalization_version=catalog.normalization_version,
        manifest_spec_version=f"manifest-spec-{uuid4().hex[:10]}",
        envelope_hash=catalog.catalog_manifest_hash,
        manifest_json=artifacts.manifest_json,
    )
    session.add(catalog_set)
    await session.flush()
    return catalog_set


def _lexical_config(index_code: str) -> CandidateIndexBuildConfig:
    return CandidateIndexBuildConfig(
        index_code=index_code,
        index_version="candidate-index-v1",
        normalization_version=CATALOG_NORMALIZATION_VERSION,
        lexical_config_version="candidate-lexical-v1",
        search_order_version="candidate-search-order-v1",
        candidate_limit=20,
        display_limit=1,
        build_mode=CandidateIndexBuildMode.LEXICAL_ONLY,
        embedding_provider=None,
        embedding_model=None,
        embedding_model_version=None,
        embedding_dimension=None,
        distance_metric=None,
        ann_config=None,
    )


async def test_build_persists_a_building_version_with_its_members() -> None:
    """#166 D-04: component가 없는 제품도 빌드가 정상 처리되고, component는 저장되지 않는다.

    ``_build_catalog_export``가 넘기는 product는 component가 0개다(D-04가 정상으로 취급하는
    빈 주성분 케이스). RAG-07A는 이런 catalog도 ``components=()``인 성공으로 판정하며, 저장
    스키마(``rag_candidate_index_member``)에는 애초에 component 컬럼이 없어 그 값을 받아도 쓸
    곳이 없다 -- ``catalog_manifest_hash``도 이 처리로 바뀌지 않고 그대로 저장된다.
    """
    snapshot = await _seed_source_snapshot()
    index_code = f"idx-{uuid4().hex[:8]}"

    async with session_factory.begin() as session:
        artifacts = await _build_catalog_export(snapshot=snapshot, catalog_version=f"catalog-{uuid4().hex[:8]}")
        catalog_set = await _seed_catalog_set(session, artifacts)

        execution = await execute_candidate_index_build(
            session,
            artifacts=artifacts,
            config=_lexical_config(index_code),
            catalog_set_id=catalog_set.id,
        )

        assert execution.stored is True
        assert execution.persisted is not None
        assert execution.persisted.reused_existing is False
        assert execution.persisted.version.status is RagCandidateIndexStatus.BUILDING
        assert execution.persisted.version.index_code == index_code
        assert len(execution.persisted.members) == 1
        assert isinstance(execution.outcome, CandidateIndexBuildSuccess)
        assert execution.outcome.components == ()
        assert execution.persisted.version.catalog_manifest_hash == artifacts.catalog.catalog_manifest_hash


async def test_identical_inputs_reproduce_the_same_content_hash() -> None:
    """RAG-07A의 결정성: 동일 catalog·config 입력은 항상 동일 content_hash를 만든다.

    Persistence는 이 결정성을 소비할 뿐 만들지 않으므로, RAG-07A의 진입점을 직접 두 번 호출해
    검증한다 (재사용 여부는 :func:`test_rerunning_the_same_build_reuses_the_stored_version`의 몫).
    """
    snapshot = await _seed_source_snapshot()
    catalog_version = f"catalog-{uuid4().hex[:8]}"
    index_code = f"idx-{uuid4().hex[:8]}"

    first_artifacts = await _build_catalog_export(snapshot=snapshot, catalog_version=catalog_version)
    second_artifacts = await _build_catalog_export(snapshot=snapshot, catalog_version=catalog_version)

    first_outcome = build_candidate_index(first_artifacts, _lexical_config(index_code))
    second_outcome = build_candidate_index(second_artifacts, _lexical_config(index_code))

    assert isinstance(first_outcome, CandidateIndexBuildSuccess)
    assert isinstance(second_outcome, CandidateIndexBuildSuccess)
    assert first_outcome.manifest.content_hash == second_outcome.manifest.content_hash
    assert first_outcome.manifest.member_set_hash == second_outcome.manifest.member_set_hash


async def test_rerunning_the_same_build_reuses_the_stored_version() -> None:
    snapshot = await _seed_source_snapshot()
    index_code = f"idx-{uuid4().hex[:8]}"
    catalog_version = f"catalog-{uuid4().hex[:8]}"

    async with session_factory.begin() as session:
        artifacts = await _build_catalog_export(snapshot=snapshot, catalog_version=catalog_version)
        catalog_set = await _seed_catalog_set(session, artifacts)
        first = await execute_candidate_index_build(
            session, artifacts=artifacts, config=_lexical_config(index_code), catalog_set_id=catalog_set.id
        )

    async with session_factory.begin() as session:
        artifacts_again = await _build_catalog_export(snapshot=snapshot, catalog_version=catalog_version)
        catalog_set_again = await _seed_catalog_set(session, artifacts_again)
        second = await execute_candidate_index_build(
            session,
            artifacts=artifacts_again,
            config=_lexical_config(index_code),
            catalog_set_id=catalog_set_again.id,
        )

    assert first.persisted is not None and second.persisted is not None
    assert second.persisted.reused_existing is True
    assert second.persisted.version.id == first.persisted.version.id

    async with session_factory() as session:
        repository = RagCandidateIndexRepository(session)
        rows = await repository.get_version_by_code_and_version(
            index_code=index_code, index_version="candidate-index-v1"
        )
        assert rows is not None
        assert rows.id == first.persisted.version.id
