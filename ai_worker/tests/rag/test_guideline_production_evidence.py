"""RAG-15 Production Guideline Evidence input contract tests (#774)."""

from __future__ import annotations

import ast
import hashlib
import inspect
import pathlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from ai_worker.tasks.rag import guideline_production_evidence
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.guide_evidence_handoff import (
    RequestDecisionStage,
    VerifiedGuideEvidenceHandoff,
    VerifiedGuideEvidenceSelection,
)
from ai_worker.tasks.rag.guideline_production_evidence import (
    PRODUCTION_GUIDELINE_EVIDENCE_SELECTION_PROJECTION_VERSION,
    ProductionGuidelineEvidence,
    canonical_production_guideline_evidence_selection_projection,
    compute_production_guideline_evidence_selection_hash,
    project_guideline_evidence_from_handoff,
)
from ai_worker.tasks.rag.source_member_identity import SourceMemberKind

EVALUATED_AT = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
FIRST_TEXT = "이 약을 복용하는 동안 과도한 음주는 피하세요."
SECOND_TEXT = "이 약을 복용하는 동안 무리한 운동은 피하고 충분히 휴식하세요."


def artifact(code: str, digest: str = "a" * 64) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(code, f"{code}@synthetic-1", digest)


def uuid_of(nibble: str) -> UUID:
    return UUID(f"{nibble * 8}-{nibble * 4}-4{nibble * 3}-8{nibble * 3}-{nibble * 12}")


def verified_selection(
    *,
    evidence_key: str = "knowledge:guideline-1",
    text: str = FIRST_TEXT,
    locator: str = "$.items[0].useMethodQesitm",
    source_version: str = "api:" + "1" * 64,
    final_rank: int = 1,
) -> VerifiedGuideEvidenceSelection:
    return VerifiedGuideEvidenceSelection(
        evidence_key=evidence_key,
        knowledge_chunk_id=uuid_of("2"),
        source_snapshot_id=uuid_of("3"),
        source_snapshot_member_id=uuid_of("4"),
        source_code="MFDS_DUR",
        source_version=source_version,
        locator=locator,
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        content_text=SensitiveText(text),
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="DUR_ITEM",
        operation_code="GET_ITEM",
        artifact_code=None,
        artifact_version=None,
        request_guard_ref=artifact("request-guard"),
        request_operation_code="GUIDE_GENERATE",
        request_decision_stage=RequestDecisionStage.REQUEST,
        request_source_decision_ref=artifact("request-source-decision"),
        request_member_decision_ref=artifact("request-member-decision"),
        retrieval_receipt_ref=artifact("retrieval-receipt"),
        eligibility_receipt_ref=artifact("eligibility-receipt"),
        assessment_artifact_ref=artifact("assessment"),
        verifier_artifact_ref=artifact("assessment-verifier"),
        assessment_valid_from=EVALUATED_AT - timedelta(days=1),
        assessment_valid_until=EVALUATED_AT + timedelta(days=1),
        final_rank=final_rank,
        canonical_checksum="c" * 64,
        external_document_id="doc-1",
        chunk_index=0,
        canonicalization_spec_version="knowledge-text@1",
        normalization_version="normalize@1",
    )


def verified_handoff(
    *selections: VerifiedGuideEvidenceSelection,
) -> VerifiedGuideEvidenceHandoff:
    items = selections or (verified_selection(),)
    return VerifiedGuideEvidenceHandoff(
        retrieval_receipt_ref=artifact("retrieval-receipt"),
        retrieval_selection_manifest_sha256="d" * 64,
        evaluated_at=EVALUATED_AT,
        selections=items,
        handoff_sha256="e" * 64,
    )


def production_evidence(**overrides: object) -> ProductionGuidelineEvidence:
    base = project_guideline_evidence_from_handoff(verified_handoff()).selections[0]
    return replace(base, **overrides)  # type: ignore[arg-type]


# ==============================================================================
# Handoff -> production evidence projection
# ==============================================================================


