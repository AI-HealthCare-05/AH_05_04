from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

from ai_worker.tasks.evaluation.canonical import JsonValue
from ai_worker.tasks.rag.evidence_rank_fusion import FractionReceipt, StableCoordinate
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, QueryFingerprint
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchPort,
    EvidenceSearchRequest,
    EvidenceSearchSuccess,
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    QueryEmbeddingReceipt,
    RetrievalExecutionMode,
)
from ai_worker.tasks.rag.knowledge_evidence_index import canonical_embedding_sha256
from ai_worker.tasks.rag.production_evidence_gate import (
    EvidenceGateFailure,
    EvidenceGateNoResult,
    EvidenceGateOutcome,
    EvidenceGateReason,
    EvidenceGateStatus,
    EvidenceGateSuccess,
    PostSearchEligibilityRequest,
    PostSearchEligibilitySuccess,
    PreSearchEligibilityRequest,
    PreSearchEligibilitySuccess,
    ProductionEvidenceEligibilityVerifierPort,
    evaluate_evidence_gate,
)
from ai_worker.tasks.rag.retrieval_run import (
    BeginRetrievalRunFailureReason,
    BeginRetrievalRunRequest,
    BeginRetrievalRunSuccess,
    FinalizeRetrievalRunRequest,
    FinalizeRetrievalRunSuccess,
    PersistedHitInput,
    PersistedRetrievalRunReceipt,
    PersistedSignalInput,
    PersistedTerminalReplayPayload,
    RetrievalRunStorePort,
    compute_hit_manifest_hash,
    compute_signal_manifest_hash,
    sha256_canonical_json,
)
from ai_worker.tasks.rag.text_embedding import TextEmbeddingPort, TextEmbeddingSuccess

logger = logging.getLogger(__name__)

HYBRID_RETRIEVE_NODE_ID = "hybrid_retrieve"
EVIDENCE_GATE_NODE_ID = "evidence_gate"

PRODUCTION_EMBEDDING_MODEL_REF = "openai:text-embedding-3-large"
PRODUCTION_EMBEDDING_MODEL_VERSION = "text-embedding-3-large"
PRODUCTION_EMBEDDING_DIMENSION = 1536


class RetrievalExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"


@dataclass(frozen=True, slots=True)
class ProductionSearchReceipt:
    artifact_ref: ImmutableArtifactRef
    variant: str  # "RET-L", "RET-D", "RET-H"
    retrieval_execution_status: RetrievalExecutionStatus
    diagnostic_code: str
    query_fingerprint: QueryFingerprint
    filter_snapshot_ref: ImmutableArtifactRef
    evidence_index_ref: ImmutableArtifactRef
    retrieval_config_ref: ImmutableArtifactRef
    adapter_artifact_ref: ImmutableArtifactRef
    query_embedding_sha256: str | None
    signal_manifest_sha256: str
    hit_manifest_sha256: str
    selection_manifest_sha256: str


RETRIEVAL_SELECTION_MANIFEST_PROJECTION_VERSION = "retrieval-selection-manifest-v2"
PRODUCTION_SEARCH_RECEIPT_VERSION = "2.0"
PRODUCTION_SEARCH_RECEIPT_PROJECTION_VERSION = "production-search-receipt-v2"


def selection_manifest_projection(selected_hits: Sequence[ProductionSearchHit]) -> JsonValue:
    seen_ranks: set[int] = set()
    for h in selected_hits:
        if h.fusion_rank <= 0:
            raise ValueError(f"Invalid fusion_rank {h.fusion_rank}: must be > 0")
        if h.fusion_rank in seen_ranks:
            raise ValueError(f"Duplicate fusion_rank {h.fusion_rank} in selection")
        seen_ranks.add(h.fusion_rank)

    sorted_hits = sorted(selected_hits, key=lambda h: h.fusion_rank)
    selections: list[JsonValue] = [
        {
            "canonical_checksum": h.provenance.canonical_checksum,
            "canonicalization_spec_version": h.provenance.canonicalization_spec_version,
            "chunk_index": h.provenance.chunk_index,
            "content_sha256": h.provenance.content_hash,
            "external_document_id": h.provenance.external_document_id,
            "final_rank": h.fusion_rank,
            "index_code": h.provenance.index_code,
            "index_configuration_hash": h.provenance.index_configuration_hash,
            "index_version": h.provenance.index_version,
            "knowledge_chunk_id": str(h.provenance.knowledge_chunk_id),
            "knowledge_index_id": str(h.provenance.knowledge_index_id),
            "locator": h.provenance.locator,
            "normalization_version": h.provenance.normalization_version,
            "source_code": h.provenance.source_code,
            "source_snapshot_id": str(h.provenance.source_snapshot_id),
            "source_snapshot_member_id": str(h.provenance.source_snapshot_member_id),
            "source_version": h.provenance.source_version,
        }
        for h in sorted_hits
    ]
    return {
        "projection_version": RETRIEVAL_SELECTION_MANIFEST_PROJECTION_VERSION,
        "selections": selections,
    }


