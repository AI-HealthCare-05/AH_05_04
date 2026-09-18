from __future__ import annotations

import errno
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import EvaluationCaseContract, ValidatedDataset, safe_path_under_root
from ai_worker.tasks.evaluation.schemas.answer_quality_v1 import (
    AnswerClaimJudgment,
    AnswerHumanJudgmentApproval,
    AnswerHumanJudgmentArtifact,
    AnswerRelevanceLabel,
    AnswerVariantId,
    parse_answer_human_judgment_approval_bytes,
    parse_answer_human_judgment_bytes,
)
from ai_worker.tasks.evaluation.schemas.artifacts import CaseResult
from ai_worker.tasks.evaluation.schemas.common import ActorRef, ImmutableReference, Partition, TaskType


@dataclass(frozen=True, slots=True)
class ResolvedJudgmentApprovalEvidence:
    reference: ImmutableReference
    approved_by: ActorRef
    approved_at: str | None = None


@dataclass(frozen=True, slots=True)
class ValidatedCaseJudgment:
    case_id: str
    input_sha256: str
    answer_sha256: str
    relevance: AnswerRelevanceLabel
    claim_judgments: tuple[AnswerClaimJudgment, ...]


@dataclass(frozen=True, slots=True)
class ValidatedAnswerJudgments:
    run_id: str
    answer_variant_id: AnswerVariantId
    answer_variant_manifest_hash: str
    dataset_manifest_sha256: str
    critical_claim_rubric_ref: ImmutableReference
    approval_evidence_ref: ImmutableReference
    approval_artifact_sha256: str
    approved_by: ActorRef
    approved_at: str
    judgments_by_case: Mapping[str, ValidatedCaseJudgment]

    def validate_for_scope(
        self,
        scoped_cases: Sequence[EvaluationCaseContract],
        results_by_case: Mapping[str, CaseResult],
        expected_input_sha256_by_case: Mapping[str, str],
    ) -> bool:
        for case in scoped_cases:
            if case.case_id not in self.judgments_by_case:
                return False
            judgment = self.judgments_by_case[case.case_id]
            expected_input = expected_input_sha256_by_case.get(case.case_id, case.input_sha256)
            if judgment.input_sha256 != expected_input:
                return False
            result = results_by_case.get(case.case_id)
            if result is None or result.answer_sha256 is None or judgment.answer_sha256 != result.answer_sha256:
                return False
            actual_claims = tuple(result.actual_claim_ids or ())
            judged_claims = tuple(item.claim_id for item in judgment.claim_judgments)
            if len(judged_claims) != len(set(judged_claims)):
                return False
            if set(actual_claims) != set(judged_claims) or len(actual_claims) != len(judged_claims):
                return False
        return True


def _read_artifact_bytes(path: Path, evals_root: Path | None = None) -> bytes:
    resolved_root = Path(os.path.abspath(evals_root)) if evals_root is not None else Path(os.path.abspath(path.parent))
    safe_path = safe_path_under_root(resolved_root, Path(os.path.abspath(path)))
    try:
        return safe_path.read_bytes()
    except OSError as error:
        if error.errno == errno.ENOENT:
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING) from error
        if error.errno in {errno.ENOTDIR, errno.EISDIR, errno.EACCES, errno.EPERM, errno.ELOOP}:
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from error
        raise


def _validate_approval(
    judgment: AnswerHumanJudgmentArtifact,
    approval: AnswerHumanJudgmentApproval,
    resolved_approval_evidence: ResolvedJudgmentApprovalEvidence | None,
) -> None:
    if approval.approval_status != "APPROVED":
        raise EvaluationValidationError(EvaluationErrorCode.REVIEW_PROVENANCE_INVALID)

    if approval.judgment_artifact_sha256 != judgment.artifact_sha256:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)

    if resolved_approval_evidence is not None:
        if resolved_approval_evidence.reference.hash != judgment.approval_evidence_ref.hash:
            raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
        if (
            resolved_approval_evidence.reference.id != judgment.approval_evidence_ref.id
            or resolved_approval_evidence.reference.version != judgment.approval_evidence_ref.version
        ):
            raise EvaluationValidationError(EvaluationErrorCode.REVIEW_PROVENANCE_INVALID)


