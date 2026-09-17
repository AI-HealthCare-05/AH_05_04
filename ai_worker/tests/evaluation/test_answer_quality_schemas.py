from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.schemas.answer_quality_v1 import (
    ANS_BASE_TO_ANS_FINAL_DELTA_KEYS,
    ANS_BASE_TO_ANS_RAG_DELTA_KEYS,
    ANS_RAG_TO_ANS_FINAL_DELTA_KEYS,
    AnswerClaimCorrectnessLabel,
    AnswerComparisonSetManifest,
    AnswerHumanJudgmentApproval,
    AnswerHumanJudgmentArtifact,
    AnswerHumanJudgmentRecord,
    AnswerRelevanceLabel,
    AnswerVariantId,
    parse_answer_comparison_set_manifest_bytes,
    parse_answer_human_judgment_approval_bytes,
    parse_answer_human_judgment_bytes,
)
from ai_worker.tasks.evaluation.schemas.common import ActorNamespace, ActorRole, Partition

VALID_RUN_ID = "00000000-0000-4000-8000-000000000001"
VALID_BASELINE_RUN_ID = "00000000-0000-4000-8000-000000000002"
VALID_CANDIDATE_RUN_ID = "00000000-0000-4000-8000-000000000003"
VALID_TIMESTAMP = "2026-09-17T12:00:00.000000Z"
VALID_COMMIT_SHA = "a" * 40
VALID_HASH_A = "1" * 64
VALID_HASH_B = "2" * 64
VALID_HASH_C = "3" * 64
VALID_HASH_D = "4" * 64

ACTOR_REVIEWER: dict[str, Any] = {
    "namespace": ActorNamespace.GITHUB_LOGIN.value,
    "actor_id": "ceohwj",
    "role": ActorRole.EVALUATION_IMPLEMENTER.value,
}

ACTOR_APPROVER: dict[str, Any] = {
    "namespace": ActorNamespace.GITHUB_LOGIN.value,
    "actor_id": "hazelnutflavoured",
    "role": ActorRole.PRODUCT_SAFETY_REVIEWER.value,
}

RUBRIC_REF: dict[str, Any] = {
    "id": "critical-claim-rubric",
    "version": "1.0.0",
    "hash": VALID_HASH_A,
}

DATASET_REF: dict[str, Any] = {
    "id": "rag-dev-foundation",
    "version": "1.0.0",
    "hash": VALID_HASH_B,
}

GOLD_REF: dict[str, Any] = {
    "id": "rag-dev-foundation-gold",
    "version": "1.0.0",
    "hash": VALID_HASH_C,
}

METRIC_POLICY_REF: dict[str, Any] = {
    "id": "answer-quality-dev-policy",
    "version": "1.0.0",
    "hash": VALID_HASH_D,
}

APPROVAL_REF: dict[str, Any] = {
    "id": "answer-judgment-approval-dev",
    "version": "1.0.0",
    "hash": "5" * 64,
}


def _make_valid_record_payload(case_id: str = "case-001") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "judgment_id": f"judgment-{case_id}",
        "judgment_version": "1.0.0",
        "run_id": VALID_RUN_ID,
        "case_id": case_id,
        "answer_variant_id": AnswerVariantId.ANS_RAG.value,
        "input_sha256": VALID_HASH_A,
        "answer_sha256": VALID_HASH_B,
        "dataset_manifest_sha256": VALID_HASH_C,
        "critical_claim_rubric_ref": RUBRIC_REF,
        "claim_judgments": [
            {"claim_id": "claim-001", "label": AnswerClaimCorrectnessLabel.CORRECT.value},
            {"claim_id": "claim-002", "label": AnswerClaimCorrectnessLabel.INCORRECT.value},
        ],
        "relevance": AnswerRelevanceLabel.RELEVANT.value,
        "reviewer": ACTOR_REVIEWER,
        "reviewed_at": VALID_TIMESTAMP,
        "approval_evidence_ref": APPROVAL_REF,
    }
    payload["record_sha256"] = canonical_sha256(payload)
    return payload


