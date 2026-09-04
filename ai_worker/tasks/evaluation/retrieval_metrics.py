from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import TypedDict, cast

from ai_worker.tasks.evaluation.loaders import EvaluationCaseContract, ValidatedDataset
from ai_worker.tasks.evaluation.schemas.artifacts import CaseResult, MetricResult, MetricResults
from ai_worker.tasks.evaluation.schemas.common import DecisionStatus, ExecutionStatus, Partition, TaskType
from ai_worker.tasks.evaluation.schemas.policy import ComparisonScope

_SIX_PLACES = Decimal("0.000001")
_SUPPORTED_METRICS = frozenset({"MRR", "NDCG_AT_5", "NO_HIT_RATE", "PRECISION_AT_5", "RECALL_AT_5"})


@dataclass(frozen=True, slots=True)
class RetrievalObservation:
    case_id: str
    slice_ids: tuple[str, ...]
    independent_group_id: str
    required_ids: tuple[str, ...]
    relevant_ids: tuple[str, ...]
    ranked_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AggregatedMetric:
    value: Decimal
    numerator: int
    denominator: int
    reason_code: str | None


class MetricResultCalculatedFields(TypedDict):
    execution_status: ExecutionStatus
    decision_status: DecisionStatus
    sample_case_count: int
    sample_independent_group_count: int
    numerator: int
    denominator: int
    metric_value: str
    reason_code: str | None


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


def _quantize_six(value: Decimal) -> Decimal:
    return value.quantize(_SIX_PLACES, rounding=ROUND_HALF_EVEN)


def _canonical_decimal(value: Decimal) -> str:
    quantized = _quantize_six(value)
    if quantized == 0:
        return "0"
    return format(quantized, "f").rstrip("0").rstrip(".")


def _validate_ranked_ids(observation: RetrievalObservation) -> None:
    if len(observation.ranked_ids) != len(set(observation.ranked_ids)):
        raise ValueError("ranked evidence ids must be unique")


def recall_at_k(observation: RetrievalObservation, k: int = 5) -> Decimal:
    _validate_ranked_ids(observation)
    required = set(observation.required_ids)
    if not required:
        raise ZeroDivisionError("required evidence denominator is zero")
    hits = required.intersection(observation.ranked_ids[:k])
    return _quantize_six(Decimal(len(hits)) / Decimal(len(required)))


def precision_at_k(observation: RetrievalObservation, k: int = 5) -> Decimal:
    _validate_ranked_ids(observation)
    relevant = set(observation.relevant_ids)
    hits = relevant.intersection(observation.ranked_ids[:k])
    return _quantize_six(Decimal(len(hits)) / Decimal(k))


def reciprocal_rank(observation: RetrievalObservation, k: int = 5) -> Decimal:
    _validate_ranked_ids(observation)
    relevant = set(observation.relevant_ids)
    rank = next(
        (index for index, item in enumerate(observation.ranked_ids[:k], 1) if item in relevant),
        None,
    )
    return Decimal("0.000000") if rank is None else _quantize_six(Decimal(1) / Decimal(rank))


def ndcg_at_k(observation: RetrievalObservation, k: int = 5) -> Decimal:
    _validate_ranked_ids(observation)
    relevant = set(observation.relevant_ids)
    dcg = sum(
        (
            Decimal(1) / Decimal(str(math.log2(rank + 1)))
            for rank, item in enumerate(observation.ranked_ids[:k], 1)
            if item in relevant
        ),
        Decimal(0),
    )
    ideal_count = min(len(relevant), k)
    idcg = sum(
        (Decimal(1) / Decimal(str(math.log2(rank + 1))) for rank in range(1, ideal_count + 1)),
        Decimal(0),
    )
    return Decimal("0.000000") if idcg == 0 else _quantize_six(dcg / idcg)


def no_hit(observation: RetrievalObservation, k: int = 5) -> Decimal:
    _validate_ranked_ids(observation)
    relevant = set(observation.relevant_ids)
    return Decimal("0.000000") if relevant.intersection(observation.ranked_ids[:k]) else Decimal("1.000000")


