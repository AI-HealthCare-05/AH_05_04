"""PostgreSQL integration coverage for the #178 RET-H AWS synthetic smoke verification.

These tests execute the real production ``SqlAlchemyRetrievalRunStore`` against a
migrated PostgreSQL database and then assert the smoke's *independent* read-only
verification session over the resulting rows. They pin the smoke's table and column
names to the real schema and prove that each fail-closed rule rejects a run whose
persisted state does not actually prove what a SUCCESS would claim.
"""

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
from ai_worker.tasks.evaluation.actual_retrieval_index import SYNTHETIC_INDEX_CODE
from ai_worker.tasks.evaluation.ret_h_smoke import (
    verify_fixture_is_synthetic,
    verify_persisted_receipt,
    verify_selected_candidates_carry_sentinel,
    verify_source_sentinel_indexed,
)
from ai_worker.tasks.rag.retrieval_run import (
    BeginRetrievalRunRequest,
    BeginRetrievalRunSuccess,
    FinalizeRetrievalRunRequest,
    FinalizeRetrievalRunSuccess,
    PersistedHitInput,
    PersistedSignalInput,
)
from ai_worker.tests.rag.retrieval_run_test_support import make_terminal_replay_payload
from app.core import config  # type: ignore[attr-defined]
from app.release_validation.ret_h_synthetic_smoke import run_verification_transaction

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio


def _alembic_config() -> Config:
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    return alembic_config


@pytest_asyncio.fixture
async def database(monkeypatch):
    name = "ret_h_smoke_178_" + uuid4().hex
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


async def _seed_synthetic_fixture(engine) -> tuple[UUID, UUID, UUID, UUID]:
    """Seed the minimal synthetic prerequisites. No real patient record is used."""
    user_id, job_id, ctx_id, index_id = uuid4(), uuid4(), uuid4(), uuid4()
    doc_id, chunk_id = uuid4(), uuid4()

    async with engine.begin() as conn:
        await conn.execute(
            text(
                'INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin) '
                "VALUES (:id, :email, 'hash', 'RETH스모크', true, false)"
            ),
            {"id": str(user_id), "email": f"ret-h-smoke-{uuid4().hex[:8]}@example.invalid"},
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
                "VALUES (:id, 'RET_H_AWS_SYNTHETIC_SMOKE', '1.0', :h, :h, :h, "
                "'text-embedding-3-large', '1.0', 1536, 'COSINE', 1)"
            ),
            {"id": str(index_id), "h": "a" * 64},
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_document (id, title, source_url, document_version, document_status, "
                "record_contract_version, publisher) "
                "VALUES (:id, 'Synthetic Smoke Doc', :url, '1.0', 'ACTIVE', 'LEGACY_V1', 'Synthetic')"
            ),
            {"id": str(doc_id), "url": f"https://example.invalid/{uuid4()}"},
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_chunk (id, knowledge_document_id, chunk_index, chunk_text, "
                "content_hash, normalization_version) "
                "VALUES (:id, :doc_id, 0, 'synthetic smoke statement', :h, 'v1')"
            ),
            {"id": str(chunk_id), "doc_id": str(doc_id), "h": "b" * 64},
        )
    return job_id, ctx_id, index_id, chunk_id


def _begin_request(job_id: UUID, ctx_id: UUID, index_id: UUID, *, variant: str = "RET-H") -> BeginRetrievalRunRequest:
    return BeginRetrievalRunRequest(
        job_id=job_id,
        node_id="hybrid_retrieve",
        execution_context_id=ctx_id,
        prescription_version_id=uuid4(),
        runtime_release_bundle_id=uuid4(),
        runtime_release_bundle_manifest_hash="3" * 64,
        runtime_execution_manifest_id=uuid4(),
        runtime_execution_manifest_hash="4" * 64,
        runtime_guard_decision_ref="ret-h-aws-synthetic-smoke",
        knowledge_index_id=index_id,
        variant=variant,
        query_digest_algorithm="sha256",
        query_digest_key_version="v1",
        query_digest="1" * 64,
        filter_snapshot={"fixture": "RET_H_AWS_SYNTHETIC_SMOKE"},
        filter_snapshot_hash="5" * 64,
        source_manifest_hash="6" * 64,
        retrieval_configuration_hash="2" * 64,
        lexical_limit=20,
        dense_limit=20,
        hybrid_limit=30,
        final_k=5,
        query_embedding_sha256="7" * 64,
    )