def _make_valid_artifact_payload() -> dict[str, Any]:
    records = [
        _make_valid_record_payload("case-001"),
        _make_valid_record_payload("case-002"),
    ]
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.answer-human-judgment",
        "schema_version": "1.0.0",
        "judgment_set_id": "answer-human-judgment-dev",
        "judgment_set_version": "1.0.0",
        "run_id": VALID_RUN_ID,
        "answer_variant_id": AnswerVariantId.ANS_RAG.value,
        "answer_variant_manifest_hash": VALID_HASH_D,
        "dataset_manifest_sha256": VALID_HASH_C,
        "critical_claim_rubric_ref": RUBRIC_REF,
        "approval_evidence_ref": APPROVAL_REF,
        "records": records,
    }
    payload["artifact_sha256"] = canonical_sha256(payload)
    return payload


def _make_valid_approval_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.answer-human-judgment-approval",
        "schema_version": "1.0.0",
        "approval_id": "answer-human-judgment-approval-001",
        "approval_version": "1.0.0",
        "judgment_artifact_sha256": VALID_HASH_A,
        "approval_status": "APPROVED",
        "approved_by": ACTOR_APPROVER,
        "approved_at": VALID_TIMESTAMP,
    }
    payload["approval_sha256"] = canonical_sha256(payload)
    return payload


def _make_valid_pair_entry(
    pair_id: str,
    baseline_variant: str,
    candidate_variant: str,
    allowed_delta_keys: tuple[str, ...],
    relative_path: str,
) -> dict[str, Any]:
    return {
        "pair_id": pair_id,
        "baseline_variant": baseline_variant,
        "candidate_variant": candidate_variant,
        "baseline_run_id": VALID_BASELINE_RUN_ID,
        "candidate_run_id": VALID_CANDIDATE_RUN_ID,
        "baseline_answer_variant_manifest_hash": VALID_HASH_A,
        "candidate_answer_variant_manifest_hash": VALID_HASH_B,
        "baseline_runner_commit_sha": VALID_COMMIT_SHA,
        "candidate_runner_commit_sha": VALID_COMMIT_SHA,
        "relative_path": relative_path,
        "comparison_sha256": VALID_HASH_C,
        "comparison_semantic_hash": VALID_HASH_D,
        "allowed_delta_keys": list(allowed_delta_keys),
    }


def _make_valid_manifest_payload() -> dict[str, Any]:
    pairs = [
        _make_valid_pair_entry(
            "ANS-BASE--ANS-RAG",
            AnswerVariantId.ANS_BASE.value,
            AnswerVariantId.ANS_RAG.value,
            ANS_BASE_TO_ANS_RAG_DELTA_KEYS,
            "ans-base--ans-rag/comparison.json",
        ),
        _make_valid_pair_entry(
            "ANS-RAG--ANS-FINAL",
            AnswerVariantId.ANS_RAG.value,
            AnswerVariantId.ANS_FINAL.value,
            ANS_RAG_TO_ANS_FINAL_DELTA_KEYS,
            "ans-rag--ans-final/comparison.json",
        ),
        _make_valid_pair_entry(
            "ANS-BASE--ANS-FINAL",
            AnswerVariantId.ANS_BASE.value,
            AnswerVariantId.ANS_FINAL.value,
            ANS_BASE_TO_ANS_FINAL_DELTA_KEYS,
            "ans-base--ans-final/comparison.json",
        ),
    ]
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.answer-comparison-set-manifest",
        "schema_version": "1.0.0",
        "experiment_id": "answer-quality-dev-experiment",
        "dataset_manifest_ref": DATASET_REF,
        "partition": Partition.DEV.value,
        "gold_manifest_ref": GOLD_REF,
        "critical_claim_rubric_ref": RUBRIC_REF,
        "metric_policy_ref": METRIC_POLICY_REF,
        "pairs": pairs,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


