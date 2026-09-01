from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from ai_worker.tasks.evaluation.schemas import authoring as authoring_schemas
from ai_worker.tasks.evaluation.schemas.authoring import (
    EVALUATION_CASE_ADAPTER,
    DatasetManifest,
    EvidenceMappingManifest,
)
from ai_worker.tasks.evaluation.schemas.common import SchemaValidationError


def _actor(
    actor_id: str,
    _display_name: str,
    role: str = "EVALUATION_IMPLEMENTER",
) -> dict[str, object]:
    return {
        "namespace": "GITHUB_LOGIN",
        "actor_id": actor_id,
        "role": role,
    }


def _provenance() -> dict[str, object]:
    return {
        "authored_by": _actor("author-1", "Author"),
        "reviewed_by": _actor("reviewer-1", "Reviewer", "PRIVACY_REVIEWER"),
        "approved_by": _actor("approver-1", "Approver", "DATASET_CUSTODIAN"),
        "authored_at": "2026-09-01T00:00:00.000000Z",
        "reviewed_at": "2026-09-01T00:00:00.000000Z",
        "approved_at": "2026-09-01T00:00:00.000000Z",
        "team_gold_status": "APPROVED",
        "external_medical_review_status": "NOT_REQUESTED",
        "external_medical_approval_receipt_ref": None,
        "evidence_review_refs": [],
    }


@pytest.fixture
def valid_retrieval_case() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "case_id": "rag-dev-retrieval-001",
        "dataset_code": "rag-dev-foundation",
        "dataset_version": "1.0.0",
        "partition": "DEV",
        "content_classification": "SYNTHETIC",
        "task_type": "RETRIEVAL",
        "input_hash": "a" * 64,
        "question": "SYNTHETIC_QUESTION",
        "context": {
            "prescription_fixture": "fixtures/prescription.json",
            "medication_fixtures": ["fixtures/medication.json"],
            "patient_context_fixture": None,
            "runtime_fixture": "fixtures/runtime.json",
        },
        "leakage_groups": {
            "question_template": "retrieval-template-001",
            "source_segment": "segment-001",
            "medication_family": "family-001",
            "transform_origin": "origin-001",
        },
        "expected": {
            "gold_evidence_ids": ["ev-synthetic-chunk-001"],
            "gold_claims": None,
            "gold_citation_evidence_ids": None,
            "gold_rule_ids": None,
            "expected_scope": None,
            "expected_safety_disposition": None,
        },
        "review_provenance": _provenance(),
    }


def test_outer_task_type_discriminator_selects_retrieval_expected_model(
    valid_retrieval_case: dict[str, Any],
) -> None:
    case = EVALUATION_CASE_ADAPTER.validate_python(valid_retrieval_case)

    assert case.task_type.value == "RETRIEVAL"
    assert case.expected.gold_evidence_ids == ["ev-synthetic-chunk-001"]


def test_retrieval_case_rejects_answer_gold_fields(valid_retrieval_case: dict[str, Any]) -> None:
    valid_retrieval_case["expected"]["gold_claims"] = []

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER.validate_python(valid_retrieval_case)


def test_retrieval_case_requires_non_applicable_fields_as_explicit_null(
    valid_retrieval_case: dict[str, Any],
) -> None:
    del valid_retrieval_case["expected"]["expected_scope"]

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER.validate_python(valid_retrieval_case)


def test_outer_discriminator_rejects_task_expected_shape_mismatch(
    valid_retrieval_case: dict[str, Any],
) -> None:
    valid_retrieval_case["task_type"] = "ANSWER_QUALITY"

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER.validate_python(valid_retrieval_case)


def test_evaluation_context_rejects_fields_outside_fixture_allowlist(
    valid_retrieval_case: dict[str, Any],
) -> None:
    valid_retrieval_case["context"]["ocr_raw"] = "SYNTHETIC_VALUE"

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER.validate_python(valid_retrieval_case)


def _valid_dataset_manifest() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "dataset_code": "rag-dev-foundation",
        "dataset_version": "1.0.0",
        "content_classification": "SYNTHETIC",
        "fixture_git_commit_sha": "b" * 40,
        "protected_artifact_receipt_ref": None,
        "deidentification_approval_receipt_ref": None,
        "case_resources": [
            {
                "case_id": "rag-dev-retrieval-001",
                "partition": "DEV",
                "task_type": "RETRIEVAL",
                "path": "retrieval/cases/retrieval.json",
                "sha256": "c" * 64,
            }
        ],
        "evidence_mapping": {
            "path": "retrieval/evidence/evidence.json",
            "sha256": "d" * 64,
        },
        "critical_claim_rubric": {
            "path": "retrieval/manifests/rubric.json",
            "sha256": "e" * 64,
        },
        "review_provenance": _provenance(),
        "content_hash": "f" * 64,
    }


