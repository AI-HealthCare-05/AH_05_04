import hashlib
import json
from datetime import UTC, datetime

from ai_worker.tasks.rag.evidence_gate import (
    EvidenceGateExecutionStatus,
    EvidenceGateOutcome,
    EvidenceGateReason,
    EvidenceGateTrace,
    EvidenceStatus,
    GatePassedKnowledgeEvidenceSelection,
)
from ai_worker.tasks.rag.evidence_retrieval import (
    CanonicalScore,
    EvidenceSearchStage,
    ImmutableArtifactRef,
    KnowledgeEvidenceCandidate,
    KnowledgeEvidenceProvenance,
    SensitiveText,
    StageSignal,
    UntrustedKnowledgeEvidenceSelection,
)
from ai_worker.tasks.rag.guideline_card import (
    ApprovedGuidelineEvidenceBinding,
    ApprovedGuidelineFallback,
    GuidelineActionClass,
    GuidelineCardRequest,
    GuidelineCardStatus,
    GuidelineCitationDraft,
    GuidelineFallbackCode,
    GuidelineGenerationProvenance,
    GuidelineScope,
    MedicationIdentityRef,
    VersionedGuidelinePolicy,
    create_canonical_card_draft,
    create_canonical_claim_draft,
)
from ai_worker.tasks.rag.guideline_card import (
    finalize_guideline_card as _finalize_guideline_card,
)
from ai_worker.tasks.rag.guideline_generator import GuidelineGenerationRequest
from ai_worker.tasks.rag.guideline_generator_prompt import (
    GUIDELINE_GENERATOR_PROMPT_VERSION,
    GUIDELINE_GENERATOR_SYSTEM_INSTRUCTIONS,
    GuidelineClaimSelection,
    GuidelineStructuredSelection,
    build_candidate_provenance,
    build_guideline_generation_input_projection,
    parse_guideline_structured_output,
)

EVALUATED_AT = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
FOOD_AVOIDANCE_TEXT = (
    "이 약을 복용하는 동안 과도한 음주는 피하고 임의로 복용을 중단하지 마세요. 궁금한 점은 약사와 상담하세요."
)
DAILY_ACTIVITY_TEXT = "이 약을 복용하는 동안 무리한 운동은 피하고 충분히 휴식하세요."


def artifact(code: str, digest: str = "a" * 64) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(code, f"{code}@synthetic-1", digest)


def make_medication(
    *,
    item_id: str = "11111111-1111-4111-8111-111111111111",
    code_system: str = "MFDS_ITEM_SEQ",
    canonical_code: str = "SYNTHETIC-ITEM-001",
) -> MedicationIdentityRef:
    return MedicationIdentityRef(
        prescription_version_medication_id=item_id,
        code_system=code_system,
        canonical_code=canonical_code,
    )


def make_gate_passed_selection(
    *,
    evidence_key: str = "knowledge:guideline-1",
    locator: str = "$.items[0].useMethodQesitm",
    source_version: str = "api:" + "1" * 64,
    content_text: str = FOOD_AVOIDANCE_TEXT,
) -> GatePassedKnowledgeEvidenceSelection:
    provenance = KnowledgeEvidenceProvenance(
        evidence_key=evidence_key,
        knowledge_chunk_ref=f"chunk-{evidence_key}",
        evidence_index_ref=artifact("knowledge-index"),
        source_snapshot_ref=artifact("source-snapshot"),
        source_version=source_version,
        locator=locator,
        content_sha256=hashlib.sha256(content_text.encode()).hexdigest(),
        canonicalization_spec_version="knowledge-text@1",
    )
    selection = UntrustedKnowledgeEvidenceSelection(
        candidate=KnowledgeEvidenceCandidate(
            provenance=provenance,
            content_text=SensitiveText(content_text),
            stage_signals=(StageSignal(EvidenceSearchStage.LEXICAL, 1, CanonicalScore("0.9")),),
        ),
        rerank_rank=1,
        rerank_score=CanonicalScore("0.9"),
    )
    return GatePassedKnowledgeEvidenceSelection(
        selection=selection,
        assessment_artifact_ref=artifact("assessment"),
        eligibility_receipt_ref=artifact("eligibility-receipt"),
        retrieval_receipt_ref=artifact("retrieval-receipt"),
        verifier_artifact_ref=artifact("eligibility-verifier"),
    )


