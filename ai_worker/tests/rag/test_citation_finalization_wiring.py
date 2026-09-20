from __future__ import annotations

from dataclasses import replace

from ai_worker.tasks.rag.citation_authorization import GuardDecision
from ai_worker.tasks.rag.citation_authorization_authority import (
    CitationAuthorityIssueDecision,
    CitationAuthorityIssueOutcome,
    CitationAuthorityIssueReason,
)
from ai_worker.tasks.rag.citation_finalization_wiring import finalize_citation_authority_outcome
from ai_worker.tasks.rag.citation_finalizer import (
    AuthorizedCitationSelection,
    DiscardGeneratedContent,
    FinalizationStage,
)
from ai_worker.tests.rag.test_citation_authorization import _built_request, _passing_receipt

_A = "a" * 64


def _outcome(
    decision: CitationAuthorityIssueDecision,
    receipt,
) -> CitationAuthorityIssueOutcome:
    reason = (
        CitationAuthorityIssueReason.REQUEST_GUARD_ABSENT
        if decision is CitationAuthorityIssueDecision.PREREQUISITE_FAILED
        else None
    )
    return CitationAuthorityIssueOutcome(decision=decision, reason=reason, receipt=receipt)


def test_issued_pass_receipt_returns_authorized_selection() -> None:
    selection, request = _built_request()
    receipt = _passing_receipt(request)

    result = finalize_citation_authority_outcome(
        validated_selection=selection,
        authorization_request=request,
        authority_outcome=_outcome(CitationAuthorityIssueDecision.ISSUED, receipt),
    )

    assert isinstance(result, AuthorizedCitationSelection)
    assert result.validated_selection == selection
    assert result.authorization_receipt == receipt


def test_replayed_pass_receipt_returns_authorized_selection() -> None:
    selection, request = _built_request()
    receipt = _passing_receipt(request)

    result = finalize_citation_authority_outcome(
        validated_selection=selection,
        authorization_request=request,
        authority_outcome=_outcome(CitationAuthorityIssueDecision.REPLAYED, receipt),
    )

    assert isinstance(result, AuthorizedCitationSelection)
    assert result.authorization_receipt == receipt


def test_issued_historical_fail_receipt_is_not_authorized() -> None:
    selection, request = _built_request()
    passing_receipt = _passing_receipt(request)
    fail_receipt = replace(
        passing_receipt,
        selections=(replace(passing_receipt.selections[0], member_decision=GuardDecision.FAIL),),
    )

    result = finalize_citation_authority_outcome(
        validated_selection=selection,
        authorization_request=request,
        authority_outcome=_outcome(CitationAuthorityIssueDecision.ISSUED, fail_receipt),
    )

    assert isinstance(result, DiscardGeneratedContent)
    assert result.failed_stage is FinalizationStage.CITATION_AUTHORIZATION
    assert result.reasons == ("SELECTION_NOT_AUTHORIZED",)


def test_prerequisite_failure_without_receipt_requires_authorization_receipt() -> None:
    selection, request = _built_request()

    result = finalize_citation_authority_outcome(
        validated_selection=selection,
        authorization_request=request,
        authority_outcome=_outcome(CitationAuthorityIssueDecision.PREREQUISITE_FAILED, None),
    )

    assert isinstance(result, DiscardGeneratedContent)
    assert result.failed_stage is FinalizationStage.CITATION_AUTHORIZATION
    assert result.reasons == ("AUTHORIZATION_RECEIPT_REQUIRED",)


def test_issued_without_receipt_fails_closed() -> None:
    selection, request = _built_request()

    result = finalize_citation_authority_outcome(
        validated_selection=selection,
        authorization_request=request,
        authority_outcome=_outcome(CitationAuthorityIssueDecision.ISSUED, None),
    )

    assert isinstance(result, DiscardGeneratedContent)
    assert result.reasons == ("AUTHORIZATION_RECEIPT_REQUIRED",)


def test_replayed_without_receipt_fails_closed() -> None:
    selection, request = _built_request()

    result = finalize_citation_authority_outcome(
        validated_selection=selection,
        authorization_request=request,
        authority_outcome=_outcome(CitationAuthorityIssueDecision.REPLAYED, None),
    )

    assert isinstance(result, DiscardGeneratedContent)
    assert result.reasons == ("AUTHORIZATION_RECEIPT_REQUIRED",)


def test_prerequisite_failure_ignores_unexpected_receipt() -> None:
    selection, request = _built_request()

    result = finalize_citation_authority_outcome(
        validated_selection=selection,
        authorization_request=request,
        authority_outcome=_outcome(
            CitationAuthorityIssueDecision.PREREQUISITE_FAILED,
            _passing_receipt(request),
        ),
    )

    assert isinstance(result, DiscardGeneratedContent)
    assert result.reasons == ("AUTHORIZATION_RECEIPT_REQUIRED",)
    assert "REQUEST_GUARD_ABSENT" not in result.reasons


def test_authority_receipt_does_not_bypass_request_mismatch_rejection() -> None:
    selection, request = _built_request()
    mismatched_request = replace(request, request_sha256=_A)

    result = finalize_citation_authority_outcome(
        validated_selection=selection,
        authorization_request=mismatched_request,
        authority_outcome=_outcome(
            CitationAuthorityIssueDecision.ISSUED,
            _passing_receipt(mismatched_request),
        ),
    )

    assert isinstance(result, DiscardGeneratedContent)
    assert result.failed_stage is FinalizationStage.CITATION_AUTHORIZATION
    assert result.reasons == ("AUTHORIZATION_REQUEST_MISMATCH",)
