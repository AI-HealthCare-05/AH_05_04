from __future__ import annotations

from dataclasses import replace

from ai_worker.tasks.rag.citation_authorization import GuardDecision
from ai_worker.tasks.rag.citation_finalizer import (
    AuthorizedCitationSelection,
    DiscardGeneratedContent,
    FinalizationStage,
    finalize_citations,
)
from ai_worker.tests.rag.test_citation_authorization import _built_request, _passing_receipt

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