def make_policy(*, maximum_claims: int = 4) -> VersionedGuidelinePolicy:
    return VersionedGuidelinePolicy.create(
        "guideline-policy",
        "guideline-policy@synthetic-1",
        maximum_claims=maximum_claims,
        uncertainty_text_sha256=hashlib.sha256("승인된 근거 범위 밖의 내용은 확인할 수 없습니다.".encode()).hexdigest(),
        consultation_text_sha256=hashlib.sha256(
            "불편하거나 궁금한 점은 의사 또는 약사와 상담하세요.".encode()
        ).hexdigest(),
    )


def make_generation_request(
    *,
    medications: tuple[MedicationIdentityRef, ...] | None = None,
    selections: tuple[GatePassedKnowledgeEvidenceSelection, ...] | None = None,
    maximum_claims: int = 4,
) -> GuidelineGenerationRequest:
    meds = medications if medications is not None else (make_medication(),)
    sels = selections if selections is not None else (make_gate_passed_selection(),)
    gate_outcome = EvidenceGateOutcome(
        execution_status=EvidenceGateExecutionStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.SUFFICIENT,
        reason=EvidenceGateReason.EVIDENCE_SUFFICIENT,
        gate_passed_selections=sels,
        trace=EvidenceGateTrace(
            policy_ref=artifact("evidence-gate-policy"),
            retrieval_receipt_ref=sels[0].retrieval_receipt_ref,
            evaluated_at=EVALUATED_AT,
            assessment_artifact_refs=tuple(s.assessment_artifact_ref for s in sels),
            selected_evidence_keys=tuple(s.selection.candidate.provenance.evidence_key for s in sels),
        ),
    )
    return GuidelineGenerationRequest(
        medication_identities=meds,
        evidence_gate_outcome=gate_outcome,
        policy=make_policy(maximum_claims=maximum_claims),
    )


def test_system_instructions_and_prompt_version() -> None:
    assert GUIDELINE_GENERATOR_PROMPT_VERSION == "guideline-claim-selector-v1"
    assert "CRITICAL RULES" in GUIDELINE_GENERATOR_SYSTEM_INSTRUCTIONS
    assert "FOOD_CAUTION" in GUIDELINE_GENERATOR_SYSTEM_INSTRUCTIONS
    assert "DAILY_ACTIVITY" in GUIDELINE_GENERATOR_SYSTEM_INSTRUCTIONS
    assert "untrusted data" in GUIDELINE_GENERATOR_SYSTEM_INSTRUCTIONS.lower()


def test_candidate_provenance_binding() -> None:
    model_name = "gpt-4o-mini-2024-07-18"
    prov = build_candidate_provenance(model=model_name)
    assert type(prov) is GuidelineGenerationProvenance
    assert prov.prompt_ref.artifact_code == "guideline-prompt"
    assert prov.prompt_ref.version == GUIDELINE_GENERATOR_PROMPT_VERSION
    expected_prompt_hash = hashlib.sha256(GUIDELINE_GENERATOR_SYSTEM_INSTRUCTIONS.encode("utf-8")).hexdigest()
    assert prov.prompt_ref.content_sha256 == expected_prompt_hash

    assert prov.model_ref.artifact_code == "guideline-model"
    assert prov.model_ref.version == f"openai:{model_name}"
    expected_model_hash = hashlib.sha256(f"openai:{model_name}".encode()).hexdigest()
    assert prov.model_ref.content_sha256 == expected_model_hash

    assert prov.parser_ref.artifact_code == "guideline-parser"
    assert prov.parser_ref.version == "guideline-structured-parser-v1"

    assert prov.validator_ref.artifact_code == "guideline-validator"
    assert prov.validator_ref.version == "guideline-card-kernel-v1"


