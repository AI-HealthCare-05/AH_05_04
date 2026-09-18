"""#781 Dynamic Guideline Evidence Binding derivation regression tests.

Derivation binds only the claim/citation combinations a real `GuidelineCardDraft`
actually produced. It never re-judges the Source, assessment or freshness authority
that #760 already closed, and it never repairs, normalizes or deduplicates a
malformed draft.
"""

from __future__ import annotations

import ast
import hashlib
import pathlib
from dataclasses import replace

from ai_worker.tasks.rag.evidence_retrieval import SensitiveText
from ai_worker.tasks.rag.guideline_card import (
    GuidelineActionClass,
    GuidelineCardDraft,
    GuidelineCitationDraft,
    GuidelineClaimDraft,
    GuidelineScope,
    MedicationIdentityRef,
    create_canonical_card_draft,
    create_canonical_claim_draft,
)
from ai_worker.tasks.rag.guideline_evidence_binding_authority import (
    GUIDELINE_EVIDENCE_BINDING_ARTIFACT_CODE,
    GUIDELINE_EVIDENCE_BINDING_ARTIFACT_VERSION,
    GuidelineAuthorityFailureReason,
    derive_guideline_evidence_bindings,
)
from ai_worker.tasks.rag.guideline_production_evidence import (
    ProductionGuidelineEvidence,
    compute_production_guideline_evidence_selection_hash,
)
from ai_worker.tests.rag.test_guideline_card import (
    FOOD_AVOIDANCE_TEXT,
    citation_draft_for,
    medication,
    production_evidence_set,
    production_selection,
)

DAILY_ACTIVITY_TEXT = "이 약을 복용하는 동안 무리한 운동은 피하고 충분히 휴식하세요."


def second_medication() -> MedicationIdentityRef:
    return MedicationIdentityRef(
        prescription_version_medication_id="22222222-2222-4222-8222-222222222222",
        code_system="MFDS_ITEM_SEQ",
        canonical_code="SYNTHETIC-ITEM-002",
    )


def second_selection() -> ProductionGuidelineEvidence:
    return production_selection(
        evidence_key="knowledge:guideline-2",
        locator="$.items[0].atpnQesitm",
        source_version="api:" + "2" * 64,
        text=DAILY_ACTIVITY_TEXT,
    )


def food_claim(
    evidence: ProductionGuidelineEvidence,
    *,
    claim_key: str = "claim-food-1",
    identity: MedicationIdentityRef | None = None,
) -> GuidelineClaimDraft:
    return create_canonical_claim_draft(
        claim_key=claim_key,
        medication_identity=identity if identity is not None else medication(),
        scope=GuidelineScope.FOOD_CAUTION,
        citations=(citation_draft_for(evidence),),
    )


def derive(draft: GuidelineCardDraft, *selections: ProductionGuidelineEvidence, medications=None):
    return derive_guideline_evidence_bindings(
        evidence=production_evidence_set(*selections),
        medication_identities=medications if medications is not None else (medication(),),
        draft=draft,
    )


def assert_failed(outcome, reason: GuidelineAuthorityFailureReason) -> None:
    assert outcome.bindings is None
    assert outcome.reason is reason


# --- Derivation happy paths ---------------------------------------------------


def test_single_claim_with_single_citation_derives_exactly_one_binding() -> None:
    evidence = production_selection()
    outcome = derive(create_canonical_card_draft((food_claim(evidence),)), evidence)

    assert outcome.reason is None
    assert outcome.bindings is not None
    assert len(outcome.bindings) == 1
    binding = outcome.bindings[0]
    assert binding.evidence_key == evidence.evidence_key
    assert binding.medication_identity == medication()
    assert binding.scope is GuidelineScope.FOOD_CAUTION
    assert binding.action_class is GuidelineActionClass.FOOD_AVOIDANCE
    assert binding.assessment_artifact_ref == evidence.assessment_artifact_ref


def test_binding_uses_the_774_production_selection_hash_and_exact_action_text_hash() -> None:
    evidence = production_selection()
    outcome = derive(create_canonical_card_draft((food_claim(evidence),)), evidence)

    assert outcome.bindings is not None
    binding = outcome.bindings[0]
    assert binding.selection_projection_sha256 == compute_production_guideline_evidence_selection_hash(evidence)
    assert binding.action_text_sha256 == hashlib.sha256(FOOD_AVOIDANCE_TEXT.encode("utf-8")).hexdigest()


def test_binding_artifact_identity_is_the_fixed_production_identity() -> None:
    evidence = production_selection()
    outcome = derive(create_canonical_card_draft((food_claim(evidence),)), evidence)

    assert outcome.bindings is not None
    ref = outcome.bindings[0].artifact_ref
    assert ref.artifact_code == GUIDELINE_EVIDENCE_BINDING_ARTIFACT_CODE
    assert ref.version == GUIDELINE_EVIDENCE_BINDING_ARTIFACT_VERSION


