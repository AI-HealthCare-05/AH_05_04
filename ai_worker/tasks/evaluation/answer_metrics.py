from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, TypedDict, cast

from ai_worker.tasks.evaluation.answer_judgment import ValidatedAnswerJudgments, ValidatedCaseJudgment
from ai_worker.tasks.evaluation.loaders import EvaluationCaseContract, ValidatedDataset
from ai_worker.tasks.evaluation.metric_support import (
    RatioContribution,
    canonical_ratio,
    percentile_cluster_bootstrap_ratio_ci,
)
from ai_worker.tasks.evaluation.schemas.answer_quality_v1 import (
    AnswerClaimCorrectnessLabel,
    AnswerRelevanceLabel,
)
from ai_worker.tasks.evaluation.schemas.artifacts import CaseResult, MetricResult, MetricResults
from ai_worker.tasks.evaluation.schemas.common import DecisionStatus, ExecutionStatus, Partition, TaskType
from ai_worker.tasks.evaluation.schemas.policy import ComparisonScope

_STRUCTURED_METRICS = frozenset({"COMPLETENESS", "REQUIRED_CLAIM_RECALL"})
_HUMAN_METRICS = frozenset({"ANSWER_CORRECTNESS", "RELEVANCE"})
_METRIC_UNITS = {
    "ANSWER_CORRECTNESS": "CLAIM",
    "COMPLETENESS": "EXPECTED_SECTION",
    "RELEVANCE": "CASE",
    "REQUIRED_CLAIM_RECALL": "REQUIRED_CLAIM",
}
_INPUT_STATUS_PRIORITY = {
    ExecutionStatus.INVALID: 0,
    ExecutionStatus.ERROR: 1,
    ExecutionStatus.NOT_IMPLEMENTED: 2,
    ExecutionStatus.NOT_EVALUATED: 3,
}


class MetricScopeFields(TypedDict):
    metric_id: str
    metric_version: str
    partition: Partition
    slice_id: str
    required: bool
    unit_of_analysis: str
    estimator_id: str
    estimator_version: str
    independence_unit: str | None
    cluster_dimension: str | None
    ci_method_id: str
    ci_method_version: str
    ci_level: str | None
    ci_sidedness: str | None
    threshold: str


def _scope_fields(scope: ComparisonScope) -> MetricScopeFields:
    parameters = dict(scope.ci_parameters)
    ci_level = parameters.get("level")
    ci_sidedness = parameters.get("sidedness")
    return {
        "metric_id": scope.metric_id,
        "metric_version": scope.metric_version,
        "partition": scope.partition,
        "slice_id": scope.slice_id,
        "required": scope.required,
        "unit_of_analysis": scope.unit_of_analysis,
        "estimator_id": scope.estimator_id,
        "estimator_version": scope.estimator_version,
        "independence_unit": scope.independence_unit,
        "cluster_dimension": None if scope.cluster_dimension is None else scope.cluster_dimension.value,
        "ci_method_id": scope.ci_method_id,
        "ci_method_version": scope.ci_method_version,
        "ci_level": ci_level if isinstance(ci_level, str) else None,
        "ci_sidedness": ci_sidedness if isinstance(ci_sidedness, str) else None,
        "threshold": scope.threshold,
    }


def _incomplete_metric(scope: ComparisonScope, status: ExecutionStatus) -> MetricResult:
    return MetricResult(
        **_scope_fields(scope),
        execution_status=status,
        decision_status=None,
        sample_case_count=None,
        sample_independent_group_count=None,
        numerator=None,
        denominator=None,
        metric_value=None,
        ci_lower=None,
        ci_upper=None,
        reason_code=None,
    )


def _matches_scope(case: EvaluationCaseContract, scope: ComparisonScope) -> bool:
    return case.partition is scope.partition and (scope.slice_id == "ALL" or scope.slice_id in case.slice_ids)


def _algorithm_signature_supported(scope: ComparisonScope) -> bool:
    parameters = dict(scope.ci_parameters)
    iterations = parameters.get("iterations")
    return (
        scope.metric_id in _METRIC_UNITS
        and scope.metric_version == "1.0.0"
        and scope.partition is Partition.DEV
        and scope.unit_of_analysis == _METRIC_UNITS[scope.metric_id]
        and not scope.required
        and scope.estimator_id == "MICRO_RATIO"
        and scope.estimator_version == "1.0.0"
        and scope.independence_unit is not None
        and scope.cluster_dimension is not None
        and scope.ci_method_id == "PERCENTILE_CLUSTER_BOOTSTRAP"
        and scope.ci_method_version == "1.0.0"
        and set(parameters) == {"iterations", "level", "sidedness"}
        and type(iterations) is int
        and iterations > 0
        and parameters.get("level") == "0.95"
        and parameters.get("sidedness") == "TWO_SIDED"
        and type(scope.seed) is int
        and scope.decision_basis == "DIAGNOSTIC_ONLY"
        and scope.threshold == "0"
    )