def test_build_guideline_generation_input_projection_slot_determinism_and_privacy() -> None:
    med1 = make_medication(
        item_id="22222222-2222-4222-8222-222222222222",
        canonical_code="ITEM-B",
    )
    med2 = make_medication(
        item_id="11111111-1111-4111-8111-111111111111",
        canonical_code="ITEM-A",
    )
    sel1 = make_gate_passed_selection(
        evidence_key="knowledge:z-last",
        locator="$.items[1]",
        source_version="api:ver-2",
        content_text=DAILY_ACTIVITY_TEXT,
    )
    sel2 = make_gate_passed_selection(
        evidence_key="knowledge:a-first",
        locator="$.items[0]",
        source_version="api:ver-1",
        content_text=FOOD_AVOIDANCE_TEXT,
    )

    request = make_generation_request(
        medications=(med1, med2),  # intentionally reversed
        selections=(sel1, sel2),  # intentionally reversed
    )

    input_json, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(request)

    # Deterministic sorting check:
    # med2 (ITEM-A) comes before med1 (ITEM-B)
    assert slot_to_med["m0"] == med2
    assert slot_to_med["m1"] == med1

    # sel2 (api:ver-1) comes before sel1 (api:ver-2)
    assert slot_to_ev["e0"] == sel2
    assert slot_to_ev["e1"] == sel1

    payload = json.loads(input_json)
    assert "medications" in payload
    assert "evidence" in payload

    # Medication payload privacy verification:
    # No prescription_version_medication_id in projected json!
    assert "prescription_version_medication_id" not in input_json
    assert "11111111-1111-4111-8111-111111111111" not in input_json
    assert "22222222-2222-4222-8222-222222222222" not in input_json
    assert payload["medications"][0] == {
        "medication_slot": "m0",
        "code_system": med2.code_system,
        "canonical_code": med2.canonical_code,
    }

    # Evidence payload privacy verification:
    # No locators, source snapshots, hashes, or receipt refs!
    assert "source_snapshot_ref" not in input_json
    assert "source-snapshot" not in input_json
    assert "content_sha256" not in input_json
    assert "retrieval_receipt_ref" not in input_json
    assert "assessment_artifact_ref" not in input_json
    assert payload["evidence"][0] == {
        "evidence_slot": "e0",
        "content_text": FOOD_AVOIDANCE_TEXT,
    }


def test_parse_guideline_structured_output_success_and_deduplication() -> None:
    med = make_medication()
    sel1 = make_gate_passed_selection(
        evidence_key="knowledge:ev-1",
        source_version="api:1",
        locator="$.items[0]",
        content_text=FOOD_AVOIDANCE_TEXT,
    )
    sel2 = make_gate_passed_selection(
        evidence_key="knowledge:ev-2",
        source_version="api:2",
        locator="$.items[1]",
        content_text=DAILY_ACTIVITY_TEXT,
    )

    request = make_generation_request(medications=(med,), selections=(sel1, sel2))
    _, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(request)

    # Provider returns duplicate claims for the same (medication_slot, scope)
    # with duplicate/overlapping evidence_slots in reverse order.
    raw = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e1", "e0"],
            ),
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],  # duplicate claim to merge
            ),
        ]
    )

    draft = parse_guideline_structured_output(
        raw,
        slot_to_medication=slot_to_med,
        slot_to_evidence=slot_to_ev,
        maximum_claims=4,
    )

    assert draft is not None
    # Merged into single claim
    assert len(draft.claims) == 1
    claim = draft.claims[0]
    assert claim.claim_key == "claim:001"
    assert claim.medication_identity == med
    assert claim.scope is GuidelineScope.FOOD_CAUTION
    assert claim.action_class is GuidelineActionClass.FOOD_AVOIDANCE
    assert claim.action_text.reveal() == FOOD_AVOIDANCE_TEXT

    # Evidence slots e0 and e1 are merged and deduplicated
    assert len(claim.citations) == 2
    # Sorted by (source_version, locator, evidence_key): e0 has api:1, e1 has api:2
    assert claim.citations[0].evidence_key == "knowledge:ev-1"
    assert claim.citations[0].source_version == "api:1"
    assert claim.citations[0].locator == "$.items[0]"
    assert claim.citations[0].content_sha256 == hashlib.sha256(FOOD_AVOIDANCE_TEXT.encode()).hexdigest()

    assert claim.citations[1].evidence_key == "knowledge:ev-2"
    assert claim.citations[1].source_version == "api:2"

    assert draft.uncertainty_text.reveal() == "승인된 근거 범위 밖의 내용은 확인할 수 없습니다."
    assert draft.consultation_text.reveal() == "불편하거나 궁금한 점은 의사 또는 약사와 상담하세요."


