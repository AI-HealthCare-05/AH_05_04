from __future__ import annotations

import hashlib
from dataclasses import fields, replace

import pytest

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.guideline_approval_pack import (
    RAG15_REQUIRED_APPROVAL_SCOPES,
    Rag15ApprovalEvidence,
    Rag15ApprovalPack,
    Rag15FallbackPin,
    build_rag15_approval_pack,
    build_rag15_pending_approval_pack,
    compute_rag15_approval_evidence_ref,
    compute_rag15_candidate_ref,
    compute_rag15_pack_ref,
    create_pending_approval_evidence,
    create_rag15_approval_evidence,
    verify_rag15_approval_pack,
)
from ai_worker.tasks.rag.guideline_card import (
    ApprovedGuidelineFallback,
    GuidelineApprovalVerificationFailure,
    GuidelineApprovalVerificationSuccess,
    GuidelineFallbackCode,
    VersionedGuidelinePolicy,
    create_canonical_guideline_fallback,
    create_canonical_guideline_policy,
)
from ai_worker.tasks.rag.guideline_generator_prompt import build_candidate_provenance


class MockApprovalVerifier:
    def __init__(self, allowed_refs: set[ImmutableArtifactRef] | None = None) -> None:
        self.allowed_refs: set[ImmutableArtifactRef] = allowed_refs if allowed_refs is not None else set()
        self.verified_calls: list[ImmutableArtifactRef] = []

    def verify(
        self,
        artifact_ref: ImmutableArtifactRef,
    ) -> GuidelineApprovalVerificationSuccess | GuidelineApprovalVerificationFailure:
        self.verified_calls.append(artifact_ref)
        if artifact_ref in self.allowed_refs:
            return GuidelineApprovalVerificationSuccess(
                artifact_ref=artifact_ref,
                verifier_artifact_ref=ImmutableArtifactRef(
                    "guideline-approval-verifier",
                    "verifier-v1",
                    hashlib.sha256(b"verifier").hexdigest(),
                ),
            )
        return GuidelineApprovalVerificationFailure()


def make_policy(*, maximum_claims: int = 4) -> VersionedGuidelinePolicy:
    return create_canonical_guideline_policy(
        artifact_code="guideline-policy",
        version="guideline-policy@synthetic-v1",
        maximum_claims=maximum_claims,
    )


def make_fallbacks() -> tuple[ApprovedGuidelineFallback, ...]:
    return tuple(
        create_canonical_guideline_fallback(
            artifact_code="guideline-fallback",
            version=f"guideline-fallback@synthetic-{code.value.lower()}",
            code=code,
        )
        for code in GuidelineFallbackCode
    )


def make_approved_evidence(
    *,
    candidate_ref: ImmutableArtifactRef,
) -> tuple[tuple[Rag15ApprovalEvidence, ...], set[ImmutableArtifactRef]]:
    evidence_list: list[Rag15ApprovalEvidence] = []
    allowed_refs: set[ImmutableArtifactRef] = set()
    for scope in RAG15_REQUIRED_APPROVAL_SCOPES:
        decision_ref = ImmutableArtifactRef(
            artifact_code=f"decision-{scope.lower()}",
            version="decision-v1",
            content_sha256=hashlib.sha256(f"approved-{scope}".encode()).hexdigest(),
        )
        ev = create_rag15_approval_evidence(
            scope=scope,
            approval_status="APPROVED",
            candidate_ref=candidate_ref,
            decision_ref=decision_ref,
        )
        evidence_list.append(ev)
        allowed_refs.add(ev.artifact_ref)
    return tuple(evidence_list), allowed_refs


