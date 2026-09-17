"""PostgreSQL physical integration tests for Candidate Index search and Resolver hydration adapter (#170 RAG-08).

Verifies:
1. Real PostgreSQL execution of all 4 retrieval stages:
   - PRODUCT_NAME_EXACT (score 1.0, rank 1)
   - APPROVED_ALIAS_EXACT (score 1.0, rank 1)
   - TRIGRAM_EDIT_DISTANCE (pg_trgm similarity score)
   - DENSE_VECTOR (pgvector cosine similarity with synthetic query embedding)
2. Authoritative product hydration against RagMedicationProduct.
3. Provenance receipt generation and ingredient_hits=() empty tuple.
4. Consumption by pure MedicationResolver via PrehydratedCandidateIndexPort.
5. Inactive product exclusion by resolver.
6. Multi-session concurrency read-consistency (FOR SHARE locking).
7. Tampered persistence detection (member hash mismatch, product field mismatch, source binding mismatch).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Table, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core import config
from app.core.db.databases import Base
from app.models.rag_candidate_index import (
    RagCandidateIndexBuildMode,
    RagCandidateIndexEntityType,
    RagCandidateIndexStatus,
    RagCandidateIndexVersion,
)
from app.models.rag_catalog import (
    RagCatalogSet,
    RagCatalogSetSource,
    RagEntityIdentity,
    RagMedicationProduct,
    RagMedicationSearchEntryType,
)
from app.models.rag_source import RagSourceSnapshot
from app.repositories.rag_candidate_index_repository import (
    RagCandidateIndexMemberCreate,
    RagCandidateIndexRepository,
    RagCandidateIndexVersionCreate,
    _recomputed_lexical_member_content_hash,
    _recomputed_member_counts,
    _recomputed_member_set_hash,
)
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)
from app.services.rag.candidate_policy import CandidateStage, ResolverPolicy
from app.services.rag.candidate_resolver import (
    AttributeCompatibility,
    CandidateAttributeAssessment,
    CandidateEvidence,
    CandidateIndexMode,
    CandidateSearchRequest,
    HydratedCandidateEvidence,
    MedicationResolver,
    ProductSnapshot,
    ProductStatus,
    ResolverInput,
    ResolverOutcome,
    ResolverResult,
)
from app.services.rag_candidate_index_search import (
    CandidateQueryEmbeddingPort,
)
from app.services.rag_candidate_resolver_hydration import (
    CandidateIndexHydrationError,
    CandidateResolverHydrationAdapter,
)


@dataclass(frozen=True, slots=True)
class PrehydratedCandidateIndexPort:
    """In-memory CandidateIndexPort protocol test bridge for pure MedicationResolver consumption."""

    evidence: HydratedCandidateEvidence

    def hydrate(self, request: CandidateSearchRequest) -> HydratedCandidateEvidence:
        return self.evidence


pytestmark = pytest.mark.asyncio

TEST_SCHEMA = "rag_candidate_index_query_test"
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
    connect_args={"server_settings": {"search_path": f"{TEST_SCHEMA},public,test_extensions"}},
)
session_factory = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)

_ROOT_TABLES = (
    "rag_candidate_index_version",
    "rag_candidate_index_member",
    "rag_catalog_set",
    "rag_catalog_set_source",
    "rag_medication_product",
    "rag_entity_identity",
    "rag_source_snapshot",
)

_CATALOG_VERSION = "catalog-2026.09.17"
_EMBEDDING_MODEL_VERSION = "synthetic-model-v1"


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _required_tables() -> list[Table]:
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
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    tables = _required_tables()
    async with test_engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(sync_connection, tables=tables, checkfirst=False)
        )
    try:
        yield
    finally:
        await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await admin_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables() -> AsyncIterator[None]:
    yield
    async with test_engine.begin() as connection:
        table_exists = await connection.scalar(
            text("SELECT to_regclass(:table_name)"),
            {"table_name": f"{TEST_SCHEMA}.rag_candidate_index_member"},
        )
        if table_exists is None:
            return
        await connection.execute(
            text(
                f"TRUNCATE TABLE {TEST_SCHEMA}.rag_candidate_index_member, "
                f"{TEST_SCHEMA}.rag_candidate_index_version, "
                f"{TEST_SCHEMA}.rag_medication_product, "
                f"{TEST_SCHEMA}.rag_entity_identity, "
                f"{TEST_SCHEMA}.rag_catalog_set_source, "
                f"{TEST_SCHEMA}.rag_catalog_set, "
                f"{TEST_SCHEMA}.rag_source_snapshot, "
                f"{TEST_SCHEMA}.rag_source_operation, "
                f"{TEST_SCHEMA}.rag_source_endpoint, "
                f"{TEST_SCHEMA}.rag_source CASCADE"
            )
        )


async def _seed_source_and_catalog(session: AsyncSession):
    suffix = uuid4().hex[:10]
    repo = RagSourceCatalogRepository(session)
    source = await repo.create_source(
        RagSourceCreate(source_code=f"MFDS_{suffix}", display_name="MFDS Integration Source", owner_name="MFDS")
    )
    endpoint = await repo.create_endpoint(
        RagSourceEndpointCreate(source_id=source.id, endpoint_code="PRODUCT_LIST", display_name="Product List")
    )
    operation = await repo.create_operation(
        RagSourceOperationCreate(endpoint_id=endpoint.id, operation_code="LIST_PRODUCTS", display_name="List Products")
    )
    snapshot = await repo.create_snapshot(
        RagSourceSnapshotCreate(
            operation_id=operation.id,
            source_version=f"api:2026-09-17:{suffix}",
            raw_manifest_checksum=_hash("a"),
            canonical_checksum=_hash("b"),
            schema_version="schema-v1",
            parser_version="parser-v1",
            normalization_version="normalization-v1",
            canonicalization_spec_version="canonical-v1",
            record_count=3,
            rejected_record_count=0,
            collected_at=datetime.now(UTC),
        )
    )
    catalog_set = RagCatalogSet(
        catalog_version=_CATALOG_VERSION,
        schema_version="schema-v1",
        normalization_version="normalization-v1",
        manifest_spec_version=f"manifest-spec-{suffix}",
        envelope_hash=_hash("envelope"),
        manifest_json=b"{}",
    )
    session.add(catalog_set)
    await session.flush()

    catalog_source = RagCatalogSetSource(
        set_id=catalog_set.id,
        source_snapshot_id=snapshot.id,
        source_version=snapshot.source_version,
    )
    session.add(catalog_source)
    await session.flush()

    return snapshot, catalog_set


async def _seed_product(
    session: AsyncSession,
    *,
    snapshot: RagSourceSnapshot,
    canonical_code: str,
    product_name: str,
    strength_text: str | None,
    dosage_form: str | None,
    manufacturer_name: str | None,
    product_status: str = "ACTIVE",
) -> RagMedicationProduct:
    identity = RagEntityIdentity(
        entity_type="PRODUCT",
        code_system="MFDS_ITEM_SEQ",
        canonical_code=canonical_code,
    )
    session.add(identity)
    await session.flush()

    product = RagMedicationProduct(
        entity_identity_id=identity.id,
        source_snapshot_id=snapshot.id,
        source_record_key=f"ITEM_SEQ:{canonical_code}",
        code_system="MFDS_ITEM_SEQ",
        canonical_code=canonical_code,
        product_name=product_name,
        normalized_product_name=product_name,
        strength_text=strength_text,
        dosage_form=dosage_form,
        manufacturer_name=manufacturer_name,
        product_status=product_status,
    )
    session.add(product)
    await session.flush()
    return product


def _build_member_create(
    *,
    snapshot: RagSourceSnapshot,
    catalog_set: RagCatalogSet,
    product: RagMedicationProduct,
    entry_type: RagMedicationSearchEntryType,
    entry_ref: str,
    display_text: str,
    normalized_text: str,
    member_key: str,
    embedding: tuple[float, ...] | None = None,
    alias_ref: str | None = None,
    alias_source_snapshot_id: UUID | None = None,
) -> RagCandidateIndexMemberCreate:
    lexical_create = RagCandidateIndexMemberCreate(
        entry_type=entry_type,
        identity_entity_type=RagCandidateIndexEntityType.PRODUCT,
        identity_code_system=product.code_system,
        identity_canonical_code=product.canonical_code,
        product_ref=f"product:{product.canonical_code}",
        entry_ref=entry_ref,
        display_text=display_text,
        normalized_text=normalized_text,
        alias_ref=alias_ref,
        product_name=product.product_name,
        strength_text=product.strength_text,
        dosage_form=product.dosage_form,
        manufacturer_name=product.manufacturer_name,
        product_source_snapshot_id=snapshot.id,
        entry_source_snapshot_id=snapshot.id,
        alias_source_snapshot_id=alias_source_snapshot_id,
        catalog_version=catalog_set.catalog_version,
        catalog_manifest_hash=catalog_set.envelope_hash,
        normalization_version=catalog_set.normalization_version,
        member_key=member_key,
        member_content_hash="",
        embedding=embedding,
    )
    lexical_content_hash = _recomputed_lexical_member_content_hash(lexical_create)
    if embedding is not None:
        content_hash = hashlib.sha256(
            json.dumps(
                {
                    "lexical_member_content_hash": lexical_content_hash,
                    "embedding_model_version": _EMBEDDING_MODEL_VERSION,
                    "embedding": list(embedding),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    else:
        content_hash = lexical_content_hash

    return RagCandidateIndexMemberCreate(
        entry_type=lexical_create.entry_type,
        identity_entity_type=lexical_create.identity_entity_type,
        identity_code_system=lexical_create.identity_code_system,
        identity_canonical_code=lexical_create.identity_canonical_code,
        product_ref=lexical_create.product_ref,
        entry_ref=lexical_create.entry_ref,
        display_text=lexical_create.display_text,
        normalized_text=lexical_create.normalized_text,
        alias_ref=lexical_create.alias_ref,
        product_name=lexical_create.product_name,
        strength_text=lexical_create.strength_text,
        dosage_form=lexical_create.dosage_form,
        manufacturer_name=lexical_create.manufacturer_name,
        product_source_snapshot_id=lexical_create.product_source_snapshot_id,
        entry_source_snapshot_id=lexical_create.entry_source_snapshot_id,
        alias_source_snapshot_id=lexical_create.alias_source_snapshot_id,
        catalog_version=lexical_create.catalog_version,
        catalog_manifest_hash=lexical_create.catalog_manifest_hash,
        normalization_version=lexical_create.normalization_version,
        member_key=lexical_create.member_key,
        member_content_hash=content_hash,
        embedding=embedding,
    )


class DeterministicSyntheticCandidateQueryEmbedding(CandidateQueryEmbeddingPort):
    """Deterministic embedding provider for pgvector integration test."""

    async def embed_query(
        self,
        text: str,
        *,
        expected_model_version: str,
        expected_dimension: int,
    ) -> tuple[float, ...]:
        assert expected_model_version == _EMBEDDING_MODEL_VERSION
        assert expected_dimension == 3
        if "타이레놀" in text:
            return (1.0, 0.0, 0.0)
        elif "아스피린" in text:
            return (0.0, 1.0, 0.0)
        elif "게보린" in text:
            return (0.0, 0.0, 1.0)
        return (0.57735, 0.57735, 0.57735)


def _synthetic_policy() -> ResolverPolicy:
    return ResolverPolicy(
        policy_version="resolver-policy-v1",
        maximum_input_length=100,
        retrieval_limit=10,
        enable_dense=True,
        release_eligible=False,
        stage_weights=(
            (CandidateStage.PRODUCT_NAME_EXACT, 4.0),
            (CandidateStage.APPROVED_ALIAS_EXACT, 3.0),
            (CandidateStage.TRIGRAM_EDIT_DISTANCE, 2.0),
            (CandidateStage.DENSE_VECTOR, 1.0),
        ),
        rrf_k=10.0,
        minimum_relevance=0.5,
        minimum_margin=0.01,
        auto_select_stages=frozenset(
            {
                CandidateStage.PRODUCT_NAME_EXACT,
                CandidateStage.APPROVED_ALIAS_EXACT,
                CandidateStage.TRIGRAM_EDIT_DISTANCE,
            }
        ),
    )


class _SyntheticMatcher:
    def assess(self, resolver_input: ResolverInput, product: ProductSnapshot) -> CandidateAttributeAssessment:
        strength_match = (
            AttributeCompatibility.MATCH
            if resolver_input.strength_text == product.strength_text
            else AttributeCompatibility.NOT_APPLICABLE
        )
        return CandidateAttributeAssessment(
            strength=strength_match,
            dosage_form=AttributeCompatibility.NOT_APPLICABLE,
            manufacturer=AttributeCompatibility.NOT_APPLICABLE,
        )


class _SyntheticEvaluator:
    def evaluate(self, search_request: CandidateSearchRequest, candidate: CandidateEvidence) -> float:
        return 0.95


async def _setup_full_dataset(session: AsyncSession, *, index_code: str, index_version: str = "v1"):
    snapshot, catalog_set = await _seed_source_and_catalog(session)

    # 1. Product 1: Tylenol (Active)
    p1 = await _seed_product(
        session,
        snapshot=snapshot,
        canonical_code="20000001",
        product_name="타이레놀정500밀리그람",
        strength_text="500mg",
        dosage_form="정제",
        manufacturer_name="한국존슨앤드존슨",
        product_status="ACTIVE",
    )
    # 2. Product 2: Aspirin (Active)
    p2 = await _seed_product(
        session,
        snapshot=snapshot,
        canonical_code="20000002",
        product_name="아스피린프로텍트정100밀리그람",
        strength_text="100mg",
        dosage_form="정제",
        manufacturer_name="바이엘코리아",
        product_status="ACTIVE",
    )
    # 3. Product 3: Geworin (Inactive)
    p3 = await _seed_product(
        session,
        snapshot=snapshot,
        canonical_code="20000003",
        product_name="게보린정",
        strength_text="300mg",
        dosage_form="정제",
        manufacturer_name="삼진제약",
        product_status="INACTIVE",
    )

    # Members:
    # m1: Tylenol product name exact
    m1 = _build_member_create(
        snapshot=snapshot,
        catalog_set=catalog_set,
        product=p1,
        entry_type=RagMedicationSearchEntryType.PRODUCT_NAME,
        entry_ref="entry:20000001:pname",
        display_text="타이레놀정500밀리그람",
        normalized_text="타이레놀정500밀리그람",
        member_key=_hash("m1"),
        embedding=(1.0, 0.0, 0.0),
    )
    # m2: Tylenol approved alias exact
    m2 = _build_member_create(
        snapshot=snapshot,
        catalog_set=catalog_set,
        product=p1,
        entry_type=RagMedicationSearchEntryType.APPROVED_ALIAS,
        entry_ref="entry:20000001:alias",
        display_text="타이레놀",
        normalized_text="타이레놀",
        member_key=_hash("m2"),
        embedding=(1.0, 0.0, 0.0),
        alias_ref="alias:20000001:1",
        alias_source_snapshot_id=snapshot.id,
    )
    # m3: Aspirin product name exact
    m3 = _build_member_create(
        snapshot=snapshot,
        catalog_set=catalog_set,
        product=p2,
        entry_type=RagMedicationSearchEntryType.PRODUCT_NAME,
        entry_ref="entry:20000002:pname",
        display_text="아스피린프로텍트정100밀리그람",
        normalized_text="아스피린프로텍트정100밀리그람",
        member_key=_hash("m3"),
        embedding=(0.0, 1.0, 0.0),
    )
    # m4: Aspirin approved alias exact
    m4 = _build_member_create(
        snapshot=snapshot,
        catalog_set=catalog_set,
        product=p2,
        entry_type=RagMedicationSearchEntryType.APPROVED_ALIAS,
        entry_ref="entry:20000002:alias",
        display_text="아스피린",
        normalized_text="아스피린",
        member_key=_hash("m4"),
        embedding=(0.0, 1.0, 0.0),
        alias_ref="alias:20000002:1",
        alias_source_snapshot_id=snapshot.id,
    )
    # m5: Geworin product name (Inactive)
    m5 = _build_member_create(
        snapshot=snapshot,
        catalog_set=catalog_set,
        product=p3,
        entry_type=RagMedicationSearchEntryType.PRODUCT_NAME,
        entry_ref="entry:20000003:pname",
        display_text="게보린정",
        normalized_text="게보린정",
        member_key=_hash("m5"),
        embedding=(0.0, 0.0, 1.0),
    )

    members = (m1, m2, m3, m4, m5)
    counts = _recomputed_member_counts(members)
    member_set_hash = _recomputed_member_set_hash(members)

    version_create = RagCandidateIndexVersionCreate(
        index_code=index_code,
        index_version=index_version,
        build_mode=RagCandidateIndexBuildMode.HYBRID,
        catalog_set_id=catalog_set.id,
        catalog_version=catalog_set.catalog_version,
        catalog_manifest_hash=catalog_set.envelope_hash,
        schema_version=catalog_set.schema_version,
        normalization_version=catalog_set.normalization_version,
        lexical_config_version="lexical-v1",
        search_order_version="search-order-v1",
        candidate_limit=20,
        display_limit=1,
        member_count=counts["member_count"],
        product_identity_count=counts["product_identity_count"],
        product_name_count=counts["product_name_count"],
        approved_alias_count=counts["approved_alias_count"],
        vector_count=counts["vector_count"],
        member_set_hash=member_set_hash,
        configuration_hash=_hash("config-1"),
        content_hash=_hash("content-1"),
        embedding_provider="synthetic",
        embedding_model="synthetic-embedding",
        embedding_model_version=_EMBEDDING_MODEL_VERSION,
        embedding_dimension=3,
        distance_metric="COSINE",
    )

    repository = RagCandidateIndexRepository(session)
    built = await repository.build_index_version(version=version_create, members=members)
    activated = await repository.activate_ready_version(built.version.id)
    await session.commit()
    return snapshot, catalog_set, activated, (p1, p2, p3)


async def test_full_four_stage_physical_search_and_hydration() -> None:
    """Verifies all 4 physical retrieval stages and authoritative product hydration against PostgreSQL."""
    index_code = f"idx-{uuid4().hex[:8]}"
    index_version = "v1"

    async with session_factory() as session:
        snapshot, catalog_set, version, (p1, p2, p3) = await _setup_full_dataset(
            session, index_code=index_code, index_version=index_version
        )

    embedding_port = DeterministicSyntheticCandidateQueryEmbedding()

    async with session_factory() as session:
        adapter = CandidateResolverHydrationAdapter(session, index_code=index_code, embedding_port=embedding_port)

        # 1. Stage 1: PRODUCT_NAME_EXACT
        req1 = CandidateSearchRequest(
            medication_name="타이레놀정500밀리그람",
            index_version=index_version,
            retrieval_limit=10,
        )
        evidence1 = await adapter.hydrate_evidence(req1)
        assert isinstance(evidence1, HydratedCandidateEvidence)
        assert evidence1.ingredient_hits == ()
        assert evidence1.provenance.index_version == index_version
        assert evidence1.provenance.catalog_version == _CATALOG_VERSION
        assert evidence1.provenance.index_mode is CandidateIndexMode.HYBRID
        assert len(evidence1.provenance.source_refs) == 1
        assert evidence1.provenance.source_refs[0].snapshot_id == str(snapshot.id)

        # Check hits
        exact_hits = [h for h in evidence1.product_hits if h.stage is CandidateStage.PRODUCT_NAME_EXACT]
        assert len(exact_hits) == 1
        assert exact_hits[0].rank == 1
        assert exact_hits[0].stage_score == 1.0
        assert exact_hits[0].identity.canonical_code == "20000001"
        assert exact_hits[0].product.product_name == "타이레놀정500밀리그람"
        assert exact_hits[0].product.strength_text == "500mg"
        assert exact_hits[0].product.dosage_form == "정제"
        assert exact_hits[0].product.manufacturer_name == "한국존슨앤드존슨"
        assert exact_hits[0].product.status is ProductStatus.ACTIVE

        # 2. Stage 2: APPROVED_ALIAS_EXACT
        req2 = CandidateSearchRequest(
            medication_name="타이레놀",
            index_version=index_version,
            retrieval_limit=10,
        )
        evidence2 = await adapter.hydrate_evidence(req2)
        alias_hits = [h for h in evidence2.product_hits if h.stage is CandidateStage.APPROVED_ALIAS_EXACT]
        assert len(alias_hits) == 1
        assert alias_hits[0].rank == 1
        assert alias_hits[0].stage_score == 1.0
        assert alias_hits[0].identity.canonical_code == "20000001"
        assert alias_hits[0].product.product_name == "타이레놀정500밀리그람"

        # 3. Stage 3: TRIGRAM_EDIT_DISTANCE (pg_trgm typo recall)
        req3 = CandidateSearchRequest(
            medication_name="타이레놀정50밀리그람",  # typo: missing one 0
            index_version=index_version,
            retrieval_limit=10,
        )
        evidence3 = await adapter.hydrate_evidence(req3)
        # Exact stages should have no matches for this typo
        assert len([h for h in evidence3.product_hits if h.stage is CandidateStage.PRODUCT_NAME_EXACT]) == 0
        assert len([h for h in evidence3.product_hits if h.stage is CandidateStage.APPROVED_ALIAS_EXACT]) == 0
        # Trigram stage should recall Tylenol with similarity score
        trigram_hits = [h for h in evidence3.product_hits if h.stage is CandidateStage.TRIGRAM_EDIT_DISTANCE]
        assert len(trigram_hits) >= 1
        top_trigram = trigram_hits[0]
        assert top_trigram.identity.canonical_code == "20000001"
        assert top_trigram.stage_score > 0.5  # High similarity due to small typo

        # 4. Stage 4: DENSE_VECTOR (pgvector cosine recall)
        req4 = CandidateSearchRequest(
            medication_name="아스피린",
            index_version=index_version,
            retrieval_limit=10,
        )
        evidence4 = await adapter.hydrate_evidence(req4)
        dense_hits = [h for h in evidence4.product_hits if h.stage is CandidateStage.DENSE_VECTOR]
        assert len(dense_hits) >= 1
        # Query embedding for "아스피린" is (0.0, 1.0, 0.0), exactly matching Aspirin members
        top_dense = dense_hits[0]
        assert top_dense.identity.canonical_code == "20000002"
        assert pytest.approx(top_dense.stage_score, 1e-4) == 1.0


async def test_resolver_consumption_end_to_end() -> None:
    """Verifies that pure MedicationResolver successfully consumes evidence via PrehydratedCandidateIndexPort."""
    index_code = f"idx-{uuid4().hex[:8]}"
    index_version = "v1"

    async with session_factory() as session:
        await _setup_full_dataset(session, index_code=index_code, index_version=index_version)

    embedding_port = DeterministicSyntheticCandidateQueryEmbedding()

    async with session_factory() as session:
        adapter = CandidateResolverHydrationAdapter(session, index_code=index_code, embedding_port=embedding_port)
        req = CandidateSearchRequest(
            medication_name="타이레놀정500밀리그람",
            index_version=index_version,
            retrieval_limit=10,
        )
        evidence = await adapter.hydrate_evidence(req)

    # Wrap in PrehydratedCandidateIndexPort
    port = PrehydratedCandidateIndexPort(evidence=evidence)
    matcher = _SyntheticMatcher()
    evaluator = _SyntheticEvaluator()
    resolver = MedicationResolver(
        index_port=port,
        attribute_matcher=matcher,
        relevance_evaluator=evaluator,
    )
    r_input = ResolverInput(
        medication_name="타이레놀정500밀리그람",
        strength_text="500mg",
        index_version=index_version,
        policy_version="resolver-policy-v1",
    )
    policy = _synthetic_policy()

    result = resolver.resolve(r_input, policy)
    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.SINGLE_CANDIDATE
    assert result.candidate is not None
    assert result.candidate.identity.canonical_code == "20000001"
    assert result.candidate.product.product_name == "타이레놀정500밀리그람"
    assert result.candidate.product.status is ProductStatus.ACTIVE


async def test_inactive_product_excluded_by_resolver() -> None:
    """Verifies that INACTIVE products are hydrated with INACTIVE status and excluded from resolver single match."""
    index_code = f"idx-{uuid4().hex[:8]}"
    index_version = "v1"

    async with session_factory() as session:
        await _setup_full_dataset(session, index_code=index_code, index_version=index_version)

    embedding_port = DeterministicSyntheticCandidateQueryEmbedding()

    async with session_factory() as session:
        adapter = CandidateResolverHydrationAdapter(session, index_code=index_code, embedding_port=embedding_port)
        # Search for inactive product "게보린정"
        req = CandidateSearchRequest(
            medication_name="게보린정",
            index_version=index_version,
            retrieval_limit=10,
        )
        evidence = await adapter.hydrate_evidence(req)

    # Check that product status is INACTIVE
    exact_hits = [h for h in evidence.product_hits if h.stage is CandidateStage.PRODUCT_NAME_EXACT]
    assert len(exact_hits) == 1
    assert exact_hits[0].product.status is ProductStatus.INACTIVE

    # Feed to resolver
    port = PrehydratedCandidateIndexPort(evidence=evidence)
    matcher = _SyntheticMatcher()
    evaluator = _SyntheticEvaluator()
    resolver = MedicationResolver(
        index_port=port,
        attribute_matcher=matcher,
        relevance_evaluator=evaluator,
    )
    r_input = ResolverInput(
        medication_name="게보린정",
        strength_text="300mg",
        index_version=index_version,
        policy_version="resolver-policy-v1",
    )
    policy = _synthetic_policy()

    result = resolver.resolve(r_input, policy)
    assert isinstance(result, ResolverResult)
    # INACTIVE product must not be selected as a single candidate
    assert result.outcome is ResolverOutcome.NO_CANDIDATE
    assert result.eligible_count == 0


async def test_multi_session_concurrency_lock_consistency() -> None:
    """Verifies read-consistency row lock (FOR SHARE) blocks concurrent modification (FOR UPDATE)."""
    index_code = f"idx-{uuid4().hex[:8]}"
    index_version = "v1"

    async with session_factory() as session:
        await _setup_full_dataset(session, index_code=index_code, index_version=index_version)

    # Session 1: holds read lock with FOR SHARE
    async with session_factory() as session1:
        repo1 = RagCandidateIndexRepository(session1)
        snapshot = await repo1.get_verified_ready_index_snapshot(
            index_code=index_code,
            expected_index_version=index_version,
        )
        assert snapshot.version.status is RagCandidateIndexStatus.READY

        # Session 2: attempts concurrent FOR UPDATE with short lock_timeout
        async with session_factory() as session2:
            await session2.execute(text("SET LOCAL lock_timeout = '100ms'"))
            # Attempting to lock the active version FOR UPDATE while Session 1 holds FOR SHARE must fail with lock timeout
            with pytest.raises((OperationalError, DBAPIError)) as exc_info:
                stmt = (
                    select(RagCandidateIndexVersion)
                    .where(RagCandidateIndexVersion.id == snapshot.version.id)
                    .with_for_update()
                )
                await session2.execute(stmt)
            assert "canceling statement due to lock timeout" in str(exc_info.value).lower()


async def test_tampered_persistence_rejections() -> None:
    """Verifies that physical tampering in PostgreSQL is immediately caught by integrity revalidation."""
    index_code = f"idx-{uuid4().hex[:8]}"
    index_version = "v1"

    async with session_factory() as session:
        await _setup_full_dataset(session, index_code=index_code, index_version=index_version)

    embedding_port = DeterministicSyntheticCandidateQueryEmbedding()

    # 1. Tamper member row directly in PostgreSQL
    async with session_factory() as session:
        await session.execute(
            text(
                f"UPDATE {TEST_SCHEMA}.rag_candidate_index_member SET normalized_text = '변조된텍스트' WHERE entry_ref = 'entry:20000001:pname'"
            )
        )
        await session.commit()

    async with session_factory() as session:
        adapter = CandidateResolverHydrationAdapter(session, index_code=index_code, embedding_port=embedding_port)
        req = CandidateSearchRequest(
            medication_name="타이레놀정500밀리그람",
            index_version=index_version,
            retrieval_limit=10,
        )
        with pytest.raises(CandidateIndexHydrationError) as exc_info:
            await adapter.hydrate_evidence(req)
        assert exc_info.value.reason == "PERSISTENCE_INTEGRITY_COMPROMISED"

    # Restore member row, then tamper product table
    async with session_factory() as session:
        await session.execute(
            text(
                f"UPDATE {TEST_SCHEMA}.rag_candidate_index_member SET normalized_text = '타이레놀정500밀리그람' WHERE entry_ref = 'entry:20000001:pname'"
            )
        )
        await session.execute(
            text(
                f"UPDATE {TEST_SCHEMA}.rag_medication_product SET product_name = '변조된제품명' WHERE canonical_code = '20000001'"
            )
        )
        await session.commit()

    async with session_factory() as session:
        adapter = CandidateResolverHydrationAdapter(session, index_code=index_code, embedding_port=embedding_port)
        req = CandidateSearchRequest(
            medication_name="타이레놀정500밀리그람",
            index_version=index_version,
            retrieval_limit=10,
        )
        with pytest.raises(CandidateIndexHydrationError) as exc_info:
            await adapter.hydrate_evidence(req)
        assert exc_info.value.reason == "PRODUCT_FIELD_MISMATCH"

    # 2. Version mismatch
    async with session_factory() as session:
        adapter = CandidateResolverHydrationAdapter(session, index_code=index_code)
        req_mismatch = CandidateSearchRequest(
            medication_name="타이레놀정500밀리그람",
            index_version="non-existent-version",
            retrieval_limit=10,
        )
        with pytest.raises(CandidateIndexHydrationError) as exc_info:
            await adapter.hydrate_evidence(req_mismatch)
        assert exc_info.value.reason == "INDEX_VERSION_MISMATCH"

    # 3. Source snapshot outside catalog set
    async with session_factory() as session:
        await session.execute(text(f"DELETE FROM {TEST_SCHEMA}.rag_catalog_set_source"))
        await session.commit()

    async with session_factory() as session:
        adapter = CandidateResolverHydrationAdapter(session, index_code=index_code, embedding_port=embedding_port)
        req = CandidateSearchRequest(
            medication_name="타이레놀정500밀리그람",
            index_version=index_version,
            retrieval_limit=10,
        )
        with pytest.raises(CandidateIndexHydrationError) as exc_info:
            await adapter.hydrate_evidence(req)
        assert exc_info.value.reason == "SOURCE_SNAPSHOT_MISMATCH"


async def test_product_name_exact_multi_hit_ranks_and_evidence() -> None:
    """Product Exact multi-hit synthetic case:

    same normalized product name
    -> different official Product identities
    -> retrieval_limit >= 2
    Expected:
    - 2 hits
    - ranks == (1, 2)
    - stable deterministic order
    - scores == (1.0, 1.0)
    - same query run twice produces identical hit order and ranks
    - HydratedCandidateEvidence validation passes
    - pure MedicationResolver consumes evidence cleanly
    """
    index_code = f"idx-multi-{uuid4().hex[:8]}"
    index_version = "v1"

    async with session_factory() as session:
        snapshot, catalog_set = await _seed_source_and_catalog(session)

        # Create two distinct active products with identical product_name
        p1 = await _seed_product(
            session,
            snapshot=snapshot,
            canonical_code="30000001",
            product_name="공통명칭정",
            strength_text="10mg",
            dosage_form="정제",
            manufacturer_name="제약사A",
            product_status="ACTIVE",
        )
        p2 = await _seed_product(
            session,
            snapshot=snapshot,
            canonical_code="30000002",
            product_name="공통명칭정",
            strength_text="20mg",
            dosage_form="정제",
            manufacturer_name="제약사B",
            product_status="ACTIVE",
        )

        m1 = _build_member_create(
            snapshot=snapshot,
            catalog_set=catalog_set,
            product=p1,
            entry_type=RagMedicationSearchEntryType.PRODUCT_NAME,
            entry_ref="entry:30000001:pname",
            display_text="공통명칭정",
            normalized_text="공통명칭정",
            member_key=_hash("m_multi_1"),
            embedding=(1.0, 0.0, 0.0),
        )
        m2 = _build_member_create(
            snapshot=snapshot,
            catalog_set=catalog_set,
            product=p2,
            entry_type=RagMedicationSearchEntryType.PRODUCT_NAME,
            entry_ref="entry:30000002:pname",
            display_text="공통명칭정",
            normalized_text="공통명칭정",
            member_key=_hash("m_multi_2"),
            embedding=(0.0, 1.0, 0.0),
        )

        members = (m1, m2)
        counts = _recomputed_member_counts(members)
        member_set_hash = _recomputed_member_set_hash(members)

        version_create = RagCandidateIndexVersionCreate(
            index_code=index_code,
            index_version=index_version,
            build_mode=RagCandidateIndexBuildMode.HYBRID,
            catalog_set_id=catalog_set.id,
            catalog_version=catalog_set.catalog_version,
            catalog_manifest_hash=catalog_set.envelope_hash,
            schema_version=catalog_set.schema_version,
            normalization_version=catalog_set.normalization_version,
            lexical_config_version="lexical-v1",
            search_order_version="search-order-v1",
            candidate_limit=20,
            display_limit=1,
            member_count=counts["member_count"],
            product_identity_count=counts["product_identity_count"],
            product_name_count=counts["product_name_count"],
            approved_alias_count=counts["approved_alias_count"],
            vector_count=counts["vector_count"],
            member_set_hash=member_set_hash,
            configuration_hash=_hash("config-multi"),
            content_hash=_hash("content-multi"),
            embedding_provider="synthetic",
            embedding_model="synthetic-embedding",
            embedding_model_version=_EMBEDDING_MODEL_VERSION,
            embedding_dimension=3,
            distance_metric="COSINE",
        )

        repository = RagCandidateIndexRepository(session)
        built = await repository.build_index_version(version=version_create, members=members)
        await repository.activate_ready_version(built.version.id)
        await session.commit()

    embedding_port = DeterministicSyntheticCandidateQueryEmbedding()

    async with session_factory() as session:
        adapter = CandidateResolverHydrationAdapter(session, index_code=index_code, embedding_port=embedding_port)
        req = CandidateSearchRequest(
            medication_name="공통명칭정",
            index_version=index_version,
            retrieval_limit=10,
        )

        # Run 1
        evidence_first = await adapter.hydrate_evidence(req)
        assert isinstance(evidence_first, HydratedCandidateEvidence)
        exact_hits = [h for h in evidence_first.product_hits if h.stage is CandidateStage.PRODUCT_NAME_EXACT]
        assert len(exact_hits) == 2

        hit1, hit2 = exact_hits
        assert hit1.rank == 1
        assert hit2.rank == 2
        assert hit1.stage_score == 1.0
        assert hit2.stage_score == 1.0
        assert hit1.identity.canonical_code == "30000001"
        assert hit2.identity.canonical_code == "30000002"
        assert hit1.product.manufacturer_name == "제약사A"
        assert hit2.product.manufacturer_name == "제약사B"

        # Run 2: ensure deterministic stability across repeated queries
        evidence_second = await adapter.hydrate_evidence(req)
        assert evidence_first.product_hits == evidence_second.product_hits
        assert [h.rank for h in evidence_second.product_hits if h.stage is CandidateStage.PRODUCT_NAME_EXACT] == [1, 2]

        # Consume via pure MedicationResolver
        port = PrehydratedCandidateIndexPort(evidence=evidence_first)
        matcher = _SyntheticMatcher()
        evaluator = _SyntheticEvaluator()
        resolver = MedicationResolver(
            index_port=port,
            attribute_matcher=matcher,
            relevance_evaluator=evaluator,
        )
        r_input = ResolverInput(
            medication_name="공통명칭정",
            strength_text=None,
            index_version=index_version,
            policy_version="resolver-policy-v1",
        )
        policy = _synthetic_policy()

        result = resolver.resolve(r_input, policy)
        assert isinstance(result, ResolverResult)
        assert result.outcome is ResolverOutcome.AMBIGUOUS
        assert result.candidate is None
        assert len(result.internal_candidates) == 2
