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
from ai_worker.adapters.postgresql_evidence_search import PostgresqlEvidenceSearchAdapter
from ai_worker.adapters.sqlalchemy_knowledge_evidence_index import (
    SqlAlchemyKnowledgeEvidenceIndexRepository,
)
from ai_worker.adapters.sqlalchemy_retrieval_run import SqlAlchemyRetrievalRunStore
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import (
    SqlAlchemySourceSnapshotRepository,
)
from ai_worker.tasks.evaluation.actual_retrieval import DeterministicFakeEmbeddingAdapter
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    QueryFingerprint,
    SensitiveText,
)
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchExecutionBinding,
    EvidenceSearchRequest,
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
                "(id, document_id, chunk_index, chunk_text, content_hash, heading, page_number) "
                "VALUES (:id, :doc_id, 0, :text_val, :hash_val, 'Usage', 1)"
            ),
            {
                "id": str(_CHUNK_ID),
                "doc_id": str(_DOCUMENT_ID),
                "text_val": _TEXT,
                "hash_val": content_hash,
            },
        )

    # Build knowledge index
    builder = DeterministicFakeEmbeddingAdapter(dimensions=1536)
    embed_res = await builder.embed(
        SensitiveText(_TEXT),
        model_ref="synthetic-embed",
        model_version="1.0.0",
        dimension=1536,
    )
    assert embed_res.is_success
    vec = embed_res.embedding.reveal()

    member_draft = KnowledgeIndexMemberDraft(
        identity=KnowledgeChunkIdentity(
            knowledge_document_id=_DOCUMENT_ID,
            knowledge_chunk_id=_CHUNK_ID,
            chunk_index=0,
            content_hash=content_hash,
        ),
        chunk_text=SensitiveEvidenceText(_TEXT),
        embedding=SensitiveVector(vec),
    )
    built_index = build_knowledge_evidence_index(
        KnowledgeIndexBuildRequest(
            members=[member_draft],
            embedding_model="synthetic-embed",
            embedding_model_version="1.0.0",
            embedding_dimensions=1536,
            distance_metric=DistanceMetric.COSINE,
            built_by="synthetic-builder",
        )
    )

    async with factory() as session, session.begin():
        repo = SqlAlchemyKnowledgeEvidenceIndexRepository(session)
        created_index = await repo.create_index(built_index)
        await repo.publish_index(created_index.id, activated_by="synthetic-builder")

    return created_index.id, _SNAPSHOT_ID


async def test_actual_retrieval_evaluation_two_transaction_flow(database) -> None:
    index_id, snapshot_id = await _seed_data(database)
    factory = async_sessionmaker(database, expire_on_commit=False, autoflush=False)

    search_port = PostgresqlEvidenceSearchAdapter(
        factory, ImmutableArtifactRef("postgresql-evidence-search-adapter", "1.0.0", "a" * 64)
    )
    run_store = SqlAlchemyRetrievalRunStore(session_factory=factory)
    eligibility_verifier = PostgreSqlEvidenceEligibilityVerifier(session_factory=factory)
    embedding_adapter = DeterministicFakeEmbeddingAdapter(dimensions=1536)

    # Create dummy job_id and prescription_id in ai_job table to satisfy foreign key
    job_id = uuid4()
    context_id = uuid4()
    prescription_id = uuid4()
    async with database.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO ai_job (id, status, job_type, created_at, updated_at) "
                "VALUES (:id, 'PENDING', 'RETRIEVAL', :now, :now)"
            ),
            {"id": str(job_id), "now": _NOW},
        )

    retrieval_cfg = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef(id="ret-h-config", version="1.0.0", hash="a" * 64),
        execution_mode=RetrievalExecutionMode.HYBRID,
        lexical_config=VersionedLexicalSearchConfiguration(
            artifact_ref=ImmutableArtifactRef(id="lex-cfg", version="1.0.0", hash="b" * 64),
            candidate_limit=5,
            trigram_similarity_threshold=0.3,
        ),
        dense_config=VersionedDenseSearchConfiguration(
            artifact_ref=ImmutableArtifactRef(id="dense-cfg", version="1.0.0", hash="c" * 64),
            candidate_limit=5,
            distance_threshold=0.9,
        ),
        expected_query_embedding_adapter_ref=ImmutableArtifactRef(id="fake-embed", version="1.0.0", hash="d" * 64),
        algorithm_id="RRF_HYBRID_SEARCH",
        rrf_k=60,
    )

    binding = EvidenceSearchExecutionBinding(
        retrieval_config=retrieval_cfg,
        filter_snapshot_ref=ImmutableArtifactRef(id="snapshot", version="1.0.0", hash="e" * 64),
        evidence_index_ref=ImmutableArtifactRef(id="index", version="1.0.0", hash="f" * 64),
    )

    request = HybridRetrieveRequest(
        search_request=EvidenceSearchRequest(
            normalized_query=SensitiveText("아세트아미노펜 복용법"),
            query_fingerprint=QueryFingerprint("f" * 64),
            execution_binding=binding,
        ),
        job_id=job_id,
        execution_context_id=context_id,
        prescription_version_id=prescription_id,
        runtime_release_bundle_id=uuid4(),
        runtime_release_bundle_manifest_hash="1" * 64,
        runtime_execution_manifest_id=uuid4(),
        runtime_execution_manifest_hash="2" * 64,
        runtime_guard_decision_ref="decision-1",
        evidence_index_id=index_id,
        filter_snapshot_id=snapshot_id,
    )

    # Transaction 1: execute_hybrid_retrieve
    outcome = await execute_hybrid_retrieve(
        request,
        search_port=search_port,
        text_embedding_port=embedding_adapter,
        run_store=run_store,
        eligibility_verifier=eligibility_verifier,
    )

    assert outcome.status == RetrievalExecutionStatus.SUCCEEDED
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
