from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from ai_worker.tasks.rag.citation_authorization import (
    GuardDecision,
    GuardOperation,
    OriginRequestGuardBinding,
    RuntimeAuthorizationBinding,
    RuntimeEnvironment,
    _authorization_entries,
    build_citation_authorization_request,
    canonical_scope_manifest_hash,
)
from ai_worker.tasks.rag.claim_citation_validator import (
    CitationCandidate,
    CitationSourceType,
    ClaimCandidate,
    ClaimCitationCandidateSet,
    ClaimKind,
    ClaimSupportAssertion,
    ClaimSupportStatus,
    ClaimSupportVerificationReceipt,
    ClaimTargetRef,
    GenerationProvenance,
    KnowledgeChunkEvidenceRef,
    SourceExecutionProvenance,
    canonical_claim_support_projection_hash,
    canonical_validated_selection_hash,
    validate_claim_citations,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.guide_evidence_handoff import (
    RequestDecisionStage,
    SensitiveText,
    VerifiedGuideEvidenceSelection,
    compute_guide_evidence_handoff_hash,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SourceSnapshotMemberKind
from ai_worker.tasks.rag.source_member_identity import (
    ENDPOINT_MEMBER_CONTRACT_VERSION,
    ENDPOINT_MEMBER_OPERATION_CODE_POLICY,
    SOURCE_MEMBER_IDENTITY_CONTRACT_VERSION,
    EndpointMemberOperationCodePolicy,
    SourceMemberIdentity,
    SourceMemberIdentityError,
    SourceMemberIdentityReason,
    SourceMemberKind,
    is_valid_source_member_identity,
    member_kind_from_persisted,
    persisted_member_kind_value,
    source_member_identity_payload,
    try_member_kind_from_persisted,
    validate_source_member_identity,
    verify_member_authority_binding,
)

# -----------------------------------------------------------------------------
# S1: Golden Vectors calculated on pristine origin/develop (66328a09)
# Any change to these hashes represents an unintended contract break.
# -----------------------------------------------------------------------------
GOLDEN_H1_ENDPOINT = "c798d7f857f688d84fccb445c03193624721a59b1bc5f6506828164f5ed6ddc0"
GOLDEN_H1_ARTIFACT = "0976fa757c4cb9b4f5c87b9bed4b001ac2d2e9190ba4c3a6d41903b0614c9d7a"

GOLDEN_H2_ENDPOINT = "31a5ec2955e8c36f584f4bd153a7c9350daeafdcc9c393387c30280cf06d7d2a"
GOLDEN_H2_ARTIFACT = "e5e54a8146f4e6a2771e9d35730351475361773a2b2df4832b6c8a60dc354b9f"

GOLDEN_H3_ENDPOINT = "0e7ae8cec219ae46a39b486931cb4f99675cdfd3cb18484257a2eec7eba7573e"
GOLDEN_H3_ARTIFACT = "a1c277c295e59b6375452dd7a829d13e57a030b89e3b9bc84dd99439c70ae396"

GOLDEN_H4_ENDPOINT = "b424e2515ae162cefd3ef2b373343307b968ecf22f1094ab42f41d72472b472a"
GOLDEN_H4_ARTIFACT = "74105ba87a0c4e326b803e7ba7f559d71c9b3dfff1a43c6c60ff84f6096317a3"

GOLDEN_H5_ENDPOINT = "db446fd35f7b984be5d0f1085bd1b714792bc24a019dea3225fd1f4371d9a3ee"
GOLDEN_H5_ARTIFACT = "2ffdd800d13d71e93c22b9b30bb2e38fbb367a55aed51460f1ed0c5a00d6a74a"

_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_D = "d" * 64


def _art(code: str, digest: str = _A) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(artifact_code=code, version="v1", content_sha256=digest)


def _golden_binding_ep() -> SourceExecutionProvenance:
    return SourceExecutionProvenance(
        source_code="knowledge-source",
        source_version="2026-09-01",
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="endpoint-001",
        operation_code="get_knowledge",
        artifact_code=None,
        artifact_version=None,
        request_source_decision_ref=_art("knowledge-source-decision", _B),
        request_member_decision_ref=_art("knowledge-source-member-decision", _C),
    )


def _golden_binding_art() -> SourceExecutionProvenance:
    return SourceExecutionProvenance(
        source_code="knowledge-source",
        source_version="2026-09-01",
        member_kind=SourceMemberKind.ARTIFACT_MEMBER,
        endpoint_code=None,
        operation_code=None,
        artifact_code="knowledge-source-artifact",
        artifact_version="2026-09-01",
        request_source_decision_ref=_art("knowledge-source-decision", _B),
        request_member_decision_ref=_art("knowledge-source-member-decision", _C),
    )


def _make_golden_candidate_set(binding: SourceExecutionProvenance) -> ClaimCitationCandidateSet:
    evidence = KnowledgeChunkEvidenceRef(
        knowledge_chunk_ref="knowledge-chunk-001",
        source_snapshot_ref=_art("knowledge-snapshot"),
        source_version="2026-09-01",
        locator="section-1",
        content_sha256=_A,
        execution_provenance=binding,
    )
    citation = CitationCandidate(
        citation_key="citation-001",
        claim_key="claim-001",
        source_type=CitationSourceType.KNOWLEDGE_CHUNK,
        evidence_ref=evidence,
        display_order=1,
    )
    claim = ClaimCandidate(
        claim_key="claim-001",
        claim_kind=ClaimKind.MEDICAL,
        text_digest=_D,
        display_order=1,
        support_assertion=ClaimSupportAssertion(
            support_status=ClaimSupportStatus.SUPPORTED,
            assessment_ref=_art("support-assessment", _B),
        ),
    )
    return ClaimCitationCandidateSet(
        target=ClaimTargetRef(target_kind="GUIDE", target_ref="guide-run-001"),
        claims=(claim,),
        citations=(citation,),
        generation_provenance=GenerationProvenance(
            prompt_ref=_art("prompt"),
            model_ref=_art("model", _B),
            parser_ref=_art("parser", _C),
        ),
        validator_policy_ref=_art("claim-citation-validator", _D),
    )


def _make_golden_support_receipt(candidate_set: ClaimCitationCandidateSet) -> ClaimSupportVerificationReceipt:
    claim = candidate_set.claims[0]
    return ClaimSupportVerificationReceipt(
        claim_key=claim.claim_key,
        support_status=claim.support_assertion.support_status,
        claim_text_digest=claim.text_digest,
        assessment_ref=claim.support_assertion.assessment_ref,
        verifier_artifact_ref=_art("support-verifier", _C),
        projection_sha256=canonical_claim_support_projection_hash(candidate_set, claim.claim_key),
    )


# -----------------------------------------------------------------------------
# Module and Policy Constants Tests
# -----------------------------------------------------------------------------
def test_contract_versions_and_policy() -> None:
    assert SOURCE_MEMBER_IDENTITY_CONTRACT_VERSION == "source-member-identity-v1"
    assert ENDPOINT_MEMBER_CONTRACT_VERSION == "endpoint-member-contract-v1"
    assert ENDPOINT_MEMBER_OPERATION_CODE_POLICY is EndpointMemberOperationCodePolicy.NULLABLE


# -----------------------------------------------------------------------------
# Identity Validation Tests
# -----------------------------------------------------------------------------
def test_endpoint_operation_with_none_operation_code_is_valid() -> None:
    identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="PRODUCT_API",
        operation_code=None,
    )
    assert validate_source_member_identity(identity) == ()
    assert is_valid_source_member_identity(identity) is True


def test_endpoint_operation_with_non_null_operation_code_is_valid() -> None:
    identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="PRODUCT_API",
        operation_code="LIST_PRODUCTS",
    )
    assert validate_source_member_identity(identity) == ()
    assert is_valid_source_member_identity(identity) is True


