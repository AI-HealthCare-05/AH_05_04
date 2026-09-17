"""Worker-side RET-H synthetic smoke composition root.

This module assembles the *real* production retrieval dependencies for the Issue
#178 AWS synthetic deployment smoke. It lives on the ``ai_worker`` side so that
``backend/app/release_validation/ret_h_synthetic_smoke.py`` keeps the PR #663
injection seam and never imports the worker runtime.

Nothing here introduces a new retrieval, evaluation or release subsystem: every
callable below is an assembly of primitives already merged for #178.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from typing import Any
from uuid import UUID

from sqlalchemy import text

from ai_worker.tasks.evaluation.actual_retrieval_index import SYNTHETIC_INDEX_CODE
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.evidence_search import ProductionSearchHit
from ai_worker.tasks.rag.production_evidence_gate import (
    EvidenceGateSuccess,
    PostSearchEligibilityRequest,
    PostSearchEligibilitySuccess,
    ProductionEvidenceEligibilityVerifierPort,
    evaluate_evidence_gate,
)
from ai_worker.tasks.rag.retrieval_run import (
    PersistedRetrievalRunReceipt,
    compute_receipt_hash,
)
from ai_worker.tasks.rag.retrieval_runtime import (
    PRODUCTION_EMBEDDING_DIMENSION,
    PRODUCTION_EMBEDDING_MODEL_REF,
    PRODUCTION_EMBEDDING_MODEL_VERSION,
    HybridRetrieveOutcome,
    HybridRetrieveRequest,
    execute_hybrid_retrieve,
)

SEARCH_ADAPTER_ARTIFACT_CODE = "postgresql-evidence-search-adapter"
EMBEDDING_ADAPTER_ARTIFACT_CODE = "openai-text-embedding-adapter"

STALE_SOURCE_VERSION_SUFFIX = "+ret-h-smoke-stale"
LOCATOR_MISMATCH_SUFFIX = "/ret-h-smoke-mismatch"


async def execute_ret_h_smoke_transaction(
    *,
    request: HybridRetrieveRequest,
    search_port: Any,
    text_embedding_port: Any,
    run_store: Any,
    eligibility_verifier: Any,
) -> HybridRetrieveOutcome:
    """Transaction 1: the production ``execute_hybrid_retrieve`` begin/finalize path."""
    return await execute_hybrid_retrieve(
        request,
        search_port=search_port,
        text_embedding_port=text_embedding_port,
        run_store=run_store,
        eligibility_verifier=eligibility_verifier,
    )


# --------------------------------------------------------------------------------------
# Live dependency assembly
# --------------------------------------------------------------------------------------


def build_session_factory(database_url: Any) -> tuple[Any, Any]:
    """Create an engine plus session factory for one runtime identity."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(database_url, hide_parameters=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def build_search_port(session_factory: Any, *, content_sha256: str) -> Any:
    from ai_worker.adapters.postgresql_evidence_search import PostgresqlEvidenceSearchAdapter

    return PostgresqlEvidenceSearchAdapter(
        session_factory,
        ImmutableArtifactRef(SEARCH_ADAPTER_ARTIFACT_CODE, "1.0.0", content_sha256),
    )


def build_eligibility_verifier(session_factory: Any) -> ProductionEvidenceEligibilityVerifierPort:
    from ai_worker.adapters.postgresql_evidence_eligibility import PostgreSqlEvidenceEligibilityVerifier

    return PostgreSqlEvidenceEligibilityVerifier(session_factory)


def build_run_store(session_factory: Any) -> Any:
    from ai_worker.adapters.sqlalchemy_retrieval_run import SqlAlchemyRetrievalRunStore

    return SqlAlchemyRetrievalRunStore(session_factory)


