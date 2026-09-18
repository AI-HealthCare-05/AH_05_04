from pathlib import Path
from typing import Any

import pytest

from ai_worker.tasks.evaluation.answer_judgment import (
    ResolvedJudgmentApprovalEvidence,
    ValidatedAnswerJudgments,
    load_answer_human_judgment,
    validate_answer_human_judgment,
)
from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import load_dataset
from ai_worker.tasks.evaluation.schemas.answer_quality_v1 import (
    AnswerClaimCorrectnessLabel,
    AnswerRelevanceLabel,
    AnswerVariantId,
    parse_answer_human_judgment_approval_bytes,
    parse_answer_human_judgment_bytes,
)
from ai_worker.tasks.evaluation.schemas.common import (
    ActorNamespace,
    ActorRef,
    ActorRole,
    ImmutableReference,
)

EVALS_ROOT = Path(__file__).parents[3] / "evals"
DATASET = load_dataset(
    EVALS_ROOT / "retrieval/manifests/dev-foundation-v1.dataset.json",
    evals_root=EVALS_ROOT,
)

RUN_ID = "00000000-0000-4000-8000-000000000001"
VARIANT_ID = AnswerVariantId.ANS_RAG
VARIANT_MANIFEST_HASH = "1" * 64
TIMESTAMP = "2026-09-17T12:00:00.000000Z"

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

APPROVAL_EVIDENCE_REF: dict[str, Any] = {
    "id": "decision-approval-dev-task",
    "version": "1.0.0",
    "hash": "7" * 64,
}


def _dev_case_ids() -> list[str]:
    return [
        case.case_id
        for case in DATASET.cases
        if case.task_type.value == "ANSWER_QUALITY" and case.partition.value == "DEV"
    ]


def _rubric_ref() -> dict[str, Any]:
    return {
        "id": DATASET.rubric.rubric_id,
        "version": DATASET.rubric.rubric_version,
        "hash": DATASET.rubric.rubric_hash,
    }


def _make_record_payload(
    case_id: str,
    *,
    run_id: str = RUN_ID,
    variant_id: AnswerVariantId = VARIANT_ID,
    input_sha256: str = "2" * 64,
    answer_sha256: str = "3" * 64,
    dataset_manifest_hash: str = DATASET.manifest.manifest_sha256,
    rubric_ref: dict[str, Any] | None = None,
    approval_ref: dict[str, Any] = APPROVAL_EVIDENCE_REF,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "judgment_id": f"judgment-{case_id}",
        "judgment_version": "1.0.0",
        "run_id": run_id,
        "case_id": case_id,
        "answer_variant_id": variant_id.value,
        "input_sha256": input_sha256,
        "answer_sha256": answer_sha256,
        "dataset_manifest_sha256": dataset_manifest_hash,
        "critical_claim_rubric_ref": rubric_ref or _rubric_ref(),
        "claim_judgments": [
            {"claim_id": "claim-001", "label": AnswerClaimCorrectnessLabel.CORRECT.value},
            {"claim_id": "claim-002", "label": AnswerClaimCorrectnessLabel.INCORRECT.value},
        ],
        "relevance": AnswerRelevanceLabel.RELEVANT.value,
        "reviewer": ACTOR_REVIEWER,
        "reviewed_at": TIMESTAMP,
        "approval_evidence_ref": approval_ref,
    }
    payload["record_sha256"] = canonical_sha256(payload)
    return payload


def _make_judgment_artifact_payload(
    case_ids: list[str] | None = None,
    *,
    approval_ref: dict[str, Any] = APPROVAL_EVIDENCE_REF,
    run_id: str = RUN_ID,
    variant_id: AnswerVariantId = VARIANT_ID,
    variant_manifest_hash: str = VARIANT_MANIFEST_HASH,
    dataset_manifest_hash: str = DATASET.manifest.manifest_sha256,
    rubric_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if case_ids is None:
        case_ids = _dev_case_ids()
    if rubric_ref is None:
        rubric_ref = _rubric_ref()

    records = [
        _make_record_payload(
            cid,
            run_id=run_id,
            variant_id=variant_id,
            dataset_manifest_hash=dataset_manifest_hash,
            rubric_ref=rubric_ref,
            approval_ref=approval_ref,
        )
        for cid in sorted(case_ids)
    ]
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.answer-human-judgment",
        "schema_version": "1.0.0",
        "judgment_set_id": "answer-judgment-set-dev",
        "judgment_set_version": "1.0.0",
        "run_id": run_id,
        "answer_variant_id": variant_id.value,
        "answer_variant_manifest_hash": variant_manifest_hash,
        "dataset_manifest_sha256": dataset_manifest_hash,
        "critical_claim_rubric_ref": rubric_ref,
        "approval_evidence_ref": approval_ref,
        "records": records,
    }
    payload["artifact_sha256"] = canonical_sha256(payload)
    return payload