@pytest.mark.parametrize("invalid_op", ["", "   ", "ge\u0301t_guide"])  # NFD combining char
def test_endpoint_operation_rejects_invalid_operation_code(invalid_op: str) -> None:
    identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="PRODUCT_API",
        operation_code=invalid_op,
    )
    assert validate_source_member_identity(identity) == (SourceMemberIdentityReason.OPERATION_CODE_INVALID,)
    assert is_valid_source_member_identity(identity) is False


@pytest.mark.parametrize("invalid_ep", [None, "", "   ", "api\u0301"])
def test_endpoint_operation_rejects_invalid_or_missing_endpoint_code(invalid_ep: str | None) -> None:
    identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code=invalid_ep,
        operation_code="LIST_PRODUCTS",
    )
    assert validate_source_member_identity(identity) == (SourceMemberIdentityReason.ENDPOINT_CODE_REQUIRED,)


def test_endpoint_operation_rejects_artifact_fields() -> None:
    identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="PRODUCT_API",
        artifact_code="art-001",
    )
    assert validate_source_member_identity(identity) == (SourceMemberIdentityReason.ARTIFACT_FIELDS_FORBIDDEN,)


def test_artifact_member_is_valid_when_well_formed() -> None:
    identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ARTIFACT_MEMBER,
        artifact_code="guide_doc",
        artifact_version="2026-09-01",
    )
    assert validate_source_member_identity(identity) == ()
    assert is_valid_source_member_identity(identity) is True


