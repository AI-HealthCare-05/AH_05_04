from __future__ import annotations

import asyncio
import importlib
from typing import cast

import pytest

from ai_worker.tasks.rag.citation_authorization import CitationAuthorizationBuildOutcome, CitationAuthorizationRequest
from ai_worker.tasks.rag.citation_authorization_authority import CitationAuthorityIssueOutcome
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.guide_citation_runtime_orchestration import (
    GuideCitationRuntimeDecision,
    GuideCitationRuntimeOrchestrationOutcome,
    GuideCitationRuntimeOrchestrationRequest,
    GuideCitationRuntimeStage,
)
from ai_worker.tasks.rag.guide_claim_citation_validation import GuideClaimCitationValidationOutcome
from ai_worker.tasks.rag.guide_generation_card_orchestration import (
    GuideGenerationCardDecision,
    GuideGenerationCardOutcome,
)
from ai_worker.tasks.rag.guide_orchestration import GuideOrchestrationOutcome
from ai_worker.tasks.rag.guideline_card import (
    GuidelineCard,
    GuidelineCardOutcome,
    GuidelineCardReason,
    GuidelineCardStatus,
    GuidelineFallbackCode,
    VerifiedGuidelineFallback,
)
from ai_worker.tests.rag.test_guide_release_projection import _authorized_selection, _card
from rag_runtime.guide_release_projection import (
    GuideRuntimeReleaseDecision,
    GuideRuntimeReleaseProjectionCarrier,
    GuideRuntimeReleaseProjectionUnavailable,
)


def _composition_module():
    return importlib.import_module("ai_worker.tasks.rag.guide_runtime_composition")


def _generated_outcome(card: GuidelineCard) -> GuideGenerationCardOutcome:
    return GuideGenerationCardOutcome(
        decision=GuideGenerationCardDecision.COMPLETED,
        stopped_stage=None,
        upstream_outcome=cast(GuideOrchestrationOutcome, object()),
        generation_result=None,
        authority_outcome=None,
        card_outcome=GuidelineCardOutcome(
            status=GuidelineCardStatus.GENERATED,
            reason=GuidelineCardReason.CARD_GENERATED,
            card=card,
        ),
    )


def _completed_outcome() -> GuideCitationRuntimeOrchestrationOutcome:
    card = _card()
    return GuideCitationRuntimeOrchestrationOutcome(
        decision=GuideCitationRuntimeDecision.COMPLETED,
        stopped_stage=None,
        generation_outcome=_generated_outcome(card),
        claim_citation_outcome=cast(GuideClaimCitationValidationOutcome, object()),
        authorization_build_outcome=cast(CitationAuthorizationBuildOutcome, object()),
        authorization_request=cast(CitationAuthorizationRequest, object()),
        authority_outcome=cast(CitationAuthorityIssueOutcome, object()),
        finalization_outcome=_authorized_selection(target_ref=card.artifact_ref.content_sha256),
    )


def _fallback_outcome(
    code: GuidelineFallbackCode,
    status: GuidelineCardStatus,
    reason: GuidelineCardReason,
) -> GuideCitationRuntimeOrchestrationOutcome:
    fallback = VerifiedGuidelineFallback(
        artifact_ref=ImmutableArtifactRef("guide-fallback", "v1", "a" * 64),
        code=code,
        text=SensitiveText("현재는 승인된 안내를 제공할 수 없습니다. 의사 또는 약사와 상담하세요."),
        approval_verifier_ref=ImmutableArtifactRef("guide-fallback-verifier", "v1", "b" * 64),
    )
    generation_outcome = GuideGenerationCardOutcome(
        decision=GuideGenerationCardDecision.COMPLETED,
        stopped_stage=None,
        upstream_outcome=cast(GuideOrchestrationOutcome, object()),
        generation_result=None,
        authority_outcome=None,
        card_outcome=GuidelineCardOutcome(
            status=status,
            reason=reason,
            fallback_code=code,
            fallback=fallback,
        ),
    )
    return GuideCitationRuntimeOrchestrationOutcome(
        decision=GuideCitationRuntimeDecision.STOPPED,
        stopped_stage=GuideCitationRuntimeStage.CLAIM_CITATION_VALIDATION,
        generation_outcome=generation_outcome,
        claim_citation_outcome=cast(GuideClaimCitationValidationOutcome, object()),
        authorization_build_outcome=None,
        authorization_request=None,
        authority_outcome=None,
        finalization_outcome=None,
    )


def _generation_stopped_outcome() -> GuideCitationRuntimeOrchestrationOutcome:
    return GuideCitationRuntimeOrchestrationOutcome(
        decision=GuideCitationRuntimeDecision.STOPPED,
        stopped_stage=GuideCitationRuntimeStage.GENERATION_CARD,
        generation_outcome=GuideGenerationCardOutcome(
            decision=GuideGenerationCardDecision.STOPPED,
            stopped_stage=None,
            upstream_outcome=cast(GuideOrchestrationOutcome, object()),
            generation_result=None,
            authority_outcome=None,
            card_outcome=None,
        ),
        claim_citation_outcome=None,
        authorization_build_outcome=None,
        authorization_request=None,
        authority_outcome=None,
        finalization_outcome=None,
    )


