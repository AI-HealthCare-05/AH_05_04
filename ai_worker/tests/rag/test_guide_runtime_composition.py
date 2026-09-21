from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from ai_worker.tasks.rag.authoritative_guide_evidence_handoff import (
    AuthoritativeGuideEvidenceAssemblyDecision,
    AuthoritativeGuideEvidenceAssemblyOutcome,
)
from ai_worker.tasks.rag.citation_authorization import CitationAuthorizationBuildOutcome, CitationAuthorizationRequest
from ai_worker.tasks.rag.citation_authorization_authority import CitationAuthorityIssueOutcome
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.guide_aggregate_evidence import (
    GuideAggregateEvidenceAssemblyDecision,
    GuideAggregateEvidenceAssemblyOutcome,
)
from ai_worker.tasks.rag.guide_citation_runtime_orchestration import (
    GuideCitationRuntimeDecision,
    GuideCitationRuntimeOrchestrationOutcome,
    GuideCitationRuntimeOrchestrationRequest,
    GuideCitationRuntimeStage,
)
from ai_worker.tasks.rag.guide_claim_citation_validation import GuideClaimCitationValidationOutcome
from ai_worker.tasks.rag.guide_evidence_handoff import (
    GuideEvidenceHandoffBuildDecision,
    GuideEvidenceHandoffBuildOutcome,
)
from ai_worker.tasks.rag.guide_generation_card_orchestration import (
    GuideGenerationCardDecision,
    GuideGenerationCardOutcome,
)
from ai_worker.tasks.rag.guide_medication_guidance_retrieval import (
    GuideMedicationGuidanceRetrievalDecision,
    GuideMedicationGuidanceRetrievalOutcome,
    MedicationGuidanceRetrieval,
)
from ai_worker.tasks.rag.guide_medication_identity_resolution import MedicationIdentityRefResolution
from ai_worker.tasks.rag.guide_orchestration import GuideOrchestrationOutcome
from ai_worker.tasks.rag.guideline_card import (
    GuidelineCard,
    GuidelineCardOutcome,
    GuidelineCardReason,
    GuidelineCardStatus,
    GuidelineFallbackCode,
    MedicationIdentityRef,
    VerifiedGuidelineFallback,
)
from ai_worker.tasks.rag.knowledge_chunk_content_hydration import (
    GuideContentHydrationDecision,
    GuideContentHydrationOutcome,
)
from ai_worker.tests.rag.test_guide_release_projection import _authorized_selection, _card
from rag_runtime.guide_release_projection import (
    GuideRuntimeReleaseDecision,
    GuideRuntimeReleaseProjectionCarrier,
    GuideRuntimeReleaseProjectionUnavailable,
)


def _composition_module():
    return importlib.import_module("ai_worker.tasks.rag.guide_runtime_composition")


def _generated_outcome(card: GuidelineCard) -> GuideGenerationCardOutcome:
    return GuideGenerationCardOutcome(
        decision=GuideGenerationCardDecision.COMPLETED,
        stopped_stage=None,
        upstream_outcome=cast(GuideOrchestrationOutcome, object()),
        generation_result=None,
        authority_outcome=None,
        card_outcome=GuidelineCardOutcome(
            status=GuidelineCardStatus.GENERATED,
            reason=GuidelineCardReason.CARD_GENERATED,
            card=card,
        ),
    )


def _completed_outcome() -> GuideCitationRuntimeOrchestrationOutcome:
    card = _card()
    return GuideCitationRuntimeOrchestrationOutcome(
        decision=GuideCitationRuntimeDecision.COMPLETED,
        stopped_stage=None,
        generation_outcome=_generated_outcome(card),
        claim_citation_outcome=cast(GuideClaimCitationValidationOutcome, object()),
        authorization_build_outcome=cast(CitationAuthorizationBuildOutcome, object()),
        authorization_request=cast(CitationAuthorizationRequest, object()),
        authority_outcome=cast(CitationAuthorityIssueOutcome, object()),
        finalization_outcome=_authorized_selection(target_ref=card.artifact_ref.content_sha256),
    )


