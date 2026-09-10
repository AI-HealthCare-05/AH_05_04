from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import BaseModel

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import load_json_object
from ai_worker.tasks.evaluation.release_gate import MetricRequirement, ReleaseGatePolicy
from ai_worker.tasks.evaluation.schemas.common import ImmutableReference
from ai_worker.tasks.evaluation.schemas.policy import ComparisonPolicy, EvaluationPolicy, EvaluationProfile
from ai_worker.tasks.evaluation.schemas.policy_v1_2 import EvaluationPolicyV12, EvaluationProfileV12


def _load_versioned[T: BaseModel](path: Path, models: tuple[type[T], ...]) -> T:
    last_error: EvaluationValidationError | None = None
    for model in models:
        try:
            return load_json_object(path, model)
        except EvaluationValidationError as error:
            last_error = error
            if error.code not in {
                EvaluationErrorCode.SCHEMA_INVALID,
                EvaluationErrorCode.MANIFEST_INVALID,
            }:
                raise
    raise last_error or EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)


def _verify_self_hash(model: BaseModel, field: str) -> None:
    payload = cast(dict[str, JsonValue], model.model_dump(mode="json"))
    expected = cast(str, payload[field])
    actual = canonical_sha256(payload, excluded_top_level_keys=frozenset({field}))
    if actual != expected:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)


def _reference(identifier: str, version: str, digest: str) -> ImmutableReference:
    return ImmutableReference(id=identifier, version=version, hash=digest)


def load_release_policy(
    evaluation_policy_path: Path,
    evaluation_profile_path: Path,
    comparison_policy_path: Path,
    *,
    paired_comparison_receipt_id: str | None = None,
    required_case_ids: tuple[str, ...] = (),
) -> ReleaseGatePolicy:
    """Load and exact-bind the existing versioned Evaluation policy graph for release gating."""

    policy = _load_versioned(evaluation_policy_path, (EvaluationPolicy, EvaluationPolicyV12))
    profile = _load_versioned(evaluation_profile_path, (EvaluationProfile, EvaluationProfileV12))
    comparison = load_json_object(comparison_policy_path, ComparisonPolicy)
    for model, field in (
        (policy, "evaluation_policy_hash"),
        (profile, "evaluation_profile_hash"),
        (comparison, "comparison_policy_hash"),
    ):
        _verify_self_hash(model, field)

    policy_ref = _reference(
        policy.evaluation_policy_id,
        policy.evaluation_policy_version,
        policy.evaluation_policy_hash,
    )
    profile_ref = _reference(
        profile.evaluation_profile_id,
        profile.evaluation_profile_version,
        profile.evaluation_profile_hash,
    )
    comparison_ref = _reference(
        comparison.comparison_policy_id,
        comparison.comparison_policy_version,
        comparison.comparison_policy_hash,
    )
    if policy.evaluation_profile_ref.reference != profile_ref:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
    if policy.comparison_policy_ref.reference != comparison_ref:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
    policy_suites = tuple(item.reference for item in policy.required_suite_refs)
    if policy_suites != profile.required_suite_refs:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    policy_gates = tuple(item.reference for item in policy.required_gate_refs)
    if policy_gates != profile.required_gate_refs:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)

    required_metrics = tuple(
        MetricRequirement(
            metric_id=scope.metric_id,
            metric_version=scope.metric_version,
            partition=scope.partition,
            slice_id=scope.slice_id,
            requirement_hash=canonical_sha256(cast(JsonValue, scope.model_dump(mode="json"))),
            unit_of_analysis=scope.unit_of_analysis,
            estimator_id=scope.estimator_id,
            estimator_version=scope.estimator_version,
            minimum_case_count=scope.minimum_case_count,
            independence_unit=scope.independence_unit,
            cluster_dimension=None if scope.cluster_dimension is None else scope.cluster_dimension.value,
            minimum_independent_group_count=scope.minimum_independent_group_count,
            threshold=scope.threshold,
            decision_basis=scope.decision_basis,
            ci_method_id=scope.ci_method_id,
            ci_method_version=scope.ci_method_version,
            ci_level=cast(str | None, dict(scope.ci_parameters).get("level")),
            ci_sidedness=cast(str | None, dict(scope.ci_parameters).get("sidedness")),
        )
        for scope in comparison.scopes
        if scope.required
    )
    if paired_comparison_receipt_id is not None and paired_comparison_receipt_id not in {
        reference.id for reference in policy_gates
    }:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    return ReleaseGatePolicy(
        evaluation_policy_ref=policy_ref,
        evaluation_profile_ref=profile_ref,
        comparison_policy_ref=comparison_ref,
        runtime_eligible=profile.runtime_eligible,
        required_experiment_types=profile.required_experiment_types,
        required_partitions=profile.required_partitions,
        required_metrics=required_metrics,
        required_suites=policy_suites,
        required_receipts=policy_gates,
        paired_comparison_receipt_id=paired_comparison_receipt_id,
        required_case_ids=required_case_ids,
        controlled_variable_keys=comparison.controlled_variable_keys,
        required_scope_manifest_hash=policy.member_manifest_hash,
    )