def test_artifact_member_symmetric_rejections() -> None:
    # 1. Missing artifact_code
    id1 = SourceMemberIdentity(
        member_kind=SourceMemberKind.ARTIFACT_MEMBER,
        artifact_code=None,
        artifact_version="v1",
    )
    assert validate_source_member_identity(id1) == (SourceMemberIdentityReason.ARTIFACT_CODE_REQUIRED,)

    # 2. Missing artifact_version
    id2 = SourceMemberIdentity(
        member_kind=SourceMemberKind.ARTIFACT_MEMBER,
        artifact_code="doc",
        artifact_version=None,
    )
    assert validate_source_member_identity(id2) == (SourceMemberIdentityReason.ARTIFACT_VERSION_REQUIRED,)

    # 3. Endpoint code present
    id3 = SourceMemberIdentity(
        member_kind=SourceMemberKind.ARTIFACT_MEMBER,
        artifact_code="doc",
        artifact_version="v1",
        endpoint_code="ep",
    )
    assert validate_source_member_identity(id3) == (SourceMemberIdentityReason.ENDPOINT_FIELDS_FORBIDDEN,)

    # 4. Operation code present
    id4 = SourceMemberIdentity(
        member_kind=SourceMemberKind.ARTIFACT_MEMBER,
        artifact_code="doc",
        artifact_version="v1",
        operation_code="op",
    )
    assert validate_source_member_identity(id4) == (SourceMemberIdentityReason.ENDPOINT_FIELDS_FORBIDDEN,)


def test_non_source_member_identity_type_rejected_immediately() -> None:
    assert validate_source_member_identity("not-an-identity") == (SourceMemberIdentityReason.MEMBER_KIND_INVALID,)
    assert validate_source_member_identity(None) == (SourceMemberIdentityReason.MEMBER_KIND_INVALID,)


def test_invalid_member_kind_rejected_without_field_checks() -> None:
    # Construct an invalid object with arbitrary member_kind
    class FakeIdentity:
        member_kind = "UNKNOWN"
        endpoint_code = None

    assert validate_source_member_identity(FakeIdentity()) == (SourceMemberIdentityReason.MEMBER_KIND_INVALID,)


def test_reasons_are_strictly_deduplicated_and_sorted() -> None:
    # ENDPOINT with missing endpoint, invalid op, and forbidden artifact fields
    identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code=None,
        operation_code="",
        artifact_code="forbidden",
        artifact_version="forbidden",
    )
    reasons = validate_source_member_identity(identity)
    assert len(reasons) == 3
    assert reasons == tuple(sorted(reasons, key=lambda r: r.value))
    assert len(reasons) == len(set(reasons))


def test_source_member_identity_payload_matches_canonical_keys() -> None:
    identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="ep",
        operation_code=None,
    )
    payload = source_member_identity_payload(identity)
    assert payload == {
        "member_kind": "ENDPOINT_OPERATION",
        "endpoint_code": "ep",
        "operation_code": None,
        "artifact_code": None,
        "artifact_version": None,
    }


