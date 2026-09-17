"""Unit tests for Guide Authority x Production Retrieval Exact Join (#697)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, QueryFingerprint
from ai_worker.tasks.rag.evidence_search import (
    FractionReceipt,
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    StableCoordinate,
)
from ai_worker.tasks.rag.guide_evidence_authority import (
    SyncGuideEvidenceAuthorityDecision,
    SyncGuideEvidenceAuthorityOutcome,
    SyncGuideEvidenceAuthorityReason,
)
from ai_worker.tasks.rag.guide_evidence_handoff import (
    ObservedDecisionOutcome,
    RequestDecisionStage,
    RequestSourceMemberBinding,
)
from ai_worker.tasks.rag.guide_retrieval_composition import (
    GuideRetrievalCompositionDecision,
    GuideRetrievalCompositionReason,
    compose_guide_authority_with_production_retrieval,
)
from ai_worker.tasks.rag.production_evidence_gate import (
    EvidenceGateFailure,
    EvidenceGateNoResult,
    EvidenceGateOutcome,
    EvidenceGateReason,
    EvidenceGateStatus,
    EvidenceGateSuccess,
)
from ai_worker.tasks.rag.retrieval_runtime import (
    ProductionRetrievalOutcome,
    ProductionSearchReceipt,
    RetrievalExecutionStatus,
)
from ai_worker.tasks.rag.source_member_identity import SourceMemberKind


def _make_artifact(code: str, version: str = "v1", digest: str | None = None) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(artifact_code=code, version=version, content_sha256=digest or ("a" * 64))


def _make_hit(
    *,
    rank: int,
    snapshot_id: UUID,
    member_id: UUID,
    source_code: str = "MFDS_LABEL",
    source_version: str = "2026.1",
    external_doc_id: str = "DOC-1",
    chunk_index: int = 0,
) -> ProductionSearchHit:
    provenance = ProductionEvidenceProvenance(
        knowledge_index_id=uuid4(),
        index_code="GUIDELINE_INDEX",
        index_version="v1",
        index_configuration_hash="4" * 64,
        knowledge_chunk_id=uuid4(),
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=member_id,
        source_code=source_code,
        source_version=source_version,
        canonical_checksum="c" * 64,
        external_document_id=external_doc_id,
        chunk_index=chunk_index,
        locator=f"doc:{external_doc_id}#p1",
        content_hash=hashlib.sha256(f"{external_doc_id}:{chunk_index}".encode()).hexdigest(),
        canonicalization_spec_version="v1",
        normalization_version="v1",
    )
    coordinate = StableCoordinate(
        source_code=source_code,
        source_version=source_version,
        external_document_id=external_doc_id,
        chunk_index=chunk_index,
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
        fusion_rank=rank,
        fraction_receipt=FractionReceipt("1", "60"),
        is_eligible_for_future_reranker=True,
    )


def _make_binding(
    *,
    snapshot_id: UUID,
    member_id: UUID,
    source_code: str = "MFDS_LABEL",
    source_version: str = "2026.1",
) -> RequestSourceMemberBinding:
    return RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("request_guard"),
        request_operation_code="GUIDE_GENERATE",
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=member_id,
        source_code=source_code,
        source_version=source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=_make_artifact("request_source_decision"),
        request_member_decision_ref=_make_artifact("request_member_decision"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_decision_stage=RequestDecisionStage.REQUEST,
        endpoint_code="MFDS_LABEL_ENDPOINT",
        operation_code=None,
    )


def _make_receipt(
    status: RetrievalExecutionStatus = RetrievalExecutionStatus.SUCCEEDED,
) -> ProductionSearchReceipt:
    return ProductionSearchReceipt(
        artifact_ref=_make_artifact("production_search_receipt", "2.0"),
        variant="RET-H",
        retrieval_execution_status=status,
        diagnostic_code=EvidenceGateReason.ELIGIBLE.value,
        query_fingerprint=QueryFingerprint("sha256", "v1", "0" * 64),
        filter_snapshot_ref=_make_artifact("filter_snapshot"),
        evidence_index_ref=_make_artifact("knowledge_index"),
        retrieval_config_ref=_make_artifact("retrieval_config"),
        adapter_artifact_ref=_make_artifact("adapter"),
        query_embedding_sha256="d" * 64,
        signal_manifest_sha256="1" * 64,
        hit_manifest_sha256="2" * 64,
        selection_manifest_sha256="3" * 64,
    )


def _authenticated(
    bindings: tuple[RequestSourceMemberBinding, ...],
) -> SyncGuideEvidenceAuthorityOutcome:
    return SyncGuideEvidenceAuthorityOutcome(
        decision=SyncGuideEvidenceAuthorityDecision.AUTHENTICATED,
        reasons=(),
        bindings=bindings,
    )


def _retrieval(
    *,
    selected_hits: tuple[ProductionSearchHit, ...] | None = None,
    status: RetrievalExecutionStatus = RetrievalExecutionStatus.SUCCEEDED,
    receipt: ProductionSearchReceipt | None = None,
    gate_outcome: EvidenceGateOutcome | None = None,
) -> ProductionRetrievalOutcome:
    gate: EvidenceGateOutcome
    if gate_outcome is not None:
        gate = gate_outcome
    else:
        gate = EvidenceGateSuccess(selected_hits=selected_hits or ())
    return ProductionRetrievalOutcome(
        status=status,
        receipt=receipt if receipt is not None else _make_receipt(),
        gate_outcome=gate,
    )


@dataclass(frozen=True)
class _Member:
    """A Source Member authority unit plus the chunk hits selected under it."""

    snapshot_id: UUID
    member_id: UUID

    def binding(self) -> RequestSourceMemberBinding:
        return _make_binding(snapshot_id=self.snapshot_id, member_id=self.member_id)

    def chunk(self, *, rank: int, external_doc_id: str = "DOC-1", chunk_index: int = 0) -> ProductionSearchHit:
        return _make_hit(
            rank=rank,
            snapshot_id=self.snapshot_id,
            member_id=self.member_id,
            external_doc_id=external_doc_id,
            chunk_index=chunk_index,
        )


def _member() -> _Member:
    return _Member(snapshot_id=uuid4(), member_id=uuid4())


def _pair(*, rank: int = 1, external_doc_id: str = "DOC-1") -> tuple[ProductionSearchHit, RequestSourceMemberBinding]:
    member = _member()
    return member.chunk(rank=rank, external_doc_id=external_doc_id), member.binding()


# ==============================================================================
# 1-2. Valid joins
# ==============================================================================


def test_single_valid_join_is_authenticated() -> None:
    hit, binding = _pair()

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding,)),
        retrieval_outcome=_retrieval(selected_hits=(hit,)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.AUTHENTICATED
    assert outcome.reasons == ()
    assert len(outcome.selections) == 1
    assert outcome.selections[0].hit is hit
    assert outcome.selections[0].binding is binding


def test_multiple_valid_joins_preserve_selected_hit_order() -> None:
    hit1, binding1 = _pair(rank=1, external_doc_id="DOC-1")
    hit2, binding2 = _pair(rank=2, external_doc_id="DOC-2")
    hit3, binding3 = _pair(rank=4, external_doc_id="DOC-3")
    selected = (hit1, hit2, hit3)

    outcome = compose_guide_authority_with_production_retrieval(
        # Authority binding order deliberately differs from production selection order.
        authority_outcome=_authenticated((binding3, binding1, binding2)),
        retrieval_outcome=_retrieval(selected_hits=selected),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.AUTHENTICATED
    assert outcome.reasons == ()
    assert tuple(s.hit for s in outcome.selections) == selected
    assert tuple(s.hit.fusion_rank for s in outcome.selections) == (1, 2, 4)
    assert tuple(s.binding for s in outcome.selections) == (binding1, binding2, binding3)


def test_successful_composition_carries_retrieval_receipt() -> None:
    hit, binding = _pair()
    receipt = _make_receipt()

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding,)),
        retrieval_outcome=_retrieval(selected_hits=(hit,), receipt=receipt),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.AUTHENTICATED
    assert outcome.retrieval_receipt is receipt


# ==============================================================================
# 3. Authority preconditions
# ==============================================================================


def test_rejected_authority_is_not_consumed() -> None:
    hit, binding = _pair()
    rejected = SyncGuideEvidenceAuthorityOutcome(
        decision=SyncGuideEvidenceAuthorityDecision.REJECTED,
        reasons=(SyncGuideEvidenceAuthorityReason.MEMBER_DECISION_NOT_PASS,),
        # Bindings must not be consumed even when incidentally present.
        bindings=(binding,),
    )

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=rejected,
        retrieval_outcome=_retrieval(selected_hits=(hit,)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.AUTHORITY_NOT_AUTHENTICATED,)
    assert outcome.selections == ()
    assert outcome.retrieval_receipt is None


def test_foreign_authority_outcome_type_is_rejected() -> None:
    hit, _binding = _pair()

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome="AUTHENTICATED",  # type: ignore[arg-type]
        retrieval_outcome=_retrieval(selected_hits=(hit,)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.AUTHORITY_NOT_AUTHENTICATED,)
    assert outcome.selections == ()


# ==============================================================================
# 4-7. Retrieval preconditions
# ==============================================================================


def test_retrieval_status_not_succeeded_is_rejected() -> None:
    hit, binding = _pair()

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding,)),
        retrieval_outcome=_retrieval(
            selected_hits=(hit,),
            status=RetrievalExecutionStatus.DEPENDENCY_ERROR,
        ),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.RETRIEVAL_NOT_SUCCEEDED,)
    assert outcome.selections == ()


def test_missing_retrieval_receipt_is_rejected() -> None:
    hit, binding = _pair()

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding,)),
        retrieval_outcome=ProductionRetrievalOutcome(
            status=RetrievalExecutionStatus.SUCCEEDED,
            receipt=None,
            gate_outcome=EvidenceGateSuccess(selected_hits=(hit,)),
        ),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.RETRIEVAL_RECEIPT_MISSING,)
    assert outcome.selections == ()


def test_foreign_receipt_type_is_rejected() -> None:
    hit, binding = _pair()

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding,)),
        retrieval_outcome=ProductionRetrievalOutcome(
            status=RetrievalExecutionStatus.SUCCEEDED,
            receipt="production_search_receipt",  # type: ignore[arg-type]
            gate_outcome=EvidenceGateSuccess(selected_hits=(hit,)),
        ),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.RETRIEVAL_RECEIPT_MISSING,)


def test_receipt_execution_status_not_succeeded_is_rejected() -> None:
    hit, binding = _pair()

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding,)),
        retrieval_outcome=_retrieval(
            selected_hits=(hit,),
            receipt=_make_receipt(RetrievalExecutionStatus.VALIDATION_ERROR),
        ),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.RETRIEVAL_NOT_SUCCEEDED,)
    assert outcome.selections == ()


def test_gate_no_result_is_rejected() -> None:
    _hit, binding = _pair()

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding,)),
        retrieval_outcome=_retrieval(
            gate_outcome=EvidenceGateNoResult(message="No candidates passed eligibility"),
        ),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.EVIDENCE_GATE_NOT_SUCCEEDED,)
    assert outcome.selections == ()


def test_gate_failure_is_rejected() -> None:
    _hit, binding = _pair()

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding,)),
        retrieval_outcome=_retrieval(
            gate_outcome=EvidenceGateFailure(
                status=EvidenceGateStatus.DEPENDENCY_ERROR,
                reason=EvidenceGateReason.INELIGIBLE,
                message="Eligibility verifier unavailable",
            ),
        ),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.EVIDENCE_GATE_NOT_SUCCEEDED,)


def test_precondition_order_status_before_receipt() -> None:
    _hit, binding = _pair()

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding,)),
        retrieval_outcome=ProductionRetrievalOutcome(
            status=RetrievalExecutionStatus.DEPENDENCY_ERROR,
            receipt=None,
            gate_outcome=EvidenceGateFailure(
                status=EvidenceGateStatus.DEPENDENCY_ERROR,
                reason=EvidenceGateReason.INELIGIBLE,
                message="Search failed",
            ),
        ),
    )

    assert outcome.reasons == (GuideRetrievalCompositionReason.RETRIEVAL_NOT_SUCCEEDED,)


# ==============================================================================
# N chunk hits : 1 authenticated member binding
#
# The authority join key is a Source Member unit while selected_hits is a chunk
# unit, so several distinct chunks of one Source Member are a normal production
# result and must not be rejected as a cardinality violation.
# ==============================================================================


def test_two_chunks_of_one_member_share_a_single_binding() -> None:
    member = _member()
    binding = member.binding()
    chunk1 = member.chunk(rank=1, external_doc_id="DOC-1", chunk_index=0)
    chunk2 = member.chunk(rank=2, external_doc_id="DOC-2", chunk_index=3)

    assert chunk1.provenance.source_snapshot_id == chunk2.provenance.source_snapshot_id
    assert chunk1.provenance.source_snapshot_member_id == chunk2.provenance.source_snapshot_member_id
    assert chunk1.provenance.source_code == chunk2.provenance.source_code
    assert chunk1.provenance.source_version == chunk2.provenance.source_version
    assert chunk1.provenance.knowledge_chunk_id != chunk2.provenance.knowledge_chunk_id

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding,)),
        retrieval_outcome=_retrieval(selected_hits=(chunk1, chunk2)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.AUTHENTICATED
    assert outcome.reasons == ()
    assert len(outcome.selections) == 2
    assert tuple(s.hit for s in outcome.selections) == (chunk1, chunk2)
    assert all(s.binding is binding for s in outcome.selections)


def test_top_5_chunks_of_one_member_reuse_one_binding_in_order() -> None:
    member = _member()
    binding = member.binding()
    chunks = tuple(member.chunk(rank=rank, external_doc_id=f"DOC-{rank}", chunk_index=rank) for rank in range(1, 6))

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding,)),
        retrieval_outcome=_retrieval(selected_hits=chunks),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.AUTHENTICATED
    assert outcome.reasons == ()
    assert tuple(s.hit for s in outcome.selections) == chunks
    assert tuple(s.hit.fusion_rank for s in outcome.selections) == (1, 2, 3, 4, 5)
    assert all(s.binding is binding for s in outcome.selections)


def test_mixed_members_reuse_their_own_bindings_and_preserve_order() -> None:
    member_a = _member()
    member_b = _member()
    binding_a = member_a.binding()
    binding_b = member_b.binding()
    chunk_a1 = member_a.chunk(rank=1, external_doc_id="DOC-A1", chunk_index=0)
    chunk_a2 = member_a.chunk(rank=2, external_doc_id="DOC-A2", chunk_index=1)
    chunk_b1 = member_b.chunk(rank=3, external_doc_id="DOC-B1", chunk_index=0)
    selected = (chunk_a1, chunk_a2, chunk_b1)

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding_b, binding_a)),
        retrieval_outcome=_retrieval(selected_hits=selected),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.AUTHENTICATED
    assert outcome.reasons == ()
    assert tuple(s.hit for s in outcome.selections) == selected
    assert tuple(s.binding for s in outcome.selections) == (binding_a, binding_a, binding_b)


def test_unused_member_binding_is_rejected_even_when_every_hit_joined() -> None:
    # bindings {A, B} vs hits {A/chunk-1, A/chunk-2}: every hit joined, but the
    # binding set is a superset of the member keys the production Gate selected.
    member_a = _member()
    member_b = _member()
    chunk_a1 = member_a.chunk(rank=1, external_doc_id="DOC-A1", chunk_index=0)
    chunk_a2 = member_a.chunk(rank=2, external_doc_id="DOC-A2", chunk_index=1)

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((member_a.binding(), member_b.binding())),
        retrieval_outcome=_retrieval(selected_hits=(chunk_a1, chunk_a2)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.EXTRA_BINDING,)
    assert outcome.selections == ()


def test_multi_chunk_member_with_one_unbound_chunk_is_rejected() -> None:
    member = _member()
    unrelated = _member()
    chunk1 = member.chunk(rank=1, external_doc_id="DOC-1", chunk_index=0)
    chunk2 = unrelated.chunk(rank=2, external_doc_id="DOC-2", chunk_index=1)

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((member.binding(),)),
        retrieval_outcome=_retrieval(selected_hits=(chunk1, chunk2)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.BINDING_NOT_FOUND,)
    assert outcome.selections == ()


# ==============================================================================
# 8-9. Missing / extra (caller scope precondition)
#
# The caller must supply bindings scoped to exactly the distinct Source Member
# keys represented by selected_hits. An authority superset is not filtered here:
# discarding part of an authenticated outcome would be an authority-selection
# policy this seam does not own, so an unused binding is EXTRA_BINDING instead.
# ==============================================================================


def test_selected_hit_without_binding_is_rejected() -> None:
    hit1, binding1 = _pair(rank=1, external_doc_id="DOC-1")
    hit2, _binding2 = _pair(rank=2, external_doc_id="DOC-2")

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding1,)),
        retrieval_outcome=_retrieval(selected_hits=(hit1, hit2)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.BINDING_NOT_FOUND,)
    assert outcome.selections == ()


def test_binding_without_selected_hit_is_rejected() -> None:
    hit1, binding1 = _pair(rank=1, external_doc_id="DOC-1")
    _hit2, binding2 = _pair(rank=2, external_doc_id="DOC-2")

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding1, binding2)),
        retrieval_outcome=_retrieval(selected_hits=(hit1,)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.EXTRA_BINDING,)
    assert outcome.selections == ()


def test_empty_selected_hits_with_bindings_is_rejected() -> None:
    _hit, binding = _pair()

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding,)),
        retrieval_outcome=_retrieval(selected_hits=()),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.EXTRA_BINDING,)


# ==============================================================================
# 10-11. Duplicates
# ==============================================================================


def test_duplicate_binding_join_key_is_rejected() -> None:
    hit, binding = _pair()
    duplicate = _make_binding(
        snapshot_id=binding.source_snapshot_id,
        member_id=binding.source_snapshot_member_id,
    )

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding, duplicate)),
        retrieval_outcome=_retrieval(selected_hits=(hit,)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.DUPLICATE_BINDING,)
    assert outcome.selections == ()


def test_duplicate_stable_coordinate_hit_is_rejected() -> None:
    member = _member()
    hit = member.chunk(rank=1, external_doc_id="DOC-1", chunk_index=0)
    # Same stable coordinate: (source_code, source_version, external_document_id, chunk_index).
    duplicate_hit = member.chunk(rank=2, external_doc_id="DOC-1", chunk_index=0)

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((member.binding(),)),
        retrieval_outcome=_retrieval(selected_hits=(hit, duplicate_hit)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.DUPLICATE_HIT,)
    assert outcome.selections == ()


def test_duplicate_stable_coordinate_across_distinct_members_is_rejected() -> None:
    member_a = _member()
    member_b = _member()
    hit_a = member_a.chunk(rank=1, external_doc_id="DOC-1", chunk_index=0)
    hit_b = member_b.chunk(rank=2, external_doc_id="DOC-1", chunk_index=0)

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((member_a.binding(), member_b.binding())),
        retrieval_outcome=_retrieval(selected_hits=(hit_a, hit_b)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.DUPLICATE_HIT,)


def test_duplicate_binding_is_not_deduplicated_into_success() -> None:
    # N hits : 1 binding allows chunk-side cardinality only. Authority-side
    # multiplicity stays ambiguous input and is rejected rather than deduped.
    hit, binding = _pair()

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding, binding)),
        retrieval_outcome=_retrieval(selected_hits=(hit,)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.DUPLICATE_BINDING,)


# ==============================================================================
# 12-16. Join key mismatches (a mismatched key is simply a hit with no binding)
# ==============================================================================


def test_source_snapshot_id_mismatch_is_rejected() -> None:
    hit, binding = _pair()
    mismatched = replace(binding, source_snapshot_id=uuid4())

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((mismatched,)),
        retrieval_outcome=_retrieval(selected_hits=(hit,)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.BINDING_NOT_FOUND,)
    assert outcome.selections == ()


def test_source_snapshot_member_id_mismatch_is_rejected() -> None:
    hit, binding = _pair()
    mismatched = replace(binding, source_snapshot_member_id=uuid4())

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((mismatched,)),
        retrieval_outcome=_retrieval(selected_hits=(hit,)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.BINDING_NOT_FOUND,)


def test_source_code_mismatch_is_rejected() -> None:
    hit, binding = _pair()
    mismatched = replace(binding, source_code="OTHER_SOURCE")

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((mismatched,)),
        retrieval_outcome=_retrieval(selected_hits=(hit,)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.BINDING_NOT_FOUND,)


def test_source_version_mismatch_is_rejected() -> None:
    hit, binding = _pair()
    mismatched = replace(binding, source_version="2026.2")

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((mismatched,)),
        retrieval_outcome=_retrieval(selected_hits=(hit,)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.BINDING_NOT_FOUND,)


def test_source_code_is_not_silently_normalized() -> None:
    hit, binding = _pair()
    mismatched = replace(binding, source_code=binding.source_code.lower())

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((mismatched,)),
        retrieval_outcome=_retrieval(selected_hits=(hit,)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.BINDING_NOT_FOUND,)


def test_source_version_whitespace_is_not_trimmed() -> None:
    hit, binding = _pair()
    mismatched = replace(binding, source_version=f" {binding.source_version} ")

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((mismatched,)),
        retrieval_outcome=_retrieval(selected_hits=(hit,)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.BINDING_NOT_FOUND,)


def test_single_mismatch_rejects_whole_composition_without_partial_success() -> None:
    hit1, binding1 = _pair(rank=1, external_doc_id="DOC-1")
    hit2, binding2 = _pair(rank=2, external_doc_id="DOC-2")
    hit3, binding3 = _pair(rank=3, external_doc_id="DOC-3")
    mismatched = replace(binding2, source_version="2026.9")

    outcome = compose_guide_authority_with_production_retrieval(
        authority_outcome=_authenticated((binding1, mismatched, binding3)),
        retrieval_outcome=_retrieval(selected_hits=(hit1, hit2, hit3)),
    )

    assert outcome.decision == GuideRetrievalCompositionDecision.REJECTED
    assert outcome.reasons == (GuideRetrievalCompositionReason.BINDING_NOT_FOUND,)
    assert outcome.selections == ()
    assert outcome.retrieval_receipt is None
