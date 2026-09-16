from __future__ import annotations

import asyncio
import json
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

from ai_worker.adapters.postgresql_evidence_eligibility import (
    PostgreSqlEvidenceEligibilityVerifier,
)
from ai_worker.adapters.sqlalchemy_retrieval_run import SqlAlchemyRetrievalRunStore
from ai_worker.tasks.rag.production_evidence_gate import (
    EvidenceGateReason,
    PostSearchEligibilityRequest,
    PostSearchEligibilitySuccess,
    PreSearchEligibilityFailure,
    PreSearchEligibilityRequest,
    PreSearchEligibilitySuccess,
)
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
from app.core import config  # type: ignore[attr-defined]

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
                'INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin) '
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
        hit_count = (
            await conn.execute(text(f"SELECT COUNT(*) FROM retrieval_hit WHERE retrieval_run_id = '{run_id}'"))
        ).scalar()
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
            text(
                f"UPDATE retrieval_run SET receipt_hash = '0000000000000000000000000000000000000000000000000000000000000000' WHERE id = '{run_id}'"
            )
        )

    # Reloading corrupt receipt fails closed
    receipt = await store.get_run_receipt(run_id)
    assert receipt is None

    # Resuming corrupt terminal run fails closed with DEPENDENCY_ERROR
    resume_res = await store.begin_run(req)
    assert isinstance(resume_res, BeginRetrievalRunFailure)
    assert resume_res.reason == BeginRetrievalRunFailureReason.DEPENDENCY_ERROR


