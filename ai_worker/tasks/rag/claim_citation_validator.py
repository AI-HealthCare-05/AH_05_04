"""Persistence-free Claim–Citation validation for the RAG runtime target.

This module verifies detached candidate and support-receipt integrity only. It
does not prove that a receipt was issued by a production authority and does not
authorize public release.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    is_valid_immutable_artifact_ref,
)
from ai_worker.tasks.rag.source_member_identity import (
    SourceMemberIdentity,
    SourceMemberKind,
    is_valid_source_member_identity,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CLAIM_SUPPORT_PROJECTION_VERSION = "rag-claim-support-v1"
VALIDATED_SELECTION_PROJECTION_VERSION = "rag-validated-citation-selection-v2"


class CitationSourceType(StrEnum):
    PRESCRIPTION = "PRESCRIPTION"
    KNOWLEDGE_CHUNK = "KNOWLEDGE_CHUNK"
    INTERACTION_RULE = "INTERACTION_RULE"
    LIFESTYLE_GUIDELINE = "LIFESTYLE_GUIDELINE"
    SAFETY_POLICY = "SAFETY_POLICY"


class ClaimKind(StrEnum):
    MEDICAL = "MEDICAL"
    AUXILIARY = "AUXILIARY"
    SAFETY_FALLBACK = "SAFETY_FALLBACK"


class ClaimSupportStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class CandidateValidationExecutionStatus(StrEnum):
    EVALUATED = "EVALUATED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


class CandidateValidationDecision(StrEnum):
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class CandidateValidationReason(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    CLAIM_IDENTITY_INVALID = "CLAIM_IDENTITY_INVALID"
    CITATION_IDENTITY_INVALID = "CITATION_IDENTITY_INVALID"
    EVIDENCE_TYPE_MISMATCH = "EVIDENCE_TYPE_MISMATCH"
    EVIDENCE_PROVENANCE_INVALID = "EVIDENCE_PROVENANCE_INVALID"
    MEDICAL_CLAIM_CITATION_REQUIRED = "MEDICAL_CLAIM_CITATION_REQUIRED"
    MEDICAL_CLAIM_NOT_SUPPORTED = "MEDICAL_CLAIM_NOT_SUPPORTED"
    CLAIM_NOT_SUPPORTED = "CLAIM_NOT_SUPPORTED"
    SUPPORT_RECEIPT_REQUIRED = "SUPPORT_RECEIPT_REQUIRED"
    SUPPORT_RECEIPT_MISMATCH = "SUPPORT_RECEIPT_MISMATCH"


@dataclass(frozen=True, slots=True)
class SourceExecutionProvenance:
    source_code: str
    source_version: str
    member_kind: SourceMemberKind
    endpoint_code: str | None
    operation_code: str | None
    artifact_code: str | None
    artifact_version: str | None
    request_source_decision_ref: ImmutableArtifactRef
    request_member_decision_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class PrescriptionEvidenceRef:
    prescription_version_ref: str
    medication_ref: str
    locator: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class KnowledgeChunkEvidenceRef:
    knowledge_chunk_ref: str
    source_snapshot_ref: ImmutableArtifactRef
    source_version: str
    locator: str
    content_sha256: str
    execution_provenance: SourceExecutionProvenance


@dataclass(frozen=True, slots=True)
class InteractionRuleEvidenceRef:
    interaction_rule_ref: str
    rule_artifact_ref: ImmutableArtifactRef
    source_version: str
    locator: str
    content_sha256: str
    execution_provenance: SourceExecutionProvenance


@dataclass(frozen=True, slots=True)
class LifestyleGuidelineEvidenceRef:
    guideline_evidence_ref: str
    guideline_artifact_ref: ImmutableArtifactRef
    source_version: str
    locator: str
    content_sha256: str
    execution_provenance: SourceExecutionProvenance


@dataclass(frozen=True, slots=True)
class SafetyPolicyEvidenceRef:
    safety_policy_ref: str
    policy_artifact_ref: ImmutableArtifactRef
    source_version: str
    locator: str
    content_sha256: str
    execution_provenance: SourceExecutionProvenance


CitationEvidenceRef = (
    PrescriptionEvidenceRef
    | KnowledgeChunkEvidenceRef
    | InteractionRuleEvidenceRef
    | LifestyleGuidelineEvidenceRef
    | SafetyPolicyEvidenceRef
)


@dataclass(frozen=True, slots=True)
class ClaimTargetRef:
    target_kind: str
    target_ref: str


@dataclass(frozen=True, slots=True)
class GenerationProvenance:
    prompt_ref: ImmutableArtifactRef
    model_ref: ImmutableArtifactRef
    parser_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class ClaimSupportAssertion:
    support_status: ClaimSupportStatus
    assessment_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class ClaimCandidate:
    claim_key: str
    claim_kind: ClaimKind
    text_digest: str
    display_order: int
    support_assertion: ClaimSupportAssertion


@dataclass(frozen=True, slots=True)
class CitationCandidate:
    citation_key: str
    claim_key: str
    source_type: CitationSourceType
    evidence_ref: CitationEvidenceRef
    display_order: int


@dataclass(frozen=True, slots=True)
class ClaimCitationCandidateSet:
    target: ClaimTargetRef
    claims: tuple[ClaimCandidate, ...]
    citations: tuple[CitationCandidate, ...]
    generation_provenance: GenerationProvenance
    validator_policy_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class ClaimSupportVerificationReceipt:
    claim_key: str
    support_status: ClaimSupportStatus
    claim_text_digest: str
    assessment_ref: ImmutableArtifactRef
    verifier_artifact_ref: ImmutableArtifactRef
    projection_sha256: str


@dataclass(frozen=True, slots=True)
class ValidatedCitationSelection:
    candidate_set: ClaimCitationCandidateSet
    support_receipts: tuple[ClaimSupportVerificationReceipt, ...]
    selection_sha256: str


@dataclass(frozen=True, slots=True)
class ClaimCitationValidationOutcome:
    execution_status: CandidateValidationExecutionStatus
    decision: CandidateValidationDecision
    reasons: tuple[CandidateValidationReason, ...]
    validated_selection: ValidatedCitationSelection | None


_EVIDENCE_TYPE_BY_SOURCE = {
    CitationSourceType.PRESCRIPTION: PrescriptionEvidenceRef,
    CitationSourceType.KNOWLEDGE_CHUNK: KnowledgeChunkEvidenceRef,
    CitationSourceType.INTERACTION_RULE: InteractionRuleEvidenceRef,
    CitationSourceType.LIFESTYLE_GUIDELINE: LifestyleGuidelineEvidenceRef,
    CitationSourceType.SAFETY_POLICY: SafetyPolicyEvidenceRef,
}


def _canonical_json_bytes(value: object) -> bytes:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return unicodedata.normalize("NFC", serialized).encode("utf-8")


def _is_nfc_text(value: object) -> bool:
    return type(value) is str and bool(value.strip()) and unicodedata.is_normalized("NFC", value)


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _artifact_payload(value: ImmutableArtifactRef) -> dict[str, str]:
    return {
        "artifact_code": value.artifact_code,
        "version": value.version,
        "content_sha256": value.content_sha256,
    }


def _source_binding_is_valid(value: object) -> bool:
    if type(value) is not SourceExecutionProvenance:
        return False
    identity = SourceMemberIdentity(
        member_kind=value.member_kind,
        endpoint_code=value.endpoint_code,
        operation_code=value.operation_code,
        artifact_code=value.artifact_code,
        artifact_version=value.artifact_version,
    )
    return (
        _is_nfc_text(value.source_code)
        and _is_nfc_text(value.source_version)
        and is_valid_source_member_identity(identity)
        and is_valid_immutable_artifact_ref(value.request_source_decision_ref)
        and is_valid_immutable_artifact_ref(value.request_member_decision_ref)
    )


def _source_binding_payload(value: SourceExecutionProvenance) -> dict[str, object]:
    return {
        "source_code": value.source_code,
        "source_version": value.source_version,
        "member_kind": value.member_kind.value,
        "endpoint_code": value.endpoint_code,
        "operation_code": value.operation_code,
        "artifact_code": value.artifact_code,
        "artifact_version": value.artifact_version,
        "request_source_decision_ref": _artifact_payload(value.request_source_decision_ref),
        "request_member_decision_ref": _artifact_payload(value.request_member_decision_ref),
    }


def _evidence_is_valid(value: CitationEvidenceRef) -> bool:
    if type(value) is PrescriptionEvidenceRef:
        return all(
            _is_nfc_text(item) for item in (value.prescription_version_ref, value.medication_ref, value.locator)
        ) and _is_sha256(value.content_sha256)
    if type(value) is KnowledgeChunkEvidenceRef:
        return (
            all(_is_nfc_text(item) for item in (value.knowledge_chunk_ref, value.source_version, value.locator))
            and is_valid_immutable_artifact_ref(value.source_snapshot_ref)
            and _is_sha256(value.content_sha256)
            and _source_binding_is_valid(value.execution_provenance)
            and value.source_version == value.execution_provenance.source_version
        )
    if type(value) is InteractionRuleEvidenceRef:
        return (
            all(_is_nfc_text(item) for item in (value.interaction_rule_ref, value.source_version, value.locator))
            and is_valid_immutable_artifact_ref(value.rule_artifact_ref)
            and _is_sha256(value.content_sha256)
            and _source_binding_is_valid(value.execution_provenance)
            and value.source_version == value.execution_provenance.source_version
        )
    if type(value) is LifestyleGuidelineEvidenceRef:
        return (
            all(_is_nfc_text(item) for item in (value.guideline_evidence_ref, value.source_version, value.locator))
            and is_valid_immutable_artifact_ref(value.guideline_artifact_ref)
            and _is_sha256(value.content_sha256)
            and _source_binding_is_valid(value.execution_provenance)
            and value.source_version == value.execution_provenance.source_version
        )
    if type(value) is SafetyPolicyEvidenceRef:
        return (
            all(_is_nfc_text(item) for item in (value.safety_policy_ref, value.source_version, value.locator))
            and is_valid_immutable_artifact_ref(value.policy_artifact_ref)
            and _is_sha256(value.content_sha256)
            and _source_binding_is_valid(value.execution_provenance)
            and value.source_version == value.execution_provenance.source_version
        )
    return False


def _evidence_payload(value: CitationEvidenceRef) -> dict[str, object]:
    if type(value) is PrescriptionEvidenceRef:
        return {
            "kind": CitationSourceType.PRESCRIPTION.value,
            "prescription_version_ref": value.prescription_version_ref,
            "medication_ref": value.medication_ref,
            "locator": value.locator,
            "content_sha256": value.content_sha256,
        }
    if type(value) is KnowledgeChunkEvidenceRef:
        return {
            "kind": CitationSourceType.KNOWLEDGE_CHUNK.value,
            "knowledge_chunk_ref": value.knowledge_chunk_ref,
            "source_snapshot_ref": _artifact_payload(value.source_snapshot_ref),
            "source_version": value.source_version,
            "locator": value.locator,
            "content_sha256": value.content_sha256,
            "execution_provenance": _source_binding_payload(value.execution_provenance),
        }
    if type(value) is InteractionRuleEvidenceRef:
        artifact_name = "rule_artifact_ref"
        reference_name = "interaction_rule_ref"
        reference_value = value.interaction_rule_ref
        artifact_value = value.rule_artifact_ref
        kind = CitationSourceType.INTERACTION_RULE.value
    elif type(value) is LifestyleGuidelineEvidenceRef:
        artifact_name = "guideline_artifact_ref"
        reference_name = "guideline_evidence_ref"
        reference_value = value.guideline_evidence_ref
        artifact_value = value.guideline_artifact_ref
        kind = CitationSourceType.LIFESTYLE_GUIDELINE.value
    elif type(value) is SafetyPolicyEvidenceRef:
        artifact_name = "policy_artifact_ref"
        reference_name = "safety_policy_ref"
        reference_value = value.safety_policy_ref
        artifact_value = value.policy_artifact_ref
        kind = CitationSourceType.SAFETY_POLICY.value
    else:
        raise TypeError("unsupported evidence reference")
    return {
        "kind": kind,
        reference_name: reference_value,
        artifact_name: _artifact_payload(artifact_value),
        "source_version": value.source_version,
        "locator": value.locator,
        "content_sha256": value.content_sha256,
        "execution_provenance": _source_binding_payload(value.execution_provenance),
    }


def _citation_payload(citation: CitationCandidate) -> dict[str, object]:
    return {
        "citation_key": citation.citation_key,
        "claim_key": citation.claim_key,
        "source_type": citation.source_type.value,
        "display_order": citation.display_order,
        "evidence_ref": _evidence_payload(citation.evidence_ref),
    }


def _claim_payload(claim: ClaimCandidate) -> dict[str, object]:
    return {
        "claim_key": claim.claim_key,
        "claim_kind": claim.claim_kind.value,
        "text_digest": claim.text_digest,
        "display_order": claim.display_order,
        "support_status": claim.support_assertion.support_status.value,
        "assessment_ref": _artifact_payload(claim.support_assertion.assessment_ref),
    }


def _support_receipt_payload(receipt: ClaimSupportVerificationReceipt) -> dict[str, object]:
    return {
        "claim_key": receipt.claim_key,
        "support_status": receipt.support_status.value,
        "claim_text_digest": receipt.claim_text_digest,
        "assessment_ref": _artifact_payload(receipt.assessment_ref),
        "verifier_artifact_ref": _artifact_payload(receipt.verifier_artifact_ref),
        "projection_sha256": receipt.projection_sha256,
    }


def canonical_claim_support_projection_hash(candidate_set: ClaimCitationCandidateSet, claim_key: str) -> str:
    claim = next(claim for claim in candidate_set.claims if claim.claim_key == claim_key)
    citations = sorted(
        (citation for citation in candidate_set.citations if citation.claim_key == claim_key),
        key=lambda citation: _canonical_json_bytes(_citation_payload(citation)),
    )
    payload = {
        "projection_version": CLAIM_SUPPORT_PROJECTION_VERSION,
        "claim": _claim_payload(claim),
        "citations": [_citation_payload(citation) for citation in citations],
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def canonical_validated_selection_hash(
    candidate_set: ClaimCitationCandidateSet,
    support_receipts: tuple[ClaimSupportVerificationReceipt, ...],
) -> str:
    claims = sorted(candidate_set.claims, key=lambda claim: _canonical_json_bytes(_claim_payload(claim)))
    citations = sorted(candidate_set.citations, key=lambda citation: _canonical_json_bytes(_citation_payload(citation)))
    receipts = sorted(
        support_receipts,
        key=lambda receipt: _canonical_json_bytes(_support_receipt_payload(receipt)),
    )
    payload = {
        "projection_version": VALIDATED_SELECTION_PROJECTION_VERSION,
        "target": {"target_kind": candidate_set.target.target_kind, "target_ref": candidate_set.target.target_ref},
        "claims": [_claim_payload(claim) for claim in claims],
        "citations": [_citation_payload(citation) for citation in citations],
        "generation_provenance": {
            "prompt_ref": _artifact_payload(candidate_set.generation_provenance.prompt_ref),
            "model_ref": _artifact_payload(candidate_set.generation_provenance.model_ref),
            "parser_ref": _artifact_payload(candidate_set.generation_provenance.parser_ref),
        },
        "validator_policy_ref": _artifact_payload(candidate_set.validator_policy_ref),
        "support_receipts": [_support_receipt_payload(receipt) for receipt in receipts],
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _request_shape_reasons(candidate_set: object) -> list[CandidateValidationReason]:
    if type(candidate_set) is not ClaimCitationCandidateSet:
        return [CandidateValidationReason.REQUEST_INVALID]
    if (
        type(candidate_set.target) is not ClaimTargetRef
        or not _is_nfc_text(candidate_set.target.target_kind)
        or not _is_nfc_text(candidate_set.target.target_ref)
        or type(candidate_set.claims) is not tuple
        or type(candidate_set.citations) is not tuple
        or not candidate_set.claims
        or type(candidate_set.generation_provenance) is not GenerationProvenance
        or not all(
            is_valid_immutable_artifact_ref(ref)
            for ref in (
                candidate_set.generation_provenance.prompt_ref,
                candidate_set.generation_provenance.model_ref,
                candidate_set.generation_provenance.parser_ref,
                candidate_set.validator_policy_ref,
            )
        )
    ):
        return [CandidateValidationReason.REQUEST_INVALID]
    return []


def _claim_reasons(claims: tuple[ClaimCandidate, ...]) -> list[CandidateValidationReason]:
    if not all(
        type(claim) is ClaimCandidate
        and _is_nfc_text(claim.claim_key)
        and type(claim.claim_kind) is ClaimKind
        and _is_sha256(claim.text_digest)
        and _is_positive_int(claim.display_order)
        and type(claim.support_assertion) is ClaimSupportAssertion
        and type(claim.support_assertion.support_status) is ClaimSupportStatus
        and is_valid_immutable_artifact_ref(claim.support_assertion.assessment_ref)
        for claim in claims
    ):
        return [CandidateValidationReason.REQUEST_INVALID]
    keys = [claim.claim_key for claim in claims]
    orders = [claim.display_order for claim in claims]
    if len(keys) != len(set(keys)) or sorted(orders) != list(range(1, len(claims) + 1)):
        return [CandidateValidationReason.CLAIM_IDENTITY_INVALID]
    return []


def _citation_reasons(
    citations: tuple[CitationCandidate, ...],
    claim_keys: set[str],
) -> list[CandidateValidationReason]:
    if not all(
        type(citation) is CitationCandidate
        and _is_nfc_text(citation.citation_key)
        and _is_nfc_text(citation.claim_key)
        and type(citation.source_type) is CitationSourceType
        and _is_positive_int(citation.display_order)
        for citation in citations
    ):
        return [CandidateValidationReason.CITATION_IDENTITY_INVALID]
    keys = [citation.citation_key for citation in citations]
    orders = [citation.display_order for citation in citations]
    reasons: list[CandidateValidationReason] = []
    if (
        len(keys) != len(set(keys))
        or sorted(orders) != list(range(1, len(citations) + 1))
        or any(citation.claim_key not in claim_keys for citation in citations)
    ):
        reasons.append(CandidateValidationReason.CITATION_IDENTITY_INVALID)
    if any(type(citation.evidence_ref) is not _EVIDENCE_TYPE_BY_SOURCE[citation.source_type] for citation in citations):
        reasons.append(CandidateValidationReason.EVIDENCE_TYPE_MISMATCH)
    elif any(not _evidence_is_valid(citation.evidence_ref) for citation in citations):
        reasons.append(CandidateValidationReason.EVIDENCE_PROVENANCE_INVALID)
    return reasons


def _support_reasons(
    candidate_set: ClaimCitationCandidateSet,
    receipts: object,
) -> list[CandidateValidationReason]:
    reasons: list[CandidateValidationReason] = []
    citations_by_claim = {
        claim.claim_key: tuple(
            citation for citation in candidate_set.citations if citation.claim_key == claim.claim_key
        )
        for claim in candidate_set.claims
    }
    for claim in candidate_set.claims:
        if claim.claim_kind is ClaimKind.MEDICAL and not citations_by_claim[claim.claim_key]:
            reasons.append(CandidateValidationReason.MEDICAL_CLAIM_CITATION_REQUIRED)
        status = claim.support_assertion.support_status
        publishable = status is ClaimSupportStatus.SUPPORTED or (
            claim.claim_kind is ClaimKind.AUXILIARY and status is ClaimSupportStatus.PARTIALLY_SUPPORTED
        )
        if not publishable and claim.claim_kind is ClaimKind.MEDICAL:
            reasons.append(CandidateValidationReason.MEDICAL_CLAIM_NOT_SUPPORTED)
        elif not publishable:
            reasons.append(CandidateValidationReason.CLAIM_NOT_SUPPORTED)
    if type(receipts) is not tuple or not all(
        type(receipt) is ClaimSupportVerificationReceipt
        and _is_nfc_text(receipt.claim_key)
        and type(receipt.support_status) is ClaimSupportStatus
        and _is_sha256(receipt.claim_text_digest)
        and is_valid_immutable_artifact_ref(receipt.assessment_ref)
        and is_valid_immutable_artifact_ref(receipt.verifier_artifact_ref)
        and _is_sha256(receipt.projection_sha256)
        for receipt in receipts
    ):
        reasons.append(CandidateValidationReason.SUPPORT_RECEIPT_MISMATCH)
        return reasons
    receipts_by_claim = {receipt.claim_key: receipt for receipt in receipts}
    if len(receipts_by_claim) != len(receipts) or set(receipts_by_claim) != {
        claim.claim_key for claim in candidate_set.claims
    }:
        reasons.append(CandidateValidationReason.SUPPORT_RECEIPT_REQUIRED)
        return reasons
    for claim in candidate_set.claims:
        receipt = receipts_by_claim[claim.claim_key]
        if (
            receipt.claim_key != claim.claim_key
            or receipt.support_status is not claim.support_assertion.support_status
            or receipt.claim_text_digest != claim.text_digest
            or receipt.assessment_ref != claim.support_assertion.assessment_ref
            or receipt.projection_sha256 != canonical_claim_support_projection_hash(candidate_set, claim.claim_key)
        ):
            reasons.append(CandidateValidationReason.SUPPORT_RECEIPT_MISMATCH)
    return reasons


def _unique_reasons(reasons: list[CandidateValidationReason]) -> tuple[CandidateValidationReason, ...]:
    return tuple(dict.fromkeys(reasons))


def validate_claim_citations(
    candidate_set: ClaimCitationCandidateSet,
    support_receipts: tuple[ClaimSupportVerificationReceipt, ...],
) -> ClaimCitationValidationOutcome:
    """Validate a detached candidate set; success is not public-release authority."""

    reasons = _request_shape_reasons(candidate_set)
    if reasons:
        return ClaimCitationValidationOutcome(
            execution_status=CandidateValidationExecutionStatus.VALIDATION_ERROR,
            decision=CandidateValidationDecision.REJECTED,
            reasons=_unique_reasons(reasons),
            validated_selection=None,
        )
    reasons.extend(_claim_reasons(candidate_set.claims))
    if not reasons:
        reasons.extend(_citation_reasons(candidate_set.citations, {claim.claim_key for claim in candidate_set.claims}))
    if reasons:
        return ClaimCitationValidationOutcome(
            execution_status=CandidateValidationExecutionStatus.VALIDATION_ERROR,
            decision=CandidateValidationDecision.REJECTED,
            reasons=_unique_reasons(reasons),
            validated_selection=None,
        )
    reasons.extend(_support_reasons(candidate_set, support_receipts))
    if reasons:
        dependency_reasons = {
            CandidateValidationReason.SUPPORT_RECEIPT_REQUIRED,
            CandidateValidationReason.SUPPORT_RECEIPT_MISMATCH,
        }
        status = (
            CandidateValidationExecutionStatus.DEPENDENCY_ERROR
            if any(reason in dependency_reasons for reason in reasons)
            else CandidateValidationExecutionStatus.EVALUATED
        )
        return ClaimCitationValidationOutcome(
            execution_status=status,
            decision=CandidateValidationDecision.REJECTED,
            reasons=_unique_reasons(reasons),
            validated_selection=None,
        )
    canonical_receipts = tuple(sorted(support_receipts, key=lambda receipt: receipt.claim_key.encode("utf-8")))
    return ClaimCitationValidationOutcome(
        execution_status=CandidateValidationExecutionStatus.EVALUATED,
        decision=CandidateValidationDecision.VALIDATED,
        reasons=(),
        validated_selection=ValidatedCitationSelection(
            candidate_set=candidate_set,
            support_receipts=canonical_receipts,
            selection_sha256=canonical_validated_selection_hash(candidate_set, canonical_receipts),
        ),
    )
