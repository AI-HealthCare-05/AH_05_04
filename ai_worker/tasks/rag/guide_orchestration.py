"""Guide Runtime Orchestration — preflight -> authoritative evidence handoff (#180).

#729 `preflight_guide_runtime()`와 #760 `assemble_authoritative_guide_evidence_handoff()`를
순서대로 연결하고, 둘 다 성공했을 때 두 상류 산출물을 손실 없이 한 객체로 묶어 멈추는
sequencing seam입니다.

Stops before RAG-15 Generator.

Blocked by:
- RAG-15 Production Evidence Input Contract Alignment
- Dynamic Guideline Evidence Binding Authority

Does not define runtime release/fallback mapping.

Scope & Authority Boundaries:
- Sequencing Only: 상류 kernel의 검증을 재구현하거나 재실행하지 않습니다. 각 단계의 실패는
  원본 `GuideRuntimePreflightOutcome` / `AuthoritativeGuideEvidenceAssemblyOutcome`을 그대로
  실어 보내며, 그 안의 `GuideEvidenceHandoffBuildOutcome`과 `GuideEvidenceHandoffReason`도
  재해석하지 않습니다.
- Fail-Fast Short Circuit: preflight가 BLOCKED이면 handoff assembly를 호출하지 않습니다.
  assembly가 REJECTED이면 그 지점에서 멈춥니다. Generator는 어느 경로에서도 실행하지 않으며,
  `generator`는 #729가 요구하는 runtime provenance 확인 용도로만 주입받습니다.
- No Public Runtime Status: `PASS | LIMITED | REJECTED | STALE`와 `GuidelineFallbackCode`로의
  mapping은 아직 승인된 정본이 없으므로 여기서 만들지 않습니다. 이 모듈의
  `GuideOrchestrationDecision`은 orchestration-local 상태입니다.
- No New Domain: 새 evidence DTO, policy, receipt, hash domain을 만들지 않습니다. 성공 결과는
  상류가 만든 `ReadyGuideRuntimeContext`와 `VerifiedGuideEvidenceHandoff` 객체를 그대로
  참조합니다.
- Pure Boundary: DB, network, clock, persistence I/O가 없습니다. `backend.*`, `sqlalchemy.*`를
  import하지 않고, handoff evaluation timestamp는 caller가 #760 request에 담아 전달합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ai_worker.tasks.rag.authoritative_guide_evidence_handoff import (
    AuthoritativeGuideEvidenceAssemblyDecision,
    AuthoritativeGuideEvidenceAssemblyOutcome,
    AuthoritativeGuideEvidenceHandoffAssemblyRequest,
    assemble_authoritative_guide_evidence_handoff,
)
from ai_worker.tasks.rag.guide_evidence_handoff import VerifiedGuideEvidenceHandoff
from ai_worker.tasks.rag.guide_runtime_preflight import (
    GuideRuntimePreflightDecision,
    GuideRuntimePreflightOutcome,
    GuideRuntimePreflightRequest,
    ReadyGuideRuntimeContext,
    RuntimeGuidelineGeneratorPort,
    preflight_guide_runtime,
)
from ai_worker.tasks.rag.guideline_approval_pack import Rag15ApprovalDecisionVerifierPort

__all__ = [
    "GuideOrchestrationDecision",
    "GuideOrchestrationOutcome",
    "GuideOrchestrationRequest",
    "GuideOrchestrationStage",
    "ReadyGuideGenerationInputs",
    "orchestrate_guide_preflight_handoff",
]


class GuideOrchestrationDecision(StrEnum):
    """#180-local sequencing state. Not a public Guide runtime or Job state."""

    READY_FOR_GENERATION = "READY_FOR_GENERATION"
    STOPPED = "STOPPED"


class GuideOrchestrationStage(StrEnum):
    """The stage a stopped run did not get past."""

    REQUEST = "REQUEST"
    RUNTIME_PREFLIGHT = "RUNTIME_PREFLIGHT"
    AUTHORITATIVE_EVIDENCE_HANDOFF = "AUTHORITATIVE_EVIDENCE_HANDOFF"


@dataclass(frozen=True, slots=True)
class GuideOrchestrationRequest:
    preflight_request: GuideRuntimePreflightRequest
    handoff_request: AuthoritativeGuideEvidenceHandoffAssemblyRequest