def test_valid_handoff_projects_to_the_minimal_production_evidence_contract() -> None:
    handoff = verified_handoff()
    upstream = handoff.selections[0]

    evidence_set = project_guideline_evidence_from_handoff(handoff)

    assert evidence_set.evaluated_at == handoff.evaluated_at
    assert evidence_set.handoff_sha256 == handoff.handoff_sha256
    assert len(evidence_set.selections) == 1
    projected = evidence_set.selections[0]
    assert projected.evidence_key == upstream.evidence_key
    assert projected.source_snapshot_id == upstream.source_snapshot_id
    assert projected.source_snapshot_member_id == upstream.source_snapshot_member_id
    assert projected.source_code == upstream.source_code
    assert projected.source_version == upstream.source_version
    assert projected.locator == upstream.locator
    assert projected.content_sha256 == upstream.content_sha256
    assert projected.content_text.reveal() == upstream.content_text.reveal()
    assert projected.retrieval_receipt_ref == upstream.retrieval_receipt_ref
    assert projected.eligibility_receipt_ref == upstream.eligibility_receipt_ref
    assert projected.assessment_artifact_ref == upstream.assessment_artifact_ref
    assert projected.verifier_artifact_ref == upstream.verifier_artifact_ref


def test_production_evidence_omits_upstream_audit_only_fields() -> None:
    """RAG-15가 소비하지 않는 상류 provenance는 입력 타입에 복사하지 않는다."""
    fields = set(ProductionGuidelineEvidence.__dataclass_fields__)

    for omitted in (
        "knowledge_chunk_id",
        "member_kind",
        "endpoint_code",
        "operation_code",
        "artifact_code",
        "artifact_version",
        "request_guard_ref",
        "request_operation_code",
        "request_decision_stage",
        "request_source_decision_ref",
        "request_member_decision_ref",
        "assessment_valid_from",
        "assessment_valid_until",
        "final_rank",
        "canonical_checksum",
        "external_document_id",
        "chunk_index",
        "canonicalization_spec_version",
        "normalization_version",
    ):
        assert omitted not in fields


def test_projection_order_is_deterministic_and_independent_of_upstream_rank() -> None:
    first = verified_selection(
        evidence_key="knowledge:guideline-a",
        locator="$.items[0].a",
        source_version="api:" + "1" * 64,
        final_rank=1,
    )
    second = verified_selection(
        evidence_key="knowledge:guideline-b",
        text=SECOND_TEXT,
        locator="$.items[0].b",
        source_version="api:" + "1" * 64,
        final_rank=2,
    )

    forward = project_guideline_evidence_from_handoff(verified_handoff(first, second))
    reversed_rank = project_guideline_evidence_from_handoff(
        verified_handoff(replace(second, final_rank=1), replace(first, final_rank=2))
    )

    keys = tuple(item.evidence_key for item in forward.selections)
    assert keys == ("knowledge:guideline-a", "knowledge:guideline-b")
    assert tuple(item.evidence_key for item in reversed_rank.selections) == keys


def test_projection_does_not_alias_upstream_sensitive_text_or_artifact_refs() -> None:
    handoff = verified_handoff()
    projected = project_guideline_evidence_from_handoff(handoff).selections[0]

    assert projected.content_text is not handoff.selections[0].content_text
    assert projected.assessment_artifact_ref is not handoff.selections[0].assessment_artifact_ref


LEGACY_GATE_NAMES = (
    "EvidenceGateOutcome",
    "GatePassedKnowledgeEvidenceSelection",
    "canonical_gate_selection_hash",
)

PRODUCTION_RAG15_MODULES = (
    "ai_worker/tasks/rag/guideline_production_evidence.py",
    "ai_worker/tasks/rag/guideline_generator.py",
    "ai_worker/tasks/rag/guideline_generator_prompt.py",
    "ai_worker/tasks/rag/guideline_card.py",
    "ai_worker/adapters/openai_guideline_generator.py",
)


def _executable_identifiers(path: str) -> set[str]:
    """Collect every identifier the module's executable code binds or references.

    Docstrings and comments are excluded on purpose: this guard is about the
    production import graph, not about prose that names the retired legacy domain.
    """
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.rsplit(".", 1)[-1])
            if node.asname:
                names.add(node.asname)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.update(node.module.split("."))
    return names


