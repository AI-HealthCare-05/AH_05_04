from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from ai_worker.tasks.rag.evidence_gate import (
    RERANK_OUTPUT_PROJECTION_VERSION,
    EvidenceAssessmentStance,
    EvidenceEligibilityVerificationFailure,
    EvidenceEligibilityVerificationSuccess,
    EvidenceGateAssessment,
    EvidenceGateExecutionStatus,
    EvidenceGateReason,
    EvidenceGateRequest,
    EvidenceGateRetrievalReceipt,
    EvidenceStatus,
    VersionedEvidenceGatePolicy,
    canonical_gate_selection_hash,
    canonical_rerank_output_hash,
    evaluate_evidence_gate,
)
from ai_worker.tasks.rag.evidence_retrieval import (
    CanonicalScore,
    EvidenceSearchStage,
    ImmutableArtifactRef,
    KnowledgeEvidenceCandidate,
    KnowledgeEvidenceProvenance,
    QueryFingerprint,
    SensitiveText,
    StageSignal,
    UntrustedKnowledgeEvidenceSelection,
)

NOW = datetime(2026, 9, 8, 3, 0, tzinfo=UTC)


def artifact(code: str, *, digest: str = "a" * 64) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(code, f"{code}@synthetic-1", digest)


def retrieval_receipt(
    *,
    selections: tuple[UntrustedKnowledgeEvidenceSelection, ...] | None = None,
    query_fingerprint: QueryFingerprint | None = None,
    filter_snapshot_ref: ImmutableArtifactRef | None = None,
    retrieval_config_ref: ImmutableArtifactRef | None = None,
    input_set_hash: str = "d" * 64,
    output_projection_version: str = RERANK_OUTPUT_PROJECTION_VERSION,
) -> EvidenceGateRetrievalReceipt:
    output_selections = selections if selections is not None else (selection(),)
    return EvidenceGateRetrievalReceipt.create(
        "evidence-retrieval-receipt",
        "evidence-retrieval-receipt@synthetic-1",
        query_fingerprint=query_fingerprint or QueryFingerprint("HMAC-SHA-256", "query-hmac@synthetic-1", "c" * 64),
        filter_snapshot_ref=filter_snapshot_ref or artifact("filter-snapshot"),
        evidence_index_ref=artifact("knowledge-index"),
        retrieval_config_ref=retrieval_config_ref or artifact("retrieval-config"),
        rerank_config_ref=artifact("rerank-config"),
        rerank_input_projection_version="knowledge-rerank-input-v1",
        input_set_hash=input_set_hash,
        rerank_output_projection_version=output_projection_version,
        rerank_output_hash=canonical_rerank_output_hash(output_projection_version, output_selections),
    )


def selection(
    evidence_key: str = "knowledge:chunk-1",
    *,
    source_snapshot_ref: ImmutableArtifactRef | None = None,
    text: str = "합성 복약 근거",
    rank: int = 1,
) -> UntrustedKnowledgeEvidenceSelection:
    content_sha256 = hashlib.sha256(text.encode()).hexdigest()
    provenance = KnowledgeEvidenceProvenance(
        evidence_key=evidence_key,
        knowledge_chunk_ref=f"chunk-{rank}",
        evidence_index_ref=artifact("knowledge-index"),
        source_snapshot_ref=source_snapshot_ref or artifact(f"source-{rank}"),
        source_version=f"mfds-synthetic@{rank}",
        locator=f"$.records.{rank}",
        content_sha256=content_sha256,
        canonicalization_spec_version="knowledge-text@1",
    )
    candidate = KnowledgeEvidenceCandidate(
        provenance=provenance,
        content_text=SensitiveText(text),
        stage_signals=(StageSignal(EvidenceSearchStage.LEXICAL, rank, CanonicalScore("0.9")),),
    )
    return UntrustedKnowledgeEvidenceSelection(candidate, rank, CanonicalScore("0.9"))


def assessment(
    selected: UntrustedKnowledgeEvidenceSelection,
    *,
    coverage_key: str = "medication-usage",
    stance: EvidenceAssessmentStance = EvidenceAssessmentStance.SUPPORTS,
    valid_from: datetime = NOW - timedelta(days=1),
    valid_until: datetime = NOW + timedelta(days=1),
    gate_retrieval_receipt: EvidenceGateRetrievalReceipt | None = None,
) -> EvidenceGateAssessment:
    provenance = selected.candidate.provenance
    return EvidenceGateAssessment.create(
        "evidence-assessment",
        f"evidence-assessment@synthetic-{provenance.evidence_key}",
        selection=selected,
        coverage_key=coverage_key,
        stance=stance,
        retrieval_receipt_ref=(gate_retrieval_receipt or retrieval_receipt(selections=(selected,))).artifact_ref,
        eligibility_receipt_ref=artifact(f"eligibility-{provenance.evidence_key}"),
        valid_from=valid_from,
        valid_until=valid_until,
    )