@dataclass(frozen=True, slots=True)
class ReadyGuideGenerationInputs:
    """The two upstream results a later Generator step will need, unchanged.

    This is NOT a `GuidelineGenerationRequest`: the production evidence input type
    is owned by the RAG-15 Production Evidence Input Contract Alignment work and is
    not decided yet. It is NOT public release readiness and NOT citation
    authorization either; neither the Guideline Card nor any Citation stage has run.
    """

    runtime_context: ReadyGuideRuntimeContext
    evidence_handoff: VerifiedGuideEvidenceHandoff


@dataclass(frozen=True, slots=True)
class GuideOrchestrationOutcome:
    """Carries each stage's own outcome verbatim.

    `preflight_outcome` and `assembly_outcome` are `None` exactly when that stage
    was never reached, so a caller can tell a short circuit from a rejection.
    """

    decision: GuideOrchestrationDecision
    stopped_stage: GuideOrchestrationStage | None
    preflight_outcome: GuideRuntimePreflightOutcome | None
    assembly_outcome: AuthoritativeGuideEvidenceAssemblyOutcome | None
    ready_inputs: ReadyGuideGenerationInputs | None


def _request_shape_is_valid(request: GuideOrchestrationRequest) -> bool:
    """입력이 두 상류 kernel의 정본 request 타입인지만 확인한다.

    각 request의 내용 검증은 해당 kernel이 계속 소유하므로 여기서 복제하지 않는다.
    """
    return (
        type(request) is GuideOrchestrationRequest
        and type(request.preflight_request) is GuideRuntimePreflightRequest
        and type(request.handoff_request) is AuthoritativeGuideEvidenceHandoffAssemblyRequest
    )


def _stopped(
    stage: GuideOrchestrationStage,
    *,
    preflight_outcome: GuideRuntimePreflightOutcome | None = None,
    assembly_outcome: AuthoritativeGuideEvidenceAssemblyOutcome | None = None,
) -> GuideOrchestrationOutcome:
    return GuideOrchestrationOutcome(
        decision=GuideOrchestrationDecision.STOPPED,
        stopped_stage=stage,
        preflight_outcome=preflight_outcome,
        assembly_outcome=assembly_outcome,
        ready_inputs=None,
    )


def orchestrate_guide_preflight_handoff(
    request: GuideOrchestrationRequest,
    *,
    generator: RuntimeGuidelineGeneratorPort,
    decision_verifier: Rag15ApprovalDecisionVerifierPort,
) -> GuideOrchestrationOutcome:
    """Run #729 preflight, then #760 assembly, and stop at the generation boundary.

    Never calls ``generator.generate``. ``generator`` is injected only because #729
    binds the approved candidate to the provenance the runtime generator would
    actually execute with.

    READY_FOR_GENERATION means the RAG-15 static runtime candidate is approved and
    exact-bound, and the authoritative Guide evidence handoff was built. It does not
    mean a Card was produced, citations were authorized, or a release was approved.
    """
    # Phase 1 — request shape
    if not _request_shape_is_valid(request):
        return _stopped(GuideOrchestrationStage.REQUEST)

    # Phase 2 — RAG-15 static runtime candidate (#729)
    preflight_outcome = preflight_guide_runtime(
        request.preflight_request,
        generator=generator,
        decision_verifier=decision_verifier,
    )
    if preflight_outcome.decision is not GuideRuntimePreflightDecision.READY or preflight_outcome.ready_context is None:
        return _stopped(
            GuideOrchestrationStage.RUNTIME_PREFLIGHT,
            preflight_outcome=preflight_outcome,
        )

    # Phase 3 — authoritative Guide evidence handoff (#760)
    assembly_outcome = assemble_authoritative_guide_evidence_handoff(request.handoff_request)
    build_outcome = assembly_outcome.build_outcome
    if (
        assembly_outcome.decision is not AuthoritativeGuideEvidenceAssemblyDecision.BUILT
        or build_outcome is None
        or build_outcome.handoff is None
    ):
        return _stopped(
            GuideOrchestrationStage.AUTHORITATIVE_EVIDENCE_HANDOFF,
            preflight_outcome=preflight_outcome,
            assembly_outcome=assembly_outcome,
        )

    return GuideOrchestrationOutcome(
        decision=GuideOrchestrationDecision.READY_FOR_GENERATION,
        stopped_stage=None,
        preflight_outcome=preflight_outcome,
        assembly_outcome=assembly_outcome,
        ready_inputs=ReadyGuideGenerationInputs(
            runtime_context=preflight_outcome.ready_context,
            evidence_handoff=build_outcome.handoff,
        ),
    )