def test_one_claim_with_two_citations_derives_one_binding_per_citation() -> None:
    first = production_selection()
    second = second_selection()
    claim = create_canonical_claim_draft(
        claim_key="claim-food-1",
        medication_identity=medication(),
        scope=GuidelineScope.FOOD_CAUTION,
        citations=(citation_draft_for(first), citation_draft_for(second)),
    )
    outcome = derive(create_canonical_card_draft((claim,)), first, second)

    assert outcome.bindings is not None
    assert [item.evidence_key for item in outcome.bindings] == [first.evidence_key, second.evidence_key]


def test_multiple_medications_and_evidence_derive_only_actual_draft_combinations() -> None:
    first = production_selection()
    second = second_selection()
    identities = (medication(), second_medication())
    draft = create_canonical_card_draft(
        (
            food_claim(first, claim_key="claim-food-1", identity=identities[0]),
            create_canonical_claim_draft(
                claim_key="claim-activity-1",
                medication_identity=identities[1],
                scope=GuidelineScope.DAILY_ACTIVITY,
                citations=(citation_draft_for(second),),
            ),
        )
    )
    outcome = derive(draft, first, second, medications=identities)

    assert outcome.bindings is not None
    # A Cartesian product of 2 medications x 2 evidence x 2 scopes would be 8.
    assert len(outcome.bindings) == 2
    assert {(item.medication_identity, item.evidence_key, item.scope) for item in outcome.bindings} == {
        (identities[0], first.evidence_key, GuidelineScope.FOOD_CAUTION),
        (identities[1], second.evidence_key, GuidelineScope.DAILY_ACTIVITY),
    }


def test_same_semantic_inputs_derive_identical_binding_artifact_refs() -> None:
    def run():
        evidence = production_selection()
        return derive(create_canonical_card_draft((food_claim(evidence),)), evidence)

    first, second = run(), run()
    assert first.bindings is not None and second.bindings is not None
    assert [item.artifact_ref for item in first.bindings] == [item.artifact_ref for item in second.bindings]


def test_different_evidence_changes_the_dynamic_binding_ref() -> None:
    first = production_selection()
    second = second_selection()
    left = derive(create_canonical_card_draft((food_claim(first),)), first)
    right = derive(create_canonical_card_draft((food_claim(second),)), second)

    assert left.bindings is not None and right.bindings is not None
    assert left.bindings[0].artifact_ref != right.bindings[0].artifact_ref


# --- Fail-closed derivation ---------------------------------------------------


def test_empty_draft_claims_fail_closed() -> None:
    assert_failed(
        derive(
            GuidelineCardDraft(claims=(), uncertainty_text=SensitiveText("x"), consultation_text=SensitiveText("y")),
            production_selection(),
        ),
        GuidelineAuthorityFailureReason.DRAFT_INVALID,
    )


def test_invalid_draft_object_fails_closed() -> None:
    evidence = production_selection()
    outcome = derive_guideline_evidence_bindings(
        evidence=production_evidence_set(evidence),
        medication_identities=(medication(),),
        draft="not-a-draft",  # type: ignore[arg-type]
    )
    assert_failed(outcome, GuidelineAuthorityFailureReason.DRAFT_INVALID)


def test_claim_without_citations_fails_closed() -> None:
    evidence = production_selection()
    claim = replace(food_claim(evidence), citations=())
    assert_failed(
        derive(create_canonical_card_draft((claim,)), evidence),
        GuidelineAuthorityFailureReason.DRAFT_INVALID,
    )


def test_empty_pinned_medications_fail_closed() -> None:
    evidence = production_selection()
    assert_failed(
        derive(create_canonical_card_draft((food_claim(evidence),)), evidence, medications=()),
        GuidelineAuthorityFailureReason.REQUEST_INVALID,
    )


def test_unpinned_medication_fails_closed() -> None:
    evidence = production_selection()
    claim = food_claim(evidence, identity=second_medication())
    assert_failed(
        derive(create_canonical_card_draft((claim,)), evidence),
        GuidelineAuthorityFailureReason.MEDICATION_NOT_PINNED,
    )


def test_empty_production_evidence_fails_closed() -> None:
    evidence = production_selection()
    outcome = derive_guideline_evidence_bindings(
        evidence=replace(production_evidence_set(evidence), selections=()),
        medication_identities=(medication(),),
        draft=create_canonical_card_draft((food_claim(evidence),)),
    )
    assert_failed(outcome, GuidelineAuthorityFailureReason.REQUEST_INVALID)


def test_unknown_evidence_key_fails_closed() -> None:
    evidence = production_selection()
    citation = replace(citation_draft_for(evidence), evidence_key="knowledge:unknown")
    claim = replace(food_claim(evidence), citations=(citation,))
    assert_failed(
        derive(create_canonical_card_draft((claim,)), evidence),
        GuidelineAuthorityFailureReason.EVIDENCE_NOT_FOUND,
    )


def _mismatched_citation(evidence: ProductionGuidelineEvidence, **overrides) -> GuidelineCitationDraft:
    return replace(citation_draft_for(evidence), **overrides)


