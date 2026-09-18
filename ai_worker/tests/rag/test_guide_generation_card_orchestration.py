"""#787 Guide Generator -> Guideline Card orchestration regressions.

This seam sequences already merged kernels, so these tests do not re-verify #729
preflight, #760 assembly, #774 projection, #781 derivation or the #179 Card
finalizer; those suites own their own validation. What is verified here is the call
order, that the Generator runs exactly once and never before READY_FOR_GENERATION,
that a generation failure reaches the existing finalizer mapping without a synthetic
draft, and that an unbindable draft fails closed short of the finalizer.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from dataclasses import replace
from datetime import datetime

import pytest

from ai_worker.tasks.rag import guide_generation_card_orchestration
from ai_worker.tasks.rag.guide_generation_card_orchestration import (
    GuideGenerationCardDecision,
    GuideGenerationCardOrchestrationRequest,
    GuideGenerationCardOutcome,
    GuideGenerationCardStage,
    orchestrate_guide_generation_card,
)
from ai_worker.tasks.rag.guide_orchestration import (
    GuideOrchestrationDecision,
    GuideOrchestrationRequest,
    GuideOrchestrationStage,
)
from ai_worker.tasks.rag.guide_runtime_preflight import (
    GuideRuntimePreflightDecision,
    GuideRuntimePreflightReason,
    GuideRuntimePreflightRequest,
)
from ai_worker.tasks.rag.guideline_approval_pack import build_rag15_pending_approval_pack
from ai_worker.tasks.rag.guideline_card import (
    GuidelineCardDraft,
    GuidelineCardReason,
    GuidelineCardStatus,
    GuidelineFallbackCode,
    GuidelineGenerationFailure,
    GuidelineScope,
    MedicationIdentityRef,
    create_canonical_card_draft,
    create_canonical_claim_draft,
)
from ai_worker.tasks.rag.guideline_evidence_binding_authority import (
    GUIDELINE_APPROVAL_VERIFIER_REF,
    GuidelineAuthorityFailureReason,
)
from ai_worker.tasks.rag.guideline_generator import GuidelineGenerationRequest, GuidelineGenerationResult
from ai_worker.tasks.rag.guideline_production_evidence import (
    ProductionGuidelineEvidenceSet,
    project_guideline_evidence_from_handoff,
)

# #729/#760이 이미 확정한 production chain fixture를 그대로 재사용한다. 같은 합성
# 체인을 여기서 다시 만들면 두 벌의 fixture가 서로 어긋날 수 있다.
from ai_worker.tests.rag.test_authoritative_guide_evidence_handoff_assembly import _valid_request
from ai_worker.tests.rag.test_guide_runtime_preflight import (
    MODEL,
    SOURCE_REVISION,
    build_approved_pack,
    build_candidate_provenance,
)
from ai_worker.tests.rag.test_guideline_approval_pack import (
    SyntheticRag15DecisionVerifier,
    make_fallbacks,
    make_policy,
)
from ai_worker.tests.rag.test_guideline_card import citation_draft_for

MEDICATION = MedicationIdentityRef(
    prescription_version_medication_id="11111111-1111-4111-8111-111111111111",
    code_system="MFDS_ITEM_SEQ",
    canonical_code="SYNTHETIC-ITEM-001",
)


class RecordingGenerator:
    """Runtime Generator double: records every request and returns a scripted result.

    `generate_calls` is the guard the whole suite leans on — the Generator must run
    exactly once on a READY run and never on any pre-generation stop.
    """

    def __init__(self, result: GuidelineGenerationResult | object) -> None:
        self._result = result
        self._provenance = build_candidate_provenance(model=MODEL)
        self.generate_calls = 0
        self.requests: list[GuidelineGenerationRequest] = []

    @property
    def provenance(self):
        return self._provenance

    async def generate(self, request: GuidelineGenerationRequest):
        self.generate_calls += 1
        self.requests.append(request)
        return self._result


class DraftFromEvidenceGenerator(RecordingGenerator):
    """Returns a draft whose citations exact-match the production evidence it is given.

    A real Generator cites the evidence it was handed, so the draft is built from the
    request rather than pinned to a fixture that could drift from the #760 chain.
    """

    def __init__(self, *, scope: GuidelineScope = GuidelineScope.FOOD_CAUTION) -> None:
        super().__init__(None)
        self._scope = scope

    async def generate(self, request: GuidelineGenerationRequest):
        self._result = draft_citing(request.evidence, scope=self._scope)
        return await super().generate(request)


def draft_citing(
    evidence: ProductionGuidelineEvidenceSet,
    *,
    scope: GuidelineScope = GuidelineScope.FOOD_CAUTION,
) -> GuidelineCardDraft:
    return create_canonical_card_draft(
        (
            create_canonical_claim_draft(
                claim_key="claim-1",
                medication_identity=MEDICATION,
                scope=scope,
                citations=(citation_draft_for(evidence.selections[0]),),
            ),
        )
    )


def approved_preflight_request() -> tuple[GuideRuntimePreflightRequest, SyntheticRag15DecisionVerifier]:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    return GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks), verifier


def blocked_preflight_request() -> tuple[GuideRuntimePreflightRequest, SyntheticRag15DecisionVerifier]:
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


def run(
    request: GuideGenerationCardOrchestrationRequest,
    *,
    generator: RecordingGenerator,
    decision_verifier: SyntheticRag15DecisionVerifier,
) -> GuideGenerationCardOutcome:
    return asyncio.run(
        orchestrate_guide_generation_card(
            request,
            generator=generator,  # type: ignore[arg-type]
            decision_verifier=decision_verifier,
        )
    )


def approved_run(
    generator: RecordingGenerator,
    *,
    medications: tuple[MedicationIdentityRef, ...] = (MEDICATION,),
) -> GuideGenerationCardOutcome:
    preflight_request, verifier = approved_preflight_request()
    request = GuideGenerationCardOrchestrationRequest(
        upstream_request=GuideOrchestrationRequest(
            preflight_request=preflight_request,
            handoff_request=_valid_request(),
        ),
        medication_identities=medications,
    )
    return run(request, generator=generator, decision_verifier=verifier)


def expected_production_evidence() -> ProductionGuidelineEvidenceSet:
    """The #774 projection of the same #760 handoff the approved run builds."""
    from ai_worker.tasks.rag.authoritative_guide_evidence_handoff import (
        assemble_authoritative_guide_evidence_handoff,
    )

    outcome = assemble_authoritative_guide_evidence_handoff(_valid_request())
    assert outcome.build_outcome is not None
    assert outcome.build_outcome.handoff is not None
    return project_guideline_evidence_from_handoff(outcome.build_outcome.handoff)


