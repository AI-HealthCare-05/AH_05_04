from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ai_worker.tasks.evaluation.schemas.authoring_v1_2 import EVALUATION_CASE_ADAPTER_V1_2
from ai_worker.tasks.evaluation.schemas.policy_v1_2 import EvaluationProfileV12

EVALS_ROOT = Path(__file__).parents[3] / "evals"


def _draft_provenance() -> dict[str, object]:
    return {
        "authored_by": {
            "namespace": "GITHUB_LOGIN",
            "actor_id": "ceohwj",
            "role": "EVALUATION_IMPLEMENTER",
        },
        "reviewed_by": None,
        "approved_by": None,
        "authored_at": "2026-09-03T00:00:00.000000Z",
        "reviewed_at": None,
        "approved_at": None,
        "team_gold_status": "DRAFT",
        "external_medical_review_status": "PENDING",
        "external_medical_approval_receipt_ref": None,
        "evidence_review_refs": [],
    }


def _safety_case_v12_payload() -> dict[str, Any]:
    path = EVALS_ROOT / "retrieval/cases/dev-foundation-v1/rag-dev-safety-001.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = "1.2.0"
    payload["review_provenance"] = _draft_provenance()
    payload["context"]["runtime_fixture"].update(
        source_eligibility_status="ELIGIBLE",
        bundle_eligibility_status="ELIGIBLE",
        dependency_fault="NONE",
    )
    payload["expected"].update(
        expected_rule_outcome="MATCHED_RULES",
        expected_rule_not_invoked_reason=None,
    )
    return payload


def test_case_v12_accepts_draft_without_reviewer() -> None:
    case = EVALUATION_CASE_ADAPTER_V1_2.validate_python(_safety_case_v12_payload())

    assert case.schema_version == "1.2.0"
    assert case.review_provenance.reviewed_by is None


def test_case_v12_rejects_v11_draft_provenance_that_claims_a_reviewer() -> None:
    payload = _safety_case_v12_payload()
    payload["review_provenance"].update(
        reviewed_by={
            "namespace": "GITHUB_LOGIN",
            "actor_id": "Jye-rookie",
            "role": "MEDICAL_REVIEWER",
        },
        reviewed_at="2026-09-03T00:01:00.000000Z",
    )

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_2.validate_python(payload)


def test_safety_case_v12_keeps_product_safety_approval_role_requirement() -> None:
    payload = _safety_case_v12_payload()
    payload["review_provenance"].update(
        reviewed_by={
            "namespace": "GITHUB_LOGIN",
            "actor_id": "gold-fixture-reviewer",
            "role": "EVALUATION_REVIEWER",
        },
        approved_by={
            "namespace": "GITHUB_LOGIN",
            "actor_id": "safety-approver",
            "role": "PRODUCT_SAFETY_REVIEWER",
        },
        reviewed_at="2026-09-03T00:01:00.000000Z",
        approved_at="2026-09-03T00:02:00.000000Z",
        team_gold_status="APPROVED",
        evidence_review_refs=[{"id": "review-evidence-1", "version": "1.0.0", "hash": "a" * 64}],
    )

    case = EVALUATION_CASE_ADAPTER_V1_2.validate_python(payload)
    assert case.review_provenance.approved_by is not None
    assert case.review_provenance.approved_by.role.value == "PRODUCT_SAFETY_REVIEWER"


def test_profile_v12_requires_v12_draft_provenance_shape() -> None:
    payload = json.loads((EVALS_ROOT / "profiles/dev-foundation-v1.profile.json").read_text(encoding="utf-8"))
    payload["schema_version"] = "1.2.0"
    payload["review_provenance"] = _draft_provenance()

    profile = EvaluationProfileV12.model_validate(payload)
    assert profile.schema_version == "1.2.0"

    payload["review_provenance"]["reviewed_at"] = "2026-09-03T00:01:00.000000Z"
    with pytest.raises(ValidationError):
        EvaluationProfileV12.model_validate(payload)
