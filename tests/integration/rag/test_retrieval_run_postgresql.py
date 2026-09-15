from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_retrieval_run import SqlAlchemyRetrievalRunStore
from ai_worker.tasks.rag.retrieval_run import (
    BeginRetrievalRunFailure,
    BeginRetrievalRunFailureReason,
    BeginRetrievalRunRequest,
    BeginRetrievalRunSuccess,
    FinalizeRetrievalRunFailure,
    FinalizeRetrievalRunFailureReason,
    FinalizeRetrievalRunRequest,
    FinalizeRetrievalRunSuccess,
    PersistedHitInput,
    PersistedSignalInput,
)
from app.core import config

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio


def _alembic_config() -> Config:
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    return alembic_config


@pytest_asyncio.fixture
async def database(monkeypatch):
    name = "retrieval_run_178_" + uuid4().hex
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


async def _seed_test_prerequisites(engine) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    user_id = uuid4()
    job_id = uuid4()
    ctx_id = uuid4()
    index_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO \"user\" (id, email, hashed_password, name, is_active, is_admin) "
                "VALUES (:id, :email, 'hash', '테스트', true, false)"
            ),
            {"id": str(user_id), "email": f"test-{uuid4().hex[:8]}@example.com"},
        )
        await conn.execute(
            text(
                "INSERT INTO ai_job (id, user_id, job_type, status, max_attempts, attempt_count) "
                "VALUES (:id, :uid, 'OCR', 'PENDING', 3, 0)"
            ),
            {"id": str(job_id), "uid": str(user_id)},
        )

        await conn.execute(
            text(
                "INSERT INTO rag_knowledge_index (id, index_code, index_version, corpus_manifest_hash, "
                "embedding_manifest_hash, index_configuration_hash, embedding_model_ref, "
                "embedding_model_version, embedding_dimension, distance_metric, member_count) "
                "VALUES (:id, 'TEST_IDX', '1.0', :h, :h, :h, 'text-embedding-3-large', '1.0', 1536, 'COSINE', 1)"
            ),
            {"id": str(index_id), "h": "a" * 64},
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_document (id, title, source_url, document_version, document_status, "
                "record_contract_version, publisher) "
                "VALUES (:id, 'Test Doc', :url, '1.0', 'ACTIVE', 'LEGACY_V1', 'Publisher')"
            ),
            {"id": str(doc_id), "url": f"https://example.com/{uuid4()}"},
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_chunk (id, knowledge_document_id, chunk_index, chunk_text, "
                "content_hash, normalization_version) "
                "VALUES (:id, :doc_id, 0, '아스피린 복용 안내', :h, 'v1')"
            ),
            {"id": str(chunk_id), "doc_id": str(doc_id), "h": "b" * 64},
        )

    return job_id, ctx_id, index_id, chunk_id, user_id


def _create_begin_request(
    job_id: UUID,
    ctx_id: UUID,
    index_id: UUID,
    *,
    node_id: str = "hybrid_retrieve",
    query_digest: str = "1" * 64,
    config_hash: str = "2" * 64,
) -> BeginRetrievalRunRequest:
    return BeginRetrievalRunRequest(
        job_id=job_id,
        node_id=node_id,
        execution_context_id=ctx_id,
        prescription_version_id=uuid4(),
        runtime_release_bundle_id=uuid4(),
        runtime_release_bundle_manifest_hash="3" * 64,
        runtime_execution_manifest_id=uuid4(),
        runtime_execution_manifest_hash="4" * 64,
        runtime_guard_decision_ref="ref-123",
        knowledge_index_id=index_id,
        variant="RET-H",
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
        query_embedding_sha256="7" * 64,
    )


