from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_worker.tasks.rag.citation_authorization import (
    AuthorizationVerificationDecision,
    UsePurpose,
    verify_citation_authorization_receipt,
)
from ai_worker.tasks.rag.claim_citation_validator import (
    CandidateValidationDecision,
    CitationSourceType,
    ClaimKind,
    ClaimSupportStatus,
    validate_claim_citations,
)
from ai_worker.tests.rag.test_citation_authorization import _built_request, _passing_receipt
from ai_worker.tests.rag.test_claim_citation_validator import _candidate_set, _support_receipt

_FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "rag" / "citation" / "finalization_cases.json"


def _cases() -> list[dict[str, str]]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["id"])
def test_target_claim_citation_matrix_executes_against_the_pure_boundary(case: dict[str, str]) -> None:
    source_type = CitationSourceType(case["source_type"])
    support_status = ClaimSupportStatus(case["support_status"])
    claim_kind = ClaimKind(case["claim_kind"])
    candidate_set = _candidate_set(
        source_type=source_type,
        support_status=support_status,
        claim_kind=claim_kind,
    )

    validation = validate_claim_citations(candidate_set, (_support_receipt(candidate_set),))

    assert validation.decision.value == case["validation_decision"]
    if validation.decision is CandidateValidationDecision.VALIDATED:
        assert validation.validated_selection is not None


def test_target_authorization_matrix_distinguishes_patient_citation_from_retrieval() -> None:
    _, request = _built_request()
    passing = _passing_receipt(request)
    retrieval_only = replace(
        passing,
        selections=(replace(passing.selections[0], purpose=UsePurpose.RETRIEVAL),),
    )

    assert (
        verify_citation_authorization_receipt(request, passing).decision is AuthorizationVerificationDecision.AUTHORIZED
    )
    assert (
        verify_citation_authorization_receipt(request, retrieval_only).decision
        is AuthorizationVerificationDecision.REJECTED
    )
