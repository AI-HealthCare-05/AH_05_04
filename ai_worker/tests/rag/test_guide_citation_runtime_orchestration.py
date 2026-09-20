from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import cast

import pytest

from ai_worker.tasks.rag import guide_citation_runtime_orchestration as runtime_orchestration
from ai_worker.tasks.rag.citation_authorization import (
    AuthorizationBuildDecision,
    AuthorizationReason,
    CitationAuthorizationBuildOutcome,
    GuardDecision,
)
from ai_worker.tasks.rag.citation_authorization_authority import (
    CitationAuthorityIssueDecision,
    CitationAuthorityIssueOutcome,
    CitationAuthorityIssueReason,
)
from ai_worker.tasks.rag.citation_finalizer import AuthorizedCitationSelection, DiscardGeneratedContent
from ai_worker.tasks.rag.guide_claim_citation_validation import (
    GuideClaimCitationDecision,
    GuideClaimCitationStage,
    GuideClaimCitationValidationOutcome,
)
from ai_worker.tasks.rag.guide_generation_card_orchestration import (
    GuideGenerationCardDecision,
    GuideGenerationCardOrchestrationRequest,
    GuideGenerationCardOutcome,
    GuideGenerationCardStage,
)
from ai_worker.tasks.rag.guide_orchestration import GuideOrchestrationOutcome
from ai_worker.tasks.rag.request_guard_runtime_binding import (
    build_origin_request_guard_binding,
    build_runtime_authorization_binding,
)
from ai_worker.tests.rag.test_citation_authorization import _passing_receipt, _validated_selection
from ai_worker.tests.rag.test_citation_authorization_authority_issuer import (
    NOW,
    _ApprovalReader,
    _AuthorityReader,
    _EligibilityReader,
    _guard_observation,
    _PinReader,
    _Store,
)
from rag_runtime.request_authority import RequestAuthorityDecisionOutcome
from rag_runtime.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingObservation,
    RequestGuardRuntimeBindingRef,
    compute_request_guard_runtime_binding_ref,
)


class _GuardReader:
    def __init__(self, observation: RequestGuardRuntimeBindingObservation | None) -> None:
        self.observation = observation
        self.references: list[RequestGuardRuntimeBindingRef] = []

    async def read_exact(self, reference: RequestGuardRuntimeBindingRef):
        self.references.append(reference)
        return self.observation


def _generation_outcome(decision: GuideGenerationCardDecision) -> GuideGenerationCardOutcome:
    return GuideGenerationCardOutcome(
        decision=decision,
        stopped_stage=GuideGenerationCardStage.UPSTREAM if decision is GuideGenerationCardDecision.STOPPED else None,
        upstream_outcome=cast(GuideOrchestrationOutcome, object()),
        generation_result=None,
        authority_outcome=None,
        card_outcome=None,
    )


def _claim_outcome(
    guide_outcome: GuideGenerationCardOutcome,
    decision: GuideClaimCitationDecision,
) -> GuideClaimCitationValidationOutcome:
    selection = _validated_selection() if decision is GuideClaimCitationDecision.VALIDATED else None
    return GuideClaimCitationValidationOutcome(
        decision=decision,
        stopped_stage=(
            GuideClaimCitationStage.CLAIM_CITATION_VALIDATION
            if decision is GuideClaimCitationDecision.STOPPED
            else None
        ),
        guide_outcome=guide_outcome,
        candidate_set=selection.candidate_set if selection is not None else None,
        support_receipts=selection.support_receipts if selection is not None else None,
        validation_outcome=None,
        validated_selection=selection,
    )


def _request(ref: RequestGuardRuntimeBindingRef):
    return runtime_orchestration.GuideCitationRuntimeOrchestrationRequest(
        generation_request=cast(GuideGenerationCardOrchestrationRequest, object()),
        request_guard_runtime_binding_ref=ref,
        evaluation_time=NOW,
    )


