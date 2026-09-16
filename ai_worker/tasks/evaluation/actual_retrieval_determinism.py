"""Compare two Actual Retrieval Run Bundles for retrieval semantic determinism.

The global :func:`ai_worker.tasks.evaluation.manifest.semantic_content_hash`
contract keeps ``latency_ms`` inside the projected case record, so two identical
Actual Retrieval executions can differ on that hash for timing reasons alone.
This module leaves the global contract untouched and adds a narrower comparator
that separates retrieval semantics, metrics, and failures from execution-volatile
observations such as latency.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.comparison import LoadedRunBundle
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.schemas.artifacts import (
    CaseResult,
    FailureRecord,
    MetricResult,
    RagEvaluationRun,
)
from ai_worker.tasks.evaluation.schemas.common import Partition

_CONTROLLED_RUN_FIELDS = (
    "experiment_id",
    "variant_id",
    "dataset_code",
    "dataset_version",
    "dataset_manifest_sha256",
    "resource_set_hash",
    "evidence_mapping_manifest_sha256",
    "resolved_evaluation_config_hash",
    "retrieval_variant_manifest_hash",
    "model_config_hash",
    "upstream_contract_manifest_hash",
    "partition_manifest_hash",
)


def _refuse() -> EvaluationValidationError:
    return EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)


@dataclass(frozen=True, slots=True)
class LatencyObservation:
    """Execution-volatile latency summary kept out of the equality projections."""

    count: int
    minimum: int | None
    median: int | None
    p95: int | None
    maximum: int | None


@dataclass(frozen=True, slots=True)
class ActualRetrievalDeterminismReport:
    """Separated determinism verdicts for one same-variant Run Bundle pair."""

    variant_id: str
    run_ids: tuple[str, str]
    compared_case_count: int
    retrieval_semantic_equal: bool
    retrieval_semantic_hashes: tuple[str, str]
    mismatched_case_ids: tuple[str, ...]
    metric_equal: bool
    metric_hashes: tuple[str, str]
    mismatched_metric_keys: tuple[str, ...]
    failure_equal: bool
    failure_hashes: tuple[str, str]
    latency_observations: tuple[LatencyObservation, LatencyObservation]


def stable_case_projection(case: CaseResult) -> JsonValue:
    """Project one case onto the retrieval-semantic fields only."""

    return cast(
        JsonValue,
        {
            "case_id": case.case_id,
            "input_sha256": case.input_sha256,
            "execution_status": case.execution_status.value,
            "decision_status": None if case.decision_status is None else case.decision_status.value,
            "failure_codes": list(case.failure_codes),
            "retrieved_evidence_ids": list(case.retrieved_evidence_ids or ()),
            "selected_evidence_ids": list(case.selected_evidence_ids or ()),
            "actual_retrieval_invocation": case.actual_retrieval_invocation,
        },
    )


def stable_metric_projection(metric: MetricResult) -> JsonValue:
    """Project one metric onto the fields that must repeat across executions."""

    return cast(
        JsonValue,
        {
            "metric_id": metric.metric_id,
            "metric_version": metric.metric_version,
            "partition": metric.partition.value,
            "slice_id": metric.slice_id,
            "sample_case_count": metric.sample_case_count,
            "sample_independent_group_count": metric.sample_independent_group_count,
            "numerator": metric.numerator,
            "denominator": metric.denominator,
            "metric_value": None if metric.metric_value is None else str(metric.metric_value),
            "ci_lower": None if metric.ci_lower is None else str(metric.ci_lower),
            "ci_upper": None if metric.ci_upper is None else str(metric.ci_upper),
            "execution_status": metric.execution_status.value,
            "decision_status": None if metric.decision_status is None else metric.decision_status.value,
            "reason_code": metric.reason_code,
        },
    )


def stable_failure_projection(failure: FailureRecord) -> JsonValue:
    """Project one failure record without its execution timestamp."""

    return cast(
        JsonValue,
        {
            "case_id": failure.case_id,
            "failure_code": failure.failure_code,
            "failure_stage": failure.failure_stage,
            "root_cause_code": failure.root_cause_code,
        },
    )


def _metric_key(metric: MetricResult) -> str:
    return f"{metric.metric_id}@{metric.metric_version}|{metric.partition.value}|{metric.slice_id}"


def latency_observation(cases: Sequence[CaseResult]) -> LatencyObservation:
    """Summarise observed case latency without folding it into any equality check."""

    samples = sorted(case.latency_ms for case in cases if case.latency_ms is not None)
    if not samples:
        return LatencyObservation(count=0, minimum=None, median=None, p95=None, maximum=None)
    index = min(len(samples) - 1, max(0, -(-95 * len(samples) // 100) - 1))
    return LatencyObservation(
        count=len(samples),
        minimum=samples[0],
        median=int(statistics.median(samples)),
        p95=samples[index],
        maximum=samples[-1],
    )


def _require_same_controlled_state(first: RagEvaluationRun, second: RagEvaluationRun) -> None:
    for field in _CONTROLLED_RUN_FIELDS:
        if getattr(first, field) != getattr(second, field):
            raise _refuse()
    if first.run_id == second.run_id:
        raise _refuse()
    for run in (first, second):
        if tuple(partition for partition in run.evaluated_partitions) != (Partition.DEV,):
            raise _refuse()


def compare_actual_retrieval_runs(
    first: LoadedRunBundle,
    second: LoadedRunBundle,
    *,
    knowledge_index_refs: tuple[Mapping[str, str], Mapping[str, str]] | None = None,
) -> ActualRetrievalDeterminismReport:
    """Compare a same-variant Actual Retrieval pair, refusing incomparable runs.

    Raises ``EvaluationValidationError`` when the two runs do not share the
    controlled execution state that makes a determinism claim meaningful.
    """

    _require_same_controlled_state(first.run, second.run)
    if knowledge_index_refs is not None and dict(knowledge_index_refs[0]) != dict(knowledge_index_refs[1]):
        raise _refuse()

    first_cases = {case.case_id: case for case in first.cases}
    second_cases = {case.case_id: case for case in second.cases}
    if not first_cases or first_cases.keys() != second_cases.keys():
        raise _refuse()

    mismatched_cases = tuple(
        case_id
        for case_id in sorted(first_cases)
        if stable_case_projection(first_cases[case_id]) != stable_case_projection(second_cases[case_id])
    )
    case_hashes = tuple(
        canonical_sha256(
            cast(JsonValue, [stable_case_projection(case) for case in sorted(cases, key=lambda c: c.case_id)])
        )
        for cases in (first.cases, second.cases)
    )

    first_metrics = {_metric_key(metric): metric for metric in first.metrics.metrics}
    second_metrics = {_metric_key(metric): metric for metric in second.metrics.metrics}
    mismatched_metrics = tuple(
        sorted(
            set(first_metrics) ^ set(second_metrics)
            | {
                key
                for key in set(first_metrics) & set(second_metrics)
                if stable_metric_projection(first_metrics[key]) != stable_metric_projection(second_metrics[key])
            }
        )
    )
    metric_hashes = tuple(
        canonical_sha256(cast(JsonValue, [stable_metric_projection(metrics[key]) for key in sorted(metrics)]))
        for metrics in (first_metrics, second_metrics)
    )

    failure_hashes = tuple(
        canonical_sha256(
            cast(
                JsonValue,
                sorted(
                    (stable_failure_projection(failure) for failure in failures),
                    key=lambda record: canonical_sha256(record),
                ),
            )
        )
        for failures in (first.failures, second.failures)
    )

    return ActualRetrievalDeterminismReport(
        variant_id=first.run.variant_id,
        run_ids=(first.run.run_id, second.run.run_id),
        compared_case_count=len(first_cases),
        retrieval_semantic_equal=not mismatched_cases,
        retrieval_semantic_hashes=(case_hashes[0], case_hashes[1]),
        mismatched_case_ids=mismatched_cases,
        metric_equal=not mismatched_metrics,
        metric_hashes=(metric_hashes[0], metric_hashes[1]),
        mismatched_metric_keys=mismatched_metrics,
        failure_equal=failure_hashes[0] == failure_hashes[1],
        failure_hashes=(failure_hashes[0], failure_hashes[1]),
        latency_observations=(latency_observation(first.cases), latency_observation(second.cases)),
    )