def _make_approval_payload(
    artifact_sha256: str,
    *,
    approval_id: str = "approval-001",
    approval_version: str = "1.0.0",
    approval_status: str = "APPROVED",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.answer-human-judgment-approval",
        "schema_version": "1.0.0",
        "approval_id": approval_id,
        "approval_version": approval_version,
        "judgment_artifact_sha256": artifact_sha256,
        "approval_status": approval_status,
        "approved_by": ACTOR_APPROVER,
        "approved_at": TIMESTAMP,
    }
    payload["approval_sha256"] = canonical_sha256(payload)
    return payload


def _resolved_evidence() -> ResolvedJudgmentApprovalEvidence:
    return ResolvedJudgmentApprovalEvidence(
        reference=ImmutableReference.model_validate(APPROVAL_EVIDENCE_REF),
        approved_by=ActorRef.model_validate(ACTOR_APPROVER),
        approved_at=TIMESTAMP,
    )


def test_valid_artifact_and_approval_accepted(tmp_path: Path) -> None:
    artifact_payload = _make_judgment_artifact_payload()
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"])

    artifact_bytes = canonical_json_bytes(artifact_payload)
    approval_bytes = canonical_json_bytes(approval_payload)

    artifact_file = tmp_path / "judgment.json"
    approval_file = tmp_path / "approval.json"
    artifact_file.write_bytes(artifact_bytes)
    approval_file.write_bytes(approval_bytes)

    resolved = _resolved_evidence()
    validated = load_answer_human_judgment(
        artifact_file,
        approval_file,
        evals_root=tmp_path,
        dataset=DATASET,
        expected_run_id=RUN_ID,
        expected_variant_id=VARIANT_ID,
        expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
        resolved_approval_evidence=resolved,
    )

    assert isinstance(validated, ValidatedAnswerJudgments)
    assert validated.run_id == RUN_ID
    assert validated.answer_variant_id == VARIANT_ID
    assert validated.approval_artifact_sha256 == approval_payload["approval_sha256"]
    assert len(validated.judgments_by_case) == len(_dev_case_ids())


def test_wrong_record_sha256_rejected() -> None:
    artifact_payload = _make_judgment_artifact_payload()
    artifact_payload["records"][0]["record_sha256"] = "0" * 64
    artifact_payload["artifact_sha256"] = canonical_sha256(artifact_payload)
    raw_bytes = canonical_json_bytes(artifact_payload)

    with pytest.raises(EvaluationValidationError) as exc_info:
        parse_answer_human_judgment_bytes(raw_bytes)
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_wrong_artifact_sha256_rejected() -> None:
    artifact_payload = _make_judgment_artifact_payload()
    artifact_payload["artifact_sha256"] = "0" * 64
    raw_bytes = canonical_json_bytes(artifact_payload)

    with pytest.raises(EvaluationValidationError) as exc_info:
        parse_answer_human_judgment_bytes(raw_bytes)
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_wrong_approval_sha256_rejected() -> None:
    artifact_payload = _make_judgment_artifact_payload()
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"])
    approval_payload["approval_sha256"] = "0" * 64
    raw_bytes = canonical_json_bytes(approval_payload)

    with pytest.raises(EvaluationValidationError) as exc_info:
        parse_answer_human_judgment_approval_bytes(raw_bytes)
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_approval_status_unsupported_rejected() -> None:
    artifact_payload = _make_judgment_artifact_payload()
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"], approval_status="REJECTED")
    raw_bytes = canonical_json_bytes(approval_payload)

    with pytest.raises(EvaluationValidationError) as exc_info:
        parse_answer_human_judgment_approval_bytes(raw_bytes)
    assert exc_info.value.code == EvaluationErrorCode.SCHEMA_INVALID


