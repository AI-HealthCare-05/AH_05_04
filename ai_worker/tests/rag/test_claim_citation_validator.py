from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from ai_worker.tasks.rag.claim_citation_validator import (
    CandidateValidationDecision,
    CandidateValidationReason,
    CitationCandidate,
    CitationEvidenceRef,
    CitationSourceType,
    ClaimCandidate,
    ClaimCitationCandidateSet,
    ClaimKind,
    ClaimSupportAssertion,
    ClaimSupportStatus,
    ClaimSupportVerificationReceipt,
    ClaimTargetRef,
    GenerationProvenance,
    InteractionRuleEvidenceRef,
    KnowledgeChunkEvidenceRef,
    LifestyleGuidelineEvidenceRef,
    PrescriptionEvidenceRef,
    SafetyPolicyEvidenceRef,
    SourceExecutionProvenance,
    SourceMemberKind,
    canonical_claim_support_projection_hash,
    validate_claim_citations,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef

_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_D = "d" * 64


def _artifact(code: str, digest: str = _A) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(artifact_code=code, version="v1", content_sha256=digest)


def _source_binding(
    source_code: str = "knowledge-source",
    source_version: str = "2026-09-01",
) -> SourceExecutionProvenance:
    return SourceExecutionProvenance(
        source_code=source_code,
        source_version=source_version,
        member_kind=SourceMemberKind.ARTIFACT_MEMBER,
        endpoint_code=None,
        operation_code=None,
        artifact_code=f"{source_code}-artifact",
        artifact_version=source_version,
        request_source_decision_ref=_artifact(f"{source_code}-decision", _B),
        request_member_decision_ref=_artifact(f"{source_code}-member-decision", _C),
    )


def _evidence(source_type: CitationSourceType):
    if source_type is CitationSourceType.PRESCRIPTION:
        return PrescriptionEvidenceRef(
            prescription_version_ref="rx-version-001",
            medication_ref="medication-001",
            locator="medication[0]",
            content_sha256=_A,
        )
    if source_type is CitationSourceType.KNOWLEDGE_CHUNK:
        return KnowledgeChunkEvidenceRef(
            knowledge_chunk_ref="knowledge-chunk-001",
            source_snapshot_ref=_artifact("knowledge-snapshot"),
            source_version="2026-09-01",
            locator="section-1",
            content_sha256=_A,
            execution_provenance=_source_binding(),
        )
    if source_type is CitationSourceType.INTERACTION_RULE:
        return InteractionRuleEvidenceRef(
            interaction_rule_ref="interaction-rule-001",
            rule_artifact_ref=_artifact("interaction-rule"),
            source_version="rules-v1",
            locator="rule-1",
            content_sha256=_A,
            execution_provenance=_source_binding("interaction-source", "rules-v1"),
        )
    if source_type is CitationSourceType.LIFESTYLE_GUIDELINE:
        return LifestyleGuidelineEvidenceRef(
            guideline_evidence_ref="guideline-evidence-001",
            guideline_artifact_ref=_artifact("guideline"),
            source_version="guideline-v1",
            locator="recommendation-1",
            content_sha256=_A,
            execution_provenance=_source_binding("guideline-source", "guideline-v1"),
        )
    return SafetyPolicyEvidenceRef(
        safety_policy_ref="safety-policy-001",
        policy_artifact_ref=_artifact("safety-policy"),
        source_version="safety-v1",
        locator="policy-1",
        content_sha256=_A,
        execution_provenance=_source_binding("safety-source", "safety-v1"),
    )


def _candidate_set(
    *,
    source_type: CitationSourceType = CitationSourceType.KNOWLEDGE_CHUNK,
    support_status: ClaimSupportStatus = ClaimSupportStatus.SUPPORTED,
    claim_kind: ClaimKind = ClaimKind.MEDICAL,
    evidence: CitationEvidenceRef | None = None,
) -> ClaimCitationCandidateSet:
    citation = CitationCandidate(
        citation_key="citation-001",
        claim_key="claim-001",
        source_type=source_type,
        evidence_ref=_evidence(source_type) if evidence is None else evidence,
        display_order=1,
    )
    claim = ClaimCandidate(
        claim_key="claim-001",
        claim_kind=claim_kind,
        text_digest=_D,
        display_order=1,
        support_assertion=ClaimSupportAssertion(
            support_status=support_status,
            assessment_ref=_artifact("support-assessment", _B),
        ),
    )
    return ClaimCitationCandidateSet(
        target=ClaimTargetRef(target_kind="GUIDE", target_ref="guide-run-001"),
        claims=(claim,),
        citations=(citation,),
        generation_provenance=GenerationProvenance(
            prompt_ref=_artifact("prompt"),
            model_ref=_artifact("model", _B),
            parser_ref=_artifact("parser", _C),
        ),
        validator_policy_ref=_artifact("claim-citation-validator", _D),
    )


def _support_receipt(candidate_set: ClaimCitationCandidateSet) -> ClaimSupportVerificationReceipt:
    claim = candidate_set.claims[0]
    return ClaimSupportVerificationReceipt(
        claim_key=claim.claim_key,
        support_status=claim.support_assertion.support_status,
        claim_text_digest=claim.text_digest,
        assessment_ref=claim.support_assertion.assessment_ref,
        verifier_artifact_ref=_artifact("support-verifier", _C),
        projection_sha256=canonical_claim_support_projection_hash(candidate_set, claim.claim_key),
    )


@pytest.mark.parametrize("source_type", tuple(CitationSourceType))
def test_validates_each_target_citation_source_type_with_exact_support_receipt(
    source_type: CitationSourceType,
) -> None:
    candidate_set = _candidate_set(source_type=source_type)

    outcome = validate_claim_citations(candidate_set, (_support_receipt(candidate_set),))

    assert outcome.decision is CandidateValidationDecision.VALIDATED
    assert outcome.reasons == ()
    assert outcome.validated_selection is not None
    assert outcome.validated_selection.candidate_set == candidate_set
    assert outcome.validated_selection.support_receipts == (_support_receipt(candidate_set),)


@pytest.mark.parametrize(
    ("claim_kind", "support_status", "expected_decision", "expected_reason"),
    (
        (ClaimKind.MEDICAL, ClaimSupportStatus.SUPPORTED, CandidateValidationDecision.VALIDATED, None),
        (
            ClaimKind.MEDICAL,
            ClaimSupportStatus.PARTIALLY_SUPPORTED,
            CandidateValidationDecision.REJECTED,
            CandidateValidationReason.MEDICAL_CLAIM_NOT_SUPPORTED,
        ),
        (
            ClaimKind.MEDICAL,
            ClaimSupportStatus.CONTRADICTED,
            CandidateValidationDecision.REJECTED,
            CandidateValidationReason.MEDICAL_CLAIM_NOT_SUPPORTED,
        ),
        (
            ClaimKind.MEDICAL,
            ClaimSupportStatus.NOT_SUPPORTED,
            CandidateValidationDecision.REJECTED,
            CandidateValidationReason.MEDICAL_CLAIM_NOT_SUPPORTED,
        ),
        (ClaimKind.AUXILIARY, ClaimSupportStatus.SUPPORTED, CandidateValidationDecision.VALIDATED, None),
        (
            ClaimKind.AUXILIARY,
            ClaimSupportStatus.PARTIALLY_SUPPORTED,
            CandidateValidationDecision.VALIDATED,
            None,
        ),
        (
            ClaimKind.AUXILIARY,
            ClaimSupportStatus.CONTRADICTED,
            CandidateValidationDecision.REJECTED,
            CandidateValidationReason.CLAIM_NOT_SUPPORTED,
        ),
        (
            ClaimKind.AUXILIARY,
            ClaimSupportStatus.NOT_SUPPORTED,
            CandidateValidationDecision.REJECTED,
            CandidateValidationReason.CLAIM_NOT_SUPPORTED,
        ),
        (ClaimKind.SAFETY_FALLBACK, ClaimSupportStatus.SUPPORTED, CandidateValidationDecision.VALIDATED, None),
        (
            ClaimKind.SAFETY_FALLBACK,
            ClaimSupportStatus.PARTIALLY_SUPPORTED,
            CandidateValidationDecision.REJECTED,
            CandidateValidationReason.CLAIM_NOT_SUPPORTED,
        ),
        (
            ClaimKind.SAFETY_FALLBACK,
            ClaimSupportStatus.CONTRADICTED,
            CandidateValidationDecision.REJECTED,
            CandidateValidationReason.CLAIM_NOT_SUPPORTED,
        ),
        (
            ClaimKind.SAFETY_FALLBACK,
            ClaimSupportStatus.NOT_SUPPORTED,
            CandidateValidationDecision.REJECTED,
            CandidateValidationReason.CLAIM_NOT_SUPPORTED,
        ),
        (
            ClaimKind.AUXILIARY,
            cast(ClaimSupportStatus, "UNSUPPORTED_VALUE"),
            CandidateValidationDecision.REJECTED,
            CandidateValidationReason.REQUEST_INVALID,
        ),
    ),
)
def test_all_claim_kind_and_support_status_combinations_fail_closed_except_allowed_pairs(
    claim_kind: ClaimKind,
    support_status: ClaimSupportStatus,
    expected_decision: CandidateValidationDecision,
    expected_reason: CandidateValidationReason | None,
) -> None:
    candidate_set = _candidate_set(support_status=support_status, claim_kind=claim_kind)
    support_receipts = (_support_receipt(candidate_set),) if type(support_status) is ClaimSupportStatus else ()

    outcome = validate_claim_citations(candidate_set, support_receipts)

    assert outcome.decision is expected_decision
    if expected_reason is None:
        assert outcome.reasons == ()
        assert outcome.validated_selection is not None
    else:
        assert expected_reason in outcome.reasons
        assert outcome.validated_selection is None


def test_rejects_medical_claim_without_a_citation() -> None:
    candidate_set = replace(_candidate_set(), citations=())

    outcome = validate_claim_citations(candidate_set, ())

    assert outcome.decision is CandidateValidationDecision.REJECTED
    assert CandidateValidationReason.MEDICAL_CLAIM_CITATION_REQUIRED in outcome.reasons


def test_rejects_generator_supported_label_without_an_exact_bound_receipt() -> None:
    candidate_set = _candidate_set()
    receipt = replace(_support_receipt(candidate_set), projection_sha256=_A)

    outcome = validate_claim_citations(candidate_set, (receipt,))

    assert outcome.decision is CandidateValidationDecision.REJECTED
    assert CandidateValidationReason.SUPPORT_RECEIPT_MISMATCH in outcome.reasons


def test_malformed_support_receipt_fails_closed_without_crossing_as_an_exception() -> None:
    candidate_set = _candidate_set()
    malformed = replace(_support_receipt(candidate_set), claim_key=cast(str, []))

    outcome = validate_claim_citations(candidate_set, (malformed,))

    assert outcome.decision is CandidateValidationDecision.REJECTED
    assert outcome.reasons == (CandidateValidationReason.SUPPORT_RECEIPT_MISMATCH,)


def test_rejects_source_type_and_tagged_evidence_ref_mismatch() -> None:
    candidate_set = _candidate_set(
        source_type=CitationSourceType.KNOWLEDGE_CHUNK,
        evidence=_evidence(CitationSourceType.SAFETY_POLICY),
    )

    outcome = validate_claim_citations(candidate_set, (_support_receipt(candidate_set),))

    assert outcome.decision is CandidateValidationDecision.REJECTED
    assert CandidateValidationReason.EVIDENCE_TYPE_MISMATCH in outcome.reasons


def test_rejects_source_member_with_mixed_endpoint_and_artifact_identity() -> None:
    evidence = _evidence(CitationSourceType.KNOWLEDGE_CHUNK)
    malformed_binding = replace(
        evidence.execution_provenance,
        endpoint_code="knowledge-endpoint",
        operation_code="search",
    )
    candidate_set = _candidate_set(evidence=replace(evidence, execution_provenance=malformed_binding))

    outcome = validate_claim_citations(candidate_set, ())

    assert outcome.decision is CandidateValidationDecision.REJECTED
    assert outcome.reasons == (CandidateValidationReason.EVIDENCE_PROVENANCE_INVALID,)


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    (
        (
            lambda value: replace(value, claims=(value.claims[0], value.claims[0])),
            CandidateValidationReason.CLAIM_IDENTITY_INVALID,
        ),
        (
            lambda value: replace(value, citations=(replace(value.citations[0], display_order=True),)),
            CandidateValidationReason.CITATION_IDENTITY_INVALID,
        ),
        (
            lambda value: replace(value, target=replace(value.target, target_ref="guide-e\u0301")),
            CandidateValidationReason.REQUEST_INVALID,
        ),
        (
            lambda value: replace(value, claims=(replace(value.claims[0], text_digest="A" * 64),)),
            CandidateValidationReason.REQUEST_INVALID,
        ),
    ),
)
def test_rejects_noncanonical_or_ambiguous_python_shapes(mutate, expected_reason: CandidateValidationReason) -> None:
    candidate_set = mutate(_candidate_set())

    outcome = validate_claim_citations(candidate_set, ())

    assert outcome.decision is CandidateValidationDecision.REJECTED
    assert expected_reason in outcome.reasons


