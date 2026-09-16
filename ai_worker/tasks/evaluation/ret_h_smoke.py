"""Worker-side RET-H synthetic smoke execution orchestration."""

from __future__ import annotations

from typing import Any

from ai_worker.tasks.rag.retrieval_runtime import (
    HybridRetrieveOutcome,
    HybridRetrieveRequest,
    execute_hybrid_retrieve,
)


async def execute_ret_h_smoke_transaction(
    *,
    request: HybridRetrieveRequest,
    search_port: Any,
    text_embedding_port: Any,
    run_store: Any,
    eligibility_verifier: Any,
) -> HybridRetrieveOutcome:
    """Transaction 1: execute_hybrid_retrieve begin/finalize."""
    return await execute_hybrid_retrieve(
        request,
        search_port=search_port,
        text_embedding_port=text_embedding_port,
        run_store=run_store,
        eligibility_verifier=eligibility_verifier,
    )
