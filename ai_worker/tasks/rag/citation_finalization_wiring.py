"""Wire Citation Authorization authority outcomes into pure finalization."""

from __future__ import annotations

from ai_worker.tasks.rag.citation_authorization import CitationAuthorizationRequest
from ai_worker.tasks.rag.citation_authorization_authority import (
    CitationAuthorityIssueDecision,
    CitationAuthorityIssueOutcome,
)
from ai_worker.tasks.rag.citation_finalizer import CitationFinalizationOutcome, finalize_citations
from ai_worker.tasks.rag.claim_citation_validator import ValidatedCitationSelection


def finalize_citation_authority_outcome(
    *,
    validated_selection: ValidatedCitationSelection,
    authorization_request: CitationAuthorizationRequest,
    authority_outcome: CitationAuthorityIssueOutcome,
) -> CitationFinalizationOutcome:
    """Finalize an issued or replayed receipt and fail closed for every other decision."""

    receipt = (
        authority_outcome.receipt
        if authority_outcome.decision
        in (CitationAuthorityIssueDecision.ISSUED, CitationAuthorityIssueDecision.REPLAYED)
        else None
    )
    return finalize_citations(validated_selection, authorization_request, receipt)