# --- A. Deterministic candidate and pack identity across input ordering ---
def test_deterministic_candidate_and_pack_identity() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    model = "gpt-4o-mini"
    rev = "git-commit-hash-abc"

    reversed_fallbacks = tuple(reversed(fallbacks))

    pack1 = build_rag15_pending_approval_pack(
        model=model,
        policy=policy,
        fallbacks=fallbacks,
        source_revision=rev,
    )
    pack2 = build_rag15_pending_approval_pack(
        model=model,
        policy=policy,
        fallbacks=reversed_fallbacks,
        source_revision=rev,
    )

    assert pack1.candidate_ref == pack2.candidate_ref
    assert pack1.pack_ref == pack2.pack_ref
    assert pack1.candidate_ref.artifact_code == "rag15-guideline-candidate"
    assert pack1.candidate_ref.version == "rag15-guideline-v1"
    assert pack1.pack_ref.artifact_code == "rag15-approval-pack"
    assert pack1.pack_ref.version == "rag15-guideline-v1"
    assert len(pack1.candidate_ref.content_sha256) == 64
    assert len(pack1.pack_ref.content_sha256) == 64

    # Reversed approval evidence tuple input still generates identical pack_ref
    reversed_ev = tuple(reversed(pack1.approval_evidence))
    pack3 = build_rag15_approval_pack(
        model=model,
        policy=policy,
        fallbacks=fallbacks,
        approval_evidence=reversed_ev,
        source_revision=rev,
    )
    assert pack3.pack_ref == pack1.pack_ref


# --- B. Prompt drift fails integrity ---
def test_prompt_drift_fails_integrity() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="trace-rev",
    )

    tampered_provenance = replace(
        pack.generation_provenance,
        prompt_ref=replace(pack.generation_provenance.prompt_ref, content_sha256="0" * 64),
    )
    tampered_pack = replace(pack, generation_provenance=tampered_provenance)

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Prompt candidate drift" in issue for issue in res.issues)


# --- C. Model drift fails integrity ---
def test_model_drift_fails_integrity() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="trace-rev",
    )

    tampered_provenance = replace(
        pack.generation_provenance,
        model_ref=replace(pack.generation_provenance.model_ref, content_sha256="1" * 64),
    )
    tampered_pack = replace(pack, generation_provenance=tampered_provenance)

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Model candidate drift" in issue for issue in res.issues)


# --- D. Parser drift fails integrity ---
def test_parser_drift_fails_integrity() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="trace-rev",
    )

    tampered_provenance = replace(
        pack.generation_provenance,
        parser_ref=replace(pack.generation_provenance.parser_ref, content_sha256="2" * 64),
    )
    tampered_pack = replace(pack, generation_provenance=tampered_provenance)

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Parser candidate drift" in issue for issue in res.issues)


# --- E. Validator drift fails integrity ---
def test_validator_drift_fails_integrity() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="trace-rev",
    )

    tampered_provenance = replace(
        pack.generation_provenance,
        validator_ref=replace(pack.generation_provenance.validator_ref, content_sha256="3" * 64),
    )
    tampered_pack = replace(pack, generation_provenance=tampered_provenance)

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Validator candidate drift" in issue for issue in res.issues)


# --- F. Policy ref drift fails candidate_ref integrity ---
def test_policy_ref_drift_fails_candidate_ref_integrity() -> None:
    policy1 = make_policy(maximum_claims=4)
    policy2 = make_policy(maximum_claims=5)
    fallbacks = make_fallbacks()

    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy1,
        fallbacks=fallbacks,
        source_revision="trace-rev",
    )

    # Swap policy_ref without recomputing candidate_ref
    tampered_pack = replace(pack, policy_ref=policy2.artifact_ref)
    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Candidate ref mismatch" in issue for issue in res.issues)


# --- G. Fallback missing rejected ---
def test_fallback_missing_rejected() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    missing_one = fallbacks[:-1]

    with pytest.raises(ValueError, match="Fallback set must contain exact GuidelineFallbackCode members"):
        build_rag15_pending_approval_pack(
            model="gpt-4o-mini",
            policy=policy,
            fallbacks=missing_one,
            source_revision="trace-rev",
        )

    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="trace-rev",
    )
    tampered_pack = replace(pack, fallback_pins=pack.fallback_pins[:-1])
    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Fallback pins exact set mismatch" in issue for issue in res.issues)


