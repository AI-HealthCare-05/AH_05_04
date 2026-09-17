"""Read-only Assessment·Eligibility Authority Reader seam (#746).

#712가 `rag_evidence_authority`에 남긴 immutable historical authority를 AI Worker /
Guide Runtime이 authoritative observation으로 소비하기 위한 최소 port입니다.

Scope & Authority Boundaries:
- Minimal Seam: generic repository/framework를 도입하지 않습니다. #712가 확정한 두 exact
  lookup(selection identity, assessment artifact identity)만 노출합니다.
- Contract Reuse: 반환 계약과 artifact identity의 정본은 `rag_runtime.evidence_authority`
  입니다. 새 authority DTO나 새 hash domain을 정의하지 않습니다.
- Historical Boundary: 이 seam은 "그때 무엇이 authoritative했는가"만 읽습니다. 현재 Snapshot
  CURRENT 여부, Source ACTIVE 여부, approval revoke 여부, verifier deployment 재판정,
  새 validity window 계산은 후속 runtime consumer 책임입니다.
- Error Semantics: 정상 no-row만 `None`입니다. 예상 가능한 persistence/data 실패는
  `AssessmentEligibilityAuthorityReaderError`로 fail closed합니다.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from rag_runtime.evidence_authority import (
    ImmutableArtifactRef,
    PersistedEvidenceAuthority,
)

__all__ = [
    "AssessmentEligibilityAuthorityReaderError",
    "AssessmentEligibilityAuthorityReaderPort",
]


class AssessmentEligibilityAuthorityReaderError(Exception):
    """Explicit dependency or data-integrity failure when reading persisted authority.

    정상 no-row는 이 예외가 아니라 `None`입니다. DB dependency 실패, ambiguous row,
    malformed identity, corrupt persisted authority만 이 예외로 승격됩니다. Programming
    error는 broad `except`로 삼키지 않고 그대로 전파됩니다.
    """


class AssessmentEligibilityAuthorityReaderPort(Protocol):
    """Read-only lookup contract over the persisted #712 authority."""

    async def read_by_selection(
        self,
        *,
        retrieval_run_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> PersistedEvidenceAuthority | None:
        """Exact selection identity lookup. no-row면 `None`."""
        ...

    async def read_by_assessment_ref(
        self,
        *,
        assessment_artifact_ref: ImmutableArtifactRef,
    ) -> PersistedEvidenceAuthority | None:
        """Exact assessment artifact identity lookup. no-row면 `None`."""
        ...