def _fallback_outcome(
    code: GuidelineFallbackCode,
    status: GuidelineCardStatus,
    reason: GuidelineCardReason,
) -> GuideCitationRuntimeOrchestrationOutcome:
    fallback = VerifiedGuidelineFallback(
        artifact_ref=ImmutableArtifactRef("guide-fallback", "v1", "a" * 64),
        code=code,
        text=SensitiveText("현재는 승인된 안내를 제공할 수 없습니다. 의사 또는 약사와 상담하세요."),
        approval_verifier_ref=ImmutableArtifactRef("guide-fallback-verifier", "v1", "b" * 64),
    )
    generation_outcome = GuideGenerationCardOutcome(
        decision=GuideGenerationCardDecision.COMPLETED,
        stopped_stage=None,
        upstream_outcome=cast(GuideOrchestrationOutcome, object()),
        generation_result=None,
        authority_outcome=None,
        card_outcome=GuidelineCardOutcome(
            status=status,
            reason=reason,
            fallback_code=code,
            fallback=fallback,
        ),
    )
    return GuideCitationRuntimeOrchestrationOutcome(
        decision=GuideCitationRuntimeDecision.STOPPED,
        stopped_stage=GuideCitationRuntimeStage.CLAIM_CITATION_VALIDATION,
        generation_outcome=generation_outcome,
        claim_citation_outcome=cast(GuideClaimCitationValidationOutcome, object()),
        authorization_build_outcome=None,
        authorization_request=None,
        authority_outcome=None,
        finalization_outcome=None,
    )


def _generation_stopped_outcome() -> GuideCitationRuntimeOrchestrationOutcome:
    return GuideCitationRuntimeOrchestrationOutcome(
        decision=GuideCitationRuntimeDecision.STOPPED,
        stopped_stage=GuideCitationRuntimeStage.GENERATION_CARD,
        generation_outcome=GuideGenerationCardOutcome(
            decision=GuideGenerationCardDecision.STOPPED,
            stopped_stage=None,
            upstream_outcome=cast(GuideOrchestrationOutcome, object()),
            generation_result=None,
            authority_outcome=None,
            card_outcome=None,
        ),
        claim_citation_outcome=None,
        authorization_build_outcome=None,
        authorization_request=None,
        authority_outcome=None,
        finalization_outcome=None,
    )


def _execute(monkeypatch: pytest.MonkeyPatch, citation_outcome: object):
    composition = _composition_module()
    calls: list[str] = []
    request = cast(GuideCitationRuntimeOrchestrationRequest, object())
    dependencies = {
        "generator": object(),
        "decision_verifier": object(),
        "guard_reader": object(),
        "request_authority_reader": object(),
        "pin_reader": object(),
        "approval_reader": object(),
        "eligibility_reader": object(),
        "store": object(),
    }
    original_finalize = composition.finalize_guide_runtime_release
    original_project = composition.project_guide_runtime_release

    async def orchestrate(request_arg, **dependency_args):
        calls.append("#890")
        assert request_arg is request
        assert dependency_args == dependencies
        return citation_outcome

    def finalize(outcome: object):
        calls.append("#893")
        return original_finalize(outcome)

    def project(result: object):
        calls.append("#906")
        return original_project(result)

    monkeypatch.setattr(composition, "orchestrate_guide_citation_runtime", orchestrate)
    monkeypatch.setattr(composition, "finalize_guide_runtime_release", finalize)
    monkeypatch.setattr(composition, "project_guide_runtime_release", project)

    projection = asyncio.run(composition.execute_guide_runtime_release(request, **dependencies))
    return projection, calls


def test_canonical_composition_projects_one_authorized_runtime_outcome_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, calls = _execute(monkeypatch, _completed_outcome())

    assert calls == ["#890", "#893", "#906"]
    assert isinstance(projection, GuideRuntimeReleaseProjectionCarrier)
    assert projection.release_decision is GuideRuntimeReleaseDecision.PASS
    assert projection.answer is not None
    assert projection.citations
    assert projection.fallback is None


@pytest.mark.parametrize(
    ("code", "status", "reason", "decision"),
    (
        (
            GuidelineFallbackCode.NO_APPROVED_EVIDENCE,
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.EVIDENCE_INSUFFICIENT,
            GuideRuntimeReleaseDecision.REJECTED,
        ),
        (
            GuidelineFallbackCode.PROVIDER_TIMEOUT,
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.PROVIDER_TIMEOUT,
            GuideRuntimeReleaseDecision.REJECTED,
        ),
        (
            GuidelineFallbackCode.UNSUPPORTED_REQUEST,
            GuidelineCardStatus.LIMITED,
            GuidelineCardReason.UNSUPPORTED_REQUEST,
            GuideRuntimeReleaseDecision.LIMITED,
        ),
    ),
)
def test_canonical_composition_preserves_approved_fallback_projection(
    monkeypatch: pytest.MonkeyPatch,
    code: GuidelineFallbackCode,
    status: GuidelineCardStatus,
    reason: GuidelineCardReason,
    decision: GuideRuntimeReleaseDecision,
) -> None:
    projection, calls = _execute(monkeypatch, _fallback_outcome(code, status, reason))

    assert calls == ["#890", "#893", "#906"]
    assert isinstance(projection, GuideRuntimeReleaseProjectionCarrier)
    assert projection.release_decision is decision
    assert projection.answer is None
    assert projection.citations == ()
    assert projection.fallback is not None