# --- H. Fallback duplicate rejected ---
def test_fallback_duplicate_rejected() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    duplicate_fallbacks = fallbacks + (fallbacks[0],)

    with pytest.raises(ValueError, match="Fallback set must contain exact GuidelineFallbackCode members"):
        build_rag15_pending_approval_pack(
            model="gpt-4o-mini",
            policy=policy,
            fallbacks=duplicate_fallbacks,
            source_revision="trace-rev",
        )

    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="trace-rev",
    )
    tampered_pack = replace(pack, fallback_pins=pack.fallback_pins + (pack.fallback_pins[0],))
    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Fallback pins exact set mismatch" in issue for issue in res.issues)


# --- I. Fallback hash tamper rejected ---
def test_fallback_hash_tamper_rejected() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="trace-rev",
    )

    tampered_pin = replace(
        pack.fallback_pins[0],
        artifact_ref=replace(pack.fallback_pins[0].artifact_ref, content_sha256="9" * 64),
    )
    tampered_pins = (tampered_pin,) + pack.fallback_pins[1:]
    tampered_pack = replace(pack, fallback_pins=tampered_pins)

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Candidate ref mismatch" in issue for issue in res.issues)


# --- J. candidate_ref mismatch ---
def test_candidate_ref_mismatch_rejected() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="trace-rev",
    )

    tampered_pack = replace(
        pack,
        candidate_ref=replace(pack.candidate_ref, content_sha256="e" * 64),
    )

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Candidate ref mismatch" in issue for issue in res.issues)


# --- K. pack_ref mismatch ---
def test_pack_ref_mismatch_rejected() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="trace-rev",
    )

    tampered_pack = replace(
        pack,
        pack_ref=replace(pack.pack_ref, content_sha256="d" * 64),
    )

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Pack ref mismatch" in issue for issue in res.issues)


# --- L. Approval replay protection (Candidate A evidence into Candidate B pack) ---
def test_approval_replay_protection() -> None:
    policy1 = make_policy(maximum_claims=4)
    policy2 = make_policy(maximum_claims=5)
    fallbacks = make_fallbacks()

    pack1 = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy1,
        fallbacks=fallbacks,
        source_revision="rev-1",
    )
    approved_ev1, _ = make_approved_evidence(candidate_ref=pack1.candidate_ref)

    # Attempt to build pack2 using policy2 but with approved_ev1 (which was bound to pack1.candidate_ref)
    with pytest.raises(ValueError, match="Approval evidence candidate_ref mismatch"):
        build_rag15_approval_pack(
            model="gpt-4o-mini",
            policy=policy2,
            fallbacks=fallbacks,
            approval_evidence=approved_ev1,
            source_revision="rev-2",
        )

    # Even if forged via replace() on an existing pack2:
    pack2 = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy2,
        fallbacks=fallbacks,
        source_revision="rev-2",
    )
    tampered_pack2 = replace(
        pack2,
        approval_evidence=approved_ev1,
        pack_ref=compute_rag15_pack_ref(
            candidate_ref=pack2.candidate_ref,
            approval_evidence=approved_ev1,
        ),
    )

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack2, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Approval evidence replay protection failure" in issue for issue in res.issues)