def metric_scores(observation: RetrievalObservation, *, k: int = 5) -> dict[str, Decimal] | None:
    """Compatibility helper for callers that treat a zero Recall denominator as absent."""

    try:
        recall = recall_at_k(observation, k)
    except ZeroDivisionError:
        return None
    return {
        "RECALL_AT_5": recall,
        "PRECISION_AT_5": precision_at_k(observation, k),
        "MRR": reciprocal_rank(observation, k),
        "NDCG_AT_5": ndcg_at_k(observation, k),
        "NO_HIT_RATE": no_hit(observation, k),
    }


def _percentile_bounds(estimates: Sequence[Decimal], level: Decimal) -> tuple[Decimal, Decimal]:
    if not estimates:
        raise ValueError("percentile estimates must not be empty")
    if not Decimal(0) < level < Decimal(1):
        raise ValueError("confidence level must be between zero and one")
    alpha = (Decimal(1) - level) / Decimal(2)
    last_index = Decimal(len(estimates) - 1)
    lower_index = int((last_index * alpha).to_integral_value(rounding=ROUND_FLOOR))
    upper_index = int((last_index * (Decimal(1) - alpha)).to_integral_value(rounding=ROUND_CEILING))
    return _quantize_six(estimates[lower_index]), _quantize_six(estimates[upper_index])


def percentile_group_bootstrap_ci(
    group_scores: Mapping[str, tuple[Decimal, ...]],
    *,
    seed: int,
    iterations: int,
    level: Decimal,
) -> tuple[Decimal, Decimal]:
    if iterations <= 0:
        raise ValueError("bootstrap iterations must be positive")
    if not group_scores or any(not scores for scores in group_scores.values()):
        raise ValueError("bootstrap groups must contain scores")
    rng = random.Random(seed)
    group_ids = tuple(sorted(group_scores, key=lambda value: value.encode("utf-16-be")))
    estimates: list[Decimal] = []
    for _ in range(iterations):
        sampled = [group_ids[rng.randrange(len(group_ids))] for _ in group_ids]
        scores = [score for group_id in sampled for score in group_scores[group_id]]
        estimates.append(sum(scores, Decimal(0)) / Decimal(len(scores)))
    estimates.sort()
    return _percentile_bounds(estimates, level)


def aggregate_metric_scores(
    score_sets: list[dict[str, Decimal] | None],
    *,
    minimum_case_count: int,
) -> dict[str, AggregatedMetric]:
    """Compatibility aggregate retained until the artifact builder adopts the kernel."""

    completed = [scores for scores in score_sets if scores is not None]
    if not completed:
        return {}
    case_count = len(completed)
    reason = "MINIMUM_CASE_COUNT_NOT_MET" if case_count < minimum_case_count else None
    aggregates: dict[str, AggregatedMetric] = {}
    for metric_id in completed[0]:
        values = [scores[metric_id] for scores in completed]
        if metric_id == "PRECISION_AT_5":
            numerator = int(sum(values, Decimal(0)) * Decimal(5))
            denominator = case_count * 5
        elif metric_id in {"RECALL_AT_5", "NO_HIT_RATE"}:
            numerator = int(sum(values, Decimal(0)))
            denominator = case_count
        else:
            numerator = sum(value > 0 for value in values)
            denominator = case_count
        aggregates[metric_id] = AggregatedMetric(
            value=_quantize_six(sum(values, Decimal(0)) / Decimal(case_count)),
            numerator=numerator,
            denominator=denominator,
            reason_code=reason,
        )
    return aggregates


def metric_result_fields(
    aggregate: AggregatedMetric,
    *,
    sample_group_count: int,
) -> MetricResultCalculatedFields:
    """Compatibility projection retained for the existing artifact builder."""

    return {
        "execution_status": ExecutionStatus.COMPLETED,
        "decision_status": (DecisionStatus.INCONCLUSIVE if aggregate.reason_code else DecisionStatus.NOT_APPLICABLE),
        "sample_case_count": sample_group_count,
        "sample_independent_group_count": sample_group_count,
        "numerator": aggregate.numerator,
        "denominator": aggregate.denominator,
        "metric_value": _canonical_decimal(aggregate.value),
        "reason_code": aggregate.reason_code,
    }