def policy(
    *,
    minimum_supporting_items_per_coverage: int = 1,
    minimum_distinct_source_snapshots_per_coverage: int = 1,
) -> VersionedEvidenceGatePolicy:
    return VersionedEvidenceGatePolicy.create(
        "evidence-gate-policy",
        "evidence-gate-policy@synthetic-1",
        minimum_supporting_items_per_coverage=minimum_supporting_items_per_coverage,
        minimum_distinct_source_snapshots_per_coverage=minimum_distinct_source_snapshots_per_coverage,
    )


def request(
    selections: tuple[UntrustedKnowledgeEvidenceSelection, ...],
    assessments: tuple[EvidenceGateAssessment, ...],
    *,
    required_coverage_keys: tuple[str, ...] = ("medication-usage",),
    gate_policy: VersionedEvidenceGatePolicy | None = None,
    gate_retrieval_receipt: EvidenceGateRetrievalReceipt | None = None,
) -> EvidenceGateRequest:
    return EvidenceGateRequest(
        selections=selections,
        assessments=assessments,
        retrieval_receipt=gate_retrieval_receipt or retrieval_receipt(selections=selections),
        required_coverage_keys=required_coverage_keys,
        evaluated_at=NOW,
        policy=gate_policy or policy(),
    )


class SyntheticEligibilityVerifier:
    def verify(
        self,
        assessment: EvidenceGateAssessment,
        gate_retrieval_receipt: EvidenceGateRetrievalReceipt,
    ) -> EvidenceEligibilityVerificationSuccess:
        return EvidenceEligibilityVerificationSuccess(
            assessment_artifact_ref=assessment.assessment_artifact_ref,
            eligibility_receipt_ref=assessment.eligibility_receipt_ref,
            selection_projection_hash=assessment.selection_projection_hash,
            retrieval_receipt_ref=gate_retrieval_receipt.artifact_ref,
            verifier_artifact_ref=artifact("synthetic-eligibility-verifier"),
        )


class RejectingEligibilityVerifier:
    def verify(
        self,
        assessment: EvidenceGateAssessment,
        gate_retrieval_receipt: EvidenceGateRetrievalReceipt,
    ) -> EvidenceEligibilityVerificationFailure:
        return EvidenceEligibilityVerificationFailure()


class MismatchingEligibilityVerifier(SyntheticEligibilityVerifier):
    def verify(
        self,
        assessment: EvidenceGateAssessment,
        gate_retrieval_receipt: EvidenceGateRetrievalReceipt,
    ) -> EvidenceEligibilityVerificationSuccess:
        return replace(
            super().verify(assessment, gate_retrieval_receipt),
            selection_projection_hash="f" * 64,
        )


class MismatchingRetrievalReceiptVerifier(SyntheticEligibilityVerifier):
    def verify(
        self,
        assessment: EvidenceGateAssessment,
        gate_retrieval_receipt: EvidenceGateRetrievalReceipt,
    ) -> EvidenceEligibilityVerificationSuccess:
        return replace(
            super().verify(assessment, gate_retrieval_receipt),
            retrieval_receipt_ref=artifact("other-retrieval-receipt"),
        )


class RaisingEligibilityVerifier:
    def verify(
        self,
        assessment: EvidenceGateAssessment,
        gate_retrieval_receipt: EvidenceGateRetrievalReceipt,
    ) -> EvidenceEligibilityVerificationSuccess:
        raise RuntimeError("private-provider-detail")


class MutatingEligibilityVerifier(SyntheticEligibilityVerifier):
    def __init__(self, mutation: str) -> None:
        self.mutation = mutation

    def verify(
        self,
        assessment: EvidenceGateAssessment,
        gate_retrieval_receipt: EvidenceGateRetrievalReceipt,
    ) -> EvidenceEligibilityVerificationSuccess:
        if self.mutation == "stance":
            object.__setattr__(assessment, "stance", EvidenceAssessmentStance.SUPPORTS)
        elif self.mutation == "freshness":
            object.__setattr__(assessment, "valid_until", NOW + timedelta(days=30))
        elif self.mutation == "receipt":
            object.__setattr__(assessment, "eligibility_receipt_ref", artifact("other-receipt"))
        else:
            object.__setattr__(assessment.eligibility_receipt_ref, "version", "tampered-version")
        return super().verify(assessment, gate_retrieval_receipt)