# --- L2. Approved decision cannot be rebound to different candidate ---
def test_approved_decision_cannot_be_rebound_to_different_candidate() -> None:
    policy1 = make_policy(maximum_claims=4)
    policy2 = make_policy(maximum_claims=5)
    fallbacks = make_fallbacks()

    pack1 = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy1,
        fallbacks=fallbacks,
        source_revision="rev-1",
    )
    approved_ev1, allowed_refs1 = make_approved_evidence(candidate_ref=pack1.candidate_ref)

    # Authority verifier ONLY approved pack1's candidate-bound evidence artifacts
    verifier = MockApprovalVerifier(allowed_refs=allowed_refs1)

    # Now create candidate 2
    pack2_pending = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy2,
        fallbacks=fallbacks,
        source_revision="rev-2",
    )

    # Caller attempts to reuse the SAME decision_refs for candidate 2:
    rebound_evidence: list[Rag15ApprovalEvidence] = []
    for ev1 in approved_ev1:
        rebound_ev = create_rag15_approval_evidence(
            scope=ev1.scope,
            approval_status="APPROVED",
            candidate_ref=pack2_pending.candidate_ref,
            decision_ref=ev1.decision_ref,  # Same decision ref
        )
        assert rebound_ev.artifact_ref != ev1.artifact_ref
        rebound_evidence.append(rebound_ev)

    pack2 = build_rag15_approval_pack(
        model="gpt-4o-mini",
        policy=policy2,
        fallbacks=fallbacks,
        approval_evidence=tuple(rebound_evidence),
        source_revision="rev-2",
    )

    # Verifier does NOT have rebound_ev in its approved set -> evidence verification fails!
    res = verify_rag15_approval_pack(pack2, approval_verifier=verifier)

    assert res.integrity_verified is True
    assert res.approval_status == "APPROVED"
    assert res.approval_evidence_verified is False
    assert res.production_consumable is False
    assert any("Authority verifier returned failure" in issue for issue in res.issues)


# --- L3. Approval evidence hash tamper rejected ---
def test_approval_evidence_hash_tamper_rejected() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=build_candidate_provenance(model="gpt-4o-mini"),
        policy_ref=policy.artifact_ref,
        fallback_pins=tuple(Rag15FallbackPin(fb.code, fb.artifact_ref) for fb in fallbacks),
    )
    approved_ev, _ = make_approved_evidence(candidate_ref=candidate_ref)

    tampered_item = replace(
        approved_ev[0],
        artifact_ref=replace(approved_ev[0].artifact_ref, content_sha256="7" * 64),
    )
    tampered_ev = (tampered_item,) + approved_ev[1:]

    with pytest.raises(ValueError, match="Approval evidence artifact_ref mismatch"):
        build_rag15_approval_pack(
            model="gpt-4o-mini",
            policy=policy,
            fallbacks=fallbacks,
            approval_evidence=tampered_ev,
            source_revision="rev",
        )

    pack = build_rag15_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        approval_evidence=approved_ev,
        source_revision="rev",
    )
    tampered_pack = replace(pack, approval_evidence=tampered_ev)
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=MockApprovalVerifier())
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("artifact_ref mismatch" in issue for issue in res.issues)


# --- M. Required scope missing ---
def test_required_scope_missing_rejected() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=build_candidate_provenance(model="gpt-4o-mini"),
        policy_ref=policy.artifact_ref,
        fallback_pins=tuple(Rag15FallbackPin(fb.code, fb.artifact_ref) for fb in fallbacks),
    )
    full_ev = create_pending_approval_evidence(candidate_ref=candidate_ref)
    missing_scope_ev = full_ev[:-1]

    with pytest.raises(ValueError, match="Approval evidence must match exact required scopes"):
        build_rag15_approval_pack(
            model="gpt-4o-mini",
            policy=policy,
            fallbacks=fallbacks,
            approval_evidence=missing_scope_ev,
            source_revision="rev",
        )

    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="rev",
    )
    tampered_pack = replace(pack, approval_evidence=missing_scope_ev)
    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Approval evidence scopes exact set mismatch" in issue for issue in res.issues)


# --- N. Required scope duplicate ---
def test_required_scope_duplicate_rejected() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=build_candidate_provenance(model="gpt-4o-mini"),
        policy_ref=policy.artifact_ref,
        fallback_pins=tuple(Rag15FallbackPin(fb.code, fb.artifact_ref) for fb in fallbacks),
    )
    full_ev = create_pending_approval_evidence(candidate_ref=candidate_ref)
    dup_scope_ev = full_ev + (full_ev[0],)

    with pytest.raises(ValueError, match="Approval evidence must match exact required scopes"):
        build_rag15_approval_pack(
            model="gpt-4o-mini",
            policy=policy,
            fallbacks=fallbacks,
            approval_evidence=dup_scope_ev,
            source_revision="rev",
        )

    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="rev",
    )
    tampered_pack = replace(pack, approval_evidence=dup_scope_ev)
    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False