def test_citation_source_snapshot_id_mismatch_fails_closed() -> None:
    evidence = production_selection()
    other = second_selection()
    claim = replace(
        food_claim(evidence),
        citations=(_mismatched_citation(evidence, source_snapshot_id=other.source_snapshot_member_id),),
    )
    assert_failed(
        derive(create_canonical_card_draft((claim,)), evidence),
        GuidelineAuthorityFailureReason.CITATION_MISMATCH,
    )


def test_citation_source_snapshot_member_id_mismatch_fails_closed() -> None:
    evidence = production_selection()
    claim = replace(
        food_claim(evidence),
        citations=(_mismatched_citation(evidence, source_snapshot_member_id=evidence.source_snapshot_id),),
    )
    assert_failed(
        derive(create_canonical_card_draft((claim,)), evidence),
        GuidelineAuthorityFailureReason.CITATION_MISMATCH,
    )


def test_citation_source_code_mismatch_fails_closed() -> None:
    evidence = production_selection()
    claim = replace(food_claim(evidence), citations=(_mismatched_citation(evidence, source_code="OTHER_SOURCE"),))
    assert_failed(
        derive(create_canonical_card_draft((claim,)), evidence),
        GuidelineAuthorityFailureReason.CITATION_MISMATCH,
    )


def test_citation_source_version_mismatch_fails_closed() -> None:
    evidence = production_selection()
    claim = replace(food_claim(evidence), citations=(_mismatched_citation(evidence, source_version="api:" + "9" * 64),))
    assert_failed(
        derive(create_canonical_card_draft((claim,)), evidence),
        GuidelineAuthorityFailureReason.CITATION_MISMATCH,
    )


def test_citation_locator_mismatch_fails_closed() -> None:
    evidence = production_selection()
    claim = replace(food_claim(evidence), citations=(_mismatched_citation(evidence, locator="$.items[9].other"),))
    assert_failed(
        derive(create_canonical_card_draft((claim,)), evidence),
        GuidelineAuthorityFailureReason.CITATION_MISMATCH,
    )


def test_citation_content_sha256_mismatch_fails_closed() -> None:
    evidence = production_selection()
    claim = replace(food_claim(evidence), citations=(_mismatched_citation(evidence, content_sha256="b" * 64),))
    assert_failed(
        derive(create_canonical_card_draft((claim,)), evidence),
        GuidelineAuthorityFailureReason.CITATION_MISMATCH,
    )


def test_duplicate_binding_identity_fails_closed_without_silent_dedup() -> None:
    evidence = production_selection()
    draft = create_canonical_card_draft(
        (
            food_claim(evidence, claim_key="claim-food-1"),
            food_claim(evidence, claim_key="claim-food-2"),
        )
    )
    assert_failed(derive(draft, evidence), GuidelineAuthorityFailureReason.DUPLICATE_BINDING)


def test_duplicate_citation_evidence_key_inside_one_claim_fails_closed() -> None:
    evidence = production_selection()
    claim = replace(
        food_claim(evidence),
        citations=(citation_draft_for(evidence), citation_draft_for(evidence)),
    )
    assert_failed(
        derive(create_canonical_card_draft((claim,)), evidence),
        GuidelineAuthorityFailureReason.DUPLICATE_BINDING,
    )


def test_duplicate_evidence_key_in_production_evidence_fails_closed() -> None:
    evidence = production_selection()
    outcome = derive_guideline_evidence_bindings(
        evidence=production_evidence_set(evidence, replace(evidence, locator="$.items[1].other")),
        medication_identities=(medication(),),
        draft=create_canonical_card_draft((food_claim(evidence),)),
    )
    assert_failed(outcome, GuidelineAuthorityFailureReason.REQUEST_INVALID)


def test_duplicate_pinned_medication_fails_closed() -> None:
    evidence = production_selection()
    assert_failed(
        derive(
            create_canonical_card_draft((food_claim(evidence),)),
            evidence,
            medications=(medication(), medication()),
        ),
        GuidelineAuthorityFailureReason.REQUEST_INVALID,
    )


# --- Source-level boundary guards ---------------------------------------------


def _imported_module_roots(path: str) -> set[str]:
    """Every module path the file actually imports, from the AST only.

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


def test_authority_module_has_no_io_persistence_or_async_dependency() -> None:
    """#781 is a pure, synchronous, request-scoped seam."""
    path = "ai_worker/tasks/rag/guideline_evidence_binding_authority.py"
    for module in _imported_module_roots(path):
        root = module.split(".")[0]
        assert root not in {"asyncio", "backend", "openai", "rag_runtime", "sqlalchemy", "time"}

    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)]
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "now" not in attributes
    assert "utcnow" not in attributes


def test_authority_module_does_not_read_the_746_authority_again() -> None:
    """Assessment/Eligibility authority was consumed by #760; a second read is forbidden."""
    modules = _imported_module_roots("ai_worker/tasks/rag/guideline_evidence_binding_authority.py")

    assert not [module for module in modules if "evidence_authority" in module]
    assert not [module for module in modules if "repository" in module]