@pytest.mark.parametrize("path", PRODUCTION_RAG15_MODULES)
def test_production_rag15_path_has_no_legacy_evidence_gate_dependency(path: str) -> None:
    """#774 source-level guard: production path must not reach the RAG-14 Gate domain."""
    identifiers = _executable_identifiers(path)

    assert "evidence_gate" not in identifiers
    for forbidden in LEGACY_GATE_NAMES:
        assert forbidden not in identifiers


def test_projection_module_stays_pure() -> None:
    source = inspect.getsource(guideline_production_evidence)

    assert "import backend" not in source
    assert "from backend" not in source
    assert "sqlalchemy" not in source
    assert "datetime.now" not in source
    assert "utcnow" not in source


# ==============================================================================
# Production selection canonical projection and hash domain
# ==============================================================================


def test_canonical_projection_pins_the_explicit_projection_version() -> None:
    projection = canonical_production_guideline_evidence_selection_projection(production_evidence())

    assert projection["projection_version"] == PRODUCTION_GUIDELINE_EVIDENCE_SELECTION_PROJECTION_VERSION
    assert PRODUCTION_GUIDELINE_EVIDENCE_SELECTION_PROJECTION_VERSION == "guideline-production-evidence-selection-v1"


def test_canonical_projection_excludes_raw_content_text() -> None:
    evidence = production_evidence()
    projection = canonical_production_guideline_evidence_selection_projection(evidence)

    assert "content_text" not in projection
    assert FIRST_TEXT not in str(projection)
    assert projection["content_sha256"] == evidence.content_sha256


def test_same_semantic_selection_produces_the_same_hash() -> None:
    first = project_guideline_evidence_from_handoff(verified_handoff()).selections[0]
    second = project_guideline_evidence_from_handoff(verified_handoff()).selections[0]

    assert first is not second
    assert compute_production_guideline_evidence_selection_hash(
        first
    ) == compute_production_guideline_evidence_selection_hash(second)


def test_mutating_any_projected_immutable_fact_changes_the_hash() -> None:
    baseline = production_evidence()
    baseline_hash = compute_production_guideline_evidence_selection_hash(baseline)

    mutations = (
        production_evidence(evidence_key="knowledge:guideline-2"),
        production_evidence(source_snapshot_id=uuid_of("9")),
        production_evidence(source_snapshot_member_id=uuid_of("9")),
        production_evidence(source_code="OTHER_SOURCE"),
        production_evidence(source_version="api:" + "2" * 64),
        production_evidence(locator="$.items[1].useMethodQesitm"),
        production_evidence(content_sha256=hashlib.sha256(SECOND_TEXT.encode()).hexdigest()),
        production_evidence(assessment_artifact_ref=artifact("assessment", "b" * 64)),
        production_evidence(verifier_artifact_ref=artifact("assessment-verifier", "b" * 64)),
        production_evidence(eligibility_receipt_ref=artifact("eligibility-receipt", "b" * 64)),
        production_evidence(retrieval_receipt_ref=artifact("retrieval-receipt", "b" * 64)),
    )

    for mutated in mutations:
        assert compute_production_guideline_evidence_selection_hash(mutated) != baseline_hash


def test_raw_content_text_mutation_alone_does_not_change_the_hash() -> None:
    """documented policy: projection은 raw 본문을 담지 않고 content_sha256으로만 결속한다.

    따라서 content_sha256을 그대로 두고 본문만 바꾸면 hash는 변하지 않는다. 그 불일치는
    #760 handoff build와 Card citation 결속이 각각 fail closed로 거부한다.
    """
    baseline = production_evidence()
    text_only = production_evidence(content_text=SensitiveText(SECOND_TEXT))

    assert text_only.content_sha256 == baseline.content_sha256
    assert compute_production_guideline_evidence_selection_hash(
        text_only
    ) == compute_production_guideline_evidence_selection_hash(baseline)


def test_production_hash_is_not_preimage_compatible_with_the_legacy_gate_hash() -> None:
    """legacy projection payload를 그대로 넣어도 production hash와 같아지지 않는다."""
    from ai_worker.tasks.rag.guide_evidence_handoff import canonical_jcs_sha256

    evidence = production_evidence()
    without_version = {
        key: value
        for key, value in canonical_production_guideline_evidence_selection_projection(evidence).items()
        if key != "projection_version"
    }

    assert canonical_jcs_sha256(without_version) != compute_production_guideline_evidence_selection_hash(evidence)
