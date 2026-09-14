"""Pure finalization after Claim–Citation and authorization verification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ai_worker.tasks.rag.citation_authorization import (
    AuthorizationBuildDecision,
    AuthorizationVerificationDecision,
    CitationAuthorizationReceipt,
    CitationAuthorizationRequest,
    build_citation_authorization_request,
    verify_citation_authorization_receipt,
)
from ai_worker.tasks.rag.claim_citation_validator import ValidatedCitationSelection


class FinalizationStage(StrEnum):
    CLAIM_CITATION_VALIDATION = "CLAIM_CITATION_VALIDATION"
    CITATION_AUTHORIZATION = "CITATION_AUTHORIZATION"


@dataclass(frozen=True, slots=True)
class AuthorizedCitationSelection:
    """Exact-bound Citation selection for a later Release Gate, not public authorization."""

    validated_selection: ValidatedCitationSelection
    authorization_receipt: CitationAuthorizationReceipt


@dataclass(frozen=True, slots=True)
class DiscardGeneratedContent:
    """Fail-closed instruction containing stable codes and no generated/source text."""

    failed_stage: FinalizationStage
    reasons: tuple[str, ...]


CitationFinalizationOutcome = AuthorizedCitationSelection | DiscardGeneratedContent


def finalize_citations(
    validated_selection: ValidatedCitationSelection,
    authorization_request: object,
    authorization_receipt: object,
) -> CitationFinalizationOutcome:
    """Finalize only when selection, request, and observed receipt bind exactly."""

    if type(authorization_request) is not CitationAuthorizationRequest:
        return DiscardGeneratedContent(
            failed_stage=FinalizationStage.CITATION_AUTHORIZATION,
            reasons=("AUTHORIZATION_REQUEST_INVALID",),
        )
    rebuilt = build_citation_authorization_request(
        validated_selection,
        authorization_request.runtime_binding,
        authorization_request.origin_request_guard,
    )
    if rebuilt.decision is AuthorizationBuildDecision.REJECTED:
        return DiscardGeneratedContent(
            failed_stage=FinalizationStage.CLAIM_CITATION_VALIDATION,
            reasons=tuple(reason.value for reason in rebuilt.reasons),
        )
    if rebuilt.request != authorization_request:
        return DiscardGeneratedContent(
            failed_stage=FinalizationStage.CITATION_AUTHORIZATION,
            reasons=("AUTHORIZATION_REQUEST_MISMATCH",),
        )
    if authorization_receipt is None:
        return DiscardGeneratedContent(
            failed_stage=FinalizationStage.CITATION_AUTHORIZATION,
            reasons=("AUTHORIZATION_RECEIPT_REQUIRED",),
        )
    verification = verify_citation_authorization_receipt(authorization_request, authorization_receipt)
    if verification.decision is AuthorizationVerificationDecision.REJECTED or verification.receipt is None:
        return DiscardGeneratedContent(
            failed_stage=FinalizationStage.CITATION_AUTHORIZATION,
            reasons=tuple(reason.value for reason in verification.reasons),
        )
    return AuthorizedCitationSelection(
        validated_selection=validated_selection,
        authorization_receipt=verification.receipt,
    )
