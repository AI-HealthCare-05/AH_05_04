from __future__ import annotations

import ast
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

import pytest

from app.services.guide_runtime_projection import (
    GuideRuntimePersistenceGap,
    GuideRuntimeProjectionKind,
    project_guide_runtime_release,
)
from rag_runtime.guide_release_projection import (
    GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
    GuideRuntimeApprovedAnswer,
    GuideRuntimeApprovedFallback,
    GuideRuntimeCitationSourceType,
    GuideRuntimeFallbackCode,
    GuideRuntimeReleaseDecision,
    GuideRuntimeReleaseProjectionCarrier,
    GuideRuntimeReleaseProjectionUnavailable,
    GuideRuntimeVerifiedCitation,
)

MODULE_PATH = Path("backend/app/services/guide_runtime_projection.py")
UUIDS = (
    (UUID("00000000-0000-0000-0000-000000000101"), UUID("00000000-0000-0000-0000-000000000102")),
    (UUID("00000000-0000-0000-0000-000000000201"), UUID("00000000-0000-0000-0000-000000000202")),
)


def test_pass_result_preserves_approved_answer_and_ordered_verified_citations() -> None:
    carrier = _pass_carrier()

    projection = project_guide_runtime_release(carrier)

    assert projection.kind is GuideRuntimeProjectionKind.ANSWER
    assert projection.is_current is True
    assert projection.answer is carrier.answer
    assert projection.fallback is None
    assert projection.citations is carrier.citations
    assert tuple(citation.display_order for citation in projection.citations) == (1, 2)
    assert projection.citations[0] == _citation(display_order=1, evidence_key="evidence-food")
    assert projection.citations[1] == _citation(display_order=2, evidence_key="evidence-routine")
    assert GuideRuntimePersistenceGap.RELEASE_STATUS_NOT_PERSISTED in projection.persistence_gaps
    assert GuideRuntimePersistenceGap.LEGACY_CITATION_TABLE_INCOMPATIBLE in projection.persistence_gaps


@pytest.mark.parametrize(
    ("decision", "is_current", "expected_kind"),
    [
        (GuideRuntimeReleaseDecision.LIMITED, True, GuideRuntimeProjectionKind.LIMITED_FALLBACK),
        (GuideRuntimeReleaseDecision.REJECTED, True, GuideRuntimeProjectionKind.REJECTED_FALLBACK),
        (GuideRuntimeReleaseDecision.STALE, False, GuideRuntimeProjectionKind.STALE_FALLBACK),
    ],
)
def test_non_pass_result_preserves_only_approved_fallback(
    decision: GuideRuntimeReleaseDecision,
    is_current: bool,
    expected_kind: GuideRuntimeProjectionKind,
) -> None:
    carrier = _fallback_carrier(decision, is_current=is_current)

    projection = project_guide_runtime_release(carrier)

    assert projection.kind is expected_kind
    assert projection.is_current is is_current
    assert projection.answer is None
    assert projection.fallback is carrier.fallback
    assert projection.fallback is not None
    assert projection.fallback.code is GuideRuntimeFallbackCode.NO_APPROVED_EVIDENCE
    assert projection.fallback.text == "현재는 승인된 안내를 제공할 수 없습니다. 의사 또는 약사와 상담하세요."
    assert projection.citations == ()
    assert projection.persistence_gaps == (
        GuideRuntimePersistenceGap.RELEASE_STATUS_NOT_PERSISTED,
        GuideRuntimePersistenceGap.FALLBACK_NOT_PERSISTED,
    )


def test_unavailable_result_fails_closed_without_public_content() -> None:
    projection = project_guide_runtime_release(GuideRuntimeReleaseProjectionUnavailable())

    assert projection.kind is GuideRuntimeProjectionKind.FAIL_CLOSED_REJECTION
    assert projection.is_current is None
    assert projection.answer is None
    assert projection.fallback is None
    assert projection.citations == ()
    assert projection.persistence_gaps == (GuideRuntimePersistenceGap.NO_PUBLIC_CONTENT,)


def test_projection_shape_does_not_leak_internal_runtime_authority_fields() -> None:
    serialized = str(asdict(project_guide_runtime_release(_pass_carrier()))).lower()

    assert "execution_status" not in serialized
    assert "evidence_status" not in serialized
    assert "internal_reason" not in serialized
    assert "score" not in serialized
    assert "rank" not in serialized
    assert "confidence" not in serialized
    assert "raw_source" not in serialized
    assert "provider_response" not in serialized
    assert "authorization_receipt" not in serialized


def test_backend_projection_depends_only_on_shared_carrier_contract() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    called_names = {
        node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    defined_names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.ClassDef, ast.FunctionDef))}

    assert not any(module == "ai_worker" or module.startswith("ai_worker.") for module in imported_modules)
    assert "getattr" not in called_names
    assert "GuideRuntimeReleaseProjectionCarrier" not in defined_names
    assert "GuideRuntimeCanonicalCitationIdentity" not in defined_names
    assert "GuideRuntimeCitationProjectionCarrier" not in defined_names


def _pass_carrier() -> GuideRuntimeReleaseProjectionCarrier:
    return GuideRuntimeReleaseProjectionCarrier(
        contract_version=GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
        release_decision=GuideRuntimeReleaseDecision.PASS,
        is_current=True,
        answer=GuideRuntimeApprovedAnswer(
            claim_action_texts=("승인된 첫 번째 안내입니다.", "승인된 두 번째 안내입니다."),
            uncertainty_text="개인 상태에 따라 달라질 수 있습니다.",
            consultation_text="의사 또는 약사와 상담하세요.",
        ),
        fallback=None,
        citations=(
            _citation(display_order=1, evidence_key="evidence-food"),
            _citation(display_order=2, evidence_key="evidence-routine"),
        ),
    )


def _fallback_carrier(
    release_decision: GuideRuntimeReleaseDecision,
    *,
    is_current: bool,
) -> GuideRuntimeReleaseProjectionCarrier:
    return GuideRuntimeReleaseProjectionCarrier(
        contract_version=GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
        release_decision=release_decision,
        is_current=is_current,
        answer=None,
        fallback=GuideRuntimeApprovedFallback(
            code=GuideRuntimeFallbackCode.NO_APPROVED_EVIDENCE,
            text="현재는 승인된 안내를 제공할 수 없습니다. 의사 또는 약사와 상담하세요.",
        ),
        citations=(),
    )


def _citation(*, display_order: int, evidence_key: str) -> GuideRuntimeVerifiedCitation:
    return GuideRuntimeVerifiedCitation(
        card_target_ref="card-sha256",
        claim_key=f"claim-{display_order}",
        evidence_key=evidence_key,
        source_type=GuideRuntimeCitationSourceType.LIFESTYLE_GUIDELINE,
        source_snapshot_id=UUIDS[display_order - 1][0],
        source_snapshot_member_id=UUIDS[display_order - 1][1],
        source_code="MFDS",
        source_version="2026-09-20",
        locator=f"section-{display_order}",
        content_sha256="a" * 64,
        display_order=display_order,
    )
