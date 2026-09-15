from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_jobs import AiJob, AiJobStatus, AiJobType
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentContractVersion,
    KnowledgeDocumentStatus,
    RagKnowledgeIndex,
)
from app.models.rag_retrieval import RetrievalRunStatus, RetrievalRunVariant
from app.models.users import User
from app.repositories.rag_retrieval_repository import (
    RagRetrievalRepository,
    RetrievalHitCreate,
    RetrievalRunConflictError,
    RetrievalRunCreate,
    RetrievalRunFinalize,
    RetrievalRunNotFoundError,
    RetrievalRunValidationError,
    RetrievalSignalCreate,
)


async def _seed_prerequisites(db_session: AsyncSession) -> tuple[AiJob, RagKnowledgeIndex, KnowledgeChunk]:
    user = User(
        id=uuid4(),
        email=f"test-{uuid4().hex[:8]}@example.com",
        hashed_password="hash",
        name="테스트",
    )
    db_session.add(user)
    await db_session.flush()

    job = AiJob(
        id=uuid4(),
        user_id=user.id,
        job_type=AiJobType.OCR,
        status=AiJobStatus.PENDING,
        max_attempts=3,
        attempt_count=0,
    )
    db_session.add(job)

    index = RagKnowledgeIndex(
        id=uuid4(),
        index_code="TEST_INDEX",
        index_version="1.0",
        corpus_manifest_hash="a" * 64,
        embedding_manifest_hash="b" * 64,
        index_configuration_hash="c" * 64,
        embedding_model_ref="text-embedding-3-large",
        embedding_model_version="1.0",
        embedding_dimension=1536,
        distance_metric="COSINE",
        member_count=1,
    )
    db_session.add(index)

    doc = KnowledgeDocument(
        id=uuid4(),
        title="Test Document",
        source_url=f"https://example.com/doc/{uuid4()}",
        document_version="1.0",
        document_status=KnowledgeDocumentStatus.ACTIVE,
        record_contract_version=KnowledgeDocumentContractVersion.LEGACY_V1,
        publisher="Test Publisher",
    )
    db_session.add(doc)
    await db_session.flush()

    chunk = KnowledgeChunk(
        id=uuid4(),
        knowledge_document_id=doc.id,
        chunk_index=0,
        chunk_text="아스피린 복용 안내문",
        content_hash="d" * 64,
        normalization_version="v1",
    )
    db_session.add(chunk)
    await db_session.flush()

    return job, index, chunk


def _make_create_data(
    job_id: UUID,
    index_id: UUID,
    *,
    node_id: str = "hybrid_retrieve",
    query_digest: str = "1" * 64,
    config_hash: str = "2" * 64,
) -> RetrievalRunCreate:
    return RetrievalRunCreate(
        job_id=job_id,
        node_id=node_id,
        execution_context_id=uuid4(),
        prescription_version_id=uuid4(),
        runtime_release_bundle_id=uuid4(),
        runtime_release_bundle_manifest_hash="3" * 64,
        runtime_execution_manifest_id=uuid4(),
        runtime_execution_manifest_hash="4" * 64,
        runtime_guard_decision_ref="ref-123",
        knowledge_index_id=index_id,
        variant=RetrievalRunVariant.RET_H.value,
        query_digest_algorithm="sha256",
        query_digest_key_version="v1",
        query_digest=query_digest,
        filter_snapshot={"code": "ASPIRIN"},
        filter_snapshot_hash="5" * 64,
        source_manifest_hash="6" * 64,
        retrieval_configuration_hash=config_hash,
        lexical_limit=20,
        dense_limit=20,
        hybrid_limit=30,
        final_k=5,
    )


async def test_absent_matching_request_creates_running(db_session: AsyncSession) -> None:
    job, index, _ = await _seed_prerequisites(db_session)
    repo = RagRetrievalRepository(db_session)
    create_data = _make_create_data(job.id, index.id)

    run, created = await repo.begin_run(create_data)
    assert created is True
    assert run.status == RetrievalRunStatus.RUNNING
    assert run.job_id == job.id
    assert run.node_id == "hybrid_retrieve"