def _run(request, *, guard_reader, **overrides):
    dependencies = {
        "generator": object(),
        "decision_verifier": object(),
        "guard_reader": guard_reader,
        "request_authority_reader": _AuthorityReader(),
        "pin_reader": _PinReader(),
        "approval_reader": _ApprovalReader(),
        "eligibility_reader": _EligibilityReader(),
        "store": _Store(),
    }
    dependencies.update(overrides)
    return asyncio.run(runtime_orchestration.orchestrate_guide_citation_runtime(request, **dependencies))


def _patch_ready(monkeypatch: pytest.MonkeyPatch):
    generation = _generation_outcome(GuideGenerationCardDecision.COMPLETED)
    claim = _claim_outcome(generation, GuideClaimCitationDecision.VALIDATED)

    async def generation_step(*args, **kwargs):
        return generation

    monkeypatch.setattr(runtime_orchestration, "orchestrate_guide_generation_card", generation_step)
    monkeypatch.setattr(runtime_orchestration, "run_guide_claim_citation_validation", lambda request: claim)
    return generation, claim


def test_generation_stop_prevents_every_downstream_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    generation = _generation_outcome(GuideGenerationCardDecision.STOPPED)

    async def generation_step(*args, **kwargs):
        return generation

    monkeypatch.setattr(runtime_orchestration, "orchestrate_guide_generation_card", generation_step)
    monkeypatch.setattr(
        runtime_orchestration,
        "run_guide_claim_citation_validation",
        lambda request: pytest.fail("claim validation must not run"),
    )
    guard_reader = _GuardReader(_guard_observation())

    outcome = _run(_request(compute_request_guard_runtime_binding_ref(_guard_observation())), guard_reader=guard_reader)

    assert outcome.decision is runtime_orchestration.GuideCitationRuntimeDecision.STOPPED
    assert outcome.stopped_stage is runtime_orchestration.GuideCitationRuntimeStage.GENERATION_CARD
    assert outcome.generation_outcome is generation
    assert outcome.claim_citation_outcome is None
    assert guard_reader.references == []


def test_claim_validation_stop_prevents_guard_and_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    generation = _generation_outcome(GuideGenerationCardDecision.COMPLETED)
    claim = _claim_outcome(generation, GuideClaimCitationDecision.STOPPED)

    async def generation_step(*args, **kwargs):
        return generation

    monkeypatch.setattr(runtime_orchestration, "orchestrate_guide_generation_card", generation_step)
    monkeypatch.setattr(runtime_orchestration, "run_guide_claim_citation_validation", lambda request: claim)
    guard_reader = _GuardReader(_guard_observation())

    outcome = _run(_request(compute_request_guard_runtime_binding_ref(_guard_observation())), guard_reader=guard_reader)

    assert outcome.decision is runtime_orchestration.GuideCitationRuntimeDecision.STOPPED
    assert outcome.stopped_stage is runtime_orchestration.GuideCitationRuntimeStage.CLAIM_CITATION_VALIDATION
    assert outcome.claim_citation_outcome is claim
    assert guard_reader.references == []


def test_absent_exact_guard_stops_before_request_and_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ready(monkeypatch)
    reference = compute_request_guard_runtime_binding_ref(_guard_observation())
    guard_reader = _GuardReader(None)

    outcome = _run(_request(reference), guard_reader=guard_reader)

    assert outcome.decision is runtime_orchestration.GuideCitationRuntimeDecision.STOPPED
    assert outcome.stopped_stage is runtime_orchestration.GuideCitationRuntimeStage.REQUEST_GUARD_RUNTIME_BINDING
    assert outcome.authorization_build_outcome is None
    assert outcome.authority_outcome is None
    assert guard_reader.references == [reference]