def compute_selection_manifest_hash(selected_hits: Sequence[ProductionSearchHit]) -> str:
    return sha256_canonical_json(selection_manifest_projection(selected_hits))


def _artifact_ref_projection(ref: ImmutableArtifactRef) -> dict[str, JsonValue]:
    return {
        "artifact_code": ref.artifact_code,
        "content_sha256": ref.content_sha256,
        "version": ref.version,
    }


def _query_fingerprint_projection(fp: QueryFingerprint) -> dict[str, JsonValue]:
    return {
        "algorithm": fp.algorithm,
        "digest": fp.digest,
        "key_version": fp.key_version,
    }


def production_search_receipt_projection(
    *,
    variant: str,
    status: RetrievalExecutionStatus,
    diagnostic_code: str,
    query_fingerprint: QueryFingerprint,
    filter_snapshot_ref: ImmutableArtifactRef,
    evidence_index_ref: ImmutableArtifactRef,
    retrieval_config_ref: ImmutableArtifactRef,
    adapter_artifact_ref: ImmutableArtifactRef,
    query_embedding_sha256: str | None,
    signal_manifest_sha256: str,
    hit_manifest_sha256: str,
    selection_manifest_sha256: str,
) -> JsonValue:
    return {
        "adapter_artifact_ref": _artifact_ref_projection(adapter_artifact_ref),
        "diagnostic_code": diagnostic_code,
        "evidence_index_ref": _artifact_ref_projection(evidence_index_ref),
        "filter_snapshot_ref": _artifact_ref_projection(filter_snapshot_ref),
        "hit_manifest_sha256": hit_manifest_sha256,
        "projection_version": PRODUCTION_SEARCH_RECEIPT_PROJECTION_VERSION,
        "query_embedding_sha256": query_embedding_sha256,
        "query_fingerprint": _query_fingerprint_projection(query_fingerprint),
        "retrieval_config_ref": _artifact_ref_projection(retrieval_config_ref),
        "selection_manifest_sha256": selection_manifest_sha256,
        "signal_manifest_sha256": signal_manifest_sha256,
        "status": status.value,
        "variant": variant,
    }


def compute_production_search_receipt(
    *,
    variant: str,
    status: RetrievalExecutionStatus,
    diagnostic_code: str,
    query_fingerprint: QueryFingerprint,
    filter_snapshot_ref: ImmutableArtifactRef,
    evidence_index_ref: ImmutableArtifactRef,
    retrieval_config_ref: ImmutableArtifactRef,
    adapter_artifact_ref: ImmutableArtifactRef,
    query_embedding_sha256: str | None,
    signal_manifest_sha256: str,
    hit_manifest_sha256: str,
    selection_manifest_sha256: str,
) -> ProductionSearchReceipt:
    projection = production_search_receipt_projection(
        variant=variant,
        status=status,
        diagnostic_code=diagnostic_code,
        query_fingerprint=query_fingerprint,
        filter_snapshot_ref=filter_snapshot_ref,
        evidence_index_ref=evidence_index_ref,
        retrieval_config_ref=retrieval_config_ref,
        adapter_artifact_ref=adapter_artifact_ref,
        query_embedding_sha256=query_embedding_sha256,
        signal_manifest_sha256=signal_manifest_sha256,
        hit_manifest_sha256=hit_manifest_sha256,
        selection_manifest_sha256=selection_manifest_sha256,
    )
    artifact_hash = sha256_canonical_json(projection)
    artifact_ref = ImmutableArtifactRef(
        artifact_code="production_search_receipt",
        version=PRODUCTION_SEARCH_RECEIPT_VERSION,
        content_sha256=artifact_hash,
    )
    return ProductionSearchReceipt(
        artifact_ref=artifact_ref,
        variant=variant,
        retrieval_execution_status=status,
        diagnostic_code=diagnostic_code,
        query_fingerprint=query_fingerprint,
        filter_snapshot_ref=filter_snapshot_ref,
        evidence_index_ref=evidence_index_ref,
        retrieval_config_ref=retrieval_config_ref,
        adapter_artifact_ref=adapter_artifact_ref,
        query_embedding_sha256=query_embedding_sha256,
        signal_manifest_sha256=signal_manifest_sha256,
        hit_manifest_sha256=hit_manifest_sha256,
        selection_manifest_sha256=selection_manifest_sha256,
    )


def _search_receipt_replay_projection(receipt: ProductionSearchReceipt) -> dict[str, Any]:
    projection = cast(
        dict[str, Any],
        production_search_receipt_projection(
            variant=receipt.variant,
            status=receipt.retrieval_execution_status,
            diagnostic_code=receipt.diagnostic_code,
            query_fingerprint=receipt.query_fingerprint,
            filter_snapshot_ref=receipt.filter_snapshot_ref,
            evidence_index_ref=receipt.evidence_index_ref,
            retrieval_config_ref=receipt.retrieval_config_ref,
            adapter_artifact_ref=receipt.adapter_artifact_ref,
            query_embedding_sha256=receipt.query_embedding_sha256,
            signal_manifest_sha256=receipt.signal_manifest_sha256,
            hit_manifest_sha256=receipt.hit_manifest_sha256,
            selection_manifest_sha256=receipt.selection_manifest_sha256,
        ),
    )
    return {"artifact_ref": _artifact_ref_projection(receipt.artifact_ref), "projection": projection}