# ==============================================================================
# A. Upstream stop — the Generator must not run
# ==============================================================================


def test_blocked_preflight_stops_upstream_without_calling_the_generator() -> None:
    preflight_request, verifier = blocked_preflight_request()
    generator = DraftFromEvidenceGenerator()
    request = GuideGenerationCardOrchestrationRequest(
        upstream_request=GuideOrchestrationRequest(
            preflight_request=preflight_request,
            handoff_request=_valid_request(),
        ),
        medication_identities=(MEDICATION,),
    )

    outcome = run(request, generator=generator, decision_verifier=verifier)

    assert outcome.decision is GuideGenerationCardDecision.STOPPED
    assert outcome.stopped_stage is GuideGenerationCardStage.UPSTREAM
    assert outcome.generation_result is None
    assert outcome.authority_outcome is None
    assert outcome.card_outcome is None
    assert generator.generate_calls == 0


def test_blocked_preflight_preserves_the_upstream_outcome_verbatim() -> None:
    preflight_request, verifier = blocked_preflight_request()
    generator = DraftFromEvidenceGenerator()
    request = GuideGenerationCardOrchestrationRequest(
        upstream_request=GuideOrchestrationRequest(
            preflight_request=preflight_request,
            handoff_request=_valid_request(),
        ),
        medication_identities=(MEDICATION,),
    )

    outcome = run(request, generator=generator, decision_verifier=verifier)

    upstream = outcome.upstream_outcome
    assert upstream.decision is GuideOrchestrationDecision.STOPPED
    assert upstream.stopped_stage is GuideOrchestrationStage.RUNTIME_PREFLIGHT
    assert upstream.preflight_outcome is not None
    assert upstream.preflight_outcome.decision is GuideRuntimePreflightDecision.BLOCKED
    assert upstream.preflight_outcome.reason is GuideRuntimePreflightReason.APPROVAL_PACK_NOT_CONSUMABLE