class MutatingRetrievalReceiptVerifier(SyntheticEligibilityVerifier):
    def verify(
        self,
        assessment: EvidenceGateAssessment,
        gate_retrieval_receipt: EvidenceGateRetrievalReceipt,
    ) -> EvidenceEligibilityVerificationSuccess:
        object.__setattr__(gate_retrieval_receipt, "input_set_hash", "e" * 64)
        return super().verify(assessment, gate_retrieval_receipt)


class ExplodingDeepcopyStr(str):
    def __deepcopy__(self, memo: object) -> str:
        raise RuntimeError("private-snapshot-detail")


def evaluate(gate_request: EvidenceGateRequest):
    return evaluate_evidence_gate(
        gate_request,
        eligibility_verifier=SyntheticEligibilityVerifier(),
    )


def test_current_supporting_evidence_passes_gate() -> None:
    selected = selection()
    selected_assessment = assessment(selected)

    outcome = evaluate(request((selected,), (selected_assessment,)))

    assert outcome.execution_status is EvidenceGateExecutionStatus.SUCCEEDED
    assert outcome.evidence_status is EvidenceStatus.SUFFICIENT
    assert outcome.reason is EvidenceGateReason.EVIDENCE_SUFFICIENT
    assert tuple(canonical_gate_selection_hash(item.selection) for item in outcome.gate_passed_selections) == (
        canonical_gate_selection_hash(selected),
    )
    assert outcome.trace is not None
    assert outcome.trace.selected_evidence_keys == ("knowledge:chunk-1",)
    assert outcome.trace.evaluated_at == NOW
    assert outcome.trace.assessment_artifact_refs == (selected_assessment.assessment_artifact_ref,)
    assert outcome.trace.retrieval_receipt_ref == retrieval_receipt().artifact_ref
    assert outcome.gate_passed_selections[0].retrieval_receipt_ref == retrieval_receipt().artifact_ref


@pytest.mark.parametrize(
    "current_receipt",
    [
        retrieval_receipt(query_fingerprint=QueryFingerprint("HMAC-SHA-256", "query-hmac@synthetic-2", "e" * 64)),
        retrieval_receipt(filter_snapshot_ref=artifact("other-filter-snapshot")),
        retrieval_receipt(retrieval_config_ref=artifact("other-retrieval-config")),
        retrieval_receipt(input_set_hash="e" * 64),
    ],
)
def test_assessment_from_another_retrieval_context_cannot_be_replayed(
    current_receipt: EvidenceGateRetrievalReceipt,
) -> None:
    selected = selection()
    previous_receipt = retrieval_receipt()
    previous_assessment = assessment(selected, gate_retrieval_receipt=previous_receipt)

    assert_request_invalid(
        request(
            (selected,),
            (previous_assessment,),
            gate_retrieval_receipt=current_receipt,
        )
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(
            value,
            candidate=replace(
                value.candidate,
                provenance=replace(value.candidate.provenance, evidence_key="knowledge:replayed"),
            ),
        ),
        lambda value: replace(value, rerank_score=CanonicalScore("0.8")),
    ],
)
def test_same_receipt_cannot_replay_changed_rerank_selection(mutation) -> None:
    original = selection()
    gate_retrieval_receipt = retrieval_receipt()
    changed = mutation(original)

    assert_request_invalid(
        request(
            (changed,),
            (assessment(changed, gate_retrieval_receipt=gate_retrieval_receipt),),
            gate_retrieval_receipt=gate_retrieval_receipt,
        )
    )


def test_same_receipt_cannot_replay_changed_rerank_ranks() -> None:
    first = selection("knowledge:first", rank=1)
    second = selection("knowledge:second", rank=2)
    gate_retrieval_receipt = retrieval_receipt(selections=(first, second))
    changed = (
        replace(first, rerank_rank=2),
        replace(second, rerank_rank=1),
    )

    assert_request_invalid(
        request(
            changed,
            tuple(assessment(item, gate_retrieval_receipt=gate_retrieval_receipt) for item in changed),
            gate_retrieval_receipt=gate_retrieval_receipt,
        )
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(
            value,
            query_fingerprint=QueryFingerprint("HMAC-SHA-256", "query-hmac@synthetic-2", "e" * 64),
        ),
        lambda value: replace(value, filter_snapshot_ref=artifact("other-filter-snapshot")),
        lambda value: replace(value, retrieval_config_ref=artifact("other-retrieval-config")),
        lambda value: replace(value, input_set_hash="e" * 64),
        lambda value: replace(value, rerank_output_projection_version="evidence-rerank-output-v2"),
        lambda value: replace(value, rerank_output_hash="e" * 64),
    ],
)
def test_retrieval_receipt_values_must_remain_bound_to_receipt_artifact(mutation) -> None:
    selected = selection()
    gate_retrieval_receipt = retrieval_receipt()
    selected_assessment = assessment(selected, gate_retrieval_receipt=gate_retrieval_receipt)

    assert_request_invalid(
        request(
            (selected,),
            (selected_assessment,),
            gate_retrieval_receipt=mutation(gate_retrieval_receipt),
        )
    )