def _selected_hit_replay_projection(hit: ProductionSearchHit) -> dict[str, Any]:
    provenance = hit.provenance
    return {
        "coordinate": {
            "chunk_index": hit.coordinate.chunk_index,
            "external_document_id": hit.coordinate.external_document_id,
            "source_code": hit.coordinate.source_code,
            "source_version": hit.coordinate.source_version,
        },
        "dense_rank": hit.dense_rank,
        "exact_hit": hit.exact_hit,
        "fraction_receipt": {
            "denominator": hit.fraction_receipt.denominator,
            "numerator": hit.fraction_receipt.numerator,
        },
        "fusion_rank": hit.fusion_rank,
        "is_eligible_for_future_reranker": hit.is_eligible_for_future_reranker,
        "lexical_rank": hit.lexical_rank,
        "observed_dense_score": hit.observed_dense_score,
        "observed_fts_score": hit.observed_fts_score,
        "observed_trigram_score": hit.observed_trigram_score,
        "provenance": {
            "canonical_checksum": provenance.canonical_checksum,
            "canonicalization_spec_version": provenance.canonicalization_spec_version,
            "chunk_index": provenance.chunk_index,
            "content_hash": provenance.content_hash,
            "external_document_id": provenance.external_document_id,
            "index_code": provenance.index_code,
            "index_configuration_hash": provenance.index_configuration_hash,
            "index_version": provenance.index_version,
            "knowledge_chunk_id": str(provenance.knowledge_chunk_id),
            "knowledge_index_id": str(provenance.knowledge_index_id),
            "locator": provenance.locator,
            "normalization_version": provenance.normalization_version,
            "source_code": provenance.source_code,
            "source_snapshot_id": str(provenance.source_snapshot_id),
            "source_snapshot_member_id": str(provenance.source_snapshot_member_id),
            "source_version": provenance.source_version,
        },
    }


def _make_terminal_replay_payload(
    receipt: ProductionSearchReceipt,
    gate_outcome: EvidenceGateOutcome,
) -> PersistedTerminalReplayPayload:
    selected_hits = gate_outcome.selected_hits if isinstance(gate_outcome, EvidenceGateSuccess) else ()
    return PersistedTerminalReplayPayload(
        search_receipt=_search_receipt_replay_projection(receipt),
        ordered_selected_hits=tuple(
            _selected_hit_replay_projection(hit) for hit in sorted(selected_hits, key=lambda hit: hit.fusion_rank)
        ),
        gate_status=gate_outcome.status.value,
        gate_reason=gate_outcome.reason.value,
        gate_message=getattr(gate_outcome, "message", ""),
    )


def _artifact_ref_from_projection(value: object) -> ImmutableArtifactRef:
    item = cast(dict[str, Any], value)
    return ImmutableArtifactRef(str(item["artifact_code"]), str(item["version"]), str(item["content_sha256"]))


def _restore_search_receipt(value: dict[str, Any]) -> ProductionSearchReceipt:
    expected_ref = _artifact_ref_from_projection(value["artifact_ref"])
    projection = cast(dict[str, Any], value["projection"])
    fingerprint = cast(dict[str, Any], projection["query_fingerprint"])
    receipt = compute_production_search_receipt(
        variant=str(projection["variant"]),
        status=RetrievalExecutionStatus(str(projection["status"])),
        diagnostic_code=str(projection["diagnostic_code"]),
        query_fingerprint=QueryFingerprint(
            str(fingerprint["algorithm"]), str(fingerprint["key_version"]), str(fingerprint["digest"])
        ),
        filter_snapshot_ref=_artifact_ref_from_projection(projection["filter_snapshot_ref"]),
        evidence_index_ref=_artifact_ref_from_projection(projection["evidence_index_ref"]),
        retrieval_config_ref=_artifact_ref_from_projection(projection["retrieval_config_ref"]),
        adapter_artifact_ref=_artifact_ref_from_projection(projection["adapter_artifact_ref"]),
        query_embedding_sha256=cast(str | None, projection["query_embedding_sha256"]),
        signal_manifest_sha256=str(projection["signal_manifest_sha256"]),
        hit_manifest_sha256=str(projection["hit_manifest_sha256"]),
        selection_manifest_sha256=str(projection["selection_manifest_sha256"]),
    )
    if receipt.artifact_ref != expected_ref:
        raise ValueError("Production search receipt artifact identity mismatch")
    return receipt


