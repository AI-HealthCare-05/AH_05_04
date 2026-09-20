"""Canonical Evaluation Guard Authority and Evidence Bridge for #162 Phase A2.

This module implements:
- EvaluationCandidateGuardEvaluator
- EvaluationRequestGuardEvaluator
- Canonical projection and hash recipes
- Required Case exact-set validation and hash
- Concurrency fence validation
- Finalization of EvaluationGuardEvidence
- Independent evidence validator
- RagEvaluationRun binding validator
- Standalone artifact loader
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import ValidatedDataset, load_json_object
from ai_worker.tasks.evaluation.schemas.authoring import DatasetStatus
from ai_worker.tasks.evaluation.schemas.common import Partition
from ai_worker.tasks.evaluation.schemas.guard_evidence_v1 import (
    EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE,
    EVALUATION_CANDIDATE_OPERATION,
    EVALUATION_GUARD_ARTIFACT_VERSION,
    EVALUATION_GUARD_PASS_DECISION,
    EVALUATION_REQUEST_GUARD_ARTIFACT_CODE,
    EVALUATION_REQUEST_OPERATION,
    PROJECTION_VERSION_CANDIDATE_GUARD,
    PROJECTION_VERSION_CASE_GUARD,
    PROJECTION_VERSION_GUARD_COVERAGE,
    PROJECTION_VERSION_REQUIRED_CASE_SET,
    CandidateSourceGovernanceObservation,
    EvaluationCandidateGuard,
    EvaluationCaseGuardBinding,
    EvaluationGuardArtifactRef,
    EvaluationGuardEvidence,
)
from rag_runtime.request_guard_runtime_binding import canonical_scope_manifest_hash

if TYPE_CHECKING:
    from ai_worker.adapters.sqlalchemy_evaluation_guard_authority import (
        EvaluationEnvironmentFenceObservation,
        EvaluationRuntimeAuthorityObservation,
    )
    from ai_worker.tasks.evaluation.schemas.artifacts import RagEvaluationRun


# ---------------------------------------------------------------------------
# Projection Builders & Canonical Hashes
# ---------------------------------------------------------------------------


def compute_required_case_set_hash(
    *,
    dataset_manifest_sha256: str,
    required_partitions: Sequence[Partition | str],
    case_ids: Sequence[str],
) -> str:
    """Compute deterministic SHA-256 for the required case set projection."""
    if len(case_ids) != len(set(case_ids)):
        raise EvaluationValidationError(EvaluationErrorCode.CASE_DUPLICATE)

    sorted_partitions = sorted(
        set(p.value if hasattr(p, "value") else str(p) for p in required_partitions),
        key=lambda s: s.encode("utf-16-be"),
    )
    sorted_case_ids = sorted(case_ids, key=lambda s: s.encode("utf-16-be"))

    projection: dict[str, Any] = {
        "case_ids": sorted_case_ids,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "projection_version": PROJECTION_VERSION_REQUIRED_CASE_SET,
        "required_partitions": sorted_partitions,
    }
    return canonical_sha256(projection)


def _candidate_guard_projection_from_fields(
    *,
    candidate_guard_decision_id: str,
    operation: str,
    decision: str,
    evaluation_run_id: str,
    environment: str,
    environment_revision_fence: int,
    governance_revision_ref: str,
    safety_epoch: int,
    candidate_bundle_id: str,
    candidate_bundle_manifest_hash: str,
    runtime_execution_manifest_id: str,
    runtime_execution_manifest_hash: str,
    dataset_code: str,
    dataset_version: str,
    dataset_manifest_sha256: str,
    required_partitions: Sequence[Partition | str],
    required_case_set_hash: str,
) -> dict[str, Any]:
    sorted_partitions = sorted(
        (p.value if hasattr(p, "value") else str(p) for p in required_partitions),
        key=lambda s: s.encode("utf-16-be"),
    )
    return {
        "candidate_bundle_id": candidate_bundle_id,
        "candidate_bundle_manifest_hash": candidate_bundle_manifest_hash,
        "candidate_guard_decision_id": candidate_guard_decision_id,
        "dataset_code": dataset_code,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "dataset_version": dataset_version,
        "decision": decision,
        "environment": environment,
        "environment_revision_fence": environment_revision_fence,
        "evaluation_run_id": evaluation_run_id,
        "governance_revision_ref": governance_revision_ref,
        "operation": operation,
        "projection_version": PROJECTION_VERSION_CANDIDATE_GUARD,
        "required_case_set_hash": required_case_set_hash,
        "required_partitions": sorted_partitions,
        "runtime_execution_manifest_hash": runtime_execution_manifest_hash,
        "runtime_execution_manifest_id": runtime_execution_manifest_id,
        "safety_epoch": safety_epoch,
    }


def candidate_guard_projection(candidate: EvaluationCandidateGuard) -> dict[str, Any]:
    """Extract exact evaluation-candidate-guard-v1 projection from an existing candidate."""
    return _candidate_guard_projection_from_fields(
        candidate_guard_decision_id=candidate.candidate_guard_decision_id,
        operation=candidate.operation,
        decision=candidate.decision,
        evaluation_run_id=candidate.evaluation_run_id,
        environment=candidate.environment,
        environment_revision_fence=candidate.environment_revision_fence,
        governance_revision_ref=candidate.governance_revision_ref,
        safety_epoch=candidate.safety_epoch,
        candidate_bundle_id=candidate.candidate_bundle_id,
        candidate_bundle_manifest_hash=candidate.candidate_bundle_manifest_hash,
        runtime_execution_manifest_id=candidate.runtime_execution_manifest_id,
        runtime_execution_manifest_hash=candidate.runtime_execution_manifest_hash,
        dataset_code=candidate.dataset_code,
        dataset_version=candidate.dataset_version,
        dataset_manifest_sha256=candidate.dataset_manifest_sha256,
        required_partitions=candidate.required_partitions,
        required_case_set_hash=candidate.required_case_set_hash,
    )


def compute_candidate_guard_ref(candidate: EvaluationCandidateGuard) -> EvaluationGuardArtifactRef:
    """Compute the immutable reference for a candidate guard."""
    projection = candidate_guard_projection(candidate)
    content_sha256 = canonical_sha256(projection)
    return EvaluationGuardArtifactRef(
        artifact_code=EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE,
        version=EVALUATION_GUARD_ARTIFACT_VERSION,
        content_sha256=content_sha256,
    )


def validate_candidate_guard_ref(candidate: EvaluationCandidateGuard) -> None:
    """Recompute candidate guard ref and verify exact match with stored ref."""
    expected_ref = compute_candidate_guard_ref(candidate)
    if candidate.candidate_guard_ref != expected_ref:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            safe_path="candidate_guard_ref.content_sha256",
        )


def _case_guard_projection_from_fields(
    *,
    case_guard_decision_id: str,
    operation: str,
    decision: str,
    evaluation_run_id: str,
    case_id: str,
    candidate_guard_ref: EvaluationGuardArtifactRef,
    environment: str,
    environment_revision_fence: int,
    governance_revision_ref: str,
    safety_epoch: int,
    candidate_bundle_id: str,
    candidate_bundle_manifest_hash: str,
    runtime_execution_manifest_id: str,
    runtime_execution_manifest_hash: str,
    required_case_set_hash: str,
    request_scope_codes: Sequence[str],
    scope_manifest_hash: str,
) -> dict[str, Any]:
    return {
        "candidate_bundle_id": candidate_bundle_id,
        "candidate_bundle_manifest_hash": candidate_bundle_manifest_hash,
        "candidate_guard_ref": {
            "artifact_code": candidate_guard_ref.artifact_code,
            "content_sha256": candidate_guard_ref.content_sha256,
            "version": candidate_guard_ref.version,
        },
        "case_guard_decision_id": case_guard_decision_id,
        "case_id": case_id,
        "decision": decision,
        "environment": environment,
        "environment_revision_fence": environment_revision_fence,
        "evaluation_run_id": evaluation_run_id,
        "governance_revision_ref": governance_revision_ref,
        "operation": operation,
        "projection_version": PROJECTION_VERSION_CASE_GUARD,
        "request_scope_codes": list(request_scope_codes),
        "required_case_set_hash": required_case_set_hash,
        "runtime_execution_manifest_hash": runtime_execution_manifest_hash,
        "runtime_execution_manifest_id": runtime_execution_manifest_id,
        "safety_epoch": safety_epoch,
        "scope_manifest_hash": scope_manifest_hash,
    }


def case_guard_projection(binding: EvaluationCaseGuardBinding) -> dict[str, Any]:
    """Extract exact evaluation-request-guard-v1 projection from an existing case guard binding."""
    return _case_guard_projection_from_fields(
        case_guard_decision_id=binding.case_guard_decision_id,
        operation=binding.operation,
        decision=binding.decision,
        evaluation_run_id=binding.evaluation_run_id,
        case_id=binding.case_id,
        candidate_guard_ref=binding.candidate_guard_ref,
        environment=binding.environment,
        environment_revision_fence=binding.environment_revision_fence,
        governance_revision_ref=binding.governance_revision_ref,
        safety_epoch=binding.safety_epoch,
        candidate_bundle_id=binding.candidate_bundle_id,
        candidate_bundle_manifest_hash=binding.candidate_bundle_manifest_hash,
        runtime_execution_manifest_id=binding.runtime_execution_manifest_id,
        runtime_execution_manifest_hash=binding.runtime_execution_manifest_hash,
        required_case_set_hash=binding.required_case_set_hash,
        request_scope_codes=binding.request_scope_codes,
        scope_manifest_hash=binding.scope_manifest_hash,
    )


def compute_case_guard_ref(binding: EvaluationCaseGuardBinding) -> EvaluationGuardArtifactRef:
    """Compute the immutable reference for a case guard."""
    projection = case_guard_projection(binding)
    content_sha256 = canonical_sha256(projection)
    return EvaluationGuardArtifactRef(
        artifact_code=EVALUATION_REQUEST_GUARD_ARTIFACT_CODE,
        version=EVALUATION_GUARD_ARTIFACT_VERSION,
        content_sha256=content_sha256,
    )


def validate_case_guard_ref(binding: EvaluationCaseGuardBinding) -> None:
    """Recompute case guard ref and verify exact match with stored ref."""
    expected_ref = compute_case_guard_ref(binding)
    if binding.case_guard_ref != expected_ref:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            safe_path=f"case_guard_bindings[{binding.case_id}].case_guard_ref.content_sha256",
        )


def guard_coverage_projection(
    *,
    evaluation_run_id: str,
    candidate_bundle_id: str,
    candidate_bundle_manifest_hash: str,
    runtime_execution_manifest_id: str,
    runtime_execution_manifest_hash: str,
    environment: str,
    environment_revision_fence: int,
    governance_revision_ref: str,
    safety_epoch: int,
    candidate_guard_ref: EvaluationGuardArtifactRef,
    dataset_manifest_sha256: str,
    required_case_set_hash: str,
    case_guard_bindings: Sequence[EvaluationCaseGuardBinding],
) -> dict[str, Any]:
    """Produce evaluation-guard-coverage-v1 projection with canonical binding order."""
    # Ensure bindings are strictly ordered by UTF-16 BE
    sorted_bindings = sorted(case_guard_bindings, key=lambda b: b.case_id.encode("utf-16-be"))

    serialized_bindings: list[dict[str, Any]] = []
    for b in sorted_bindings:
        # Recompute case ref independently to avoid trusting self-asserted ref
        recomputed_case_ref = compute_case_guard_ref(b)
        serialized_bindings.append(
            {
                "case_guard_decision_id": b.case_guard_decision_id,
                "case_guard_ref": {
                    "artifact_code": recomputed_case_ref.artifact_code,
                    "content_sha256": recomputed_case_ref.content_sha256,
                    "version": recomputed_case_ref.version,
                },
                "case_id": b.case_id,
                "scope_manifest_hash": b.scope_manifest_hash,
            }
        )

    return {
        "candidate_bundle_id": candidate_bundle_id,
        "candidate_bundle_manifest_hash": candidate_bundle_manifest_hash,
        "candidate_guard_ref": {
            "artifact_code": candidate_guard_ref.artifact_code,
            "content_sha256": candidate_guard_ref.content_sha256,
            "version": candidate_guard_ref.version,
        },
        "case_guard_bindings": serialized_bindings,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "environment": environment,
        "environment_revision_fence": environment_revision_fence,
        "evaluation_run_id": evaluation_run_id,
        "governance_revision_ref": governance_revision_ref,
        "projection_version": PROJECTION_VERSION_GUARD_COVERAGE,
        "required_case_set_hash": required_case_set_hash,
        "runtime_execution_manifest_hash": runtime_execution_manifest_hash,
        "runtime_execution_manifest_id": runtime_execution_manifest_id,
        "safety_epoch": safety_epoch,
    }


def compute_guard_coverage_manifest_hash(
    *,
    evaluation_run_id: str,
    candidate_bundle_id: str,
    candidate_bundle_manifest_hash: str,
    runtime_execution_manifest_id: str,
    runtime_execution_manifest_hash: str,
    environment: str,
    environment_revision_fence: int,
    governance_revision_ref: str,
    safety_epoch: int,
    candidate_guard_ref: EvaluationGuardArtifactRef,
    dataset_manifest_sha256: str,
    required_case_set_hash: str,
    case_guard_bindings: Sequence[EvaluationCaseGuardBinding],
) -> str:
    """Compute canonical SHA-256 for guard coverage manifest."""
    projection = guard_coverage_projection(
        evaluation_run_id=evaluation_run_id,
        candidate_bundle_id=candidate_bundle_id,
        candidate_bundle_manifest_hash=candidate_bundle_manifest_hash,
        runtime_execution_manifest_id=runtime_execution_manifest_id,
        runtime_execution_manifest_hash=runtime_execution_manifest_hash,
        environment=environment,
        environment_revision_fence=environment_revision_fence,
        governance_revision_ref=governance_revision_ref,
        safety_epoch=safety_epoch,
        candidate_guard_ref=candidate_guard_ref,
        dataset_manifest_sha256=dataset_manifest_sha256,
        required_case_set_hash=required_case_set_hash,
        case_guard_bindings=case_guard_bindings,
    )
    return canonical_sha256(projection)


canonical_evaluation_guard_coverage_hash = compute_guard_coverage_manifest_hash


# ---------------------------------------------------------------------------
# Dataset Helper (Standalone, avoids #163 reverse dependency)
# ---------------------------------------------------------------------------


def derive_required_case_ids_for_partitions(
    dataset: ValidatedDataset,
    required_partitions: Sequence[Partition | str],
) -> tuple[str, ...]:
    """Derive case IDs for target partitions, verifying FROZEN status and case uniqueness."""
    status = getattr(dataset.manifest, "status", None)
    if status is not DatasetStatus.FROZEN and status != "FROZEN" and getattr(status, "value", None) != "FROZEN":
        raise EvaluationValidationError(EvaluationErrorCode.REVIEW_PROVENANCE_INVALID)

    target_values = {p.value if hasattr(p, "value") else str(p) for p in required_partitions}

    seen_all: set[str] = set()
    filtered: list[str] = []
    for c in dataset.cases:
        case_id = c.case_id
        if case_id in seen_all:
            raise EvaluationValidationError(EvaluationErrorCode.CASE_DUPLICATE)
        seen_all.add(case_id)

        partition_val = getattr(c.partition, "value", None) or str(c.partition)
        if partition_val in target_values:
            filtered.append(case_id)

    if not filtered:
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    return tuple(filtered)


# ---------------------------------------------------------------------------
# Pure Evaluators
# ---------------------------------------------------------------------------


def compute_candidate_guard_hash(
    *,
    candidate_guard_decision_id: str,
    operation: str = EVALUATION_CANDIDATE_OPERATION,
    decision: str = EVALUATION_GUARD_PASS_DECISION,
    evaluation_run_id: str,
    environment: str = "LOCAL",
    environment_revision_fence: int,
    governance_revision_ref: str,
    safety_epoch: int,
    candidate_bundle_id: str,
    candidate_bundle_manifest_hash: str,
    runtime_execution_manifest_id: str,
    runtime_execution_manifest_hash: str,
    dataset_code: str,
    dataset_version: str,
    dataset_manifest_sha256: str,
    required_partitions: Sequence[Partition | str],
    required_case_set_hash: str,
) -> str:
    """Compute cryptographic hash of evaluation-candidate-guard-v1 projection."""
    projection = _candidate_guard_projection_from_fields(
        candidate_guard_decision_id=candidate_guard_decision_id,
        operation=operation,
        decision=decision,
        evaluation_run_id=evaluation_run_id,
        environment=environment,
        environment_revision_fence=environment_revision_fence,
        governance_revision_ref=governance_revision_ref,
        safety_epoch=safety_epoch,
        candidate_bundle_id=candidate_bundle_id,
        candidate_bundle_manifest_hash=candidate_bundle_manifest_hash,
        runtime_execution_manifest_id=runtime_execution_manifest_id,
        runtime_execution_manifest_hash=runtime_execution_manifest_hash,
        dataset_code=dataset_code,
        dataset_version=dataset_version,
        dataset_manifest_sha256=dataset_manifest_sha256,
        required_partitions=required_partitions,
        required_case_set_hash=required_case_set_hash,
    )
    return canonical_sha256(projection)


canonical_evaluation_candidate_guard_hash = compute_candidate_guard_hash
canonical_evaluation_required_case_set_hash = compute_required_case_set_hash


# ---------------------------------------------------------------------------
# Pure Evaluators
# ---------------------------------------------------------------------------


class EvaluationCandidateGuardEvaluator:
    """Pure evaluator for Run-level Candidate Guard authority.

    Authority Boundary Notice (#162 Phase A2):
    In Phase A2, canonical production Source Governance authority (Source, Snapshot,
    Approval, Freshness, Scope, Revocation) is not yet available as an end-to-end
    consumer chain in develop.
    Therefore, CandidateSourceGovernanceObservation serves strictly as an upstream
    diagnostic observation carrier, NOT as a canonical authorization proof.
    The production evaluator permanently operates fail-closed:
    no production EvaluationCandidateGuard PASS object can be issued
    (CANDIDATE_SOURCE_AUTHORITY_BLOCKED), and evaluate_candidate_start always returns None.
    """

    @staticmethod
    def evaluate_candidate_start(
        *,
        evaluation_run_id: UUID | str,
        dataset: ValidatedDataset,
        required_partitions: Sequence[Partition | str],
        runtime_authority: EvaluationRuntimeAuthorityObservation,
        source_governance_authority: CandidateSourceGovernanceObservation | None = None,
        decision_id_factory: Callable[[], UUID] = uuid4,
    ) -> EvaluationCandidateGuard | None:
        """Evaluate candidate start conditions.

        Validates structural runtime authority and fails closed if corrupted.
        Because canonical Source Governance authority is blocked/unavailable in Phase A2,
        this evaluator never issues an EvaluationCandidateGuard PASS artifact,
        returning None even if a caller-created PASS observation is provided.
        """
        # Validate Runtime Authority
        if runtime_authority.bundle_status != "BUILDING":
            raise EvaluationValidationError(
                EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
                safe_path="runtime_authority.bundle_status",
            )
        if runtime_authority.environment_code != "LOCAL":
            raise EvaluationValidationError(
                EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
                safe_path="runtime_authority.environment_code",
            )
        if runtime_authority.environment_revision < 1:
            raise EvaluationValidationError(
                EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
                safe_path="runtime_authority.environment_revision",
            )
        if runtime_authority.safety_epoch < 1:
            raise EvaluationValidationError(
                EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
                safe_path="runtime_authority.safety_epoch",
            )
        if not runtime_authority.governance_revision_ref or not runtime_authority.governance_revision_ref.strip():
            raise EvaluationValidationError(
                EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
                safe_path="runtime_authority.governance_revision_ref",
            )

        # In Phase A2, canonical production Source Governance authority is not available.
        # CandidateSourceGovernanceObservation is merely a diagnostic observation carrier,
        # never a canonical authorization proof. Caller-asserted PASS does not grant authority.
        # Production Candidate PASS issuance is blocked (CANDIDATE_SOURCE_AUTHORITY_BLOCKED).
        return None

    evaluate = evaluate_candidate_start


class EvaluationRequestGuardEvaluator:
    """Pure producer for Case-level Request Guard authority."""

    @staticmethod
    def evaluate_case(
        *,
        candidate_guard: EvaluationCandidateGuard,
        case_id: str,
        request_scope_codes: tuple[str, ...],
        decision_id_factory: Callable[[], UUID] = uuid4,
    ) -> EvaluationCaseGuardBinding:
        """Produce a verified Case Guard decision strictly bound to the parent candidate guard."""
        # Verify parent candidate guard ref
        validate_candidate_guard_ref(candidate_guard)

        # Compute scope hash using canonical helper
        try:
            scope_manifest_hash = canonical_scope_manifest_hash(request_scope_codes)
        except Exception as error:
            raise EvaluationValidationError(
                EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
                safe_path="request_scope_codes",
            ) from error

        decision_id = str(decision_id_factory())

        # Build ref-free projection first
        projection = _case_guard_projection_from_fields(
            case_guard_decision_id=decision_id,
            operation=EVALUATION_REQUEST_OPERATION,
            decision=EVALUATION_GUARD_PASS_DECISION,
            evaluation_run_id=candidate_guard.evaluation_run_id,
            case_id=case_id,
            candidate_guard_ref=candidate_guard.candidate_guard_ref,
            environment=candidate_guard.environment,
            environment_revision_fence=candidate_guard.environment_revision_fence,
            governance_revision_ref=candidate_guard.governance_revision_ref,
            safety_epoch=candidate_guard.safety_epoch,
            candidate_bundle_id=candidate_guard.candidate_bundle_id,
            candidate_bundle_manifest_hash=candidate_guard.candidate_bundle_manifest_hash,
            runtime_execution_manifest_id=candidate_guard.runtime_execution_manifest_id,
            runtime_execution_manifest_hash=candidate_guard.runtime_execution_manifest_hash,
            required_case_set_hash=candidate_guard.required_case_set_hash,
            request_scope_codes=request_scope_codes,
            scope_manifest_hash=scope_manifest_hash,
        )

        content_sha256 = canonical_sha256(projection)
        case_ref = EvaluationGuardArtifactRef(
            artifact_code=EVALUATION_REQUEST_GUARD_ARTIFACT_CODE,
            version=EVALUATION_GUARD_ARTIFACT_VERSION,
            content_sha256=content_sha256,
        )

        return EvaluationCaseGuardBinding(
            case_guard_decision_id=decision_id,
            operation=EVALUATION_REQUEST_OPERATION,
            decision=EVALUATION_GUARD_PASS_DECISION,
            evaluation_run_id=candidate_guard.evaluation_run_id,
            case_id=case_id,
            candidate_guard_ref=candidate_guard.candidate_guard_ref,
            environment=candidate_guard.environment,
            environment_revision_fence=candidate_guard.environment_revision_fence,
            governance_revision_ref=candidate_guard.governance_revision_ref,
            safety_epoch=candidate_guard.safety_epoch,
            candidate_bundle_id=candidate_guard.candidate_bundle_id,
            candidate_bundle_manifest_hash=candidate_guard.candidate_bundle_manifest_hash,
            runtime_execution_manifest_id=candidate_guard.runtime_execution_manifest_id,
            runtime_execution_manifest_hash=candidate_guard.runtime_execution_manifest_hash,
            required_case_set_hash=candidate_guard.required_case_set_hash,
            request_scope_codes=request_scope_codes,
            scope_manifest_hash=scope_manifest_hash,
            case_guard_ref=case_ref,
        )

    evaluate = evaluate_case


# ---------------------------------------------------------------------------
# Concurrency Fence & Finalization
# ---------------------------------------------------------------------------


def validate_environment_fence(
    *,
    candidate_guard: EvaluationCandidateGuard,
    end_fence: EvaluationEnvironmentFenceObservation,
) -> None:
    """Validate that environment authority did not transition during evaluation run."""
    if end_fence.environment_code != candidate_guard.environment:
        raise EvaluationValidationError(
            EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
            safe_path="end_fence.environment_code",
        )
    if end_fence.environment_revision != candidate_guard.environment_revision_fence:
        raise EvaluationValidationError(
            EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
            safe_path="end_fence.environment_revision",
        )
    if end_fence.governance_revision_ref != candidate_guard.governance_revision_ref:
        raise EvaluationValidationError(
            EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
            safe_path="end_fence.governance_revision_ref",
        )
    if end_fence.safety_epoch != candidate_guard.safety_epoch:
        raise EvaluationValidationError(
            EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
            safe_path="end_fence.safety_epoch",
        )


verify_environment_revision_fence = validate_environment_fence


def finalize_evaluation_guard_evidence(
    *,
    candidate_guard: EvaluationCandidateGuard,
    case_guards: Sequence[EvaluationCaseGuardBinding],
    dataset: ValidatedDataset,
    required_partitions: Sequence[Partition | str],
    end_environment_fence: EvaluationEnvironmentFenceObservation,
) -> EvaluationGuardEvidence:
    """Finalize the EvaluationGuardEvidence artifact following the strict 12-step contract."""
    # 1. FROZEN Dataset validation & 2. required case exact set
    expected_case_ids = derive_required_case_ids_for_partitions(dataset, required_partitions)

    # 3. required_case_set_hash verification
    canonical_partitions = tuple(
        sorted(
            (Partition(p.value if hasattr(p, "value") else str(p)) for p in required_partitions),
            key=lambda p: p.value.encode("utf-16-be"),
        )
    )
    expected_case_set_hash = compute_required_case_set_hash(
        dataset_manifest_sha256=dataset.manifest.manifest_sha256,
        required_partitions=canonical_partitions,
        case_ids=expected_case_ids,
    )
    if candidate_guard.required_case_set_hash != expected_case_set_hash:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            safe_path="candidate_guard.required_case_set_hash",
        )

    # 4. candidate ref validation
    validate_candidate_guard_ref(candidate_guard)

    # 5. case refs validation & 6. candidate <-> case coordinates exact match
    seen_case_ids: set[str] = set()
    for idx, binding in enumerate(case_guards):
        validate_case_guard_ref(binding)
        if binding.case_id in seen_case_ids:
            raise EvaluationValidationError(
                EvaluationErrorCode.CASE_DUPLICATE,
                safe_path=f"case_guards[{idx}].case_id",
            )
        seen_case_ids.add(binding.case_id)
        if (
            binding.evaluation_run_id != candidate_guard.evaluation_run_id
            or binding.candidate_bundle_id != candidate_guard.candidate_bundle_id
            or binding.candidate_bundle_manifest_hash != candidate_guard.candidate_bundle_manifest_hash
            or binding.candidate_guard_ref != candidate_guard.candidate_guard_ref
            or binding.runtime_execution_manifest_id != candidate_guard.runtime_execution_manifest_id
            or binding.runtime_execution_manifest_hash != candidate_guard.runtime_execution_manifest_hash
            or binding.environment != candidate_guard.environment
            or binding.environment_revision_fence != candidate_guard.environment_revision_fence
            or binding.governance_revision_ref != candidate_guard.governance_revision_ref
            or binding.safety_epoch != candidate_guard.safety_epoch
            or binding.required_case_set_hash != candidate_guard.required_case_set_hash
        ):
            raise EvaluationValidationError(
                EvaluationErrorCode.HASH_MISMATCH,
                safe_path=f"case_guards[{idx}].candidate_coordinate_mismatch",
            )

    # 7. duplicate / missing / extra exact-set verification
    expected_set = set(expected_case_ids)
    if seen_case_ids != expected_set:
        # Check missing or extra
        missing = expected_set - seen_case_ids
        if missing:
            raise EvaluationValidationError(
                EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
                safe_path="case_guards.missing_cases",
            )
        extra = seen_case_ids - expected_set
        if extra:
            raise EvaluationValidationError(
                EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
                safe_path="case_guards.extra_cases",
            )
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    # 8. canonical wire ordering check: input must match UTF-16 BE order
    canonical_ordered_bindings = tuple(sorted(case_guards, key=lambda b: b.case_id.encode("utf-16-be")))
    if tuple(case_guards) != canonical_ordered_bindings:
        raise EvaluationValidationError(
            EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
            safe_path="case_guards.wire_order",
        )

    # 9. end Environment fence validation
    validate_environment_fence(
        candidate_guard=candidate_guard,
        end_fence=end_environment_fence,
    )

    # 10 & 11. coverage projection and canonical_sha256
    coverage_hash = compute_guard_coverage_manifest_hash(
        evaluation_run_id=candidate_guard.evaluation_run_id,
        candidate_bundle_id=candidate_guard.candidate_bundle_id,
        candidate_bundle_manifest_hash=candidate_guard.candidate_bundle_manifest_hash,
        runtime_execution_manifest_id=candidate_guard.runtime_execution_manifest_id,
        runtime_execution_manifest_hash=candidate_guard.runtime_execution_manifest_hash,
        environment=candidate_guard.environment,
        environment_revision_fence=candidate_guard.environment_revision_fence,
        governance_revision_ref=candidate_guard.governance_revision_ref,
        safety_epoch=candidate_guard.safety_epoch,
        candidate_guard_ref=candidate_guard.candidate_guard_ref,
        dataset_manifest_sha256=dataset.manifest.manifest_sha256,
        required_case_set_hash=expected_case_set_hash,
        case_guard_bindings=canonical_ordered_bindings,
    )

    # 12. build final aggregate evidence
    try:
        return EvaluationGuardEvidence(
            evaluation_run_id=candidate_guard.evaluation_run_id,
            candidate_bundle_id=candidate_guard.candidate_bundle_id,
            candidate_bundle_manifest_hash=candidate_guard.candidate_bundle_manifest_hash,
            dataset_code=dataset.manifest.dataset_code,
            dataset_version=dataset.manifest.dataset_version,
            dataset_manifest_sha256=dataset.manifest.manifest_sha256,
            required_partitions=canonical_partitions,
            required_case_set_hash=expected_case_set_hash,
            environment=candidate_guard.environment,
            environment_revision_fence=candidate_guard.environment_revision_fence,
            governance_revision_ref=candidate_guard.governance_revision_ref,
            safety_epoch=candidate_guard.safety_epoch,
            candidate_guard_decision_id=candidate_guard.candidate_guard_decision_id,
            candidate_guard_decision=candidate_guard.decision,
            candidate_guard_ref=candidate_guard.candidate_guard_ref,
            runtime_execution_manifest_id=candidate_guard.runtime_execution_manifest_id,
            runtime_execution_manifest_hash=candidate_guard.runtime_execution_manifest_hash,
            case_guard_bindings=canonical_ordered_bindings,
            guard_coverage_manifest_hash=coverage_hash,
        )
    except ValidationError as exc:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# Independent Evidence Validator
# ---------------------------------------------------------------------------


def _verify_evidence_schema(evidence: EvaluationGuardEvidence) -> None:
    if evidence.schema_id != "rag-eval.evaluation-guard-evidence":
        raise EvaluationValidationError(
            EvaluationErrorCode.SCHEMA_INVALID,
            safe_path="schema_id",
        )
    if evidence.schema_version != "1.0.0":
        raise EvaluationValidationError(
            EvaluationErrorCode.SCHEMA_INVALID,
            safe_path="schema_version",
        )


def _verify_evidence_dataset_provenance(
    evidence: EvaluationGuardEvidence,
    dataset: ValidatedDataset,
) -> None:
    status = getattr(dataset.manifest, "status", None)
    if status is not DatasetStatus.FROZEN and status != "FROZEN" and getattr(status, "value", None) != "FROZEN":
        raise EvaluationValidationError(EvaluationErrorCode.REVIEW_PROVENANCE_INVALID)

    checks = (
        (evidence.dataset_manifest_sha256 != dataset.manifest.manifest_sha256, "dataset_manifest_sha256"),
        (evidence.dataset_code != dataset.manifest.dataset_code, "dataset_code"),
        (evidence.dataset_version != dataset.manifest.dataset_version, "dataset_version"),
    )
    for is_mismatch, field in checks:
        if is_mismatch:
            raise EvaluationValidationError(
                EvaluationErrorCode.HASH_MISMATCH,
                safe_path=field,
            )


def _verify_evidence_case_set(
    evidence: EvaluationGuardEvidence,
    dataset: ValidatedDataset,
    required_partitions: Sequence[Partition | str],
) -> tuple[tuple[str, ...], str]:
    expected_case_ids = tuple(derive_required_case_ids_for_partitions(dataset, required_partitions))
    canonical_partitions = tuple(
        sorted(
            (Partition(p.value if hasattr(p, "value") else str(p)) for p in required_partitions),
            key=lambda p: p.value.encode("utf-16-be"),
        )
    )
    expected_case_set_hash = compute_required_case_set_hash(
        dataset_manifest_sha256=dataset.manifest.manifest_sha256,
        required_partitions=canonical_partitions,
        case_ids=expected_case_ids,
    )
    if evidence.required_case_set_hash != expected_case_set_hash:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            safe_path="required_case_set_hash",
        )
    if evidence.required_partitions != canonical_partitions:
        raise EvaluationValidationError(
            EvaluationErrorCode.PARTITION_INVALID,
            safe_path="required_partitions",
        )
    return expected_case_ids, expected_case_set_hash


def _recompute_evidence_candidate_ref(evidence: EvaluationGuardEvidence) -> EvaluationGuardArtifactRef:
    recomputed_candidate_ref = EvaluationGuardArtifactRef(
        artifact_code=EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE,
        version=EVALUATION_GUARD_ARTIFACT_VERSION,
        content_sha256=canonical_sha256(
            _candidate_guard_projection_from_fields(
                candidate_guard_decision_id=evidence.candidate_guard_decision_id,
                operation=EVALUATION_CANDIDATE_OPERATION,
                decision=evidence.candidate_guard_decision,
                evaluation_run_id=evidence.evaluation_run_id,
                environment=evidence.environment,
                environment_revision_fence=evidence.environment_revision_fence,
                governance_revision_ref=evidence.governance_revision_ref,
                safety_epoch=evidence.safety_epoch,
                candidate_bundle_id=evidence.candidate_bundle_id,
                candidate_bundle_manifest_hash=evidence.candidate_bundle_manifest_hash,
                runtime_execution_manifest_id=evidence.runtime_execution_manifest_id,
                runtime_execution_manifest_hash=evidence.runtime_execution_manifest_hash,
                dataset_code=evidence.dataset_code,
                dataset_version=evidence.dataset_version,
                dataset_manifest_sha256=evidence.dataset_manifest_sha256,
                required_partitions=evidence.required_partitions,
                required_case_set_hash=evidence.required_case_set_hash,
            )
        ),
    )
    if evidence.candidate_guard_ref != recomputed_candidate_ref:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            safe_path="candidate_guard_ref",
        )
    return recomputed_candidate_ref


def _verify_evidence_case_bindings_and_coverage(
    evidence: EvaluationGuardEvidence,
    expected_case_ids: Sequence[str],
    expected_case_set_hash: str,
    recomputed_candidate_ref: EvaluationGuardArtifactRef,
) -> None:
    seen_case_ids: list[str] = []
    for idx, binding in enumerate(evidence.case_guard_bindings):
        validate_case_guard_ref(binding)
        if binding.case_id in seen_case_ids:
            raise EvaluationValidationError(
                EvaluationErrorCode.CASE_DUPLICATE,
                safe_path=f"case_guard_bindings[{idx}].case_id",
            )
        seen_case_ids.append(binding.case_id)

    canonical_case_ids = sorted(seen_case_ids, key=lambda s: s.encode("utf-16-be"))
    if seen_case_ids != canonical_case_ids:
        raise EvaluationValidationError(
            EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
            safe_path="case_guard_bindings.wire_order",
        )

    if set(seen_case_ids) != set(expected_case_ids):
        if set(expected_case_ids) - set(seen_case_ids):
            raise EvaluationValidationError(
                EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
                safe_path="case_guard_bindings.missing_cases",
            )
        if set(seen_case_ids) - set(expected_case_ids):
            raise EvaluationValidationError(
                EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
                safe_path="case_guard_bindings.extra_cases",
            )
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    expected_coverage_hash = compute_guard_coverage_manifest_hash(
        evaluation_run_id=evidence.evaluation_run_id,
        candidate_bundle_id=evidence.candidate_bundle_id,
        candidate_bundle_manifest_hash=evidence.candidate_bundle_manifest_hash,
        runtime_execution_manifest_id=evidence.runtime_execution_manifest_id,
        runtime_execution_manifest_hash=evidence.runtime_execution_manifest_hash,
        environment=evidence.environment,
        environment_revision_fence=evidence.environment_revision_fence,
        governance_revision_ref=evidence.governance_revision_ref,
        safety_epoch=evidence.safety_epoch,
        candidate_guard_ref=recomputed_candidate_ref,
        dataset_manifest_sha256=evidence.dataset_manifest_sha256,
        required_case_set_hash=expected_case_set_hash,
        case_guard_bindings=evidence.case_guard_bindings,
    )
    if evidence.guard_coverage_manifest_hash != expected_coverage_hash:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            safe_path="guard_coverage_manifest_hash",
        )


def validate_evaluation_guard_evidence(
    evidence: EvaluationGuardEvidence,
    *,
    dataset: ValidatedDataset,
    required_partitions: Sequence[Partition | str],
) -> None:
    """Independently validate the structural and cryptographic integrity of an evidence artifact."""
    _verify_evidence_schema(evidence)
    _verify_evidence_dataset_provenance(evidence, dataset)
    expected_case_ids, expected_case_set_hash = _verify_evidence_case_set(evidence, dataset, required_partitions)
    candidate_ref = _recompute_evidence_candidate_ref(evidence)
    _verify_evidence_case_bindings_and_coverage(evidence, expected_case_ids, expected_case_set_hash, candidate_ref)


# ---------------------------------------------------------------------------
# Run Binding Validator
# ---------------------------------------------------------------------------


def validate_run_guard_binding(
    run: RagEvaluationRun,
    evidence: EvaluationGuardEvidence,
) -> None:
    """Verify exact match between RagEvaluationRun and EvaluationGuardEvidence."""
    if run.run_id != evidence.evaluation_run_id:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            safe_path="run.run_id",
        )
    if run.candidate_bundle_id != evidence.candidate_bundle_id:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            safe_path="run.candidate_bundle_id",
        )
    if run.candidate_bundle_manifest_hash != evidence.candidate_bundle_manifest_hash:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            safe_path="run.candidate_bundle_manifest_hash",
        )
    if run.candidate_guard_decision_id != evidence.candidate_guard_decision_id:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            safe_path="run.candidate_guard_decision_id",
        )
    if run.candidate_guard_decision != "PASS":
        raise EvaluationValidationError(
            EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
            safe_path="run.candidate_guard_decision",
        )
    if run.required_case_guard_coverage_manifest_hash != evidence.guard_coverage_manifest_hash:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            safe_path="run.required_case_guard_coverage_manifest_hash",
        )


# ---------------------------------------------------------------------------
# Standalone Artifact Loader
# ---------------------------------------------------------------------------


def load_evaluation_guard_evidence(path: Path) -> EvaluationGuardEvidence:
    """Safely load standalone EvaluationGuardEvidence artifact from disk."""
    return load_json_object(path, EvaluationGuardEvidence)