def test_rejected_handoff_stops_upstream_without_calling_the_generator() -> None:
    preflight_request, verifier = approved_preflight_request()
    generator = DraftFromEvidenceGenerator()
    request = GuideGenerationCardOrchestrationRequest(
        upstream_request=GuideOrchestrationRequest(
            preflight_request=preflight_request,
            # A naive evaluation timestamp is rejected by the #760 assembly request guard.
            handoff_request=replace(_valid_request(), evaluated_at=datetime(2026, 9, 17, 12, 0, 0)),
        ),
        medication_identities=(MEDICATION,),
    )

    outcome = run(request, generator=generator, decision_verifier=verifier)

    assert outcome.decision is GuideGenerationCardDecision.STOPPED
    assert outcome.stopped_stage is GuideGenerationCardStage.UPSTREAM
    assert outcome.upstream_outcome.stopped_stage is GuideOrchestrationStage.AUTHORITATIVE_EVIDENCE_HANDOFF
    assert outcome.card_outcome is None
    assert generator.generate_calls == 0


def test_foreign_upstream_request_stops_at_slice_1_request_guard() -> None:
    generator = DraftFromEvidenceGenerator()
    _, verifier = approved_preflight_request()
    request = GuideGenerationCardOrchestrationRequest(
        upstream_request=object(),  # type: ignore[arg-type]
        medication_identities=(MEDICATION,),
    )

    outcome = run(request, generator=generator, decision_verifier=verifier)

    assert outcome.stopped_stage is GuideGenerationCardStage.UPSTREAM
    assert outcome.upstream_outcome.stopped_stage is GuideOrchestrationStage.REQUEST
    assert generator.generate_calls == 0


# ==============================================================================
# B. Success — READY -> #774 evidence -> Generator -> #781 -> finalizer
# ==============================================================================


def test_ready_run_generates_a_card_through_the_dynamic_authority() -> None:
    generator = DraftFromEvidenceGenerator()

    outcome = approved_run(generator)

    assert outcome.decision is GuideGenerationCardDecision.COMPLETED
    assert outcome.stopped_stage is None
    assert outcome.upstream_outcome.decision is GuideOrchestrationDecision.READY_FOR_GENERATION
    assert outcome.card_outcome is not None
    assert outcome.card_outcome.status is GuidelineCardStatus.GENERATED
    assert outcome.card_outcome.reason is GuidelineCardReason.CARD_GENERATED
    assert outcome.card_outcome.card is not None
    assert generator.generate_calls == 1


def test_ready_run_consumes_the_774_projection_as_the_generation_evidence() -> None:
    generator = DraftFromEvidenceGenerator()

    outcome = approved_run(generator)

    # `SensitiveText` compares by identity, so the two projections are compared on the
    # coordinates that actually bind a citation rather than with `==` on the whole set.
    expected = expected_production_evidence()
    actual = generator.requests[0].evidence
    assert actual.evaluated_at == expected.evaluated_at
    assert actual.handoff_sha256 == expected.handoff_sha256
    assert [(item.evidence_key, item.locator, item.content_sha256) for item in actual.selections] == [
        (item.evidence_key, item.locator, item.content_sha256) for item in expected.selections
    ]
    assert outcome.upstream_outcome.ready_inputs is not None
    handoff = outcome.upstream_outcome.ready_inputs.evidence_handoff
    assert actual.handoff_sha256 == handoff.handoff_sha256
    assert len(actual.selections) == len(handoff.selections)


def test_generation_request_reuses_the_preflight_policy_and_pinned_medications() -> None:
    preflight_request, verifier = approved_preflight_request()
    generator = DraftFromEvidenceGenerator()
    request = GuideGenerationCardOrchestrationRequest(
        upstream_request=GuideOrchestrationRequest(
            preflight_request=preflight_request,
            handoff_request=_valid_request(),
        ),
        medication_identities=(MEDICATION,),
    )

    outcome = run(request, generator=generator, decision_verifier=verifier)

    assert outcome.decision is GuideGenerationCardDecision.COMPLETED
    generation_request = generator.requests[0]
    assert generation_request.policy is preflight_request.policy
    assert generation_request.medication_identities == (MEDICATION,)


def test_successful_run_carries_the_781_authority_outcome() -> None:
    generator = DraftFromEvidenceGenerator()

    outcome = approved_run(generator)

    assert outcome.authority_outcome is not None
    assert outcome.authority_outcome.reason is None
    authority = outcome.authority_outcome.authority
    assert authority is not None
    assert len(authority.bindings) == 1
    assert authority.bindings[0].medication_identity == MEDICATION