def _run_integrity_status(
    answer_cases: tuple[EvaluationCaseContract, ...],
    case_results: tuple[CaseResult, ...],
    expected_run_id: str,
    expected_input_sha256_by_case: Mapping[str, str],
) -> ExecutionStatus | None:
    cases_by_id = {case.case_id: case for case in answer_cases}
    if set(expected_input_sha256_by_case) != set(cases_by_id):
        return ExecutionStatus.INVALID
    result_ids = [result.case_id for result in case_results]
    if len(result_ids) != len(set(result_ids)) or set(result_ids) != set(cases_by_id):
        return ExecutionStatus.INVALID
    if any(result.run_id != expected_run_id for result in case_results):
        return ExecutionStatus.INVALID
    for result in case_results:
        case = cases_by_id[result.case_id]
        if (
            result.task_type is not TaskType.ANSWER_QUALITY
            or result.dataset_code != case.dataset_code
            or result.dataset_version != case.dataset_version
            or result.partition is not case.partition
            or result.input_sha256 != expected_input_sha256_by_case[result.case_id]
        ):
            return ExecutionStatus.INVALID
    return None


def _scope_execution_status(
    cases: tuple[EvaluationCaseContract, ...],
    results_by_case: Mapping[str, CaseResult],
) -> ExecutionStatus | None:
    incomplete_statuses: set[ExecutionStatus] = set()
    for case in cases:
        status = results_by_case[case.case_id].execution_status
        if not isinstance(status, ExecutionStatus):
            return ExecutionStatus.INVALID
        if status is not ExecutionStatus.COMPLETED:
            incomplete_statuses.add(status)
    if incomplete_statuses:
        return min(incomplete_statuses, key=_INPUT_STATUS_PRIORITY.__getitem__)
    return None


def _structured_contribution(case: EvaluationCaseContract, result: CaseResult, metric_id: str) -> RatioContribution:
    expected = cast(Any, case.expected)
    if metric_id == "REQUIRED_CLAIM_RECALL":
        required = {claim.claim_id for claim in expected.gold_claims if claim.required}
        return RatioContribution(len(required.intersection(result.actual_claim_ids or ())), len(required))
    expected_sections = set(expected.expected_sections)
    return RatioContribution(
        len(expected_sections.intersection(result.actual_sections or ())),
        len(expected_sections),
    )


def _human_contribution(
    judgment: ValidatedCaseJudgment,
    metric_id: str,
) -> RatioContribution:
    if metric_id == "ANSWER_CORRECTNESS":
        numerator = sum(1 for item in judgment.claim_judgments if item.label == AnswerClaimCorrectnessLabel.CORRECT)
        return RatioContribution(numerator, len(judgment.claim_judgments))
    if metric_id == "RELEVANCE":
        numerator = 1 if judgment.relevance == AnswerRelevanceLabel.RELEVANT else 0
        return RatioContribution(numerator, 1)
    raise AssertionError(f"unexpected human metric: {metric_id}")