@pytest.mark.parametrize(
    ("code", "reason"),
    (
        (GuidelineFallbackCode.PRESCRIPTION_STALE, GuidelineCardReason.PRESCRIPTION_STALE),
        (GuidelineFallbackCode.EXECUTION_CONTEXT_STALE, GuidelineCardReason.EXECUTION_CONTEXT_STALE),
    ),
)
def test_canonical_composition_preserves_stale_projection(
    monkeypatch: pytest.MonkeyPatch,
    code: GuidelineFallbackCode,
    reason: GuidelineCardReason,
) -> None:
    projection, calls = _execute(monkeypatch, _fallback_outcome(code, GuidelineCardStatus.STALE, reason))

    assert calls == ["#890", "#893", "#906"]
    assert isinstance(projection, GuideRuntimeReleaseProjectionCarrier)
    assert projection.release_decision is GuideRuntimeReleaseDecision.STALE
    assert projection.is_current is False
    assert projection.answer is None
    assert projection.citations == ()


def test_canonical_composition_fails_closed_for_malformed_runtime_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, calls = _execute(monkeypatch, object())

    assert calls == ["#890", "#893", "#906"]
    assert isinstance(projection, GuideRuntimeReleaseProjectionUnavailable)


def test_canonical_composition_does_not_reinterpret_early_runtime_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, calls = _execute(monkeypatch, _generation_stopped_outcome())

    assert calls == ["#890", "#893", "#906"]
    assert isinstance(projection, GuideRuntimeReleaseProjectionUnavailable)


@dataclass(frozen=True)
class _CarrierIdentification:
    medication_identification_id: UUID
    prescription_version_medication_id: UUID
    medication_name_snapshot: str = "synthetic"
    strength_text_snapshot: str | None = None


@dataclass(frozen=True)
class _RuntimeCarrier:
    prescription_version_id: UUID
    request_guard_runtime_binding_ref: object
    identifications: tuple[_CarrierIdentification, ...]


class _DeterministicIdentityResolver:
    def __init__(self, resolutions: tuple[MedicationIdentityRefResolution, ...]) -> None:
        self.resolutions = resolutions
        self.calls = 0

    async def resolve_ordered(self, *, identifications: tuple[object, ...]):
        self.calls += 1
        return self.resolutions


class _AuthorityReader:
    async def read_by_selection(self, *, retrieval_run_id: UUID, knowledge_chunk_id: UUID):
        return object()


def _canonical_root_fixture(count: int):
    prescription_id = UUID("c0000000-0000-4000-8000-000000000001")
    identifications = tuple(
        _CarrierIdentification(
            UUID(f"c0000000-0000-4000-8000-000000000{index:03d}"),
            UUID(f"d0000000-0000-4000-8000-000000000{index:03d}"),
        )
        for index in range(1, count + 1)
    )
    carrier = _RuntimeCarrier(prescription_id, object(), identifications)
    resolver = _DeterministicIdentityResolver(
        tuple(
            MedicationIdentityRefResolution(
                identification.medication_identification_id,
                identification.prescription_version_medication_id,
                MedicationIdentityRef(
                    str(identification.prescription_version_medication_id), "MFDS_ITEM_SEQ", f"ITEM-{index}"
                ),
            )
            for index, identification in enumerate(identifications, start=1)
        )
    )
    medications = tuple(
        MedicationGuidanceRetrieval(
            identification.prescription_version_medication_id,
            SimpleNamespace(
                persisted_receipt=SimpleNamespace(run_id=UUID(f"e0000000-0000-4000-8000-000000000{index:03d}"))
            ),  # type: ignore[arg-type]
            SimpleNamespace(selections=(object(),), retrieval_receipt=object()),  # type: ignore[arg-type]
        )
        for index, identification in enumerate(identifications, start=1)
    )
    return carrier, resolver, medications


