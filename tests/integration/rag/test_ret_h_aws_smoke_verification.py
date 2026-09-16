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
from ai_worker.tasks.evaluation.ret_h_smoke import verify_persisted_receipt
from ai_worker.tasks.rag.retrieval_run import (
    BeginRetrievalRunRequest,
    BeginRetrievalRunSuccess,
    FinalizeRetrievalRunRequest,
    FinalizeRetrievalRunSuccess,
    PersistedHitInput,
    PersistedSignalInput,
)
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

    begun = await store.begin_run(_begin_request(job_id, ctx_id, index_id, variant=variant))
    assert isinstance(begun, BeginRetrievalRunSuccess)

    finalized = await store.finalize_run(
        FinalizeRetrievalRunRequest(
            run_id=begun.run_id,
            status=status,
            search_receipt_hash="8" * 64,
            signals=_signals(chunk_id, methods),
            hits=_hits(chunk_id, selected=selected),
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
