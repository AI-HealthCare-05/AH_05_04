"""#180 Guide orchestration preflight -> handoff sequencing regressions.

The seam under test only sequences the two already merged kernels, so these
tests deliberately do not re-verify #729 preflight validation or #760 assembly
validation; `test_guide_runtime_preflight.py` and
`test_authoritative_guide_evidence_handoff_assembly.py` own those. What is
verified here is call ordering, short-circuiting, and that no upstream object
is reconstructed or downgraded on the way through.

The Generator must never run in any of these tests.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from ai_worker.tasks.rag import guide_orchestration
from ai_worker.tasks.rag.authoritative_guide_evidence_handoff import (
    AuthoritativeGuideEvidenceAssemblyDecision,
    AuthoritativeGuideEvidenceAssemblyOutcome,
    AuthoritativeGuideEvidenceAssemblyReason,
    AuthoritativeGuideEvidenceHandoffAssemblyRequest,
)
from ai_worker.tasks.rag.guide_evidence_handoff import (
    GuideEvidenceHandoffBuildDecision,
    GuideEvidenceHandoffReason,
)
from ai_worker.tasks.rag.guide_orchestration import (
    GuideOrchestrationDecision,
    GuideOrchestrationOutcome,
    GuideOrchestrationRequest,
    GuideOrchestrationStage,
    orchestrate_guide_preflight_handoff,
)
from ai_worker.tasks.rag.guide_runtime_preflight import (
    GuideRuntimePreflightDecision,
    GuideRuntimePreflightReason,
    GuideRuntimePreflightRequest,
)
from ai_worker.tasks.rag.guideline_approval_pack import build_rag15_pending_approval_pack

# #760/#729가 이미 확정한 production chain fixture를 그대로 재사용한다. 같은 합성
# 체인을 여기서 다시 만들면 두 벌의 fixture가 서로 어긋날 수 있다.
from ai_worker.tests.rag.test_authoritative_guide_evidence_handoff_assembly import (
    _authority,
    _valid_request,
)
from ai_worker.tests.rag.test_guide_runtime_preflight import (
    MODEL,
    SOURCE_REVISION,
    SyntheticRuntimeGuidelineGenerator,
    build_approved_pack,
    make_generator,
)
from ai_worker.tests.rag.test_guideline_approval_pack import (
    SyntheticRag15DecisionVerifier,
    make_fallbacks,
    make_policy,
)


class _CountingAssembly:
    """Counts how many times the orchestrator reached the #760 kernel."""

    def __init__(self) -> None:
        self.calls = 0


@pytest.fixture
def counted_assembly(monkeypatch: pytest.MonkeyPatch) -> _CountingAssembly:
    counter = _CountingAssembly()
    real = guide_orchestration.assemble_authoritative_guide_evidence_handoff

    def _delegate(
        request: AuthoritativeGuideEvidenceHandoffAssemblyRequest,
    ) -> AuthoritativeGuideEvidenceAssemblyOutcome:
        counter.calls += 1
        return real(request)

    monkeypatch.setattr(guide_orchestration, "assemble_authoritative_guide_evidence_handoff", _delegate)
    return counter


def _approved_preflight() -> tuple[GuideRuntimePreflightRequest, SyntheticRag15DecisionVerifier]:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    return GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks), verifier


def _blocked_preflight() -> tuple[GuideRuntimePreflightRequest, SyntheticRag15DecisionVerifier]:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model=MODEL,
        policy=policy,
        fallbacks=fallbacks,
        source_revision=SOURCE_REVISION,
    )
    return (
        GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks),
        SyntheticRag15DecisionVerifier(),
    )


def _run(
    request: GuideOrchestrationRequest,
    *,
    generator: SyntheticRuntimeGuidelineGenerator,
    decision_verifier: SyntheticRag15DecisionVerifier,
) -> GuideOrchestrationOutcome:
    return orchestrate_guide_preflight_handoff(
        request,
        generator=generator,
        decision_verifier=decision_verifier,
    )


# ==============================================================================
# Phase 1: request shape
# ==============================================================================


