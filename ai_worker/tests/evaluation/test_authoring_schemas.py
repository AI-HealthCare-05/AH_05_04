from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ai_worker.tasks.evaluation.schemas import authoring as authoring_schemas
from ai_worker.tasks.evaluation.schemas.authoring import (
    EVALUATION_CASE_ADAPTER,
    CriticalClaimRubric,
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
        "authored_by": _actor("ceohwj", "Author"),
        "reviewed_by": _actor("hazelnutflavoured", "Reviewer", "PRIVACY_REVIEWER"),
        "approved_by": None,
        "authored_at": "2026-09-01T00:00:00.000000Z",
        "reviewed_at": "2026-09-01T00:00:00.000000Z",
        "approved_at": None,
        "team_gold_status": "REVIEWED",
        "external_medical_review_status": "NOT_REQUESTED",
        "external_medical_approval_receipt_ref": None,
        "evidence_review_refs": [],
    }


@pytest.fixture
def valid_retrieval_case() -> dict[str, Any]:
    return _fixture_json("retrieval/cases/dev-foundation-v1/rag-dev-retrieval-001.json")


EVALS_ROOT = Path(__file__).parents[3] / "evals"


def _fixture_json(relative_path: str) -> dict[str, Any]:
    return json.loads((EVALS_ROOT / relative_path).read_text(encoding="utf-8"))


def test_outer_task_type_discriminator_selects_retrieval_expected_model(
    valid_retrieval_case: dict[str, Any],
) -> None:
    case = EVALUATION_CASE_ADAPTER.validate_python(valid_retrieval_case)

    assert case.task_type.value == "RETRIEVAL"
    assert case.expected.relevant_evidence_refs == ("ev-synthetic-chunk-001",)


def test_retrieval_case_rejects_answer_gold_fields(valid_retrieval_case: dict[str, Any]) -> None:
    valid_retrieval_case["expected"]["gold_claims"] = []

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER.validate_python(valid_retrieval_case)


@pytest.mark.parametrize(
    ("claim_id", "evidence_ref_id"),
    [
        ("SYNTHETIC_CLAIM_MISSING", "ev-synthetic-chunk-001"),
        ("SYNTHETIC_CLAIM_ANSWER_GROUNDING", "ev-synthetic-guideline-001"),
    ],
)
def test_answer_gold_rejects_citation_outside_claim_support(
    claim_id: str,
    evidence_ref_id: str,
) -> None:
    payload = _fixture_json("retrieval/cases/dev-foundation-v1/rag-dev-answer-grounding-001.json")
    citation = payload["expected"]["expected_citations"][0]
    citation["claim_id"] = claim_id
    citation["evidence_ref_id"] = evidence_ref_id

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER.validate_python(payload)


def test_retrieval_case_requires_non_applicable_fields_as_explicit_null(
    valid_retrieval_case: dict[str, Any],
) -> None:
    del valid_retrieval_case["expected"]["expected_scope_codes"]

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
    return _fixture_json("retrieval/manifests/dev-foundation-v1.dataset.json")