def test_unknown_rerank_output_projection_version_is_rejected() -> None:
    selected = selection()
    gate_retrieval_receipt = retrieval_receipt(
        selections=(selected,),
        output_projection_version="evidence-rerank-output-v2",
    )

    assert_request_invalid(
        request(
            (selected,),
            (assessment(selected, gate_retrieval_receipt=gate_retrieval_receipt),),
            gate_retrieval_receipt=gate_retrieval_receipt,
        )
    )


def test_successful_outcome_does_not_alias_caller_owned_request_objects() -> None:
    selected = selection()
    gate_retrieval_receipt = retrieval_receipt()
    selected_assessment = assessment(selected, gate_retrieval_receipt=gate_retrieval_receipt)
    gate_request = request(
        (selected,),
        (selected_assessment,),
        gate_retrieval_receipt=gate_retrieval_receipt,
    )

    outcome = evaluate(gate_request)
    passed = outcome.gate_passed_selections[0]
    original_locator = passed.selection.candidate.provenance.locator
    original_assessment_version = passed.assessment_artifact_ref.version
    original_retrieval_receipt_version = passed.retrieval_receipt_ref.version

    object.__setattr__(selected.candidate.content_text, "_SensitiveText__value", "TAMPERED-AFTER-GATE")
    object.__setattr__(selected.candidate.provenance, "locator", "$.tampered")
    object.__setattr__(selected_assessment.assessment_artifact_ref, "version", "tampered-version")
    object.__setattr__(gate_retrieval_receipt.artifact_ref, "version", "tampered-version")

    assert passed.selection.candidate.content_text.reveal() == "합성 복약 근거"
    assert passed.selection.candidate.provenance.locator == original_locator
    assert passed.assessment_artifact_ref.version == original_assessment_version
    assert passed.retrieval_receipt_ref.version == original_retrieval_receipt_version
    assert outcome.trace is not None
    assert outcome.trace.assessment_artifact_refs[0].version == original_assessment_version
    assert outcome.trace.retrieval_receipt_ref.version == original_retrieval_receipt_version


def test_evidence_expiring_at_evaluation_time_is_stale_and_returns_no_selection() -> None:
    selected = selection()

    outcome = evaluate(request((selected,), (assessment(selected, valid_until=NOW),)))

    assert outcome.execution_status is EvidenceGateExecutionStatus.NO_RESULT
    assert outcome.evidence_status is EvidenceStatus.STALE
    assert outcome.reason is EvidenceGateReason.EVIDENCE_STALE
    assert outcome.gate_passed_selections == ()
    assert outcome.trace is not None
    assert outcome.trace.stale_evidence_keys == ("knowledge:chunk-1",)


def test_opposing_current_evidence_for_same_coverage_is_conflicted() -> None:
    supporting = selection("knowledge:support", rank=1)
    contradicting = selection("knowledge:contradict", rank=2)
    gate_retrieval_receipt = retrieval_receipt(selections=(supporting, contradicting))

    outcome = evaluate(
        request(
            (supporting, contradicting),
            (
                assessment(supporting, gate_retrieval_receipt=gate_retrieval_receipt),
                assessment(
                    contradicting,
                    stance=EvidenceAssessmentStance.CONTRADICTS,
                    gate_retrieval_receipt=gate_retrieval_receipt,
                ),
            ),
            gate_retrieval_receipt=gate_retrieval_receipt,
        )
    )

    assert outcome.execution_status is EvidenceGateExecutionStatus.NO_RESULT
    assert outcome.evidence_status is EvidenceStatus.CONFLICTED
    assert outcome.reason is EvidenceGateReason.EVIDENCE_CONFLICTED
    assert outcome.gate_passed_selections == ()
    assert outcome.trace is not None
    assert outcome.trace.conflicting_coverage_keys == ("medication-usage",)


def test_missing_required_coverage_is_insufficient() -> None:
    selected = selection()

    outcome = evaluate(
        request(
            (selected,),
            (assessment(selected),),
            required_coverage_keys=("medication-usage", "medication-warning"),
        )
    )

    assert outcome.execution_status is EvidenceGateExecutionStatus.NO_RESULT
    assert outcome.evidence_status is EvidenceStatus.INSUFFICIENT
    assert outcome.reason is EvidenceGateReason.EVIDENCE_INSUFFICIENT
    assert outcome.trace is not None
    assert outcome.trace.insufficient_coverage_keys == ("medication-warning",)