def _restore_selected_hit(value: dict[str, Any]) -> ProductionSearchHit:
    provenance = cast(dict[str, Any], value["provenance"])
    coordinate = cast(dict[str, Any], value["coordinate"])
    fraction = cast(dict[str, Any], value["fraction_receipt"])
    return ProductionSearchHit(
        provenance=ProductionEvidenceProvenance(
            knowledge_index_id=UUID(str(provenance["knowledge_index_id"])),
            index_code=str(provenance["index_code"]),
            index_version=str(provenance["index_version"]),
            index_configuration_hash=str(provenance["index_configuration_hash"]),
            knowledge_chunk_id=UUID(str(provenance["knowledge_chunk_id"])),
            source_snapshot_id=UUID(str(provenance["source_snapshot_id"])),
            source_snapshot_member_id=UUID(str(provenance["source_snapshot_member_id"])),
            source_code=str(provenance["source_code"]),
            source_version=str(provenance["source_version"]),
            canonical_checksum=str(provenance["canonical_checksum"]),
            external_document_id=str(provenance["external_document_id"]),
            chunk_index=int(provenance["chunk_index"]),
            locator=str(provenance["locator"]),
            content_hash=str(provenance["content_hash"]),
            canonicalization_spec_version=str(provenance["canonicalization_spec_version"]),
            normalization_version=str(provenance["normalization_version"]),
        ),
        coordinate=StableCoordinate(
            source_code=str(coordinate["source_code"]),
            source_version=str(coordinate["source_version"]),
            external_document_id=str(coordinate["external_document_id"]),
            chunk_index=int(coordinate["chunk_index"]),
        ),
        exact_hit=bool(value["exact_hit"]),
        observed_trigram_score=cast(str | None, value["observed_trigram_score"]),
        observed_fts_score=cast(str | None, value["observed_fts_score"]),
        observed_dense_score=cast(str | None, value["observed_dense_score"]),
        lexical_rank=cast(int | None, value["lexical_rank"]),
        dense_rank=cast(int | None, value["dense_rank"]),
        fusion_rank=int(value["fusion_rank"]),
        fraction_receipt=FractionReceipt(str(fraction["numerator"]), str(fraction["denominator"])),
        is_eligible_for_future_reranker=bool(value["is_eligible_for_future_reranker"]),
    )


def _restore_terminal_replay_payload(
    payload: PersistedTerminalReplayPayload,
) -> tuple[ProductionSearchReceipt, EvidenceGateOutcome]:
    receipt = _restore_search_receipt(payload.search_receipt)
    selected_hits = tuple(_restore_selected_hit(hit) for hit in payload.ordered_selected_hits)
    if tuple(hit.fusion_rank for hit in selected_hits) != tuple(sorted(hit.fusion_rank for hit in selected_hits)):
        raise ValueError("Terminal replay selected hits are not ordered")
    if compute_selection_manifest_hash(selected_hits) != receipt.selection_manifest_sha256:
        raise ValueError("Terminal replay selection manifest mismatch")
    status = EvidenceGateStatus(payload.gate_status)
    reason = EvidenceGateReason(payload.gate_reason)
    if status == EvidenceGateStatus.SUCCEEDED and reason == EvidenceGateReason.ELIGIBLE:
        return receipt, EvidenceGateSuccess(selected_hits=selected_hits)
    if status == EvidenceGateStatus.NO_RESULT and reason == EvidenceGateReason.INSUFFICIENT and not selected_hits:
        return receipt, EvidenceGateNoResult(message=payload.gate_message)
    raise ValueError("Unsupported terminal replay gate outcome")


def _terminal_replay_outcome(begin_result: BeginRetrievalRunSuccess) -> HybridRetrieveOutcome:
    payload = begin_result.existing_terminal_replay_payload
    if payload is None:
        message = "Terminal replay payload unavailable"
        return HybridRetrieveOutcome(
            status=RetrievalExecutionStatus.DEPENDENCY_ERROR,
            persisted_receipt=None,
            search_receipt=None,
            gate_outcome=EvidenceGateFailure(
                status=EvidenceGateStatus.DEPENDENCY_ERROR,
                reason=EvidenceGateReason.INVALID_BINDING,
                message=message,
            ),
            message=message,
        )
    try:
        search_receipt, gate_outcome = _restore_terminal_replay_payload(payload)
        persisted_receipt = begin_result.existing_receipt
        if persisted_receipt is None or (
            persisted_receipt.search_receipt_hash != search_receipt.artifact_ref.content_sha256
            or persisted_receipt.variant != search_receipt.variant
            or persisted_receipt.query_digest != search_receipt.query_fingerprint.digest
            or persisted_receipt.retrieval_configuration_hash != search_receipt.retrieval_config_ref.content_sha256
            or persisted_receipt.selected_count
            != len(gate_outcome.selected_hits if isinstance(gate_outcome, EvidenceGateSuccess) else ())
            or persisted_receipt.diagnostic_code != gate_outcome.reason.value
        ):
            raise ValueError("Terminal replay payload does not match persisted run receipt")
    except (KeyError, TypeError, ValueError):
        message = "Terminal replay payload invalid"
        return HybridRetrieveOutcome(
            status=RetrievalExecutionStatus.DEPENDENCY_ERROR,
            persisted_receipt=None,
            search_receipt=None,
            gate_outcome=EvidenceGateFailure(
                status=EvidenceGateStatus.DEPENDENCY_ERROR,
                reason=EvidenceGateReason.INVALID_BINDING,
                message=message,
            ),
            message=message,
        )
    return HybridRetrieveOutcome(
        status=RetrievalExecutionStatus.SUCCEEDED,
        persisted_receipt=begin_result.existing_receipt,
        search_receipt=search_receipt,
        gate_outcome=gate_outcome,
        message="Replayed verified existing terminal retrieval run",
    )