def test_parse_does_not_merge_different_medications_with_same_canonical_code() -> None:
    med1 = make_medication(
        item_id="11111111-1111-4111-8111-111111111111",
        canonical_code="SAME-CODE",
    )
    med2 = make_medication(
        item_id="22222222-2222-4222-8222-222222222222",
        canonical_code="SAME-CODE",
    )
    sel = make_gate_passed_selection()

    request = make_generation_request(medications=(med1, med2), selections=(sel,))
    _, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(request)

    raw = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            ),
            GuidelineClaimSelection(
                medication_slot="m1",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            ),
        ]
    )

    draft = parse_guideline_structured_output(
        raw,
        slot_to_medication=slot_to_med,
        slot_to_evidence=slot_to_ev,
        maximum_claims=4,
    )

    assert draft is not None
    # Must NOT merge because medication_slot (and full MedicationIdentityRef) differs!
    assert len(draft.claims) == 2
    assert draft.claims[0].claim_key == "claim:001"
    assert draft.claims[1].claim_key == "claim:002"
    assert draft.claims[0].medication_identity.prescription_version_medication_id != (
        draft.claims[1].medication_identity.prescription_version_medication_id
    )


def test_parse_unknown_medication_slot_fails() -> None:
    request = make_generation_request()
    _, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(request)

    raw = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m999",  # forged/unknown slot
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            )
        ]
    )
    assert (
        parse_guideline_structured_output(
            raw, slot_to_medication=slot_to_med, slot_to_evidence=slot_to_ev, maximum_claims=4
        )
        is None
    )


def test_parse_unknown_evidence_slot_fails() -> None:
    request = make_generation_request()
    _, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(request)

    raw = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e999"],  # forged/unknown slot
            )
        ]
    )
    assert (
        parse_guideline_structured_output(
            raw, slot_to_medication=slot_to_med, slot_to_evidence=slot_to_ev, maximum_claims=4
        )
        is None
    )


def test_parse_empty_evidence_slots_fails() -> None:
    request = make_generation_request()
    _, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(request)

    raw = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=[],  # empty citations disallowed
            )
        ]
    )
    assert (
        parse_guideline_structured_output(
            raw, slot_to_medication=slot_to_med, slot_to_evidence=slot_to_ev, maximum_claims=4
        )
        is None
    )


def test_parse_empty_claims_fails() -> None:
    request = make_generation_request()
    _, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(request)

    raw = GuidelineStructuredSelection(claims=[])
    assert (
        parse_guideline_structured_output(
            raw, slot_to_medication=slot_to_med, slot_to_evidence=slot_to_ev, maximum_claims=4
        )
        is None
    )


def test_parse_claims_exceeding_maximum_claims_fails() -> None:
    request = make_generation_request(maximum_claims=1)
    _, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(request)

    raw = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            ),
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.DAILY_ACTIVITY,
                evidence_slots=["e0"],
            ),
        ]
    )
    # 2 merged claims exceeds maximum_claims=1 -> fail closed without partial draft!
    assert (
        parse_guideline_structured_output(
            raw, slot_to_medication=slot_to_med, slot_to_evidence=slot_to_ev, maximum_claims=1
        )
        is None
    )