def test_policy_minimum_supporting_item_count_is_enforced() -> None:
    selected = selection()

    outcome = evaluate(
        request(
            (selected,),
            (assessment(selected),),
            gate_policy=policy(minimum_supporting_items_per_coverage=2),
        )
    )

    assert outcome.evidence_status is EvidenceStatus.INSUFFICIENT
    assert outcome.gate_passed_selections == ()


def test_policy_minimum_distinct_source_count_does_not_count_two_chunks_twice() -> None:
    shared_source = artifact("shared-source")
    first = selection("knowledge:first", source_snapshot_ref=shared_source, rank=1)
    second = selection("knowledge:second", source_snapshot_ref=shared_source, rank=2)
    gate_retrieval_receipt = retrieval_receipt(selections=(first, second))

    outcome = evaluate(
        request(
            (first, second),
            (
                assessment(first, gate_retrieval_receipt=gate_retrieval_receipt),
                assessment(second, gate_retrieval_receipt=gate_retrieval_receipt),
            ),
            gate_policy=policy(
                minimum_supporting_items_per_coverage=2,
                minimum_distinct_source_snapshots_per_coverage=2,
            ),
            gate_retrieval_receipt=gate_retrieval_receipt,
        )
    )

    assert outcome.evidence_status is EvidenceStatus.INSUFFICIENT
    assert outcome.trace is not None
    assert outcome.trace.insufficient_coverage_keys == ("medication-usage",)


def test_stale_status_precedes_conflict_and_insufficiency() -> None:
    supporting = selection("knowledge:support", rank=1)
    contradicting = selection("knowledge:contradict", rank=2)
    gate_retrieval_receipt = retrieval_receipt(selections=(supporting, contradicting))

    outcome = evaluate(
        request(
            (supporting, contradicting),
            (
                assessment(
                    supporting,
                    valid_until=NOW,
                    gate_retrieval_receipt=gate_retrieval_receipt,
                ),
                assessment(
                    contradicting,
                    stance=EvidenceAssessmentStance.CONTRADICTS,
                    gate_retrieval_receipt=gate_retrieval_receipt,
                ),
            ),
            required_coverage_keys=("medication-usage", "medication-warning"),
            gate_retrieval_receipt=gate_retrieval_receipt,
        )
    )

    assert outcome.evidence_status is EvidenceStatus.STALE
    assert outcome.reason is EvidenceGateReason.EVIDENCE_STALE


def assert_request_invalid(gate_request: EvidenceGateRequest) -> None:
    outcome = evaluate(gate_request)

    assert outcome.execution_status is EvidenceGateExecutionStatus.VALIDATION_ERROR
    assert outcome.evidence_status is None
    assert outcome.reason is EvidenceGateReason.REQUEST_INVALID
    assert outcome.gate_passed_selections == ()
    assert outcome.trace is None


def test_no_selected_evidence_is_a_valid_insufficient_result() -> None:
    outcome = evaluate(request((), ()))

    assert outcome.execution_status is EvidenceGateExecutionStatus.NO_RESULT
    assert outcome.evidence_status is EvidenceStatus.INSUFFICIENT
    assert outcome.reason is EvidenceGateReason.EVIDENCE_INSUFFICIENT


@pytest.mark.parametrize("include_extra", [False, True])
def test_assessments_must_match_selected_evidence_one_to_one(include_extra: bool) -> None:
    selected = selection()
    assessments: tuple[EvidenceGateAssessment, ...] = ()
    if include_extra:
        extra = selection("knowledge:extra", rank=2)
        assessments = (assessment(selected), assessment(extra))

    assert_request_invalid(request((selected,), assessments))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(value, evidence_index_ref=artifact("other-index")),
        lambda value: replace(value, source_snapshot_ref=artifact("other-source")),
        lambda value: replace(value, content_sha256="b" * 64),
    ],
)
def test_assessment_must_be_bound_to_selected_provenance(mutation) -> None:
    selected = selection()

    assert_request_invalid(request((selected,), (mutation(assessment(selected)),)))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(value, knowledge_chunk_ref="other-chunk"),
        lambda value: replace(value, source_version="mfds-synthetic@other"),
        lambda value: replace(value, locator="$.records.other"),
        lambda value: replace(value, canonicalization_spec_version="knowledge-text@other"),
    ],
)
def test_full_selection_provenance_remains_bound_to_assessment(
    mutation: Callable[[KnowledgeEvidenceProvenance], KnowledgeEvidenceProvenance],
) -> None:
    selected = selection()
    selected_assessment = assessment(selected)
    changed_provenance = mutation(selected.candidate.provenance)
    changed_selection = replace(
        selected,
        candidate=replace(selected.candidate, provenance=changed_provenance),
    )

    assert_request_invalid(request((changed_selection,), (selected_assessment,)))


