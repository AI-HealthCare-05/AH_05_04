from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.schemas.authoring_v1_3 import DatasetManifestV13
from ai_worker.tasks.evaluation.schemas.provenance_v1 import (
    AuthoringIdentityManifest,
    IndexBuildReceipt,
    StudySplitReceipt,
    parse_authoring_identity_manifest_bytes,
    parse_index_build_receipt_bytes,
    parse_study_split_receipt_bytes,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
Payload = dict[str, Any]
Parser = Callable[[bytes], BaseModel]


def _ref(identifier: str, *, hash_value: str = SHA_A) -> dict[str, str]:
    return {"id": identifier, "version": "1.0.0", "hash": hash_value}


def _draft_provenance() -> Payload:
    return {
        "authored_by": {
            "namespace": "GITHUB_LOGIN",
            "actor_id": "ceohwj",
            "role": "EVALUATION_IMPLEMENTER",
        },
        "reviewed_by": None,
        "approved_by": None,
        "authored_at": "2026-09-05T00:00:00.000000Z",
        "reviewed_at": None,
        "approved_at": None,
        "team_gold_status": "DRAFT",
        "external_medical_review_status": "NOT_REQUESTED",
        "external_medical_approval_receipt_ref": None,
        "evidence_review_refs": [],
    }


def _with_self_hash(payload: Payload, field: str) -> Payload:
    result = deepcopy(payload)
    result[field] = canonical_sha256(result, excluded_top_level_keys=frozenset({field}))
    return result


def _authoring_entry(order: int, suffix: str) -> Payload:
    return {
        "member_order": order,
        "case_id": f"case-{suffix}",
        "question_template_id": f"question-template-{suffix}",
        "source_segment_id": f"source-segment-{suffix}",
        "medication_family_id": f"medication-family-{suffix}",
        "transform_origin_id": f"transform-origin-{suffix}",
        "question_template_spec": f"question template {suffix}",
        "source_snapshot_ref": _ref(f"source-snapshot-{suffix}"),
        "source_locator": f"section-{suffix}",
        "source_chunk_sha256": SHA_B,
        "medication_family_fixture_id": f"medication-fixture-{suffix}",
        "base_intent_seed": f"base-intent-{suffix}",
        "transform_spec": f"transform specification {suffix}",
    }


def _authoring_payload() -> Payload:
    return _with_self_hash(
        {
            "schema_id": "rag-eval.authoring-identity-manifest",
            "schema_version": "1.0.0",
            "manifest_id": "rag-natural-language-retrieval-dev-authoring-identities",
            "manifest_version": "1.0.0",
            "dataset_code": "rag-natural-language-retrieval-dev",
            "dataset_version": "1.0.0",
            "canonicalization_spec_version": "1.0.0",
            "entries": [_authoring_entry(1, "001"), _authoring_entry(2, "002")],
            "manifest_sha256": SHA_A,
        },
        "manifest_sha256",
    )


def _bridge_entry(suffix: str) -> Payload:
    return {
        "evidence_ref_id": f"evidence-ref-{suffix}",
        "evidence_mapping_stable_key": f"mapping-key-{suffix}",
        "evidence_key": f"evidence-key-{suffix}",
        "knowledge_chunk_ref": f"knowledge-chunk-{suffix}",
        "source_locator": f"section-{suffix}",
        "source_version": "1.0.0",
        "content_sha256": SHA_B,
    }


def _index_receipt_payload() -> Payload:
    return _with_self_hash(
        {
            "schema_id": "rag-eval.index-build-receipt",
            "schema_version": "1.0.0",
            "receipt_id": "rag-natural-language-retrieval-dev-index-build",
            "receipt_version": "1.0.0",
            "dataset_ref": _ref("rag-natural-language-retrieval-dev"),
            "evidence_mapping_ref": _ref("rag-natural-language-retrieval-dev-evidence-mapping"),
            "source_snapshot_ref": _ref("rag-natural-language-retrieval-dev-source-snapshot"),
            "evidence_index_ref": _ref("rag-natural-language-retrieval-dev-evidence-index"),
            "build_config_ref": _ref("rag-natural-language-retrieval-dev-index-build-config"),
            "adapter_artifact_ref": _ref("knowledge-evidence-retrieval.actual.v1"),
            "canonicalization_spec_version": "1.0.0",
            "bridge_entries": [_bridge_entry("001"), _bridge_entry("002")],
            "built_at": "2026-09-05T00:00:00.000000Z",
            "built_by": _draft_provenance(),
            "receipt_sha256": SHA_A,
        },
        "receipt_sha256",
    )


def _axis_summary(axis: str) -> Payload:
    return {"axis": axis, "comparison_count": 40, "intersection_count": 0}


def _study_split_payload() -> Payload:
    return _with_self_hash(
        {
            "schema_id": "rag-eval.study-split-receipt",
            "schema_version": "1.0.0",
            "receipt_id": "rag-natural-language-retrieval-study-split",
            "receipt_version": "1.0.0",
            "dev_dataset_ref": _ref("rag-natural-language-retrieval-dev", hash_value=SHA_A),
            "holdout_dataset_ref": _ref("rag-natural-language-retrieval-holdout", hash_value=SHA_B),
            "dev_authoring_identity_manifest_ref": _ref("rag-natural-language-retrieval-dev-authoring"),
            "holdout_authoring_identity_manifest_ref": _ref(
                "rag-natural-language-retrieval-holdout-authoring", hash_value=SHA_B
            ),
            "evidence_index_ref": _ref("rag-natural-language-retrieval-study-index"),
            "evaluation_config_ref": _ref("rag-natural-language-retrieval-study-config"),
            "gold_schema_ref": _ref("rag-eval.retrieval-gold-schema"),
            "canonical_identity_hmac_algorithm_ref": _ref("canonical-identity-hmac-sha256"),
            "hmac_key_version": "evaluation-hmac-key-v1",
            "query_fingerprint_algorithm_ref": _ref("query-fingerprint"),
            "simple_substitution_fingerprint_algorithm_ref": _ref("simple-substitution-fingerprint"),
            "transform_fingerprint_algorithm_ref": _ref("transform-fingerprint"),
            "axis_summaries": [
                _axis_summary("question_template"),
                _axis_summary("source_segment"),
                _axis_summary("medication_family"),
                _axis_summary("transform_origin"),
            ],
            "authorization_receipt_ref": _ref("rag-natural-language-retrieval-authorization"),
            "recorded_at": "2026-09-05T00:00:00.000000Z",
            "recorded_by": _draft_provenance(),
            "receipt_sha256": SHA_A,
        },
        "receipt_sha256",
    )


def _dataset_manifest_payload() -> Payload:
    return {
        "schema_id": "rag-eval.dataset-manifest",
        "schema_version": "1.3.0",
        "dataset_code": "rag-natural-language-retrieval-dev",
        "dataset_version": "1.0.0",
        "scope": "SYNTHETIC_NATURAL_LANGUAGE_RETRIEVAL_DEV",
        "description": "Synthetic natural-language retrieval DEV Dataset",
        "data_classification": "SYNTHETIC",
        "deidentification_approval_receipt_ref": None,
        "critical_claim_rubric_ref": _ref("rag-eval-critical-claim-rubric"),
        "evidence_mapping_manifest_sha256": SHA_A,
        "evaluation_corpus_snapshot_ref": _ref("evaluation-corpus-snapshot"),
        "case_resources": [
            {
                "path": "retrieval/cases/rag-natural-language-retrieval-dev-v1/case-001.json",
                "sha256": SHA_B,
                "case_id": "case-001",
                "partition": "DEV",
            }
        ],
        "partition_counts": {"AUTHORING": 0, "DEV": 1, "HOLDOUT": 0, "SAFETY_REGRESSION": 0},
        "resource_set_hash": SHA_C,
        "fixture_git_commit_sha": "d" * 40,
        "protected_artifact_receipt_ref": None,
        "status": "DRAFT",
        "frozen_at": None,
        "review_provenance": _draft_provenance(),
        "manifest_sha256": SHA_A,
        "authoring_identity_manifest_ref": {
            "path": "retrieval/manifests/rag-natural-language-retrieval-dev-v1.authoring-identities.json",
            "sha256": SHA_B,
        },
    }


@pytest.mark.parametrize(
    ("payload_factory", "parser", "hash_field", "model_type"),
    [
        (_authoring_payload, parse_authoring_identity_manifest_bytes, "manifest_sha256", AuthoringIdentityManifest),
        (_index_receipt_payload, parse_index_build_receipt_bytes, "receipt_sha256", IndexBuildReceipt),
        (_study_split_payload, parse_study_split_receipt_bytes, "receipt_sha256", StudySplitReceipt),
    ],
)
def test_parsers_accept_valid_payloads_and_verify_self_hash(
    payload_factory: Callable[[], Payload],
    parser: Parser,
    hash_field: str,
    model_type: type[BaseModel],
) -> None:
    payload = payload_factory()
    parsed = parser(canonical_json_bytes(payload))

    assert isinstance(parsed, model_type)
    assert getattr(parsed, hash_field) == payload[hash_field]


@pytest.mark.parametrize(
    ("payload_factory", "parser", "hash_field"),
    [
        (_authoring_payload, parse_authoring_identity_manifest_bytes, "manifest_sha256"),
        (_index_receipt_payload, parse_index_build_receipt_bytes, "receipt_sha256"),
        (_study_split_payload, parse_study_split_receipt_bytes, "receipt_sha256"),
    ],
)
def test_parsers_reject_self_hash_mismatch(
    payload_factory: Callable[[], Payload],
    parser: Parser,
    hash_field: str,
) -> None:
    payload = payload_factory()
    payload[hash_field] = "0" * 64

    with pytest.raises(EvaluationValidationError) as caught:
        parser(canonical_json_bytes(payload))

    assert caught.value.code is EvaluationErrorCode.HASH_MISMATCH


@pytest.mark.parametrize(
    ("raw_bytes", "parser"),
    [
        (b'{"schema_id":"first","schema_id":"second"}', parse_authoring_identity_manifest_bytes),
        (b"{not-json", parse_index_build_receipt_bytes),
        (b"[]", parse_study_split_receipt_bytes),
    ],
)
def test_parsers_map_malformed_json_to_schema_invalid(raw_bytes: bytes, parser: Parser) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        parser(raw_bytes)

    assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID


@pytest.mark.parametrize(
    ("payload_factory", "parser", "field", "value"),
    [
        (_authoring_payload, parse_authoring_identity_manifest_bytes, "unknown", True),
        (_authoring_payload, parse_authoring_identity_manifest_bytes, "schema_id", "wrong"),
        (_authoring_payload, parse_authoring_identity_manifest_bytes, "schema_version", "9.9.9"),
        (_index_receipt_payload, parse_index_build_receipt_bytes, "unknown", True),
        (_index_receipt_payload, parse_index_build_receipt_bytes, "schema_id", "wrong"),
        (_study_split_payload, parse_study_split_receipt_bytes, "unknown", True),
        (_study_split_payload, parse_study_split_receipt_bytes, "schema_version", "9.9.9"),
    ],
)
def test_parsers_map_contract_shape_errors_to_schema_invalid(
    payload_factory: Callable[[], Payload],
    parser: Parser,
    field: str,
    value: object,
) -> None:
    payload = payload_factory()
    payload[field] = value
    hash_field = "manifest_sha256" if "manifest_sha256" in payload else "receipt_sha256"
    payload = _with_self_hash(payload, hash_field)

    with pytest.raises(EvaluationValidationError) as caught:
        parser(canonical_json_bytes(payload))

    assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID


@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "non_positive_order",
        "unsorted_order",
        "duplicate_order",
        "duplicate_case",
        "missing_leakage_axis",
        "empty_spec",
    ],
)
def test_authoring_manifest_rejects_invalid_order_identity_and_required_text(mutation: str) -> None:
    payload = _authoring_payload()
    entries: list[Payload] = payload["entries"]
    if mutation == "empty":
        entries.clear()
    elif mutation == "non_positive_order":
        entries[0]["member_order"] = 0
    elif mutation == "unsorted_order":
        entries.reverse()
    elif mutation == "duplicate_order":
        entries[1]["member_order"] = entries[0]["member_order"]
    elif mutation == "duplicate_case":
        entries[1]["case_id"] = entries[0]["case_id"]
    elif mutation == "missing_leakage_axis":
        del entries[0]["transform_origin_id"]
    else:
        entries[0]["transform_spec"] = ""
    payload = _with_self_hash(payload, "manifest_sha256")

    with pytest.raises(EvaluationValidationError) as caught:
        parse_authoring_identity_manifest_bytes(canonical_json_bytes(payload))

    assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID


@pytest.mark.parametrize("unique_field", ["evidence_ref_id", "evidence_key", "knowledge_chunk_ref"])
def test_index_receipt_rejects_duplicate_bridge_identity(unique_field: str) -> None:
    payload = _index_receipt_payload()
    entries: list[Payload] = payload["bridge_entries"]
    entries[1][unique_field] = entries[0][unique_field]
    payload = _with_self_hash(payload, "receipt_sha256")

    with pytest.raises(EvaluationValidationError) as caught:
        parse_index_build_receipt_bytes(canonical_json_bytes(payload))

    assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID


def test_index_receipt_rejects_empty_or_unsorted_bridge_entries() -> None:
    for entries in ([], list(reversed(_index_receipt_payload()["bridge_entries"]))):
        payload = _index_receipt_payload()
        payload["bridge_entries"] = entries
        payload = _with_self_hash(payload, "receipt_sha256")

        with pytest.raises(EvaluationValidationError) as caught:
            parse_index_build_receipt_bytes(canonical_json_bytes(payload))

        assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID


@pytest.mark.parametrize(
    "mutation",
    [
        "same_dataset",
        "same_authoring_manifest",
        "missing_axis",
        "duplicate_axis",
        "unsorted_axes",
        "zero_comparisons",
        "nonzero_intersection",
    ],
)
def test_study_split_rejects_invalid_partition_or_axis_summary(mutation: str) -> None:
    payload = _study_split_payload()
    summaries: list[Payload] = payload["axis_summaries"]
    if mutation == "same_dataset":
        payload["holdout_dataset_ref"] = deepcopy(payload["dev_dataset_ref"])
    elif mutation == "same_authoring_manifest":
        payload["holdout_authoring_identity_manifest_ref"] = deepcopy(payload["dev_authoring_identity_manifest_ref"])
    elif mutation == "missing_axis":
        summaries.pop()
    elif mutation == "duplicate_axis":
        summaries[-1]["axis"] = summaries[0]["axis"]
    elif mutation == "unsorted_axes":
        summaries[0], summaries[1] = summaries[1], summaries[0]
    elif mutation == "zero_comparisons":
        summaries[0]["comparison_count"] = 0
    else:
        summaries[0]["intersection_count"] = 1
    payload = _with_self_hash(payload, "receipt_sha256")

    with pytest.raises(EvaluationValidationError) as caught:
        parse_study_split_receipt_bytes(canonical_json_bytes(payload))

    assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID


def test_study_split_receipt_preserves_gold_and_fingerprint_algorithm_bindings() -> None:
    parsed = parse_study_split_receipt_bytes(canonical_json_bytes(_study_split_payload()))

    assert parsed.gold_schema_ref.id == "rag-eval.retrieval-gold-schema"
    assert parsed.canonical_identity_hmac_algorithm_ref.id == "canonical-identity-hmac-sha256"
    assert parsed.hmac_key_version == "evaluation-hmac-key-v1"
    assert parsed.query_fingerprint_algorithm_ref.id == "query-fingerprint"
    assert parsed.simple_substitution_fingerprint_algorithm_ref.id == "simple-substitution-fingerprint"
    assert parsed.transform_fingerprint_algorithm_ref.id == "transform-fingerprint"


@pytest.mark.parametrize(
    "required_field",
    [
        "gold_schema_ref",
        "canonical_identity_hmac_algorithm_ref",
        "hmac_key_version",
        "query_fingerprint_algorithm_ref",
        "simple_substitution_fingerprint_algorithm_ref",
        "transform_fingerprint_algorithm_ref",
    ],
)
def test_study_split_receipt_rejects_missing_gold_or_fingerprint_algorithm_binding(required_field: str) -> None:
    payload = _study_split_payload()
    del payload[required_field]
    payload = _with_self_hash(payload, "receipt_sha256")

    with pytest.raises(EvaluationValidationError) as caught:
        parse_study_split_receipt_bytes(canonical_json_bytes(payload))

    assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID


@pytest.mark.parametrize("forbidden_field", ["question", "gold", "fingerprint_value", "hmac_value", "protected_path"])
def test_study_split_receipt_rejects_protected_content_fields(forbidden_field: str) -> None:
    payload = _study_split_payload()
    payload[forbidden_field] = "must-not-be-exposed"
    payload = _with_self_hash(payload, "receipt_sha256")

    with pytest.raises(EvaluationValidationError) as caught:
        parse_study_split_receipt_bytes(canonical_json_bytes(payload))

    assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID


def test_dataset_manifest_v13_requires_authoring_identity_ref_and_preserves_v12_validation() -> None:
    payload = _dataset_manifest_payload()
    assert DatasetManifestV13.model_validate(payload).authoring_identity_manifest_ref.sha256 == SHA_B

    missing_ref = deepcopy(payload)
    del missing_ref["authoring_identity_manifest_ref"]
    with pytest.raises(ValidationError):
        DatasetManifestV13.model_validate(missing_ref)

    invalid_v12_provenance = deepcopy(payload)
    invalid_v12_provenance["fixture_git_commit_sha"] = None
    with pytest.raises(ValidationError, match="exactly one dataset source provenance is required"):
        DatasetManifestV13.model_validate(invalid_v12_provenance)