def _validate_manifest_bindings(
    judgment: AnswerHumanJudgmentArtifact,
    dataset: ValidatedDataset,
    expected_run_id: str,
    expected_variant_id: AnswerVariantId | str,
    expected_answer_variant_manifest_hash: str,
) -> None:
    if judgment.run_id != expected_run_id:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)

    try:
        expected_variant = (
            AnswerVariantId(expected_variant_id) if isinstance(expected_variant_id, str) else expected_variant_id
        )
    except ValueError as error:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID) from error

    if judgment.answer_variant_id != expected_variant:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)

    if judgment.answer_variant_manifest_hash != expected_answer_variant_manifest_hash:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)

    if judgment.dataset_manifest_sha256 != dataset.manifest.manifest_sha256:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)

    rubric_ref = judgment.critical_claim_rubric_ref
    if (
        rubric_ref.hash != dataset.rubric.rubric_hash
        or rubric_ref.id != dataset.rubric.rubric_id
        or rubric_ref.version != dataset.rubric.rubric_version
    ):
        raise EvaluationValidationError(EvaluationErrorCode.RUBRIC_MISMATCH)


def _validate_records_in_dataset(
    judgment: AnswerHumanJudgmentArtifact,
    dataset: ValidatedDataset,
) -> None:
    dataset_case_ids = {
        case.case_id
        for case in dataset.cases
        if case.task_type is TaskType.ANSWER_QUALITY and case.partition is Partition.DEV
    }
    for record in judgment.records:
        if record.case_id not in dataset_case_ids:
            raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)


def validate_answer_human_judgment(
    judgment: AnswerHumanJudgmentArtifact,
    approval: AnswerHumanJudgmentApproval,
    *,
    dataset: ValidatedDataset,
    expected_run_id: str,
    expected_variant_id: AnswerVariantId | str,
    expected_answer_variant_manifest_hash: str,
    resolved_approval_evidence: ResolvedJudgmentApprovalEvidence | None = None,
) -> ValidatedAnswerJudgments:
    _validate_approval(judgment, approval, resolved_approval_evidence)
    _validate_manifest_bindings(
        judgment,
        dataset,
        expected_run_id,
        expected_variant_id,
        expected_answer_variant_manifest_hash,
    )
    _validate_records_in_dataset(judgment, dataset)

    judgments_by_case = {
        record.case_id: ValidatedCaseJudgment(
            case_id=record.case_id,
            input_sha256=record.input_sha256,
            answer_sha256=record.answer_sha256,
            relevance=record.relevance,
            claim_judgments=record.claim_judgments,
        )
        for record in judgment.records
    }

    return ValidatedAnswerJudgments(
        run_id=judgment.run_id,
        answer_variant_id=judgment.answer_variant_id,
        answer_variant_manifest_hash=judgment.answer_variant_manifest_hash,
        dataset_manifest_sha256=judgment.dataset_manifest_sha256,
        critical_claim_rubric_ref=judgment.critical_claim_rubric_ref,
        approval_evidence_ref=judgment.approval_evidence_ref,
        approval_artifact_sha256=approval.approval_sha256,
        approved_by=approval.approved_by,
        approved_at=approval.approved_at,
        judgments_by_case=judgments_by_case,
    )


def load_answer_human_judgment(
    judgment_path: Path | str,
    approval_path: Path | str,
    *,
    dataset: ValidatedDataset,
    expected_run_id: str,
    expected_variant_id: AnswerVariantId | str,
    expected_answer_variant_manifest_hash: str,
    resolved_approval_evidence: ResolvedJudgmentApprovalEvidence | None = None,
    evals_root: Path | None = None,
    judgment_bytes: bytes | None = None,
    approval_bytes: bytes | None = None,
) -> ValidatedAnswerJudgments:
    if judgment_bytes is None:
        judgment_bytes = _read_artifact_bytes(Path(judgment_path), evals_root=evals_root)
    if approval_bytes is None:
        approval_bytes = _read_artifact_bytes(Path(approval_path), evals_root=evals_root)

    judgment = parse_answer_human_judgment_bytes(judgment_bytes)
    approval = parse_answer_human_judgment_approval_bytes(approval_bytes)

    return validate_answer_human_judgment(
        judgment,
        approval,
        dataset=dataset,
        expected_run_id=expected_run_id,
        expected_variant_id=expected_variant_id,
        expected_answer_variant_manifest_hash=expected_answer_variant_manifest_hash,
        resolved_approval_evidence=resolved_approval_evidence,
    )
