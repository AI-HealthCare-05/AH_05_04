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
