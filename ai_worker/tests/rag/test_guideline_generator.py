"""Tests for GuidelineGeneratorPort and GuidelineGenerationRequest."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID

import pytest

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
from ai_worker.tasks.rag.guideline_production_evidence import (
    ProductionGuidelineEvidence,
    ProductionGuidelineEvidenceSet,
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
    evidence = ProductionGuidelineEvidenceSet(
        evaluated_at=datetime(2026, 9, 10, 3, 0, tzinfo=UTC),
        handoff_sha256="e" * 64,
        selections=(
            ProductionGuidelineEvidence(
                evidence_key="synthetic-evidence-001",
                source_snapshot_id=UUID("33333333-3333-4333-8333-333333333333"),
                source_snapshot_member_id=UUID("44444444-4444-4444-8444-444444444444"),
                source_code="SYNTHETIC_SOURCE",
                source_version="synthetic-source-v1",
                locator="$.synthetic.locator",
                content_sha256="4" * 64,
                content_text=SensitiveText("합성 근거 본문입니다."),
                retrieval_receipt_ref=ImmutableArtifactRef("retrieval-receipt", "v1", "5" * 64),
                eligibility_receipt_ref=ImmutableArtifactRef("eligibility-receipt", "v1", "6" * 64),
                assessment_artifact_ref=ImmutableArtifactRef("assessment", "v1", "7" * 64),
                verifier_artifact_ref=ImmutableArtifactRef("assessment-verifier", "v1", "8" * 64),
            ),
        ),
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
        evidence=evidence,
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
                        source_snapshot_id=UUID("33333333-3333-4333-8333-333333333333"),
                        source_snapshot_member_id=UUID("44444444-4444-4444-8444-444444444444"),
                        source_code="SYNTHETIC_SOURCE",
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
    assert generator.last_request.evidence == request.evidence
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
    assert generator.last_request.evidence == request.evidence
    assert generator.last_request.policy == request.policy