@dataclass(frozen=True, slots=True)
class ProductionRetrievalRequest:
    search_request: EvidenceSearchRequest


@dataclass(frozen=True, slots=True)
class ProductionRetrievalOutcome:
    status: RetrievalExecutionStatus
    receipt: ProductionSearchReceipt | None
    gate_outcome: EvidenceGateOutcome
    search_success: EvidenceSearchSuccess | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class HybridRetrieveRequest:
    job_id: UUID
    execution_context_id: UUID
    prescription_version_id: UUID
    runtime_release_bundle_id: UUID
    runtime_release_bundle_manifest_hash: str
    runtime_execution_manifest_id: UUID
    runtime_execution_manifest_hash: str
    runtime_guard_decision_ref: str
    search_request: EvidenceSearchRequest
    filter_snapshot: dict[str, Any] | None = None
    source_manifest_hash: str | None = None
    node_id: str = HYBRID_RETRIEVE_NODE_ID


@dataclass(frozen=True, slots=True)
class HybridRetrieveOutcome:
    status: RetrievalExecutionStatus
    persisted_receipt: PersistedRetrievalRunReceipt | None
    search_receipt: ProductionSearchReceipt | None
    gate_outcome: EvidenceGateOutcome
    message: str = ""


def _determine_variant(mode: RetrievalExecutionMode) -> str:
    if mode == RetrievalExecutionMode.LEXICAL_ONLY:
        return "RET-L"
    if mode == RetrievalExecutionMode.DENSE_ONLY:
        return "RET-D"
    return "RET-H"