# --- O. PENDING status ---
def test_pending_status_integrity_verified_but_not_production_consumable() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="rev",
    )

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(pack, approval_verifier=verifier)

    assert res.integrity_verified is True
    assert res.approval_status == "PENDING"
    assert res.approval_evidence_verified is False
    assert res.production_consumable is False
    assert len(res.issues) == 0


# --- P. REJECTED status ---
def test_rejected_status_integrity_verified_but_not_production_consumable() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=build_candidate_provenance(model="gpt-4o-mini"),
        policy_ref=policy.artifact_ref,
        fallback_pins=tuple(Rag15FallbackPin(fb.code, fb.artifact_ref) for fb in fallbacks),
    )
    approved_ev, _ = make_approved_evidence(candidate_ref=candidate_ref)
    rejected_ev = list(approved_ev)
    rejected_ev[1] = create_rag15_approval_evidence(
        scope=rejected_ev[1].scope,
        approval_status="REJECTED",
        candidate_ref=candidate_ref,
        decision_ref=ImmutableArtifactRef(
            artifact_code="decision-pharmacy-reject",
            version="decision-v1",
            content_sha256=hashlib.sha256(b"reject-pharmacy").hexdigest(),
        ),
    )

    pack = build_rag15_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        approval_evidence=tuple(rejected_ev),
        source_revision="rev",
    )

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(pack, approval_verifier=verifier)

    assert res.integrity_verified is True
    assert res.approval_status == "REJECTED"
    assert res.approval_evidence_verified is False
    assert res.production_consumable is False


# --- Q. APPROVED but evidence verification fails ---
def test_approved_evidence_verification_failure() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=build_candidate_provenance(model="gpt-4o-mini"),
        policy_ref=policy.artifact_ref,
        fallback_pins=tuple(Rag15FallbackPin(fb.code, fb.artifact_ref) for fb in fallbacks),
    )
    approved_ev, _ = make_approved_evidence(candidate_ref=candidate_ref)

    pack = build_rag15_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        approval_evidence=approved_ev,
        source_revision="rev",
    )

    # Empty allowed_refs -> all verifier calls fail
    verifier = MockApprovalVerifier(allowed_refs=set())
    res = verify_rag15_approval_pack(pack, approval_verifier=verifier)

    assert res.integrity_verified is True
    assert res.approval_status == "APPROVED"
    assert res.approval_evidence_verified is False
    assert res.production_consumable is False
    assert any("Authority verifier returned failure" in issue for issue in res.issues)


# --- Q2. Verifier wrong artifact success rejected ---
def test_verifier_wrong_artifact_success_fails_evidence_verification() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=build_candidate_provenance(model="gpt-4o-mini"),
        policy_ref=policy.artifact_ref,
        fallback_pins=tuple(Rag15FallbackPin(fb.code, fb.artifact_ref) for fb in fallbacks),
    )
    approved_ev, _ = make_approved_evidence(candidate_ref=candidate_ref)
    pack = build_rag15_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        approval_evidence=approved_ev,
        source_revision="rev",
    )

    class WrongArtifactVerifier:
        def verify(self, artifact_ref: ImmutableArtifactRef) -> GuidelineApprovalVerificationSuccess:
            return GuidelineApprovalVerificationSuccess(
                artifact_ref=ImmutableArtifactRef("wrong-code", "wrong-v", "f" * 64),
                verifier_artifact_ref=ImmutableArtifactRef("verifier", "v1", "a" * 64),
            )

    res = verify_rag15_approval_pack(pack, approval_verifier=WrongArtifactVerifier())  # type: ignore[arg-type]
    assert res.integrity_verified is True
    assert res.approval_status == "APPROVED"
    assert res.approval_evidence_verified is False
    assert res.production_consumable is False
    assert any("invalid success response" in issue for issue in res.issues)


