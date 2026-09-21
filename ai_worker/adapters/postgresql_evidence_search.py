"""PostgreSQL adapter for Knowledge Evidence Search and deterministic RRF ranking."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    DateTime,
    Integer,
    String,
    and_,
    column,
    func,
    or_,
    select,
    table,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.rag.evidence_rank_fusion import (
    CandidateStageSignal,
    EvidenceFusionError,
    FractionReceipt,
    HybridFusionCandidate,
    LexicalCandidateInput,
    LexicalFusionCandidate,
    StableCoordinate,
    fuse_hybrid_rrf,
    fuse_lexical_subsearches,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchExecutionBinding,
    EvidenceSearchFailure,
    EvidenceSearchFailureReason,
    EvidenceSearchRequest,
    EvidenceSearchSuccess,
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    ProductionSearchMethod,
    ProductionSearchSignal,
    RetrievalExecutionMode,
    format_observed_score,
    validate_search_request,
)

logger = logging.getLogger(__name__)

POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_ARTIFACT_CODE = "postgresql-evidence-search-adapter"
POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_VERSION = "1.0.0"
POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION_VERSION = "postgresql-evidence-search-adapter@1"

POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION: dict[str, Any] = {
    "projection_version": POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION_VERSION,
    "artifact_code": POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_ARTIFACT_CODE,
    "artifact_version": POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_VERSION,
    "database_engine": "postgresql",
    "contract": "knowledge-evidence-search-rrf-v1",
    "subsearches": ["dense", "exact", "fts", "trigram"],
    "fusion_algorithm": "rrf-rank-fusion@1",
    "runtime_module": "ai_worker.adapters.postgresql_evidence_search",
}

POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_HASH: str = canonical_sha256(POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION)

POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF: ImmutableArtifactRef = ImmutableArtifactRef(
    artifact_code=POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_ARTIFACT_CODE,
    version=POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_VERSION,
    content_sha256=POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_HASH,
)

SessionFactory = Callable[[], AsyncSession]

_SOURCE = table(
    "rag_source",
    column("id", String(36)),
    column("source_code", String(100)),
    column("lifecycle_status", String(20)),
)
_ENDPOINT = table(
    "rag_source_endpoint",
    column("id", String(36)),
    column("source_id", String(36)),
    column("lifecycle_status", String(20)),
    column("runtime_status", String(20)),
    column("acquisition_status", String(20)),
)
_OPERATION = table(
    "rag_source_operation",
    column("id", String(36)),
    column("endpoint_id", String(36)),
    column("runtime_status", String(20)),
    column("acquisition_status", String(20)),
)
_SNAPSHOT = table(
    "rag_source_snapshot",
    column("id", String(36)),
    column("operation_id", String(36)),
    column("source_version", String(200)),
    column("canonical_checksum", String(64)),
    column("verification_status", String(20)),
)
_SNAPSHOT_MEMBER = table(
    "rag_source_snapshot_member",
    column("id", String(36)),
    column("source_snapshot_id", String(36)),
    column("member_kind", String(30)),
    column("endpoint_id", String(36)),
    column("operation_id", String(36)),
    column("ingestion_artifact_id", String(36)),
    column("locator", String(500)),
    column("content_sha256", String(64)),
)
_INGESTION_RUN = table(
    "rag_source_ingestion_run",
    column("id", String(36)),
    column("operation_id", String(36)),
    column("snapshot_id", String(36)),
)
_INGESTION_ARTIFACT = table(
    "rag_source_ingestion_artifact",
    column("id", String(36)),
    column("ingestion_run_id", String(36)),
)
_DOCUMENT = table(
    "knowledge_document",
    column("id", String(36)),
    column("record_contract_version", String(40)),
    column("document_status", String(20)),
    column("source_snapshot_member_id", String(36)),
    column("external_document_id", String(300)),
    column("document_content_hash", String(64)),
    column("canonicalization_spec_version", String(100)),
)
_CHUNK = table(
    "knowledge_chunk",
    column("id", String(36)),
    column("knowledge_document_id", String(36)),
    column("chunk_index", Integer),
    column("chunk_text", String),
    column("content_hash", String(64)),
    column("normalization_version", String(100)),
)
_INDEX = table(
    "rag_knowledge_index",
    column("id", String(36)),
    column("index_code", String(120)),
    column("index_version", String(80)),
    column("corpus_manifest_hash", String(64)),
    column("embedding_manifest_hash", String(64)),
    column("index_configuration_hash", String(64)),
    column("embedding_model_ref", String(255)),
    column("embedding_model_version", String(80)),
    column("embedding_dimension", Integer),
    column("distance_metric", String(20)),
    column("member_count", Integer),
    column("created_at", DateTime(timezone=True)),
)
_INDEX_MEMBER = table(
    "rag_knowledge_index_member",
    column("id", String(36)),
    column("knowledge_index_id", String(36)),
    column("knowledge_chunk_id", String(36)),
    column("evidence_key", String(300)),
    column("source_snapshot_id", String(36)),
    column("source_snapshot_member_id", String(36)),
    column("source_code", String(100)),
    column("source_version", String(200)),
    column("canonical_checksum", String(64)),
    column("external_document_id", String(300)),
    column("chunk_index", Integer),
    column("content_hash", String(64)),
    column("embedding", VECTOR()),
    column("embedding_sha256", String(64)),
    column("member_order", Integer),
    column("created_at", DateTime(timezone=True)),
)


@dataclass(frozen=True, slots=True)
class _InternalProvenance:
    knowledge_index_id: UUID
    index_code: str
    index_version: str
    index_configuration_hash: str
    knowledge_chunk_id: UUID
    evidence_key: str
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    canonical_checksum: str
    external_document_id: str
    chunk_index: int
    locator: str
    content_hash: str
    canonicalization_spec_version: str
    normalization_version: str
    chunk_text: str


@dataclass(frozen=True, slots=True)
class _SubsearchRecord:
    source_code: str
    source_version: str
    external_document_id: str
    chunk_index: int
    score: float


@dataclass(frozen=True, slots=True)
class _DenseRecord:
    source_code: str
    source_version: str
    external_document_id: str
    chunk_index: int
    distance: float
    similarity: float


@dataclass(frozen=True, slots=True)
class _IndexMetadata:
    id: UUID
    index_code: str
    index_version: str
    index_configuration_hash: str
    embedding_dimension: int
    embedding_model_ref: str
    embedding_model_version: str


def _required_persisted_evidence_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("persisted evidence_key is missing")
    return value


def _to_production_provenance(p: _InternalProvenance) -> ProductionEvidenceProvenance:
    return ProductionEvidenceProvenance(
        knowledge_index_id=p.knowledge_index_id,
        index_code=p.index_code,
        index_version=p.index_version,
        index_configuration_hash=p.index_configuration_hash,
        knowledge_chunk_id=p.knowledge_chunk_id,
        evidence_key=p.evidence_key,
        source_snapshot_id=p.source_snapshot_id,
        source_snapshot_member_id=p.source_snapshot_member_id,
        source_code=p.source_code,
        source_version=p.source_version,
        canonical_checksum=p.canonical_checksum,
        external_document_id=p.external_document_id,
        chunk_index=p.chunk_index,
        locator=p.locator,
        content_hash=p.content_hash,
        canonicalization_spec_version=p.canonicalization_spec_version,
        normalization_version=p.normalization_version,
    )


def _collect_signals(
    *,
    exact_records: list[_SubsearchRecord],
    trigram_records: list[_SubsearchRecord],
    fts_records: list[_SubsearchRecord],
    dense_records: list[_DenseRecord],
    fused_lexical: Sequence[LexicalFusionCandidate],
    provenance_map: dict[tuple[str, str, str, int], _InternalProvenance],
) -> tuple[ProductionSearchSignal, ...]:
    raw_signals: list[ProductionSearchSignal] = []

    for rank, rec in enumerate(exact_records, start=1):
        key = (rec.source_code, rec.source_version, rec.external_document_id, rec.chunk_index)
        p = provenance_map[key]
        raw_signals.append(
            ProductionSearchSignal(
                provenance=_to_production_provenance(p),
                method=ProductionSearchMethod.EXACT,
                raw_rank=rank,
                observed_score="1",
            )
        )

    for rank, rec in enumerate(trigram_records, start=1):
        key = (rec.source_code, rec.source_version, rec.external_document_id, rec.chunk_index)
        p = provenance_map[key]
        raw_signals.append(
            ProductionSearchSignal(
                provenance=_to_production_provenance(p),
                method=ProductionSearchMethod.TRIGRAM,
                raw_rank=rank,
                observed_score=format_observed_score(rec.score),
            )
        )

    for rank, rec in enumerate(fts_records, start=1):
        key = (rec.source_code, rec.source_version, rec.external_document_id, rec.chunk_index)
        p = provenance_map[key]
        raw_signals.append(
            ProductionSearchSignal(
                provenance=_to_production_provenance(p),
                method=ProductionSearchMethod.FTS,
                raw_rank=rank,
                observed_score=format_observed_score(rec.score),
            )
        )

    for rank, d in enumerate(dense_records, start=1):
        key = (d.source_code, d.source_version, d.external_document_id, d.chunk_index)
        p = provenance_map[key]
        raw_signals.append(
            ProductionSearchSignal(
                provenance=_to_production_provenance(p),
                method=ProductionSearchMethod.DENSE,
                raw_rank=rank,
                observed_score=format_observed_score(d.similarity),
            )
        )

    for c in fused_lexical:
        key = (
            c.coordinate.source_code,
            c.coordinate.source_version,
            c.coordinate.external_document_id,
            c.coordinate.chunk_index,
        )
        p = provenance_map[key]
        score_dec = "1" if c.is_exact else format_observed_score(c.raw_trigram_score or 0.0)
        raw_signals.append(
            ProductionSearchSignal(
                provenance=_to_production_provenance(p),
                method=ProductionSearchMethod.LEXICAL,
                raw_rank=c.lexical_rank,
                observed_score=score_dec,
            )
        )

    def _sig_sort_key(s: ProductionSearchSignal) -> tuple[str, int, bytes, bytes, bytes, int]:
        return (
            s.method.value,
            s.raw_rank,
            s.provenance.source_code.encode("utf-8"),
            s.provenance.source_version.encode("utf-8"),
            s.provenance.external_document_id.encode("utf-8"),
            s.provenance.chunk_index,
        )

    raw_signals.sort(key=_sig_sort_key)
    return tuple(raw_signals)


class PostgresqlEvidenceSearchAdapter:
    """Production Knowledge Evidence Search port implementation using PostgreSQL."""

    def __init__(
        self,
        session_factory: SessionFactory,
        adapter_artifact_ref: ImmutableArtifactRef,
    ) -> None:
        self._session_factory = session_factory
        self._adapter_artifact_ref = adapter_artifact_ref

    async def search(
        self,
        request: EvidenceSearchRequest,
    ) -> EvidenceSearchSuccess | EvidenceSearchFailure:
        val_failure = validate_search_request(request)
        if val_failure is not None:
            return val_failure

        binding = request.execution_binding
        mode = binding.retrieval_config.execution_mode
        dense_active = mode in (RetrievalExecutionMode.DENSE_ONLY, RetrievalExecutionMode.HYBRID_RRF)
        lexical_active = mode in (RetrievalExecutionMode.LEXICAL_ONLY, RetrievalExecutionMode.HYBRID_RRF)

        try:
            tx_res = await self._execute_search_transaction(
                binding=binding,
                request=request,
                dense_active=dense_active,
                lexical_active=lexical_active,
            )
            if isinstance(tx_res, EvidenceSearchFailure):
                return tx_res
            exact_records, trigram_records, fts_records, dense_records, provenance_map = tx_res
        except Exception as exc:
            logger.error("Evidence search failed with database exception: %s", exc.__class__.__name__)
            return EvidenceSearchFailure(EvidenceSearchFailureReason.LEXICAL_DEPENDENCY_ERROR)

        return self._fuse_and_build_hits(
            request=request,
            binding=binding,
            exact_records=exact_records,
            trigram_records=trigram_records,
            fts_records=fts_records,
            dense_records=dense_records,
            provenance_map=provenance_map,
        )

    async def _execute_search_transaction(
        self,
        binding: EvidenceSearchExecutionBinding,
        request: EvidenceSearchRequest,
        *,
        dense_active: bool,
        lexical_active: bool,
    ) -> (
        tuple[
            list[_SubsearchRecord],
            list[_SubsearchRecord],
            list[_SubsearchRecord],
            list[_DenseRecord],
            dict[tuple[str, str, str, int], _InternalProvenance],
        ]
        | EvidenceSearchFailure
    ):
        async with self._session_factory() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            if lexical_active:
                trigram_th = binding.retrieval_config.lexical_config.trigram_threshold
                await session.execute(
                    text("SELECT set_config('pg_trgm.similarity_threshold', :th, true)"),
                    {"th": trigram_th},
                )

            index_meta = await self._fetch_and_validate_index(session, binding)
            if isinstance(index_meta, EvidenceSearchFailure):
                return index_meta

            if dense_active:
                assert request.query_embedding_receipt is not None
                receipt = request.query_embedding_receipt
                if (
                    receipt.dimension != index_meta.embedding_dimension
                    or receipt.model_ref != index_meta.embedding_model_ref
                    or receipt.model_version != index_meta.embedding_model_version
                ):
                    return EvidenceSearchFailure(EvidenceSearchFailureReason.QUERY_EMBEDDING_INVALID)

            exact_records: list[_SubsearchRecord] = []
            trigram_records: list[_SubsearchRecord] = []
            fts_records: list[_SubsearchRecord] = []
            provenance_map: dict[tuple[str, str, str, int], _InternalProvenance] = {}

            if lexical_active:
                lex_res = await self._execute_lexical_searches(
                    session,
                    binding,
                    request.normalized_query.reveal(),
                    index_meta,
                )
                if isinstance(lex_res, EvidenceSearchFailure):
                    return lex_res
                exact_records, trigram_records, fts_records, provenance_map = lex_res

            dense_records: list[_DenseRecord] = []
            if dense_active:
                assert request.query_embedding_receipt is not None
                dense_res = await self._execute_dense_search(
                    session,
                    binding,
                    request.query_embedding_receipt.embedding.reveal(),
                    index_meta,
                    provenance_map,
                )
                if isinstance(dense_res, EvidenceSearchFailure):
                    return dense_res
                dense_records = dense_res

            return exact_records, trigram_records, fts_records, dense_records, provenance_map

    def _create_search_hit(
        self,
        *,
        coord: StableCoordinate,
        fusion_rank: int,
        frac_receipt: FractionReceipt,
        is_eligible: bool,
        lex_rank: int | None = None,
        dense_rank: int | None = None,
        provenance_map: dict[tuple[str, str, str, int], _InternalProvenance],
        query_text: str,
        trigram_score_map: dict[tuple[str, str, str, int], float],
        fts_score_map: dict[tuple[str, str, str, int], float],
        dense_signals: dict[tuple[str, str, str, int], CandidateStageSignal],
    ) -> ProductionSearchHit:
        key = (coord.source_code, coord.source_version, coord.external_document_id, coord.chunk_index)
        p = provenance_map[key]
        prov = _to_production_provenance(p)
        is_exact = query_text in p.chunk_text
        trig_score = trigram_score_map.get(key)
        fts_score = fts_score_map.get(key)
        dense_sig = dense_signals.get(key)
        return ProductionSearchHit(
            provenance=prov,
            coordinate=coord,
            exact_hit=is_exact,
            observed_trigram_score=format_observed_score(trig_score) if trig_score is not None else None,
            observed_fts_score=format_observed_score(fts_score) if fts_score is not None else None,
            observed_dense_score=dense_sig.score_decimal if dense_sig is not None else None,
            lexical_rank=lex_rank,
            dense_rank=dense_rank,
            fusion_rank=fusion_rank,
            fraction_receipt=frac_receipt,
            is_eligible_for_future_reranker=is_eligible,
        )

    def _resolve_hybrid_hits(
        self,
        *,
        mode: RetrievalExecutionMode,
        lexical_hits: tuple[ProductionSearchHit, ...],
        dense_hits: tuple[ProductionSearchHit, ...],
        fused_lexical: Sequence[LexicalFusionCandidate],
        all_lexical_coords: set[tuple[str, str, str, int]],
        dense_signals: dict[tuple[str, str, str, int], CandidateStageSignal],
        binding: EvidenceSearchExecutionBinding,
        provenance_map: dict[tuple[str, str, str, int], _InternalProvenance],
        query_text: str,
        trigram_score_map: dict[tuple[str, str, str, int], float],
        fts_score_map: dict[tuple[str, str, str, int], float],
    ) -> tuple[ProductionSearchHit, ...]:
        if mode == RetrievalExecutionMode.LEXICAL_ONLY:
            return lexical_hits
        if mode == RetrievalExecutionMode.DENSE_ONLY:
            return dense_hits

        all_candidate_coords = all_lexical_coords | set(dense_signals.keys())
        lexical_signals = {
            (
                c.coordinate.source_code,
                c.coordinate.source_version,
                c.coordinate.external_document_id,
                c.coordinate.chunk_index,
            ): CandidateStageSignal(
                rank=c.lexical_rank,
                score_decimal="1" if c.is_exact else format_observed_score(c.raw_trigram_score or 0.0),
            )
            for c in fused_lexical
        }

        hybrid_inputs = [
            HybridFusionCandidate(
                coordinate=StableCoordinate(
                    source_code=provenance_map[k].source_code,
                    source_version=provenance_map[k].source_version,
                    external_document_id=provenance_map[k].external_document_id,
                    chunk_index=provenance_map[k].chunk_index,
                ),
                content_hash=provenance_map[k].content_hash,
                lexical_signal=lexical_signals.get(k),
                dense_signal=dense_signals.get(k),
            )
            for k in all_candidate_coords
        ]

        fused_hybrid = fuse_hybrid_rrf(
            hybrid_inputs,
            rrf_k=binding.retrieval_config.rrf_k,
            hybrid_limit=binding.retrieval_config.hybrid_limit,
            reranker_input_limit=binding.retrieval_config.future_reranker_input_limit,
        )

        return tuple(
            self._create_search_hit(
                coord=h.coordinate,
                fusion_rank=h.fusion_rank,
                frac_receipt=h.fraction_receipt,
                is_eligible=h.is_eligible_for_future_reranker,
                lex_rank=h.lexical_signal.rank if h.lexical_signal else None,
                dense_rank=h.dense_signal.rank if h.dense_signal else None,
                provenance_map=provenance_map,
                query_text=query_text,
                trigram_score_map=trigram_score_map,
                fts_score_map=fts_score_map,
                dense_signals=dense_signals,
            )
            for h in fused_hybrid
        )

    def _fuse_and_build_hits(
        self,
        request: EvidenceSearchRequest,
        binding: EvidenceSearchExecutionBinding,
        exact_records: list[_SubsearchRecord],
        trigram_records: list[_SubsearchRecord],
        fts_records: list[_SubsearchRecord],
        dense_records: list[_DenseRecord],
        provenance_map: dict[tuple[str, str, str, int], _InternalProvenance],
    ) -> EvidenceSearchSuccess | EvidenceSearchFailure:
        query_text = request.normalized_query.reveal()
        mode = binding.retrieval_config.execution_mode
        try:
            all_lexical_coords: set[tuple[str, str, str, int]] = {
                (r.source_code, r.source_version, r.external_document_id, r.chunk_index)
                for r in exact_records + trigram_records + fts_records
            }
            trigram_score_map: dict[tuple[str, str, str, int], float] = {
                (r.source_code, r.source_version, r.external_document_id, r.chunk_index): r.score
                for r in trigram_records
            }
            fts_score_map: dict[tuple[str, str, str, int], float] = {
                (r.source_code, r.source_version, r.external_document_id, r.chunk_index): r.score for r in fts_records
            }

            fused_lexical = []
            if mode in (RetrievalExecutionMode.LEXICAL_ONLY, RetrievalExecutionMode.HYBRID_RRF):
                lexical_inputs = [
                    LexicalCandidateInput(
                        coordinate=StableCoordinate(
                            source_code=provenance_map[k].source_code,
                            source_version=provenance_map[k].source_version,
                            external_document_id=provenance_map[k].external_document_id,
                            chunk_index=provenance_map[k].chunk_index,
                        ),
                        content_hash=provenance_map[k].content_hash,
                        is_exact=query_text in provenance_map[k].chunk_text,
                        trigram_score=trigram_score_map.get(k),
                        fts_score=fts_score_map.get(k),
                    )
                    for k in all_lexical_coords
                ]

                fused_lexical = fuse_lexical_subsearches(
                    lexical_inputs,
                    limit=binding.retrieval_config.lexical_limit,
                    rrf_k=binding.retrieval_config.rrf_k,
                )

            dense_signals: dict[tuple[str, str, str, int], CandidateStageSignal] = {}
            if mode in (RetrievalExecutionMode.DENSE_ONLY, RetrievalExecutionMode.HYBRID_RRF):
                for rank, d in enumerate(dense_records, start=1):
                    key = (d.source_code, d.source_version, d.external_document_id, d.chunk_index)
                    dense_signals[key] = CandidateStageSignal(
                        rank=rank,
                        score_decimal=format_observed_score(d.similarity),
                    )

            signals = _collect_signals(
                exact_records=exact_records,
                trigram_records=trigram_records,
                fts_records=fts_records,
                dense_records=dense_records,
                fused_lexical=fused_lexical,
                provenance_map=provenance_map,
            )

            lexical_hits = tuple(
                self._create_search_hit(
                    coord=c.coordinate,
                    fusion_rank=c.lexical_rank,
                    frac_receipt=c.fraction_receipt,
                    is_eligible=c.lexical_rank <= binding.retrieval_config.future_reranker_input_limit,
                    lex_rank=c.lexical_rank,
                    dense_rank=None,
                    provenance_map=provenance_map,
                    query_text=query_text,
                    trigram_score_map=trigram_score_map,
                    fts_score_map=fts_score_map,
                    dense_signals=dense_signals,
                )
                for c in fused_lexical
            )

            dense_hits = tuple(
                self._create_search_hit(
                    coord=StableCoordinate(
                        source_code=d.source_code,
                        source_version=d.source_version,
                        external_document_id=d.external_document_id,
                        chunk_index=d.chunk_index,
                    ),
                    fusion_rank=r,
                    frac_receipt=FractionReceipt(numerator="1", denominator=str(binding.retrieval_config.rrf_k + r)),
                    is_eligible=r <= binding.retrieval_config.future_reranker_input_limit,
                    lex_rank=None,
                    dense_rank=r,
                    provenance_map=provenance_map,
                    query_text=query_text,
                    trigram_score_map=trigram_score_map,
                    fts_score_map=fts_score_map,
                    dense_signals=dense_signals,
                )
                for r, d in enumerate(dense_records, start=1)
            )

            hybrid_hits = self._resolve_hybrid_hits(
                mode=mode,
                lexical_hits=lexical_hits,
                dense_hits=dense_hits,
                fused_lexical=fused_lexical,
                all_lexical_coords=all_lexical_coords,
                dense_signals=dense_signals,
                binding=binding,
                provenance_map=provenance_map,
                query_text=query_text,
                trigram_score_map=trigram_score_map,
                fts_score_map=fts_score_map,
            )

            return EvidenceSearchSuccess(
                request=request,
                adapter_artifact_ref=self._adapter_artifact_ref,
                lexical_hits=lexical_hits,
                dense_hits=dense_hits,
                hybrid_hits=hybrid_hits,
                signals=signals,
            )

        except EvidenceFusionError as e:
            logger.error("Fusion failed: %s", e.code)
            return EvidenceSearchFailure(EvidenceSearchFailureReason.FUSION_INPUT_INVALID)
        except Exception as e:
            logger.error("Fusion processing error: %s", e.__class__.__name__)
            return EvidenceSearchFailure(EvidenceSearchFailureReason.SEARCH_RESULT_INVALID)

    async def _fetch_and_validate_index(
        self,
        session: AsyncSession,
        binding: EvidenceSearchExecutionBinding,
    ) -> _IndexMetadata | EvidenceSearchFailure:
        stmt = select(
            _INDEX.c.id,
            _INDEX.c.index_code,
            _INDEX.c.index_version,
            _INDEX.c.index_configuration_hash,
            _INDEX.c.embedding_dimension,
            _INDEX.c.embedding_model_ref,
            _INDEX.c.embedding_model_version,
        ).where(_INDEX.c.id == str(binding.knowledge_index_id))
        res = (await session.execute(stmt)).mappings().one_or_none()
        if res is None:
            return EvidenceSearchFailure(EvidenceSearchFailureReason.INDEX_BINDING_INVALID)

        expected_ref = binding.evidence_index_ref
        if (
            str(res["index_code"]) != expected_ref.artifact_code
            or str(res["index_version"]) != expected_ref.version
            or str(res["index_configuration_hash"]) != expected_ref.content_sha256
        ):
            return EvidenceSearchFailure(EvidenceSearchFailureReason.INDEX_BINDING_INVALID)

        return _IndexMetadata(
            id=UUID(str(res["id"])),
            index_code=str(res["index_code"]),
            index_version=str(res["index_version"]),
            index_configuration_hash=str(res["index_configuration_hash"]),
            embedding_dimension=int(res["embedding_dimension"]),
            embedding_model_ref=str(res["embedding_model_ref"]),
            embedding_model_version=str(res["embedding_model_version"]),
        )

    def _build_base_eligible_query(
        self,
        binding: EvidenceSearchExecutionBinding,
    ):
        allowed_snaps = [str(sid) for sid in binding.allowed_source_snapshot_ids]
        allowed_members = [str(mid) for mid in binding.allowed_source_snapshot_member_ids]

        stmt = (
            select(
                _INDEX_MEMBER.c.knowledge_index_id,
                _INDEX_MEMBER.c.knowledge_chunk_id,
                _INDEX_MEMBER.c.evidence_key,
                _INDEX_MEMBER.c.source_snapshot_id,
                _INDEX_MEMBER.c.source_snapshot_member_id,
                _INDEX_MEMBER.c.source_code,
                _INDEX_MEMBER.c.source_version,
                _INDEX_MEMBER.c.canonical_checksum,
                _INDEX_MEMBER.c.external_document_id,
                _INDEX_MEMBER.c.chunk_index,
                _INDEX_MEMBER.c.content_hash,
                _CHUNK.c.chunk_text,
                _CHUNK.c.normalization_version,
                _DOCUMENT.c.canonicalization_spec_version,
                _SNAPSHOT_MEMBER.c.locator,
                _INDEX_MEMBER.c.embedding,
            )
            .select_from(_INDEX_MEMBER)
            .join(_CHUNK, _CHUNK.c.id == _INDEX_MEMBER.c.knowledge_chunk_id)
            .join(_DOCUMENT, _DOCUMENT.c.id == _CHUNK.c.knowledge_document_id)
            .join(_SNAPSHOT_MEMBER, _SNAPSHOT_MEMBER.c.id == _INDEX_MEMBER.c.source_snapshot_member_id)
            .join(_SNAPSHOT, _SNAPSHOT.c.id == _INDEX_MEMBER.c.source_snapshot_id)
            .outerjoin(_INGESTION_ARTIFACT, _INGESTION_ARTIFACT.c.id == _SNAPSHOT_MEMBER.c.ingestion_artifact_id)
            .outerjoin(_INGESTION_RUN, _INGESTION_RUN.c.id == _INGESTION_ARTIFACT.c.ingestion_run_id)
            .join(
                _OPERATION,
                or_(
                    _OPERATION.c.id == _SNAPSHOT_MEMBER.c.operation_id,
                    _OPERATION.c.id == _INGESTION_RUN.c.operation_id,
                    _OPERATION.c.id == _SNAPSHOT.c.operation_id,
                ),
            )
            .join(_ENDPOINT, _ENDPOINT.c.id == _OPERATION.c.endpoint_id)
            .join(_SOURCE, _SOURCE.c.id == _ENDPOINT.c.source_id)
            .where(
                and_(
                    _INDEX_MEMBER.c.knowledge_index_id == str(binding.knowledge_index_id),
                    _INDEX_MEMBER.c.source_snapshot_id.in_(allowed_snaps),
                    _INDEX_MEMBER.c.source_snapshot_member_id.in_(allowed_members),
                    _SOURCE.c.lifecycle_status == "ACTIVE",
                    _SNAPSHOT.c.verification_status == "CURRENT",
                    _ENDPOINT.c.lifecycle_status == "VERIFIED",
                    _ENDPOINT.c.runtime_status == "ENABLED",
                    _ENDPOINT.c.acquisition_status == "APPROVED",
                    _OPERATION.c.runtime_status == "ENABLED",
                    _OPERATION.c.acquisition_status == "APPROVED",
                    _DOCUMENT.c.record_contract_version == "KNOWLEDGE_EVIDENCE_V1",
                    _CHUNK.c.content_hash == _INDEX_MEMBER.c.content_hash,
                )
            )
        )
        return stmt

    async def _execute_lexical_searches(
        self,
        session: AsyncSession,
        binding: EvidenceSearchExecutionBinding,
        query_text: str,
        index_meta: _IndexMetadata,
    ) -> (
        tuple[
            list[_SubsearchRecord],
            list[_SubsearchRecord],
            list[_SubsearchRecord],
            dict[tuple[str, str, str, int], _InternalProvenance],
        ]
        | EvidenceSearchFailure
    ):
        lex_cfg = binding.retrieval_config.lexical_config
        base_stmt = self._build_base_eligible_query(binding)

        exact_stmt = (
            base_stmt.add_columns(
                func.similarity(_CHUNK.c.chunk_text, query_text).label("similarity_score"),
                func.ts_rank_cd(
                    func.to_tsvector(text("'simple'"), _CHUNK.c.chunk_text),
                    func.plainto_tsquery(text("'simple'"), query_text),
                ).label("fts_score"),
            )
            .where(func.strpos(_CHUNK.c.chunk_text, query_text) > 0)
            .order_by(
                func.similarity(_CHUNK.c.chunk_text, query_text).desc(),
                func.ts_rank_cd(
                    func.to_tsvector(text("'simple'"), _CHUNK.c.chunk_text),
                    func.plainto_tsquery(text("'simple'"), query_text),
                ).desc(),
                func.convert_to(_INDEX_MEMBER.c.source_code, "UTF8").asc(),
                func.convert_to(_INDEX_MEMBER.c.source_version, "UTF8").asc(),
                func.convert_to(_INDEX_MEMBER.c.external_document_id, "UTF8").asc(),
                _INDEX_MEMBER.c.chunk_index.asc(),
            )
            .limit(lex_cfg.exact_limit)
        )

        trigram_stmt = (
            base_stmt.add_columns(
                func.similarity(_CHUNK.c.chunk_text, query_text).label("similarity_score"),
            )
            .where(text("knowledge_chunk.chunk_text % :query_param"))
            .params(query_param=query_text)
            .order_by(
                func.similarity(_CHUNK.c.chunk_text, query_text).desc(),
                func.convert_to(_INDEX_MEMBER.c.source_code, "UTF8").asc(),
                func.convert_to(_INDEX_MEMBER.c.source_version, "UTF8").asc(),
                func.convert_to(_INDEX_MEMBER.c.external_document_id, "UTF8").asc(),
                _INDEX_MEMBER.c.chunk_index.asc(),
            )
            .limit(lex_cfg.trigram_limit)
        )

        fts_stmt = (
            base_stmt.add_columns(
                func.ts_rank_cd(
                    func.to_tsvector(text("'simple'"), _CHUNK.c.chunk_text),
                    func.plainto_tsquery(text("'simple'"), query_text),
                ).label("fts_score"),
            )
            .where(text("to_tsvector('simple', knowledge_chunk.chunk_text) @@ plainto_tsquery('simple', :query_param)"))
            .params(query_param=query_text)
            .order_by(
                func.ts_rank_cd(
                    func.to_tsvector(text("'simple'"), _CHUNK.c.chunk_text),
                    func.plainto_tsquery(text("'simple'"), query_text),
                ).desc(),
                func.convert_to(_INDEX_MEMBER.c.source_code, "UTF8").asc(),
                func.convert_to(_INDEX_MEMBER.c.source_version, "UTF8").asc(),
                func.convert_to(_INDEX_MEMBER.c.external_document_id, "UTF8").asc(),
                _INDEX_MEMBER.c.chunk_index.asc(),
            )
            .limit(lex_cfg.fts_limit)
        )

        exact_res = (await session.execute(exact_stmt)).mappings().all()
        trigram_res = (await session.execute(trigram_stmt)).mappings().all()
        fts_res = (await session.execute(fts_stmt)).mappings().all()

        provenance_map: dict[tuple[str, str, str, int], _InternalProvenance] = {}

        def _record_row(row, score_name: str | None = None) -> _SubsearchRecord:
            key = (
                str(row["source_code"]),
                str(row["source_version"]),
                str(row["external_document_id"]),
                int(row["chunk_index"]),
            )
            if key not in provenance_map:
                chunk_bytes = str(row["chunk_text"]).encode("utf-8")
                computed_hash = hashlib.sha256(chunk_bytes).hexdigest()
                if computed_hash != str(row["content_hash"]):
                    raise ValueError(f"Content hash mismatch for chunk {row['knowledge_chunk_id']}")

                provenance_map[key] = _InternalProvenance(
                    knowledge_index_id=UUID(str(row["knowledge_index_id"])),
                    index_code=index_meta.index_code,
                    index_version=index_meta.index_version,
                    index_configuration_hash=index_meta.index_configuration_hash,
                    knowledge_chunk_id=UUID(str(row["knowledge_chunk_id"])),
                    evidence_key=_required_persisted_evidence_key(row["evidence_key"]),
                    source_snapshot_id=UUID(str(row["source_snapshot_id"])),
                    source_snapshot_member_id=UUID(str(row["source_snapshot_member_id"])),
                    source_code=str(row["source_code"]),
                    source_version=str(row["source_version"]),
                    canonical_checksum=str(row["canonical_checksum"]),
                    external_document_id=str(row["external_document_id"]),
                    chunk_index=int(row["chunk_index"]),
                    locator=str(row["locator"]),
                    content_hash=str(row["content_hash"]),
                    canonicalization_spec_version=str(row["canonicalization_spec_version"]),
                    normalization_version=str(row["normalization_version"]),
                    chunk_text=str(row["chunk_text"]),
                )
            raw_score = float(row[score_name]) if score_name else 1.0
            return _SubsearchRecord(
                source_code=str(row["source_code"]),
                source_version=str(row["source_version"]),
                external_document_id=str(row["external_document_id"]),
                chunk_index=int(row["chunk_index"]),
                score=raw_score,
            )

        try:
            exact_records = [_record_row(r) for r in exact_res]
            trigram_records = [_record_row(r, "similarity_score") for r in trigram_res]
            fts_records = [_record_row(r, "fts_score") for r in fts_res]
        except ValueError:
            return EvidenceSearchFailure(EvidenceSearchFailureReason.SEARCH_RESULT_INVALID)

        return exact_records, trigram_records, fts_records, provenance_map

    async def _execute_dense_search(
        self,
        session: AsyncSession,
        binding: EvidenceSearchExecutionBinding,
        query_vector: Sequence[float],
        index_meta: _IndexMetadata,
        provenance_map: dict[tuple[str, str, str, int], _InternalProvenance],
    ) -> list[_DenseRecord] | EvidenceSearchFailure:
        dense_cfg = binding.retrieval_config.dense_config
        assert dense_cfg is not None
        base_stmt = self._build_base_eligible_query(binding)

        distance_expr = _INDEX_MEMBER.c.embedding.cosine_distance(list(query_vector)).label("distance")
        stmt = (
            base_stmt.add_columns(distance_expr)
            .order_by(
                distance_expr.asc(),
                func.convert_to(_INDEX_MEMBER.c.source_code, "UTF8").asc(),
                func.convert_to(_INDEX_MEMBER.c.source_version, "UTF8").asc(),
                func.convert_to(_INDEX_MEMBER.c.external_document_id, "UTF8").asc(),
                _INDEX_MEMBER.c.chunk_index.asc(),
            )
            .limit(binding.retrieval_config.dense_limit)
        )

        res = (await session.execute(stmt)).mappings().all()
        dense_records: list[_DenseRecord] = []
        for row in res:
            key = (
                str(row["source_code"]),
                str(row["source_version"]),
                str(row["external_document_id"]),
                int(row["chunk_index"]),
            )
            dist = float(row["distance"])
            if not (0.0 <= dist <= 2.0):
                return EvidenceSearchFailure(EvidenceSearchFailureReason.SEARCH_RESULT_INVALID)
            sim = 1.0 - dist
            if not (-1.0 <= sim <= 1.0):
                return EvidenceSearchFailure(EvidenceSearchFailureReason.SEARCH_RESULT_INVALID)

            if key not in provenance_map:
                chunk_bytes = str(row["chunk_text"]).encode("utf-8")
                computed_hash = hashlib.sha256(chunk_bytes).hexdigest()
                if computed_hash != str(row["content_hash"]):
                    return EvidenceSearchFailure(EvidenceSearchFailureReason.SEARCH_RESULT_INVALID)

                try:
                    evidence_key = _required_persisted_evidence_key(row["evidence_key"])
                except ValueError:
                    return EvidenceSearchFailure(EvidenceSearchFailureReason.SEARCH_RESULT_INVALID)

                provenance_map[key] = _InternalProvenance(
                    knowledge_index_id=UUID(str(row["knowledge_index_id"])),
                    index_code=index_meta.index_code,
                    index_version=index_meta.index_version,
                    index_configuration_hash=index_meta.index_configuration_hash,
                    knowledge_chunk_id=UUID(str(row["knowledge_chunk_id"])),
                    evidence_key=evidence_key,
                    source_snapshot_id=UUID(str(row["source_snapshot_id"])),
                    source_snapshot_member_id=UUID(str(row["source_snapshot_member_id"])),
                    source_code=str(row["source_code"]),
                    source_version=str(row["source_version"]),
                    canonical_checksum=str(row["canonical_checksum"]),
                    external_document_id=str(row["external_document_id"]),
                    chunk_index=int(row["chunk_index"]),
                    locator=str(row["locator"]),
                    content_hash=str(row["content_hash"]),
                    canonicalization_spec_version=str(row["canonicalization_spec_version"]),
                    normalization_version=str(row["normalization_version"]),
                    chunk_text=str(row["chunk_text"]),
                )

            dense_records.append(
                _DenseRecord(
                    source_code=str(row["source_code"]),
                    source_version=str(row["source_version"]),
                    external_document_id=str(row["external_document_id"]),
                    chunk_index=int(row["chunk_index"]),
                    distance=dist,
                    similarity=sim,
                )
            )

        return dense_records