def test_non_pass_guard_stops_at_binding_without_issuing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ready(monkeypatch)
    guard = replace(_guard_observation(), actual_decision_outcome=RequestAuthorityDecisionOutcome.FAIL)
    guard_reader = _GuardReader(guard)

    outcome = _run(
        _request(compute_request_guard_runtime_binding_ref(guard)),
        guard_reader=guard_reader,
    )

    assert outcome.decision is runtime_orchestration.GuideCitationRuntimeDecision.STOPPED
    assert outcome.stopped_stage is runtime_orchestration.GuideCitationRuntimeStage.REQUEST_GUARD_RUNTIME_BINDING
    assert outcome.authorization_request is None
    assert outcome.authority_outcome is None


def test_rejected_authorization_request_preserves_build_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ready(monkeypatch)
    rejected = CitationAuthorizationBuildOutcome(
        AuthorizationBuildDecision.REJECTED,
        (AuthorizationReason.RUNTIME_BINDING_INVALID,),
        None,
    )
    monkeypatch.setattr(runtime_orchestration, "build_citation_authorization_request", lambda *args: rejected)
    guard = _guard_observation()

    outcome = _run(
        _request(compute_request_guard_runtime_binding_ref(guard)),
        guard_reader=_GuardReader(guard),
    )

    assert outcome.decision is runtime_orchestration.GuideCitationRuntimeDecision.STOPPED
    assert outcome.stopped_stage is runtime_orchestration.GuideCitationRuntimeStage.CITATION_AUTHORIZATION_REQUEST
    assert outcome.authorization_build_outcome is rejected
    assert outcome.authorization_request is None
    assert outcome.authority_outcome is None


def test_full_issued_pass_reaches_authorized_selection_and_reads_guard_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, claim = _patch_ready(monkeypatch)
    guard = _guard_observation()
    reference = compute_request_guard_runtime_binding_ref(guard)
    guard_reader = _GuardReader(guard)

    outcome = _run(_request(reference), guard_reader=guard_reader)

    assert outcome.decision is runtime_orchestration.GuideCitationRuntimeDecision.COMPLETED
    assert outcome.stopped_stage is None
    assert outcome.claim_citation_outcome is claim
    assert outcome.authority_outcome is not None
    assert outcome.authority_outcome.decision is CitationAuthorityIssueDecision.ISSUED
    assert isinstance(outcome.finalization_outcome, AuthorizedCitationSelection)
    assert guard_reader.references == [reference, reference]


def test_replayed_pass_still_completes_with_authorized_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ready(monkeypatch)
    guard = _guard_observation()
    reference = compute_request_guard_runtime_binding_ref(guard)
    store = _Store()
    first = _run(_request(reference), guard_reader=_GuardReader(guard), store=store)
    assert first.authority_outcome is not None
    assert first.authority_outcome.decision is CitationAuthorityIssueDecision.ISSUED

    replay = _run(_request(reference), guard_reader=_GuardReader(guard), store=store)

    assert replay.decision is runtime_orchestration.GuideCitationRuntimeDecision.COMPLETED
    assert replay.authority_outcome is not None
    assert replay.authority_outcome.decision is CitationAuthorityIssueDecision.REPLAYED
    assert isinstance(replay.finalization_outcome, AuthorizedCitationSelection)


def test_historical_fail_receipt_completes_with_discard_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    _, claim = _patch_ready(monkeypatch)
    guard = _guard_observation()
    selection = claim.validated_selection
    assert selection is not None
    built = runtime_orchestration.build_citation_authorization_request(
        selection,
        build_runtime_authorization_binding(guard),
        build_origin_request_guard_binding(guard),
    )
    assert built.request is not None
    passing_receipt = _passing_receipt(built.request)
    fail_receipt = replace(
        passing_receipt,
        selections=(replace(passing_receipt.selections[0], member_decision=GuardDecision.FAIL),),
    )

    async def issue(**kwargs):
        return CitationAuthorityIssueOutcome(CitationAuthorityIssueDecision.ISSUED, None, fail_receipt)

    monkeypatch.setattr(runtime_orchestration, "issue_citation_authorization", issue)

    outcome = _run(
        _request(compute_request_guard_runtime_binding_ref(guard)),
        guard_reader=_GuardReader(guard),
    )

    assert outcome.decision is runtime_orchestration.GuideCitationRuntimeDecision.COMPLETED
    assert isinstance(outcome.finalization_outcome, DiscardGeneratedContent)
    assert outcome.finalization_outcome.reasons == ("SELECTION_NOT_AUTHORIZED",)