def _signals(chunk_id: UUID, methods: tuple[str, ...]) -> tuple[PersistedSignalInput, ...]:
    return tuple(
        PersistedSignalInput(
            knowledge_chunk_id=chunk_id,
            method=method,
            raw_rank=rank,
            raw_score=Decimal("0.5"),
            score_projection_version="observed-stage-score-decimal@1",
        )
        for rank, method in enumerate(methods, start=1)
    )


def _hits(chunk_id: UUID, *, selected: bool) -> tuple[PersistedHitInput, ...]:
    return (
        PersistedHitInput(
            knowledge_chunk_id=chunk_id,
            lexical_rank=1,
            dense_rank=1,
            rrf_rank=1,
            rrf_score=Decimal("0.032786885245901639"),
            rrf_score_numerator="2",
            rrf_score_denominator="61",
            rerank_score=None,
            final_rank=1,
            selected=selected,
        ),
    )


async def _persist_run(
    engine,
    *,
    variant: str = "RET-H",
    status: str = "COMPLETED",
    methods: tuple[str, ...] = ("LEXICAL", "DENSE"),
    selected: bool = True,
):
    job_id, ctx_id, index_id, chunk_id = await _seed_synthetic_fixture(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    store = SqlAlchemyRetrievalRunStore(factory)

    begin_request = _begin_request(job_id, ctx_id, index_id, variant=variant)
    begun = await store.begin_run(begin_request)
    assert isinstance(begun, BeginRetrievalRunSuccess)
    signals = _signals(chunk_id, methods) if status == "COMPLETED" else ()
    hits = _hits(chunk_id, selected=selected) if status == "COMPLETED" else ()
    terminal_payload = None
    search_receipt_hash = None
    diagnostic_code = None
    if status == "COMPLETED":
        terminal_payload, search_receipt_hash, diagnostic_code = make_terminal_replay_payload(
            begin_request,
            index_id=index_id,
            signals=signals,
            hits=hits,
        )

    finalized = await store.finalize_run(
        FinalizeRetrievalRunRequest(
            run_id=begun.run_id,
            status=status,
            diagnostic_code=diagnostic_code,
            search_receipt_hash=search_receipt_hash,
            signals=signals,
            hits=hits,
            terminal_replay_payload=terminal_payload,
        )
    )
    assert isinstance(finalized, FinalizeRetrievalRunSuccess), finalized
    return factory, finalized.receipt


async def test_independent_session_verifies_a_real_completed_ret_h_run(database) -> None:
    factory, receipt = await _persist_run(database)

    details = await run_verification_transaction(
        session_factory=factory,
        run_id=str(receipt.run_id),
        expected_receipt_hash=receipt.receipt_hash,
    )

    assert details["status"] == "COMPLETED"
    assert details["variant"] == "RET-H"
    assert set(details["signal_methods"]) == {"LEXICAL", "DENSE"}
    assert details["selected_hits_count"] == 1
    # The canonical production receipt verifier agrees with the persisted row.
    assert verify_persisted_receipt(receipt) is True


async def test_receipt_mismatch_is_rejected_against_real_rows(database) -> None:
    factory, receipt = await _persist_run(database)
    with pytest.raises(AssertionError, match="receipt_hash mismatch"):
        await run_verification_transaction(
            session_factory=factory,
            run_id=str(receipt.run_id),
            expected_receipt_hash="0" * 64,
        )


async def test_failed_terminal_status_is_rejected(database) -> None:
    factory, receipt = await _persist_run(database, status="FAILED")
    with pytest.raises(AssertionError, match="expected COMPLETED"):
        await run_verification_transaction(
            session_factory=factory,
            run_id=str(receipt.run_id),
            expected_receipt_hash=receipt.receipt_hash,
        )


async def test_non_ret_h_variant_is_rejected(database) -> None:
    factory, receipt = await _persist_run(database, variant="RET-L")
    with pytest.raises(AssertionError, match="expected RET-H"):
        await run_verification_transaction(
            session_factory=factory,
            run_id=str(receipt.run_id),
            expected_receipt_hash=receipt.receipt_hash,
        )


@pytest.mark.parametrize(
    ("methods", "match"),
    [(("DENSE",), "No lexical retrieval_signal"), (("LEXICAL",), "No dense retrieval_signal")],
)
async def test_missing_signal_family_is_rejected(database, methods: tuple[str, ...], match: str) -> None:
    factory, receipt = await _persist_run(database, methods=methods)
    with pytest.raises(AssertionError, match=match):
        await run_verification_transaction(
            session_factory=factory,
            run_id=str(receipt.run_id),
            expected_receipt_hash=receipt.receipt_hash,
        )


async def test_run_without_a_selected_hit_is_rejected(database) -> None:
    factory, receipt = await _persist_run(database, selected=False)
    with pytest.raises(AssertionError, match="No selected retrieval_hit"):
        await run_verification_transaction(
            session_factory=factory,
            run_id=str(receipt.run_id),
            expected_receipt_hash=receipt.receipt_hash,
        )


async def test_unknown_run_id_is_rejected(database) -> None:
    factory, _ = await _persist_run(database)
    with pytest.raises(AssertionError, match="retrieval_run row not found"):
        await run_verification_transaction(
            session_factory=factory,
            run_id=str(uuid4()),
            expected_receipt_hash="9" * 64,
        )


# --------------------------------------------------------------------------------------
# Synthetic-fixture authenticity and Source sentinel binding against real rows
# --------------------------------------------------------------------------------------

SOURCE_SENTINEL = "RET_H_SMOKE_S_integration0001"


async def _seed_index(engine, *, index_code: str, chunk_text: str) -> tuple[UUID, UUID, UUID, UUID]:
    """Create one Knowledge Index with a single member chunk over a real Source snapshot.

    The snapshot is left ``PENDING``: sealing a ``CURRENT`` snapshot needs a verification
    row, and neither check under test reads verification_status. These tests cover index
    authenticity and Source sentinel presence only.
    """
    index_id, doc_id, chunk_id = uuid4(), uuid4(), uuid4()
    source_id, endpoint_id, operation_id = uuid4(), uuid4(), uuid4()
    snapshot_id, snapshot_member_id = uuid4(), uuid4()
    digest = "c" * 64

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO rag_source (id, source_code, display_name, lifecycle_status) "
                "VALUES (:id, :code, 'Synthetic Smoke Source', 'ACTIVE')"
            ),
            {"id": str(source_id), "code": f"SYNTHETIC_DEV_{uuid4().hex[:8]}"},
        )
        await conn.execute(
            text(
                "INSERT INTO rag_source_endpoint (id, source_id, endpoint_code, display_name, "
                "lifecycle_status, runtime_status, acquisition_status) "
                "VALUES (:id, :src, 'SYNTHETIC_SMOKE_ENDPOINT', 'Synthetic', 'VERIFIED', 'ENABLED', 'APPROVED')"
            ),
            {"id": str(endpoint_id), "src": str(source_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO rag_source_operation (id, endpoint_id, operation_code, display_name, "
                "runtime_status, acquisition_status) "
                "VALUES (:id, :ep, 'SYNTHETIC_SMOKE_RECORDS', 'Synthetic', 'ENABLED', 'APPROVED')"
            ),
            {"id": str(operation_id), "ep": str(endpoint_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO rag_source_snapshot (id, operation_id, source_version, raw_manifest_checksum, "
                "canonical_checksum, schema_version, parser_version, normalization_version, "
                "canonicalization_spec_version, record_count, rejected_record_count, verification_status, "
                "collected_at) VALUES (:id, :op, '1.0', :h, :h, '1.0', '1.0', 'v1', 'canonical-v1', 1, 0, "
                "'PENDING', now())"
            ),
            {"id": str(snapshot_id), "op": str(operation_id), "h": digest},
        )
        await conn.execute(
            text(
                "INSERT INTO rag_source_snapshot_member (id, source_snapshot_id, member_kind, endpoint_id, "
                "operation_id, locator, content_sha256) "
                "VALUES (:id, :snap, 'ENDPOINT_OPERATION', :ep, :op, '$.records[0]', :h)"
            ),
            {
                "id": str(snapshot_member_id),
                "snap": str(snapshot_id),
                "ep": str(endpoint_id),
                "op": str(operation_id),
                "h": digest,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO rag_knowledge_index (id, index_code, index_version, corpus_manifest_hash, "
                "embedding_manifest_hash, index_configuration_hash, embedding_model_ref, "
                "embedding_model_version, embedding_dimension, distance_metric, member_count) "
                "VALUES (:id, :code, :ver, :h, :h, :h, 'text-embedding-3-large', '1.0', 1536, 'COSINE', 1)"
            ),
            # A unique version keeps repeated seeds inside one test distinct; the
            # authenticity check keys on index_code, not version.
            {"id": str(index_id), "code": index_code, "ver": uuid4().hex[:8], "h": "a" * 64},
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_document (id, title, source_url, document_version, document_status, "
                "record_contract_version, publisher) "
                "VALUES (:id, 'Synthetic', :url, '1.0', 'ACTIVE', 'LEGACY_V1', 'Synthetic')"
            ),
            {"id": str(doc_id), "url": f"https://example.invalid/{uuid4()}"},
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_chunk (id, knowledge_document_id, chunk_index, chunk_text, "
                "content_hash, normalization_version) VALUES (:id, :doc, 0, :body, :h, 'v1')"
            ),
            {"id": str(chunk_id), "doc": str(doc_id), "body": chunk_text, "h": "b" * 64},
        )
        await conn.execute(
            text(
                "INSERT INTO rag_knowledge_index_member (id, knowledge_index_id, knowledge_chunk_id, "
                "evidence_key, "
                "source_snapshot_id, source_snapshot_member_id, source_code, source_version, "
                "canonical_checksum, external_document_id, chunk_index, content_hash, member_order, embedding, "
                "embedding_sha256) "
                "VALUES (:id, :idx, :chunk, :evidence_key, :snap, :snapm, 'SYNTHETIC_DEV', '1.0', :h, 'doc-1', 0, :h, 1, "
                ":vec, :h)"
            ),
            {
                "id": str(uuid4()),
                "idx": str(index_id),
                "chunk": str(chunk_id),
                "evidence_key": "synthetic-ret-h-aws-evidence-1",
                "snap": str(snapshot_id),
                "snapm": str(snapshot_member_id),
                "h": digest,
                "vec": "[" + ",".join(["0.0"] * 1536) + "]",
            },
        )
    return index_id, snapshot_id, snapshot_member_id, chunk_id


async def test_approved_synthetic_index_is_accepted(database) -> None:
    index_id, snapshot_id, member_id, chunk_id = await _seed_index(
        database, index_code=SYNTHETIC_INDEX_CODE, chunk_text=f"본문 {SOURCE_SENTINEL} 입니다"
    )
    factory = async_sessionmaker(database, expire_on_commit=False, autoflush=False)

    genuine, _ = await verify_fixture_is_synthetic(
        session_factory=factory, knowledge_index_id=index_id, allowed_source_snapshot_ids=(snapshot_id,)
    )
    assert genuine is True

    bound, _ = await verify_source_sentinel_indexed(
        session_factory=factory,
        knowledge_index_id=index_id,
        source_sentinel=SOURCE_SENTINEL,
        allowed_source_snapshot_member_ids=(member_id,),
    )
    assert bound is True


async def test_non_synthetic_index_code_is_rejected(database) -> None:
    index_id, snapshot_id, member_id, chunk_id = await _seed_index(
        database, index_code="mfds-production-index", chunk_text=f"본문 {SOURCE_SENTINEL}"
    )
    factory = async_sessionmaker(database, expire_on_commit=False, autoflush=False)
    genuine, message = await verify_fixture_is_synthetic(
        session_factory=factory, knowledge_index_id=index_id, allowed_source_snapshot_ids=(snapshot_id,)
    )
    assert genuine is False
    assert "not an approved synthetic index" in message


async def test_unknown_index_is_rejected(database) -> None:
    factory = async_sessionmaker(database, expire_on_commit=False, autoflush=False)
    genuine, message = await verify_fixture_is_synthetic(
        session_factory=factory, knowledge_index_id=uuid4(), allowed_source_snapshot_ids=(uuid4(),)
    )
    assert genuine is False
    assert "does not exist" in message


async def test_member_snapshot_outside_the_allow_list_is_rejected(database) -> None:
    index_id, _, member_id, chunk_id = await _seed_index(
        database, index_code=SYNTHETIC_INDEX_CODE, chunk_text=f"본문 {SOURCE_SENTINEL}"
    )
    factory = async_sessionmaker(database, expire_on_commit=False, autoflush=False)
    genuine, message = await verify_fixture_is_synthetic(
        session_factory=factory, knowledge_index_id=index_id, allowed_source_snapshot_ids=(uuid4(),)
    )
    assert genuine is False
    assert "outside the declared allow-list" in message


async def test_source_sentinel_absent_from_the_corpus_is_rejected(database) -> None:
    index_id, _, member_id, chunk_id = await _seed_index(
        database, index_code=SYNTHETIC_INDEX_CODE, chunk_text="본문에 sentinel 이 없다"
    )
    factory = async_sessionmaker(database, expire_on_commit=False, autoflush=False)
    bound, message = await verify_source_sentinel_indexed(
        session_factory=factory,
        knowledge_index_id=index_id,
        source_sentinel=SOURCE_SENTINEL,
        allowed_source_snapshot_member_ids=(member_id,),
    )
    assert bound is False
    assert "no allowed indexed chunk carries" in message


async def test_like_wildcards_do_not_satisfy_the_source_binding(database) -> None:
    """``_`` and ``%`` must be literal. A LIKE-based check would accept this corpus."""
    index_id, _, member_id, _chunk = await _seed_index(
        database, index_code=SYNTHETIC_INDEX_CODE, chunk_text="본문 RET_H_SMOKE_SXintegration0001 입니다"
    )
    factory = async_sessionmaker(database, expire_on_commit=False, autoflush=False)
    bound, message = await verify_source_sentinel_indexed(
        session_factory=factory,
        knowledge_index_id=index_id,
        # Underscores here would be single-character wildcards under LIKE.
        source_sentinel="RET_H_SMOKE_S_integration0001",
        allowed_source_snapshot_member_ids=(member_id,),
    )
    assert bound is False
    assert "no allowed indexed chunk carries" in message


async def test_marker_outside_the_allowed_members_does_not_bind(database) -> None:
    index_id, _, _member, _chunk = await _seed_index(
        database, index_code=SYNTHETIC_INDEX_CODE, chunk_text=f"본문 {SOURCE_SENTINEL} 입니다"
    )
    factory = async_sessionmaker(database, expire_on_commit=False, autoflush=False)
    bound, message = await verify_source_sentinel_indexed(
        session_factory=factory,
        knowledge_index_id=index_id,
        source_sentinel=SOURCE_SENTINEL,
        # The marker exists in the index, but not under any declared member.
        allowed_source_snapshot_member_ids=(uuid4(),),
    )
    assert bound is False
    assert "no allowed indexed chunk carries" in message


async def test_selected_candidates_must_carry_the_source_sentinel(database) -> None:
    index_id, _, _member, chunk_id = await _seed_index(
        database, index_code=SYNTHETIC_INDEX_CODE, chunk_text=f"본문 {SOURCE_SENTINEL} 입니다"
    )
    other_index, _, _m2, other_chunk = await _seed_index(
        database, index_code=SYNTHETIC_INDEX_CODE, chunk_text="marker 없는 본문"
    )
    assert other_index != index_id
    factory = async_sessionmaker(database, expire_on_commit=False, autoflush=False)

    ok, _ = await verify_selected_candidates_carry_sentinel(
        session_factory=factory, selected_chunk_ids=(chunk_id,), source_sentinel=SOURCE_SENTINEL
    )
    assert ok is True

    bad, message = await verify_selected_candidates_carry_sentinel(
        session_factory=factory,
        selected_chunk_ids=(chunk_id, other_chunk),
        source_sentinel=SOURCE_SENTINEL,
    )
    assert bad is False
    assert "selected candidate does not carry" in message


async def test_no_selected_candidate_is_never_a_pass(database) -> None:
    factory = async_sessionmaker(database, expire_on_commit=False, autoflush=False)
    ok, message = await verify_selected_candidates_carry_sentinel(
        session_factory=factory, selected_chunk_ids=(), source_sentinel=SOURCE_SENTINEL
    )
    assert ok is False
    assert "no selected candidate" in message
