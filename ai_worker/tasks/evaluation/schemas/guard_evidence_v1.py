"""Standalone typed Guard Evidence schemas for #162 Phase A2.

These models strictly materialize the frozen contract from PR #868
(`docs/contracts/proposed/post-mvp-1/evaluation-guard-evidence-v1.md`).
They are standalone authority artifacts and are NOT registered into
`schema_registry.py` (no Schema Set 1.6).
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator

from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.schemas.common import (
    CanonicalUuid,
    Partition,
    SemanticVersion,
    Sha256Hex,
    StableId,
    StrictContractModel,
)
from rag_runtime.request_guard_runtime_binding import canonical_scope_manifest_hash

EVALUATION_CANDIDATE_OPERATION: Literal["EVALUATION_CANDIDATE"] = "EVALUATION_CANDIDATE"
EVALUATION_REQUEST_OPERATION: Literal["EVALUATION_REQUEST"] = "EVALUATION_REQUEST"
EVALUATION_GUARD_PASS_DECISION: Literal["PASS"] = "PASS"

EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE: Literal["evaluation_candidate_guard"] = "evaluation_candidate_guard"
EVALUATION_REQUEST_GUARD_ARTIFACT_CODE: Literal["evaluation_request_guard"] = "evaluation_request_guard"
EVALUATION_GUARD_ARTIFACT_VERSION: Literal["1.0"] = "1.0"

EVALUATION_GUARD_EVIDENCE_SCHEMA_ID: Literal["rag-eval.evaluation-guard-evidence"] = (
    "rag-eval.evaluation-guard-evidence"
)
EVALUATION_GUARD_EVIDENCE_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"

PROJECTION_VERSION_REQUIRED_CASE_SET: Literal["evaluation-required-case-set-v1"] = "evaluation-required-case-set-v1"
PROJECTION_VERSION_CANDIDATE_GUARD: Literal["evaluation-candidate-guard-v1"] = "evaluation-candidate-guard-v1"
PROJECTION_VERSION_CASE_GUARD: Literal["evaluation-request-guard-v1"] = "evaluation-request-guard-v1"
PROJECTION_VERSION_GUARD_COVERAGE: Literal["evaluation-guard-coverage-v1"] = "evaluation-guard-coverage-v1"


def _tuple_from_wire(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


def _enum_from_wire(enum_type: type[StrEnum], value: object) -> object:
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            return value
    return value


PartitionValue = Annotated[Partition, BeforeValidator(lambda value: _enum_from_wire(Partition, value))]


def _validate_nonblank_nfc(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} cannot be empty or have surrounding whitespace")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{field_name} must be in Unicode NFC format")
    return value


class EvaluationGuardArtifactRef(StrictContractModel):
    """Immutable reference to an evaluation guard artifact."""

    artifact_code: StableId
    version: Literal["1.0"]
    content_sha256: Sha256Hex


class CandidateSourceGovernanceObservation(StrictContractModel):
    """Pure observation representing Source Governance authority for a candidate."""

    decision: Literal["PASS", "FAIL", "UNAVAILABLE"]
    failure_reasons: Annotated[tuple[str, ...], BeforeValidator(_tuple_from_wire)] = ()


class EvaluationCandidateGuard(StrictContractModel):
    """Run-level Candidate Guard decision artifact."""

    candidate_guard_decision_id: CanonicalUuid
    operation: Literal["EVALUATION_CANDIDATE"]
    decision: Literal["PASS"]

    evaluation_run_id: CanonicalUuid

    environment: Literal["LOCAL"]
    environment_revision_fence: Annotated[int, Field(ge=1)]
    governance_revision_ref: str
    safety_epoch: Annotated[int, Field(ge=1)]

    candidate_bundle_id: CanonicalUuid
    candidate_bundle_manifest_hash: Sha256Hex

    runtime_execution_manifest_id: CanonicalUuid
    runtime_execution_manifest_hash: Sha256Hex

    dataset_code: StableId
    dataset_version: SemanticVersion
    dataset_manifest_sha256: Sha256Hex

    required_partitions: Annotated[tuple[PartitionValue, ...], BeforeValidator(_tuple_from_wire)]
    required_case_set_hash: Sha256Hex

    candidate_guard_ref: EvaluationGuardArtifactRef

    @model_validator(mode="after")
    def validate_candidate_guard(self) -> EvaluationCandidateGuard:
        _validate_nonblank_nfc(self.governance_revision_ref, "governance_revision_ref")

        if self.candidate_guard_ref.artifact_code != EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE:
            raise ValueError(f"candidate_guard_ref artifact_code must be {EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE}")
        if self.candidate_guard_ref.version != EVALUATION_GUARD_ARTIFACT_VERSION:
            raise ValueError(f"candidate_guard_ref version must be {EVALUATION_GUARD_ARTIFACT_VERSION}")

        if not self.required_partitions:
            raise ValueError("required_partitions must not be empty")
        if len(self.required_partitions) != len(set(self.required_partitions)):
            raise ValueError("required_partitions must not contain duplicates")
        sorted_partitions = tuple(sorted(self.required_partitions, key=lambda p: p.value.encode("utf-16-be")))
        if self.required_partitions != sorted_partitions:
            raise ValueError("required_partitions must be sorted in UTF-16 BE order")

        return self


class EvaluationCaseGuardBinding(StrictContractModel):
    """Case-level Request Guard decision and binding artifact."""

    case_guard_decision_id: CanonicalUuid
    operation: Literal["EVALUATION_REQUEST"]
    decision: Literal["PASS"]

    evaluation_run_id: CanonicalUuid
    case_id: StableId

    candidate_guard_ref: EvaluationGuardArtifactRef

    environment: Literal["LOCAL"]
    environment_revision_fence: Annotated[int, Field(ge=1)]
    governance_revision_ref: str
    safety_epoch: Annotated[int, Field(ge=1)]

    candidate_bundle_id: CanonicalUuid
    candidate_bundle_manifest_hash: Sha256Hex

    runtime_execution_manifest_id: CanonicalUuid
    runtime_execution_manifest_hash: Sha256Hex

    required_case_set_hash: Sha256Hex

    request_scope_codes: Annotated[tuple[str, ...], BeforeValidator(_tuple_from_wire)]
    scope_manifest_hash: Sha256Hex

    case_guard_ref: EvaluationGuardArtifactRef

    @model_validator(mode="after")
    def validate_case_guard(self) -> EvaluationCaseGuardBinding:
        _validate_nonblank_nfc(self.governance_revision_ref, "governance_revision_ref")

        if self.candidate_guard_ref.artifact_code != EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE:
            raise ValueError(f"candidate_guard_ref artifact_code must be {EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE}")
        if self.candidate_guard_ref.version != EVALUATION_GUARD_ARTIFACT_VERSION:
            raise ValueError(f"candidate_guard_ref version must be {EVALUATION_GUARD_ARTIFACT_VERSION}")

        if self.case_guard_ref.artifact_code != EVALUATION_REQUEST_GUARD_ARTIFACT_CODE:
            raise ValueError(f"case_guard_ref artifact_code must be {EVALUATION_REQUEST_GUARD_ARTIFACT_CODE}")
        if self.case_guard_ref.version != EVALUATION_GUARD_ARTIFACT_VERSION:
            raise ValueError(f"case_guard_ref version must be {EVALUATION_GUARD_ARTIFACT_VERSION}")

        try:
            expected_scope_hash = canonical_scope_manifest_hash(self.request_scope_codes)
        except Exception as error:
            raise ValueError("request_scope_codes validation failed") from error

        if self.scope_manifest_hash != expected_scope_hash:
            raise ValueError("scope_manifest_hash must match canonical_scope_manifest_hash")

        return self


class EvaluationGuardEvidence(StrictContractModel):
    """Aggregate flat evaluation guard evidence artifact."""

    schema_id: Literal["rag-eval.evaluation-guard-evidence"] = EVALUATION_GUARD_EVIDENCE_SCHEMA_ID
    schema_version: Literal["1.0.0"] = EVALUATION_GUARD_EVIDENCE_SCHEMA_VERSION

    evaluation_run_id: CanonicalUuid
    candidate_bundle_id: CanonicalUuid
    candidate_bundle_manifest_hash: Sha256Hex

    dataset_code: StableId
    dataset_version: SemanticVersion
    dataset_manifest_sha256: Sha256Hex

    required_partitions: Annotated[tuple[PartitionValue, ...], BeforeValidator(_tuple_from_wire)]
    required_case_set_hash: Sha256Hex

    environment: Literal["LOCAL"]
    environment_revision_fence: Annotated[int, Field(ge=1)]
    governance_revision_ref: str
    safety_epoch: Annotated[int, Field(ge=1)]

    candidate_guard_decision_id: CanonicalUuid
    candidate_guard_decision: Literal["PASS"] = EVALUATION_GUARD_PASS_DECISION
    candidate_guard_ref: EvaluationGuardArtifactRef

    runtime_execution_manifest_id: CanonicalUuid
    runtime_execution_manifest_hash: Sha256Hex

    case_guard_bindings: Annotated[tuple[EvaluationCaseGuardBinding, ...], BeforeValidator(_tuple_from_wire)]
    guard_coverage_manifest_hash: Sha256Hex

    @model_validator(mode="after")
    def validate_evidence(self) -> EvaluationGuardEvidence:
        _validate_nonblank_nfc(self.governance_revision_ref, "governance_revision_ref")

        if self.candidate_guard_ref.artifact_code != EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE:
            raise ValueError(f"candidate_guard_ref artifact_code must be {EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE}")
        if self.candidate_guard_ref.version != EVALUATION_GUARD_ARTIFACT_VERSION:
            raise ValueError(f"candidate_guard_ref version must be {EVALUATION_GUARD_ARTIFACT_VERSION}")

        _validate_evidence_partitions(self.required_partitions)
        _validate_evidence_bindings(self)

        return self


def _validate_evidence_partitions(partitions: tuple[Partition, ...]) -> None:
    if not partitions:
        raise ValueError("required_partitions must not be empty")
    if len(partitions) != len(set(partitions)):
        raise ValueError("required_partitions must not contain duplicates")
    sorted_partitions = tuple(sorted(partitions, key=lambda p: p.value.encode("utf-16-be")))
    if partitions != sorted_partitions:
        raise ValueError("required_partitions must be sorted in UTF-16 BE order")


def _validate_single_binding_coordinates(
    evidence: EvaluationGuardEvidence,
    binding: EvaluationCaseGuardBinding,
    idx: int,
) -> None:
    checks = (
        (binding.evaluation_run_id != evidence.evaluation_run_id, "evaluation_run_id"),
        (binding.candidate_bundle_id != evidence.candidate_bundle_id, "candidate_bundle_id"),
        (
            binding.candidate_bundle_manifest_hash != evidence.candidate_bundle_manifest_hash,
            "candidate_bundle_manifest_hash",
        ),
        (binding.candidate_guard_ref != evidence.candidate_guard_ref, "candidate_guard_ref"),
        (
            binding.runtime_execution_manifest_id != evidence.runtime_execution_manifest_id,
            "runtime_execution_manifest_id",
        ),
        (
            binding.runtime_execution_manifest_hash != evidence.runtime_execution_manifest_hash,
            "runtime_execution_manifest_hash",
        ),
        (binding.environment != evidence.environment, "environment"),
        (binding.environment_revision_fence != evidence.environment_revision_fence, "environment_revision_fence"),
        (binding.governance_revision_ref != evidence.governance_revision_ref, "governance_revision_ref"),
        (binding.safety_epoch != evidence.safety_epoch, "safety_epoch"),
        (binding.required_case_set_hash != evidence.required_case_set_hash, "required_case_set_hash"),
    )
    for is_mismatch, field in checks:
        if is_mismatch:
            raise EvaluationValidationError(
                EvaluationErrorCode.HASH_MISMATCH,
                safe_path=f"case_guard_bindings[{idx}].{field}",
            )


def _validate_evidence_bindings(evidence: EvaluationGuardEvidence) -> None:
    seen_case_ids: set[str] = set()
    for idx, binding in enumerate(evidence.case_guard_bindings):
        if binding.case_id in seen_case_ids:
            raise EvaluationValidationError(
                EvaluationErrorCode.CASE_DUPLICATE,
                safe_path=f"case_guard_bindings[{idx}].case_id",
            )
        seen_case_ids.add(binding.case_id)
        _validate_single_binding_coordinates(evidence, binding, idx)

    sorted_bindings = tuple(sorted(evidence.case_guard_bindings, key=lambda b: b.case_id.encode("utf-16-be")))
    if evidence.case_guard_bindings != sorted_bindings:
        raise EvaluationValidationError(
            EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
            safe_path="case_guard_bindings",
        )