def test_selection_hash_is_deterministic_when_input_order_changes() -> None:
    base = _candidate_set(claim_kind=ClaimKind.AUXILIARY)
    second_claim = replace(
        base.claims[0],
        claim_key="claim-002",
        text_digest=_A,
        display_order=2,
    )
    second_citation = replace(
        base.citations[0],
        citation_key="citation-002",
        claim_key="claim-002",
        display_order=2,
    )
    ordered = replace(base, claims=(base.claims[0], second_claim), citations=(base.citations[0], second_citation))
    reversed_input = replace(
        ordered, claims=tuple(reversed(ordered.claims)), citations=tuple(reversed(ordered.citations))
    )
    receipts = (
        _support_receipt(ordered),
        ClaimSupportVerificationReceipt(
            claim_key="claim-002",
            support_status=second_claim.support_assertion.support_status,
            claim_text_digest=second_claim.text_digest,
            assessment_ref=second_claim.support_assertion.assessment_ref,
            verifier_artifact_ref=_artifact("support-verifier", _C),
            projection_sha256=canonical_claim_support_projection_hash(ordered, "claim-002"),
        ),
    )

    ordered_outcome = validate_claim_citations(ordered, receipts)
    reversed_outcome = validate_claim_citations(reversed_input, receipts)

    assert ordered_outcome.validated_selection is not None
    assert reversed_outcome.validated_selection is not None
    assert ordered_outcome.validated_selection.selection_sha256 == reversed_outcome.validated_selection.selection_sha256


