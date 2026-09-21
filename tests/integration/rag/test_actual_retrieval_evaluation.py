import asyncio
import hashlib
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

from ai_worker.adapters.postgresql_evidence_eligibility import (
    PostgreSqlEvidenceEligibilityVerifier,
)
from ai_worker.adapters.postgresql_evidence_search import (
    POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF,
    PostgresqlEvidenceSearchAdapter,
)
from ai_worker.adapters.sqlalchemy_knowledge_evidence_index import (
    SqlAlchemyKnowledgeEvidenceIndexRepository,
)
from ai_worker.adapters.sqlalchemy_retrieval_run import SqlAlchemyRetrievalRunStore
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import (
    SqlAlchemySourceSnapshotRepository,
)
from ai_worker.tasks.evaluation.actual_retrieval_index import DeterministicFakeEmbeddingAdapter
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    QueryFingerprint,
    SensitiveText,
)
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchExecutionBinding,
    EvidenceSearchRequest,
    RetrievalExecutionMode,
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
from ai_worker.tasks.rag.retrieval_runtime import (
    HybridRetrieveRequest,
    RetrievalExecutionStatus,
    execute_hybrid_retrieve,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotProvenanceReceipt,
    SnapshotVerificationStatus,
    SourceSnapshotMemberCreate,
    SourceSnapshotMemberKind,
)
from ai_worker.tasks.rag.text_embedding import TextEmbeddingSuccess
from app.core import config
from app.release_validation.ret_h_synthetic_smoke import run_verification_transaction

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio

_SOURCE_ID = UUID("17810000-0000-4000-8000-000000000011")
_ENDPOINT_ID = UUID("17810000-0000-4000-8000-000000000012")
_OPERATION_ID = UUID("17810000-0000-4000-8000-000000000013")
_SNAPSHOT_ID = UUID("17810000-0000-4000-8000-000000000014")
_VERIFICATION_ID = UUID("17810000-0000-4000-8000-000000000015")
_DOCUMENT_ID = UUID("17810000-0000-4000-8000-000000000016")

_CHUNK_ID = UUID("17810000-0000-4000-8000-000000000021")
_TEXT = "아세트아미노펜 500mg 복용법 및 하루 최대 투여량 안내"
_NOW = datetime(2026, 9, 15, 1, 0, tzinfo=UTC)


def _alembic_config() -> Config:
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    return alembic_config


@pytest_asyncio.fixture
async def database(monkeypatch):
    name = "ret_h_eval_" + uuid4().hex
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