async def test_running_same_identity_recovers_same_run(db_session: AsyncSession) -> None:
    job, index, _ = await _seed_prerequisites(db_session)
    repo = RagRetrievalRepository(db_session)
    create_data = _make_create_data(job.id, index.id)

    run1, created1 = await repo.begin_run(create_data)
    assert created1 is True

    run2, created2 = await repo.begin_run(create_data)
    assert created2 is False
    assert run1.id == run2.id


async def test_differing_identity_raises_conflict(db_session: AsyncSession) -> None:
    job, index, _ = await _seed_prerequisites(db_session)
    repo = RagRetrievalRepository(db_session)
    create_data1 = _make_create_data(job.id, index.id, query_digest="1" * 64)
    run1, _ = await repo.begin_run(create_data1)

    create_data2 = _make_create_data(job.id, index.id, query_digest="9" * 64)
    with pytest.raises(RetrievalRunConflictError, match="already exists with differing identity"):
        await repo.begin_run(create_data2)


async def test_finalize_success_persists_children_and_terminal_status(db_session: AsyncSession) -> None:
    job, index, chunk = await _seed_prerequisites(db_session)
    repo = RagRetrievalRepository(db_session)
    create_data = _make_create_data(job.id, index.id)

    run, _ = await repo.begin_run(create_data)

    sig = RetrievalSignalCreate(
        knowledge_chunk_id=chunk.id,
        method="EXACT",
        raw_rank=1,
        raw_score=Decimal("1.0"),
        score_projection_version="observed-stage-score-decimal@1",
    )
    hit = RetrievalHitCreate(
        knowledge_chunk_id=chunk.id,
        rrf_rank=1,
        rrf_score=Decimal("0.016393442622950820"),
        rrf_score_numerator="1",
        rrf_score_denominator="61",
        final_rank=1,
        selected=True,
        lexical_rank=1,
        dense_rank=1,
    )

    finalize_data = RetrievalRunFinalize(
        status=RetrievalRunStatus.COMPLETED.value,
        search_receipt_hash="e" * 64,
        receipt_hash="f" * 64,
        signals=(sig,),
        hits=(hit,),
    )

    finalized = await repo.finalize_run(run.id, finalize_data)
    assert finalized.status == RetrievalRunStatus.COMPLETED
    assert finalized.receipt_hash == "f" * 64
    assert finalized.completed_at is not None

    signals = await repo.get_run_signals(run.id)
    assert len(signals) == 1
    assert signals[0].retrieval_method == "EXACT"

    hits = await repo.get_run_hits(run.id)
    assert len(hits) == 1
    assert hits[0].selected is True
    assert hits[0].final_rank == 1


async def test_finalize_invalid_selection_fails_validation(db_session: AsyncSession) -> None:
    job, index, chunk = await _seed_prerequisites(db_session)
    repo = RagRetrievalRepository(db_session)
    create_data = _make_create_data(job.id, index.id)
    run, _ = await repo.begin_run(create_data)

    # selected=True but final_rank=6 is invalid (must be <= 5)
    invalid_hit = RetrievalHitCreate(
        knowledge_chunk_id=chunk.id,
        rrf_rank=6,
        rrf_score=Decimal("0.01"),
        rrf_score_numerator="1",
        rrf_score_denominator="66",
        final_rank=6,
        selected=True,
    )
    finalize_data = RetrievalRunFinalize(
        status=RetrievalRunStatus.COMPLETED.value,
        search_receipt_hash="e" * 64,
        receipt_hash="f" * 64,
        hits=(invalid_hit,),
    )

    with pytest.raises(RetrievalRunValidationError, match="Selected hit final_rank cannot exceed 5"):
        await repo.finalize_run(run.id, finalize_data)


async def test_finalize_nonexistent_run_raises_not_found(db_session: AsyncSession) -> None:
    repo = RagRetrievalRepository(db_session)
    finalize_data = RetrievalRunFinalize(
        status=RetrievalRunStatus.COMPLETED.value,
        search_receipt_hash="e" * 64,
        receipt_hash="f" * 64,
    )
    with pytest.raises(RetrievalRunNotFoundError):
        await repo.finalize_run(uuid4(), finalize_data)