# --- Q3. Verifier invalid verifier ref rejected ---
def test_verifier_invalid_verifier_ref_fails_evidence_verification() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=build_candidate_provenance(model="gpt-4o-mini"),
        policy_ref=policy.artifact_ref,
        fallback_pins=tuple(Rag15FallbackPin(fb.code, fb.artifact_ref) for fb in fallbacks),
    )
    approved_ev, _ = make_approved_evidence(candidate_ref=candidate_ref)
    pack = build_rag15_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        approval_evidence=approved_ev,
        source_revision="rev",
    )

    class BadVerifierRefVerifier:
        def verify(self, artifact_ref: ImmutableArtifactRef) -> GuidelineApprovalVerificationSuccess:
            return GuidelineApprovalVerificationSuccess(
                artifact_ref=artifact_ref,
                verifier_artifact_ref=ImmutableArtifactRef("", "", "bad-hash"),
            )

    res = verify_rag15_approval_pack(pack, approval_verifier=BadVerifierRefVerifier())  # type: ignore[arg-type]
    assert res.integrity_verified is True
    assert res.approval_status == "APPROVED"
    assert res.approval_evidence_verified is False
    assert res.production_consumable is False
    assert any("invalid success response" in issue for issue in res.issues)


# --- Q4. Verifier exception fails closed ---
def test_verifier_exception_fails_closed() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=build_candidate_provenance(model="gpt-4o-mini"),
        policy_ref=policy.artifact_ref,
        fallback_pins=tuple(Rag15FallbackPin(fb.code, fb.artifact_ref) for fb in fallbacks),
    )
    approved_ev, _ = make_approved_evidence(candidate_ref=candidate_ref)
    pack = build_rag15_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        approval_evidence=approved_ev,
        source_revision="rev",
    )

    class CrashingVerifier:
        def verify(self, artifact_ref: ImmutableArtifactRef) -> GuidelineApprovalVerificationSuccess:
            raise RuntimeError("Connection timed out to authority registry")

    res = verify_rag15_approval_pack(pack, approval_verifier=CrashingVerifier())  # type: ignore[arg-type]
    assert res.integrity_verified is True
    assert res.approval_status == "APPROVED"
    assert res.approval_evidence_verified is False
    assert res.production_consumable is False
    assert any("raised exception" in issue for issue in res.issues)


# --- Q5. Verifier input mutation fails closed ---
def test_verifier_input_mutation_fails_closed() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=build_candidate_provenance(model="gpt-4o-mini"),
        policy_ref=policy.artifact_ref,
        fallback_pins=tuple(Rag15FallbackPin(fb.code, fb.artifact_ref) for fb in fallbacks),
    )
    approved_ev, _ = make_approved_evidence(candidate_ref=candidate_ref)
    pack = build_rag15_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        approval_evidence=approved_ev,
        source_revision="rev",
    )

    class MutatingVerifier:
        def verify(self, artifact_ref: ImmutableArtifactRef) -> GuidelineApprovalVerificationSuccess:
            object.__setattr__(artifact_ref, "version", "mutated-version")
            return GuidelineApprovalVerificationSuccess(
                artifact_ref=artifact_ref,
                verifier_artifact_ref=ImmutableArtifactRef("v", "v", "b" * 64),
            )

    res = verify_rag15_approval_pack(pack, approval_verifier=MutatingVerifier())  # type: ignore[arg-type]
    assert res.integrity_verified is True
    assert res.approval_status == "APPROVED"
    assert res.approval_evidence_verified is False
    assert res.production_consumable is False
    assert any("mutated input" in issue for issue in res.issues)


# --- R. Fully approved ---
def test_fully_approved_production_consumable() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=build_candidate_provenance(model="gpt-4o-mini"),
        policy_ref=policy.artifact_ref,
        fallback_pins=tuple(Rag15FallbackPin(fb.code, fb.artifact_ref) for fb in fallbacks),
    )
    approved_ev, allowed_refs = make_approved_evidence(candidate_ref=candidate_ref)

    pack = build_rag15_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        approval_evidence=approved_ev,
        source_revision="rev",
    )

    verifier = MockApprovalVerifier(allowed_refs=allowed_refs)
    res = verify_rag15_approval_pack(pack, approval_verifier=verifier)

    assert res.integrity_verified is True
    assert res.approval_status == "APPROVED"
    assert res.approval_evidence_verified is True
    assert res.production_consumable is True
    assert len(res.issues) == 0
    assert len(verifier.verified_calls) == len(RAG15_REQUIRED_APPROVAL_SCOPES)