# -----------------------------------------------------------------------------
# S2: Persistence Value Mapping & Drift Detection Tests
# -----------------------------------------------------------------------------
def test_persisted_member_kind_round_trip() -> None:
    assert persisted_member_kind_value(SourceMemberKind.ARTIFACT_MEMBER) == "ARTIFACT"
    assert persisted_member_kind_value(SourceMemberKind.ENDPOINT_OPERATION) == "ENDPOINT_OPERATION"

    assert member_kind_from_persisted("ARTIFACT") is SourceMemberKind.ARTIFACT_MEMBER
    assert member_kind_from_persisted("ENDPOINT_OPERATION") is SourceMemberKind.ENDPOINT_OPERATION

    for kind in SourceMemberKind:
        wire = persisted_member_kind_value(kind)
        assert member_kind_from_persisted(wire) is kind

    with pytest.raises(SourceMemberIdentityError) as exc_info:
        member_kind_from_persisted("INVALID_WIRE")
    assert exc_info.value.reason == SourceMemberIdentityReason.PERSISTED_MEMBER_KIND_UNKNOWN

    val, err = try_member_kind_from_persisted("INVALID_WIRE")
    assert val is None
    assert err == SourceMemberIdentityReason.PERSISTED_MEMBER_KIND_UNKNOWN


def test_persisted_member_kind_values_match_snapshot_lifecycle_enum() -> None:
    """Drift detection: ensure pure kernel wire values match snapshot_lifecycle definitions."""
    projected = {persisted_member_kind_value(kind) for kind in SourceMemberKind}
    assert projected == {member.value for member in SourceSnapshotMemberKind}


# -----------------------------------------------------------------------------
# Exact-Match Verifier Tests
# -----------------------------------------------------------------------------
def test_verify_member_authority_binding_exact_match() -> None:
    obs = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="EP",
        operation_code="OP",
    )
    sel = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="EP",
        operation_code="OP",
    )
    assert verify_member_authority_binding(observed=obs, selected=sel) == ()


@pytest.mark.parametrize(
    "mutator",
    [
        lambda s: replace(
            s,
            member_kind=SourceMemberKind.ARTIFACT_MEMBER,
            artifact_code="doc",
            artifact_version="v1",
            endpoint_code=None,
            operation_code=None,
        ),
        lambda s: replace(s, endpoint_code="DIFFERENT_EP"),
        lambda s: replace(s, operation_code="DIFFERENT_OP"),
        lambda s: replace(s, operation_code=None),
    ],
)
def test_verify_member_authority_binding_mismatch(mutator) -> None:
    obs = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="EP",
        operation_code="OP",
    )
    sel = mutator(obs)
    reasons = verify_member_authority_binding(observed=obs, selected=sel)
    assert SourceMemberIdentityReason.MEMBER_AUTHORITY_MISMATCH in reasons


def test_verify_member_authority_binding_reports_individual_and_mismatch_reasons() -> None:
    obs = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code=None,  # invalid
        operation_code="OP",
    )
    sel = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="EP",
        operation_code="OP",
    )
    reasons = verify_member_authority_binding(observed=obs, selected=sel)
    assert SourceMemberIdentityReason.ENDPOINT_CODE_REQUIRED in reasons
    assert SourceMemberIdentityReason.MEMBER_AUTHORITY_MISMATCH in reasons


# -----------------------------------------------------------------------------
# S1: Golden Vector Hash Invariance Verification (10 Hashes)
# -----------------------------------------------------------------------------
def test_golden_vectors_h1_and_h2() -> None:
    # 1. ENDPOINT case
    cs_ep = _make_golden_candidate_set(_golden_binding_ep())
    rcpt_ep = _make_golden_support_receipt(cs_ep)
    outcome_ep = validate_claim_citations(cs_ep, (rcpt_ep,))
    assert outcome_ep.decision.value == "VALIDATED"

    h1_ep = canonical_claim_support_projection_hash(cs_ep, "claim-001")
    h2_ep = canonical_validated_selection_hash(cs_ep, (rcpt_ep,))
    assert h1_ep == GOLDEN_H1_ENDPOINT
    assert h2_ep == GOLDEN_H2_ENDPOINT

    # 2. ARTIFACT case
    cs_art = _make_golden_candidate_set(_golden_binding_art())
    rcpt_art = _make_golden_support_receipt(cs_art)
    outcome_art = validate_claim_citations(cs_art, (rcpt_art,))
    assert outcome_art.decision.value == "VALIDATED"

    h1_art = canonical_claim_support_projection_hash(cs_art, "claim-001")
    h2_art = canonical_validated_selection_hash(cs_art, (rcpt_art,))
    assert h1_art == GOLDEN_H1_ARTIFACT
    assert h2_art == GOLDEN_H2_ARTIFACT