def observations_from_case_results(
    gold_by_case: dict[str, tuple[tuple[str, ...], tuple[str, ...]]],
    ranked_by_case: dict[str, tuple[str, ...]],
) -> tuple[RetrievalObservation, ...]:
    """Compatibility binder used by the existing artifact builder."""

    if set(gold_by_case) != set(ranked_by_case):
        raise ValueError("gold and ranked case sets must match")
    return tuple(
        RetrievalObservation(
            case_id=case_id,
            slice_ids=("ALL",),
            independent_group_id=case_id,
            required_ids=gold_by_case[case_id][0],
            relevant_ids=gold_by_case[case_id][1],
            ranked_ids=ranked_by_case[case_id],
        )
        for case_id in sorted(gold_by_case, key=lambda value: value.encode("utf-16-be"))
    )


def _scope_fields(scope: ComparisonScope) -> MetricScopeFields:
    ci_parameters = dict(scope.ci_parameters)
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
        "ci_level": cast(str | None, ci_parameters.get("level")),
        "ci_sidedness": cast(str | None, ci_parameters.get("sidedness")),
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


def _case_matches_scope(case: EvaluationCaseContract, scope: ComparisonScope) -> bool:
    return case.partition is scope.partition and (scope.slice_id == "ALL" or scope.slice_id in case.slice_ids)


def _observations_for_scope(
    dataset: ValidatedDataset,
    results_by_case: Mapping[str, CaseResult],
    scope: ComparisonScope,
) -> tuple[RetrievalObservation, ...]:
    observations: list[RetrievalObservation] = []
    cluster_dimension = None if scope.cluster_dimension is None else scope.cluster_dimension.value
    for case in dataset.cases:
        if case.task_type is not TaskType.RETRIEVAL or not _case_matches_scope(case, scope):
            continue
        result = results_by_case[case.case_id]
        if cluster_dimension is None:
            independent_group_id = case.case_id
        else:
            independent_group_id = cast(str, getattr(case.leakage_group_ids, cluster_dimension))
        observations.append(
            RetrievalObservation(
                case_id=case.case_id,
                slice_ids=tuple(case.slice_ids),
                independent_group_id=independent_group_id,
                required_ids=tuple(case.expected.required_evidence_refs or ()),
                relevant_ids=tuple(case.expected.relevant_evidence_refs or ()),
                ranked_ids=tuple(result.retrieved_evidence_ids or ()),
            )
        )
    return tuple(observations)


def _input_status(dataset: ValidatedDataset, case_results: tuple[CaseResult, ...]) -> ExecutionStatus | None:
    retrieval_cases = {case.case_id: case for case in dataset.cases if case.task_type is TaskType.RETRIEVAL}
    result_ids = [result.case_id for result in case_results]
    if len(result_ids) != len(set(result_ids)) or set(result_ids) != set(retrieval_cases):
        return ExecutionStatus.INVALID
    run_ids = {result.run_id for result in case_results}
    for result in case_results:
        case = retrieval_cases[result.case_id]
        if (
            result.task_type is not TaskType.RETRIEVAL
            or result.dataset_code != case.dataset_code
            or result.dataset_version != case.dataset_version
            or result.partition is not case.partition
            or len(result.retrieved_evidence_ids or ()) != len(set(result.retrieved_evidence_ids or ()))
        ):
            return ExecutionStatus.INVALID
    if len(run_ids) != 1:
        return ExecutionStatus.INVALID
    if any(result.execution_status is not ExecutionStatus.COMPLETED for result in case_results):
        return ExecutionStatus.ERROR
    return None


def _score(metric_id: str, observation: RetrievalObservation) -> Decimal:
    estimator = {
        "MRR": reciprocal_rank,
        "NDCG_AT_5": ndcg_at_k,
        "NO_HIT_RATE": no_hit,
        "PRECISION_AT_5": precision_at_k,
        "RECALL_AT_5": recall_at_k,
    }[metric_id]
    return estimator(observation)


