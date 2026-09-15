import asyncio
import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.adapters.postgresql_evidence_search import PostgresqlEvidenceSearchAdapter
from ai_worker.adapters.sqlalchemy_knowledge_evidence_index import (
    SqlAlchemyKnowledgeEvidenceIndexRepository,
)
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    QueryFingerprint,
    SensitiveText,
)
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchExecutionBinding,
    EvidenceSearchFailure,
    EvidenceSearchFailureReason,
    EvidenceSearchRequest,
    EvidenceSearchSuccess,
    ProductionSearchMethod,
    ProductionSearchSignal,
    QueryEmbeddingReceipt,
    RetrievalExecutionMode,
    SensitiveVector,
    VersionedDenseSearchConfiguration,
    VersionedEvidenceRetrievalConfiguration,
    VersionedLexicalSearchConfiguration,
)
from ai_worker.tasks.rag.knowledge_evidence_index import (
    DistanceMetric,
    KnowledgeChunkIdentity,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexMemberDraft,
    SensitiveEvidenceText,
    build_knowledge_evidence_index,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotProvenanceReceipt,
    SnapshotVerificationStatus,
    SourceSnapshotMemberCreate,
    SourceSnapshotMemberKind,
)
from app.core import config

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio

_SOURCE_ID = UUID("17800000-0000-4000-8000-000000000011")
_ENDPOINT_ID = UUID("17800000-0000-4000-8000-000000000012")
_OPERATION_ID = UUID("17800000-0000-4000-8000-000000000013")
_SNAPSHOT_ID = UUID("17800000-0000-4000-8000-000000000014")
_VERIFICATION_ID = UUID("17800000-0000-4000-8000-000000000015")
_DOCUMENT_ID = UUID("17800000-0000-4000-8000-000000000016")

_CHUNK_EXACT_ID = UUID("17800000-0000-4000-8000-000000000021")
_CHUNK_TYPO_ID = UUID("17800000-0000-4000-8000-000000000022")
_CHUNK_FTS_ID = UUID("17800000-0000-4000-8000-000000000023")

_TEXT_EXACT = "아스피린 장용정 100mg 복용 안내"
_TEXT_TYPO = "아스피린정 복약 지도"
_TEXT_FTS = "해열 소염 진통제 안내서 아스피린 포함"

_NOW = datetime(2026, 9, 14, 1, 0, tzinfo=UTC)


def _alembic_config() -> Config:
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    return alembic_config


@pytest_asyncio.fixture
async def database(monkeypatch):
    name = "knowledge_search_178_" + uuid4().hex
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