def test_assessment_coverage_must_be_required_by_request() -> None:
    selected = selection()

    assert_request_invalid(request((selected,), (assessment(selected, coverage_key="unrequested-coverage"),)))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(value, minimum_supporting_items_per_coverage=2),
        lambda value: replace(value, minimum_distinct_source_snapshots_per_coverage=2),
        lambda value: replace(value, artifact_ref=replace(value.artifact_ref, content_sha256="f" * 64)),
    ],
)
def test_policy_values_must_remain_bound_to_policy_artifact(mutation) -> None:
    selected = selection()

    assert_request_invalid(request((selected,), (assessment(selected),), gate_policy=mutation(policy())))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(value, stance=EvidenceAssessmentStance.CONTRADICTS),
        lambda value: replace(value, coverage_key="medication-warning"),
        lambda value: replace(value, valid_until=NOW + timedelta(days=2)),
        lambda value: replace(value, eligibility_receipt_ref=artifact("other-eligibility")),
    ],
)
def test_assessment_values_must_remain_bound_to_assessment_artifact(mutation) -> None:
    selected = selection()

    assert_request_invalid(
        request(
            (selected,),
            (mutation(assessment(selected)),),
            required_coverage_keys=("medication-usage", "medication-warning"),
        )
    )


@pytest.mark.parametrize(
    "gate_policy",
    [
        policy(minimum_supporting_items_per_coverage=0),
        policy(minimum_distinct_source_snapshots_per_coverage=0),
    ],
)
def test_invalid_policy_thresholds_fail_closed(gate_policy: VersionedEvidenceGatePolicy) -> None:
    selected = selection()

    assert_request_invalid(request((selected,), (assessment(selected),), gate_policy=gate_policy))


@pytest.mark.parametrize(
    "bad_assessment",
    [
        lambda selected: replace(assessment(selected), valid_from=NOW.replace(tzinfo=None)),
        lambda selected: replace(assessment(selected), valid_until=NOW.replace(tzinfo=None)),
        lambda selected: replace(assessment(selected), valid_from=NOW, valid_until=NOW),
        lambda selected: replace(
            assessment(selected),
            eligibility_receipt_ref=ImmutableArtifactRef("receipt", "receipt@1", "not-a-hash"),
        ),
    ],
)
def test_malformed_freshness_or_receipt_input_fails_closed(bad_assessment) -> None:
    selected = selection()

    assert_request_invalid(request((selected,), (bad_assessment(selected),)))


def test_request_rejects_duplicate_required_coverage_keys() -> None:
    selected = selection()

    assert_request_invalid(
        request(
            (selected,),
            (assessment(selected),),
            required_coverage_keys=("medication-usage", "medication-usage"),
        )
    )


def test_request_rejects_non_tuple_container_subclasses() -> None:
    class StatefulTuple(tuple):
        pass

    selected = selection()
    gate_request = request((selected,), (assessment(selected),))

    assert_request_invalid(replace(gate_request, selections=StatefulTuple(gate_request.selections)))


def test_content_mutation_after_retrieval_fails_closed() -> None:
    selected = selection()
    changed_candidate = replace(selected.candidate, content_text=SensitiveText("변조된 원문"))

    assert_request_invalid(request((replace(selected, candidate=changed_candidate),), (assessment(selected),)))


@pytest.mark.parametrize("text", ["", "synthetic-e\N{COMBINING ACUTE ACCENT}"])
def test_empty_or_non_nfc_evidence_content_fails_closed(text: str) -> None:
    selected = selection(text=text)

    assert_request_invalid(request((selected,), (assessment(selected),)))


@pytest.mark.parametrize(
    "signal",
    [
        StageSignal(cast(EvidenceSearchStage, "NOT_A_STAGE"), 1, CanonicalScore("0.9")),
        StageSignal(EvidenceSearchStage.LEXICAL, 0, CanonicalScore("0.9")),
        StageSignal(EvidenceSearchStage.LEXICAL, cast(int, True), CanonicalScore("0.9")),
    ],
)
def test_malformed_stage_signal_fails_closed(signal: StageSignal) -> None:
    selected = selection()
    changed = replace(selected, candidate=replace(selected.candidate, stage_signals=(signal,)))

    assert_request_invalid(request((changed,), (assessment(changed),)))


def test_duplicate_stage_signal_fails_closed() -> None:
    selected = selection()
    signal = selected.candidate.stage_signals[0]
    changed = replace(
        selected,
        candidate=replace(selected.candidate, stage_signals=(signal, replace(signal, rank=2))),
    )

    assert_request_invalid(request((changed,), (assessment(changed),)))