async def execute_production_retrieval(
    request: ProductionRetrievalRequest,
    *,
    search_port: EvidenceSearchPort,
    text_embedding_port: TextEmbeddingPort | None,
    eligibility_verifier: ProductionEvidenceEligibilityVerifierPort,
) -> ProductionRetrievalOutcome:
    """Pure evaluation/production retrieval orchestration without DB run writes."""
    sr = request.search_request
    b = sr.execution_binding
    cfg = b.retrieval_config
    variant = _determine_variant(cfg.execution_mode)

    # 1. Pre-search eligibility check
    pre_req = PreSearchEligibilityRequest(knowledge_index_id=b.knowledge_index_id)
    pre_outcome = await eligibility_verifier.pre_search(pre_req)
    if not isinstance(pre_outcome, PreSearchEligibilitySuccess):
        gate_res = EvidenceGateNoResult(
            status=pre_outcome.status,
            reason=pre_outcome.reason,
            message=pre_outcome.message,
        )
        return ProductionRetrievalOutcome(
            status=(
                RetrievalExecutionStatus.VALIDATION_ERROR
                if pre_outcome.status == EvidenceGateStatus.VALIDATION_ERROR
                else RetrievalExecutionStatus.DEPENDENCY_ERROR
            ),
            receipt=None,
            gate_outcome=gate_res,
            message=pre_outcome.message,
        )

    # 2. Embedding if RET-D or RET-H
    query_embedding_sha: str | None = None
    effective_search_request = sr
    if variant in ("RET-D", "RET-H"):
        if sr.query_embedding_receipt is not None:
            query_embedding_sha = canonical_embedding_sha256(sr.query_embedding_receipt.embedding.reveal())
        else:
            if text_embedding_port is None:
                return ProductionRetrievalOutcome(
                    status=RetrievalExecutionStatus.VALIDATION_ERROR,
                    receipt=None,
                    gate_outcome=EvidenceGateFailure(
                        status=EvidenceGateStatus.VALIDATION_ERROR,
                        reason=EvidenceGateReason.INVALID_BINDING,
                        message="Text embedding port required for dense/hybrid retrieval",
                    ),
                    message="Embedding port missing",
                )

            embed_res = await text_embedding_port.embed(
                sr.normalized_query,
                model_ref=PRODUCTION_EMBEDDING_MODEL_REF,
                model_version=PRODUCTION_EMBEDDING_MODEL_VERSION,
                dimension=PRODUCTION_EMBEDDING_DIMENSION,
            )
            if not isinstance(embed_res, TextEmbeddingSuccess):
                return ProductionRetrievalOutcome(
                    status=RetrievalExecutionStatus.DEPENDENCY_ERROR,
                    receipt=None,
                    gate_outcome=EvidenceGateFailure(
                        status=EvidenceGateStatus.DEPENDENCY_ERROR,
                        reason=EvidenceGateReason.INELIGIBLE,
                        message=f"Embedding failure: {embed_res.reason}",
                    ),
                    message=f"Embedding failed: {embed_res.reason}",
                )

            query_embedding_sha = canonical_embedding_sha256(embed_res.embedding.reveal())
            q_receipt = QueryEmbeddingReceipt(
                query_fingerprint=sr.query_fingerprint,
                model_ref=PRODUCTION_EMBEDDING_MODEL_REF,
                model_version=PRODUCTION_EMBEDDING_MODEL_VERSION,
                dimension=PRODUCTION_EMBEDDING_DIMENSION,
                embedding=embed_res.embedding,
                adapter_artifact_ref=embed_res.adapter_artifact_ref,
            )
            effective_search_request = EvidenceSearchRequest(
                normalized_query=sr.normalized_query,
                query_fingerprint=sr.query_fingerprint,
                execution_binding=b,
                query_embedding_receipt=q_receipt,
            )

    # 3. Production search
    search_outcome = await search_port.search(effective_search_request)
    if not isinstance(search_outcome, EvidenceSearchSuccess):
        return ProductionRetrievalOutcome(
            status=RetrievalExecutionStatus.DEPENDENCY_ERROR,
            receipt=None,
            gate_outcome=EvidenceGateFailure(
                status=EvidenceGateStatus.DEPENDENCY_ERROR,
                reason=EvidenceGateReason.INELIGIBLE,
                message=f"Search failed: {search_outcome.reason}",
            ),
            message=f"Search failed: {search_outcome.reason}",
        )

    # 4. Extract candidate hits
    candidates: Sequence[ProductionSearchHit]
    if variant == "RET-H":
        candidates = search_outcome.hybrid_hits
    elif variant == "RET-D":
        candidates = search_outcome.dense_hits
    else:
        candidates = search_outcome.lexical_hits

    # Pre-gate Top 5 candidates
    pre_gate_top_5 = candidates[:5]

    # 5. Post-search eligibility check
    post_req = PostSearchEligibilityRequest(knowledge_index_id=b.knowledge_index_id)
    post_outcome = await eligibility_verifier.post_search(post_req, pre_gate_top_5)
    if not isinstance(post_outcome, PostSearchEligibilitySuccess):
        return ProductionRetrievalOutcome(
            status=RetrievalExecutionStatus.DEPENDENCY_ERROR,
            receipt=None,
            gate_outcome=EvidenceGateFailure(
                status=post_outcome.status,
                reason=post_outcome.reason,
                message=post_outcome.message,
            ),
            message=post_outcome.message,
        )

    # 6. Evidence Gate evaluation
    gate_outcome = evaluate_evidence_gate(pre_gate_top_5, post_outcome.eligible_chunk_ids)

    # 7. Compute manifests & receipt
    signals_input = [
        PersistedSignalInput(
            knowledge_chunk_id=s.provenance.knowledge_chunk_id,
            method=s.method.value,
            raw_rank=s.raw_rank,
            raw_score=Decimal(s.observed_score),
            score_projection_version="observed-stage-score-decimal@1",
        )
        for s in search_outcome.signals
    ]
    signal_hash = compute_signal_manifest_hash(signals_input)

    hits_input = [
        PersistedHitInput(
            knowledge_chunk_id=h.provenance.knowledge_chunk_id,
            rrf_rank=h.fusion_rank,
            rrf_score=Decimal(f"{int(h.fraction_receipt.numerator) / int(h.fraction_receipt.denominator):.18f}"),
            rrf_score_numerator=h.fraction_receipt.numerator,
            rrf_score_denominator=h.fraction_receipt.denominator,
            final_rank=h.fusion_rank,
            selected=(isinstance(gate_outcome, EvidenceGateSuccess) and h in gate_outcome.selected_hits),
            lexical_rank=h.lexical_rank,
            dense_rank=h.dense_rank,
        )
        for h in candidates
    ]
    hit_hash = compute_hit_manifest_hash(hits_input)

    selected_hits = gate_outcome.selected_hits if isinstance(gate_outcome, EvidenceGateSuccess) else ()
    selection_hash = compute_selection_manifest_hash(selected_hits)

    search_receipt = compute_production_search_receipt(
        variant=variant,
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code=gate_outcome.reason.value,
        query_fingerprint=sr.query_fingerprint,
        filter_snapshot_ref=b.filter_snapshot_ref,
        evidence_index_ref=b.evidence_index_ref,
        retrieval_config_ref=ImmutableArtifactRef("retrieval_config", "1.0", cfg.compute_canonical_hash()),
        adapter_artifact_ref=search_outcome.adapter_artifact_ref,
        query_embedding_sha256=query_embedding_sha,
        signal_manifest_sha256=signal_hash,
        hit_manifest_sha256=hit_hash,
        selection_manifest_sha256=selection_hash,
    )

    return ProductionRetrievalOutcome(
        status=RetrievalExecutionStatus.SUCCEEDED,
        receipt=search_receipt,
        gate_outcome=gate_outcome,
        search_success=search_outcome,
    )


