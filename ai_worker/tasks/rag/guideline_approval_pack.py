"""RAG-15 Canonical Guideline Approval Pack & #180 Handoff Kernel.

Seals the technical candidate identity of RAG-15 (Prompt, Model, Parser,
Validator, Guideline Policy, and Fallback Set) into a canonical approval pack
with candidate-bound approval evidence, deterministic hashing, and pure
fail-closed verification without unearned approvals or runtime bundle DB mutations.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.guideline_card import (
    ApprovedGuidelineFallback,
    GuidelineApprovalVerificationSuccess,
    GuidelineApprovalVerifierPort,
    GuidelineFallbackCode,
    GuidelineGenerationProvenance,
    VersionedGuidelinePolicy,
)
from ai_worker.tasks.rag.guideline_generator_prompt import build_candidate_provenance

Rag15ApprovalStatus = Literal[
    "PENDING",
    "APPROVED",
    "REJECTED",
]

_VALID_APPROVAL_STATUSES: frozenset[str] = frozenset({"PENDING", "APPROVED", "REJECTED"})

RAG15_REQUIRED_APPROVAL_SCOPES: tuple[str, ...] = (
    "MEDICAL",
    "PHARMACY",
    "SOURCE",
    "PRIVACY",
    "SAFETY",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _artifact_payload(value: ImmutableArtifactRef) -> dict[str, str]:
    return {
        "artifact_code": value.artifact_code,
        "content_sha256": value.content_sha256,
        "version": value.version,
    }


def _validate_artifact_ref(ref: ImmutableArtifactRef, field_name: str) -> None:
    if not isinstance(ref, ImmutableArtifactRef):
        raise TypeError(f"{field_name} must be an ImmutableArtifactRef, got {type(ref)}")
    if not ref.artifact_code or not ref.artifact_code.strip():
        raise ValueError(f"{field_name}.artifact_code cannot be empty")
    if not ref.version or not ref.version.strip():
        raise ValueError(f"{field_name}.version cannot be empty")
    if not _SHA256_RE.match(ref.content_sha256):
        raise ValueError(f"{field_name}.content_sha256 must be a 64-char lowercase hex sha256: {ref.content_sha256}")


@dataclass(frozen=True, slots=True)
class Rag15FallbackPin:
    code: GuidelineFallbackCode
    artifact_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class Rag15ApprovalEvidence:
    scope: str
    approval_status: Rag15ApprovalStatus
    candidate_ref: ImmutableArtifactRef
    decision_ref: ImmutableArtifactRef | None


@dataclass(frozen=True, slots=True)
class Rag15ApprovalPack:
    generation_provenance: GuidelineGenerationProvenance
    policy_ref: ImmutableArtifactRef
    fallback_pins: tuple[Rag15FallbackPin, ...]
    approval_evidence: tuple[Rag15ApprovalEvidence, ...]
    candidate_ref: ImmutableArtifactRef
    pack_ref: ImmutableArtifactRef
    source_revision: str


@dataclass(frozen=True, slots=True)
class Rag15ApprovalPackVerification:
    integrity_verified: bool
    approval_status: Rag15ApprovalStatus | None
    approval_evidence_verified: bool
    production_consumable: bool
    issues: tuple[str, ...]


def _extract_and_validate_fallback_pins(
    fallbacks: tuple[ApprovedGuidelineFallback, ...] | tuple[Rag15FallbackPin, ...] | object,
) -> tuple[Rag15FallbackPin, ...]:
    if not isinstance(fallbacks, (tuple, list)):
        raise TypeError("fallbacks must be a tuple or list")

    pins: list[Rag15FallbackPin] = []
    codes: list[GuidelineFallbackCode] = []
    for fb in fallbacks:
        if isinstance(fb, ApprovedGuidelineFallback):
            pins.append(Rag15FallbackPin(code=fb.code, artifact_ref=fb.artifact_ref))
            codes.append(fb.code)
        elif isinstance(fb, Rag15FallbackPin):
            pins.append(fb)
            codes.append(fb.code)
        else:
            raise TypeError(f"Invalid fallback element: {type(fb)}")

    if set(codes) != set(GuidelineFallbackCode) or len(codes) != len(GuidelineFallbackCode):
        raise ValueError(
            f"Fallback set must contain exact GuidelineFallbackCode members: expected {len(GuidelineFallbackCode)}, got {len(codes)}"
        )

    return tuple(sorted(pins, key=lambda p: p.code.value))


def _validate_evidence_item(ev: Rag15ApprovalEvidence, candidate_ref: ImmutableArtifactRef) -> None:
    if not isinstance(ev, Rag15ApprovalEvidence):
        raise TypeError(f"approval_evidence item must be Rag15ApprovalEvidence, got {type(ev)}")
    if ev.approval_status not in _VALID_APPROVAL_STATUSES:
        raise ValueError(f"Invalid approval_status: {ev.approval_status}")
    if ev.candidate_ref != candidate_ref:
        raise ValueError(
            f"Approval evidence candidate_ref mismatch: evidence bound to {ev.candidate_ref}, candidate_ref is {candidate_ref}"
        )
    if ev.approval_status in ("APPROVED", "REJECTED"):
        if ev.decision_ref is None:
            raise ValueError(f"decision_ref is required for {ev.approval_status} evidence in scope {ev.scope}")
        _validate_artifact_ref(ev.decision_ref, f"decision_ref for {ev.scope}")
    elif ev.approval_status == "PENDING" and ev.decision_ref is not None:
        _validate_artifact_ref(ev.decision_ref, f"decision_ref for {ev.scope}")


def _validate_and_sort_approval_evidence(
    approval_evidence: tuple[Rag15ApprovalEvidence, ...],
    candidate_ref: ImmutableArtifactRef,
) -> tuple[Rag15ApprovalEvidence, ...]:
    evidence_scopes = [ev.scope for ev in approval_evidence]
    if set(evidence_scopes) != set(RAG15_REQUIRED_APPROVAL_SCOPES) or len(evidence_scopes) != len(
        RAG15_REQUIRED_APPROVAL_SCOPES
    ):
        raise ValueError(
            f"Approval evidence must match exact required scopes {RAG15_REQUIRED_APPROVAL_SCOPES}, got {evidence_scopes}"
        )

    for ev in approval_evidence:
        _validate_evidence_item(ev, candidate_ref)

    return tuple(sorted(approval_evidence, key=lambda ev: ev.scope))


def compute_rag15_candidate_ref(
    *,
    generation_provenance: GuidelineGenerationProvenance,
    policy_ref: ImmutableArtifactRef,
    fallback_pins: tuple[Rag15FallbackPin, ...] | tuple[ApprovedGuidelineFallback, ...],
) -> ImmutableArtifactRef:
    """Computes deterministic candidate identity from technical candidate configuration."""
    _validate_artifact_ref(generation_provenance.prompt_ref, "prompt_ref")
    _validate_artifact_ref(generation_provenance.model_ref, "model_ref")
    _validate_artifact_ref(generation_provenance.parser_ref, "parser_ref")
    _validate_artifact_ref(generation_provenance.validator_ref, "validator_ref")
    _validate_artifact_ref(policy_ref, "policy_ref")

    sorted_pins = _extract_and_validate_fallback_pins(fallback_pins)

    payload = {
        "fallback_pins": [
            {
                "artifact_ref": _artifact_payload(pin.artifact_ref),
                "code": pin.code.value,
            }
            for pin in sorted_pins
        ],
        "generation_provenance": {
            "model_ref": _artifact_payload(generation_provenance.model_ref),
            "parser_ref": _artifact_payload(generation_provenance.parser_ref),
            "prompt_ref": _artifact_payload(generation_provenance.prompt_ref),
            "validator_ref": _artifact_payload(generation_provenance.validator_ref),
        },
        "policy_ref": _artifact_payload(policy_ref),
    }
    content_sha256 = _canonical_sha256(payload)
    return ImmutableArtifactRef("rag15-guideline-candidate", "rag15-guideline-v1", content_sha256)


def compute_rag15_pack_ref(
    *,
    candidate_ref: ImmutableArtifactRef,
    approval_evidence: tuple[Rag15ApprovalEvidence, ...],
) -> ImmutableArtifactRef:
    """Computes deterministic pack identity from candidate_ref and ordered approval evidence."""
    _validate_artifact_ref(candidate_ref, "candidate_ref")
    sorted_ev = sorted(approval_evidence, key=lambda ev: ev.scope)
    payload = {
        "approval_evidence": [
            {
                "approval_status": ev.approval_status,
                "candidate_ref": _artifact_payload(ev.candidate_ref),
                "decision_ref": _artifact_payload(ev.decision_ref) if ev.decision_ref is not None else None,
                "scope": ev.scope,
            }
            for ev in sorted_ev
        ],
        "candidate_ref": _artifact_payload(candidate_ref),
    }
    content_sha256 = _canonical_sha256(payload)
    return ImmutableArtifactRef("rag15-approval-pack", "rag15-guideline-v1", content_sha256)


def create_pending_approval_evidence(
    *,
    candidate_ref: ImmutableArtifactRef,
) -> tuple[Rag15ApprovalEvidence, ...]:
    """Builds initial PENDING approval evidence bound to the exact candidate_ref."""
    _validate_artifact_ref(candidate_ref, "candidate_ref")
    return tuple(
        Rag15ApprovalEvidence(
            scope=scope,
            approval_status="PENDING",
            candidate_ref=candidate_ref,
            decision_ref=None,
        )
        for scope in RAG15_REQUIRED_APPROVAL_SCOPES
    )


def build_rag15_approval_pack(
    *,
    model: str,
    policy: VersionedGuidelinePolicy,
    fallbacks: tuple[ApprovedGuidelineFallback, ...] | tuple[Rag15FallbackPin, ...],
    approval_evidence: tuple[Rag15ApprovalEvidence, ...],
    source_revision: str,
) -> Rag15ApprovalPack:
    """Builds a sealed Rag15ApprovalPack bound to candidate provenance, policy, fallbacks, and approval evidence."""
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    if not isinstance(policy, VersionedGuidelinePolicy):
        raise TypeError("policy must be a VersionedGuidelinePolicy")
    if not isinstance(source_revision, str) or not source_revision.strip():
        raise ValueError("source_revision must be a non-empty string")

    generation_provenance = build_candidate_provenance(model=model)
    sorted_pins = _extract_and_validate_fallback_pins(fallbacks)
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=generation_provenance,
        policy_ref=policy.artifact_ref,
        fallback_pins=sorted_pins,
    )
    sorted_evidence = _validate_and_sort_approval_evidence(approval_evidence, candidate_ref)
    pack_ref = compute_rag15_pack_ref(
        candidate_ref=candidate_ref,
        approval_evidence=sorted_evidence,
    )

    return Rag15ApprovalPack(
        generation_provenance=generation_provenance,
        policy_ref=policy.artifact_ref,
        fallback_pins=sorted_pins,
        approval_evidence=sorted_evidence,
        candidate_ref=candidate_ref,
        pack_ref=pack_ref,
        source_revision=source_revision,
    )


def build_rag15_pending_approval_pack(
    *,
    model: str,
    policy: VersionedGuidelinePolicy,
    fallbacks: tuple[ApprovedGuidelineFallback, ...] | tuple[Rag15FallbackPin, ...],
    source_revision: str,
) -> Rag15ApprovalPack:
    """Builds a candidate approval pack with initial PENDING status for all required scopes."""
    provenance = build_candidate_provenance(model=model)
    sorted_pins = _extract_and_validate_fallback_pins(fallbacks)
    candidate_ref = compute_rag15_candidate_ref(
        generation_provenance=provenance,
        policy_ref=policy.artifact_ref,
        fallback_pins=sorted_pins,
    )
    evidence = create_pending_approval_evidence(candidate_ref=candidate_ref)
    return build_rag15_approval_pack(
        model=model,
        policy=policy,
        fallbacks=sorted_pins,
        approval_evidence=evidence,
        source_revision=source_revision,
    )


def _verify_provenance_refs(
    actual: GuidelineGenerationProvenance,
    expected: GuidelineGenerationProvenance,
    issues: list[str],
) -> bool:
    valid = True
    if actual.prompt_ref != expected.prompt_ref:
        issues.append(f"Prompt candidate drift: expected {expected.prompt_ref}, got {actual.prompt_ref}")
        valid = False
    if actual.model_ref != expected.model_ref:
        issues.append(f"Model candidate drift: expected {expected.model_ref}, got {actual.model_ref}")
        valid = False
    if actual.parser_ref != expected.parser_ref:
        issues.append(f"Parser candidate drift: expected {expected.parser_ref}, got {actual.parser_ref}")
        valid = False
    if actual.validator_ref != expected.validator_ref:
        issues.append(f"Validator candidate drift: expected {expected.validator_ref}, got {actual.validator_ref}")
        valid = False
    return valid


def _verify_candidate_drift(provenance: GuidelineGenerationProvenance, issues: list[str]) -> bool:
    model_ref = getattr(provenance, "model_ref", None)
    if not isinstance(model_ref, ImmutableArtifactRef) or model_ref.artifact_code != "guideline-model":
        issues.append(
            f"Invalid model_ref artifact_code: expected 'guideline-model', got {getattr(model_ref, 'artifact_code', None)}"
        )
        return False
    if not model_ref.version.startswith("openai:"):
        issues.append(f"Invalid model_ref version format: expected prefix 'openai:', got {model_ref.version}")
        return False

    model_name = model_ref.version[len("openai:") :]
    if not model_name or model_name.strip() != model_name:
        issues.append(f"Malformed model name in model_ref: {model_name}")
        return False

    try:
        expected_provenance = build_candidate_provenance(model=model_name)
    except Exception as e:
        issues.append(f"Candidate provenance calculation failed: {e}")
        return False

    return _verify_provenance_refs(provenance, expected_provenance, issues)


def _verify_fallback_pins(pins: tuple[Rag15FallbackPin, ...] | object, issues: list[str]) -> bool:
    if not isinstance(pins, (tuple, list)):
        issues.append("fallback_pins must be a tuple")
        return False

    pin_codes: list[GuidelineFallbackCode] = []
    valid = True
    for pin in pins:
        if not isinstance(pin, Rag15FallbackPin):
            issues.append(f"Invalid fallback_pin item type: {type(pin)}")
            valid = False
            continue
        try:
            _validate_artifact_ref(pin.artifact_ref, f"fallback_pin({pin.code})")
        except (ValueError, TypeError) as e:
            issues.append(f"Fallback pin artifact_ref invalid: {e}")
            valid = False
        pin_codes.append(pin.code)

    if set(pin_codes) != set(GuidelineFallbackCode) or len(pin_codes) != len(GuidelineFallbackCode):
        issues.append(
            f"Fallback pins exact set mismatch: expected {len(GuidelineFallbackCode)} unique members, got {len(pin_codes)}"
        )
        valid = False

    return valid


def _verify_evidence_element(
    ev: Rag15ApprovalEvidence,
    candidate_ref: ImmutableArtifactRef,
    issues: list[str],
) -> bool:
    if not isinstance(ev, Rag15ApprovalEvidence):
        issues.append(f"Invalid approval_evidence item type: {type(ev)}")
        return False

    valid = True
    if ev.approval_status not in _VALID_APPROVAL_STATUSES:
        issues.append(f"Invalid approval_status for scope {ev.scope}: {ev.approval_status}")
        valid = False

    if ev.candidate_ref != candidate_ref:
        issues.append(
            f"Approval evidence replay protection failure: evidence scope {ev.scope} bound to {ev.candidate_ref}, pack candidate_ref is {candidate_ref}"
        )
        valid = False

    if ev.approval_status in ("APPROVED", "REJECTED"):
        if ev.decision_ref is None:
            issues.append(f"decision_ref required for {ev.approval_status} evidence in scope {ev.scope}")
            valid = False
        else:
            try:
                _validate_artifact_ref(ev.decision_ref, f"decision_ref for {ev.scope}")
            except (ValueError, TypeError) as e:
                issues.append(f"Decision ref invalid for scope {ev.scope}: {e}")
                valid = False
    elif ev.approval_status == "PENDING" and ev.decision_ref is not None:
        try:
            _validate_artifact_ref(ev.decision_ref, f"decision_ref for {ev.scope}")
        except (ValueError, TypeError) as e:
            issues.append(f"Decision ref invalid for scope {ev.scope}: {e}")
            valid = False

    return valid


def _verify_evidence_collection(
    evidence: tuple[Rag15ApprovalEvidence, ...] | object,
    candidate_ref: ImmutableArtifactRef,
    issues: list[str],
) -> bool:
    if not isinstance(evidence, (tuple, list)):
        issues.append("approval_evidence must be a tuple")
        return False

    valid = True
    scopes: list[str] = []
    for ev in evidence:
        if not _verify_evidence_element(ev, candidate_ref, issues):
            valid = False
        if isinstance(ev, Rag15ApprovalEvidence):
            scopes.append(ev.scope)

    if set(scopes) != set(RAG15_REQUIRED_APPROVAL_SCOPES) or len(scopes) != len(RAG15_REQUIRED_APPROVAL_SCOPES):
        issues.append(
            f"Approval evidence scopes exact set mismatch: expected {RAG15_REQUIRED_APPROVAL_SCOPES}, got {scopes}"
        )
        valid = False

    return valid


def _verify_hashes(pack: Rag15ApprovalPack, issues: list[str]) -> bool:
    valid = True
    try:
        expected_candidate_ref = compute_rag15_candidate_ref(
            generation_provenance=pack.generation_provenance,
            policy_ref=pack.policy_ref,
            fallback_pins=pack.fallback_pins,
        )
        if pack.candidate_ref != expected_candidate_ref:
            issues.append(f"Candidate ref mismatch: expected {expected_candidate_ref}, got {pack.candidate_ref}")
            valid = False
    except Exception as e:
        issues.append(f"Candidate ref recomputation failed: {e}")
        valid = False

    try:
        expected_pack_ref = compute_rag15_pack_ref(
            candidate_ref=pack.candidate_ref,
            approval_evidence=pack.approval_evidence,
        )
        if pack.pack_ref != expected_pack_ref:
            issues.append(f"Pack ref mismatch: expected {expected_pack_ref}, got {pack.pack_ref}")
            valid = False
    except Exception as e:
        issues.append(f"Pack ref recomputation failed: {e}")
        valid = False

    return valid


def _evaluate_aggregate_status(evidence: tuple[Rag15ApprovalEvidence, ...]) -> Rag15ApprovalStatus | None:
    valid_ev = [
        ev
        for ev in evidence
        if isinstance(ev, Rag15ApprovalEvidence) and ev.approval_status in _VALID_APPROVAL_STATUSES
    ]
    if len(valid_ev) != len(RAG15_REQUIRED_APPROVAL_SCOPES):
        return None
    if set(ev.scope for ev in valid_ev) != set(RAG15_REQUIRED_APPROVAL_SCOPES):
        return None

    statuses = [ev.approval_status for ev in valid_ev]
    if "REJECTED" in statuses:
        return "REJECTED"
    if "PENDING" in statuses:
        return "PENDING"
    return "APPROVED"


def _verify_external_evidence(
    evidence: tuple[Rag15ApprovalEvidence, ...],
    verifier: GuidelineApprovalVerifierPort,
    issues: list[str],
) -> bool:
    all_passed = True
    for ev in evidence:
        if ev.decision_ref is None:
            all_passed = False
            issues.append(f"Missing decision_ref for APPROVED scope {ev.scope}")
            continue
        try:
            ver_res = verifier.verify(ev.decision_ref)
            if not isinstance(ver_res, GuidelineApprovalVerificationSuccess):
                all_passed = False
                issues.append(f"Authority verifier failed for decision_ref {ev.decision_ref} in scope {ev.scope}")
        except Exception as e:
            all_passed = False
            issues.append(f"Authority verifier raised exception for scope {ev.scope}: {e}")
    return all_passed


def verify_rag15_approval_pack(
    pack: Rag15ApprovalPack,
    *,
    approval_verifier: GuidelineApprovalVerifierPort,
) -> Rag15ApprovalPackVerification:
    """Pure fail-closed verification of candidate integrity, drift, approval status, and evidence."""
    issues: list[str] = []

    if not isinstance(pack, Rag15ApprovalPack):
        return Rag15ApprovalPackVerification(
            integrity_verified=False,
            approval_status=None,
            approval_evidence_verified=False,
            production_consumable=False,
            issues=("Input is not a Rag15ApprovalPack",),
        )

    drift_ok = _verify_candidate_drift(pack.generation_provenance, issues)

    policy_ok = True
    try:
        _validate_artifact_ref(pack.policy_ref, "policy_ref")
    except (ValueError, TypeError) as e:
        issues.append(f"Policy ref invalid: {e}")
        policy_ok = False

    pins_ok = _verify_fallback_pins(pack.fallback_pins, issues)
    ev_ok = _verify_evidence_collection(pack.approval_evidence, pack.candidate_ref, issues)
    hashes_ok = _verify_hashes(pack, issues)

    integrity_verified = drift_ok and policy_ok and pins_ok and ev_ok and hashes_ok

    computed_status = _evaluate_aggregate_status(pack.approval_evidence) if ev_ok else None

    if computed_status == "APPROVED":
        approval_evidence_verified = _verify_external_evidence(pack.approval_evidence, approval_verifier, issues)
    else:
        approval_evidence_verified = False

    production_consumable = integrity_verified and computed_status == "APPROVED" and approval_evidence_verified

    return Rag15ApprovalPackVerification(
        integrity_verified=integrity_verified,
        approval_status=computed_status,
        approval_evidence_verified=approval_evidence_verified,
        production_consumable=production_consumable,
        issues=tuple(issues),
    )