def test_reversed_stage_signal_order_fails_closed() -> None:
    selected = selection()
    lexical = selected.candidate.stage_signals[0]
    dense = StageSignal(EvidenceSearchStage.DENSE, 1, CanonicalScore("0.8"))
    changed = replace(
        selected,
        candidate=replace(selected.candidate, stage_signals=(dense, lexical)),
    )

    assert_request_invalid(request((changed,), (assessment(changed),)))


def test_duplicate_stage_rank_across_selected_evidence_fails_closed() -> None:
    first = selection("knowledge:first", rank=1)
    second = selection("knowledge:second", rank=2)
    second_signal = replace(second.candidate.stage_signals[0], rank=1)
    second = replace(
        second,
        candidate=replace(second.candidate, stage_signals=(second_signal,)),
    )

    assert_request_invalid(
        request(
            (first, second),
            (assessment(first), assessment(second)),
        )
    )


def test_same_knowledge_chunk_cannot_be_counted_under_different_evidence_keys() -> None:
    first = selection("knowledge:first", rank=1)
    second = selection("knowledge:second", rank=2)
    duplicate_chunk = replace(
        second.candidate.provenance,
        knowledge_chunk_ref=first.candidate.provenance.knowledge_chunk_ref,
    )
    second = replace(second, candidate=replace(second.candidate, provenance=duplicate_chunk))

    assert_request_invalid(
        request(
            (first, second),
            (assessment(first), assessment(second)),
            gate_policy=policy(minimum_supporting_items_per_coverage=2),
        )
    )


def test_selections_from_different_evidence_indexes_fail_closed() -> None:
    first = selection("knowledge:first", rank=1)
    second = selection("knowledge:second", rank=2)
    second_provenance = replace(second.candidate.provenance, evidence_index_ref=artifact("other-index"))
    second = replace(second, candidate=replace(second.candidate, provenance=second_provenance))

    assert_request_invalid(
        request(
            (first, second),
            (assessment(first), assessment(second)),
        )
    )


def test_gate_result_is_deterministic_for_input_order() -> None:
    first = selection("knowledge:first", rank=1)
    second = selection("knowledge:second", rank=2)
    gate_retrieval_receipt = retrieval_receipt(selections=(first, second))
    first_assessment = assessment(first, gate_retrieval_receipt=gate_retrieval_receipt)
    second_assessment = assessment(second, gate_retrieval_receipt=gate_retrieval_receipt)

    forward = evaluate(
        request(
            (first, second),
            (first_assessment, second_assessment),
            gate_policy=policy(),
            gate_retrieval_receipt=gate_retrieval_receipt,
        )
    )
    reversed_input = evaluate(
        request(
            (second, first),
            (second_assessment, first_assessment),
            gate_policy=policy(),
            gate_retrieval_receipt=gate_retrieval_receipt,
        )
    )

    assert replace(reversed_input, gate_passed_selections=()) == replace(forward, gate_passed_selections=())
    assert tuple(
        (
            canonical_gate_selection_hash(item.selection),
            item.assessment_artifact_ref,
            item.eligibility_receipt_ref,
            item.verifier_artifact_ref,
        )
        for item in reversed_input.gate_passed_selections
    ) == tuple(
        (
            canonical_gate_selection_hash(item.selection),
            item.assessment_artifact_ref,
            item.eligibility_receipt_ref,
            item.verifier_artifact_ref,
        )
        for item in forward.gate_passed_selections
    )
    assert tuple(item.selection.candidate.provenance.evidence_key for item in forward.gate_passed_selections) == (
        "knowledge:first",
        "knowledge:second",
    )


def test_trace_does_not_expose_evidence_content_or_locator() -> None:
    secret = "SECRET-SYNTHETIC-EVIDENCE"
    selected = selection(text=secret)

    outcome = evaluate(request((selected,), (assessment(selected),)))

    assert outcome.trace is not None
    serialized_trace = json.dumps(asdict(outcome.trace), ensure_ascii=False, default=str)
    assert secret not in serialized_trace
    assert selected.candidate.provenance.locator not in serialized_trace


def test_evidence_valid_from_evaluation_time_is_current() -> None:
    selected = selection()

    outcome = evaluate(request((selected,), (assessment(selected, valid_from=NOW),)))

    assert outcome.execution_status is EvidenceGateExecutionStatus.SUCCEEDED
    assert outcome.evidence_status is EvidenceStatus.SUFFICIENT


def test_evidence_not_yet_valid_is_stale() -> None:
    selected = selection()

    outcome = evaluate(
        request(
            (selected,),
            (assessment(selected, valid_from=NOW + timedelta(seconds=1)),),
        )
    )

    assert outcome.execution_status is EvidenceGateExecutionStatus.NO_RESULT
    assert outcome.evidence_status is EvidenceStatus.STALE