@pytest.mark.parametrize(
    ("fixture_sha", "protected_receipt"),
    [(None, None), ("b" * 40, "receipt:protected:001")],
)
def test_dataset_manifest_requires_exactly_one_source_provenance(
    fixture_sha: str | None,
    protected_receipt: str | None,
) -> None:
    payload = _valid_dataset_manifest()
    payload["fixture_git_commit_sha"] = fixture_sha
    payload["protected_artifact_receipt_ref"] = protected_receipt

    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(payload)


def test_approved_deidentified_dataset_requires_approval_receipt() -> None:
    payload = _valid_dataset_manifest()
    payload["content_classification"] = "APPROVED_DEIDENTIFIED"
    payload["fixture_git_commit_sha"] = None
    payload["protected_artifact_receipt_ref"] = "receipt:protected:001"

    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(payload)

    payload["deidentification_approval_receipt_ref"] = "receipt:privacy:001"
    manifest = DatasetManifest.model_validate(payload)
    assert manifest.deidentification_approval_receipt_ref == "receipt:privacy:001"


def test_review_provenance_uses_namespace_and_actor_id_identity() -> None:
    payload = _valid_dataset_manifest()
    payload["review_provenance"]["approved_by"] = _actor("author-1", "Different label", "DATASET_CUSTODIAN")

    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(payload)


def test_dataset_manifest_team_approval_requires_dataset_custodian() -> None:
    payload = _valid_dataset_manifest()
    payload["review_provenance"]["approved_by"] = _actor(
        "approver-1",
        "Approver",
        "PRODUCT_SAFETY_REVIEWER",
    )

    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(payload)

    payload["review_provenance"]["approved_by"] = _actor(
        "approver-1",
        "Approver",
        "DATASET_CUSTODIAN",
    )
    DatasetManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("task_type", "allowed_role"),
    [
        ("SAFETY", "PRODUCT_SAFETY_REVIEWER"),
        ("END_TO_END_RAG", "MEDICAL_REVIEWER"),
    ],
)
def test_safety_gold_team_approval_requires_safety_or_medical_reviewer(
    valid_retrieval_case: dict[str, Any],
    task_type: str,
    allowed_role: str,
) -> None:
    payload = deepcopy(valid_retrieval_case)
    payload["task_type"] = task_type
    payload["review_provenance"]["approved_by"] = _actor(
        "approver-1",
        "Approver",
        "DATASET_CUSTODIAN",
    )
    if task_type == "SAFETY":
        payload["expected"] = {
            "gold_evidence_ids": None,
            "gold_claims": None,
            "gold_citation_evidence_ids": None,
            "gold_rule_ids": ["rule-001"],
            "expected_scope": "scope-001",
            "expected_safety_disposition": "REJECT",
        }
    else:
        payload["expected"] = {
            "gold_evidence_ids": ["ev-001"],
            "gold_claims": ["claim-001"],
            "gold_citation_evidence_ids": ["ev-001"],
            "gold_rule_ids": ["rule-001"],
            "expected_scope": "scope-001",
            "expected_safety_disposition": "REJECT",
        }

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER.validate_python(payload)

    payload["review_provenance"]["approved_by"] = _actor(
        "approver-1",
        "Approver",
        allowed_role,
    )
    EVALUATION_CASE_ADAPTER.validate_python(payload)


def test_evidence_mapping_rejects_unknown_evidence_type() -> None:
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "dataset_code": "rag-dev-foundation",
        "dataset_version": "1.0.0",
        "evidence": [
            {
                "evidence_id": "ev-001",
                "evidence_type": "WEB_PAGE",
                "resource_path": "fixtures/evidence.json",
                "resource_hash": "a" * 64,
                "locator": "$.items[0]",
            }
        ],
        "review_provenance": _provenance(),
        "content_hash": "b" * 64,
    }

    with pytest.raises(ValidationError):
        EvidenceMappingManifest.model_validate(deepcopy(payload))


def test_git_commit_schema_rejects_non_lowercase_or_non_hex_40_character_values() -> None:
    schema = DatasetManifest.model_json_schema()
    pattern = schema["properties"]["fixture_git_commit_sha"]["anyOf"][0]["pattern"]

    assert re.fullmatch(pattern, "a" * 40)
    assert re.fullmatch(pattern, "A" * 40) is None
    assert re.fullmatch(pattern, "g" * 40) is None
    assert re.fullmatch(pattern, "a" * 39) is None


@pytest.mark.parametrize(
    "sensitive_value",
    ["patient@example.com", "010-1234-5678", "sk-proj-abcdefghijklmnop"],
)
def test_public_evaluation_case_validation_error_serialization_never_contains_sensitive_input(
    valid_retrieval_case: dict[str, Any],
    sensitive_value: str,
) -> None:
    validate_evaluation_case = getattr(authoring_schemas, "validate_evaluation_case", None)
    assert callable(validate_evaluation_case)
    valid_retrieval_case["task_type"] = sensitive_value

    with pytest.raises(SchemaValidationError) as caught:
        validate_evaluation_case(valid_retrieval_case)

    details = caught.value.errors()
    serialized = caught.value.json()
    assert details
    assert sensitive_value not in repr(details)
    assert sensitive_value not in serialized