def test_golden_vectors_h3_and_h4() -> None:
    scopes = ("GUIDE", "PATIENT_CITATION")
    runtime = RuntimeAuthorizationBinding(
        environment=RuntimeEnvironment.TEST,
        bundle_id="bundle-001",
        bundle_manifest_hash=_A,
        request_scope_codes=scopes,
        scope_manifest_hash=canonical_scope_manifest_hash(scopes),
    )
    origin = OriginRequestGuardBinding(
        guard_ref=_art("request-guard", _B),
        decision=GuardDecision.PASS,
        operation=GuardOperation.REQUEST,
        environment=runtime.environment,
        bundle_id=runtime.bundle_id,
        bundle_manifest_hash=runtime.bundle_manifest_hash,
        request_scope_codes=runtime.request_scope_codes,
        scope_manifest_hash=runtime.scope_manifest_hash,
    )

    # 1. ENDPOINT case
    cs_ep = _make_golden_candidate_set(_golden_binding_ep())
    rcpt_ep = _make_golden_support_receipt(cs_ep)
    val_outcome_ep = validate_claim_citations(cs_ep, (rcpt_ep,))
    assert val_outcome_ep.validated_selection is not None
    build_outcome_ep = build_citation_authorization_request(val_outcome_ep.validated_selection, runtime, origin)
    assert build_outcome_ep.request is not None
    assert build_outcome_ep.request.selection_manifest_sha256 == GOLDEN_H3_ENDPOINT
    assert build_outcome_ep.request.request_sha256 == GOLDEN_H4_ENDPOINT

    # 2. ARTIFACT case
    cs_art = _make_golden_candidate_set(_golden_binding_art())
    rcpt_art = _make_golden_support_receipt(cs_art)
    val_outcome_art = validate_claim_citations(cs_art, (rcpt_art,))
    assert val_outcome_art.validated_selection is not None
    build_outcome_art = build_citation_authorization_request(val_outcome_art.validated_selection, runtime, origin)
    assert build_outcome_art.request is not None
    assert build_outcome_art.request.selection_manifest_sha256 == GOLDEN_H3_ARTIFACT
    assert build_outcome_art.request.request_sha256 == GOLDEN_H4_ARTIFACT


def test_golden_vectors_h5() -> None:
    dt = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
    dt_eval = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)

    sel_endpoint = VerifiedGuideEvidenceSelection(
        final_rank=1,
        knowledge_chunk_id=UUID("00000000-0000-0000-0000-000000000001"),
        locator="section-1",
        content_sha256=_A,
        content_text=SensitiveText("sample content"),
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="endpoint-001",
        operation_code="get_knowledge",
        artifact_code=None,
        artifact_version=None,
        normalization_version="norm-v1",
        request_decision_stage=RequestDecisionStage.REQUEST,
        request_guard_ref=_art("request-guard", _B),
        request_source_decision_ref=_art("source-decision", _C),
        request_member_decision_ref=_art("member-decision", _D),
        request_operation_code="get_knowledge",
        retrieval_receipt_ref=_art("retrieval-receipt", _A),
        source_code="knowledge-source",
        source_snapshot_id=UUID("00000000-0000-0000-0000-000000000002"),
        source_snapshot_member_id=UUID("00000000-0000-0000-0000-000000000003"),
        source_version="2026-09-01",
        verifier_artifact_ref=_art("verifier", _B),
        assessment_artifact_ref=_art("assessment", _C),
        assessment_valid_from=dt,
        assessment_valid_until=dt,
        canonical_checksum=_D,
        canonicalization_spec_version="canon-v1",
        chunk_index=0,
        eligibility_receipt_ref=_art("eligibility", _A),
        evidence_key="ev:1",
        external_document_id="doc-001",
    )

    sel_artifact = VerifiedGuideEvidenceSelection(
        final_rank=1,
        knowledge_chunk_id=UUID("00000000-0000-0000-0000-000000000001"),
        locator="section-1",
        content_sha256=_A,
        content_text=SensitiveText("sample content"),
        member_kind=SourceMemberKind.ARTIFACT_MEMBER,
        endpoint_code=None,
        operation_code=None,
        artifact_code="knowledge-artifact",
        artifact_version="2026-09-01",
        normalization_version="norm-v1",
        request_decision_stage=RequestDecisionStage.REQUEST,
        request_guard_ref=_art("request-guard", _B),
        request_source_decision_ref=_art("source-decision", _C),
        request_member_decision_ref=_art("member-decision", _D),
        request_operation_code="get_knowledge",
        retrieval_receipt_ref=_art("retrieval-receipt", _A),
        source_code="knowledge-source",
        source_snapshot_id=UUID("00000000-0000-0000-0000-000000000002"),
        source_snapshot_member_id=UUID("00000000-0000-0000-0000-000000000003"),
        source_version="2026-09-01",
        verifier_artifact_ref=_art("verifier", _B),
        assessment_artifact_ref=_art("assessment", _C),
        assessment_valid_from=dt,
        assessment_valid_until=dt,
        canonical_checksum=_D,
        canonicalization_spec_version="canon-v1",
        chunk_index=0,
        eligibility_receipt_ref=_art("eligibility", _A),
        evidence_key="ev:1",
        external_document_id="doc-001",
    )

    rcpt_ref = _art("retrieval-receipt", _A)
    manifest_sha256 = _B

    h5_ep = compute_guide_evidence_handoff_hash(
        retrieval_receipt_ref=rcpt_ref,
        retrieval_selection_manifest_sha256=manifest_sha256,
        evaluated_at=dt_eval,
        selections=(sel_endpoint,),
    )
    assert h5_ep == GOLDEN_H5_ENDPOINT

    h5_art = compute_guide_evidence_handoff_hash(
        retrieval_receipt_ref=rcpt_ref,
        retrieval_selection_manifest_sha256=manifest_sha256,
        evaluated_at=dt_eval,
        selections=(sel_artifact,),
    )
    assert h5_art == GOLDEN_H5_ARTIFACT