@pytest.mark.parametrize(
    "field",
    ["preflight_request", "handoff_request"],
)
def test_foreign_request_member_stops_before_preflight(
    field: str,
    counted_assembly: _CountingAssembly,
) -> None:
    preflight_request, verifier = _approved_preflight()
    generator = make_generator()
    members: dict[str, Any] = {
        "preflight_request": preflight_request,
        "handoff_request": _valid_request(),
    }
    members[field] = object()
    request = GuideOrchestrationRequest(**members)

    outcome = _run(request, generator=generator, decision_verifier=verifier)

    assert outcome.decision is GuideOrchestrationDecision.STOPPED
    assert outcome.stopped_stage is GuideOrchestrationStage.REQUEST
    assert outcome.preflight_outcome is None
    assert outcome.assembly_outcome is None
    assert outcome.ready_inputs is None
    assert counted_assembly.calls == 0
    assert generator.generate_calls == 0


def test_foreign_request_object_stops_before_preflight(counted_assembly: _CountingAssembly) -> None:
    _, verifier = _approved_preflight()
    generator = make_generator()

    outcome = orchestrate_guide_preflight_handoff(
        object(),  # type: ignore[arg-type]
        generator=generator,
        decision_verifier=verifier,
    )

    assert outcome.decision is GuideOrchestrationDecision.STOPPED
    assert outcome.stopped_stage is GuideOrchestrationStage.REQUEST
    assert counted_assembly.calls == 0
    assert generator.generate_calls == 0


# ==============================================================================
# Phase 2: preflight short-circuit
# ==============================================================================


def test_blocked_preflight_stops_before_any_assembly(counted_assembly: _CountingAssembly) -> None:
    preflight_request, verifier = _blocked_preflight()
    generator = make_generator()
    request = GuideOrchestrationRequest(
        preflight_request=preflight_request,
        handoff_request=_valid_request(),
    )

    outcome = _run(request, generator=generator, decision_verifier=verifier)

    assert outcome.decision is GuideOrchestrationDecision.STOPPED
    assert outcome.stopped_stage is GuideOrchestrationStage.RUNTIME_PREFLIGHT
    assert outcome.assembly_outcome is None
    assert outcome.ready_inputs is None
    assert counted_assembly.calls == 0
    assert generator.generate_calls == 0


def test_blocked_preflight_preserves_the_original_preflight_reason() -> None:
    preflight_request, verifier = _blocked_preflight()
    generator = make_generator()
    request = GuideOrchestrationRequest(
        preflight_request=preflight_request,
        handoff_request=_valid_request(),
    )

    outcome = _run(request, generator=generator, decision_verifier=verifier)

    assert outcome.preflight_outcome is not None
    assert outcome.preflight_outcome.decision is GuideRuntimePreflightDecision.BLOCKED
    assert outcome.preflight_outcome.reason is GuideRuntimePreflightReason.APPROVAL_PACK_NOT_CONSUMABLE
    assert outcome.preflight_outcome.ready_context is None


# ==============================================================================
# Phase 3: assembly short-circuit
# ==============================================================================


def test_rejected_assembly_stops_and_preserves_the_assembly_reason(
    counted_assembly: _CountingAssembly,
) -> None:
    preflight_request, verifier = _approved_preflight()
    generator = make_generator()
    naive = datetime(2026, 9, 17, 12, 0, 0)
    request = GuideOrchestrationRequest(
        preflight_request=preflight_request,
        handoff_request=replace(_valid_request(), evaluated_at=naive),
    )

    outcome = _run(request, generator=generator, decision_verifier=verifier)

    assert outcome.decision is GuideOrchestrationDecision.STOPPED
    assert outcome.stopped_stage is GuideOrchestrationStage.AUTHORITATIVE_EVIDENCE_HANDOFF
    assert outcome.assembly_outcome is not None
    assert outcome.assembly_outcome.decision is AuthoritativeGuideEvidenceAssemblyDecision.REJECTED
    assert outcome.assembly_outcome.reasons == (AuthoritativeGuideEvidenceAssemblyReason.REQUEST_INVALID,)
    assert outcome.assembly_outcome.build_outcome is None
    assert outcome.ready_inputs is None
    assert counted_assembly.calls == 1
    assert generator.generate_calls == 0


