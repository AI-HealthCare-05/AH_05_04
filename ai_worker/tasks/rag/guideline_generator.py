"""Production Guideline Generator port and generation request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai_worker.tasks.rag.evidence_gate import EvidenceGateOutcome
from ai_worker.tasks.rag.guide_evidence_handoff import VerifiedGuideEvidenceHandoff
from ai_worker.tasks.rag.guideline_card import (
    GuidelineCardDraft,
    GuidelineGenerationFailure,
    MedicationIdentityRef,
    VersionedGuidelinePolicy,
)


@dataclass(frozen=True, slots=True)
class GuidelineGenerationRequest:
    medication_identities: tuple[MedicationIdentityRef, ...]
    policy: VersionedGuidelinePolicy
    evidence_gate_outcome: EvidenceGateOutcome | None = None
    evidence_handoff: VerifiedGuideEvidenceHandoff | None = None

    def __init__(
        self,
        medication_identities: tuple[MedicationIdentityRef, ...],
        evidence_gate_outcome_or_policy: EvidenceGateOutcome | VersionedGuidelinePolicy | None = None,
        policy: VersionedGuidelinePolicy | None = None,
        *,
        evidence_gate_outcome: EvidenceGateOutcome | None = None,
        evidence_handoff: VerifiedGuideEvidenceHandoff | None = None,
    ) -> None:
        object.__setattr__(self, "medication_identities", medication_identities)
        if isinstance(evidence_gate_outcome_or_policy, VersionedGuidelinePolicy):
            object.__setattr__(self, "policy", evidence_gate_outcome_or_policy)
            object.__setattr__(self, "evidence_gate_outcome", evidence_gate_outcome)
            object.__setattr__(self, "evidence_handoff", evidence_handoff)
        elif isinstance(evidence_gate_outcome_or_policy, EvidenceGateOutcome):
            object.__setattr__(self, "evidence_gate_outcome", evidence_gate_outcome_or_policy)
            if policy is None:
                raise ValueError("policy must be provided")
            object.__setattr__(self, "policy", policy)
            object.__setattr__(self, "evidence_handoff", evidence_handoff)
        else:
            if policy is None:
                raise ValueError("policy must be provided")
            object.__setattr__(self, "policy", policy)
            object.__setattr__(self, "evidence_gate_outcome", evidence_gate_outcome)
            object.__setattr__(self, "evidence_handoff", evidence_handoff)


type GuidelineGenerationResult = GuidelineCardDraft | GuidelineGenerationFailure


class GuidelineGeneratorPort(Protocol):
    async def generate(
        self,
        request: GuidelineGenerationRequest,
    ) -> GuidelineGenerationResult: ...