def test_prerequisite_failure_completes_with_receipt_required(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ready(monkeypatch)
    guard = _guard_observation()

    async def issue(**kwargs):
        return CitationAuthorityIssueOutcome(
            CitationAuthorityIssueDecision.PREREQUISITE_FAILED,
            CitationAuthorityIssueReason.REQUEST_GUARD_ABSENT,
            None,
        )

    monkeypatch.setattr(runtime_orchestration, "issue_citation_authorization", issue)

    outcome = _run(
        _request(compute_request_guard_runtime_binding_ref(guard)),
        guard_reader=_GuardReader(guard),
    )

    assert outcome.decision is runtime_orchestration.GuideCitationRuntimeDecision.COMPLETED
    assert isinstance(outcome.finalization_outcome, DiscardGeneratedContent)
    assert outcome.finalization_outcome.reasons == ("AUTHORIZATION_RECEIPT_REQUIRED",)


def test_call_order_is_fixed_and_each_terminal_stage_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    generation = _generation_outcome(GuideGenerationCardDecision.COMPLETED)
    claim = _claim_outcome(generation, GuideClaimCitationDecision.VALIDATED)
    guard = _guard_observation()
    reference = compute_request_guard_runtime_binding_ref(guard)
    calls: list[str] = []
    original_build = runtime_orchestration.build_citation_authorization_request
    original_finalize = runtime_orchestration.finalize_citation_authority_outcome

    async def generation_step(*args, **kwargs):
        calls.append("generation/card")
        return generation

    def validation_step(request):
        calls.append("claim/citation validation")
        return claim

    class OrderedGuardReader(_GuardReader):
        async def read_exact(self, exact_reference):
            calls.append("#806 exact read")
            return await super().read_exact(exact_reference)

    def build_step(*args):
        calls.append("authorization request build")
        return original_build(*args)

    async def issue_step(**kwargs):
        calls.append("#869 authority")
        request = kwargs["request"]
        return CitationAuthorityIssueOutcome(
            CitationAuthorityIssueDecision.REPLAYED,
            None,
            _passing_receipt(request),
        )

    def finalize_step(**kwargs):
        calls.append("#882 finalization")
        return original_finalize(**kwargs)

    monkeypatch.setattr(runtime_orchestration, "orchestrate_guide_generation_card", generation_step)
    monkeypatch.setattr(runtime_orchestration, "run_guide_claim_citation_validation", validation_step)
    monkeypatch.setattr(runtime_orchestration, "build_citation_authorization_request", build_step)
    monkeypatch.setattr(runtime_orchestration, "issue_citation_authorization", issue_step)
    monkeypatch.setattr(runtime_orchestration, "finalize_citation_authority_outcome", finalize_step)

    outcome = _run(_request(reference), guard_reader=OrderedGuardReader(guard))

    assert outcome.decision is runtime_orchestration.GuideCitationRuntimeDecision.COMPLETED
    assert calls == [
        "generation/card",
        "claim/citation validation",
        "#806 exact read",
        "authorization request build",
        "#869 authority",
        "#882 finalization",
    ]


def test_request_rejects_naive_evaluation_time() -> None:
    reference = compute_request_guard_runtime_binding_ref(_guard_observation())

    with pytest.raises(ValueError, match="evaluation_time must be timezone-aware"):
        runtime_orchestration.GuideCitationRuntimeOrchestrationRequest(
            generation_request=cast(GuideGenerationCardOrchestrationRequest, object()),
            request_guard_runtime_binding_ref=reference,
            evaluation_time=datetime(2026, 9, 20, 12),
        )