async def execute_hybrid_retrieve(
    request: HybridRetrieveRequest,
    *,
    search_port: EvidenceSearchPort,
    text_embedding_port: TextEmbeddingPort | None,
    run_store: RetrievalRunStorePort,
    eligibility_verifier: ProductionEvidenceEligibilityVerifierPort,
) -> HybridRetrieveOutcome:
    """Production runtime callable with idempotent begin/finalize persistence."""
    sr = request.search_request
    b = sr.execution_binding
    cfg = b.retrieval_config
    variant = _determine_variant(cfg.execution_mode)

    # 1. Query embedding preparation for RET-D / RET-H before begin_run
    query_embedding_sha: str | None = None
    effective_sr = sr
    if variant in ("RET-D", "RET-H"):
        if sr.query_embedding_receipt is not None:
            query_embedding_sha = canonical_embedding_sha256(sr.query_embedding_receipt.embedding.reveal())
        elif text_embedding_port is not None:
            embed_res = await text_embedding_port.embed(
                sr.normalized_query,
                model_ref=PRODUCTION_EMBEDDING_MODEL_REF,
                model_version=PRODUCTION_EMBEDDING_MODEL_VERSION,
                dimension=PRODUCTION_EMBEDDING_DIMENSION,
            )
            if not isinstance(embed_res, TextEmbeddingSuccess):
                return HybridRetrieveOutcome(
                    status=RetrievalExecutionStatus.DEPENDENCY_ERROR,
                    persisted_receipt=None,
                    search_receipt=None,
                    gate_outcome=EvidenceGateFailure(
                        status=EvidenceGateStatus.DEPENDENCY_ERROR,
                        reason=EvidenceGateReason.INELIGIBLE,
                        message=f"Embedding failure: {embed_res.reason}",
                    ),
                    message=f"Embedding failed: {embed_res.reason}",
                )
            query_embedding_sha = canonical_embedding_sha256(embed_res.embedding.reveal())
            q_receipt = QueryEmbeddingReceipt(
                query_fingerprint=sr.query_fingerprint,
                model_ref=PRODUCTION_EMBEDDING_MODEL_REF,
                model_version=PRODUCTION_EMBEDDING_MODEL_VERSION,
                dimension=PRODUCTION_EMBEDDING_DIMENSION,
                embedding=embed_res.embedding,
                adapter_artifact_ref=embed_res.adapter_artifact_ref,
            )
            effective_sr = EvidenceSearchRequest(
                normalized_query=sr.normalized_query,
                query_fingerprint=sr.query_fingerprint,
                execution_binding=b,
                query_embedding_receipt=q_receipt,
            )
        else:
            return HybridRetrieveOutcome(
                status=RetrievalExecutionStatus.VALIDATION_ERROR,
                persisted_receipt=None,
                search_receipt=None,
                gate_outcome=EvidenceGateFailure(
                    status=EvidenceGateStatus.VALIDATION_ERROR,
                    reason=EvidenceGateReason.INVALID_BINDING,
                    message="Text embedding port required for dense/hybrid retrieval",
                ),
                message="Embedding port missing",
            )

    filter_snapshot_payload = (
        request.filter_snapshot
        if request.filter_snapshot is not None
        else {
            "allowed_source_snapshot_ids": [str(s) for s in b.allowed_source_snapshot_ids],
            "allowed_source_snapshot_member_ids": [str(m) for m in b.allowed_source_snapshot_member_ids],
        }
    )
    source_manifest_hash_val = (
        request.source_manifest_hash
        if request.source_manifest_hash is not None
        else b.evidence_index_ref.content_sha256
    )

    # 2. begin_run
    begin_req = BeginRetrievalRunRequest(
        job_id=request.job_id,
        execution_context_id=request.execution_context_id,
        prescription_version_id=request.prescription_version_id,
        runtime_release_bundle_id=request.runtime_release_bundle_id,
        runtime_release_bundle_manifest_hash=request.runtime_release_bundle_manifest_hash,
        runtime_execution_manifest_id=request.runtime_execution_manifest_id,
        runtime_execution_manifest_hash=request.runtime_execution_manifest_hash,
        runtime_guard_decision_ref=request.runtime_guard_decision_ref,
        knowledge_index_id=b.knowledge_index_id,
        variant=variant,
        query_digest_algorithm=sr.query_fingerprint.algorithm,
        query_digest_key_version=sr.query_fingerprint.key_version,
        query_digest=sr.query_fingerprint.digest,
        filter_snapshot=filter_snapshot_payload,
        filter_snapshot_hash=b.filter_snapshot_ref.content_sha256,
        source_manifest_hash=source_manifest_hash_val,
        retrieval_configuration_hash=cfg.compute_canonical_hash(),
        lexical_limit=cfg.lexical_limit,
        dense_limit=cfg.dense_limit,
        hybrid_limit=cfg.hybrid_limit,
        final_k=5,
        node_id=request.node_id,
        query_embedding_sha256=query_embedding_sha,
    )
    begin_res = await run_store.begin_run(begin_req)
    if not isinstance(begin_res, BeginRetrievalRunSuccess):
        return HybridRetrieveOutcome(
            status=(
                RetrievalExecutionStatus.DEPENDENCY_ERROR
                if begin_res.reason == BeginRetrievalRunFailureReason.DEPENDENCY_ERROR
                else RetrievalExecutionStatus.VALIDATION_ERROR
            ),
            persisted_receipt=None,
            search_receipt=None,
            gate_outcome=EvidenceGateFailure(
                status=EvidenceGateStatus.VALIDATION_ERROR,
                reason=EvidenceGateReason.INVALID_BINDING,
                message=begin_res.message or "Begin retrieval run failed",
            ),
            message=begin_res.message or "Begin retrieval run failed",
        )

    # Fast-path for verified terminal replay
    if begin_res.is_resumed and begin_res.existing_receipt is not None:
        return _terminal_replay_outcome(begin_res)

    run_id = begin_res.run_id

    # 3. Execute production retrieval
    prod_req = ProductionRetrievalRequest(search_request=effective_sr)
    prod_outcome = await execute_production_retrieval(
        prod_req,
        search_port=search_port,
        text_embedding_port=text_embedding_port,
        eligibility_verifier=eligibility_verifier,
    )

    if prod_outcome.status != RetrievalExecutionStatus.SUCCEEDED or prod_outcome.search_success is None:
        fin_fail = FinalizeRetrievalRunRequest(
            run_id=run_id,
            status="FAILED",
            error_code=prod_outcome.gate_outcome.reason.value,
        )
        await run_store.finalize_run(fin_fail)
        return HybridRetrieveOutcome(
            status=prod_outcome.status,
            persisted_receipt=None,
            search_receipt=None,
            gate_outcome=prod_outcome.gate_outcome,
            message=prod_outcome.message,
        )

    # 4. Finalize run as COMPLETED
    search_success = prod_outcome.search_success
    gate_outcome = prod_outcome.gate_outcome
    candidates = (
        search_success.hybrid_hits
        if variant == "RET-H"
        else search_success.dense_hits
        if variant == "RET-D"
        else search_success.lexical_hits
    )

    signals_input = tuple(
        PersistedSignalInput(
            knowledge_chunk_id=s.provenance.knowledge_chunk_id,
            method=s.method.value,
            raw_rank=s.raw_rank,
            raw_score=Decimal(s.observed_score),
            score_projection_version="observed-stage-score-decimal@1",
        )
        for s in search_success.signals
    )

    hits_input = tuple(
        PersistedHitInput(
            knowledge_chunk_id=h.provenance.knowledge_chunk_id,
            rrf_rank=h.fusion_rank,
            rrf_score=Decimal(f"{int(h.fraction_receipt.numerator) / int(h.fraction_receipt.denominator):.18f}"),
            rrf_score_numerator=h.fraction_receipt.numerator,
            rrf_score_denominator=h.fraction_receipt.denominator,
            final_rank=h.fusion_rank,
            selected=(isinstance(gate_outcome, EvidenceGateSuccess) and h in gate_outcome.selected_hits),
            lexical_rank=h.lexical_rank,
            dense_rank=h.dense_rank,
        )
        for h in candidates
    )

    fin_req = FinalizeRetrievalRunRequest(
        run_id=run_id,
        status="COMPLETED",
        search_receipt_hash=prod_outcome.receipt.artifact_ref.content_sha256 if prod_outcome.receipt else None,
        diagnostic_code=gate_outcome.reason.value,
        signals=signals_input,
        hits=hits_input,
        terminal_replay_payload=(
            _make_terminal_replay_payload(prod_outcome.receipt, gate_outcome)
            if prod_outcome.receipt is not None
            else None
        ),
    )
    fin_outcome = await run_store.finalize_run(fin_req)
    if not isinstance(fin_outcome, FinalizeRetrievalRunSuccess):
        return HybridRetrieveOutcome(
            status=RetrievalExecutionStatus.DEPENDENCY_ERROR,
            persisted_receipt=None,
            search_receipt=prod_outcome.receipt,
            gate_outcome=gate_outcome,
            message=fin_outcome.message or "Finalize retrieval run failed",
        )

    return HybridRetrieveOutcome(
        status=RetrievalExecutionStatus.SUCCEEDED,
        persisted_receipt=fin_outcome.receipt,
        search_receipt=prod_outcome.receipt,
        gate_outcome=gate_outcome,
    )