# --- 1. AnswerVariantId Tests ---


def test_answer_variant_id_accepts_exactly_three_canonical_variants() -> None:
    assert AnswerVariantId.ANS_BASE == "ANS-BASE"
    assert AnswerVariantId.ANS_RAG == "ANS-RAG"
    assert AnswerVariantId.ANS_FINAL == "ANS-FINAL"
    assert tuple(variant.value for variant in AnswerVariantId) == (
        "ANS-BASE",
        "ANS-RAG",
        "ANS-FINAL",
    )


def test_answer_variant_id_rejects_unknown_variant() -> None:
    with pytest.raises(ValueError):
        AnswerVariantId("UNKNOWN-VARIANT")


# --- 2. AnswerHumanJudgmentRecord & Artifact Tests ---


def test_valid_answer_human_judgment_artifact_loads_successfully() -> None:
    payload = _make_valid_artifact_payload()
    artifact = AnswerHumanJudgmentArtifact.model_validate(payload)
    assert artifact.schema_id == "rag-eval.answer-human-judgment"
    assert len(artifact.records) == 2
    assert artifact.artifact_sha256 == payload["artifact_sha256"]

    # Byte parser round-trip
    parsed = parse_answer_human_judgment_bytes(canonical_json_bytes(payload))
    assert parsed.artifact_sha256 == artifact.artifact_sha256


def test_answer_human_judgment_record_rejects_self_hash_mismatch() -> None:
    payload = _make_valid_record_payload()
    payload["record_sha256"] = "f" * 64
    with pytest.raises((ValidationError, ValueError)):
        AnswerHumanJudgmentRecord.model_validate(payload)


def test_answer_human_judgment_artifact_rejects_self_hash_mismatch() -> None:
    payload = _make_valid_artifact_payload()
    payload["artifact_sha256"] = "f" * 64
    with pytest.raises((ValidationError, ValueError)):
        AnswerHumanJudgmentArtifact.model_validate(payload)

    with pytest.raises(EvaluationValidationError) as exc_info:
        parse_answer_human_judgment_bytes(canonical_json_bytes(payload))
    assert exc_info.value.code is EvaluationErrorCode.HASH_MISMATCH