def test_canonical_projection_hashes_match_the_reviewed_v2_fixture() -> None:
    candidate_set = _candidate_set()
    outcome = validate_claim_citations(candidate_set, (_support_receipt(candidate_set),))

    assert canonical_claim_support_projection_hash(candidate_set, "claim-001") == (
        "0976fa757c4cb9b4f5c87b9bed4b001ac2d2e9190ba4c3a6d41903b0614c9d7a"
    )
    assert outcome.validated_selection is not None
    assert outcome.validated_selection.selection_sha256 == (
        "e5e54a8146f4e6a2771e9d35730351475361773a2b2df4832b6c8a60dc354b9f"
    )


def test_validates_endpoint_operation_with_nullable_operation_code() -> None:
    binding = SourceExecutionProvenance(
        source_code="knowledge-source",
        source_version="2026-09-01",
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="endpoint-001",
        operation_code=None,
        artifact_code=None,
        artifact_version=None,
        request_source_decision_ref=_artifact("knowledge-source-decision", _B),
        request_member_decision_ref=_artifact("knowledge-source-member-decision", _C),
    )
    evidence = KnowledgeChunkEvidenceRef(
        knowledge_chunk_ref="knowledge-chunk-001",
        source_snapshot_ref=_artifact("knowledge-snapshot"),
        source_version="2026-09-01",
        locator="section-1",
        content_sha256=_A,
        execution_provenance=binding,
    )
    candidate_set = _candidate_set(evidence=evidence)
    receipt = _support_receipt(candidate_set)

    outcome = validate_claim_citations(candidate_set, (receipt,))

    assert outcome.decision is CandidateValidationDecision.VALIDATED
    assert outcome.reasons == ()
    assert outcome.validated_selection is not None
    assert outcome.validated_selection.selection_sha256 is not None


