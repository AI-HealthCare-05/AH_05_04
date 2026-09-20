from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
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
    null,
    select,
    table,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.production_evidence_gate import EvidenceGateSuccess
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
    PersistedTerminalReplayPayload,
    RetrievalRunStorePort,
    compute_hit_manifest_hash,
    compute_receipt_hash,
    compute_signal_manifest_hash,
    sha256_canonical_json,
)
from ai_worker.tasks.rag.retrieval_runtime import (
    RetrievalExecutionStatus,
    restore_terminal_replay_payload,
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
    column("terminal_replay_payload", JSON),
    column("terminal_replay_payload_hash", String(64)),
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


@dataclass(frozen=True, slots=True)
class _PreparedTerminalReplayPayload:
    projection: dict[str, Any] | None
    payload_hash: str | None


def _validation_failure(message: str) -> FinalizeRetrievalRunFailure:
    return FinalizeRetrievalRunFailure(
        reason=FinalizeRetrievalRunFailureReason.VALIDATION_ERROR,
        message=message,
    )


def _terminal_replay_bindings_match(
    request: FinalizeRetrievalRunRequest,
    run_row: Any,
    *,
    search_receipt: Any,
    gate_outcome: Any,
    signal_manifest_hash: str,
    hit_manifest_hash: str,
) -> bool:
    selected_hits = gate_outcome.selected_hits if isinstance(gate_outcome, EvidenceGateSuccess) else ()
    payload_selected = tuple((hit.provenance.knowledge_chunk_id, hit.fusion_rank) for hit in selected_hits)
    persisted_selected = tuple(
        (hit.knowledge_chunk_id, hit.final_rank)
        for hit in sorted(request.hits, key=lambda item: item.final_rank)
        if hit.selected
    )
    return (
        search_receipt.retrieval_execution_status == RetrievalExecutionStatus.SUCCEEDED
        and request.search_receipt_hash == search_receipt.artifact_ref.content_sha256
        and request.diagnostic_code == gate_outcome.reason.value == search_receipt.diagnostic_code
        and search_receipt.variant == run_row["variant"]
        and search_receipt.query_fingerprint.digest == run_row["query_digest"]
        and search_receipt.retrieval_config_ref.content_sha256 == run_row["retrieval_configuration_hash"]
        and search_receipt.signal_manifest_sha256 == signal_manifest_hash
        and search_receipt.hit_manifest_sha256 == hit_manifest_hash
        and payload_selected == persisted_selected
    )


def _prepare_terminal_replay_payload(
    request: FinalizeRetrievalRunRequest,
    run_row: Any,
    *,
    signal_manifest_hash: str,
    hit_manifest_hash: str,
) -> _PreparedTerminalReplayPayload | FinalizeRetrievalRunFailure:
    if request.status == "FAILED":
        if request.terminal_replay_payload is not None or request.search_receipt_hash is not None:
            return _validation_failure("FAILED run cannot persist a terminal replay payload")
        return _PreparedTerminalReplayPayload(projection=None, payload_hash=None)
    if request.status != "COMPLETED":
        return _validation_failure("Retrieval run terminal status must be COMPLETED or FAILED")
    if request.terminal_replay_payload is None or request.search_receipt_hash is None:
        return _validation_failure("COMPLETED run requires search receipt and terminal replay payload")

    try:
        search_receipt, gate_outcome = restore_terminal_replay_payload(request.terminal_replay_payload)
    except (ArithmeticError, KeyError, TypeError, ValueError):
        return _validation_failure("Invalid terminal replay payload")

    if not _terminal_replay_bindings_match(
        request,
        run_row,
        search_receipt=search_receipt,
        gate_outcome=gate_outcome,
        signal_manifest_hash=signal_manifest_hash,
        hit_manifest_hash=hit_manifest_hash,
    ):
        return _validation_failure("Terminal replay payload does not match persisted retrieval result")

    projection = request.terminal_replay_payload.to_projection()
    return _PreparedTerminalReplayPayload(
        projection=projection,
        payload_hash=sha256_canonical_json(projection),
    )


class SqlAlchemyRetrievalRunStore(RetrievalRunStorePort):
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def _validate_and_resume_existing(
        self,
        session: AsyncSession,
        request: BeginRetrievalRunRequest,
        run_row: Any,
    ) -> BeginRetrievalRunOutcome:
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
            try:
                replay_payload = (
                    PersistedTerminalReplayPayload.from_projection(run_row["terminal_replay_payload"])
                    if run_row["terminal_replay_payload"] is not None
                    else None
                )
            except ValueError:
                return BeginRetrievalRunFailure(
                    reason=BeginRetrievalRunFailureReason.DEPENDENCY_ERROR,
                    message="Corrupt stored terminal replay payload",
                )
            return BeginRetrievalRunSuccess(
                run_id=run_id,
                is_resumed=True,
                existing_receipt=receipt,
                existing_terminal_replay_payload=replay_payload,
            )
        return BeginRetrievalRunSuccess(run_id=run_id, is_resumed=True)

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
                        return await self._validate_and_resume_existing(session, request, run_row)

                    # 2. Insert new RUNNING row with ON CONFLICT DO NOTHING
                    new_run_id = uuid4()
                    ins_stmt = (
                        pg_insert(_RETRIEVAL_RUN)
                        .values(
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
                        .on_conflict_do_nothing(index_elements=["job_id", "node_id"])
                        .returning(_RETRIEVAL_RUN.c.id)
                    )
                    ins_res = await session.execute(ins_stmt)
                    inserted_id = ins_res.scalar_one_or_none()

                    if inserted_id is not None:
                        return BeginRetrievalRunSuccess(run_id=new_run_id, is_resumed=False)

                    # 3. Race condition collision: concurrent transaction created the run first.
                    conflict_row = (await session.execute(run_stmt)).mappings().first()
                    if conflict_row is not None:
                        return await self._validate_and_resume_existing(session, request, conflict_row)

                    return BeginRetrievalRunFailure(
                        reason=BeginRetrievalRunFailureReason.DEPENDENCY_ERROR,
                        message="Failed to create or retrieve concurrent retrieval run",
                    )
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
                    prepared_payload = _prepare_terminal_replay_payload(
                        request,
                        run_row,
                        signal_manifest_hash=signal_manifest_hash,
                        hit_manifest_hash=hit_manifest_hash,
                    )
                    if isinstance(prepared_payload, FinalizeRetrievalRunFailure):
                        return prepared_payload
                    terminal_replay_projection = prepared_payload.projection
                    terminal_replay_hash = prepared_payload.payload_hash

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
                        terminal_replay_payload_hash=terminal_replay_hash,
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
                                terminal_replay_payload_hash=terminal_replay_hash,
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
                            terminal_replay_payload=(
                                terminal_replay_projection if terminal_replay_projection is not None else null()
                            ),
                            terminal_replay_payload_hash=terminal_replay_hash,
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
                        terminal_replay_payload_hash=terminal_replay_hash,
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

        terminal_replay_projection = run_row["terminal_replay_payload"]
        terminal_replay_hash = run_row["terminal_replay_payload_hash"]
        replay_payload: PersistedTerminalReplayPayload | None = None
        if (terminal_replay_projection is None) != (terminal_replay_hash is None):
            logger.error("Corrupt retrieval run %s: incomplete terminal replay payload", run_id)
            return None
        if terminal_replay_projection is not None:
            try:
                replay_payload = PersistedTerminalReplayPayload.from_projection(terminal_replay_projection)
            except ValueError:
                logger.error("Corrupt retrieval run %s: invalid terminal replay payload", run_id)
                return None
            expected_terminal_replay_hash = sha256_canonical_json(terminal_replay_projection)
            if expected_terminal_replay_hash != terminal_replay_hash:
                logger.error("Corrupt retrieval run %s: terminal replay payload hash mismatch", run_id)
                return None
            validation_request = FinalizeRetrievalRunRequest(
                run_id=run_id,
                status=run_row["status"],
                diagnostic_code=run_row["diagnostic_code"],
                error_code=run_row["error_code"],
                search_receipt_hash=run_row["search_receipt_hash"],
                signals=tuple(signals),
                hits=tuple(hits),
                terminal_replay_payload=replay_payload,
            )
            prepared = _prepare_terminal_replay_payload(
                validation_request,
                run_row,
                signal_manifest_hash=signal_manifest_hash,
                hit_manifest_hash=hit_manifest_hash,
            )
            if isinstance(prepared, FinalizeRetrievalRunFailure):
                logger.error("Corrupt retrieval run %s: terminal replay cross-binding mismatch", run_id)
                return None

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
            terminal_replay_payload_hash=terminal_replay_hash,
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
            terminal_replay_payload_hash=terminal_replay_hash,
        )