def test_approval_judgment_artifact_sha256_mismatch_rejected() -> None:
    artifact_payload = _make_judgment_artifact_payload()
    approval_payload = _make_approval_payload("0" * 64)

    judgment = parse_answer_human_judgment_bytes(canonical_json_bytes(artifact_payload))
    approval = parse_answer_human_judgment_approval_bytes(canonical_json_bytes(approval_payload))

    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_human_judgment(
            judgment,
            approval,
            dataset=DATASET,
            expected_run_id=RUN_ID,
            expected_variant_id=VARIANT_ID,
            expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
        )
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_resolved_approval_evidence_hash_mismatch_rejected() -> None:
    artifact_payload = _make_judgment_artifact_payload()
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"])

    judgment = parse_answer_human_judgment_bytes(canonical_json_bytes(artifact_payload))
    approval = parse_answer_human_judgment_approval_bytes(canonical_json_bytes(approval_payload))

    wrong_ref = ImmutableReference.model_validate({**APPROVAL_EVIDENCE_REF, "hash": "0" * 64})
    wrong_evidence = ResolvedJudgmentApprovalEvidence(
        reference=wrong_ref,
        approved_by=ActorRef.model_validate(ACTOR_APPROVER),
    )

    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_human_judgment(
            judgment,
            approval,
            dataset=DATASET,
            expected_run_id=RUN_ID,
            expected_variant_id=VARIANT_ID,
            expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
            resolved_approval_evidence=wrong_evidence,
        )
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_resolved_approval_evidence_id_mismatch_rejected() -> None:
    artifact_payload = _make_judgment_artifact_payload()
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"])

    judgment = parse_answer_human_judgment_bytes(canonical_json_bytes(artifact_payload))
    approval = parse_answer_human_judgment_approval_bytes(canonical_json_bytes(approval_payload))

    wrong_ref = ImmutableReference.model_validate({**APPROVAL_EVIDENCE_REF, "id": "wrong-approval-id"})
    wrong_evidence = ResolvedJudgmentApprovalEvidence(
        reference=wrong_ref,
        approved_by=ActorRef.model_validate(ACTOR_APPROVER),
    )

    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_human_judgment(
            judgment,
            approval,
            dataset=DATASET,
            expected_run_id=RUN_ID,
            expected_variant_id=VARIANT_ID,
            expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
            resolved_approval_evidence=wrong_evidence,
        )
    assert exc_info.value.code == EvaluationErrorCode.REVIEW_PROVENANCE_INVALID


def test_wrong_run_id_rejected() -> None:
    artifact_payload = _make_judgment_artifact_payload()
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"])

    judgment = parse_answer_human_judgment_bytes(canonical_json_bytes(artifact_payload))
    approval = parse_answer_human_judgment_approval_bytes(canonical_json_bytes(approval_payload))

    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_human_judgment(
            judgment,
            approval,
            dataset=DATASET,
            expected_run_id="00000000-0000-4000-8000-000000000099",
            expected_variant_id=VARIANT_ID,
            expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
        )
    assert exc_info.value.code == EvaluationErrorCode.MANIFEST_INVALID


def test_wrong_answer_variant_id_rejected() -> None:
    artifact_payload = _make_judgment_artifact_payload()
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"])

    judgment = parse_answer_human_judgment_bytes(canonical_json_bytes(artifact_payload))
    approval = parse_answer_human_judgment_approval_bytes(canonical_json_bytes(approval_payload))

    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_human_judgment(
            judgment,
            approval,
            dataset=DATASET,
            expected_run_id=RUN_ID,
            expected_variant_id=AnswerVariantId.ANS_BASE,
            expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
        )
    assert exc_info.value.code == EvaluationErrorCode.MANIFEST_INVALID


def test_wrong_answer_variant_manifest_hash_rejected() -> None:
    artifact_payload = _make_judgment_artifact_payload()
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"])

    judgment = parse_answer_human_judgment_bytes(canonical_json_bytes(artifact_payload))
    approval = parse_answer_human_judgment_approval_bytes(canonical_json_bytes(approval_payload))

    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_human_judgment(
            judgment,
            approval,
            dataset=DATASET,
            expected_run_id=RUN_ID,
            expected_variant_id=VARIANT_ID,
            expected_answer_variant_manifest_hash="0" * 64,
        )
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_wrong_dataset_manifest_hash_rejected() -> None:
    artifact_payload = _make_judgment_artifact_payload(dataset_manifest_hash="0" * 64)
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"])

    judgment = parse_answer_human_judgment_bytes(canonical_json_bytes(artifact_payload))
    approval = parse_answer_human_judgment_approval_bytes(canonical_json_bytes(approval_payload))

    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_human_judgment(
            judgment,
            approval,
            dataset=DATASET,
            expected_run_id=RUN_ID,
            expected_variant_id=VARIANT_ID,
            expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
        )
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_wrong_rubric_ref_rejected() -> None:
    wrong_rubric = {**_rubric_ref(), "hash": "0" * 64}
    artifact_payload = _make_judgment_artifact_payload(rubric_ref=wrong_rubric)
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"])

    judgment = parse_answer_human_judgment_bytes(canonical_json_bytes(artifact_payload))
    approval = parse_answer_human_judgment_approval_bytes(canonical_json_bytes(approval_payload))

    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_human_judgment(
            judgment,
            approval,
            dataset=DATASET,
            expected_run_id=RUN_ID,
            expected_variant_id=VARIANT_ID,
            expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
        )
    assert exc_info.value.code == EvaluationErrorCode.RUBRIC_MISMATCH