def test_answer_human_judgment_artifact_rejects_duplicate_case_id() -> None:
    records = [
        _make_valid_record_payload("case-001"),
        _make_valid_record_payload("case-001"),
    ]
    payload = _make_valid_artifact_payload()
    payload["records"] = records
    payload["artifact_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"artifact_sha256"}))
    with pytest.raises((ValidationError, ValueError), match="case_id"):
        AnswerHumanJudgmentArtifact.model_validate(payload)


def test_answer_human_judgment_artifact_rejects_non_canonical_case_ordering() -> None:
    records = [
        _make_valid_record_payload("case-002"),
        _make_valid_record_payload("case-001"),
    ]
    payload = _make_valid_artifact_payload()
    payload["records"] = records
    payload["artifact_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"artifact_sha256"}))
    with pytest.raises((ValidationError, ValueError), match="canonical"):
        AnswerHumanJudgmentArtifact.model_validate(payload)


def test_answer_human_judgment_record_rejects_duplicate_claim_id() -> None:
    payload = _make_valid_record_payload()
    payload["claim_judgments"] = [
        {"claim_id": "claim-001", "label": "CORRECT"},
        {"claim_id": "claim-001", "label": "INCORRECT"},
    ]
    payload["record_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"record_sha256"}))
    with pytest.raises((ValidationError, ValueError), match="claim_id"):
        AnswerHumanJudgmentRecord.model_validate(payload)


def test_answer_human_judgment_record_rejects_non_canonical_claim_ordering() -> None:
    payload = _make_valid_record_payload()
    payload["claim_judgments"] = [
        {"claim_id": "claim-002", "label": "CORRECT"},
        {"claim_id": "claim-001", "label": "INCORRECT"},
    ]
    payload["record_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"record_sha256"}))
    with pytest.raises((ValidationError, ValueError), match="canonical"):
        AnswerHumanJudgmentRecord.model_validate(payload)


def test_answer_human_judgment_record_rejects_invalid_claim_label() -> None:
    payload = _make_valid_record_payload()
    payload["claim_judgments"] = [{"claim_id": "claim-001", "label": "MAYBE"}]
    with pytest.raises(ValidationError):
        AnswerHumanJudgmentRecord.model_validate(payload)


def test_answer_human_judgment_record_rejects_invalid_relevance_label() -> None:
    payload = _make_valid_record_payload()
    payload["relevance"] = "SOMEWHAT_RELEVANT"
    with pytest.raises(ValidationError):
        AnswerHumanJudgmentRecord.model_validate(payload)


def test_answer_human_judgment_record_forbids_extra_and_sensitive_fields() -> None:
    for forbidden_field in [
        "question",
        "question_text",
        "answer",
        "answer_text",
        "claim_text",
        "reasoning",
        "notes",
        "provider_payload",
        "patient_id",
        "comments",
    ]:
        payload = _make_valid_record_payload()
        payload[forbidden_field] = "sensitive_data"
        with pytest.raises(ValidationError):
            AnswerHumanJudgmentRecord.model_validate(payload)


def test_answer_human_judgment_artifact_rejects_record_variant_mismatch() -> None:
    record = _make_valid_record_payload("case-001")
    record["answer_variant_id"] = AnswerVariantId.ANS_BASE.value
    record["record_sha256"] = canonical_sha256(record, excluded_top_level_keys=frozenset({"record_sha256"}))
    payload = _make_valid_artifact_payload()
    payload["records"] = [record]
    payload["artifact_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"artifact_sha256"}))
    with pytest.raises((ValidationError, ValueError), match="answer_variant_id"):
        AnswerHumanJudgmentArtifact.model_validate(payload)


# --- 3. AnswerHumanJudgmentApproval Tests ---


def test_valid_answer_human_judgment_approval_loads_successfully() -> None:
    payload = _make_valid_approval_payload()
    approval = AnswerHumanJudgmentApproval.model_validate(payload)
    assert approval.schema_id == "rag-eval.answer-human-judgment-approval"
    assert approval.approval_status == "APPROVED"
    assert approval.approval_sha256 == payload["approval_sha256"]

    parsed = parse_answer_human_judgment_approval_bytes(canonical_json_bytes(payload))
    assert parsed.approval_sha256 == approval.approval_sha256


def test_answer_human_judgment_approval_rejects_self_hash_mismatch() -> None:
    payload = _make_valid_approval_payload()
    payload["approval_sha256"] = "f" * 64
    with pytest.raises((ValidationError, ValueError)):
        AnswerHumanJudgmentApproval.model_validate(payload)

    with pytest.raises(EvaluationValidationError) as exc_info:
        parse_answer_human_judgment_approval_bytes(canonical_json_bytes(payload))
    assert exc_info.value.code is EvaluationErrorCode.HASH_MISMATCH


def test_answer_human_judgment_approval_rejects_non_approved_state() -> None:
    for bad_status in ["REJECTED", "PENDING", "DRAFT", "REVIEWED"]:
        payload = _make_valid_approval_payload()
        payload["approval_status"] = bad_status
        payload["approval_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"approval_sha256"}))
        with pytest.raises(ValidationError):
            AnswerHumanJudgmentApproval.model_validate(payload)


def test_answer_human_judgment_approval_rejects_missing_approver() -> None:
    payload = _make_valid_approval_payload()
    del payload["approved_by"]
    with pytest.raises(ValidationError):
        AnswerHumanJudgmentApproval.model_validate(payload)


# --- 4. AnswerComparisonSetManifest Tests ---


def test_valid_answer_comparison_set_manifest_loads_successfully() -> None:
    payload = _make_valid_manifest_payload()
    manifest = AnswerComparisonSetManifest.model_validate(payload)
    assert manifest.schema_id == "rag-eval.answer-comparison-set-manifest"
    assert len(manifest.pairs) == 3
    assert manifest.manifest_sha256 == payload["manifest_sha256"]

    parsed = parse_answer_comparison_set_manifest_bytes(canonical_json_bytes(payload))
    assert parsed.manifest_sha256 == manifest.manifest_sha256


def test_answer_comparison_set_manifest_rejects_self_hash_mismatch() -> None:
    payload = _make_valid_manifest_payload()
    payload["manifest_sha256"] = "f" * 64
    with pytest.raises((ValidationError, ValueError)):
        AnswerComparisonSetManifest.model_validate(payload)

    with pytest.raises(EvaluationValidationError) as exc_info:
        parse_answer_comparison_set_manifest_bytes(canonical_json_bytes(payload))
    assert exc_info.value.code is EvaluationErrorCode.HASH_MISMATCH


def test_answer_comparison_set_manifest_rejects_missing_pair() -> None:
    payload = _make_valid_manifest_payload()
    payload["pairs"] = payload["pairs"][:2]  # Only 2 pairs
    payload["manifest_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"manifest_sha256"}))
    with pytest.raises((ValidationError, ValueError), match="pair"):
        AnswerComparisonSetManifest.model_validate(payload)


def test_answer_comparison_set_manifest_rejects_duplicate_pair() -> None:
    payload = _make_valid_manifest_payload()
    payload["pairs"] = [payload["pairs"][0], payload["pairs"][0], payload["pairs"][2]]
    payload["manifest_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"manifest_sha256"}))
    with pytest.raises((ValidationError, ValueError), match="pair"):
        AnswerComparisonSetManifest.model_validate(payload)


def test_answer_comparison_set_manifest_rejects_non_canonical_pair_order() -> None:
    payload = _make_valid_manifest_payload()
    payload["pairs"] = [payload["pairs"][1], payload["pairs"][0], payload["pairs"][2]]
    payload["manifest_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"manifest_sha256"}))
    with pytest.raises((ValidationError, ValueError), match="pair"):
        AnswerComparisonSetManifest.model_validate(payload)


def test_answer_comparison_set_manifest_rejects_wrong_variant_in_pair() -> None:
    payload = _make_valid_manifest_payload()
    payload["pairs"][0]["candidate_variant"] = AnswerVariantId.ANS_FINAL.value
    payload["manifest_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"manifest_sha256"}))
    with pytest.raises((ValidationError, ValueError), match="candidate_variant"):
        AnswerComparisonSetManifest.model_validate(payload)


def test_answer_comparison_set_manifest_rejects_wrong_allowed_delta_keys() -> None:
    payload = _make_valid_manifest_payload()
    # Add an illegal delta key
    payload["pairs"][0]["allowed_delta_keys"] = list(ANS_BASE_TO_ANS_RAG_DELTA_KEYS) + ["ILLEGAL_DELTA"]
    payload["manifest_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"manifest_sha256"}))
    with pytest.raises((ValidationError, ValueError), match="allowed_delta_keys"):
        AnswerComparisonSetManifest.model_validate(payload)


def test_answer_comparison_set_manifest_rejects_path_traversal_and_absolute_paths() -> None:
    for bad_path in [
        "../traversal/comparison.json",
        "/absolute/path/comparison.json",
        "nested/../../escape/comparison.json",
    ]:
        payload = _make_valid_manifest_payload()
        payload["pairs"][0]["relative_path"] = bad_path
        payload["manifest_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"manifest_sha256"}))
        with pytest.raises((ValidationError, ValueError, EvaluationValidationError)):
            AnswerComparisonSetManifest.model_validate(payload)