def _completed_metric(
    scope: ComparisonScope,
    cases: tuple[EvaluationCaseContract, ...],
    results_by_case: Mapping[str, CaseResult],
    human_judgments: ValidatedAnswerJudgments | None = None,
) -> MetricResult:
    grouped: defaultdict[str, list[RatioContribution]] = defaultdict(list)
    contributions: list[RatioContribution] = []
    for case in cases:
        if scope.metric_id in _HUMAN_METRICS:
            assert human_judgments is not None
            judgment = human_judgments.judgments_by_case[case.case_id]
            contribution = _human_contribution(judgment, scope.metric_id)
        else:
            contribution = _structured_contribution(case, results_by_case[case.case_id], scope.metric_id)
        contributions.append(contribution)
        group_id = case.case_id
        if scope.cluster_dimension is not None:
            group_id = getattr(case.leakage_group_ids, scope.cluster_dimension.value)
        grouped[group_id].append(contribution)

    numerator = sum(item.numerator for item in contributions)
    denominator = sum(item.denominator for item in contributions)
    group_count = len(grouped)
    reason_code: str | None = None
    if denominator == 0:
        reason_code = "ZERO_DENOMINATOR"
    elif len(cases) < scope.minimum_case_count:
        reason_code = "MINIMUM_CASE_COUNT_NOT_MET"
    elif scope.minimum_independent_group_count is not None and group_count < scope.minimum_independent_group_count:
        reason_code = "MINIMUM_INDEPENDENT_GROUP_COUNT_NOT_MET"

    ci_lower: str | None = None
    ci_upper: str | None = None
    metric_value: str | None = None
    if denominator > 0:
        parameters = dict(scope.ci_parameters)
        metric_value = canonical_ratio(numerator, denominator)
        ci_lower, ci_upper = percentile_cluster_bootstrap_ratio_ci(
            {group_id: tuple(values) for group_id, values in grouped.items()},
            seed=cast(int, scope.seed),
            iterations=cast(int, parameters["iterations"]),
            level=Decimal(cast(str, parameters["level"])),
        )
    return MetricResult(
        **_scope_fields(scope),
        execution_status=ExecutionStatus.COMPLETED,
        decision_status=DecisionStatus.INCONCLUSIVE if reason_code else DecisionStatus.NOT_APPLICABLE,
        sample_case_count=len(cases),
        sample_independent_group_count=group_count,
        numerator=numerator,
        denominator=denominator,
        metric_value=metric_value,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        reason_code=reason_code,
    )


def _evaluate_scope_metric(
    scope: ComparisonScope,
    scoped_cases: tuple[EvaluationCaseContract, ...],
    results_by_case: Mapping[str, CaseResult],
    expected_input_sha256_by_case: Mapping[str, str],
    human_judgments: ValidatedAnswerJudgments | None,
) -> MetricResult:
    if scope.metric_id in _HUMAN_METRICS:
        if human_judgments is None:
            return _incomplete_metric(scope, ExecutionStatus.NOT_EVALUATED)
        if not human_judgments.validate_for_scope(
            scoped_cases,
            results_by_case,
            expected_input_sha256_by_case,
        ):
            return _incomplete_metric(scope, ExecutionStatus.INVALID)
        try:
            return _completed_metric(scope, scoped_cases, results_by_case, human_judgments=human_judgments)
        except ValueError as exc:
            if str(exc) != "bootstrap replicate denominator is zero":
                raise
            return _incomplete_metric(scope, ExecutionStatus.NOT_IMPLEMENTED)

    if scope.metric_id not in _STRUCTURED_METRICS:
        return _incomplete_metric(scope, ExecutionStatus.NOT_IMPLEMENTED)

    try:
        return _completed_metric(scope, scoped_cases, results_by_case)
    except ValueError as exc:
        if str(exc) != "bootstrap replicate denominator is zero":
            raise
        return _incomplete_metric(scope, ExecutionStatus.NOT_IMPLEMENTED)


def build_answer_metrics(
    dataset: ValidatedDataset,
    case_results: tuple[CaseResult, ...],
    *,
    expected_run_id: str,
    expected_input_sha256_by_case: Mapping[str, str],
    human_judgments: ValidatedAnswerJudgments | None = None,
) -> MetricResults:
    """Build approved #159 DEV metrics without reading answer text or protected data."""

    answer_cases = tuple(
        case for case in dataset.cases if case.task_type is TaskType.ANSWER_QUALITY and case.partition is Partition.DEV
    )
    run_integrity_status = _run_integrity_status(
        answer_cases,
        case_results,
        expected_run_id,
        expected_input_sha256_by_case,
    )
    results_by_case = {result.case_id: result for result in case_results}
    metrics: list[MetricResult] = []
    for scope in dataset.comparison_policy.scopes:
        if scope.metric_id not in _METRIC_UNITS or not _algorithm_signature_supported(scope):
            metrics.append(_incomplete_metric(scope, ExecutionStatus.NOT_IMPLEMENTED))
            continue
        if run_integrity_status is not None:
            metrics.append(_incomplete_metric(scope, run_integrity_status))
            continue
        scoped_cases = tuple(case for case in answer_cases if _matches_scope(case, scope))
        scope_execution_status = _scope_execution_status(scoped_cases, results_by_case)
        if scope_execution_status is not None:
            metrics.append(_incomplete_metric(scope, scope_execution_status))
            continue
        metrics.append(
            _evaluate_scope_metric(
                scope,
                scoped_cases,
                results_by_case,
                expected_input_sha256_by_case,
                human_judgments,
            )
        )
    metrics.sort(key=lambda item: item.sort_key)
    return MetricResults(
        schema_id="rag-eval.metrics",
        schema_version="1.0.0",
        run_id=expected_run_id,
        metrics=tuple(metrics),
    )
