from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    Numeric,
    String,
    column,
    func,
    insert,
    select,
    table,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.retrieval_run import (
    BeginRetrievalRunFailure,
    BeginRetrievalRunFailureReason,
    BeginRetrievalRunOutcome,
    BeginRetrievalRunRequest,
    BeginRetrievalRunSuccess,
    FinalizeRetrievalRunFailure,
    FinalizeRetrievalRunFailureReason,
    FinalizeRetrievalRunOutcome,
    FinalizeRetrievalRunRequest,
    FinalizeRetrievalRunSuccess,
    PersistedHitInput,
    PersistedRetrievalRunReceipt,
    PersistedSignalInput,
    RetrievalRunStorePort,
    compute_hit_manifest_hash,
    compute_receipt_hash,
    compute_signal_manifest_hash,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]

_RETRIEVAL_RUN = table(
    "retrieval_run",
    column("id", String(36)),
    column("job_id", String(36)),
    column("execution_context_id", String(36)),
    column("prescription_version_id", String(36)),
    column("runtime_release_bundle_id", String(36)),
    column("runtime_release_bundle_manifest_hash", String(64)),
    column("runtime_execution_manifest_id", String(36)),
    column("runtime_execution_manifest_hash", String(64)),
    column("runtime_guard_decision_ref", String(255)),
    column("knowledge_index_id", String(36)),
    column("node_id", String(80)),
    column("variant", String(20)),
    column("query_digest_algorithm", String(80)),
    column("query_digest_key_version", String(80)),
    column("query_digest", String(64)),
    column("filter_snapshot", JSON),
    column("filter_snapshot_hash", String(64)),
    column("source_manifest_hash", String(64)),
    column("retrieval_configuration_hash", String(64)),
    column("query_embedding_sha256", String(64)),
    column("lexical_limit", Integer),
    column("dense_limit", Integer),
    column("hybrid_limit", Integer),
    column("final_k", Integer),
    column("status", String(30)),
    column("diagnostic_code", String(80)),
    column("error_code", String(80)),
    column("search_receipt_hash", String(64)),
    column("receipt_hash", String(64)),
    column("started_at", DateTime(timezone=True)),
    column("completed_at", DateTime(timezone=True)),
)

_RETRIEVAL_SIGNAL = table(
    "retrieval_signal",
    column("retrieval_run_id", String(36)),
    column("retrieval_method", String(20)),
    column("knowledge_chunk_id", String(36)),
    column("raw_rank", Integer),
    column("raw_score", Numeric(38, 18)),
    column("score_projection_version", String(80)),
)

_RETRIEVAL_HIT = table(
    "retrieval_hit",
    column("retrieval_run_id", String(36)),
    column("knowledge_chunk_id", String(36)),
    column("lexical_rank", Integer),
    column("dense_rank", Integer),
    column("rrf_rank", Integer),
    column("rrf_score", Numeric(38, 18)),
    column("rrf_score_numerator", String(64)),
    column("rrf_score_denominator", String(64)),
    column("rerank_score", Numeric(38, 18)),
    column("final_rank", Integer),
    column("selected", Boolean),
)