async def _seed_data(engine) -> tuple[UUID, UUID]:
    """Seed synthetic Source, Snapshot, Document, Chunk and Index."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO rag_source "
                "(id, source_code, display_name, lifecycle_status, max_rejected_records, "
                "max_rejection_rate, empty_result_policy) "
                "VALUES (:id, 'MFDS_SYNTHETIC', 'Synthetic MFDS', 'ACTIVE', 0, 0, 'REJECT')"
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
                "'parser-v1', 'normalization-v1', 'canonical-v1', :receipt_hash, 1, 0, 'PENDING', :now)"
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
        source_code="MFDS_SYNTHETIC",
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

    content_hash = hashlib.sha256(_TEXT.encode("utf-8")).hexdigest()

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
                "VALUES (:id, 'Synthetic Acetaminophen', NULL, NULL, NULL, 'ACTIVE', 'KNOWLEDGE_EVIDENCE_V1', "
                ":member_id, 'doc-1', :doc_hash, 'canonical-v1')"
            ),
            {
                "id": str(_DOCUMENT_ID),
                "member_id": str(member_id),
                "doc_hash": "d" * 64,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO knowledge_chunk "
                "(id, knowledge_document_id, chunk_index, chunk_text, content_hash, normalization_version, embedding_model, vector_store_key) "
                "VALUES (:id, :doc_id, 0, :text_val, :hash_val, '1.0.0', 'openai:text-embedding-3-large', 'key-1')"
            ),
            {
                "id": str(_CHUNK_ID),
                "doc_id": str(_DOCUMENT_ID),
                "text_val": _TEXT,
                "hash_val": content_hash,
            },
        )

    # Build knowledge index
    builder = DeterministicFakeEmbeddingAdapter(dimension=1536)
    embed_res = await builder.embed(
        SensitiveText(_TEXT),
        model_ref="synthetic-embed",
        model_version="1.0.0",
        dimension=1536,
    )
    assert isinstance(embed_res, TextEmbeddingSuccess)
    vec = embed_res.embedding.reveal()

    member_draft = KnowledgeIndexMemberDraft(
        identity=KnowledgeChunkIdentity(
            knowledge_chunk_id=_CHUNK_ID,
            evidence_key="synthetic-actual-retrieval-evidence-1",
            source_snapshot_id=_SNAPSHOT_ID,
            source_snapshot_member_id=member_id,
            source_code="MFDS_SYNTHETIC",
            source_version="external:v1",
            canonical_checksum="b" * 64,
            external_document_id="doc-1",
            chunk_index=0,
            content_hash=content_hash,
            locator="$.records[0]",
        ),
        content_text=SensitiveEvidenceText(_TEXT),
        embedding=tuple(vec),
    )
    build_req = KnowledgeIndexBuildRequest(
        index_code="synthetic-index",
        index_version="1.0.0",
        embedding_model_ref="openai:text-embedding-3-large",
        embedding_model_version="text-embedding-3-large",
        embedding_dimension=1536,
        distance_metric=DistanceMetric.COSINE,
        members=(member_draft,),
    )

    repo = SqlAlchemyKnowledgeEvidenceIndexRepository(factory)
    receipt = await build_knowledge_evidence_index(build_req, repository=repo)

    async with factory() as session:
        index_id_res = await session.scalar(
            text("SELECT id FROM rag_knowledge_index WHERE index_code = 'synthetic-index' AND index_version = '1.0.0'")
        )

    return UUID(str(index_id_res)), _SNAPSHOT_ID, member_id, receipt.index_configuration_hash


async def test_actual_retrieval_evaluation_two_transaction_flow(database) -> None:
    index_id, snapshot_id, member_id, index_hash = await _seed_data(database)
    factory = async_sessionmaker(database, expire_on_commit=False, autoflush=False)

    search_port = PostgresqlEvidenceSearchAdapter(factory, POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF)
    run_store = SqlAlchemyRetrievalRunStore(session_factory=factory)
    eligibility_verifier = PostgreSqlEvidenceEligibilityVerifier(session_factory=factory)
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimension=1536)

    # Create dummy user, job_id and prescription_id in ai_job table to satisfy foreign key
    user_id = uuid4()
    job_id = uuid4()
    context_id = uuid4()
    prescription_id = uuid4()
    async with database.begin() as conn:
        await conn.execute(
            text(
                'INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin) '
                "VALUES (:id, :email, 'hash', '테스트', true, false)"
            ),
            {"id": str(user_id), "email": f"test-{uuid4().hex[:8]}@example.com"},
        )
        await conn.execute(
            text(
                "INSERT INTO ai_job (id, user_id, status, job_type, max_attempts, attempt_count, created_at, updated_at) "
                "VALUES (:id, :uid, 'PENDING', 'OCR', 3, 0, :now, :now)"
            ),
            {"id": str(job_id), "uid": str(user_id), "now": _NOW},
        )

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

    d_cfg = VersionedDenseSearchConfiguration(
        artifact_ref=ImmutableArtifactRef("dense-cfg", "1.0", "0" * 64),
        dense_limit=20,
    )
    dense_bound = VersionedDenseSearchConfiguration(
        artifact_ref=ImmutableArtifactRef("dense-cfg", "1.0", d_cfg.compute_canonical_hash()),
        dense_limit=20,
    )
    expected_adapter_ref = ImmutableArtifactRef("deterministic-fake-embedding", "1.0.0", "0" * 64)

    ret_cfg = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef("ret-cfg", "1.0", "0" * 64),
        lexical_config=lex_bound,
        dense_config=dense_bound,
        expected_query_embedding_adapter_ref=expected_adapter_ref,
        execution_mode=RetrievalExecutionMode.HYBRID_RRF,
    )
    ret_bound = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef("ret-cfg", "1.0", ret_cfg.compute_canonical_hash()),
        lexical_config=lex_bound,
        dense_config=dense_bound,
        expected_query_embedding_adapter_ref=expected_adapter_ref,
        execution_mode=RetrievalExecutionMode.HYBRID_RRF,
    )

    binding = EvidenceSearchExecutionBinding(
        filter_snapshot_ref=ImmutableArtifactRef("filter-ref", "1.0", "f" * 64),
        evidence_index_ref=ImmutableArtifactRef("synthetic-index", "1.0.0", index_hash),
        knowledge_index_id=index_id,
        allowed_source_snapshot_ids=(snapshot_id,),
        allowed_source_snapshot_member_ids=(member_id,),
        retrieval_config=ret_bound,
    )

    request = HybridRetrieveRequest(
        search_request=EvidenceSearchRequest(
            normalized_query=SensitiveText("아세트아미노펜 복용법"),
            query_fingerprint=QueryFingerprint("sha256", "v1", "1" * 64),
            execution_binding=binding,
            query_embedding_receipt=None,
        ),
        job_id=job_id,
        execution_context_id=context_id,
        prescription_version_id=prescription_id,
        runtime_release_bundle_id=uuid4(),
        runtime_release_bundle_manifest_hash="1" * 64,
        runtime_execution_manifest_id=uuid4(),
        runtime_execution_manifest_hash="2" * 64,
        runtime_guard_decision_ref="decision-1",
    )

    # Transaction 1: execute_hybrid_retrieve
    outcome = await execute_hybrid_retrieve(
        request,
        search_port=search_port,
        text_embedding_port=embedding_adapter,
        run_store=run_store,
        eligibility_verifier=eligibility_verifier,
    )

    assert outcome.status == RetrievalExecutionStatus.SUCCEEDED, f"Failed with message: {outcome.message}"
    assert outcome.persisted_receipt is not None
    run_id = str(outcome.persisted_receipt.run_id)
    receipt_hash = outcome.persisted_receipt.receipt_hash

    # Transaction 2: run_verification_transaction (read-only query)
    details = await run_verification_transaction(
        session_factory=factory,
        run_id=run_id,
        expected_receipt_hash=receipt_hash,
    )

    assert details["run_id"] == run_id
    assert details["status"] == "COMPLETED"
    assert details["variant"] == "RET-H"
    assert details["signals_count"] > 0
    assert details["hits_count"] > 0
    assert details["selected_hits_count"] >= 1