@pytest.mark.parametrize(
    "invalid_kind",
    ["ENDPOINT_OPERATION", "ARTIFACT_MEMBER", "UNSUPPORTED"],
)
def test_rejects_non_enum_member_kind_with_typed_rejection_without_exception(invalid_kind: object) -> None:
    binding = SourceExecutionProvenance(
        source_code="knowledge-source",
        source_version="2026-09-01",
        member_kind=cast(SourceMemberKind, invalid_kind),
        endpoint_code="endpoint-001",
        operation_code=None,
        artifact_code=None,
        artifact_version=None,
        request_source_decision_ref=_artifact("knowledge-source-decision", _B),
        request_member_decision_ref=_artifact("knowledge-source-member-decision", _C),
    )
    evidence = KnowledgeChunkEvidenceRef(
        knowledge_chunk_ref="knowledge-chunk-001",
        source_snapshot_ref=_artifact("knowledge-snapshot"),
        source_version="2026-09-01",
        locator="section-1",
        content_sha256=_A,
        execution_provenance=binding,
    )
    candidate_set = _candidate_set(evidence=evidence)
    receipt = ClaimSupportVerificationReceipt(
        claim_key=candidate_set.claims[0].claim_key,
        support_status=candidate_set.claims[0].support_assertion.support_status,
        claim_text_digest=candidate_set.claims[0].text_digest,
        assessment_ref=candidate_set.claims[0].support_assertion.assessment_ref,
        verifier_artifact_ref=_artifact("support-verifier", _C),
        projection_sha256=_A,
    )

    outcome = validate_claim_citations(candidate_set, (receipt,))

    assert outcome.decision is CandidateValidationDecision.REJECTED
    assert CandidateValidationReason.EVIDENCE_PROVENANCE_INVALID in outcome.reasons