# -----------------------------------------------------------------------------
# S1: Selection Payload Dedup Behavior Test
# -----------------------------------------------------------------------------
def test_selection_payload_deduplication_preserves_single_member_entry() -> None:
    binding = _golden_binding_ep()
    # Create two citations with identical execution_provenance
    ev1 = KnowledgeChunkEvidenceRef(
        knowledge_chunk_ref="knowledge-chunk-001",
        source_snapshot_ref=_art("knowledge-snapshot"),
        source_version="2026-09-01",
        locator="section-1",
        content_sha256=_A,
        execution_provenance=binding,
    )
    ev2 = KnowledgeChunkEvidenceRef(
        knowledge_chunk_ref="knowledge-chunk-002",
        source_snapshot_ref=_art("knowledge-snapshot"),
        source_version="2026-09-01",
        locator="section-2",
        content_sha256=_B,
        execution_provenance=binding,
    )
    c1 = CitationCandidate(
        citation_key="citation-001",
        claim_key="claim-001",
        source_type=CitationSourceType.KNOWLEDGE_CHUNK,
        evidence_ref=ev1,
        display_order=1,
    )
    c2 = CitationCandidate(
        citation_key="citation-002",
        claim_key="claim-001",
        source_type=CitationSourceType.KNOWLEDGE_CHUNK,
        evidence_ref=ev2,
        display_order=2,
    )
    claim = ClaimCandidate(
        claim_key="claim-001",
        claim_kind=ClaimKind.MEDICAL,
        text_digest=_D,
        display_order=1,
        support_assertion=ClaimSupportAssertion(
            support_status=ClaimSupportStatus.SUPPORTED,
            assessment_ref=_art("support-assessment", _B),
        ),
    )
    cs = ClaimCitationCandidateSet(
        target=ClaimTargetRef(target_kind="GUIDE", target_ref="guide-run-001"),
        claims=(claim,),
        citations=(c1, c2),
        generation_provenance=GenerationProvenance(
            prompt_ref=_art("prompt"),
            model_ref=_art("model", _B),
            parser_ref=_art("parser", _C),
        ),
        validator_policy_ref=_art("claim-citation-validator", _D),
    )
    rcpt = _make_golden_support_receipt(cs)
    val_outcome = validate_claim_citations(cs, (rcpt,))
    assert val_outcome.validated_selection is not None

    entries = _authorization_entries(val_outcome.validated_selection)
    # Despite 2 citations, identical member binding deduplicates to exactly 1 entry
    assert len(entries) == 1
    assert entries[0].endpoint_code == "endpoint-001"
    assert entries[0].operation_code == "get_knowledge"