# --- S. source_revision is trace-only ---
def test_source_revision_is_trace_only() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()

    pack1 = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="rev-commit-1",
    )
    pack2 = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="rev-commit-2",
    )

    assert pack1.candidate_ref == pack2.candidate_ref
    assert pack1.pack_ref == pack2.pack_ref
    assert pack1.source_revision != pack2.source_revision


# --- T. No request-specific data in pack ---
def test_no_request_specific_data_in_pack() -> None:
    field_names = {f.name for f in fields(Rag15ApprovalPack)}
    forbidden_terms = {
        "medication_identity",
        "evidence_gate_outcome",
        "gate_passed_selection",
        "approved_evidence_binding",
        "assessment_receipt",
        "retrieval_receipt",
        "prescription_version_medication_id",
    }
    for field in field_names:
        for forbidden in forbidden_terms:
            assert forbidden not in field.lower()


# --- U. Canonical helpers validation ---
def test_canonical_helpers_consistency() -> None:
    policy = create_canonical_guideline_policy(
        artifact_code="guideline-policy",
        version="v1",
        maximum_claims=4,
    )
    assert policy.maximum_claims == 4
    assert len(policy.artifact_ref.content_sha256) == 64

    for code in GuidelineFallbackCode:
        fb = create_canonical_guideline_fallback(
            artifact_code="guideline-fallback",
            version=f"fallback-{code.value}",
            code=code,
        )
        assert fb.code == code
        assert len(fb.artifact_ref.content_sha256) == 64
        assert "의사 또는 약사와 상담하세요" in fb.text.reveal()


# --- V. Factory validation for create_rag15_approval_evidence ---
def test_create_rag15_approval_evidence_factory_validation() -> None:
    cand_ref = ImmutableArtifactRef("cand", "v1", "a" * 64)
    dec_ref = ImmutableArtifactRef("dec", "v1", "b" * 64)

    # Invalid scope
    with pytest.raises(ValueError, match="scope must be one of"):
        create_rag15_approval_evidence(
            scope="UNKNOWN_SCOPE",
            approval_status="APPROVED",
            candidate_ref=cand_ref,
            decision_ref=dec_ref,
        )

    # Invalid status
    with pytest.raises(ValueError, match="Invalid approval_status"):
        create_rag15_approval_evidence(
            scope="MEDICAL",
            approval_status="INVALID_STATUS",  # type: ignore[arg-type]
            candidate_ref=cand_ref,
            decision_ref=dec_ref,
        )

    # APPROVED without decision_ref
    with pytest.raises(ValueError, match="decision_ref is required for APPROVED"):
        create_rag15_approval_evidence(
            scope="MEDICAL",
            approval_status="APPROVED",
            candidate_ref=cand_ref,
            decision_ref=None,
        )

    # REJECTED without decision_ref
    with pytest.raises(ValueError, match="decision_ref is required for REJECTED"):
        create_rag15_approval_evidence(
            scope="MEDICAL",
            approval_status="REJECTED",
            candidate_ref=cand_ref,
            decision_ref=None,
        )

    # PENDING with decision_ref=None is valid
    ev_pending = create_rag15_approval_evidence(
        scope="MEDICAL",
        approval_status="PENDING",
        candidate_ref=cand_ref,
        decision_ref=None,
    )
    assert ev_pending.artifact_ref.artifact_code == "rag15-approval-evidence"
    assert ev_pending.artifact_ref.version == "rag15-guideline-v1"
    assert len(ev_pending.artifact_ref.content_sha256) == 64

    # Direct compute helper consistency
    computed_ref = compute_rag15_approval_evidence_ref(
        scope="MEDICAL",
        approval_status="PENDING",
        candidate_ref=cand_ref,
        decision_ref=None,
    )
    assert ev_pending.artifact_ref == computed_ref