def test_handoff_rejection_preserves_the_underlying_build_outcome() -> None:
    preflight_request, verifier = _approved_preflight()
    generator = make_generator()
    handoff_request = _valid_request()
    expired = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
    authorities = tuple(
        _authority(hydrated.selection.hit, evaluated_at=expired) for hydrated in handoff_request.hydrated_selections
    )
    request = GuideOrchestrationRequest(
        preflight_request=preflight_request,
        handoff_request=replace(handoff_request, authorities=authorities),
    )

    outcome = _run(request, generator=generator, decision_verifier=verifier)

    assert outcome.decision is GuideOrchestrationDecision.STOPPED
    assert outcome.stopped_stage is GuideOrchestrationStage.AUTHORITATIVE_EVIDENCE_HANDOFF
    assert outcome.assembly_outcome is not None
    assert outcome.assembly_outcome.reasons == (AuthoritativeGuideEvidenceAssemblyReason.HANDOFF_REJECTED,)
    build_outcome = outcome.assembly_outcome.build_outcome
    assert build_outcome is not None
    assert build_outcome.decision is GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.ASSESSMENT_EXPIRED in build_outcome.reasons
    assert build_outcome.handoff is None
    assert generator.generate_calls == 0


# ==============================================================================
# Phase 4: READY_FOR_GENERATION boundary
# ==============================================================================


def test_approved_preflight_and_built_handoff_reach_the_generation_boundary(
    counted_assembly: _CountingAssembly,
) -> None:
    preflight_request, verifier = _approved_preflight()
    generator = make_generator()
    request = GuideOrchestrationRequest(
        preflight_request=preflight_request,
        handoff_request=_valid_request(),
    )

    outcome = _run(request, generator=generator, decision_verifier=verifier)

    assert outcome.decision is GuideOrchestrationDecision.READY_FOR_GENERATION
    assert outcome.stopped_stage is None
    assert outcome.ready_inputs is not None
    assert counted_assembly.calls == 1
    assert generator.generate_calls == 0


def test_ready_inputs_carry_the_exact_preflight_runtime_context() -> None:
    preflight_request, verifier = _approved_preflight()
    generator = make_generator()
    request = GuideOrchestrationRequest(
        preflight_request=preflight_request,
        handoff_request=_valid_request(),
    )

    outcome = _run(request, generator=generator, decision_verifier=verifier)

    assert outcome.preflight_outcome is not None
    expected = outcome.preflight_outcome.ready_context
    assert expected is not None
    assert outcome.ready_inputs is not None
    assert outcome.ready_inputs.runtime_context is expected
    assert outcome.ready_inputs.runtime_context.generation_provenance == generator.provenance
    assert outcome.ready_inputs.runtime_context.policy_ref == preflight_request.policy.artifact_ref


def test_ready_inputs_carry_the_exact_verified_handoff() -> None:
    preflight_request, verifier = _approved_preflight()
    generator = make_generator()
    handoff_request = _valid_request()
    request = GuideOrchestrationRequest(
        preflight_request=preflight_request,
        handoff_request=handoff_request,
    )

    outcome = _run(request, generator=generator, decision_verifier=verifier)

    assert outcome.assembly_outcome is not None
    build_outcome = outcome.assembly_outcome.build_outcome
    assert build_outcome is not None
    assert build_outcome.handoff is not None
    assert outcome.ready_inputs is not None
    assert outcome.ready_inputs.evidence_handoff is build_outcome.handoff
    assert len(outcome.ready_inputs.evidence_handoff.selections) == len(handoff_request.hydrated_selections)


# ==============================================================================
# Blocker A / B containment
# ==============================================================================


def test_module_does_not_reach_into_the_blocked_rag15_or_binding_contracts() -> None:
    """A and B own these names; this seam must not anticipate either contract."""
    source = inspect.getsource(guide_orchestration)

    for forbidden in (
        "EvidenceGateOutcome",
        "GatePassedKnowledgeEvidenceSelection",
        "canonical_gate_selection_hash",
        "selection_projection_sha256",
        "GuidelineGenerationRequest",
        "ApprovedGuidelineEvidenceBinding",
        "GuidelineApprovalVerifierPort",
        "finalize_guideline_card",
    ):
        assert f"{forbidden}(" not in source
        assert f"import {forbidden}" not in source


def test_module_stays_pure() -> None:
    source = inspect.getsource(guide_orchestration)

    assert "import backend" not in source
    assert "from backend" not in source
    assert "import sqlalchemy" not in source
    assert "from sqlalchemy" not in source
    assert "datetime.now" not in source
    assert "utcnow" not in source
