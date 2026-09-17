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
    compute_rag15_candidate_ref,
    compute_rag15_pack_ref,
    create_pending_approval_evidence,
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
        evidence_list.append(
            Rag15ApprovalEvidence(
                scope=scope,
                approval_status="APPROVED",
                candidate_ref=candidate_ref,
                decision_ref=decision_ref,
            )
        )
        allowed_refs.add(decision_ref)
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

    # Also test approval evidence permutation produces same pack_ref
    ev1, _ = make_approved_evidence(candidate_ref=pack1.candidate_ref)
    ev2 = tuple(reversed(ev1))

    approved_pack1 = build_rag15_approval_pack(
        model=model,
        policy=policy,
        fallbacks=fallbacks,
        approval_evidence=ev1,
        source_revision=rev,
    )
    approved_pack2 = build_rag15_approval_pack(
        model=model,
        policy=policy,
        fallbacks=reversed_fallbacks,
        approval_evidence=ev2,
        source_revision=rev,
    )
    assert approved_pack1.candidate_ref == approved_pack2.candidate_ref
    assert approved_pack1.pack_ref == approved_pack2.pack_ref


# --- B. Prompt drift ---
def test_prompt_drift_fails_integrity() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="trace-rev",
    )

    # Tamper with prompt_ref version
    tampered_provenance = replace(
        pack.generation_provenance,
        prompt_ref=replace(pack.generation_provenance.prompt_ref, version="guideline-prompt-tampered"),
    )
    tampered_pack = replace(pack, generation_provenance=tampered_provenance)

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Prompt candidate drift" in issue for issue in res.issues)

    # Tamper with prompt_ref hash
    tampered_provenance_hash = replace(
        pack.generation_provenance,
        prompt_ref=replace(pack.generation_provenance.prompt_ref, content_sha256="0" * 64),
    )
    tampered_pack_hash = replace(pack, generation_provenance=tampered_provenance_hash)
    res2 = verify_rag15_approval_pack(tampered_pack_hash, approval_verifier=verifier)
    assert res2.integrity_verified is False
    assert res2.production_consumable is False


# --- C. Model drift ---
def test_model_drift_fails_integrity() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="trace-rev",
    )

    # Invalid version format (not starting with openai:)
    tampered_provenance = replace(
        pack.generation_provenance,
        model_ref=ImmutableArtifactRef("guideline-model", "anthropic:claude-3-5-sonnet", "0" * 64),
    )
    tampered_pack = replace(pack, generation_provenance=tampered_provenance)

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("model_ref" in issue for issue in res.issues)

    # Valid format but model hash mismatch
    tampered_hash_provenance = replace(
        pack.generation_provenance,
        model_ref=ImmutableArtifactRef("guideline-model", "openai:gpt-4o-mini", "f" * 64),
    )
    tampered_pack_hash = replace(pack, generation_provenance=tampered_hash_provenance)
    res2 = verify_rag15_approval_pack(tampered_pack_hash, approval_verifier=verifier)
    assert res2.integrity_verified is False
    assert res2.production_consumable is False


# --- D. Parser drift ---
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
        parser_ref=replace(pack.generation_provenance.parser_ref, content_sha256="1" * 64),
    )
    tampered_pack = replace(pack, generation_provenance=tampered_provenance)

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Parser candidate drift" in issue for issue in res.issues)


# --- E. Validator drift ---
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
        validator_ref=replace(pack.generation_provenance.validator_ref, content_sha256="2" * 64),
    )
    tampered_pack = replace(pack, generation_provenance=tampered_provenance)

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Validator candidate drift" in issue for issue in res.issues)


# --- F. Policy ref drift ---
def test_policy_ref_drift_fails_candidate_ref_integrity() -> None:
    policy = make_policy(maximum_claims=4)
    fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=fallbacks,
        source_revision="trace-rev",
    )

    different_policy = make_policy(maximum_claims=5)
    tampered_pack = replace(pack, policy_ref=different_policy.artifact_ref)

    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Candidate ref mismatch" in issue for issue in res.issues)


# --- G. Fallback missing ---
def test_fallback_missing_rejected() -> None:
    policy = make_policy()
    all_fallbacks = make_fallbacks()
    missing_one = all_fallbacks[:-1]

    with pytest.raises(ValueError, match="Fallback set must contain exact GuidelineFallbackCode"):
        build_rag15_pending_approval_pack(
            model="gpt-4o-mini",
            policy=policy,
            fallbacks=missing_one,
            source_revision="trace-rev",
        )

    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=all_fallbacks,
        source_revision="trace-rev",
    )
    tampered_pack = replace(pack, fallback_pins=pack.fallback_pins[:-1])
    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False
    assert any("Fallback pins exact set mismatch" in issue for issue in res.issues)


# --- H. Fallback duplicate ---
def test_fallback_duplicate_rejected() -> None:
    policy = make_policy()
    all_fallbacks = make_fallbacks()
    duplicated = all_fallbacks + (all_fallbacks[0],)

    with pytest.raises(ValueError, match="Fallback set must contain exact GuidelineFallbackCode"):
        build_rag15_pending_approval_pack(
            model="gpt-4o-mini",
            policy=policy,
            fallbacks=duplicated,
            source_revision="trace-rev",
        )

    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=all_fallbacks,
        source_revision="trace-rev",
    )
    tampered_pack = replace(pack, fallback_pins=pack.fallback_pins + (pack.fallback_pins[0],))
    verifier = MockApprovalVerifier()
    res = verify_rag15_approval_pack(tampered_pack, approval_verifier=verifier)
    assert res.integrity_verified is False
    assert res.production_consumable is False


# --- I. Fallback hash tamper ---
def test_fallback_hash_tamper_rejected() -> None:
    policy = make_policy()
    all_fallbacks = make_fallbacks()
    pack = build_rag15_pending_approval_pack(
        model="gpt-4o-mini",
        policy=policy,
        fallbacks=all_fallbacks,
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


# --- L. Approval replay protection ---
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
    # Reject one scope (e.g. PHARMACY) with a decision_ref
    rejected_ev = list(approved_ev)
    rejected_ev[1] = Rag15ApprovalEvidence(
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
    approved_ev, allowed_refs = make_approved_evidence(candidate_ref=candidate_ref)

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
    assert any("Authority verifier failed" in issue for issue in res.issues)


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
