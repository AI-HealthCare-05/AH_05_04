from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from ai_worker.tasks.rag.citation_authorization import (
    CitationAuthorizationBuildOutcome,
    CitationAuthorizationRequest,
)
from ai_worker.tasks.rag.citation_authorization_authority import CitationAuthorityIssueOutcome
from ai_worker.tasks.rag.citation_finalizer import (
    AuthorizedCitationSelection,
    DiscardGeneratedContent,
    FinalizationStage,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.guide_citation_runtime_orchestration import (
    GuideCitationRuntimeDecision,
    GuideCitationRuntimeOrchestrationOutcome,
    GuideCitationRuntimeStage,
)
from ai_worker.tasks.rag.guide_claim_citation_validation import GuideClaimCitationValidationOutcome
from ai_worker.tasks.rag.guide_generation_card_orchestration import (
    GuideGenerationCardDecision,
    GuideGenerationCardOutcome,
)
from ai_worker.tasks.rag.guide_orchestration import GuideOrchestrationOutcome
from ai_worker.tasks.rag.guide_runtime_release import (
    GuideRuntimeEvidenceStatus,
    GuideRuntimeExecutionStatus,
    GuideRuntimeReleaseDecision,
    finalize_guide_runtime_release,
)
from ai_worker.tasks.rag.guideline_card import (
    GuidelineCard,
    GuidelineCardOutcome,
    GuidelineCardReason,
    GuidelineCardStatus,
    GuidelineFallbackCode,
    VerifiedGuidelineFallback,
)
from ai_worker.tests.rag.test_citation_authorization import _built_request, _passing_receipt


class _SensitiveSentinel:
    def __repr__(self) -> str:
        return "SYNTHETIC_DISCARDED_MEDICAL_TEXT"


def _generated_card_outcome(card: object | None = None) -> GuidelineCardOutcome:
    return GuidelineCardOutcome(
        status=GuidelineCardStatus.GENERATED,
        reason=GuidelineCardReason.CARD_GENERATED,
        card=cast(GuidelineCard, card if card is not None else object()),
    )


def _verified_fallback(code: GuidelineFallbackCode) -> VerifiedGuidelineFallback:
    return VerifiedGuidelineFallback(
        artifact_ref=ImmutableArtifactRef("guide-fallback", "v1", "a" * 64),
        code=code,
        text=SensitiveText("현재는 승인된 안내를 제공할 수 없습니다. 의사 또는 약사와 상담하세요."),
        approval_verifier_ref=ImmutableArtifactRef("guide-fallback-verifier", "v1", "b" * 64),
    )


def _fallback_card_outcome(
    code: GuidelineFallbackCode,
    status: GuidelineCardStatus,
    reason: GuidelineCardReason,
) -> GuidelineCardOutcome:
    return GuidelineCardOutcome(
        status=status,
        reason=reason,
        fallback_code=code,
        fallback=_verified_fallback(code),
    )


def _generation(card_outcome: GuidelineCardOutcome) -> GuideGenerationCardOutcome:
    return GuideGenerationCardOutcome(
        decision=GuideGenerationCardDecision.COMPLETED,
        stopped_stage=None,
        upstream_outcome=cast(GuideOrchestrationOutcome, object()),
        generation_result=None,
        authority_outcome=None,
        card_outcome=card_outcome,
    )


def _authorized() -> AuthorizedCitationSelection:
    selection, request = _built_request()
    return AuthorizedCitationSelection(selection, _passing_receipt(request))


def _completed(
    finalization: AuthorizedCitationSelection | DiscardGeneratedContent,
    *,
    card_outcome: GuidelineCardOutcome | None = None,
) -> GuideCitationRuntimeOrchestrationOutcome:
    return GuideCitationRuntimeOrchestrationOutcome(
        decision=GuideCitationRuntimeDecision.COMPLETED,
        stopped_stage=None,
        generation_outcome=_generation(card_outcome or _generated_card_outcome()),
        claim_citation_outcome=cast(GuideClaimCitationValidationOutcome, object()),
        authorization_build_outcome=cast(CitationAuthorizationBuildOutcome, object()),
        authorization_request=cast(CitationAuthorizationRequest, object()),
        authority_outcome=cast(CitationAuthorityIssueOutcome, object()),
        finalization_outcome=finalization,
    )


def _stopped_with_card(
    card_outcome: GuidelineCardOutcome,
    *,
    stopped_stage: GuideCitationRuntimeStage = GuideCitationRuntimeStage.CLAIM_CITATION_VALIDATION,
) -> GuideCitationRuntimeOrchestrationOutcome:
    return GuideCitationRuntimeOrchestrationOutcome(
        decision=GuideCitationRuntimeDecision.STOPPED,
        stopped_stage=stopped_stage,
        generation_outcome=_generation(card_outcome),
        claim_citation_outcome=cast(GuideClaimCitationValidationOutcome, object()),
        authorization_build_outcome=None,
        authorization_request=None,
        authority_outcome=None,
        finalization_outcome=None,
    )


def test_completed_authorized_selection_is_the_only_pass_projection() -> None:
    authorized = _authorized()

    result = finalize_guide_runtime_release(_completed(authorized))

    assert result.execution_status is GuideRuntimeExecutionStatus.SUCCEEDED
    assert result.evidence_status is GuideRuntimeEvidenceStatus.SUFFICIENT
    assert result.release_decision is GuideRuntimeReleaseDecision.PASS
    assert result.is_current is True
    assert result.fallback_code is None
    assert result.authorized_selection is authorized
    assert result.card_outcome is not None


@pytest.mark.parametrize(
    ("reason", "code"),
    (
        ("SELECTION_NOT_AUTHORIZED", GuidelineFallbackCode.NO_APPROVED_EVIDENCE),
        ("AUTHORIZATION_RECEIPT_REQUIRED", GuidelineFallbackCode.VALIDATION_FAILED),
    ),
)
def test_citation_failure_discards_generated_content_without_inventing_a_public_fallback(
    reason: str,
    code: GuidelineFallbackCode,
) -> None:
    discarded = DiscardGeneratedContent(FinalizationStage.CITATION_AUTHORIZATION, (reason,))

    result = finalize_guide_runtime_release(_completed(discarded))

    assert result.execution_status is GuideRuntimeExecutionStatus.VALIDATION_ERROR
    assert result.release_decision is GuideRuntimeReleaseDecision.REJECTED
    assert result.fallback_code is None
    assert result.fallback_code is not code
    assert result.authorized_selection is None
    assert result.card_outcome is None
    assert not hasattr(result, "reasons")


@pytest.mark.parametrize(
    ("code", "status", "reason", "execution", "evidence", "release", "is_current"),
    (
        (
            GuidelineFallbackCode.NO_APPROVED_EVIDENCE,
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.EVIDENCE_INSUFFICIENT,
            GuideRuntimeExecutionStatus.NO_RESULT,
            GuideRuntimeEvidenceStatus.INSUFFICIENT,
            GuideRuntimeReleaseDecision.REJECTED,
            True,
        ),
        (
            GuidelineFallbackCode.CONFLICTING_EVIDENCE,
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.EVIDENCE_CONFLICTED,
            GuideRuntimeExecutionStatus.NO_RESULT,
            GuideRuntimeEvidenceStatus.CONFLICTED,
            GuideRuntimeReleaseDecision.REJECTED,
            True,
        ),
        (
            GuidelineFallbackCode.PROVIDER_TIMEOUT,
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.PROVIDER_TIMEOUT,
            GuideRuntimeExecutionStatus.TIMED_OUT,
            None,
            GuideRuntimeReleaseDecision.REJECTED,
            True,
        ),
        (
            GuidelineFallbackCode.DEPENDENCY_UNAVAILABLE,
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.DEPENDENCY_UNAVAILABLE,
            GuideRuntimeExecutionStatus.DEPENDENCY_ERROR,
            None,
            GuideRuntimeReleaseDecision.REJECTED,
            True,
        ),
        (
            GuidelineFallbackCode.VALIDATION_FAILED,
            GuidelineCardStatus.VALIDATION_REJECTED,
            GuidelineCardReason.VALIDATION_FAILED,
            GuideRuntimeExecutionStatus.VALIDATION_ERROR,
            None,
            GuideRuntimeReleaseDecision.REJECTED,
            True,
        ),
        (
            GuidelineFallbackCode.PRESCRIPTION_STALE,
            GuidelineCardStatus.STALE,
            GuidelineCardReason.PRESCRIPTION_STALE,
            None,
            None,
            GuideRuntimeReleaseDecision.STALE,
            False,
        ),
        (
            GuidelineFallbackCode.EXECUTION_CONTEXT_STALE,
            GuidelineCardStatus.STALE,
            GuidelineCardReason.EXECUTION_CONTEXT_STALE,
            None,
            None,
            GuideRuntimeReleaseDecision.STALE,
            False,
        ),
    ),
)
def test_approved_card_fallbacks_project_only_exact_safety_result_vocabulary(
    code: GuidelineFallbackCode,
    status: GuidelineCardStatus,
    reason: GuidelineCardReason,
    execution: GuideRuntimeExecutionStatus | None,
    evidence: GuideRuntimeEvidenceStatus | None,
    release: GuideRuntimeReleaseDecision,
    is_current: bool,
) -> None:
    card_outcome = _fallback_card_outcome(code, status, reason)

    result = finalize_guide_runtime_release(_stopped_with_card(card_outcome))

    assert result.execution_status is execution
    assert result.evidence_status is evidence
    assert result.release_decision is release
    assert result.is_current is is_current
    assert result.fallback_code is code
    assert result.card_outcome is card_outcome
    assert result.authorized_selection is None


def test_stale_source_evidence_is_not_mislabeled_as_execution_context_stale() -> None:
    card_outcome = _fallback_card_outcome(
        GuidelineFallbackCode.NO_APPROVED_EVIDENCE,
        GuidelineCardStatus.NO_RESULT,
        GuidelineCardReason.EVIDENCE_STALE,
    )

    result = finalize_guide_runtime_release(_stopped_with_card(card_outcome))

    assert result.execution_status is GuideRuntimeExecutionStatus.NO_RESULT
    assert result.evidence_status is GuideRuntimeEvidenceStatus.STALE
    assert result.release_decision is GuideRuntimeReleaseDecision.REJECTED
    assert result.is_current is True
    assert result.fallback_code is GuidelineFallbackCode.NO_APPROVED_EVIDENCE


def test_request_guard_stop_with_no_approved_fallback_never_fakes_pass() -> None:
    outcome = _stopped_with_card(
        _generated_card_outcome(),
        stopped_stage=GuideCitationRuntimeStage.REQUEST_GUARD_RUNTIME_BINDING,
    )

    result = finalize_guide_runtime_release(outcome)

    assert result.execution_status is GuideRuntimeExecutionStatus.VALIDATION_ERROR
    assert result.release_decision is GuideRuntimeReleaseDecision.REJECTED
    assert result.authorized_selection is None
    assert result.card_outcome is None


def test_malformed_or_internally_inconsistent_outcome_fails_closed() -> None:
    malformed = replace(
        _completed(_authorized()),
        stopped_stage=GuideCitationRuntimeStage.CITATION_AUTHORIZATION_REQUEST,
    )

    result = finalize_guide_runtime_release(malformed)

    assert result.execution_status is GuideRuntimeExecutionStatus.VALIDATION_ERROR
    assert result.release_decision is GuideRuntimeReleaseDecision.REJECTED
    assert result.fallback_code is None
    assert result.authorized_selection is None
    assert result.card_outcome is None


def test_discarded_generated_text_is_absent_from_result_repr() -> None:
    discarded = DiscardGeneratedContent(
        FinalizationStage.CITATION_AUTHORIZATION,
        ("SELECTION_NOT_AUTHORIZED",),
    )
    outcome = _completed(discarded, card_outcome=_generated_card_outcome(_SensitiveSentinel()))

    result = finalize_guide_runtime_release(outcome)

    assert "SYNTHETIC_DISCARDED_MEDICAL_TEXT" not in repr(result)


def test_inconsistent_approved_fallback_carrier_fails_closed() -> None:
    card_outcome = replace(
        _fallback_card_outcome(
            GuidelineFallbackCode.PROVIDER_TIMEOUT,
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.PROVIDER_TIMEOUT,
        ),
        fallback=_verified_fallback(GuidelineFallbackCode.VALIDATION_FAILED),
    )

    result = finalize_guide_runtime_release(_stopped_with_card(card_outcome))

    assert result.execution_status is GuideRuntimeExecutionStatus.VALIDATION_ERROR
    assert result.release_decision is GuideRuntimeReleaseDecision.REJECTED
    assert result.fallback_code is None
    assert result.card_outcome is None


def test_unsupported_request_preserves_approved_limited_fallback() -> None:
    card_outcome = _fallback_card_outcome(
        GuidelineFallbackCode.UNSUPPORTED_REQUEST,
        GuidelineCardStatus.LIMITED,
        GuidelineCardReason.UNSUPPORTED_REQUEST,
    )

    result = finalize_guide_runtime_release(_stopped_with_card(card_outcome))

    assert result.execution_status is GuideRuntimeExecutionStatus.SUCCEEDED
    assert result.release_decision is GuideRuntimeReleaseDecision.LIMITED
    assert result.is_current is True
    assert result.fallback_code is GuidelineFallbackCode.UNSUPPORTED_REQUEST


def test_result_type_rejects_pass_without_authorized_selection() -> None:
    result = finalize_guide_runtime_release(_completed(_authorized()))

    with pytest.raises(ValueError, match="invalid Guide runtime release result"):
        replace(result, authorized_selection=None)


def test_result_type_rejects_current_stale_result() -> None:
    card_outcome = _fallback_card_outcome(
        GuidelineFallbackCode.EXECUTION_CONTEXT_STALE,
        GuidelineCardStatus.STALE,
        GuidelineCardReason.EXECUTION_CONTEXT_STALE,
    )
    result = finalize_guide_runtime_release(_stopped_with_card(card_outcome))

    with pytest.raises(ValueError, match="invalid Guide runtime release result"):
        replace(result, is_current=True)


def test_result_type_rejects_non_allowlisted_fallback_code() -> None:
    result = finalize_guide_runtime_release(
        _stopped_with_card(
            _fallback_card_outcome(
                GuidelineFallbackCode.VALIDATION_FAILED,
                GuidelineCardStatus.VALIDATION_REJECTED,
                GuidelineCardReason.VALIDATION_FAILED,
            )
        )
    )

    with pytest.raises(ValueError, match="invalid Guide runtime release result"):
        replace(result, fallback_code=cast(GuidelineFallbackCode, "AUTHORIZATION_RECEIPT_REQUIRED"))