@pytest.mark.parametrize(
    ("fixture_sha", "protected_receipt"),
    [(None, None), ("b" * 40, {"id": "SYNTHETIC_RECEIPT", "version": "1.0.0", "hash": "a" * 64})],
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
    payload["data_classification"] = "APPROVED_DEIDENTIFIED"
    payload["fixture_git_commit_sha"] = None
    payload["protected_artifact_receipt_ref"] = {
        "id": "SYNTHETIC_RECEIPT",
        "version": "1.0.0",
        "hash": "a" * 64,
    }

    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(payload)

    payload["deidentification_approval_receipt_ref"] = {
        "id": "SYNTHETIC_PRIVACY_RECEIPT",
        "version": "1.0.0",
        "hash": "b" * 64,
    }
    manifest = DatasetManifest.model_validate(payload)
    assert manifest.deidentification_approval_receipt_ref is not None


def test_review_provenance_uses_namespace_and_actor_id_identity() -> None:
    payload = _valid_dataset_manifest()
    payload["review_provenance"]["approved_by"] = _actor("ceohwj", "Different label", "DATASET_CUSTODIAN")
    payload["review_provenance"]["approved_at"] = "2026-09-01T00:02:00.000000Z"
    payload["review_provenance"]["team_gold_status"] = "APPROVED"

    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(payload)


def test_dataset_manifest_team_approval_requires_dataset_custodian() -> None:
    payload = _valid_dataset_manifest()
    payload["review_provenance"]["approved_by"] = _actor(
        "SYNTHETIC_APPROVER",
        "Approver",
        "PRODUCT_SAFETY_REVIEWER",
    )
    payload["review_provenance"]["approved_by"]["namespace"] = "EXTERNAL_APPROVAL_REGISTRY"
    payload["review_provenance"]["approved_at"] = "2026-09-01T00:02:00.000000Z"
    payload["review_provenance"]["team_gold_status"] = "APPROVED"

    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(payload)

    payload["review_provenance"]["approved_by"] = _actor(
        "SYNTHETIC_APPROVER",
        "Approver",
        "DATASET_CUSTODIAN",
    )
    payload["review_provenance"]["approved_by"]["namespace"] = "EXTERNAL_APPROVAL_REGISTRY"
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
    del valid_retrieval_case
    case_id = "rag-dev-safety-001" if task_type == "SAFETY" else "rag-dev-end-to-end-001"
    payload = _fixture_json(f"retrieval/cases/dev-foundation-v1/{case_id}.json")
    payload["review_provenance"]["approved_by"] = _actor(
        "SYNTHETIC_APPROVER",
        "Approver",
        "DATASET_CUSTODIAN",
    )
    payload["review_provenance"]["approved_by"]["namespace"] = "EXTERNAL_APPROVAL_REGISTRY"
    payload["review_provenance"]["approved_at"] = "2026-09-01T00:02:00.000000Z"
    payload["review_provenance"]["team_gold_status"] = "APPROVED"

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER.validate_python(payload)

    payload["review_provenance"]["approved_by"] = _actor(
        "SYNTHETIC_APPROVER",
        "Approver",
        allowed_role,
    )
    payload["review_provenance"]["approved_by"]["namespace"] = "EXTERNAL_APPROVAL_REGISTRY"
    EVALUATION_CASE_ADAPTER.validate_python(payload)


def test_evidence_mapping_rejects_unknown_evidence_type() -> None:
    payload = _fixture_json("retrieval/evidence/dev-foundation-v1.evidence-mapping.json")
    payload["entries"][0]["evidence_type"] = "WEB_PAGE"

    with pytest.raises(ValidationError):
        EvidenceMappingManifest.model_validate(deepcopy(payload))


@pytest.mark.parametrize("collection", ["classification_rules", "reason_code_catalog"])
def test_critical_claim_rubric_rejects_duplicate_logical_ids(
    collection: str,
) -> None:
    payload = _fixture_json("retrieval/manifests/dev-foundation-v1.critical-claim-rubric.json")
    duplicate = deepcopy(payload[collection][0])
    duplicate["member_order"] = 2
    payload[collection].append(duplicate)

    with pytest.raises(ValidationError):
        CriticalClaimRubric.model_validate(payload)


@pytest.mark.parametrize("target_kind", ["RUNTIME_REFERENCE", "FIXTURE", "UNKNOWN"])
def test_evidence_mapping_rejects_unapproved_target_kind(target_kind: str) -> None:
    payload = _fixture_json("retrieval/evidence/dev-foundation-v1.evidence-mapping.json")
    payload["entries"][0]["target_kind"] = target_kind

    with pytest.raises(ValidationError):
        EvidenceMappingManifest.model_validate(payload)


def test_evidence_mapping_binds_target_kind_to_exactly_one_matching_branch() -> None:
    payload = _fixture_json("retrieval/evidence/dev-foundation-v1.evidence-mapping.json")
    payload["entries"][0]["target_kind"] = "RUNTIME_TYPED_REF"

    with pytest.raises(ValidationError):
        EvidenceMappingManifest.model_validate(payload)


def test_evidence_mapping_rejects_duplicate_ids_and_stable_tuples_independently() -> None:
    payload = _fixture_json("retrieval/evidence/dev-foundation-v1.evidence-mapping.json")
    duplicate_id = deepcopy(payload)
    duplicate_id["entries"][1]["evidence_ref_id"] = duplicate_id["entries"][0]["evidence_ref_id"]
    with pytest.raises(ValidationError):
        EvidenceMappingManifest.model_validate(duplicate_id)

    duplicate_tuple = deepcopy(payload)
    for field in ("evidence_type", "stable_key", "source_version", "locator"):
        duplicate_tuple["entries"][1][field] = duplicate_tuple["entries"][0][field]
    with pytest.raises(ValidationError):
        EvidenceMappingManifest.model_validate(duplicate_tuple)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("expected_response_level", "LOW"),
        ("expected_safety_disposition", "SAFE"),
        ("expected_fallback_code", "UNAPPROVED_FALLBACK"),
        ("risk_level", "LOW"),
    ],
)
def test_safety_expected_rejects_values_outside_authoritative_track_f_enums(
    field: str,
    invalid_value: str,
) -> None:
    payload = _fixture_json("retrieval/cases/dev-foundation-v1/rag-dev-safety-001.json")
    payload["expected"][field] = invalid_value

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER.validate_python(payload)


def test_claim_criticality_is_exactly_critical_or_non_critical() -> None:
    payload = _fixture_json("retrieval/cases/dev-foundation-v1/rag-dev-answer-quality-001.json")
    payload["expected"]["gold_claims"][0]["criticality"] = "IMPORTANT"

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER.validate_python(payload)


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
