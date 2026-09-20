from __future__ import annotations

from uuid import UUID, uuid4

from ai_worker.tasks.rag.evidence_rank_fusion import FractionReceipt, StableCoordinate
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, QueryFingerprint
from ai_worker.tasks.rag.evidence_search import ProductionEvidenceProvenance, ProductionSearchHit
from ai_worker.tasks.rag.production_evidence_gate import (
    EvidenceGateNoResult,
    EvidenceGateSuccess,
)
from ai_worker.tasks.rag.retrieval_run import (
    BeginRetrievalRunRequest,
    PersistedHitInput,
    PersistedSignalInput,
    PersistedTerminalReplayPayload,
    compute_hit_manifest_hash,
    compute_signal_manifest_hash,
)
from ai_worker.tasks.rag.retrieval_runtime import (
    RetrievalExecutionStatus,
    _make_terminal_replay_payload,
    compute_production_search_receipt,
    compute_selection_manifest_hash,
)


def make_terminal_replay_payload(
    request: BeginRetrievalRunRequest,
    *,
    index_id: UUID,
    signals: tuple[PersistedSignalInput, ...],
    hits: tuple[PersistedHitInput, ...],
) -> tuple[PersistedTerminalReplayPayload, str, str]:
    selected_hits = tuple(
        ProductionSearchHit(
            provenance=ProductionEvidenceProvenance(
                knowledge_index_id=index_id,
                index_code="TEST_IDX",
                index_version="1.0",
                index_configuration_hash="a" * 64,
                knowledge_chunk_id=hit.knowledge_chunk_id,
                source_snapshot_id=uuid4(),
                source_snapshot_member_id=uuid4(),
                source_code="SRC",
                source_version="1.0",
                canonical_checksum="b" * 64,
                external_document_id=f"doc-{hit.final_rank}",
                chunk_index=hit.final_rank,
                locator=f"loc-{hit.final_rank}",
                content_hash="c" * 64,
                canonicalization_spec_version="v1",
                normalization_version="v1",
            ),
            coordinate=StableCoordinate("SRC", "1.0", f"doc-{hit.final_rank}", hit.final_rank),
            exact_hit=True,
            observed_trigram_score=None,
            observed_fts_score=None,
            observed_dense_score=None,
            lexical_rank=hit.lexical_rank,
            dense_rank=hit.dense_rank,
            fusion_rank=hit.final_rank,
            fraction_receipt=FractionReceipt(hit.rrf_score_numerator, hit.rrf_score_denominator),
            is_eligible_for_future_reranker=True,
        )
        for hit in sorted(hits, key=lambda item: item.final_rank)
        if hit.selected
    )
    gate_outcome = (
        EvidenceGateSuccess(selected_hits=selected_hits)
        if selected_hits
        else EvidenceGateNoResult(message="Synthetic no-result fixture")
    )
    receipt = compute_production_search_receipt(
        variant=request.variant,
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code=gate_outcome.reason.value,
        query_fingerprint=QueryFingerprint(
            request.query_digest_algorithm,
            request.query_digest_key_version,
            request.query_digest,
        ),
        filter_snapshot_ref=ImmutableArtifactRef("filter_snapshot", "1.0", request.filter_snapshot_hash),
        evidence_index_ref=ImmutableArtifactRef("evidence_index", "1.0", request.source_manifest_hash),
        retrieval_config_ref=ImmutableArtifactRef("retrieval_config", "1.0", request.retrieval_configuration_hash),
        adapter_artifact_ref=ImmutableArtifactRef("adapter", "1.0", "d" * 64),
        query_embedding_sha256=request.query_embedding_sha256,
        signal_manifest_sha256=compute_signal_manifest_hash(signals),
        hit_manifest_sha256=compute_hit_manifest_hash(hits),
        selection_manifest_sha256=compute_selection_manifest_hash(selected_hits),
    )
    return (
        _make_terminal_replay_payload(receipt, gate_outcome),
        receipt.artifact_ref.content_sha256,
        gate_outcome.reason.value,
    )
