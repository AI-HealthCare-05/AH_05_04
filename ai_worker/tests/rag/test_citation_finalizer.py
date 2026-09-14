from __future__ import annotations

from dataclasses import replace

import pytest

from ai_worker.tasks.rag.citation_authorization import (
    AuthorizationBuildDecision,
    GuardDecision,
    build_citation_authorization_request,
)
from ai_worker.tasks.rag.citation_finalizer import (
    AuthorizedCitationSelection,
    DiscardGeneratedContent,
    FinalizationStage,
    finalize_citations,
)
from ai_worker.tasks.rag.claim_citation_validator import (
    CandidateValidationDecision,
    ClaimKind,
    ClaimSupportStatus,
    validate_claim_citations,
)
from ai_worker.tests.rag.test_citation_authorization import (
    _built_request,
    _origin_guard,
    _passing_receipt,
    _runtime_binding,
)
from ai_worker.tests.rag.test_claim_citation_validator import _candidate_set, _support_receipt

_A = "a" * 64


def test_returns_release_gate_input_only_for_an_exact_bound_pass_receipt() -> None:
    selection, request = _built_request()
    receipt = _passing_receipt(request)

    outcome = finalize_citations(selection, request, receipt)

    assert isinstance(outcome, AuthorizedCitationSelection)
    assert outcome.validated_selection == selection
    assert outcome.authorization_receipt == receipt
    assert not hasattr(outcome, "release_decision")


def test_discards_content_when_request_was_built_for_another_selection() -> None:
    selection, request = _built_request()
    altered_selection = replace(selection, selection_sha256=_A)

    outcome = finalize_citations(altered_selection, request, _passing_receipt(request))

    assert isinstance(outcome, DiscardGeneratedContent)
    assert outcome.failed_stage is FinalizationStage.CLAIM_CITATION_VALIDATION
    assert outcome.reasons == ("VALIDATED_SELECTION_INVALID",)


def test_discards_content_when_request_self_binding_is_mutated() -> None:
    selection, request = _built_request()
    altered_request = replace(request, request_sha256=_A)

    outcome = finalize_citations(selection, altered_request, _passing_receipt(altered_request))

    assert isinstance(outcome, DiscardGeneratedContent)
    assert outcome.failed_stage is FinalizationStage.CITATION_AUTHORIZATION
    assert outcome.reasons == ("AUTHORIZATION_REQUEST_MISMATCH",)


def test_discards_content_for_missing_or_rejected_authorization_receipt() -> None:
    selection, request = _built_request()
    rejected_receipt = replace(_passing_receipt(request), origin_decision=GuardDecision.FAIL)

    missing = finalize_citations(selection, request, None)
    rejected = finalize_citations(selection, request, rejected_receipt)

    assert isinstance(missing, DiscardGeneratedContent)
    assert missing.reasons == ("AUTHORIZATION_RECEIPT_REQUIRED",)
    assert isinstance(rejected, DiscardGeneratedContent)
    assert rejected.reasons == ("RECEIPT_BINDING_MISMATCH",)


def test_failure_output_and_repr_do_not_contain_generated_or_source_text() -> None:
    selection, request = _built_request()
    sentinel = "SYNTHETIC_SECRET_MEDICAL_SENTINEL"
    malformed_receipt = object()

    outcome = finalize_citations(selection, request, malformed_receipt)

    assert isinstance(outcome, DiscardGeneratedContent)
    assert sentinel not in repr(outcome)
    assert outcome.reasons == ("RECEIPT_INVALID",)


def test_malformed_request_fails_closed_without_crossing_the_boundary_as_an_exception() -> None:
    selection, _ = _built_request()

    outcome = finalize_citations(selection, object(), object())

    assert isinstance(outcome, DiscardGeneratedContent)
    assert outcome.reasons == ("AUTHORIZATION_REQUEST_INVALID",)


def test_partially_supported_safety_fallback_cannot_reach_finalizer_success() -> None:
    candidate_set = _candidate_set(
        claim_kind=ClaimKind.SAFETY_FALLBACK,
        support_status=ClaimSupportStatus.PARTIALLY_SUPPORTED,
    )
    validation = validate_claim_citations(candidate_set, (_support_receipt(candidate_set),))
    finalization = None
    if validation.validated_selection is not None:
        runtime = _runtime_binding()
        built = build_citation_authorization_request(
            validation.validated_selection,
            runtime,
            _origin_guard(runtime),
        )
        if built.decision is AuthorizationBuildDecision.BUILT and built.request is not None:
            finalization = finalize_citations(
                validation.validated_selection,
                built.request,
                _passing_receipt(built.request),
            )

    assert not isinstance(finalization, AuthorizedCitationSelection)
    assert validation.decision is CandidateValidationDecision.REJECTED
    assert validation.validated_selection is None


@pytest.mark.parametrize(
    "mutate_verifier",
    (
        lambda ref: replace(ref, artifact_code="support-verifier-v2"),
        lambda ref: replace(ref, version="v2"),
        lambda ref: replace(ref, content_sha256=_A),
    ),
)
def test_previous_authorization_cannot_be_reused_after_support_verifier_history_changes(
    mutate_verifier,
) -> None:
    original_selection, request = _built_request()
    original_receipt = _passing_receipt(request)
    changed_support_receipt = replace(
        original_selection.support_receipts[0],
        verifier_artifact_ref=mutate_verifier(original_selection.support_receipts[0].verifier_artifact_ref),
    )
    replayed = validate_claim_citations(
        original_selection.candidate_set,
        (changed_support_receipt,),
    )
    assert replayed.validated_selection is not None
    rebuilt = build_citation_authorization_request(
        replayed.validated_selection,
        request.runtime_binding,
        request.origin_request_guard,
    )
    assert rebuilt.request is not None

    outcome = finalize_citations(replayed.validated_selection, rebuilt.request, original_receipt)

    assert isinstance(outcome, DiscardGeneratedContent)
    assert replayed.validated_selection.selection_sha256 != original_selection.selection_sha256
    assert rebuilt.request.request_sha256 != request.request_sha256