async def test_postgresql_evidence_eligibility_verifier(database) -> None:
    engine = database
    job_id, ctx_id, index_id, chunk_id, _ = await _seed_test_prerequisites(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    verifier = PostgreSqlEvidenceEligibilityVerifier(factory)

    # 1. Pre-search check on valid index passes
    pre_res = await verifier.pre_search(PreSearchEligibilityRequest(knowledge_index_id=index_id))
    assert isinstance(pre_res, PreSearchEligibilitySuccess)
    assert pre_res.is_eligible is True

    # 2. Pre-search check on non-existent index fails with INVALID_BINDING
    pre_fail = await verifier.pre_search(PreSearchEligibilityRequest(knowledge_index_id=uuid4()))
    assert isinstance(pre_fail, PreSearchEligibilityFailure)
    assert pre_fail.reason == EvidenceGateReason.INVALID_BINDING

    # 3. Post-search empty hits returns empty eligible set
    post_empty = await verifier.post_search(PostSearchEligibilityRequest(knowledge_index_id=index_id), ())
    assert isinstance(post_empty, PostSearchEligibilitySuccess)
    assert len(post_empty.eligible_chunk_ids) == 0


async def test_begin_run_concurrent_initial_creation_race(database) -> None:
    engine = database
    job_id, ctx_id, index_id, _, _ = await _seed_test_prerequisites(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    store = SqlAlchemyRetrievalRunStore(factory)

    req = _create_begin_request(job_id, ctx_id, index_id)

    res1, res2 = await asyncio.gather(
        store.begin_run(req),
        store.begin_run(req),
    )

    assert isinstance(res1, BeginRetrievalRunSuccess)
    assert isinstance(res2, BeginRetrievalRunSuccess)
    assert res1.run_id == res2.run_id
    assert sorted([res1.is_resumed, res2.is_resumed]) == [False, True]


async def test_legacy_persisted_retrieval_run_postgresql_replay(database) -> None:
    """Verify replay compatibility of legacy persisted retrieval run using pure SQL INSERT.

    Ensures that historical retrieval runs recorded before RFC 8785 canonical JCS
    alignment can be cleanly replayed, verified, and resumed from PostgreSQL tables
    without modifying database rows, and that any tampering fails closed.
    """
    engine = database

    # Fixed legacy UUIDs and pre-computed golden values
    chunk_id = UUID("11111111-1111-1111-1111-111111111111")
    run_id = UUID("22222222-2222-2222-2222-222222222222")
    job_id = UUID("33333333-3333-3333-3333-333333333333")
    user_id = UUID("44444444-4444-4444-4444-444444444444")
    ctx_id = UUID("55555555-5555-5555-5555-555555555555")
    index_id = UUID("66666666-6666-6666-6666-666666666666")
    doc_id = UUID("77777777-7777-7777-7777-777777777777")
    prescription_version_id = UUID("88888888-8888-8888-8888-888888888888")
    runtime_release_bundle_id = UUID("99999999-9999-9999-9999-999999999999")
    runtime_execution_manifest_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    node_id = "hybrid_retrieve"
    variant = "RET-H"
    query_digest_algorithm = "sha256"
    query_digest_key_version = "v1"
    query_digest = "4" * 64
    retrieval_configuration_hash = "5" * 64
    source_manifest_hash = "6" * 64
    search_receipt_hash = "7" * 64
    filter_snapshot_hash = "8" * 64
    bundle_manifest_hash = "3" * 64
    execution_manifest_hash = "4" * 64
    query_embedding_sha256 = "7" * 64
    filter_snapshot = {"code": "ASPIRIN"}

    # Frozen legacy expected values (identical to test_legacy_digest_golden_regression_frozen_constants)
    frozen_legacy_signal_hash = "80949196c75ff5de674d05d4971c35c540e941ca51750d05914a9524170825e7"
    frozen_legacy_hit_hash = "1f15571ed69526a78f7d4f341a746e55b2a0f368efc6700866b24fb345d2314b"
    frozen_legacy_receipt_hash = "094833a6c8ce15863990257d143c55c6626ac879a3b09d9adf41d2a8b2b7947e"

    async with engine.begin() as conn:
        # 1. Foreign key prerequisites
        await conn.execute(
            text(
                'INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin) '
                "VALUES (:id, :email, 'hash', '테스트', true, false)"
            ),
            {"id": str(user_id), "email": f"legacy-replay-{uuid4().hex[:8]}@example.com"},
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
            {"id": str(doc_id), "url": f"https://example.com/legacy-{uuid4()}"},
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_chunk (id, knowledge_document_id, chunk_index, chunk_text, "
                "content_hash, normalization_version) "
                "VALUES (:id, :doc_id, 0, '아스피린 복용 안내', :h, 'v1')"
            ),
            {"id": str(chunk_id), "doc_id": str(doc_id), "h": "b" * 64},
        )

        # 2. Pure SQL INSERT into retrieval_run (no current writer used)
        await conn.execute(
            text(
                "INSERT INTO retrieval_run ("
                "id, job_id, execution_context_id, prescription_version_id, "
                "runtime_release_bundle_id, runtime_release_bundle_manifest_hash, "
                "runtime_execution_manifest_id, runtime_execution_manifest_hash, "
                "runtime_guard_decision_ref, knowledge_index_id, node_id, variant, "
                "query_digest_algorithm, query_digest_key_version, query_digest, "
                "filter_snapshot, filter_snapshot_hash, source_manifest_hash, "
                "retrieval_configuration_hash, query_embedding_sha256, "
                "lexical_limit, dense_limit, hybrid_limit, final_k, "
                "status, diagnostic_code, error_code, search_receipt_hash, receipt_hash, "
                "started_at, completed_at"
                ") VALUES ("
                ":id, :job_id, :execution_context_id, :prescription_version_id, "
                ":runtime_release_bundle_id, :bundle_hash, "
                ":runtime_execution_manifest_id, :execution_manifest_hash, "
                ":guard_ref, :knowledge_index_id, :node_id, :variant, "
                ":query_algo, :query_key_ver, :query_digest, "
                ":filter_snapshot, :filter_snapshot_hash, :source_manifest_hash, "
                ":retrieval_configuration_hash, :query_embedding_sha256, "
                "20, 20, 30, 5, "
                "'COMPLETED', 'OK', NULL, :search_receipt_hash, :receipt_hash, "
                "NOW(), NOW()"
                ")"
            ),
            {
                "id": str(run_id),
                "job_id": str(job_id),
                "execution_context_id": str(ctx_id),
                "prescription_version_id": str(prescription_version_id),
                "runtime_release_bundle_id": str(runtime_release_bundle_id),
                "bundle_hash": bundle_manifest_hash,
                "runtime_execution_manifest_id": str(runtime_execution_manifest_id),
                "execution_manifest_hash": execution_manifest_hash,
                "guard_ref": "ref-123",
                "knowledge_index_id": str(index_id),
                "node_id": node_id,
                "variant": variant,
                "query_algo": query_digest_algorithm,
                "query_key_ver": query_digest_key_version,
                "query_digest": query_digest,
                "filter_snapshot": json.dumps(filter_snapshot),
                "filter_snapshot_hash": filter_snapshot_hash,
                "source_manifest_hash": source_manifest_hash,
                "retrieval_configuration_hash": retrieval_configuration_hash,
                "query_embedding_sha256": query_embedding_sha256,
                "search_receipt_hash": search_receipt_hash,
                "receipt_hash": frozen_legacy_receipt_hash,
            },
        )

        # 3. Pure SQL INSERT into retrieval_signal
        await conn.execute(
            text(
                "INSERT INTO retrieval_signal ("
                "retrieval_run_id, retrieval_method, knowledge_chunk_id, raw_rank, raw_score, score_projection_version"
                ") VALUES ("
                ":run_id, 'EXACT', :chunk_id, 1, 1.000000000000000000, 'v1'"
                ")"
            ),
            {"run_id": str(run_id), "chunk_id": str(chunk_id)},
        )

        # 4. Pure SQL INSERT into retrieval_hit
        await conn.execute(
            text(
                "INSERT INTO retrieval_hit ("
                "retrieval_run_id, knowledge_chunk_id, lexical_rank, dense_rank, rrf_rank, "
                "rrf_score, rrf_score_numerator, rrf_score_denominator, rerank_score, final_rank, selected"
                ") VALUES ("
                ":run_id, :chunk_id, 1, NULL, 1, "
                "0.016393442622950820, '1', '61', NULL, 1, true"
                ")"
            ),
            {"run_id": str(run_id), "chunk_id": str(chunk_id)},
        )

    # Helper to capture exact DB rows
    async def snapshot_db_rows() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        async with engine.connect() as conn:
            r_run = (
                (await conn.execute(text("SELECT * FROM retrieval_run WHERE id = :id"), {"id": str(run_id)}))
                .mappings()
                .one()
            )
            r_sig = (
                (
                    await conn.execute(
                        text("SELECT * FROM retrieval_signal WHERE retrieval_run_id = :id"), {"id": str(run_id)}
                    )
                )
                .mappings()
                .one()
            )
            r_hit = (
                (
                    await conn.execute(
                        text("SELECT * FROM retrieval_hit WHERE retrieval_run_id = :id"), {"id": str(run_id)}
                    )
                )
                .mappings()
                .one()
            )
            return dict(r_run), dict(r_sig), dict(r_hit)

    before_run, before_sig, before_hit = await snapshot_db_rows()

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    store = SqlAlchemyRetrievalRunStore(factory)

    # 5. Read via get_run_receipt() and verify matching hashes
    receipt = await store.get_run_receipt(run_id)
    assert receipt is not None
    assert receipt.receipt_hash == frozen_legacy_receipt_hash
    assert receipt.signal_manifest_hash == frozen_legacy_signal_hash
    assert receipt.hit_manifest_hash == frozen_legacy_hit_hash

    # 6. Read via begin_run() with matching request (resume existing completed run)
    req = BeginRetrievalRunRequest(
        job_id=job_id,
        node_id=node_id,
        execution_context_id=ctx_id,
        prescription_version_id=prescription_version_id,
        runtime_release_bundle_id=runtime_release_bundle_id,
        runtime_release_bundle_manifest_hash=bundle_manifest_hash,
        runtime_execution_manifest_id=runtime_execution_manifest_id,
        runtime_execution_manifest_hash=execution_manifest_hash,
        runtime_guard_decision_ref="ref-123",
        knowledge_index_id=index_id,
        variant=variant,
        query_digest_algorithm=query_digest_algorithm,
        query_digest_key_version=query_digest_key_version,
        query_digest=query_digest,
        filter_snapshot=filter_snapshot,
        filter_snapshot_hash=filter_snapshot_hash,
        source_manifest_hash=source_manifest_hash,
        retrieval_configuration_hash=retrieval_configuration_hash,
        lexical_limit=20,
        dense_limit=20,
        hybrid_limit=30,
        final_k=5,
        query_embedding_sha256=query_embedding_sha256,
    )
    outcome = await store.begin_run(req)
    assert isinstance(outcome, BeginRetrievalRunSuccess)
    assert outcome.is_resumed is True
    assert outcome.existing_receipt is not None
    assert outcome.existing_receipt.receipt_hash == frozen_legacy_receipt_hash

    # 7. Verify byte/column level row immutability across reads
    after_run, after_sig, after_hit = await snapshot_db_rows()
    assert before_run == after_run
    assert before_sig == after_sig
    assert before_hit == after_hit

    # 8. Hash tampering causes fail-closed behavior
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE retrieval_run SET receipt_hash = :h WHERE id = :id"),
            {"id": str(run_id), "h": "f" * 64},
        )

    # get_run_receipt must fail-closed (return None)
    tampered_receipt = await store.get_run_receipt(run_id)
    assert tampered_receipt is None

    # begin_run must fail-closed (return DEPENDENCY_ERROR)
    tampered_begin = await store.begin_run(req)
    assert isinstance(tampered_begin, BeginRetrievalRunFailure)
    assert tampered_begin.reason == BeginRetrievalRunFailureReason.DEPENDENCY_ERROR
