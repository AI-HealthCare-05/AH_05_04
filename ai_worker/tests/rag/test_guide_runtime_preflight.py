"""#180 Guide Runtime Preflight fail-closed regression tests.

READY here means only that the RAG-15 static runtime candidate is formally
approved and exact-bound to the currently configured Generator, Policy, and
Fallback Set. It is not Guide evidence readiness.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.guide_runtime_preflight import (
    GuideRuntimePreflightDecision,
    GuideRuntimePreflightOutcome,
    GuideRuntimePreflightReason,
    GuideRuntimePreflightRequest,
    RuntimeGuidelineGeneratorPort,
    preflight_guide_runtime,
)
from ai_worker.tasks.rag.guideline_approval_pack import (
    Rag15ApprovalPack,
    Rag15FallbackPin,
    build_rag15_approval_pack,
    build_rag15_pending_approval_pack,
    compute_rag15_candidate_ref,
    create_rag15_approval_evidence,
)
from ai_worker.tasks.rag.guideline_card import (
    ApprovedGuidelineFallback,
    GuidelineFallbackCode,
    GuidelineGenerationProvenance,
    VersionedGuidelinePolicy,
    create_canonical_guideline_policy,
)
from ai_worker.tasks.rag.guideline_generator import (
    GuidelineGenerationRequest,
    GuidelineGenerationResult,
)
from ai_worker.tasks.rag.guideline_generator_prompt import build_candidate_provenance
from ai_worker.tests.rag.test_guideline_approval_pack import (
    SyntheticRag15DecisionVerifier,
    make_approved_evidence,
    make_fallbacks,
    make_policy,
)

MODEL = "gpt-4o-mini"
SOURCE_REVISION = "rev-180-preflight"


class SyntheticRuntimeGuidelineGenerator:
    """Runtime generator double exposing provenance and counting generate() calls."""

    def __init__(self, provenance: GuidelineGenerationProvenance) -> None:
        self._provenance = provenance
        self.generate_calls = 0

    @property
    def provenance(self) -> GuidelineGenerationProvenance:
        return self._provenance

    async def generate(self, request: GuidelineGenerationRequest) -> GuidelineGenerationResult:
        self.generate_calls += 1
        raise AssertionError("Preflight must never execute the Guideline Generator")


def make_generator(
    provenance: GuidelineGenerationProvenance | None = None,
) -> SyntheticRuntimeGuidelineGenerator:
    return SyntheticRuntimeGuidelineGenerator(
        provenance if provenance is not None else build_candidate_provenance(model=MODEL)
    )


def build_approved_pack(
    *,
    policy: VersionedGuidelinePolicy,
    fallbacks: tuple[ApprovedGuidelineFallback, ...],
) -> tuple[Rag15ApprovalPack, SyntheticRag15DecisionVerifier]:
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=build_candidate_provenance(model=MODEL),
        policy_ref=policy.artifact_ref,
        fallback_pins=tuple(Rag15FallbackPin(fb.code, fb.artifact_ref) for fb in fallbacks),
    )
    evidence, decisions = make_approved_evidence(candidate_ref=candidate_ref)
    pack = build_rag15_approval_pack(
        model=MODEL,
        policy=policy,
        fallbacks=fallbacks,
        approval_evidence=evidence,
        source_revision=SOURCE_REVISION,
    )
    return pack, SyntheticRag15DecisionVerifier(decisions)


def assert_blocked(outcome: GuideRuntimePreflightOutcome, reason: GuideRuntimePreflightReason) -> None:
    assert outcome.decision is GuideRuntimePreflightDecision.BLOCKED
    assert outcome.reason is reason
    assert outcome.ready_context is None


# --- Happy path ---------------------------------------------------------------


def test_exact_approved_candidate_is_ready() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    generator = make_generator()

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks),
        generator=generator,
        decision_verifier=verifier,
    )

    assert outcome.decision is GuideRuntimePreflightDecision.READY
    assert outcome.reason is None
    assert outcome.ready_context is not None
    assert outcome.ready_context.approval_pack_ref == pack.pack_ref
    assert outcome.ready_context.candidate_ref == pack.candidate_ref
    assert outcome.ready_context.generation_provenance == generator.provenance
    assert outcome.ready_context.policy_ref == policy.artifact_ref
    assert outcome.ready_context.fallback_refs == tuple(
        pin.artifact_ref for pin in sorted(pack.fallback_pins, key=lambda p: p.code.value)
    )
    assert generator.generate_calls == 0


def test_reversed_fallback_ordering_is_ready() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    generator = make_generator()

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(
            approval_pack=pack,
            policy=policy,
            fallbacks=tuple(reversed(fallbacks)),
        ),
        generator=generator,
        decision_verifier=verifier,
    )

    assert outcome.decision is GuideRuntimePreflightDecision.READY
    assert generator.generate_calls == 0


# --- Phase 1: request shape ---------------------------------------------------


def test_non_request_input_is_blocked() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    _, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    generator = make_generator()

    outcome = preflight_guide_runtime(
        object(),  # type: ignore[arg-type]
        generator=generator,
        decision_verifier=verifier,
    )

    assert_blocked(outcome, GuideRuntimePreflightReason.REQUEST_INVALID)
    assert generator.generate_calls == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("approval_pack", object()),
        ("policy", object()),
        ("fallbacks", "not-a-tuple"),
        ("fallbacks", ()),
        ("fallbacks", None),
    ],
)
def test_invalid_request_fields_are_blocked(field: str, value: Any) -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    generator = make_generator()

    request = replace(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks),
        **{field: value},
    )
    outcome = preflight_guide_runtime(request, generator=generator, decision_verifier=verifier)

    assert_blocked(outcome, GuideRuntimePreflightReason.REQUEST_INVALID)
    assert generator.generate_calls == 0


# --- Phase 2: approval pack consumability -------------------------------------


def test_pending_pack_is_blocked() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model=MODEL,
        policy=policy,
        fallbacks=fallbacks,
        source_revision=SOURCE_REVISION,
    )
    generator = make_generator()

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks),
        generator=generator,
        decision_verifier=SyntheticRag15DecisionVerifier(),
    )

    assert_blocked(outcome, GuideRuntimePreflightReason.APPROVAL_PACK_NOT_CONSUMABLE)
    assert generator.generate_calls == 0


def test_rejected_pack_is_blocked() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=build_candidate_provenance(model=MODEL),
        policy_ref=policy.artifact_ref,
        fallback_pins=tuple(Rag15FallbackPin(fb.code, fb.artifact_ref) for fb in fallbacks),
    )
    approved, decisions = make_approved_evidence(candidate_ref=candidate_ref)
    rejected_ref = ImmutableArtifactRef(
        "decision-safety", "decision-v1", hashlib.sha256(b"rejected-SAFETY").hexdigest()
    )
    evidence = tuple(
        create_rag15_approval_evidence(
            scope=ev.scope,
            approval_status="REJECTED" if ev.scope == "SAFETY" else ev.approval_status,
            candidate_ref=candidate_ref,
            decision_ref=rejected_ref if ev.scope == "SAFETY" else ev.decision_ref,
        )
        for ev in approved
    )
    pack = build_rag15_approval_pack(
        model=MODEL,
        policy=policy,
        fallbacks=fallbacks,
        approval_evidence=evidence,
        source_revision=SOURCE_REVISION,
    )
    generator = make_generator()

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks),
        generator=generator,
        decision_verifier=SyntheticRag15DecisionVerifier(decisions),
    )

    assert_blocked(outcome, GuideRuntimePreflightReason.APPROVAL_PACK_NOT_CONSUMABLE)
    assert generator.generate_calls == 0


def test_approved_pack_with_unauthenticated_decisions_is_blocked() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, _ = build_approved_pack(policy=policy, fallbacks=fallbacks)
    generator = make_generator()

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks),
        generator=generator,
        decision_verifier=SyntheticRag15DecisionVerifier(),
    )

    assert_blocked(outcome, GuideRuntimePreflightReason.APPROVAL_PACK_NOT_CONSUMABLE)
    assert generator.generate_calls == 0


def test_candidate_hash_drift_is_blocked() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    drifted = replace(
        pack,
        candidate_ref=ImmutableArtifactRef(pack.candidate_ref.artifact_code, pack.candidate_ref.version, "a" * 64),
    )
    generator = make_generator()

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=drifted, policy=policy, fallbacks=fallbacks),
        generator=generator,
        decision_verifier=verifier,
    )

    assert_blocked(outcome, GuideRuntimePreflightReason.APPROVAL_PACK_NOT_CONSUMABLE)
    assert generator.generate_calls == 0


# --- Phase 3: actual generator identity ---------------------------------------


@pytest.mark.parametrize("ref_field", ["prompt_ref", "model_ref", "parser_ref", "validator_ref"])
def test_generator_provenance_drift_is_blocked(ref_field: str) -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)

    approved_provenance = build_candidate_provenance(model=MODEL)
    original: ImmutableArtifactRef = getattr(approved_provenance, ref_field)
    drifted_provenance = replace(
        approved_provenance,
        **{ref_field: ImmutableArtifactRef(original.artifact_code, original.version, "b" * 64)},
    )
    generator = make_generator(drifted_provenance)

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks),
        generator=generator,
        decision_verifier=verifier,
    )

    assert_blocked(outcome, GuideRuntimePreflightReason.GENERATOR_PROVENANCE_MISMATCH)
    assert generator.generate_calls == 0


def test_generator_with_different_model_is_blocked() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    generator = make_generator(build_candidate_provenance(model="gpt-4o"))

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks),
        generator=generator,
        decision_verifier=verifier,
    )

    assert_blocked(outcome, GuideRuntimePreflightReason.GENERATOR_PROVENANCE_MISMATCH)
    assert generator.generate_calls == 0


# --- Phase 4: policy exact-match ----------------------------------------------


def test_policy_ref_drift_is_blocked() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    other_policy = create_canonical_guideline_policy(
        artifact_code="guideline-policy",
        version="guideline-policy@synthetic-v2",
        maximum_claims=4,
    )
    assert other_policy.artifact_ref != policy.artifact_ref
    generator = make_generator()

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=other_policy, fallbacks=fallbacks),
        generator=generator,
        decision_verifier=verifier,
    )

    assert_blocked(outcome, GuideRuntimePreflightReason.POLICY_REF_MISMATCH)
    assert generator.generate_calls == 0


PolicyTamper = Callable[[VersionedGuidelinePolicy], VersionedGuidelinePolicy]

_POLICY_TAMPERS: dict[str, PolicyTamper] = {
    "maximum_claims": lambda p: replace(p, maximum_claims=p.maximum_claims + 1),
    "uncertainty_text_sha256": lambda p: replace(p, uncertainty_text_sha256="d" * 64),
    "consultation_text_sha256": lambda p: replace(p, consultation_text_sha256="e" * 64),
}


@pytest.mark.parametrize("field", sorted(_POLICY_TAMPERS))
def test_policy_semantic_tamper_under_same_ref_is_blocked(field: str) -> None:
    """A policy whose runtime semantics no longer reproduce its pinned ref must fail closed."""
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)

    tampered = _POLICY_TAMPERS[field](policy)
    # The pinned identity is untouched, so a ref-only comparison would let this through.
    assert tampered.artifact_ref == policy.artifact_ref
    assert tampered.artifact_ref == pack.policy_ref
    generator = make_generator()

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=tampered, fallbacks=fallbacks),
        generator=generator,
        decision_verifier=verifier,
    )

    assert_blocked(outcome, GuideRuntimePreflightReason.POLICY_REF_MISMATCH)
    assert generator.generate_calls == 0


# --- Phase 5: fallback set exact-match ----------------------------------------


def _fallback_variants() -> dict[str, tuple[Any, ...]]:
    fallbacks = make_fallbacks()
    tampered = replace(
        fallbacks[0],
        artifact_ref=ImmutableArtifactRef(
            fallbacks[0].artifact_ref.artifact_code,
            fallbacks[0].artifact_ref.version,
            "c" * 64,
        ),
    )
    return {
        "missing_code": fallbacks[1:],
        "duplicate_code": fallbacks + (fallbacks[0],),
        "same_code_different_ref": (tampered,) + fallbacks[1:],
        "extra_invalid_item": fallbacks + ("not-a-fallback",),
    }


@pytest.mark.parametrize("variant", sorted(_fallback_variants()))
def test_fallback_set_drift_is_blocked(variant: str) -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    generator = make_generator()

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(
            approval_pack=pack,
            policy=policy,
            fallbacks=_fallback_variants()[variant],
        ),
        generator=generator,
        decision_verifier=verifier,
    )

    assert_blocked(outcome, GuideRuntimePreflightReason.FALLBACK_SET_MISMATCH)
    assert generator.generate_calls == 0


def test_fallback_code_type_mismatch_is_blocked() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    # A raw str code must be rejected even though it equals a valid enum value.
    broken = (replace(fallbacks[0], code="NO_APPROVED_EVIDENCE"),) + fallbacks[1:]  # type: ignore[arg-type]
    generator = make_generator()

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=broken),
        generator=generator,
        decision_verifier=verifier,
    )

    assert_blocked(outcome, GuideRuntimePreflightReason.FALLBACK_SET_MISMATCH)
    assert generator.generate_calls == 0


def test_fallback_text_tamper_under_same_ref_is_blocked() -> None:
    """A fallback whose text no longer reproduces its pinned ref must fail closed."""
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)

    tampered = replace(fallbacks[0], text=SensitiveText("승인되지 않은 대체 안내 문구"))
    # Same code, same pinned ref: only the semantic payload drifted.
    assert tampered.code == fallbacks[0].code
    assert tampered.artifact_ref == fallbacks[0].artifact_ref
    generator = make_generator()

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(
            approval_pack=pack,
            policy=policy,
            fallbacks=(tampered,) + fallbacks[1:],
        ),
        generator=generator,
        decision_verifier=verifier,
    )

    assert_blocked(outcome, GuideRuntimePreflightReason.FALLBACK_SET_MISMATCH)
    assert generator.generate_calls == 0


# --- Scope guards -------------------------------------------------------------


def test_ready_context_carries_no_runtime_payload() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks),
        generator=make_generator(),
        decision_verifier=verifier,
    )

    assert outcome.ready_context is not None
    assert set(type(outcome.ready_context).__slots__) == {
        "approval_pack_ref",
        "candidate_ref",
        "generation_provenance",
        "policy_ref",
        "fallback_refs",
    }


def test_fallback_codes_cover_every_guideline_fallback_code() -> None:
    assert {fb.code for fb in make_fallbacks()} == set(GuidelineFallbackCode)


def test_openai_adapter_satisfies_runtime_generator_port() -> None:
    """The production adapter structurally satisfies the runtime port without any API call."""
    from ai_worker.adapters.openai_guideline_generator import OpenAIGuidelineGeneratorAdapter
    from ai_worker.tests.rag.test_openai_guideline_generator import build_mock_client, make_context

    adapter = OpenAIGuidelineGeneratorAdapter(
        client=build_mock_client(),
        model=MODEL,
        timeout_seconds=1.0,
        context=make_context(),
    )

    port: RuntimeGuidelineGeneratorPort = adapter
    assert port.provenance == build_candidate_provenance(model=MODEL)


def test_openai_adapter_is_accepted_by_preflight() -> None:
    from ai_worker.adapters.openai_guideline_generator import OpenAIGuidelineGeneratorAdapter
    from ai_worker.tests.rag.test_openai_guideline_generator import build_mock_client, make_context

    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    adapter = OpenAIGuidelineGeneratorAdapter(
        client=build_mock_client(),
        model=MODEL,
        timeout_seconds=1.0,
        context=make_context(),
    )

    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks),
        generator=adapter,
        decision_verifier=verifier,
    )

    assert outcome.decision is GuideRuntimePreflightDecision.READY