def build_text_embedding_port(*, content_sha256: str, environment: Any = None) -> Any | None:
    """Build the approved OpenAI embedding adapter, or ``None`` when unavailable.

    There is no deterministic/fake fallback: a missing credential must block the
    smoke rather than silently produce an unapproved embedding.
    """
    env = environment if environment is not None else os.environ
    api_key = (env.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    from openai import AsyncOpenAI

    from ai_worker.adapters.openai_text_embedding import OpenAITextEmbeddingAdapter

    adapter = OpenAITextEmbeddingAdapter(
        client=AsyncOpenAI(api_key=api_key),
        adapter_artifact_ref=ImmutableArtifactRef(EMBEDDING_ADAPTER_ARTIFACT_CODE, "1.0.0", content_sha256),
    )
    if (
        adapter.model_ref != PRODUCTION_EMBEDDING_MODEL_REF
        or adapter.model_version != PRODUCTION_EMBEDDING_MODEL_VERSION
        or adapter.dimension != PRODUCTION_EMBEDDING_DIMENSION
    ):
        return None
    return adapter


# --------------------------------------------------------------------------------------
# Canonical receipt verification
# --------------------------------------------------------------------------------------


def verify_persisted_receipt(receipt: PersistedRetrievalRunReceipt) -> bool:
    """Recompute the canonical receipt hash with the merged production function.

    The persisted receipt is self-describing, so this reuses ``compute_receipt_hash``
    over the receipt's own manifest hashes rather than comparing receipt strings by
    hand or inventing a new hash domain.
    """
    recomputed = compute_receipt_hash(
        run_id=receipt.run_id,
        job_id=receipt.job_id,
        node_id=receipt.node_id,
        variant=receipt.variant,
        status=receipt.status,
        query_digest=receipt.query_digest,
        retrieval_configuration_hash=receipt.retrieval_configuration_hash,
        source_manifest_hash=receipt.source_manifest_hash,
        search_receipt_hash=receipt.search_receipt_hash,
        total_signals=receipt.total_signals,
        total_hits=receipt.total_hits,
        selected_count=receipt.selected_count,
        signal_manifest_hash=receipt.signal_manifest_hash,
        hit_manifest_hash=receipt.hit_manifest_hash,
    )
    return recomputed == receipt.receipt_hash


def build_receipt_verifier() -> Callable[[PersistedRetrievalRunReceipt], bool]:
    """Return the canonical receipt verifier used by the smoke orchestration."""
    return verify_persisted_receipt


# --------------------------------------------------------------------------------------
# Evidence Gate negative cases
# --------------------------------------------------------------------------------------


def make_stale_hit(hit: ProductionSearchHit) -> ProductionSearchHit:
    """Return an in-memory copy whose Source currentness no longer matches the DB."""
    provenance = replace(hit.provenance, source_version=f"{hit.provenance.source_version}{STALE_SOURCE_VERSION_SUFFIX}")
    return replace(hit, provenance=provenance)


def make_locator_mismatch_hit(hit: ProductionSearchHit) -> ProductionSearchHit:
    """Return an in-memory copy whose snapshot member locator no longer matches."""
    provenance = replace(hit.provenance, locator=f"{hit.provenance.locator}{LOCATOR_MISMATCH_SUFFIX}")
    return replace(hit, provenance=provenance)


async def verify_gate_fail_closed(
    *,
    eligibility_verifier: ProductionEvidenceEligibilityVerifierPort,
    knowledge_index_id: UUID,
    tampered_hits: tuple[ProductionSearchHit, ...],
) -> tuple[bool, str]:
    """Run tampered hits through the real post-search verifier and Evidence Gate.

    No production row is modified: only the in-memory provenance of a candidate is
    perturbed, exactly as a stale or mislocated Source would present itself.
    """
    if not tampered_hits:
        return False, "no tampered candidate available"

    outcome = await eligibility_verifier.post_search(
        PostSearchEligibilityRequest(knowledge_index_id=knowledge_index_id),
        tampered_hits,
    )
    if not isinstance(outcome, PostSearchEligibilitySuccess):
        # A verifier-level failure is itself fail-closed: nothing is selected.
        return True, "post_search returned a fail-closed outcome"

    gate_outcome = evaluate_evidence_gate(tampered_hits, outcome.eligible_chunk_ids)
    if isinstance(gate_outcome, EvidenceGateSuccess) and gate_outcome.selected_hits:
        return False, "Evidence Gate selected a tampered candidate"
    return True, "Evidence Gate excluded every tampered candidate"


# --------------------------------------------------------------------------------------
# Synthetic fixture authenticity and Source sentinel binding
# --------------------------------------------------------------------------------------
#
# Both checks run read-only and *before* any embedding call or Retrieval Run write, so a
# mis-pointed manifest cannot send a real question to the provider or create smoke rows
# against a production Index.

# The approved synthetic Knowledge Index identity is the one PR #663 already established.
# This module reuses that constant read-only rather than defining a new convention.
APPROVED_SYNTHETIC_INDEX_CODES: frozenset[str] = frozenset({SYNTHETIC_INDEX_CODE})


async def verify_fixture_is_synthetic(
    *,
    session_factory: Any,
    knowledge_index_id: UUID,
    allowed_source_snapshot_ids: tuple[UUID, ...],
    approved_index_codes: frozenset[str] = APPROVED_SYNTHETIC_INDEX_CODES,
) -> tuple[bool, str]:
    """Prove the pinned Index really is an approved synthetic Index in the database.

    Naming a manifest "synthetic" proves nothing. This resolves the pinned
    ``knowledge_index_id`` in PostgreSQL and requires its ``index_code`` to be an
    approved synthetic code, and every member snapshot to be inside the declared
    allow-list.
    """
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    text("SELECT index_code, index_version FROM rag_knowledge_index WHERE id = :id"),
                    {"id": str(knowledge_index_id)},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return False, "pinned knowledge index does not exist"
        if str(row["index_code"]) not in approved_index_codes:
            return False, "pinned knowledge index is not an approved synthetic index"

        member_rows = (
            (
                await session.execute(
                    text(
                        "SELECT DISTINCT source_snapshot_id FROM rag_knowledge_index_member "
                        "WHERE knowledge_index_id = :id"
                    ),
                    {"id": str(knowledge_index_id)},
                )
            )
            .scalars()
            .all()
        )
        if not member_rows:
            return False, "pinned knowledge index has no members"

        allowed = {str(value) for value in allowed_source_snapshot_ids}
        if not allowed:
            return False, "fixture declares no allowed source snapshot"
        outside = {str(value) for value in member_rows} - allowed
        if outside:
            return False, "knowledge index members reference snapshots outside the declared allow-list"

    return True, "pinned index is an approved synthetic index within the declared snapshots"


async def verify_source_sentinel_indexed(
    *,
    session_factory: Any,
    knowledge_index_id: UUID,
    source_sentinel: str,
    allowed_source_snapshot_member_ids: tuple[UUID, ...],
) -> tuple[bool, str]:
    """Prove the declared Source sentinel is present in an *allowed* indexed chunk.

    Two properties matter and neither is free:

    * ``strpos`` is a literal substring search. ``LIKE`` would treat ``_`` and ``%`` in
      the sentinel as wildcards, so a sentinel of ``a_c`` would match ``abc`` and the
      binding proof would accept a corpus that never carried the marker.
    * The search is restricted to the snapshot members the fixture declares. Otherwise a
      marker sitting in some unrelated chunk of the index could manufacture the binding
      proof for a Source the run never retrieves.
    """
    if not source_sentinel.strip():
        return False, "fixture declares an empty Source sentinel"
    if not allowed_source_snapshot_member_ids:
        return False, "fixture declares no allowed source snapshot member"

    async with session_factory() as session:
        matches = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM rag_knowledge_index_member m "
                    "JOIN knowledge_chunk c ON c.id = m.knowledge_chunk_id "
                    "WHERE m.knowledge_index_id = :id "
                    "AND m.source_snapshot_member_id = ANY(:members) "
                    "AND strpos(c.chunk_text, :needle) > 0"
                ),
                {
                    "id": str(knowledge_index_id),
                    "members": [str(value) for value in allowed_source_snapshot_member_ids],
                    "needle": source_sentinel,
                },
            )
        ).scalar_one()

    if not matches:
        return False, "no allowed indexed chunk carries the declared Source sentinel"
    return True, "declared Source sentinel is present in an allowed indexed chunk"


async def verify_selected_candidates_carry_sentinel(
    *,
    session_factory: Any,
    selected_chunk_ids: tuple[UUID, ...],
    source_sentinel: str,
) -> tuple[bool, str]:
    """Prove the candidates the Gate actually selected carry the Source sentinel.

    The pre-execution binding proof only shows the marker exists somewhere allowed. This
    closes the remaining gap: the Source whose non-logging is being certified must be the
    Source this run actually retrieved.
    """
    if not selected_chunk_ids:
        return False, "no selected candidate to check"
    if not source_sentinel.strip():
        return False, "fixture declares an empty Source sentinel"

    async with session_factory() as session:
        matches = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM knowledge_chunk c "
                    "WHERE c.id = ANY(:ids) AND strpos(c.chunk_text, :needle) > 0"
                ),
                {"ids": [str(value) for value in selected_chunk_ids], "needle": source_sentinel},
            )
        ).scalar_one()

    if matches != len(selected_chunk_ids):
        return False, "a selected candidate does not carry the declared Source sentinel"
    return True, "every selected candidate carries the declared Source sentinel"