class SqlAlchemyRetrievalRunStore(RetrievalRunStorePort):
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def begin_run(self, request: BeginRetrievalRunRequest) -> BeginRetrievalRunOutcome:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    # 1. Check existing run for (job_id, node_id)
                    run_stmt = (
                        select(_RETRIEVAL_RUN)
                        .where(
                            _RETRIEVAL_RUN.c.job_id == str(request.job_id),
                            _RETRIEVAL_RUN.c.node_id == request.node_id,
                        )
                        .with_for_update()
                    )
                    run_row = (await session.execute(run_stmt)).mappings().first()

                    if run_row is not None:
                        # Validate identity match
                        matches = (
                            run_row["query_digest"] == request.query_digest
                            and run_row["retrieval_configuration_hash"] == request.retrieval_configuration_hash
                            and run_row["knowledge_index_id"] == str(request.knowledge_index_id)
                            and run_row["variant"] == request.variant
                            and run_row["source_manifest_hash"] == request.source_manifest_hash
                            and run_row["filter_snapshot_hash"] == request.filter_snapshot_hash
                            and run_row["execution_context_id"] == str(request.execution_context_id)
                        )
                        if not matches:
                            return BeginRetrievalRunFailure(
                                reason=BeginRetrievalRunFailureReason.CONFLICT,
                                message="Existing retrieval run identity differs from request",
                            )

                        run_id = UUID(run_row["id"])
                        if run_row["status"] == "COMPLETED":
                            receipt = await self._load_receipt_within_session(session, run_id, run_row)
                            if receipt is None:
                                return BeginRetrievalRunFailure(
                                    reason=BeginRetrievalRunFailureReason.DEPENDENCY_ERROR,
                                    message="Corrupt stored retrieval run receipt",
                                )
                            return BeginRetrievalRunSuccess(
                                run_id=run_id,
                                is_resumed=True,
                                existing_receipt=receipt,
                            )
                        return BeginRetrievalRunSuccess(run_id=run_id, is_resumed=True)

                    # 4. Insert new RUNNING row
                    new_run_id = uuid4()
                    ins_stmt = insert(_RETRIEVAL_RUN).values(
                        id=str(new_run_id),
                        job_id=str(request.job_id),
                        execution_context_id=str(request.execution_context_id),
                        prescription_version_id=str(request.prescription_version_id),
                        runtime_release_bundle_id=str(request.runtime_release_bundle_id),
                        runtime_release_bundle_manifest_hash=request.runtime_release_bundle_manifest_hash,
                        runtime_execution_manifest_id=str(request.runtime_execution_manifest_id),
                        runtime_execution_manifest_hash=request.runtime_execution_manifest_hash,
                        runtime_guard_decision_ref=request.runtime_guard_decision_ref,
                        knowledge_index_id=str(request.knowledge_index_id),
                        node_id=request.node_id,
                        variant=request.variant,
                        query_digest_algorithm=request.query_digest_algorithm,
                        query_digest_key_version=request.query_digest_key_version,
                        query_digest=request.query_digest,
                        filter_snapshot=request.filter_snapshot,
                        filter_snapshot_hash=request.filter_snapshot_hash,
                        source_manifest_hash=request.source_manifest_hash,
                        retrieval_configuration_hash=request.retrieval_configuration_hash,
                        query_embedding_sha256=request.query_embedding_sha256,
                        lexical_limit=request.lexical_limit,
                        dense_limit=request.dense_limit,
                        hybrid_limit=request.hybrid_limit,
                        final_k=request.final_k,
                        status="RUNNING",
                        started_at=func.now(),
                    )
                    await session.execute(ins_stmt)
                    return BeginRetrievalRunSuccess(run_id=new_run_id, is_resumed=False)
        except Exception as e:
            logger.exception("begin_run failed: %s", type(e).__name__)
            return BeginRetrievalRunFailure(
                reason=BeginRetrievalRunFailureReason.DEPENDENCY_ERROR,
                message=f"Database error during begin_run: {type(e).__name__}",
            )

    async def finalize_run(self, request: FinalizeRetrievalRunRequest) -> FinalizeRetrievalRunOutcome:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    # Lock retrieval run FOR UPDATE
                    run_stmt = (
                        select(_RETRIEVAL_RUN)
                        .where(
                            _RETRIEVAL_RUN.c.id == str(request.run_id),
                        )
                        .with_for_update()
                    )
                    run_row = (await session.execute(run_stmt)).mappings().first()
                    if run_row is None:
                        return FinalizeRetrievalRunFailure(
                            reason=FinalizeRetrievalRunFailureReason.NOT_FOUND,
                            message=f"Retrieval run {request.run_id} not found",
                        )

                    # Check selection rule: selected must be final_rank <= 5
                    for h in request.hits:
                        if h.selected and h.final_rank > 5:
                            return FinalizeRetrievalRunFailure(
                                reason=FinalizeRetrievalRunFailureReason.VALIDATION_ERROR,
                                message="Selected hit cannot have final_rank > 5",
                            )

                    # Compute manifest hashes
                    signal_manifest_hash = compute_signal_manifest_hash(request.signals)
                    hit_manifest_hash = compute_hit_manifest_hash(request.hits)
                    selected_count = sum(1 for h in request.hits if h.selected)

                    receipt_hash = compute_receipt_hash(
                        run_id=request.run_id,
                        job_id=UUID(run_row["job_id"]),
                        node_id=run_row["node_id"],
                        variant=run_row["variant"],
                        status=request.status,
                        query_digest=run_row["query_digest"],
                        retrieval_configuration_hash=run_row["retrieval_configuration_hash"],
                        source_manifest_hash=run_row["source_manifest_hash"],
                        search_receipt_hash=request.search_receipt_hash,
                        total_signals=len(request.signals),
                        total_hits=len(request.hits),
                        selected_count=selected_count,
                        signal_manifest_hash=signal_manifest_hash,
                        hit_manifest_hash=hit_manifest_hash,
                    )

                    if run_row["status"] != "RUNNING":
                        if run_row["status"] == request.status and run_row["receipt_hash"] == receipt_hash:
                            receipt = PersistedRetrievalRunReceipt(
                                run_id=request.run_id,
                                job_id=UUID(run_row["job_id"]),
                                node_id=run_row["node_id"],
                                variant=run_row["variant"],
                                status=run_row["status"],
                                query_digest=run_row["query_digest"],
                                retrieval_configuration_hash=run_row["retrieval_configuration_hash"],
                                source_manifest_hash=run_row["source_manifest_hash"],
                                receipt_hash=receipt_hash,
                                total_signals=len(request.signals),
                                total_hits=len(request.hits),
                                selected_count=selected_count,
                                signal_manifest_hash=signal_manifest_hash,
                                hit_manifest_hash=hit_manifest_hash,
                                search_receipt_hash=request.search_receipt_hash,
                                diagnostic_code=request.diagnostic_code,
                                error_code=request.error_code,
                            )
                            return FinalizeRetrievalRunSuccess(receipt=receipt)
                        return FinalizeRetrievalRunFailure(
                            reason=FinalizeRetrievalRunFailureReason.STATE_CONFLICT,
                            message=f"Run {request.run_id} is in status {run_row['status']}, expected RUNNING",
                        )

                    # Insert signals
                    if request.signals:
                        sig_values = [
                            {
                                "retrieval_run_id": str(request.run_id),
                                "retrieval_method": s.method,
                                "knowledge_chunk_id": str(s.knowledge_chunk_id),
                                "raw_rank": s.raw_rank,
                                "raw_score": s.raw_score,
                                "score_projection_version": s.score_projection_version,
                            }
                            for s in request.signals
                        ]
                        await session.execute(insert(_RETRIEVAL_SIGNAL).values(sig_values))

                    # Insert hits
                    if request.hits:
                        hit_values = [
                            {
                                "retrieval_run_id": str(request.run_id),
                                "knowledge_chunk_id": str(h.knowledge_chunk_id),
                                "lexical_rank": h.lexical_rank,
                                "dense_rank": h.dense_rank,
                                "rrf_rank": h.rrf_rank,
                                "rrf_score": h.rrf_score,
                                "rrf_score_numerator": h.rrf_score_numerator,
                                "rrf_score_denominator": h.rrf_score_denominator,
                                "rerank_score": h.rerank_score,
                                "final_rank": h.final_rank,
                                "selected": h.selected,
                            }
                            for h in request.hits
                        ]
                        await session.execute(insert(_RETRIEVAL_HIT).values(hit_values))

                    # Update run to terminal
                    upd_stmt = (
                        update(_RETRIEVAL_RUN)
                        .where(_RETRIEVAL_RUN.c.id == str(request.run_id))
                        .values(
                            status=request.status,
                            diagnostic_code=request.diagnostic_code,
                            error_code=request.error_code,
                            search_receipt_hash=request.search_receipt_hash,
                            receipt_hash=receipt_hash,
                            completed_at=func.now(),
                        )
                    )
                    await session.execute(upd_stmt)

                    receipt = PersistedRetrievalRunReceipt(
                        run_id=request.run_id,
                        job_id=UUID(run_row["job_id"]),
                        node_id=run_row["node_id"],
                        variant=run_row["variant"],
                        status=request.status,
                        query_digest=run_row["query_digest"],
                        retrieval_configuration_hash=run_row["retrieval_configuration_hash"],
                        source_manifest_hash=run_row["source_manifest_hash"],
                        receipt_hash=receipt_hash,
                        total_signals=len(request.signals),
                        total_hits=len(request.hits),
                        selected_count=selected_count,
                        signal_manifest_hash=signal_manifest_hash,
                        hit_manifest_hash=hit_manifest_hash,
                        search_receipt_hash=request.search_receipt_hash,
                        diagnostic_code=request.diagnostic_code,
                        error_code=request.error_code,
                    )
                    return FinalizeRetrievalRunSuccess(receipt=receipt)
        except Exception as e:
            logger.exception("finalize_run failed: %s", type(e).__name__)
            return FinalizeRetrievalRunFailure(
                reason=FinalizeRetrievalRunFailureReason.DEPENDENCY_ERROR,
                message=f"Database error during finalize_run: {type(e).__name__}",
            )

    async def get_run_receipt(self, run_id: UUID) -> PersistedRetrievalRunReceipt | None:
        async with self._session_factory() as session:
            stmt = select(_RETRIEVAL_RUN).where(_RETRIEVAL_RUN.c.id == str(run_id))
            row = (await session.execute(stmt)).mappings().first()
            if row is None or row["status"] != "COMPLETED":
                return None
            return await self._load_receipt_within_session(session, run_id, row)

    async def _load_receipt_within_session(
        self,
        session: AsyncSession,
        run_id: UUID,
        run_row: Any,
    ) -> PersistedRetrievalRunReceipt | None:
        # Load signals
        sig_stmt = select(_RETRIEVAL_SIGNAL).where(_RETRIEVAL_SIGNAL.c.retrieval_run_id == str(run_id))
        sig_rows = (await session.execute(sig_stmt)).mappings().all()
        signals = [
            PersistedSignalInput(
                knowledge_chunk_id=UUID(r["knowledge_chunk_id"]),
                method=r["retrieval_method"],
                raw_rank=r["raw_rank"],
                raw_score=Decimal(str(r["raw_score"])),
                score_projection_version=r["score_projection_version"],
            )
            for r in sig_rows
        ]

        # Load hits
        hit_stmt = select(_RETRIEVAL_HIT).where(_RETRIEVAL_HIT.c.retrieval_run_id == str(run_id))
        hit_rows = (await session.execute(hit_stmt)).mappings().all()
        hits = [
            PersistedHitInput(
                knowledge_chunk_id=UUID(r["knowledge_chunk_id"]),
                rrf_rank=r["rrf_rank"],
                rrf_score=Decimal(str(r["rrf_score"])),
                rrf_score_numerator=r["rrf_score_numerator"],
                rrf_score_denominator=r["rrf_score_denominator"],
                final_rank=r["final_rank"],
                selected=bool(r["selected"]),
                lexical_rank=r["lexical_rank"],
                dense_rank=r["dense_rank"],
                rerank_score=Decimal(str(r["rerank_score"])) if r["rerank_score"] is not None else None,
            )
            for r in hit_rows
        ]

        signal_manifest_hash = compute_signal_manifest_hash(signals)
        hit_manifest_hash = compute_hit_manifest_hash(hits)
        selected_count = sum(1 for h in hits if h.selected)

        expected_hash = compute_receipt_hash(
            run_id=run_id,
            job_id=UUID(run_row["job_id"]),
            node_id=run_row["node_id"],
            variant=run_row["variant"],
            status=run_row["status"],
            query_digest=run_row["query_digest"],
            retrieval_configuration_hash=run_row["retrieval_configuration_hash"],
            source_manifest_hash=run_row["source_manifest_hash"],
            search_receipt_hash=run_row["search_receipt_hash"],
            total_signals=len(signals),
            total_hits=len(hits),
            selected_count=selected_count,
            signal_manifest_hash=signal_manifest_hash,
            hit_manifest_hash=hit_manifest_hash,
        )

        # Integrity verification: fail closed if corrupt
        if expected_hash != run_row["receipt_hash"]:
            logger.error(
                "Corrupt retrieval run %s: receipt_hash mismatch (expected %s, stored %s)",
                run_id,
                expected_hash,
                run_row["receipt_hash"],
            )
            return None

        return PersistedRetrievalRunReceipt(
            run_id=run_id,
            job_id=UUID(run_row["job_id"]),
            node_id=run_row["node_id"],
            variant=run_row["variant"],
            status=run_row["status"],
            query_digest=run_row["query_digest"],
            retrieval_configuration_hash=run_row["retrieval_configuration_hash"],
            source_manifest_hash=run_row["source_manifest_hash"],
            receipt_hash=expected_hash,
            total_signals=len(signals),
            total_hits=len(hits),
            selected_count=selected_count,
            signal_manifest_hash=signal_manifest_hash,
            hit_manifest_hash=hit_manifest_hash,
            search_receipt_hash=run_row["search_receipt_hash"],
            diagnostic_code=run_row["diagnostic_code"],
            error_code=run_row["error_code"],
        )
