from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path
from uuid import UUID

from rag_runtime.guide_release_projection import (
    GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
    GuideRuntimeApprovedAnswer,
    GuideRuntimeApprovedFallback,
    GuideRuntimeFallbackCode,
    GuideRuntimeReleaseDecision,
    GuideRuntimeReleaseProjectionCarrier,
    GuideRuntimeReleaseProjectionUnavailable,
    GuideRuntimeVerifiedCitation,
)

MODULE_PATH = Path("rag_runtime/guide_release_projection.py")


def _imported_modules() -> set[str]:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_shared_carrier_has_only_safe_versioned_fields() -> None:
    assert tuple(field.name for field in fields(GuideRuntimeReleaseProjectionCarrier)) == (
        "contract_version",
        "release_decision",
        "is_current",
        "answer",
        "fallback",
        "citations",
    )
    assert tuple(field.name for field in fields(GuideRuntimeReleaseProjectionUnavailable)) == ("contract_version",)
    assert tuple(field.name for field in fields(GuideRuntimeApprovedAnswer)) == (
        "claim_action_texts",
        "uncertainty_text",
        "consultation_text",
    )
    assert tuple(field.name for field in fields(GuideRuntimeApprovedFallback)) == ("code", "text")
    assert tuple(field.name for field in fields(GuideRuntimeVerifiedCitation)) == (
        "card_target_ref",
        "claim_key",
        "evidence_key",
        "source_type",
        "source_snapshot_id",
        "source_snapshot_member_id",
        "source_code",
        "source_version",
        "locator",
        "content_sha256",
        "display_order",
    )


def test_shared_carrier_uses_fixed_vocabulary_and_uuid_coordinates() -> None:
    assert GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION == "guide-runtime-release-projection-v1"
    assert tuple(GuideRuntimeReleaseDecision) == (
        GuideRuntimeReleaseDecision.PASS,
        GuideRuntimeReleaseDecision.LIMITED,
        GuideRuntimeReleaseDecision.REJECTED,
        GuideRuntimeReleaseDecision.STALE,
    )
    assert {code.value for code in GuideRuntimeFallbackCode} == {
        "NO_APPROVED_EVIDENCE",
        "CONFLICTING_EVIDENCE",
        "PROVIDER_TIMEOUT",
        "DEPENDENCY_UNAVAILABLE",
        "VALIDATION_FAILED",
        "PRESCRIPTION_STALE",
        "EXECUTION_CONTEXT_STALE",
        "UNSUPPORTED_REQUEST",
    }
    citation_types = {field.name: field.type for field in fields(GuideRuntimeVerifiedCitation)}
    assert citation_types["source_snapshot_id"] is UUID
    assert citation_types["source_snapshot_member_id"] is UUID


def test_shared_module_has_no_worker_or_backend_import() -> None:
    modules = _imported_modules()
    forbidden_roots = ("ai_worker", "backend", "app", "sqlalchemy")
    assert not any(module == root or module.startswith(f"{root}.") for module in modules for root in forbidden_roots)


def test_shared_carrier_omits_internal_runtime_and_authority_fields() -> None:
    field_names = {
        field.name
        for carrier_type in (
            GuideRuntimeReleaseProjectionCarrier,
            GuideRuntimeReleaseProjectionUnavailable,
            GuideRuntimeApprovedAnswer,
            GuideRuntimeApprovedFallback,
            GuideRuntimeVerifiedCitation,
        )
        for field in fields(carrier_type)
    }
    forbidden = {
        "execution_status",
        "evidence_status",
        "internal_reason",
        "stopped_stage",
        "score",
        "rank",
        "confidence",
        "authorization_receipt",
        "assessment_artifact_ref",
        "eligibility_receipt_ref",
        "retrieval_receipt_ref",
        "verifier_artifact_ref",
        "provider_response",
    }
    assert field_names.isdisjoint(forbidden)
