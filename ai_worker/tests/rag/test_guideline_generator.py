"""Tests for GuidelineGeneratorPort and GuidelineGenerationRequest."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ai_worker.tasks.rag.evidence_gate import (
    EvidenceGateExecutionStatus,
    EvidenceGateOutcome,
    EvidenceGateReason,
    EvidenceStatus,
)
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    SensitiveText,
)
from ai_worker.tasks.rag.guideline_card import (
    GuidelineActionClass,
    GuidelineCardDraft,
    GuidelineCitationDraft,
    GuidelineClaimDraft,
    GuidelineGenerationFailure,
    GuidelineScope,
    MedicationIdentityRef,
    VersionedGuidelinePolicy,
)
from ai_worker.tasks.rag.guideline_generator import (
    GuidelineGenerationRequest,
    GuidelineGenerationResult,
    GuidelineGeneratorPort,
)


class SyntheticGuidelineGenerator:
    def __init__(
        self,
        result: GuidelineGenerationResult,
    ) -> None:
        self._result = result
        self.last_request: GuidelineGenerationRequest | None = None

    async def generate(
        self,
        request: GuidelineGenerationRequest,
    ) -> GuidelineGenerationResult:
        self.last_request = request
        return self._result


def consume_generator(
    generator: GuidelineGeneratorPort,
) -> GuidelineGeneratorPort:
    return generator


def make_synthetic_request() -> GuidelineGenerationRequest:
    medication_identity = MedicationIdentityRef(
        prescription_version_medication_id="SYNTHETIC-ITEM-001",
        code_system="SYNTHETIC_CODE_SYSTEM",
        canonical_code="SYNTHETIC-CODE-001",
    )
    evidence_gate_outcome = EvidenceGateOutcome(
        execution_status=EvidenceGateExecutionStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.SUFFICIENT,
        reason=EvidenceGateReason.EVIDENCE_SUFFICIENT,
    )
    policy = VersionedGuidelinePolicy(
        artifact_ref=ImmutableArtifactRef(
            artifact_code="synthetic-guideline-policy",
            version="synthetic-policy@v1",
            content_sha256="0" * 64,
        ),
        maximum_claims=4,
        uncertainty_text_sha256="1" * 64,
        consultation_text_sha256="2" * 64,
    )
    return GuidelineGenerationRequest(
        medication_identities=(medication_identity,),
        evidence_gate_outcome=evidence_gate_outcome,
        policy=policy,
    )


def make_synthetic_draft() -> GuidelineCardDraft:
    return GuidelineCardDraft(
        claims=(
            GuidelineClaimDraft(
                claim_key="synthetic-claim-001",
                medication_identity=MedicationIdentityRef(
                    prescription_version_medication_id="SYNTHETIC-ITEM-001",
                    code_system="SYNTHETIC_CODE_SYSTEM",
                    canonical_code="SYNTHETIC-CODE-001",
                ),
                scope=GuidelineScope.FOOD_CAUTION,
                action_class=GuidelineActionClass.FOOD_AVOIDANCE,
                action_text=SensitiveText("합성 음식 주의 문구입니다."),
                citations=(
                    GuidelineCitationDraft(
                        evidence_key="synthetic-evidence-001",
                        source_snapshot_ref=ImmutableArtifactRef(
                            artifact_code="synthetic-source-snapshot",
                            version="snapshot@v1",
                            content_sha256="3" * 64,
                        ),
                        source_version="synthetic-source-v1",
                        locator="$.synthetic.locator",
                        content_sha256="4" * 64,
                    ),
                ),
            ),
        ),
        uncertainty_text=SensitiveText("합성 불확실성 안내입니다."),
        consultation_text=SensitiveText("합성 전문가 상담 안내입니다."),
    )


def test_synthetic_generator_satisfies_protocol() -> None:
    generator = SyntheticGuidelineGenerator(result=GuidelineGenerationFailure.PROVIDER_TIMEOUT)
    accepted = consume_generator(generator)
    assert accepted is generator


def test_guideline_generation_request_is_frozen() -> None:
    request = make_synthetic_request()
    with pytest.raises(FrozenInstanceError):
        request.policy = request.policy  # type: ignore[misc]


async def test_generator_success_path() -> None:
    expected_draft = make_synthetic_draft()
    generator = SyntheticGuidelineGenerator(result=expected_draft)
    request = make_synthetic_request()

    result = await generator.generate(request)

    assert result is expected_draft
    assert generator.last_request is not None
    assert generator.last_request.medication_identities == request.medication_identities
    assert generator.last_request.evidence_gate_outcome == request.evidence_gate_outcome
    assert generator.last_request.policy == request.policy


@pytest.mark.parametrize(
    "failure",
    tuple(GuidelineGenerationFailure),
)
async def test_generator_preserves_typed_failure(
    failure: GuidelineGenerationFailure,
) -> None:
    generator = SyntheticGuidelineGenerator(result=failure)
    request = make_synthetic_request()

    result = await generator.generate(request)

    assert result is failure
    assert isinstance(result, GuidelineGenerationFailure)
    assert generator.last_request is not None
    assert generator.last_request.medication_identities == request.medication_identities
    assert generator.last_request.evidence_gate_outcome == request.evidence_gate_outcome
    assert generator.last_request.policy == request.policy