def test_card_provenance_is_the_729_ready_runtime_provenance() -> None:
    generator = DraftFromEvidenceGenerator()

    outcome = approved_run(generator)

    assert outcome.card_outcome is not None
    card = outcome.card_outcome.card
    assert card is not None
    # `GuidelineCard` carries the Card-level provenance, which extends the #729 runtime
    # generation provenance with the policy refs the finalizer verified.
    runtime_provenance = generator.provenance
    assert card.provenance.prompt_ref == runtime_provenance.prompt_ref
    assert card.provenance.model_ref == runtime_provenance.model_ref
    assert card.provenance.parser_ref == runtime_provenance.parser_ref
    assert card.provenance.validator_ref == runtime_provenance.validator_ref

    preflight_outcome = outcome.upstream_outcome.preflight_outcome
    assert preflight_outcome is not None
    assert preflight_outcome.ready_context is not None
    assert card.provenance.guideline_policy_ref == preflight_outcome.ready_context.policy_ref
    assert card.provenance.guideline_policy_verifier_ref == GUIDELINE_APPROVAL_VERIFIER_REF


# ==============================================================================
# C/D. Generation failure — straight to the existing finalizer mapping
# ==============================================================================


@pytest.mark.parametrize(
    ("failure", "status", "reason", "fallback_code"),
    [
        (
            GuidelineGenerationFailure.PROVIDER_TIMEOUT,
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.PROVIDER_TIMEOUT,
            GuidelineFallbackCode.PROVIDER_TIMEOUT,
        ),
        (
            GuidelineGenerationFailure.DEPENDENCY_UNAVAILABLE,
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.DEPENDENCY_UNAVAILABLE,
            GuidelineFallbackCode.DEPENDENCY_UNAVAILABLE,
        ),
    ],
)
def test_generation_failure_reaches_the_existing_finalizer_mapping_unchanged(
    failure: GuidelineGenerationFailure,
    status: GuidelineCardStatus,
    reason: GuidelineCardReason,
    fallback_code: GuidelineFallbackCode,
) -> None:
    generator = RecordingGenerator(failure)

    outcome = approved_run(generator)

    assert outcome.decision is GuideGenerationCardDecision.COMPLETED
    assert generator.generate_calls == 1
    assert outcome.generation_result is failure
    assert outcome.card_outcome is not None
    assert outcome.card_outcome.status is status
    assert outcome.card_outcome.reason is reason
    assert outcome.card_outcome.fallback_code is fallback_code
    assert outcome.card_outcome.fallback is not None


def test_generation_failure_never_builds_a_draft_or_a_dynamic_binding() -> None:
    generator = RecordingGenerator(GuidelineGenerationFailure.PROVIDER_TIMEOUT)

    outcome = approved_run(generator)

    # No synthetic draft was fabricated to reuse the success path, and the dynamic
    # binding authority was never consulted for a request that has no draft.
    assert outcome.authority_outcome is None
    assert outcome.card_outcome is not None
    assert outcome.card_outcome.card is None


def test_generation_failure_fallback_is_verified_against_the_729_static_pins() -> None:
    generator = RecordingGenerator(GuidelineGenerationFailure.PROVIDER_TIMEOUT)

    outcome = approved_run(generator)

    preflight_outcome = outcome.upstream_outcome.preflight_outcome
    assert preflight_outcome is not None
    ready_context = preflight_outcome.ready_context
    assert ready_context is not None
    assert outcome.card_outcome is not None
    fallback = outcome.card_outcome.fallback
    assert fallback is not None
    assert fallback.artifact_ref in ready_context.fallback_refs


def test_foreign_generator_result_stops_at_generation_without_a_card() -> None:
    generator = RecordingGenerator(object())

    outcome = approved_run(generator)

    assert outcome.decision is GuideGenerationCardDecision.STOPPED
    assert outcome.stopped_stage is GuideGenerationCardStage.GENERATION
    assert outcome.card_outcome is None
    assert outcome.authority_outcome is None


# ==============================================================================
# E. Dynamic authority failure — fail closed before the finalizer
# ==============================================================================


def test_draft_citing_unpinned_medication_stops_before_the_finalizer() -> None:
    """#781 refuses the draft, so this slice stops rather than answering with a fallback."""
    generator = DraftFromEvidenceGenerator()
    unpinned = MedicationIdentityRef(
        prescription_version_medication_id="22222222-2222-4222-8222-222222222222",
        code_system="MFDS_ITEM_SEQ",
        canonical_code="SYNTHETIC-ITEM-002",
    )

    outcome = approved_run(generator, medications=(unpinned,))

    assert outcome.decision is GuideGenerationCardDecision.STOPPED
    assert outcome.stopped_stage is GuideGenerationCardStage.DYNAMIC_BINDING_AUTHORITY
    assert outcome.authority_outcome is not None
    assert outcome.authority_outcome.authority is None
    assert outcome.authority_outcome.reason is GuidelineAuthorityFailureReason.MEDICATION_NOT_PINNED
    assert outcome.card_outcome is None
    assert generator.generate_calls == 1