def _execute(monkeypatch: pytest.MonkeyPatch, citation_outcome: object):
    composition = _composition_module()
    calls: list[str] = []
    request = cast(GuideCitationRuntimeOrchestrationRequest, object())
    dependencies = {
        "generator": object(),
        "decision_verifier": object(),
        "guard_reader": object(),
        "request_authority_reader": object(),
        "pin_reader": object(),
        "approval_reader": object(),
        "eligibility_reader": object(),
        "store": object(),
    }
    original_finalize = composition.finalize_guide_runtime_release
    original_project = composition.project_guide_runtime_release

    async def orchestrate(request_arg, **dependency_args):
        calls.append("#890")
        assert request_arg is request
        assert dependency_args == dependencies
        return citation_outcome

    def finalize(outcome: object):
        calls.append("#893")
        return original_finalize(outcome)

    def project(result: object):
        calls.append("#906")
        return original_project(result)

    monkeypatch.setattr(composition, "orchestrate_guide_citation_runtime", orchestrate)
    monkeypatch.setattr(composition, "finalize_guide_runtime_release", finalize)
    monkeypatch.setattr(composition, "project_guide_runtime_release", project)

    projection = asyncio.run(composition.execute_guide_runtime_release(request, **dependencies))
    return projection, calls


def test_canonical_composition_projects_one_authorized_runtime_outcome_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, calls = _execute(monkeypatch, _completed_outcome())

    assert calls == ["#890", "#893", "#906"]
    assert isinstance(projection, GuideRuntimeReleaseProjectionCarrier)
    assert projection.release_decision is GuideRuntimeReleaseDecision.PASS
    assert projection.answer is not None
    assert projection.citations
    assert projection.fallback is None


@pytest.mark.parametrize(
    ("code", "status", "reason", "decision"),
    (
        (
            GuidelineFallbackCode.NO_APPROVED_EVIDENCE,
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.EVIDENCE_INSUFFICIENT,
            GuideRuntimeReleaseDecision.REJECTED,
        ),
        (
            GuidelineFallbackCode.PROVIDER_TIMEOUT,
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.PROVIDER_TIMEOUT,
            GuideRuntimeReleaseDecision.REJECTED,
        ),
        (
            GuidelineFallbackCode.UNSUPPORTED_REQUEST,
            GuidelineCardStatus.LIMITED,
            GuidelineCardReason.UNSUPPORTED_REQUEST,
            GuideRuntimeReleaseDecision.LIMITED,
        ),
    ),
)
def test_canonical_composition_preserves_approved_fallback_projection(
    monkeypatch: pytest.MonkeyPatch,
    code: GuidelineFallbackCode,
    status: GuidelineCardStatus,
    reason: GuidelineCardReason,
    decision: GuideRuntimeReleaseDecision,
) -> None:
    projection, calls = _execute(monkeypatch, _fallback_outcome(code, status, reason))

    assert calls == ["#890", "#893", "#906"]
    assert isinstance(projection, GuideRuntimeReleaseProjectionCarrier)
    assert projection.release_decision is decision
    assert projection.answer is None
    assert projection.citations == ()
    assert projection.fallback is not None


@pytest.mark.parametrize(
    ("code", "reason"),
    (
        (GuidelineFallbackCode.PRESCRIPTION_STALE, GuidelineCardReason.PRESCRIPTION_STALE),
        (GuidelineFallbackCode.EXECUTION_CONTEXT_STALE, GuidelineCardReason.EXECUTION_CONTEXT_STALE),
    ),
)
def test_canonical_composition_preserves_stale_projection(
    monkeypatch: pytest.MonkeyPatch,
    code: GuidelineFallbackCode,
    reason: GuidelineCardReason,
) -> None:
    projection, calls = _execute(monkeypatch, _fallback_outcome(code, GuidelineCardStatus.STALE, reason))

    assert calls == ["#890", "#893", "#906"]
    assert isinstance(projection, GuideRuntimeReleaseProjectionCarrier)
    assert projection.release_decision is GuideRuntimeReleaseDecision.STALE
    assert projection.is_current is False
    assert projection.answer is None
    assert projection.citations == ()


def test_canonical_composition_fails_closed_for_malformed_runtime_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, calls = _execute(monkeypatch, object())

    assert calls == ["#890", "#893", "#906"]
    assert isinstance(projection, GuideRuntimeReleaseProjectionUnavailable)


def test_canonical_composition_does_not_reinterpret_early_runtime_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, calls = _execute(monkeypatch, _generation_stopped_outcome())

    assert calls == ["#890", "#893", "#906"]
    assert isinstance(projection, GuideRuntimeReleaseProjectionUnavailable)
