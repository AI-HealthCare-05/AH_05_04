from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

import pytest

from ai_worker.tasks.rag.citation_finalizer import AuthorizedCitationSelection
from ai_worker.tasks.rag.claim_citation_validator import (
    CitationCandidate,
    CitationSourceType,
    ClaimCitationCandidateSet,
    ClaimTargetRef,
    GenerationProvenance,
    LifestyleGuidelineEvidenceRef,
    SourceExecutionProvenance,
    ValidatedCitationSelection,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.guide_release_projection import (
    _FALLBACK_CODE_MAP,
    project_guide_runtime_release,
)
from ai_worker.tasks.rag.guide_runtime_release import (
    GuideRuntimeEvidenceStatus,
    GuideRuntimeExecutionStatus,
    GuideRuntimeReleaseDecision,
    GuideRuntimeReleaseResult,
)
from ai_worker.tasks.rag.guideline_card import (
    GuidelineActionClass,
    GuidelineCard,
    GuidelineCardOutcome,
    GuidelineCardProvenance,
    GuidelineCardReason,
    GuidelineCardStatus,
    GuidelineClaim,
    GuidelineFallbackCode,
    GuidelineScope,
    MedicationIdentityRef,
    VerifiedGuidelineFallback,
)
from ai_worker.tasks.rag.source_member_identity import SourceMemberKind
from rag_runtime.guide_release_projection import (
    GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
    GuideRuntimeFallbackCode,
    GuideRuntimeReleaseProjectionCarrier,
    GuideRuntimeReleaseProjectionUnavailable,
)
from rag_runtime.guide_release_projection import (
    GuideRuntimeReleaseDecision as SharedReleaseDecision,
)


def _artifact(code: str, digest: str) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(code, "v1", digest)


def _card(*, target_ref: str = "c" * 64) -> GuidelineCard:
    claim = GuidelineClaim(
        claim_key="claim-food",
        medication_identity=MedicationIdentityRef("prescription-medication", "MFDS", "A01"),
        scope=GuidelineScope.FOOD_CAUTION,
        action_class=GuidelineActionClass.FOOD_AVOIDANCE,
        action_text=SensitiveText("승인된 식이 안내입니다."),
        citations=(),
    )
    return GuidelineCard(
        artifact_ref=_artifact("guide-card", target_ref),
        claims=(claim,),
        uncertainty_text=SensitiveText("승인된 근거 범위 밖의 내용은 확인할 수 없습니다."),
        consultation_text=SensitiveText("불편하거나 궁금한 점은 의사 또는 약사와 상담하세요."),
        provenance=GuidelineCardProvenance(
            prompt_ref=_artifact("prompt", "a" * 64),
            model_ref=_artifact("model", "b" * 64),
            parser_ref=_artifact("parser", "c" * 64),
            validator_ref=_artifact("validator", "d" * 64),
            guideline_policy_ref=_artifact("policy", "e" * 64),
            guideline_policy_verifier_ref=_artifact("policy-verifier", "f" * 64),
        ),
        evaluated_at=datetime.now(UTC),
    )


def _authorized_selection(
    *,
    target_ref: str = "c" * 64,
    include_second_citation: bool = False,
) -> AuthorizedCitationSelection:
    snapshot_id = UUID("11111111-1111-1111-1111-111111111111")
    member_id = UUID("22222222-2222-2222-2222-222222222222")
    evidence_ref = LifestyleGuidelineEvidenceRef(
        guideline_evidence_ref=f"{snapshot_id}/{member_id}/evidence-food",
        guideline_artifact_ref=_artifact("binding", "a" * 64),
        source_version="2026-09-20",
        locator="section-1",
        content_sha256="b" * 64,
        execution_provenance=SourceExecutionProvenance(
            source_code="MFDS",
            source_version="2026-09-20",
            member_kind=SourceMemberKind.ARTIFACT_MEMBER,
            endpoint_code=None,
            operation_code=None,
            artifact_code="guideline",
            artifact_version="v1",
            request_source_decision_ref=_artifact("source-decision", "c" * 64),
            request_member_decision_ref=_artifact("member-decision", "d" * 64),
        ),
    )
    citations: tuple[CitationCandidate, ...] = (
        CitationCandidate(
            citation_key="claim-food:evidence-food",
            claim_key="claim-food",
            source_type=CitationSourceType.LIFESTYLE_GUIDELINE,
            evidence_ref=evidence_ref,
            display_order=2 if include_second_citation else 1,
        ),
    )
    if include_second_citation:
        second_evidence_ref = LifestyleGuidelineEvidenceRef(
            guideline_evidence_ref=f"{snapshot_id}/{member_id}/evidence-water",
            guideline_artifact_ref=_artifact("binding", "a" * 64),
            source_version="2026-09-20",
            locator="section-2",
            content_sha256="d" * 64,
            execution_provenance=evidence_ref.execution_provenance,
        )
        citations += (
            CitationCandidate(
                citation_key="claim-water:evidence-water",
                claim_key="claim-water",
                source_type=CitationSourceType.LIFESTYLE_GUIDELINE,
                evidence_ref=second_evidence_ref,
                display_order=1,
            ),
        )
    candidate_set = ClaimCitationCandidateSet(
        target=ClaimTargetRef("GUIDE_CARD", target_ref),
        claims=(),
        citations=citations,
        generation_provenance=GenerationProvenance(
            prompt_ref=_artifact("prompt", "e" * 64),
            model_ref=_artifact("model", "f" * 64),
            parser_ref=_artifact("parser", "0" * 64),
        ),
        validator_policy_ref=_artifact("validator-policy", "1" * 64),
    )
    return AuthorizedCitationSelection(
        validated_selection=ValidatedCitationSelection(candidate_set, (), "2" * 64),
        authorization_receipt=object(),  # type: ignore[arg-type]
    )


def _pass_result(
    *,
    selection_target_ref: str = "c" * 64,
    include_second_citation: bool = False,
) -> GuideRuntimeReleaseResult:
    card = _card()
    return GuideRuntimeReleaseResult(
        execution_status=GuideRuntimeExecutionStatus.SUCCEEDED,
        evidence_status=GuideRuntimeEvidenceStatus.SUFFICIENT,
        release_decision=GuideRuntimeReleaseDecision.PASS,
        is_current=True,
        fallback_code=None,
        authorized_selection=_authorized_selection(
            target_ref=selection_target_ref,
            include_second_citation=include_second_citation,
        ),
        card_outcome=GuidelineCardOutcome(
            status=GuidelineCardStatus.GENERATED,
            reason=GuidelineCardReason.CARD_GENERATED,
            card=card,
        ),
    )


def _fallback_result(
    *,
    code: GuidelineFallbackCode,
    status: GuidelineCardStatus,
    reason: GuidelineCardReason,
    decision: GuideRuntimeReleaseDecision,
    execution: GuideRuntimeExecutionStatus | None,
    evidence: GuideRuntimeEvidenceStatus | None,
    is_current: bool,
) -> GuideRuntimeReleaseResult:
    fallback = VerifiedGuidelineFallback(
        artifact_ref=_artifact("fallback", "3" * 64),
        code=code,
        text=SensitiveText("현재는 승인된 안내를 제공할 수 없습니다. 의사 또는 약사와 상담하세요."),
        approval_verifier_ref=_artifact("fallback-verifier", "4" * 64),
    )
    return GuideRuntimeReleaseResult(
        execution_status=execution,
        evidence_status=evidence,
        release_decision=decision,
        is_current=is_current,
        fallback_code=code,
        authorized_selection=None,
        card_outcome=GuidelineCardOutcome(status=status, reason=reason, fallback_code=code, fallback=fallback),
    )


def test_valid_pass_projects_lossless_card_text_and_authorized_citation_order() -> None:
    projection = project_guide_runtime_release(_pass_result(include_second_citation=True))

    assert isinstance(projection, GuideRuntimeReleaseProjectionCarrier)
    assert projection.contract_version == GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION
    assert projection.release_decision is SharedReleaseDecision.PASS
    assert projection.answer is not None
    assert projection.answer.claim_action_texts == ("승인된 식이 안내입니다.",)
    assert projection.answer.uncertainty_text == "승인된 근거 범위 밖의 내용은 확인할 수 없습니다."
    assert projection.answer.consultation_text == "불편하거나 궁금한 점은 의사 또는 약사와 상담하세요."
    assert projection.fallback is None
    assert tuple(citation.evidence_key for citation in projection.citations) == ("evidence-food", "evidence-water")
    assert tuple(citation.display_order for citation in projection.citations) == (2, 1)
    citation = projection.citations[0]
    assert citation.card_target_ref == "c" * 64
    assert citation.claim_key == "claim-food"
    assert citation.evidence_key == "evidence-food"
    assert citation.source_snapshot_id == UUID("11111111-1111-1111-1111-111111111111")
    assert citation.source_snapshot_member_id == UUID("22222222-2222-2222-2222-222222222222")
    assert citation.source_code == "MFDS"
    assert citation.source_version == "2026-09-20"
    assert citation.locator == "section-1"
    assert citation.content_sha256 == "b" * 64
    assert citation.display_order == 2


def test_card_target_anchor_mismatch_returns_content_free_unavailable() -> None:
    projection = project_guide_runtime_release(_pass_result(selection_target_ref="f" * 64))

    assert isinstance(projection, GuideRuntimeReleaseProjectionUnavailable)
    assert asdict(projection) == {"contract_version": GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION}


@pytest.mark.parametrize("corruption", ("source_type", "citations"))
def test_corrupt_nested_citation_state_returns_content_free_unavailable(corruption: str) -> None:
    result = _pass_result()
    assert result.authorized_selection is not None
    candidate_set = result.authorized_selection.validated_selection.candidate_set
    if corruption == "source_type":
        object.__setattr__(candidate_set.citations[0], "source_type", object())
    else:
        object.__setattr__(candidate_set, "citations", list(candidate_set.citations))

    projection = project_guide_runtime_release(result)

    assert isinstance(projection, GuideRuntimeReleaseProjectionUnavailable)
    assert asdict(projection) == {"contract_version": GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION}


@pytest.mark.parametrize(
    ("result", "decision", "is_current", "fallback_code"),
    (
        (
            _fallback_result(
                code=GuidelineFallbackCode.UNSUPPORTED_REQUEST,
                status=GuidelineCardStatus.LIMITED,
                reason=GuidelineCardReason.UNSUPPORTED_REQUEST,
                decision=GuideRuntimeReleaseDecision.LIMITED,
                execution=GuideRuntimeExecutionStatus.SUCCEEDED,
                evidence=None,
                is_current=True,
            ),
            SharedReleaseDecision.LIMITED,
            True,
            GuideRuntimeFallbackCode.UNSUPPORTED_REQUEST,
        ),
        (
            _fallback_result(
                code=GuidelineFallbackCode.NO_APPROVED_EVIDENCE,
                status=GuidelineCardStatus.NO_RESULT,
                reason=GuidelineCardReason.EVIDENCE_INSUFFICIENT,
                decision=GuideRuntimeReleaseDecision.REJECTED,
                execution=GuideRuntimeExecutionStatus.NO_RESULT,
                evidence=GuideRuntimeEvidenceStatus.INSUFFICIENT,
                is_current=True,
            ),
            SharedReleaseDecision.REJECTED,
            True,
            GuideRuntimeFallbackCode.NO_APPROVED_EVIDENCE,
        ),
        (
            _fallback_result(
                code=GuidelineFallbackCode.PRESCRIPTION_STALE,
                status=GuidelineCardStatus.STALE,
                reason=GuidelineCardReason.PRESCRIPTION_STALE,
                decision=GuideRuntimeReleaseDecision.STALE,
                execution=None,
                evidence=None,
                is_current=False,
            ),
            SharedReleaseDecision.STALE,
            False,
            GuideRuntimeFallbackCode.PRESCRIPTION_STALE,
        ),
    ),
)
def test_approved_fallback_results_project_only_existing_safe_fields(
    result: GuideRuntimeReleaseResult,
    decision: SharedReleaseDecision,
    is_current: bool,
    fallback_code: GuideRuntimeFallbackCode,
) -> None:
    projection = project_guide_runtime_release(result)

    assert isinstance(projection, GuideRuntimeReleaseProjectionCarrier)
    assert projection.release_decision is decision
    assert projection.is_current is is_current
    assert projection.answer is None
    assert projection.citations == ()
    assert projection.fallback is not None
    assert projection.fallback.code is fallback_code


def test_wrong_input_and_corrupt_fallback_shape_return_unavailable() -> None:
    assert isinstance(project_guide_runtime_release(object()), GuideRuntimeReleaseProjectionUnavailable)

    result = _fallback_result(
        code=GuidelineFallbackCode.NO_APPROVED_EVIDENCE,
        status=GuidelineCardStatus.NO_RESULT,
        reason=GuidelineCardReason.EVIDENCE_INSUFFICIENT,
        decision=GuideRuntimeReleaseDecision.REJECTED,
        execution=GuideRuntimeExecutionStatus.NO_RESULT,
        evidence=GuideRuntimeEvidenceStatus.INSUFFICIENT,
        is_current=True,
    )
    object.__setattr__(result, "card_outcome", None)

    assert isinstance(project_guide_runtime_release(result), GuideRuntimeReleaseProjectionUnavailable)


def test_corrupt_release_decision_is_not_reinterpreted_as_a_different_fallback() -> None:
    result = _fallback_result(
        code=GuidelineFallbackCode.NO_APPROVED_EVIDENCE,
        status=GuidelineCardStatus.NO_RESULT,
        reason=GuidelineCardReason.EVIDENCE_INSUFFICIENT,
        decision=GuideRuntimeReleaseDecision.REJECTED,
        execution=GuideRuntimeExecutionStatus.NO_RESULT,
        evidence=GuideRuntimeEvidenceStatus.INSUFFICIENT,
        is_current=True,
    )
    object.__setattr__(result, "release_decision", GuideRuntimeReleaseDecision.LIMITED)

    assert isinstance(project_guide_runtime_release(result), GuideRuntimeReleaseProjectionUnavailable)


def test_missing_shared_fallback_mapping_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _fallback_result(
        code=GuidelineFallbackCode.NO_APPROVED_EVIDENCE,
        status=GuidelineCardStatus.NO_RESULT,
        reason=GuidelineCardReason.EVIDENCE_INSUFFICIENT,
        decision=GuideRuntimeReleaseDecision.REJECTED,
        execution=GuideRuntimeExecutionStatus.NO_RESULT,
        evidence=GuideRuntimeEvidenceStatus.INSUFFICIENT,
        is_current=True,
    )
    monkeypatch.delitem(_FALLBACK_CODE_MAP, GuidelineFallbackCode.NO_APPROVED_EVIDENCE)

    assert isinstance(project_guide_runtime_release(result), GuideRuntimeReleaseProjectionUnavailable)


def test_adapter_fallback_mapping_covers_the_worker_vocabulary() -> None:
    assert set(_FALLBACK_CODE_MAP) == set(GuidelineFallbackCode)
