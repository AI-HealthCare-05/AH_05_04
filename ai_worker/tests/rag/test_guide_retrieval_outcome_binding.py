"""Tests for the narrow #180 Hybrid retrieval outcome projection."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from ai_worker.tasks.rag.evidence_rank_fusion import FractionReceipt, StableCoordinate
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, QueryFingerprint
from ai_worker.tasks.rag.evidence_search import ProductionEvidenceProvenance, ProductionSearchHit
from ai_worker.tasks.rag.guide_evidence_authority import (
    SyncGuideEvidenceAuthorityDecision,
    SyncGuideEvidenceAuthorityOutcome,
)
from ai_worker.tasks.rag.guide_evidence_handoff import (
    ObservedDecisionOutcome,
    RequestDecisionStage,
    RequestSourceMemberBinding,
)
from ai_worker.tasks.rag.guide_retrieval_composition import (
    GuideRetrievalCompositionDecision,
    compose_guide_authority_with_production_retrieval,
)
from ai_worker.tasks.rag.guide_retrieval_outcome_binding import (
    GuideRetrievalOutcomeBindingDecision,
    GuideRetrievalOutcomeBindingReason,
    project_hybrid_retrieval_for_guide_composition,
)
from ai_worker.tasks.rag.production_evidence_gate import (
    EvidenceGateNoResult,
    EvidenceGateReason,
    EvidenceGateSuccess,
)
from ai_worker.tasks.rag.retrieval_run import PersistedRetrievalRunReceipt
from ai_worker.tasks.rag.retrieval_runtime import (
    HybridRetrieveOutcome,
    ProductionSearchReceipt,
    RetrievalExecutionStatus,
)
from ai_worker.tasks.rag.source_member_identity import SourceMemberKind


def _artifact(code: str, digest: str = "a" * 64) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(artifact_code=code, version="v1", content_sha256=digest)


def _receipt() -> ProductionSearchReceipt:
    return ProductionSearchReceipt(
        artifact_ref=_artifact("production_search_receipt"),
        variant="RET-H",
        retrieval_execution_status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code=EvidenceGateReason.ELIGIBLE.value,
        query_fingerprint=QueryFingerprint("sha256", "v1", "0" * 64),
        filter_snapshot_ref=_artifact("filter_snapshot"),
        evidence_index_ref=_artifact("evidence_index"),
        retrieval_config_ref=_artifact("retrieval_config"),
        adapter_artifact_ref=_artifact("adapter"),
        query_embedding_sha256=None,
        signal_manifest_sha256="1" * 64,
        hit_manifest_sha256="2" * 64,
        selection_manifest_sha256="3" * 64,
    )


def _persisted_receipt() -> PersistedRetrievalRunReceipt:
    return PersistedRetrievalRunReceipt(
        run_id=uuid4(),
        job_id=uuid4(),
        node_id="hybrid_retrieve",
        variant="RET-H",
        status="COMPLETED",
        query_digest="0" * 64,
        retrieval_configuration_hash="a" * 64,
        source_manifest_hash="b" * 64,
        receipt_hash="c" * 64,
        total_signals=1,
        total_hits=1,
        selected_count=1,
        signal_manifest_hash="d" * 64,
        hit_manifest_hash="e" * 64,
    )


def _hit() -> ProductionSearchHit:
    source_snapshot_id = uuid4()
    source_snapshot_member_id = uuid4()
    coordinate = StableCoordinate("MFDS_LABEL", "2026.1", "DOC-1", 0)
    provenance = ProductionEvidenceProvenance(
        knowledge_index_id=uuid4(),
        index_code="GUIDELINE_INDEX",
        index_version="v1",
        index_configuration_hash="4" * 64,
        knowledge_chunk_id=uuid4(),
        source_snapshot_id=source_snapshot_id,
        source_snapshot_member_id=source_snapshot_member_id,
        source_code=coordinate.source_code,
        source_version=coordinate.source_version,
        canonical_checksum="5" * 64,
        external_document_id=coordinate.external_document_id,
        chunk_index=coordinate.chunk_index,
        locator="doc:DOC-1#p1",
        content_hash="6" * 64,
        canonicalization_spec_version="v1",
        normalization_version="v1",
    )
    return ProductionSearchHit(
        provenance=provenance,
        coordinate=coordinate,
        exact_hit=False,
        observed_trigram_score=None,
        observed_fts_score=None,
        observed_dense_score=None,
        lexical_rank=None,
        dense_rank=None,
        fusion_rank=1,
        fraction_receipt=FractionReceipt("1", "60"),
        is_eligible_for_future_reranker=True,
    )


def _outcome(*, hit: ProductionSearchHit | None = None) -> HybridRetrieveOutcome:
    selected_hit = hit or _hit()
    return HybridRetrieveOutcome(
        status=RetrievalExecutionStatus.SUCCEEDED,
        persisted_receipt=_persisted_receipt(),
        search_receipt=_receipt(),
        gate_outcome=EvidenceGateSuccess(selected_hits=(selected_hit,)),
        message="raw upstream diagnostic must not cross the projection",
    )


def _binding_for(hit: ProductionSearchHit) -> RequestSourceMemberBinding:
    return RequestSourceMemberBinding(
        request_guard_ref=_artifact("request_guard"),
        request_operation_code="GUIDE_GENERATE",
        source_snapshot_id=hit.provenance.source_snapshot_id,
        source_snapshot_member_id=hit.provenance.source_snapshot_member_id,
        source_code=hit.provenance.source_code,
        source_version=hit.provenance.source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=_artifact("request_source_decision"),
        request_member_decision_ref=_artifact("request_member_decision"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_decision_stage=RequestDecisionStage.REQUEST,
        endpoint_code="MFDS_LABEL_ENDPOINT",
        operation_code=None,
    )


def test_complete_first_run_projects_only_697_semantic_surface() -> None:
    hybrid = _outcome()

    outcome = project_hybrid_retrieval_for_guide_composition(hybrid)

    assert outcome.decision is GuideRetrievalOutcomeBindingDecision.READY
    assert outcome.reason is None
    assert outcome.retrieval_outcome is not None
    assert outcome.retrieval_outcome.status is hybrid.status
    assert outcome.retrieval_outcome.receipt is hybrid.search_receipt
    assert outcome.retrieval_outcome.gate_outcome is hybrid.gate_outcome
    assert outcome.retrieval_outcome.search_success is None
    assert outcome.retrieval_outcome.message == ""
    assert hybrid.message == "raw upstream diagnostic must not cross the projection"


def test_missing_search_receipt_blocks_terminal_replay_without_recovery() -> None:
    hybrid = replace(_outcome(), search_receipt=None)

    outcome = project_hybrid_retrieval_for_guide_composition(hybrid)

    assert outcome.decision is GuideRetrievalOutcomeBindingDecision.BLOCKED
    assert outcome.reason is GuideRetrievalOutcomeBindingReason.SEARCH_RECEIPT_REQUIRED
    assert outcome.retrieval_outcome is None


def test_non_succeeded_or_malformed_gate_input_blocks_without_partial_projection() -> None:
    failed = replace(_outcome(), status=RetrievalExecutionStatus.DEPENDENCY_ERROR)
    malformed_gate = replace(
        _outcome(),
        gate_outcome=EvidenceGateNoResult(message="not a production success"),
    )

    failed_outcome = project_hybrid_retrieval_for_guide_composition(failed)
    malformed_gate_outcome = project_hybrid_retrieval_for_guide_composition(malformed_gate)
    foreign_outcome = project_hybrid_retrieval_for_guide_composition(object())

    assert failed_outcome.reason is GuideRetrievalOutcomeBindingReason.RETRIEVAL_NOT_SUCCEEDED
    assert failed_outcome.retrieval_outcome is None
    assert malformed_gate_outcome.reason is GuideRetrievalOutcomeBindingReason.EVIDENCE_GATE_INVALID
    assert malformed_gate_outcome.retrieval_outcome is None
    assert foreign_outcome.reason is GuideRetrievalOutcomeBindingReason.INVALID_INPUT
    assert foreign_outcome.retrieval_outcome is None


def test_non_tuple_gate_selection_is_malformed_and_blocks() -> None:
    malformed_gate = EvidenceGateSuccess(selected_hits=[_hit()])  # type: ignore[arg-type]

    outcome = project_hybrid_retrieval_for_guide_composition(
        replace(_outcome(), gate_outcome=malformed_gate),
    )

    assert outcome.decision is GuideRetrievalOutcomeBindingDecision.BLOCKED
    assert outcome.reason is GuideRetrievalOutcomeBindingReason.EVIDENCE_GATE_INVALID
    assert outcome.retrieval_outcome is None


def test_empty_success_gate_blocks_before_it_can_reach_697_composition() -> None:
    empty_success = EvidenceGateSuccess(selected_hits=())

    outcome = project_hybrid_retrieval_for_guide_composition(
        replace(_outcome(), gate_outcome=empty_success),
    )

    assert outcome.decision is GuideRetrievalOutcomeBindingDecision.BLOCKED
    assert outcome.reason is GuideRetrievalOutcomeBindingReason.EVIDENCE_GATE_INVALID
    assert outcome.retrieval_outcome is None


def test_ready_projection_remains_compatible_with_unchanged_697_composition() -> None:
    hit = _hit()
    projected = project_hybrid_retrieval_for_guide_composition(_outcome(hit=hit))
    assert projected.retrieval_outcome is not None
    authority = SyncGuideEvidenceAuthorityOutcome(
        decision=SyncGuideEvidenceAuthorityDecision.AUTHENTICATED,
        reasons=(),
        bindings=(_binding_for(hit),),
    )

    composition = compose_guide_authority_with_production_retrieval(
        authority_outcome=authority,
        retrieval_outcome=projected.retrieval_outcome,
    )

    assert composition.decision is GuideRetrievalCompositionDecision.AUTHENTICATED
    assert composition.retrieval_receipt is projected.retrieval_outcome.receipt
