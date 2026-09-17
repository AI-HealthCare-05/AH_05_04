"""Unit tests for RAG-15 Production Evidence Input Contract Alignment (FAST-TRACK).

Verifies safe consumption of #760 VerifiedGuideEvidenceHandoff by RAG-15
Guideline Generator and Card Finalizer without fake legacy EvidenceGateOutcome.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from ai_worker.adapters.openai_guideline_generator import OpenAIGuidelineGeneratorAdapter
from ai_worker.tasks.rag.claim_citation_validator import SourceMemberKind
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    QueryFingerprint,
    SensitiveText,
)
from ai_worker.tasks.rag.evidence_search import (
    FractionReceipt,
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    StableCoordinate,
)
from ai_worker.tasks.rag.guide_evidence_handoff import (
    GuideEvidenceHandoffBuildDecision,
    GuideEvidenceHandoffRequest,
    GuideEvidenceSelectionRequest,
    ObservedDecisionOutcome,
    RequestSourceMemberBinding,
    VerifiedGuideEvidenceHandoff,
    build_guide_evidence_handoff,
    canonical_jcs_bytes,
    canonical_production_evidence_selection_hash,
    canonical_production_evidence_selection_projection,
)
from ai_worker.tasks.rag.guideline_card import (
    ApprovedGuidelineEvidenceBinding,
    ApprovedGuidelineFallback,
    GuidelineApprovalVerificationSuccess,
    GuidelineCardReason,
    GuidelineCardRequest,
    GuidelineCardStatus,
    GuidelineCitationDraft,
    GuidelineCitationSourceType,
    GuidelineFallbackCode,
    GuidelineGenerationFailure,
    GuidelineScope,
    MedicationIdentityRef,
    VersionedGuidelinePolicy,
    create_canonical_card_draft,
    create_canonical_claim_draft,
    create_canonical_guideline_policy,
    finalize_guideline_card,
)
from ai_worker.tasks.rag.guideline_generator import (
    GuidelineGenerationRequest,
)
from ai_worker.tasks.rag.guideline_generator_prompt import (
    GuidelineClaimSelection,
    GuidelineStructuredSelection,
    build_candidate_provenance,
    build_guideline_generation_input_projection,
    parse_guideline_structured_output,
)
from ai_worker.tasks.rag.retrieval_runtime import (
    RetrievalExecutionStatus,
    compute_production_search_receipt,
    compute_selection_manifest_hash,
)
from provider_contracts.observability import DeploymentEnvironment, ProviderCallContext

EVALUATED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
FOOD_AVOIDANCE_TEXT = (
    "이 약을 복용하는 동안 과도한 음주는 피하고 임의로 복용을 중단하지 마세요. 궁금한 점은 약사와 상담하세요."
)


def _make_artifact(code: str, version: str = "v1", digest: str | None = None) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(
        artifact_code=code,
        version=version,
        content_sha256=digest or ("a" * 64),
    )


class SyntheticApprovalVerifier:
    def verify(self, artifact_ref: ImmutableArtifactRef) -> GuidelineApprovalVerificationSuccess:
        return GuidelineApprovalVerificationSuccess(
            artifact_ref=artifact_ref,
            verifier_artifact_ref=_make_artifact("guideline-approval-verifier"),
        )


def _make_search_hit(
    *,
    rank: int,
    external_doc_id: str,
    chunk_index: int,
    content_text: str,
    source_code: str = "MFDS_GUIDE",
    source_version: str = "2026.09",
    locator: str = "$.item[0].useMethod",
) -> tuple[ProductionSearchHit, SensitiveText]:
    text = SensitiveText(content_text)
    content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
    provenance = ProductionEvidenceProvenance(
        knowledge_index_id=uuid4(),
        index_code="GUIDELINE_INDEX",
        index_version="v1",
        index_configuration_hash="4" * 64,
        knowledge_chunk_id=uuid4(),
        source_snapshot_id=uuid4(),
        source_snapshot_member_id=uuid4(),
        source_code=source_code,
        source_version=source_version,
        external_document_id=external_doc_id,
        chunk_index=chunk_index,
        locator=locator,
        canonical_checksum=hashlib.sha256(f"checksum:{external_doc_id}".encode()).hexdigest(),
        content_hash=content_hash,
        canonicalization_spec_version="text-v1",
        normalization_version="nfc-v1",
    )
    coordinate = StableCoordinate(
        source_code=source_code,
        source_version=source_version,
        external_document_id=external_doc_id,
        chunk_index=chunk_index,
    )
    hit = ProductionSearchHit(
        provenance=provenance,
        coordinate=coordinate,
        exact_hit=False,
        observed_trigram_score=None,
        observed_fts_score=None,
        observed_dense_score=None,
        lexical_rank=None,
        dense_rank=None,
        fusion_rank=rank,
        fraction_receipt=FractionReceipt("1", "60"),
        is_eligible_for_future_reranker=True,
    )
    return hit, text


def make_production_handoff(
    evaluated_at: datetime = EVALUATED_AT,
) -> VerifiedGuideEvidenceHandoff:
    hit1, text1 = _make_search_hit(
        rank=1, external_doc_id="DOC-1", chunk_index=0, content_text="음주 금지 안내: 알코올과 상호작용 위험"
    )
    hit2, text2 = _make_search_hit(
        rank=2, external_doc_id="DOC-2", chunk_index=1, content_text="주의사항: 자몽 주스와 함께 복용 금지"
    )

    manifest_sha = compute_selection_manifest_hash([hit1, hit2])
    receipt = compute_production_search_receipt(
        variant="RET-H",
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code="OK",
        query_fingerprint=QueryFingerprint("sha256", "v1", "0" * 64),
        filter_snapshot_ref=ImmutableArtifactRef("filter_snapshot", "1.0", "f" * 64),
        evidence_index_ref=ImmutableArtifactRef("knowledge_index", "1.0", "e" * 64),
        retrieval_config_ref=ImmutableArtifactRef("retrieval_config", "1.0", "3" * 64),
        adapter_artifact_ref=_make_artifact("adapter"),
        query_embedding_sha256="d" * 64,
        signal_manifest_sha256="1" * 64,
        hit_manifest_sha256="2" * 64,
        selection_manifest_sha256=manifest_sha,
    )

    valid_from = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    valid_until = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)

    binding1 = RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("guard-1"),
        source_snapshot_id=hit1.provenance.source_snapshot_id,
        source_snapshot_member_id=hit1.provenance.source_snapshot_member_id,
        source_code=hit1.provenance.source_code,
        source_version=hit1.provenance.source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=_make_artifact("src-dec-1"),
        request_member_decision_ref=_make_artifact("mem-dec-1"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_operation_code="get_guide",
        endpoint_code="guide_api",
        operation_code="get_guide",
    )
    sel1 = GuideEvidenceSelectionRequest(
        hit=hit1,
        binding=binding1,
        evidence_key="evidence:prod-1",
        content_text=text1,
        retrieval_receipt_ref=receipt.artifact_ref,
        eligibility_receipt_ref=_make_artifact("elig-1"),
        assessment_artifact_ref=_make_artifact("assess-1"),
        verifier_artifact_ref=_make_artifact("verif-1"),
        assessment_valid_from=valid_from,
        assessment_valid_until=valid_until,
    )

    binding2 = RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("guard-1"),
        source_snapshot_id=hit2.provenance.source_snapshot_id,
        source_snapshot_member_id=hit2.provenance.source_snapshot_member_id,
        source_code=hit2.provenance.source_code,
        source_version=hit2.provenance.source_version,
        member_kind=SourceMemberKind.ARTIFACT_MEMBER,
        request_source_decision_ref=_make_artifact("src-dec-2"),
        request_member_decision_ref=_make_artifact("mem-dec-2"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_operation_code="get_guide",
        artifact_code="guide_doc",
        artifact_version="v1",
    )
    sel2 = GuideEvidenceSelectionRequest(
        hit=hit2,
        binding=binding2,
        evidence_key="evidence:prod-2",
        content_text=text2,
        retrieval_receipt_ref=receipt.artifact_ref,
        eligibility_receipt_ref=_make_artifact("elig-2"),
        assessment_artifact_ref=_make_artifact("assess-2"),
        verifier_artifact_ref=_make_artifact("verif-2"),
        assessment_valid_from=valid_from,
        assessment_valid_until=valid_until,
    )

    req = GuideEvidenceHandoffRequest(
        retrieval_receipt=receipt,
        selections=(sel1, sel2),
        evaluated_at=evaluated_at,
    )
    outcome = build_guide_evidence_handoff(req)
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.BUILT
    assert outcome.handoff is not None
    return outcome.handoff


def make_medication() -> MedicationIdentityRef:
    return MedicationIdentityRef(
        prescription_version_medication_id="11111111-1111-4111-8111-111111111111",
        code_system="MFDS_ITEM_SEQ",
        canonical_code="SYNTHETIC-ITEM-001",
    )


def make_policy() -> VersionedGuidelinePolicy:
    return create_canonical_guideline_policy(
        artifact_code="guideline-policy",
        version="v1",
        maximum_claims=4,
    )


def make_fallbacks() -> tuple[ApprovedGuidelineFallback, ...]:
    return tuple(
        ApprovedGuidelineFallback.create(
            "guideline-fallback",
            f"guideline-fallback@synthetic-{code.value.lower()}",
            code=code,
            text=SensitiveText("현재는 승인된 안내를 제공할 수 없습니다. 의사 또는 약사와 상담하세요."),
        )
        for code in GuidelineFallbackCode
    )


# ==============================================================================
# Case 1: VerifiedGuideEvidenceHandoff Generator input creation success
# ==============================================================================
def test_case_1_verified_guide_evidence_handoff_generator_input_creation_success() -> None:
    handoff = make_production_handoff()
    med = make_medication()
    policy = make_policy()

    req = GuidelineGenerationRequest(
        medication_identities=(med,),
        evidence_handoff=handoff,
        policy=policy,
    )

    input_json, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(req)

    data = json.loads(input_json)
    assert "medications" in data
    assert "evidence" in data
    assert len(data["medications"]) == 1
    assert data["medications"][0]["medication_slot"] == "m0"
    assert data["medications"][0]["code_system"] == med.code_system
    assert data["medications"][0]["canonical_code"] == med.canonical_code

    assert len(data["evidence"]) == 2
    assert data["evidence"][0]["evidence_slot"] == "e0"
    assert data["evidence"][0]["content_text"] == handoff.selections[0].content_text.reveal()
    assert data["evidence"][1]["evidence_slot"] == "e1"
    assert data["evidence"][1]["content_text"] == handoff.selections[1].content_text.reveal()

    assert slot_to_med["m0"] == med
    assert slot_to_ev["e0"] == handoff.selections[0]
    assert slot_to_ev["e1"] == handoff.selections[1]


# ==============================================================================
# Case 2: Content/source/hash/receipt identity preservation
# ==============================================================================
def test_case_2_content_source_hash_receipt_identity_preservation() -> None:
    handoff = make_production_handoff()
    med = make_medication()
    policy = make_policy()

    req = GuidelineGenerationRequest(
        medication_identities=(med,),
        evidence_handoff=handoff,
        policy=policy,
    )
    _, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(req)

    structured = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            )
        ]
    )

    draft = parse_guideline_structured_output(
        structured,
        slot_to_medication=slot_to_med,
        slot_to_evidence=slot_to_ev,
        maximum_claims=policy.maximum_claims,
    )

    assert draft is not None
    assert len(draft.claims) == 1
    claim = draft.claims[0]
    assert len(claim.citations) == 1
    citation = claim.citations[0]

    sel0 = handoff.selections[0]
    assert citation.evidence_key == sel0.evidence_key
    assert citation.source_snapshot_ref == sel0.source_snapshot_ref
    assert citation.source_version == sel0.source_version
    assert citation.locator == sel0.locator
    assert citation.content_sha256 == sel0.content_sha256


# ==============================================================================
# Case 3: Provider projection contains no internal authority identifier
# ==============================================================================
def test_case_3_provider_projection_contains_no_internal_authority_identifier() -> None:
    handoff = make_production_handoff()
    med = make_medication()
    policy = make_policy()

    req = GuidelineGenerationRequest(
        medication_identities=(med,),
        evidence_handoff=handoff,
        policy=policy,
    )
    input_json, _, _ = build_guideline_generation_input_projection(req)

    sel0 = handoff.selections[0]
    # Forbidden internal identifiers must not leak into Provider JSON
    forbidden_tokens = [
        str(sel0.knowledge_chunk_id),
        str(sel0.source_snapshot_id),
        str(sel0.source_snapshot_member_id),
        sel0.source_code,
        sel0.locator,
        sel0.canonical_checksum,
        sel0.retrieval_receipt_ref.artifact_code,
        sel0.eligibility_receipt_ref.artifact_code,
        sel0.assessment_artifact_ref.artifact_code,
        sel0.verifier_artifact_ref.artifact_code,
        sel0.request_guard_ref.artifact_code,
        med.prescription_version_medication_id,
    ]
    for token in forbidden_tokens:
        assert token not in input_json, f"Internal token leaked to Provider payload: {token}"


# ==============================================================================
# Case 4: No legacy rerank/stage values generated
# ==============================================================================
def test_case_4_no_legacy_rerank_stage_values_generated() -> None:
    handoff = make_production_handoff()
    med = make_medication()
    policy = make_policy()

    req = GuidelineGenerationRequest(
        medication_identities=(med,),
        evidence_handoff=handoff,
        policy=policy,
    )
    input_json, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(req)

    # Provider payload has no rerank/stage concepts
    assert "rerank" not in input_json
    assert "stage_signal" not in input_json
    assert "evidence_index" not in input_json

    # Parse and check draft citation
    structured = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            )
        ]
    )
    draft = parse_guideline_structured_output(
        structured,
        slot_to_medication=slot_to_med,
        slot_to_evidence=slot_to_ev,
        maximum_claims=policy.maximum_claims,
    )
    assert draft is not None
    cit = draft.claims[0].citations[0]
    # Citation has no fake legacy provenance or rerank attributes
    assert not hasattr(cit, "rerank_score")
    assert not hasattr(cit, "stage_signals")
    assert not hasattr(cit, "evidence_index_ref")


# ==============================================================================
# Case 5: Production selection canonical hash deterministic and RFC 8785 compliant
# ==============================================================================
def test_case_5_production_selection_canonical_hash_deterministic_and_rfc8785() -> None:
    handoff = make_production_handoff()
    sel0 = handoff.selections[0]

    projection = canonical_production_evidence_selection_projection(sel0)
    assert projection["projection_version"] == "rag15-production-evidence-selection-v1"
    assert len(projection) == 31

    hash1 = canonical_production_evidence_selection_hash(sel0)
    hash2 = canonical_production_evidence_selection_hash(sel0)
    assert hash1 == hash2
    assert len(hash1) == 64
    assert all(c in "0123456789abcdef" for c in hash1)

    # Re-encoded through JCS produces identical bytes and hash
    jcs_bytes = canonical_jcs_bytes(projection)
    assert hashlib.sha256(jcs_bytes).hexdigest() == hash1


# ==============================================================================
# Case 6: Content hash mismatch fail closed (VALIDATION_FAILED)
# ==============================================================================
@pytest.mark.asyncio
async def test_case_6_content_hash_mismatch_fail_closed_validation_failed() -> None:
    handoff = make_production_handoff()
    med = make_medication()
    policy = make_policy()

    # Tamper with content_sha256 on selection
    tampered_sel = replace(handoff.selections[0], content_sha256="0" * 64)
    tampered_handoff = replace(handoff, selections=(tampered_sel, handoff.selections[1]))

    # 1. Generator adapter rejects before Provider invocation
    mock_client = MagicMock()
    mock_client.max_retries = 0
    mock_client.with_options.return_value = mock_client
    adapter = OpenAIGuidelineGeneratorAdapter(
        client=mock_client,
        model="gpt-4o",
        timeout_seconds=5.0,
        context=ProviderCallContext(
            trace_id="1" * 32,
            validation_run_id=None,
            environment=DeploymentEnvironment.LOCAL,
            validation_enabled=False,
        ),
    )
    req = GuidelineGenerationRequest(
        medication_identities=(med,),
        evidence_handoff=tampered_handoff,
        policy=policy,
    )
    result = await adapter.generate(req)
    assert result == GuidelineGenerationFailure.VALIDATION_FAILED
    mock_client.responses.parse.assert_not_called()

    # 2. Card Finalizer rejects
    card_req = GuidelineCardRequest(
        medication_identities=(med,),
        evidence_handoff=tampered_handoff,
        draft=None,
        generation_failure=None,
        policy=policy,
        provenance=build_candidate_provenance(model="gpt-4o"),
        approved_fallbacks=make_fallbacks(),
        evaluated_at=EVALUATED_AT,
    )
    outcome = finalize_guideline_card(card_req, approval_verifier=SyntheticApprovalVerifier())
    assert outcome.status == GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.reason == GuidelineCardReason.VALIDATION_FAILED
    assert outcome.fallback is not None
    assert outcome.fallback.code == GuidelineFallbackCode.VALIDATION_FAILED


# ==============================================================================
# Case 7: Handoff / evaluated_at mismatch fail closed
# ==============================================================================
def test_case_7_handoff_evaluated_at_mismatch_fail_closed() -> None:
    handoff = make_production_handoff()
    med = make_medication()
    policy = make_policy()

    # Evaluated at differs from handoff.evaluated_at
    mismatched_evaluated_at = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)

    card_req = GuidelineCardRequest(
        medication_identities=(med,),
        evidence_handoff=handoff,
        draft=None,
        generation_failure=None,
        policy=policy,
        provenance=build_candidate_provenance(model="gpt-4o"),
        approved_fallbacks=make_fallbacks(),
        evaluated_at=mismatched_evaluated_at,
    )
    outcome = finalize_guideline_card(card_req, approval_verifier=SyntheticApprovalVerifier())
    assert outcome.status == GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.reason == GuidelineCardReason.VALIDATION_FAILED
    assert outcome.fallback is not None
    assert outcome.fallback.code == GuidelineFallbackCode.VALIDATION_FAILED


# ==============================================================================
# Case 8: Generator draft Citation referencing unknown evidence fail closed
# ==============================================================================
def test_case_8_generator_draft_citation_referencing_unknown_evidence_fail_closed() -> None:
    handoff = make_production_handoff()
    med = make_medication()
    policy = make_policy()

    req = GuidelineGenerationRequest(
        medication_identities=(med,),
        evidence_handoff=handoff,
        policy=policy,
    )
    _, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(req)

    # Provider references an unknown slot "e99"
    structured = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e99"],
            )
        ]
    )
    draft = parse_guideline_structured_output(
        structured,
        slot_to_medication=slot_to_med,
        slot_to_evidence=slot_to_ev,
        maximum_claims=policy.maximum_claims,
    )
    assert draft is None

    # Card finalizer receives draft with unknown evidence_key
    unknown_citation = GuidelineCitationDraft(
        evidence_key="evidence:unknown",
        source_snapshot_ref=_make_artifact("source-snapshot"),
        source_version="2026.09",
        locator="$.unknown",
        content_sha256="a" * 64,
    )
    claim_draft = create_canonical_claim_draft(
        claim_key="claim:001",
        medication_identity=med,
        scope=GuidelineScope.FOOD_CAUTION,
        citations=(unknown_citation,),
    )
    card_draft = create_canonical_card_draft((claim_draft,))

    card_req = GuidelineCardRequest(
        medication_identities=(med,),
        evidence_handoff=handoff,
        draft=card_draft,
        generation_failure=None,
        policy=policy,
        provenance=build_candidate_provenance(model="gpt-4o"),
        approved_fallbacks=make_fallbacks(),
        evaluated_at=EVALUATED_AT,
    )
    outcome = finalize_guideline_card(card_req, approval_verifier=SyntheticApprovalVerifier())
    assert outcome.status == GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.reason == GuidelineCardReason.VALIDATION_FAILED


# ==============================================================================
# Case 9: Card finalizer production evidence exact binding success
# ==============================================================================
def test_case_9_card_finalizer_production_evidence_exact_binding_success() -> None:
    handoff = make_production_handoff()
    med = make_medication()
    policy = make_policy()
    sel0 = handoff.selections[0]

    req = GuidelineGenerationRequest(
        medication_identities=(med,),
        evidence_handoff=handoff,
        policy=policy,
    )
    _, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(req)

    structured = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            )
        ]
    )
    draft = parse_guideline_structured_output(
        structured,
        slot_to_medication=slot_to_med,
        slot_to_evidence=slot_to_ev,
        maximum_claims=policy.maximum_claims,
    )
    assert draft is not None

    # Compute production selection canonical hash
    selection_hash = canonical_production_evidence_selection_hash(sel0)

    binding = ApprovedGuidelineEvidenceBinding.create(
        "guideline-evidence-binding",
        "guideline-evidence-binding@v1",
        medication_identity=med,
        scope=GuidelineScope.FOOD_CAUTION,
        action_class=draft.claims[0].action_class,
        evidence_key=sel0.evidence_key,
        assessment_artifact_ref=sel0.assessment_artifact_ref,
        selection_projection_sha256=selection_hash,
        action_text_sha256=hashlib.sha256(draft.claims[0].action_text.reveal().encode()).hexdigest(),
    )

    card_req = GuidelineCardRequest(
        medication_identities=(med,),
        evidence_handoff=handoff,
        draft=draft,
        generation_failure=None,
        policy=policy,
        provenance=build_candidate_provenance(model="gpt-4o"),
        approved_fallbacks=make_fallbacks(),
        evaluated_at=EVALUATED_AT,
        approved_evidence_bindings=(binding,),
    )

    verifier = SyntheticApprovalVerifier()
    outcome = finalize_guideline_card(card_req, approval_verifier=verifier)

    assert outcome.status == GuidelineCardStatus.GENERATED
    assert outcome.reason == GuidelineCardReason.CARD_GENERATED
    assert outcome.card is not None

    card = outcome.card
    assert len(card.claims) == 1
    claim = card.claims[0]
    assert len(claim.citations) == 1
    citation = claim.citations[0]

    # Verify exact binding of all 12 citation attributes
    assert citation.source_type == GuidelineCitationSourceType.LIFESTYLE_GUIDELINE
    assert citation.evidence_key == sel0.evidence_key
    assert citation.source_snapshot_ref == sel0.source_snapshot_ref
    assert citation.source_version == sel0.source_version
    assert citation.locator == sel0.locator
    assert citation.content_sha256 == sel0.content_sha256
    assert citation.assessment_artifact_ref == sel0.assessment_artifact_ref
    assert citation.eligibility_receipt_ref == sel0.eligibility_receipt_ref
    assert citation.retrieval_receipt_ref == sel0.retrieval_receipt_ref
    assert citation.verifier_artifact_ref == sel0.verifier_artifact_ref
    assert citation.guideline_evidence_binding_ref == binding.artifact_ref
    assert citation.guideline_evidence_binding_verifier_ref == _make_artifact("guideline-approval-verifier")


# ==============================================================================
# Case 10: Altered locator/source/content/ref Card rejected
# ==============================================================================
@pytest.mark.parametrize(
    "tamper_fn",
    [
        lambda c: replace(c, locator="$.altered.locator"),
        lambda c: replace(c, source_version="9999.99"),
        lambda c: replace(c, source_snapshot_ref=_make_artifact("tampered-snapshot")),
        lambda c: replace(c, content_sha256="f" * 64),
    ],
)
def test_case_10_altered_citation_fields_card_rejected(tamper_fn: Any) -> None:
    handoff = make_production_handoff()
    med = make_medication()
    policy = make_policy()
    sel0 = handoff.selections[0]

    valid_citation = GuidelineCitationDraft(
        evidence_key=sel0.evidence_key,
        source_snapshot_ref=sel0.source_snapshot_ref,
        source_version=sel0.source_version,
        locator=sel0.locator,
        content_sha256=sel0.content_sha256,
    )
    tampered_citation = tamper_fn(valid_citation)

    claim_draft = create_canonical_claim_draft(
        claim_key="claim:001",
        medication_identity=med,
        scope=GuidelineScope.FOOD_CAUTION,
        citations=(tampered_citation,),
    )
    draft = create_canonical_card_draft((claim_draft,))

    binding = ApprovedGuidelineEvidenceBinding.create(
        "guideline-evidence-binding",
        "guideline-evidence-binding@v1",
        medication_identity=med,
        scope=GuidelineScope.FOOD_CAUTION,
        action_class=claim_draft.action_class,
        evidence_key=sel0.evidence_key,
        assessment_artifact_ref=sel0.assessment_artifact_ref,
        selection_projection_sha256=canonical_production_evidence_selection_hash(sel0),
        action_text_sha256=hashlib.sha256(claim_draft.action_text.reveal().encode()).hexdigest(),
    )

    card_req = GuidelineCardRequest(
        medication_identities=(med,),
        evidence_handoff=handoff,
        draft=draft,
        generation_failure=None,
        policy=policy,
        provenance=build_candidate_provenance(model="gpt-4o"),
        approved_fallbacks=make_fallbacks(),
        evaluated_at=EVALUATED_AT,
        approved_evidence_bindings=(binding,),
    )

    outcome = finalize_guideline_card(card_req, approval_verifier=SyntheticApprovalVerifier())
    assert outcome.status == GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.reason == GuidelineCardReason.VALIDATION_FAILED


def test_case_10_tampered_selection_projection_hash_card_rejected() -> None:
    handoff = make_production_handoff()
    med = make_medication()
    policy = make_policy()
    sel0 = handoff.selections[0]

    valid_citation = GuidelineCitationDraft(
        evidence_key=sel0.evidence_key,
        source_snapshot_ref=sel0.source_snapshot_ref,
        source_version=sel0.source_version,
        locator=sel0.locator,
        content_sha256=sel0.content_sha256,
    )
    claim_draft = create_canonical_claim_draft(
        claim_key="claim:001",
        medication_identity=med,
        scope=GuidelineScope.FOOD_CAUTION,
        citations=(valid_citation,),
    )
    draft = create_canonical_card_draft((claim_draft,))

    # Binding has a tampered/forged selection_projection_sha256
    binding = ApprovedGuidelineEvidenceBinding.create(
        "guideline-evidence-binding",
        "guideline-evidence-binding@v1",
        medication_identity=med,
        scope=GuidelineScope.FOOD_CAUTION,
        action_class=claim_draft.action_class,
        evidence_key=sel0.evidence_key,
        assessment_artifact_ref=sel0.assessment_artifact_ref,
        selection_projection_sha256="e" * 64,
        action_text_sha256=hashlib.sha256(claim_draft.action_text.reveal().encode()).hexdigest(),
    )

    card_req = GuidelineCardRequest(
        medication_identities=(med,),
        evidence_handoff=handoff,
        draft=draft,
        generation_failure=None,
        policy=policy,
        provenance=build_candidate_provenance(model="gpt-4o"),
        approved_fallbacks=make_fallbacks(),
        evaluated_at=EVALUATED_AT,
        approved_evidence_bindings=(binding,),
    )

    outcome = finalize_guideline_card(card_req, approval_verifier=SyntheticApprovalVerifier())
    assert outcome.status == GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.reason == GuidelineCardReason.VALIDATION_FAILED