def test_draft_citing_evidence_outside_the_production_set_stops_before_the_finalizer() -> None:
    evidence_set = expected_production_evidence()
    mismatched = replace(
        draft_citing(evidence_set).claims[0].citations[0],
        content_sha256="b" * 64,
    )
    draft = create_canonical_card_draft(
        (
            replace(
                draft_citing(evidence_set).claims[0],
                citations=(mismatched,),
            ),
        )
    )
    generator = RecordingGenerator(draft)

    outcome = approved_run(generator)

    assert outcome.stopped_stage is GuideGenerationCardStage.DYNAMIC_BINDING_AUTHORITY
    assert outcome.authority_outcome is not None
    assert outcome.authority_outcome.reason is GuidelineAuthorityFailureReason.CITATION_MISMATCH
    assert outcome.card_outcome is None


# ==============================================================================
# Containment guards
# ==============================================================================


def _imported_module_roots(path: str) -> set[str]:
    """Every module this file actually imports, from the AST only.

    Docstrings and comments are excluded on purpose: this guard is about the real
    import graph, not about prose that names a forbidden dependency.
    """
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


ORCHESTRATION_PATH = "ai_worker/tasks/rag/guide_generation_card_orchestration.py"


def test_orchestration_has_no_persistence_or_legacy_gate_dependency() -> None:
    for module in _imported_module_roots(ORCHESTRATION_PATH):
        root = module.split(".")[0]
        assert root not in {"backend", "openai", "rag_runtime", "sqlalchemy"}
        assert "evidence_gate" not in module
        assert "repository" not in module


def test_orchestration_does_not_duplicate_the_finalizer_failure_mapping() -> None:
    """Every `GuidelineGenerationFailure` member must be absent from this module's code.

    Naming even one of them here would mean a second mapping table competing with
    `finalize_guideline_card()`. The check is AST-based so the module docstring, which
    lists the members precisely to say they are NOT mapped here, does not trip it.
    """
    tree = ast.parse(pathlib.Path(ORCHESTRATION_PATH).read_text(encoding="utf-8"))
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    for member in GuidelineGenerationFailure:
        assert member.name not in attributes


def test_orchestration_does_not_reach_citation_authorization_or_release() -> None:
    tree = ast.parse(pathlib.Path(ORCHESTRATION_PATH).read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names.update(node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute))
    names.update(_imported_module_roots(ORCHESTRATION_PATH))

    for forbidden in (
        "ClaimCitationCandidateSet",
        "ClaimSupportVerificationReceipt",
        "CitationAuthorizationReceipt",
        "AuthorizedCitationSelection",
        "DiscardGeneratedContent",
    ):
        assert forbidden not in names
    for module in _imported_module_roots(ORCHESTRATION_PATH):
        assert "citation" not in module


def test_slice_1_module_still_stops_before_the_generator_and_the_card() -> None:
    """#787 must not pull the Generator or Card contracts into the Slice 1 module."""
    modules = _imported_module_roots("ai_worker/tasks/rag/guide_orchestration.py")

    assert "ai_worker.tasks.rag.guideline_generator" not in modules
    assert "ai_worker.tasks.rag.guideline_card" not in modules
    assert "ai_worker.tasks.rag.guideline_evidence_binding_authority" not in modules
    assert "ai_worker.tasks.rag.guideline_production_evidence" not in modules


def test_orchestration_defines_no_public_runtime_status() -> None:
    for forbidden in ("PASS", "LIMITED", "REJECTED", "STALE", "PUBLIC_TRACK_F"):
        assert forbidden not in [member.name for member in GuideGenerationCardDecision]
        assert forbidden not in [member.name for member in GuideGenerationCardStage]
    assert set(guide_generation_card_orchestration.__all__) == {
        "GuideGenerationCardDecision",
        "GuideGenerationCardOrchestrationRequest",
        "GuideGenerationCardOutcome",
        "GuideGenerationCardStage",
        "orchestrate_guide_generation_card",
    }