def test_unverified_eligibility_receipt_cannot_produce_sufficient_evidence() -> None:
    selected = selection()

    outcome = evaluate_evidence_gate(
        request((selected,), (assessment(selected),)),
        eligibility_verifier=RejectingEligibilityVerifier(),
    )

    assert outcome.execution_status is EvidenceGateExecutionStatus.NO_RESULT
    assert outcome.evidence_status is EvidenceStatus.INSUFFICIENT
    assert outcome.reason is EvidenceGateReason.EVIDENCE_INELIGIBLE
    assert outcome.gate_passed_selections == ()


def test_mismatched_eligibility_success_receipt_fails_closed() -> None:
    selected = selection()

    outcome = evaluate_evidence_gate(
        request((selected,), (assessment(selected),)),
        eligibility_verifier=MismatchingEligibilityVerifier(),
    )

    assert outcome.execution_status is EvidenceGateExecutionStatus.DEPENDENCY_ERROR
    assert outcome.evidence_status is None
    assert outcome.reason is EvidenceGateReason.ELIGIBILITY_RECEIPT_MISMATCH
    assert outcome.gate_passed_selections == ()


def test_mismatched_retrieval_receipt_from_verifier_fails_closed() -> None:
    selected = selection()

    outcome = evaluate_evidence_gate(
        request((selected,), (assessment(selected),)),
        eligibility_verifier=MismatchingRetrievalReceiptVerifier(),
    )

    assert outcome.execution_status is EvidenceGateExecutionStatus.DEPENDENCY_ERROR
    assert outcome.evidence_status is None
    assert outcome.reason is EvidenceGateReason.RETRIEVAL_RECEIPT_MISMATCH
    assert outcome.gate_passed_selections == ()


def test_eligibility_verifier_exception_is_sanitized_dependency_error() -> None:
    selected = selection()

    outcome = evaluate_evidence_gate(
        request((selected,), (assessment(selected),)),
        eligibility_verifier=RaisingEligibilityVerifier(),
    )

    assert outcome.execution_status is EvidenceGateExecutionStatus.DEPENDENCY_ERROR
    assert outcome.evidence_status is None
    assert outcome.reason is EvidenceGateReason.ELIGIBILITY_VERIFICATION_ERROR
    assert "private-provider-detail" not in repr(outcome)


@pytest.mark.parametrize("mutation", ["stance", "freshness", "receipt", "nested-receipt"])
def test_eligibility_verifier_cannot_mutate_assessment_input(mutation: str) -> None:
    selected = selection()
    selected_assessment = assessment(selected, stance=EvidenceAssessmentStance.CONTRADICTS)

    outcome = evaluate_evidence_gate(
        request((selected,), (selected_assessment,)),
        eligibility_verifier=MutatingEligibilityVerifier(mutation),
    )

    assert outcome.execution_status is EvidenceGateExecutionStatus.DEPENDENCY_ERROR
    assert outcome.evidence_status is None
    assert outcome.reason is EvidenceGateReason.ELIGIBILITY_VERIFICATION_ERROR
    assert outcome.gate_passed_selections == ()


def test_eligibility_verifier_cannot_mutate_retrieval_receipt_input() -> None:
    selected = selection()

    outcome = evaluate_evidence_gate(
        request((selected,), (assessment(selected),)),
        eligibility_verifier=MutatingRetrievalReceiptVerifier(),
    )

    assert outcome.execution_status is EvidenceGateExecutionStatus.DEPENDENCY_ERROR
    assert outcome.evidence_status is None
    assert outcome.reason is EvidenceGateReason.ELIGIBILITY_VERIFICATION_ERROR
    assert outcome.gate_passed_selections == ()


def test_eligibility_snapshot_exception_is_sanitized_dependency_error() -> None:
    selected = selection()
    receipt = replace(
        artifact("eligibility-snapshot"),
        version=ExplodingDeepcopyStr("eligibility-snapshot@synthetic-1"),
    )
    selected_assessment = EvidenceGateAssessment.create(
        "evidence-assessment",
        "evidence-assessment@snapshot-failure",
        selection=selected,
        coverage_key="medication-usage",
        stance=EvidenceAssessmentStance.SUPPORTS,
        retrieval_receipt_ref=retrieval_receipt().artifact_ref,
        eligibility_receipt_ref=receipt,
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=1),
    )

    outcome = evaluate_evidence_gate(
        request((selected,), (selected_assessment,)),
        eligibility_verifier=SyntheticEligibilityVerifier(),
    )

    assert outcome.execution_status is EvidenceGateExecutionStatus.DEPENDENCY_ERROR
    assert outcome.evidence_status is None
    assert outcome.reason is EvidenceGateReason.ELIGIBILITY_VERIFICATION_ERROR
    assert "private-snapshot-detail" not in repr(outcome)
