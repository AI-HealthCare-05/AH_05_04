from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    RetrievalHit,
    RetrievalRun,
    RetrievalRunStatus,
    RetrievalSignal,
)


class RetrievalRunConflictError(Exception):
    pass


class RetrievalRunNotFoundError(Exception):
    pass


class RetrievalRunStateError(Exception):
    pass


class RetrievalRunValidationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class RetrievalRunCreate:
    job_id: UUID
    execution_context_id: UUID
    prescription_version_id: UUID
    runtime_release_bundle_id: UUID
    runtime_release_bundle_manifest_hash: str
    runtime_execution_manifest_id: UUID
    runtime_execution_manifest_hash: str
    runtime_guard_decision_ref: str
    knowledge_index_id: UUID
    variant: str
    query_digest_algorithm: str
    query_digest_key_version: str
    query_digest: str
    filter_snapshot: dict[str, Any]
    filter_snapshot_hash: str
    source_manifest_hash: str
    retrieval_configuration_hash: str
    lexical_limit: int
    dense_limit: int
    hybrid_limit: int
    final_k: int
    node_id: str = "hybrid_retrieve"
    query_embedding_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalSignalCreate:
    knowledge_chunk_id: UUID
    method: str
    raw_rank: int
    raw_score: Decimal
    score_projection_version: str


@dataclass(frozen=True, slots=True)
class RetrievalHitCreate:
    knowledge_chunk_id: UUID
    rrf_rank: int
    rrf_score: Decimal
    rrf_score_numerator: str
    rrf_score_denominator: str
    final_rank: int
    selected: bool
    lexical_rank: int | None = None
    dense_rank: int | None = None
    rerank_score: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RetrievalRunFinalize:
    status: str
    search_receipt_hash: str | None
    receipt_hash: str
    diagnostic_code: str | None = None
    error_code: str | None = None
    signals: tuple[RetrievalSignalCreate, ...] = ()
    hits: tuple[RetrievalHitCreate, ...] = ()


class RagRetrievalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_run(self, run_id: UUID) -> RetrievalRun | None:
        stmt = select(RetrievalRun).where(RetrievalRun.id == run_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_run_by_job_and_node(
        self,
        job_id: UUID,
        node_id: str,
        *,
        for_update: bool = False,
    ) -> RetrievalRun | None:
        stmt = select(RetrievalRun).where(
            RetrievalRun.job_id == job_id,
            RetrievalRun.node_id == node_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def begin_run(self, data: RetrievalRunCreate) -> tuple[RetrievalRun, bool]:
        """Begin a retrieval run idempotently.

        Returns (run, is_created). If matching run exists with same identity,
        returns (existing_run, False). If identity conflicts, raises RetrievalRunConflictError.
        """
        existing = await self.get_run_by_job_and_node(data.job_id, data.node_id, for_update=True)
        if existing is not None:
            # Check identity match
            if (
                existing.query_digest != data.query_digest
                or existing.retrieval_configuration_hash != data.retrieval_configuration_hash
                or existing.knowledge_index_id != data.knowledge_index_id
                or existing.variant != data.variant
                or existing.source_manifest_hash != data.source_manifest_hash
                or existing.filter_snapshot_hash != data.filter_snapshot_hash
            ):
                raise RetrievalRunConflictError(
                    f"Retrieval run for job {data.job_id} and node {data.node_id} already exists with differing identity"
                )
            return existing, False

        run = RetrievalRun(
            id=uuid4(),
            job_id=data.job_id,
            execution_context_id=data.execution_context_id,
            prescription_version_id=data.prescription_version_id,
            runtime_release_bundle_id=data.runtime_release_bundle_id,
            runtime_release_bundle_manifest_hash=data.runtime_release_bundle_manifest_hash,
            runtime_execution_manifest_id=data.runtime_execution_manifest_id,
            runtime_execution_manifest_hash=data.runtime_execution_manifest_hash,
            runtime_guard_decision_ref=data.runtime_guard_decision_ref,
            knowledge_index_id=data.knowledge_index_id,
            node_id=data.node_id,
            variant=data.variant,
            query_digest_algorithm=data.query_digest_algorithm,
            query_digest_key_version=data.query_digest_key_version,
            query_digest=data.query_digest,
            filter_snapshot=data.filter_snapshot,
            filter_snapshot_hash=data.filter_snapshot_hash,
            source_manifest_hash=data.source_manifest_hash,
            retrieval_configuration_hash=data.retrieval_configuration_hash,
            query_embedding_sha256=data.query_embedding_sha256,
            lexical_limit=data.lexical_limit,
            dense_limit=data.dense_limit,
            hybrid_limit=data.hybrid_limit,
            final_k=data.final_k,
            status=RetrievalRunStatus.RUNNING,
        )
        self._session.add(run)
        await self._session.flush()
        return run, True

    async def finalize_run(self, run_id: UUID, data: RetrievalRunFinalize) -> RetrievalRun:
        """Atomically insert signals/hits and update retrieval_run to terminal status."""
        stmt = select(RetrievalRun).where(RetrievalRun.id == run_id).with_for_update()
        res = await self._session.execute(stmt)
        run = res.scalar_one_or_none()
        if run is None:
            raise RetrievalRunNotFoundError(f"Retrieval run {run_id} not found")

        if run.status != RetrievalRunStatus.RUNNING:
            if run.status == data.status and run.receipt_hash == data.receipt_hash:
                return run
            raise RetrievalRunStateError(
                f"Retrieval run {run_id} has invalid status {run.status} for finalization"
            )

        # Validate selection rules: selected must only be in pre-gate top 5 (final_rank <= 5)
        for h in data.hits:
            if h.selected and h.final_rank > 5:
                raise RetrievalRunValidationError("Selected hit final_rank cannot exceed 5")

        # Insert signals
        for sig in data.signals:
            sig_row = RetrievalSignal(
                retrieval_run_id=run.id,
                retrieval_method=sig.method,
                knowledge_chunk_id=sig.knowledge_chunk_id,
                raw_rank=sig.raw_rank,
                raw_score=sig.raw_score,
                score_projection_version=sig.score_projection_version,
            )
            self._session.add(sig_row)

        # Insert hits
        for hit in data.hits:
            hit_row = RetrievalHit(
                retrieval_run_id=run.id,
                knowledge_chunk_id=hit.knowledge_chunk_id,
                lexical_rank=hit.lexical_rank,
                dense_rank=hit.dense_rank,
                rrf_rank=hit.rrf_rank,
                rrf_score=hit.rrf_score,
                rrf_score_numerator=hit.rrf_score_numerator,
                rrf_score_denominator=hit.rrf_score_denominator,
                rerank_score=hit.rerank_score,
                final_rank=hit.final_rank,
                selected=hit.selected,
            )
            self._session.add(hit_row)

        # Update run to terminal
        run.status = data.status
        run.diagnostic_code = data.diagnostic_code
        run.error_code = data.error_code
        run.search_receipt_hash = data.search_receipt_hash
        run.receipt_hash = data.receipt_hash
        run.completed_at = datetime.now(UTC)

        await self._session.flush()
        return run

    async def get_run_signals(self, run_id: UUID) -> list[RetrievalSignal]:
        stmt = (
            select(RetrievalSignal)
            .where(RetrievalSignal.retrieval_run_id == run_id)
            .order_by(RetrievalSignal.retrieval_method, RetrievalSignal.raw_rank)
        )
        res = await self._session.execute(stmt)
        return list(res.scalars().all())

    async def get_run_hits(self, run_id: UUID) -> list[RetrievalHit]:
        stmt = (
            select(RetrievalHit)
            .where(RetrievalHit.retrieval_run_id == run_id)
            .order_by(RetrievalHit.final_rank)
        )
        res = await self._session.execute(stmt)
        return list(res.scalars().all())