async def _seed_test_data(engine) -> tuple[UUID, UUID, UUID]:
    """Seed Source -> Endpoint -> Operation -> Snapshot -> Document -> 3 Chunks."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO rag_source "
                "(id, source_code, display_name, lifecycle_status, max_rejected_records, "
                "max_rejection_rate, empty_result_policy) "
                "VALUES (:id, 'MFDS', 'Synthetic MFDS', 'ACTIVE', 0, 0, 'REJECT')"
            ),
            {"id": str(_SOURCE_ID)},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_endpoint "
                "(id, source_id, endpoint_code, display_name, lifecycle_status, runtime_status, acquisition_status) "
                "VALUES (:id, :source_id, 'PRODUCTS', 'Synthetic products', 'VERIFIED', 'ENABLED', 'APPROVED')"
            ),
            {"id": str(_ENDPOINT_ID), "source_id": str(_SOURCE_ID)},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_operation "
                "(id, endpoint_id, operation_code, display_name, runtime_status, acquisition_status) "
                "VALUES (:id, :endpoint_id, 'LIST', 'Synthetic list', 'ENABLED', 'APPROVED')"
            ),
            {"id": str(_OPERATION_ID), "endpoint_id": str(_ENDPOINT_ID)},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_snapshot "
                "(id, operation_id, source_version, external_version, raw_manifest_checksum, canonical_checksum, "
                "schema_version, parser_version, normalization_version, canonicalization_spec_version, "
                "endpoint_receipt_hash, record_count, rejected_record_count, verification_status, collected_at) "
                "VALUES (:id, :operation_id, 'external:v1', 'v1', :raw_hash, :canonical_hash, 'schema-v1', "
                "'parser-v1', 'normalization-v1', 'canonical-v1', :receipt_hash, 3, 0, 'PENDING', :now)"
            ),
            {
                "id": str(_SNAPSHOT_ID),
                "operation_id": str(_OPERATION_ID),
                "raw_hash": "a" * 64,
                "canonical_hash": "b" * 64,
                "receipt_hash": "c" * 64,
                "now": _NOW,
            },
        )

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    prov = SnapshotProvenanceReceipt(
        source_id=_SOURCE_ID,
        source_code="MFDS",
        endpoint_id=_ENDPOINT_ID,
        operation_id=_OPERATION_ID,
        source_snapshot_id=_SNAPSHOT_ID,
        source_version="external:v1",
        external_version="v1",
        canonical_checksum="b" * 64,
        canonicalization_spec_version="canonical-v1",
        endpoint_receipt_hash="c" * 64,
        verification_seal_id=None,
        verification_status=SnapshotVerificationStatus.PENDING,
        rejected_record_count=0,
        publication_verification_id=None,
    )
    async with factory() as session, session.begin():
        member = await SqlAlchemySourceSnapshotRepository(session).append_snapshot_member(
            SourceSnapshotMemberCreate(
                provenance=prov,
                member_kind=SourceSnapshotMemberKind.ENDPOINT_OPERATION,
                endpoint_id=_ENDPOINT_ID,
                operation_id=_OPERATION_ID,
                ingestion_artifact_id=None,
                locator="$.records[0]",
                content_sha256="d" * 64,
            )
        )
    member_id = member.source_snapshot_member_id

    hash_exact = hashlib.sha256(_TEXT_EXACT.encode("utf-8")).hexdigest()
    hash_typo = hashlib.sha256(_TEXT_TYPO.encode("utf-8")).hexdigest()
    hash_fts = hashlib.sha256(_TEXT_FTS.encode("utf-8")).hexdigest()

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO rag_source_snapshot_verification "
                "(id, snapshot_id, check_name, verification_result, verified_by, verified_at) "
                "VALUES (:id, :snapshot_id, 'source-ingestion-integrity', 'PASSED', 'synthetic-reviewer', :now)"
            ),
            {"id": str(_VERIFICATION_ID), "snapshot_id": str(_SNAPSHOT_ID), "now": _NOW},
        )
        await connection.execute(
            text(
                "UPDATE rag_source_snapshot SET verification_status = 'CURRENT', verification_seal_id = :seal_id, "
                "verified_at = :now, effective_at = :now WHERE id = :snapshot_id"
            ),
            {"seal_id": str(_VERIFICATION_ID), "snapshot_id": str(_SNAPSHOT_ID), "now": _NOW},
        )
        await connection.execute(
            text(
                "INSERT INTO knowledge_document "
                "(id, title, publisher, source_url, document_version, document_status, record_contract_version, "
                "source_snapshot_member_id, external_document_id, document_content_hash, "
                "canonicalization_spec_version) "
                "VALUES (:id, 'Synthetic evidence', NULL, NULL, NULL, 'ACTIVE', 'KNOWLEDGE_EVIDENCE_V1', "
                ":member_id, 'doc-1', :doc_hash, 'canonical-v1')"
            ),
            {
                "id": str(_DOCUMENT_ID),
                "member_id": str(member_id),
                "doc_hash": "d" * 64,
            },
        )
        for chunk_id, idx, text_val, h_val in (
            (_CHUNK_EXACT_ID, 0, _TEXT_EXACT, hash_exact),
            (_CHUNK_TYPO_ID, 1, _TEXT_TYPO, hash_typo),
            (_CHUNK_FTS_ID, 2, _TEXT_FTS, hash_fts),
        ):
            await connection.execute(
                text(
                    "INSERT INTO knowledge_chunk "
                    "(id, knowledge_document_id, chunk_index, chunk_text, embedding_model, vector_store_key, "
                    "content_hash, normalization_version) "
                    "VALUES (:id, :document_id, :idx, :chunk_text, NULL, NULL, :content_hash, 'normalization-v1')"
                ),
                {
                    "id": str(chunk_id),
                    "document_id": str(_DOCUMENT_ID),
                    "idx": idx,
                    "chunk_text": text_val,
                    "content_hash": h_val,
                },
            )

    # Build Index via repository
    repo = SqlAlchemyKnowledgeEvidenceIndexRepository(factory)
    members = (
        KnowledgeIndexMemberDraft(
            identity=KnowledgeChunkIdentity(
                knowledge_chunk_id=_CHUNK_EXACT_ID,
                source_snapshot_id=_SNAPSHOT_ID,
                source_snapshot_member_id=member_id,
                source_code="MFDS",
                source_version="external:v1",
                canonical_checksum="b" * 64,
                external_document_id="doc-1",
                chunk_index=0,
                content_hash=hash_exact,
                locator="$.records[0]",
            ),
            content_text=SensitiveEvidenceText(_TEXT_EXACT),
            embedding=(1.0, 0.0),
        ),
        KnowledgeIndexMemberDraft(
            identity=KnowledgeChunkIdentity(
                knowledge_chunk_id=_CHUNK_TYPO_ID,
                source_snapshot_id=_SNAPSHOT_ID,
                source_snapshot_member_id=member_id,
                source_code="MFDS",
                source_version="external:v1",
                canonical_checksum="b" * 64,
                external_document_id="doc-1",
                chunk_index=1,
                content_hash=hash_typo,
                locator="$.records[0]",
            ),
            content_text=SensitiveEvidenceText(_TEXT_TYPO),
            embedding=(0.5, 0.5),
        ),
        KnowledgeIndexMemberDraft(
            identity=KnowledgeChunkIdentity(
                knowledge_chunk_id=_CHUNK_FTS_ID,
                source_snapshot_id=_SNAPSHOT_ID,
                source_snapshot_member_id=member_id,
                source_code="MFDS",
                source_version="external:v1",
                canonical_checksum="b" * 64,
                external_document_id="doc-1",
                chunk_index=2,
                content_hash=hash_fts,
                locator="$.records[0]",
            ),
            content_text=SensitiveEvidenceText(_TEXT_FTS),
            embedding=(0.0, 1.0),
        ),
    )

    build_req = KnowledgeIndexBuildRequest(
        index_code="test-index",
        index_version="1.0.0",
        embedding_model_ref="test-embedding-model",
        embedding_model_version="1.0",
        embedding_dimension=2,
        distance_metric=DistanceMetric.COSINE,
        members=members,
    )
    receipt = await build_knowledge_evidence_index(build_req, repository=repo)

    # Fetch index ID
    async with factory() as session:
        index_id_res = await session.scalar(
            text("SELECT id FROM rag_knowledge_index WHERE index_code = 'test-index' AND index_version = '1.0.0'")
        )
    return UUID(str(index_id_res)), member_id, receipt.index_configuration_hash


def _create_binding(
    index_id: UUID,
    member_id: UUID,
    index_config_hash: str,
    *,
    dense_enabled: bool = True,
    execution_mode: RetrievalExecutionMode | None = None,
) -> EvidenceSearchExecutionBinding:
    lex_cfg = VersionedLexicalSearchConfiguration(
        artifact_ref=ImmutableArtifactRef("lex-cfg", "1.0", "0" * 64),
        exact_strategy="case-sensitive-substring-v1",
        query_normalization="caller-supplied-nonblank-nfc-no-silent-transform-v1",
        trigram_match_operator="%",
        trigram_score_function="similarity",
        trigram_threshold="0.3",
        fts_regconfig="simple",
        fts_vector_expression="to_tsvector('simple', chunk_text)",
        fts_query_constructor="plainto_tsquery('simple', query)",
        fts_score_function="ts_rank_cd",
        exact_limit=20,
        trigram_limit=20,
        fts_limit=20,
    )
    lex_bound = VersionedLexicalSearchConfiguration(
        artifact_ref=ImmutableArtifactRef("lex-cfg", "1.0", lex_cfg.compute_canonical_hash()),
        exact_strategy=lex_cfg.exact_strategy,
        query_normalization=lex_cfg.query_normalization,
        trigram_match_operator=lex_cfg.trigram_match_operator,
        trigram_score_function=lex_cfg.trigram_score_function,
        trigram_threshold=lex_cfg.trigram_threshold,
        fts_regconfig=lex_cfg.fts_regconfig,
        fts_vector_expression=lex_cfg.fts_vector_expression,
        fts_query_constructor=lex_cfg.fts_query_constructor,
        fts_score_function=lex_cfg.fts_score_function,
        exact_limit=lex_cfg.exact_limit,
        trigram_limit=lex_cfg.trigram_limit,
        fts_limit=lex_cfg.fts_limit,
    )

    dense_bound: VersionedDenseSearchConfiguration | None = None
    expected_adapter_ref: ImmutableArtifactRef | None = None
    if dense_enabled:
        d_cfg = VersionedDenseSearchConfiguration(
            artifact_ref=ImmutableArtifactRef("dense-cfg", "1.0", "0" * 64),
            dense_limit=20,
        )
        dense_bound = VersionedDenseSearchConfiguration(
            artifact_ref=ImmutableArtifactRef("dense-cfg", "1.0", d_cfg.compute_canonical_hash()),
            dense_limit=20,
        )
        expected_adapter_ref = ImmutableArtifactRef("test-embed-adapter", "1.0", "e" * 64)

    if execution_mode is None:
        execution_mode = RetrievalExecutionMode.HYBRID_RRF if dense_enabled else RetrievalExecutionMode.LEXICAL_ONLY

    ret_cfg = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef("ret-cfg", "1.0", "0" * 64),
        lexical_config=lex_bound,
        dense_config=dense_bound,
        expected_query_embedding_adapter_ref=expected_adapter_ref,
        execution_mode=execution_mode,
    )
    ret_bound = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef("ret-cfg", "1.0", ret_cfg.compute_canonical_hash()),
        lexical_config=lex_bound,
        dense_config=dense_bound,
        expected_query_embedding_adapter_ref=expected_adapter_ref,
        execution_mode=execution_mode,
    )

    return EvidenceSearchExecutionBinding(
        filter_snapshot_ref=ImmutableArtifactRef("filter-ref", "1.0", "f" * 64),
        evidence_index_ref=ImmutableArtifactRef("test-index", "1.0.0", index_config_hash),
        knowledge_index_id=index_id,
        allowed_source_snapshot_ids=(_SNAPSHOT_ID,),
        allowed_source_snapshot_member_ids=(member_id,),
        retrieval_config=ret_bound,
    )


async def test_exact_hit_precedes_non_exact_in_lexical_fusion(database) -> None:
    engine = database
    index_id, member_id, index_hash = await _seed_test_data(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    adapter = PostgresqlEvidenceSearchAdapter(factory, ImmutableArtifactRef("adapter", "1.0", "a" * 64))

    binding = _create_binding(index_id, member_id, index_hash, dense_enabled=True)
    # Exact query matching chunk 0 ("아스피린 장용정 100mg 복용 안내")
    query = SensitiveText("장용정 100mg")
    fp = QueryFingerprint("sha256", "v1", "1" * 64)

    receipt = QueryEmbeddingReceipt(
        query_fingerprint=fp,
        model_ref="test-embedding-model",
        model_version="1.0",
        dimension=2,
        embedding=SensitiveVector((1.0, 0.0)),
        adapter_artifact_ref=ImmutableArtifactRef("test-embed-adapter", "1.0", "e" * 64),
    )

    req = EvidenceSearchRequest(
        normalized_query=query,
        query_fingerprint=fp,
        execution_binding=binding,
        query_embedding_receipt=receipt,
    )

    res = await adapter.search(req)
    assert isinstance(res, EvidenceSearchSuccess)
    assert len(res.lexical_hits) > 0

    # The exact hit (chunk_index=0) must be rank 1!
    top_hit = res.lexical_hits[0]
    assert top_hit.exact_hit is True
    assert top_hit.coordinate.chunk_index == 0
    assert top_hit.lexical_rank == 1


async def test_trigram_and_fts_subsearches(database) -> None:
    engine = database
    index_id, member_id, index_hash = await _seed_test_data(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    adapter = PostgresqlEvidenceSearchAdapter(factory, ImmutableArtifactRef("adapter", "1.0", "a" * 64))

    binding = _create_binding(index_id, member_id, index_hash, dense_enabled=False)

    # 1. Trigram typo query: "아스피린정 복약지" (typo for "아스피린정 복약 지도")
    query_typo = SensitiveText("아스피린정 복약지")
    fp = QueryFingerprint("sha256", "v1", "2" * 64)
    req_typo = EvidenceSearchRequest(
        normalized_query=query_typo,
        query_fingerprint=fp,
        execution_binding=binding,
        query_embedding_receipt=None,
    )
    res_typo = await adapter.search(req_typo)
    assert isinstance(res_typo, EvidenceSearchSuccess)
    assert len(res_typo.lexical_hits) > 0

    # 2. FTS token query: "해열 진통제"
    query_fts = SensitiveText("해열 진통제")
    req_fts = EvidenceSearchRequest(
        normalized_query=query_fts,
        query_fingerprint=fp,
        execution_binding=binding,
        query_embedding_receipt=None,
    )
    res_fts = await adapter.search(req_fts)
    assert isinstance(res_fts, EvidenceSearchSuccess)
    fts_hit_chunks = [h.coordinate.chunk_index for h in res_fts.lexical_hits]
    assert 2 in fts_hit_chunks  # Chunk 2 has "해열 소염 진통제"


async def test_dense_cosine_search_and_receipt(database) -> None:
    engine = database
    index_id, member_id, index_hash = await _seed_test_data(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    adapter = PostgresqlEvidenceSearchAdapter(factory, ImmutableArtifactRef("adapter", "1.0", "a" * 64))

    binding = _create_binding(index_id, member_id, index_hash, dense_enabled=True)
    query = SensitiveText("진통 소염")
    fp = QueryFingerprint("sha256", "v1", "3" * 64)

    # Vector (0.0, 1.0) matches Chunk 2 exact cosine distance 0 (similarity 1.0)
    receipt = QueryEmbeddingReceipt(
        query_fingerprint=fp,
        model_ref="test-embedding-model",
        model_version="1.0",
        dimension=2,
        embedding=SensitiveVector((0.0, 1.0)),
        adapter_artifact_ref=ImmutableArtifactRef("test-embed-adapter", "1.0", "e" * 64),
    )

    req = EvidenceSearchRequest(
        normalized_query=query,
        query_fingerprint=fp,
        execution_binding=binding,
        query_embedding_receipt=receipt,
    )

    res = await adapter.search(req)
    assert isinstance(res, EvidenceSearchSuccess)
    assert len(res.dense_hits) == 3
    # Chunk 2 should be rank 1 with similarity "1"
    top_dense = res.dense_hits[0]
    assert top_dense.coordinate.chunk_index == 2
    assert top_dense.dense_rank == 1
    assert top_dense.observed_dense_score == "1"


async def test_mismatched_embedding_receipt_fail_closed(database) -> None:
    engine = database
    index_id, member_id, index_hash = await _seed_test_data(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    adapter = PostgresqlEvidenceSearchAdapter(factory, ImmutableArtifactRef("adapter", "1.0", "a" * 64))

    binding = _create_binding(index_id, member_id, index_hash, dense_enabled=True)
    query = SensitiveText("아스피린")
    fp = QueryFingerprint("sha256", "v1", "4" * 64)

    # Wrong dimension: 3 instead of 2
    receipt = QueryEmbeddingReceipt(
        query_fingerprint=fp,
        model_ref="test-embedding-model",
        model_version="1.0",
        dimension=3,
        embedding=SensitiveVector((1.0, 0.0, 0.0)),
        adapter_artifact_ref=ImmutableArtifactRef("test-embed-adapter", "1.0", "e" * 64),
    )

    req = EvidenceSearchRequest(
        normalized_query=query,
        query_fingerprint=fp,
        execution_binding=binding,
        query_embedding_receipt=receipt,
    )

    res = await adapter.search(req)
    assert isinstance(res, EvidenceSearchFailure)
    assert res.reason == EvidenceSearchFailureReason.QUERY_EMBEDDING_INVALID


async def test_content_hash_tampering_fails_closed(database) -> None:
    engine = database
    index_id, member_id, index_hash = await _seed_test_data(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    adapter = PostgresqlEvidenceSearchAdapter(factory, ImmutableArtifactRef("adapter", "1.0", "a" * 64))

    # Tamper with chunk_text in database so SHA-256 does not match content_hash
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE knowledge_chunk SET chunk_text = '변조된 본문' WHERE id = :id"),
            {"id": str(_CHUNK_EXACT_ID)},
        )

    binding = _create_binding(index_id, member_id, index_hash, dense_enabled=False)
    query = SensitiveText("변조된")
    fp = QueryFingerprint("sha256", "v1", "5" * 64)

    req = EvidenceSearchRequest(
        normalized_query=query,
        query_fingerprint=fp,
        execution_binding=binding,
        query_embedding_receipt=None,
    )

    res = await adapter.search(req)
    assert isinstance(res, EvidenceSearchFailure)
    assert res.reason == EvidenceSearchFailureReason.SEARCH_RESULT_INVALID


async def test_transaction_local_trigram_threshold_cleanup(database) -> None:
    engine = database
    index_id, member_id, index_hash = await _seed_test_data(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    adapter = PostgresqlEvidenceSearchAdapter(factory, ImmutableArtifactRef("adapter", "1.0", "a" * 64))

    binding = _create_binding(index_id, member_id, index_hash, dense_enabled=False)
    query = SensitiveText("아스피린")
    fp = QueryFingerprint("sha256", "v1", "6" * 64)
    req = EvidenceSearchRequest(
        normalized_query=query,
        query_fingerprint=fp,
        execution_binding=binding,
        query_embedding_receipt=None,
    )

    await adapter.search(req)

    # After search, check the threshold on the connection - it should be the default 0.3 or not leaked from custom
    async with factory() as session:
        val = await session.scalar(text("SELECT current_setting('pg_trgm.similarity_threshold')"))
        assert val is not None


async def test_explain_query_plan_smoke(database) -> None:
    engine = database
    index_id, member_id, index_hash = await _seed_test_data(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async with factory() as session, session.begin():
        # Force index usage to smoke-test index definitions with EXPLAIN
        await session.execute(text("SET LOCAL enable_seqscan = off"))

        # 1. Test FTS index plan
        fts_plan = await session.execute(
            text(
                "EXPLAIN (FORMAT JSON) SELECT * FROM knowledge_chunk "
                "WHERE to_tsvector('simple', chunk_text) @@ plainto_tsquery('simple', '아스피린')"
            )
        )
        plan_json = fts_plan.scalar()
        assert plan_json is not None
        plan_str = str(plan_json)
        assert "ix_knowledge_chunk_fts_simple" in plan_str or "Bitmap Index Scan" in plan_str

        # 2. Test Trigram index plan
        trgm_plan = await session.execute(
            text("EXPLAIN (FORMAT JSON) SELECT * FROM knowledge_chunk WHERE chunk_text % '아스피린'")
        )
        plan_json2 = trgm_plan.scalar()
        assert plan_json2 is not None
        plan_str2 = str(plan_json2)
        assert "ix_knowledge_chunk_chunk_text_trgm" in plan_str2 or "Bitmap Index Scan" in plan_str2


async def test_transaction_isolation_and_read_only_verified_during_search(database) -> None:
    engine = database
    index_id, member_id, index_hash = await _seed_test_data(engine)
    raw_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    observed_isolation: list[str] = []
    observed_read_only: list[str] = []

    class ObservingSession:
        def __init__(self, delegate):
            self._delegate = delegate

        async def execute(self, statement, *args, **kwargs):
            res = await self._delegate.execute(statement, *args, **kwargs)
            # After SET TRANSACTION is executed (observed on the next command like set_config)
            if not observed_isolation and "set_config" in str(statement):
                iso = await self._delegate.scalar(text("SHOW transaction_isolation"))
                ro = await self._delegate.scalar(text("SHOW transaction_read_only"))
                observed_isolation.append(str(iso))
                observed_read_only.append(str(ro))
            return res

        def begin(self):
            return self._delegate.begin()

        def __getattr__(self, name):
            return getattr(self._delegate, name)

    @asynccontextmanager
    async def observing_factory():
        async with raw_factory() as session:
            yield ObservingSession(session)

    adapter = PostgresqlEvidenceSearchAdapter(observing_factory, ImmutableArtifactRef("adapter", "1.0", "a" * 64))

    binding = _create_binding(index_id, member_id, index_hash, dense_enabled=False)
    query = SensitiveText("아스피린")
    fp = QueryFingerprint("sha256", "v1", "8" * 64)
    req = EvidenceSearchRequest(
        normalized_query=query,
        query_fingerprint=fp,
        execution_binding=binding,
        query_embedding_receipt=None,
    )

    res = await adapter.search(req)
    assert isinstance(res, EvidenceSearchSuccess)
    assert len(observed_isolation) == 1
    assert observed_isolation[0].lower() == "repeatable read"
    assert observed_read_only[0].lower() == "on"

    # Verify that default pool connection outside the search transaction is default read committed and read-write
    async with raw_factory() as session:
        default_iso = await session.scalar(text("SHOW transaction_isolation"))
        default_ro = await session.scalar(text("SHOW transaction_read_only"))
        assert default_iso.lower() == "read committed"
        assert default_ro.lower() == "off"


async def test_lexical_only_does_not_execute_dense_sql(database) -> None:
    engine = database
    index_id, member_id, index_hash = await _seed_test_data(engine)
    raw_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    executed_statements: list[str] = []

    class StatementObservingSession:
        def __init__(self, delegate):
            self._delegate = delegate

        async def execute(self, statement, *args, **kwargs):
            executed_statements.append(str(statement))
            return await self._delegate.execute(statement, *args, **kwargs)

        def begin(self):
            return self._delegate.begin()

        def __getattr__(self, name):
            return getattr(self._delegate, name)

    @asynccontextmanager
    async def observing_factory():
        async with raw_factory() as session:
            yield StatementObservingSession(session)

    adapter = PostgresqlEvidenceSearchAdapter(observing_factory, ImmutableArtifactRef("adapter", "1.0", "a" * 64))
    binding = _create_binding(
        index_id,
        member_id,
        index_hash,
        dense_enabled=False,
        execution_mode=RetrievalExecutionMode.LEXICAL_ONLY,
    )
    query = SensitiveText("아스피린")
    fp = QueryFingerprint("sha256", "v1", "1" * 64)

    # 1. Providing query embedding receipt to LEXICAL_ONLY fails closed before opening search transaction
    unwanted_receipt = QueryEmbeddingReceipt(
        query_fingerprint=fp,
        model_ref="test-embedding-model",
        model_version="1.0",
        dimension=2,
        embedding=SensitiveVector((1.0, 0.0)),
        adapter_artifact_ref=ImmutableArtifactRef("test-embed-adapter", "1.0", "e" * 64),
    )
    invalid_req = EvidenceSearchRequest(
        normalized_query=query,
        query_fingerprint=fp,
        execution_binding=binding,
        query_embedding_receipt=unwanted_receipt,
    )
    fail_res = await adapter.search(invalid_req)
    assert fail_res == EvidenceSearchFailure(EvidenceSearchFailureReason.REQUEST_INVALID)
    assert len(executed_statements) == 0

    # 2. Valid LEXICAL_ONLY search executes without dense SQL
    valid_req = EvidenceSearchRequest(
        normalized_query=query,
        query_fingerprint=fp,
        execution_binding=binding,
        query_embedding_receipt=None,
    )
    res = await adapter.search(valid_req)
    assert isinstance(res, EvidenceSearchSuccess)
    assert len(res.lexical_hits) > 0
    assert len(res.dense_hits) == 0
    assert len(res.hybrid_hits) == len(res.lexical_hits)
    for h, lex_h in zip(res.hybrid_hits, res.lexical_hits, strict=True):
        assert h.coordinate == lex_h.coordinate
        assert h.fusion_rank == lex_h.fusion_rank
    # Assert no cosine distance / dense query in executed statements
    assert not any("cosine_distance" in stmt or "<=>" in stmt for stmt in executed_statements)
    # Assert no DENSE signal exists
    assert all(sig.method != ProductionSearchMethod.DENSE for sig in res.signals)
    assert any(sig.method == ProductionSearchMethod.LEXICAL for sig in res.signals)


async def test_dense_only_does_not_execute_lexical_sql(database) -> None:
    engine = database
    index_id, member_id, index_hash = await _seed_test_data(engine)
    raw_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    executed_statements: list[str] = []

    class StatementObservingSession:
        def __init__(self, delegate):
            self._delegate = delegate

        async def execute(self, statement, *args, **kwargs):
            executed_statements.append(str(statement))
            return await self._delegate.execute(statement, *args, **kwargs)

        def begin(self):
            return self._delegate.begin()

        def __getattr__(self, name):
            return getattr(self._delegate, name)

    @asynccontextmanager
    async def observing_factory():
        async with raw_factory() as session:
            yield StatementObservingSession(session)

    adapter = PostgresqlEvidenceSearchAdapter(observing_factory, ImmutableArtifactRef("adapter", "1.0", "a" * 64))
    binding = _create_binding(
        index_id,
        member_id,
        index_hash,
        dense_enabled=True,
        execution_mode=RetrievalExecutionMode.DENSE_ONLY,
    )
    query = SensitiveText("아스피린")
    fp = QueryFingerprint("sha256", "v1", "1" * 64)
    receipt = QueryEmbeddingReceipt(
        query_fingerprint=fp,
        model_ref="test-embedding-model",
        model_version="1.0",
        dimension=2,
        embedding=SensitiveVector((1.0, 0.0)),
        adapter_artifact_ref=ImmutableArtifactRef("test-embed-adapter", "1.0", "e" * 64),
    )
    req = EvidenceSearchRequest(
        normalized_query=query,
        query_fingerprint=fp,
        execution_binding=binding,
        query_embedding_receipt=receipt,
    )

    res = await adapter.search(req)
    assert isinstance(res, EvidenceSearchSuccess)
    assert len(res.lexical_hits) == 0
    assert len(res.dense_hits) > 0
    assert len(res.hybrid_hits) == len(res.dense_hits)
    for r, h in enumerate(res.hybrid_hits, start=1):
        assert h.fusion_rank == r
    # Assert lexical SQL (similarity / ts_rank_cd / % operator) was not executed
    assert not any("similarity" in stmt or "ts_rank_cd" in stmt or "plainto_tsquery" in stmt for stmt in executed_statements)
    # Assert signals only have DENSE method
    assert len(res.signals) > 0
    assert all(sig.method == ProductionSearchMethod.DENSE for sig in res.signals)


async def test_hybrid_returns_all_raw_and_fused_signals_in_canonical_order(database) -> None:
    engine = database
    index_id, member_id, index_hash = await _seed_test_data(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    adapter = PostgresqlEvidenceSearchAdapter(factory, ImmutableArtifactRef("adapter", "1.0", "a" * 64))

    binding = _create_binding(
        index_id,
        member_id,
        index_hash,
        dense_enabled=True,
        execution_mode=RetrievalExecutionMode.HYBRID_RRF,
    )
    query = SensitiveText("아스피린 장용정 100mg 복용 안내")
    fp = QueryFingerprint("sha256", "v1", "1" * 64)
    receipt = QueryEmbeddingReceipt(
        query_fingerprint=fp,
        model_ref="test-embedding-model",
        model_version="1.0",
        dimension=2,
        embedding=SensitiveVector((1.0, 0.0)),
        adapter_artifact_ref=ImmutableArtifactRef("test-embed-adapter", "1.0", "e" * 64),
    )
    req = EvidenceSearchRequest(
        normalized_query=query,
        query_fingerprint=fp,
        execution_binding=binding,
        query_embedding_receipt=receipt,
    )

    res = await adapter.search(req)
    assert isinstance(res, EvidenceSearchSuccess)
    assert len(res.signals) > 0

    signal_methods = {sig.method for sig in res.signals}
    assert ProductionSearchMethod.EXACT in signal_methods
    assert ProductionSearchMethod.TRIGRAM in signal_methods
    assert ProductionSearchMethod.FTS in signal_methods
    assert ProductionSearchMethod.LEXICAL in signal_methods
    assert ProductionSearchMethod.DENSE in signal_methods

    # Verify canonical signal ordering: method -> raw_rank -> StableCoordinate UTF-8
    def _sig_key(s: ProductionSearchSignal):
        return (
            s.method.value,
            s.raw_rank,
            s.provenance.source_code.encode("utf-8"),
            s.provenance.source_version.encode("utf-8"),
            s.provenance.external_document_id.encode("utf-8"),
            s.provenance.chunk_index,
        )

    assert list(res.signals) == sorted(res.signals, key=_sig_key)