def test_canonical_copy_helpers() -> None:
    med = make_medication()
    citation = GuidelineCitationDraft(
        evidence_key="ev-1",
        source_snapshot_ref=artifact("snap"),
        source_version="v1",
        locator="loc",
        content_sha256="b" * 64,
    )
    claim = create_canonical_claim_draft(
        claim_key="claim:001",
        medication_identity=med,
        scope=GuidelineScope.DAILY_ACTIVITY,
        citations=(citation,),
    )
    assert claim.claim_key == "claim:001"
    assert claim.action_class is GuidelineActionClass.DAILY_ACTIVITY_PRECAUTION
    assert claim.action_text.reveal() == DAILY_ACTIVITY_TEXT

    card = create_canonical_card_draft((claim,))
    assert card.claims == (claim,)
    assert card.uncertainty_text.reveal() == "승인된 근거 범위 밖의 내용은 확인할 수 없습니다."
    assert card.consultation_text.reveal() == "불편하거나 궁금한 점은 의사 또는 약사와 상담하세요."


class SyntheticApprovalVerifier:
    def verify(self, artifact_ref: ImmutableArtifactRef):
        from ai_worker.tasks.rag.guideline_card import GuidelineApprovalVerificationSuccess

        return GuidelineApprovalVerificationSuccess(
            artifact_ref=artifact_ref,
            verifier_artifact_ref=artifact("guideline-approval-verifier"),
        )


def test_finalizer_compatibility_regression() -> None:
    """Verifies that GuidelineCardDraft built by parser cleanly passes existing finalize_guideline_card."""
    med = make_medication()
    sel = make_gate_passed_selection()
    request = make_generation_request(medications=(med,), selections=(sel,))
    _, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(request)

    raw = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            )
        ]
    )
    draft = parse_guideline_structured_output(
        raw,
        slot_to_medication=slot_to_med,
        slot_to_evidence=slot_to_ev,
        maximum_claims=4,
    )
    assert draft is not None

    from ai_worker.tasks.rag.evidence_gate import canonical_gate_selection_hash

    binding = ApprovedGuidelineEvidenceBinding.create(
        "evidence-binding",
        "evidence-binding@synthetic-1",
        medication_identity=med,
        scope=GuidelineScope.FOOD_CAUTION,
        action_class=GuidelineActionClass.FOOD_AVOIDANCE,
        evidence_key=sel.selection.candidate.provenance.evidence_key,
        assessment_artifact_ref=sel.assessment_artifact_ref,
        selection_projection_sha256=canonical_gate_selection_hash(sel.selection),
        action_text_sha256=hashlib.sha256(FOOD_AVOIDANCE_TEXT.encode()).hexdigest(),
    )

    card_request = GuidelineCardRequest(
        medication_identities=(med,),
        evidence_gate_outcome=request.evidence_gate_outcome,
        draft=draft,
        generation_failure=None,
        policy=request.policy,
        provenance=build_candidate_provenance(model="gpt-4o"),
        approved_fallbacks=tuple(
            ApprovedGuidelineFallback.create(
                "guideline-fallback",
                f"guideline-fallback@synthetic-{code.value.lower()}",
                code=code,
                text=SensitiveText("현재는 승인된 안내를 제공할 수 없습니다. 의사 또는 약사와 상담하세요."),
            )
            for code in GuidelineFallbackCode
        ),
        evaluated_at=EVALUATED_AT,
        approved_evidence_bindings=(binding,),
    )

    outcome = _finalize_guideline_card(card_request, approval_verifier=SyntheticApprovalVerifier())
    assert outcome.status is GuidelineCardStatus.GENERATED
    assert outcome.reason.value == "CARD_GENERATED"
    assert outcome.card is not None
    assert len(outcome.card.claims) == 1
    assert outcome.card.claims[0].claim_key == "claim:001"