async def test_begin_and_finalize_lifecycle_postgresql(database) -> None:
    engine = database
    job_id, ctx_id, index_id, chunk_id, _ = await _seed_test_prerequisites(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    store = SqlAlchemyRetrievalRunStore(factory)

    req = _create_begin_request(job_id, ctx_id, index_id)

    # 1. absent + matching request -> create RUNNING
    outcome1 = await store.begin_run(req)
    assert isinstance(outcome1, BeginRetrievalRunSuccess)
    assert outcome1.is_resumed is False
    run_id = outcome1.run_id

    # 2. RUNNING + same identity -> recover same run id
    outcome2 = await store.begin_run(req)
    assert isinstance(outcome2, BeginRetrievalRunSuccess)
    assert outcome2.is_resumed is True
    assert outcome2.run_id == run_id

    # 3. finalize success -> insert child rows then terminal status
    sig = PersistedSignalInput(
        knowledge_chunk_id=chunk_id,
        method="EXACT",
        raw_rank=1,
        raw_score=Decimal("1.0"),
        score_projection_version="observed-stage-score-decimal@1",
    )
    hit = PersistedHitInput(
        knowledge_chunk_id=chunk_id,
        rrf_rank=1,
        rrf_score=Decimal("0.016393442622950820"),
        rrf_score_numerator="1",
        rrf_score_denominator="61",
        final_rank=1,
        selected=True,
        lexical_rank=1,
        dense_rank=1,
    )
    fin_req = FinalizeRetrievalRunRequest(
        run_id=run_id,
        status="COMPLETED",
        search_receipt_hash="c" * 64,
        signals=(sig,),
        hits=(hit,),
    )
    fin_res = await store.finalize_run(fin_req)
    assert isinstance(fin_res, FinalizeRetrievalRunSuccess)
    assert fin_res.receipt.status == "COMPLETED"
    assert fin_res.receipt.total_signals == 1
    assert fin_res.receipt.total_hits == 1
    assert fin_res.receipt.selected_count == 1

    # 4. terminal + same identity -> return verified stored receipt
    outcome3 = await store.begin_run(req)
    assert isinstance(outcome3, BeginRetrievalRunSuccess)
    assert outcome3.is_resumed is True
    assert outcome3.existing_receipt is not None
    assert outcome3.existing_receipt.receipt_hash == fin_res.receipt.receipt_hash

    # 5. terminal + different identity -> conflict
    req_differing = _create_begin_request(job_id, ctx_id, index_id, query_digest="9" * 64)
    conflict_outcome = await store.begin_run(req_differing)
    assert isinstance(conflict_outcome, BeginRetrievalRunFailure)
    assert conflict_outcome.reason == BeginRetrievalRunFailureReason.CONFLICT


async def test_finalize_rollback_on_failure_preserves_running(database) -> None:
    engine = database
    job_id, ctx_id, index_id, chunk_id, _ = await _seed_test_prerequisites(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    store = SqlAlchemyRetrievalRunStore(factory)

    req = _create_begin_request(job_id, ctx_id, index_id)
    begin_res = await store.begin_run(req)
    assert isinstance(begin_res, BeginRetrievalRunSuccess)
    run_id = begin_res.run_id

    # Injected validation failure: selected=True with final_rank=6
    invalid_hit = PersistedHitInput(
        knowledge_chunk_id=chunk_id,
        rrf_rank=6,
        rrf_score=Decimal("0.01"),
        rrf_score_numerator="1",
        rrf_score_denominator="66",
        final_rank=6,
        selected=True,
    )
    fin_req = FinalizeRetrievalRunRequest(
        run_id=run_id,
        status="COMPLETED",
        hits=(invalid_hit,),
    )
    fin_res = await store.finalize_run(fin_req)
    assert isinstance(fin_res, FinalizeRetrievalRunFailure)
    assert fin_res.reason == FinalizeRetrievalRunFailureReason.VALIDATION_ERROR

    # Verify run remains in RUNNING status and no hits were inserted
    async with engine.connect() as conn:
        run_row = (await conn.execute(text(f"SELECT status FROM retrieval_run WHERE id = '{run_id}'"))).first()
        assert run_row[0] == "RUNNING"
        hit_count = (await conn.execute(text(f"SELECT COUNT(*) FROM retrieval_hit WHERE retrieval_run_id = '{run_id}'"))).scalar()
        assert hit_count == 0


async def test_corrupt_receipt_hash_fails_closed(database) -> None:
    engine = database
    job_id, ctx_id, index_id, chunk_id, _ = await _seed_test_prerequisites(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    store = SqlAlchemyRetrievalRunStore(factory)

    req = _create_begin_request(job_id, ctx_id, index_id)
    begin_res = await store.begin_run(req)
    assert isinstance(begin_res, BeginRetrievalRunSuccess)
    run_id = begin_res.run_id

    fin_req = FinalizeRetrievalRunRequest(
        run_id=run_id,
        status="COMPLETED",
        hits=(
            PersistedHitInput(
                knowledge_chunk_id=chunk_id,
                rrf_rank=1,
                rrf_score=Decimal("0.01"),
                rrf_score_numerator="1",
                rrf_score_denominator="61",
                final_rank=1,
                selected=True,
            ),
        ),
    )
    await store.finalize_run(fin_req)

    # Mutate receipt_hash in database to simulate tampering/corruption
    async with engine.begin() as conn:
        await conn.execute(
            text(f"UPDATE retrieval_run SET receipt_hash = '0000000000000000000000000000000000000000000000000000000000000000' WHERE id = '{run_id}'")
        )

    # Reloading corrupt receipt fails closed
    receipt = await store.get_run_receipt(run_id)
    assert receipt is None

    # Resuming corrupt terminal run fails closed with DEPENDENCY_ERROR
    resume_res = await store.begin_run(req)
    assert isinstance(resume_res, BeginRetrievalRunFailure)
    assert resume_res.reason == BeginRetrievalRunFailureReason.DEPENDENCY_ERROR
