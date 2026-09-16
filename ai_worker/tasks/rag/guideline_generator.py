"""Production Guideline Generator port and generation request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai_worker.tasks.rag.evidence_gate import EvidenceGateOutcome
from ai_worker.tasks.rag.guideline_card import (
    GuidelineCardDraft,
    GuidelineGenerationFailure,
    MedicationIdentityRef,
    VersionedGuidelinePolicy,
)


@dataclass(frozen=True, slots=True)
class GuidelineGenerationRequest:
    medication_identities: tuple[MedicationIdentityRef, ...]
    evidence_gate_outcome: EvidenceGateOutcome
    policy: VersionedGuidelinePolicy


type GuidelineGenerationResult = GuidelineCardDraft | GuidelineGenerationFailure


class GuidelineGeneratorPort(Protocol):
    async def generate(
        self,
        request: GuidelineGenerationRequest,
    ) -> GuidelineGenerationResult: ...