def test_unknown_dataset_case_rejected() -> None:
    artifact_payload = _make_judgment_artifact_payload(case_ids=["unknown-case-id-12345"])
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"])

    judgment = parse_answer_human_judgment_bytes(canonical_json_bytes(artifact_payload))
    approval = parse_answer_human_judgment_approval_bytes(canonical_json_bytes(approval_payload))

    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_human_judgment(
            judgment,
            approval,
            dataset=DATASET,
            expected_run_id=RUN_ID,
            expected_variant_id=VARIANT_ID,
            expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
        )
    assert exc_info.value.code == EvaluationErrorCode.SCHEMA_INVALID


def test_missing_file_raises_resource_missing(tmp_path: Path) -> None:
    missing_judgment = tmp_path / "missing_judgment.json"
    missing_approval = tmp_path / "missing_approval.json"

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_answer_human_judgment(
            missing_judgment,
            missing_approval,
            evals_root=tmp_path,
            dataset=DATASET,
            expected_run_id=RUN_ID,
            expected_variant_id=VARIANT_ID,
            expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
        )
    assert exc_info.value.code == EvaluationErrorCode.RESOURCE_MISSING


def test_load_with_normal_evals_root_directory_hierarchy_succeeds(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    judgments_dir = evals_root / "judgments"
    judgments_dir.mkdir(parents=True)

    artifact_payload = _make_judgment_artifact_payload()
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"])

    artifact_file = judgments_dir / "judgment.json"
    approval_file = judgments_dir / "approval.json"
    artifact_file.write_bytes(canonical_json_bytes(artifact_payload))
    approval_file.write_bytes(canonical_json_bytes(approval_payload))

    resolved = _resolved_evidence()
    validated = load_answer_human_judgment(
        artifact_file,
        approval_file,
        evals_root=evals_root,
        dataset=DATASET,
        expected_run_id=RUN_ID,
        expected_variant_id=VARIANT_ID,
        expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
        resolved_approval_evidence=resolved,
    )
    assert isinstance(validated, ValidatedAnswerJudgments)
    assert validated.run_id == RUN_ID


def test_loader_rejects_parent_escape(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    evals_root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    artifact_payload = _make_judgment_artifact_payload()
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"])

    artifact_file = outside_dir / "judgment.json"
    approval_file = outside_dir / "approval.json"
    artifact_file.write_bytes(canonical_json_bytes(artifact_payload))
    approval_file.write_bytes(canonical_json_bytes(approval_payload))

    escaped_judgment_path = evals_root / "../outside/judgment.json"
    escaped_approval_path = evals_root / "../outside/approval.json"

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_answer_human_judgment(
            escaped_judgment_path,
            escaped_approval_path,
            evals_root=evals_root,
            dataset=DATASET,
            expected_run_id=RUN_ID,
            expected_variant_id=VARIANT_ID,
            expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
        )
    assert exc_info.value.code == EvaluationErrorCode.RESOURCE_PATH_INVALID


def test_loader_rejects_child_symlink_escape(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    evals_root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    artifact_payload = _make_judgment_artifact_payload()
    approval_payload = _make_approval_payload(artifact_payload["artifact_sha256"])

    outside_judgment = outside_dir / "judgment.json"
    outside_approval = outside_dir / "approval.json"
    outside_judgment.write_bytes(canonical_json_bytes(artifact_payload))
    outside_approval.write_bytes(canonical_json_bytes(approval_payload))

    linked_dir = evals_root / "linked"
    try:
        linked_dir.symlink_to(outside_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported in environment")

    symlinked_judgment_path = linked_dir / "judgment.json"
    symlinked_approval_path = linked_dir / "approval.json"

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_answer_human_judgment(
            symlinked_judgment_path,
            symlinked_approval_path,
            evals_root=evals_root,
            dataset=DATASET,
            expected_run_id=RUN_ID,
            expected_variant_id=VARIANT_ID,
            expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
        )
    assert exc_info.value.code == EvaluationErrorCode.RESOURCE_PATH_INVALID


def test_loader_requires_evals_root_keyword_argument(tmp_path: Path) -> None:
    artifact_file = tmp_path / "judgment.json"
    approval_file = tmp_path / "approval.json"

    with pytest.raises(TypeError, match="missing.*required keyword-only argument: 'evals_root'"):
        load_answer_human_judgment(  # type: ignore[call-arg]
            artifact_file,
            approval_file,
            dataset=DATASET,
            expected_run_id=RUN_ID,
            expected_variant_id=VARIANT_ID,
            expected_answer_variant_manifest_hash=VARIANT_MANIFEST_HASH,
        )