@pytest.mark.parametrize("count", (1, 2))
def test_final_canonical_root_sequences_existing_boundaries_once_per_medication(
    monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    composition = _composition_module()
    carrier, resolver, medications = _canonical_root_fixture(count)
    calls: list[str] = []
    handoffs = tuple(object() for _ in medications)
    release_projection = GuideRuntimeReleaseProjectionUnavailable()

    async def retrieve(request, *, dependencies):
        calls.append("#919")
        assert request.runtime_request is carrier
        return GuideMedicationGuidanceRetrievalOutcome(
            GuideMedicationGuidanceRetrievalDecision.READY, None, medications
        )

    async def hydrate(selections, *, reader):
        calls.append("#711")
        return GuideContentHydrationOutcome(
            GuideContentHydrationDecision.HYDRATED,
            (),
            (
                SimpleNamespace(
                    selection=SimpleNamespace(
                        hit=SimpleNamespace(
                            provenance=SimpleNamespace(knowledge_chunk_id=UUID("f0000000-0000-4000-8000-000000000001"))
                        )
                    ),
                ),
            ),
        )

    def handoff(request):
        calls.append("#760")
        return AuthoritativeGuideEvidenceAssemblyOutcome(
            AuthoritativeGuideEvidenceAssemblyDecision.BUILT,
            (),
            GuideEvidenceHandoffBuildOutcome(
                GuideEvidenceHandoffBuildDecision.BUILT,
                (),
                SimpleNamespace(handoff=handoffs[len([call for call in calls if call == "#760"]) - 1]),
            ),
        )

    def aggregate(entries):
        calls.append("aggregate")
        assert tuple(entry.medication_identity for entry in entries) == tuple(
            item.medication_identity for item in resolver.resolutions
        )
        return GuideAggregateEvidenceAssemblyOutcome(
            GuideAggregateEvidenceAssemblyDecision.ASSEMBLED, cast(object, object())
        )

    async def release(request, **dependencies):
        calls.append("#918")
        assert request.generation_request.aggregate is not None
        return release_projection

    monkeypatch.setattr(composition, "retrieve_medication_guidance", retrieve)
    monkeypatch.setattr(composition, "hydrate_guide_retrieval_content", hydrate)
    monkeypatch.setattr(composition, "assemble_authoritative_guide_evidence_handoff", handoff)
    monkeypatch.setattr(composition, "assemble_guide_aggregate_evidence", aggregate)
    monkeypatch.setattr(composition, "execute_guide_runtime_release", release)

    result = asyncio.run(
        composition.execute_canonical_guide_runtime(
            carrier,
            guide_preflight_request=cast(object, object()),
            evaluation_time=datetime(2026, 9, 21, tzinfo=UTC),
            retrieval_dependencies=cast(object, object()),
            medication_identity_resolver=resolver,
            content_reader=cast(object, object()),
            evidence_authority_reader=_AuthorityReader(),
            generator=cast(object, object()),
            decision_verifier=cast(object, object()),
            guard_reader=cast(object, object()),
            request_authority_reader=cast(object, object()),
            pin_reader=cast(object, object()),
            approval_reader=cast(object, object()),
            eligibility_reader=cast(object, object()),
            store=cast(object, object()),
        )
    )

    assert result is release_projection
    assert resolver.calls == 1
    assert calls == ["#919", *("#711", "#760") * count, "aggregate", "#918"]


def test_final_canonical_root_stops_after_blocked_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    composition = _composition_module()
    carrier, resolver, _ = _canonical_root_fixture(1)
    calls: list[str] = []

    async def retrieve(request, *, dependencies):
        calls.append("#919")
        return GuideMedicationGuidanceRetrievalOutcome(GuideMedicationGuidanceRetrievalDecision.BLOCKED, object())

    monkeypatch.setattr(composition, "retrieve_medication_guidance", retrieve)

    result = asyncio.run(
        composition.execute_canonical_guide_runtime(
            carrier,
            guide_preflight_request=cast(object, object()),
            evaluation_time=datetime(2026, 9, 21, tzinfo=UTC),
            retrieval_dependencies=cast(object, object()),
            medication_identity_resolver=resolver,
            content_reader=cast(object, object()),
            evidence_authority_reader=cast(object, object()),
            generator=cast(object, object()),
            decision_verifier=cast(object, object()),
            guard_reader=cast(object, object()),
            request_authority_reader=cast(object, object()),
            pin_reader=cast(object, object()),
            approval_reader=cast(object, object()),
            eligibility_reader=cast(object, object()),
            store=cast(object, object()),
        )
    )

    assert isinstance(result, GuideRuntimeReleaseProjectionUnavailable)
    assert resolver.calls == 0
    assert calls == ["#919"]