def _ratio_counts(
    metric_id: str, observations: Sequence[RetrievalObservation], scores: Sequence[Decimal]
) -> tuple[int, int]:
    if metric_id == "RECALL_AT_5":
        numerator = sum(len(set(item.required_ids).intersection(item.ranked_ids[:5])) for item in observations)
        denominator = sum(len(set(item.required_ids)) for item in observations)
        return numerator, denominator
    if metric_id == "PRECISION_AT_5":
        numerator = sum(len(set(item.relevant_ids).intersection(item.ranked_ids[:5])) for item in observations)
        return numerator, len(observations) * 5
    if metric_id == "NO_HIT_RATE":
        return sum(score != 0 for score in scores), len(observations)
    return sum(score != 0 for score in scores), len(observations)


def _completed_metric(scope: ComparisonScope, observations: tuple[RetrievalObservation, ...]) -> MetricResult:
    group_count = len({item.independent_group_id for item in observations})
    zero_denominator = scope.metric_id == "RECALL_AT_5" and any(not item.required_ids for item in observations)
    if zero_denominator:
        return MetricResult(
            **_scope_fields(scope),
            execution_status=ExecutionStatus.COMPLETED,
            decision_status=DecisionStatus.INCONCLUSIVE,
            sample_case_count=len(observations),
            sample_independent_group_count=group_count,
            numerator=0,
            denominator=0,
            metric_value=None,
            ci_lower=None,
            ci_upper=None,
            reason_code="ZERO_DENOMINATOR",
        )

    scores = tuple(_score(scope.metric_id, item) for item in observations)
    numerator, denominator = _ratio_counts(scope.metric_id, observations, scores)
    value = None if not scores else _quantize_six(sum(scores, Decimal(0)) / Decimal(len(scores)))
    reason_code: str | None = None
    if len(observations) < scope.minimum_case_count:
        reason_code = "MINIMUM_CASE_COUNT_NOT_MET"
    elif scope.minimum_independent_group_count is not None and group_count < scope.minimum_independent_group_count:
        reason_code = "MINIMUM_INDEPENDENT_GROUP_COUNT_NOT_MET"

    ci_lower: Decimal | None = None
    ci_upper: Decimal | None = None
    if scores:
        parameters = dict(scope.ci_parameters)
        grouped: defaultdict[str, list[Decimal]] = defaultdict(list)
        for observation, score in zip(observations, scores, strict=True):
            grouped[observation.independent_group_id].append(score)
        ci_lower, ci_upper = percentile_group_bootstrap_ci(
            {group_id: tuple(values) for group_id, values in grouped.items()},
            seed=cast(int, scope.seed),
            iterations=cast(int, parameters["iterations"]),
            level=Decimal(cast(str, parameters["level"])),
        )
    decision_status = DecisionStatus.INCONCLUSIVE if reason_code else DecisionStatus.NOT_APPLICABLE
    return MetricResult(
        **_scope_fields(scope),
        execution_status=ExecutionStatus.COMPLETED,
        decision_status=decision_status,
        sample_case_count=len(observations),
        sample_independent_group_count=group_count,
        numerator=numerator,
        denominator=denominator,
        metric_value=None if value is None else _canonical_decimal(value),
        ci_lower=None if ci_lower is None else _canonical_decimal(ci_lower),
        ci_upper=None if ci_upper is None else _canonical_decimal(ci_upper),
        reason_code=reason_code,
    )


def build_retrieval_metrics(
    dataset: ValidatedDataset,
    case_results: tuple[CaseResult, ...],
) -> MetricResults:
    if not case_results:
        raise ValueError("retrieval metrics require case results to bind run_id")
    input_status = _input_status(dataset, case_results)
    results_by_case = {result.case_id: result for result in case_results}
    metrics: list[MetricResult] = []
    for scope in dataset.comparison_policy.scopes:
        if scope.metric_id not in _SUPPORTED_METRICS:
            metrics.append(_incomplete_metric(scope, ExecutionStatus.NOT_IMPLEMENTED))
        elif input_status is not None:
            metrics.append(_incomplete_metric(scope, input_status))
        else:
            metrics.append(_completed_metric(scope, _observations_for_scope(dataset, results_by_case, scope)))
    metrics.sort(key=lambda item: item.sort_key)
    return MetricResults(
        schema_id="rag-eval.metrics",
        schema_version="1.0.0",
        run_id=case_results[0].run_id,
        metrics=tuple(metrics),
    )
