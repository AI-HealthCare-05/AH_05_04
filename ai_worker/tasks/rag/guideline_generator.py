"""Production Guideline Generator port and generation request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai_worker.tasks.rag.guideline_card import (
    GuidelineCardDraft,
    GuidelineGenerationFailure,
    MedicationIdentityRef,
    VersionedGuidelinePolicy,
)
from ai_worker.tasks.rag.guideline_production_evidence import ProductionGuidelineEvidenceSet


@dataclass(frozen=True, slots=True)
class GuidelineGenerationRequest:
    """RAG-15 production generation request.

    `evidence`는 #760 `VerifiedGuideEvidenceHandoff`에서 투영된 production evidence
    정본이다(`project_guideline_evidence_from_handoff`). legacy RAG-14
    `EvidenceGateOutcome`은 production 입력이 아니며, 이 request에 legacy/production
    dual-mode field를 두지 않는다.
    """

    medication_identities: tuple[MedicationIdentityRef, ...]
    evidence: ProductionGuidelineEvidenceSet
    policy: VersionedGuidelinePolicy


type GuidelineGenerationResult = GuidelineCardDraft | GuidelineGenerationFailure


class GuidelineGeneratorPort(Protocol):
    async def generate(
        self,
        request: GuidelineGenerationRequest,
    ) -> GuidelineGenerationResult: ...
