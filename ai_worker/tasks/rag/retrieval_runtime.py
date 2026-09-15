from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, QueryFingerprint
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchPort,
    EvidenceSearchRequest,
    EvidenceSearchSuccess,
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
    RetrievalRunStorePort,
    compute_hit_manifest_hash,
    compute_signal_manifest_hash,
    sha256_canonical_json,
)
from ai_worker.tasks.rag.text_embedding import TextEmbeddingPort, TextEmbeddingSuccess

logger = logging.getLogger(__name__)

HYBRID_RETRIEVE_NODE_ID = "hybrid_retrieve"
EVIDENCE_GATE_NODE_ID = "evidence_gate"


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


def compute_selection_manifest_hash(selected_hits: Sequence[ProductionSearchHit]) -> str:
    sorted_hits = sorted(selected_hits, key=lambda h: h.fusion_rank)
    payload = [
        {
            "chunk_index": h.coordinate.chunk_index,
            "external_document_id": h.coordinate.external_document_id,
            "final_rank": h.fusion_rank,
            "knowledge_chunk_id": str(h.provenance.knowledge_chunk_id),
            "source_code": h.coordinate.source_code,
            "source_version": h.coordinate.source_version,
        }
        for h in sorted_hits
    ]
    return sha256_canonical_json(payload)


def compute_production_search_receipt(
    *,
    variant: str,
    status: RetrievalExecutionStatus,
    diagnostic_code: str,
    query_fingerprint: QueryFingerprint,
    filter_snapshot_hash: str,
    evidence_index_config_hash: str,
    retrieval_config_hash: str,
    adapter_artifact_ref: ImmutableArtifactRef,
    query_embedding_sha256: str | None,
    signal_manifest_sha256: str,
    hit_manifest_sha256: str,
    selection_manifest_sha256: str,
) -> ProductionSearchReceipt:
    envelope = {
        "diagnostic_code": diagnostic_code,
        "evidence_index_ref": evidence_index_config_hash,
        "filter_snapshot_ref": filter_snapshot_hash,
        "hit_manifest_sha256": hit_manifest_sha256,
        "query_digest": query_fingerprint.digest,
        "query_embedding_sha256": query_embedding_sha256,
        "retrieval_config_ref": retrieval_config_hash,
        "selection_manifest_sha256": selection_manifest_sha256,
        "signal_manifest_sha256": signal_manifest_sha256,
        "status": status.value,
        "variant": variant,
    }
    artifact_hash = sha256_canonical_json(envelope)
    artifact_ref = ImmutableArtifactRef(
        artifact_code="production_search_receipt",
        version="1.0",
        content_sha256=artifact_hash,
    )
    return ProductionSearchReceipt(
        artifact_ref=artifact_ref,
        variant=variant,
        retrieval_execution_status=status,
        diagnostic_code=diagnostic_code,
        query_fingerprint=query_fingerprint,
        filter_snapshot_ref=ImmutableArtifactRef("filter_snapshot", "1.0", filter_snapshot_hash),
        evidence_index_ref=ImmutableArtifactRef("knowledge_index", "1.0", evidence_index_config_hash),
        retrieval_config_ref=ImmutableArtifactRef("retrieval_config", "1.0", retrieval_config_hash),
        adapter_artifact_ref=adapter_artifact_ref,
        query_embedding_sha256=query_embedding_sha256,
        signal_manifest_sha256=signal_manifest_sha256,
        hit_manifest_sha256=hit_manifest_sha256,
        selection_manifest_sha256=selection_manifest_sha256,
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
                model_ref="text-embedding-3-large",
                model_version="1.0",
                dimension=1536,
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
                model_ref="text-embedding-3-large",
                model_version="1.0",
                dimension=1536,
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
        filter_snapshot_hash=b.filter_snapshot_ref.content_sha256,
        evidence_index_config_hash=b.evidence_index_ref.content_sha256,
        retrieval_config_hash=cfg.compute_canonical_hash(),
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
                model_ref="text-embedding-3-large",
                model_version="1.0",
                dimension=1536,
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
                model_ref="text-embedding-3-large",
                model_version="1.0",
                dimension=1536,
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
        return HybridRetrieveOutcome(
            status=RetrievalExecutionStatus.SUCCEEDED,
            persisted_receipt=begin_res.existing_receipt,
            search_receipt=None,
            gate_outcome=EvidenceGateSuccess(selected_hits=()),
            message="Replayed verified existing terminal retrieval run",
        )

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
