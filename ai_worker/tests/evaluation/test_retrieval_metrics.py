from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes
from ai_worker.tasks.evaluation.config import RepositoryState, load_dev_execution_request
from ai_worker.tasks.evaluation.loaders import ValidatedDataset, load_dataset
from ai_worker.tasks.evaluation.retrieval_metrics import (
    RetrievalObservation,
    build_retrieval_metrics,
    ndcg_at_k,
    no_hit,
    percentile_group_bootstrap_ci,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from ai_worker.tasks.evaluation.retrieval_replay import build_adapter_registry, load_retrieval_replay
from ai_worker.tasks.evaluation.runner import execute_dev_cases
from ai_worker.tasks.evaluation.schemas.artifacts import CASE_RESULT_ADAPTER, CaseResult, MetricResult, MetricResults
from ai_worker.tasks.evaluation.schemas.common import LeakageAxis

EVALS_ROOT = Path(__file__).parents[3] / "evals"
MANIFEST = EVALS_ROOT / "retrieval/manifests/rag-retrieval-dev-v1.dataset.json"
REPLAY = Path("evals/retrieval/replays/rag-retrieval-dev-v1/ret-l-v1.replay.json")
RUN_ID = "15800000-0000-4000-8000-000000000003"
DATASET = load_dataset(MANIFEST, evals_root=EVALS_ROOT)


def _case_result(
    case_id: str,
    ranked_ids: tuple[str, ...],
    *,
    execution_status: str = "COMPLETED",
) -> CaseResult:
    case = next(item for item in DATASET.cases if item.case_id == case_id)
    completed = execution_status == "COMPLETED"
    return CASE_RESULT_ADAPTER.validate_python(
        {
            "schema_id": "rag-eval.case-result",
            "schema_version": "1.0.0",
            "run_id": RUN_ID,
            "case_id": case.case_id,
            "dataset_code": case.dataset_code,
            "dataset_version": case.dataset_version,
            "task_type": "RETRIEVAL",
            "partition": case.partition.value,
            "input_sha256": case.input_sha256,
            "execution_status": execution_status,
            "decision_status": "N/A" if completed else None,
            "failure_codes": [] if completed else ["SYNTHETIC_RETRIEVAL_FAILURE"],
            "retrieved_evidence_ids": list(ranked_ids),
            "selected_evidence_ids": list(ranked_ids),
            "actual_claim_ids": None,
            "actual_citation_evidence_ids": None,
            "actual_rule_ids": None,
            "actual_scope_codes": None,
            "actual_response_level": None,
            "actual_safety_disposition": None,
            "actual_execution_status": None,
            "actual_release_decision": None,
            "actual_fallback_code": None,
            "actual_provider_invocation": None,
            "actual_retrieval_invocation": True,
            "actual_publication_allowed": None,
            "actual_sections": None,
            "omitted_sections": None,
            "risk_level": None,
            "answer_sha256": None,
            "latency_ms": 0,
            "input_token_count": None,
            "output_token_count": None,
            "estimated_cost": None,
        }
    )


def ret_l_case_results() -> tuple[CaseResult, ...]:
    replay = load_retrieval_replay(REPLAY, repository_root=EVALS_ROOT.parent)
    return tuple(_case_result(item.case_id, item.ranked_evidence_ids) for item in replay.case_results)


def _required_dataset(dataset: ValidatedDataset = DATASET) -> ValidatedDataset:
    policy = dataset.comparison_policy.model_copy(
        update={
            "scopes": tuple(
                scope.model_copy(update={"required": scope.metric_id == "RECALL_AT_5"})
                for scope in dataset.comparison_policy.scopes
            )
        }
    )
    return replace(dataset, comparison_policy=policy)


def _metric(metrics: tuple[MetricResult, ...], metric_id: str = "RECALL_AT_5") -> MetricResult:
    return next(item for item in metrics if item.metric_id == metric_id)


def test_five_case_fixture_matches_hand_calculated_metrics() -> None:
    metrics = build_retrieval_metrics(DATASET, ret_l_case_results())
    values = {
        metric.metric_id: Decimal(metric.metric_value) for metric in metrics.metrics if metric.metric_value is not None
    }

    assert values == {
        "MRR": Decimal("0.416667"),
        "NDCG_AT_5": Decimal("0.434951"),
        "NO_HIT_RATE": Decimal("0.200000"),
        "PRECISION_AT_5": Decimal("0.160000"),
        "RECALL_AT_5": Decimal("0.800000"),
    }


def test_metric_builder_accepts_runner_bound_case_input_hashes() -> None:
    resolved = load_dev_execution_request(
        EVALS_ROOT / "configs/rag-retrieval-dev-ret-l-v1.execution.json",
        repository_root=EVALS_ROOT.parent,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    outcome = execute_dev_cases(
        DATASET,
        resolved,
        run_id=RUN_ID,
        adapter_registry=build_adapter_registry(resolved),
    )

    metrics = build_retrieval_metrics(DATASET, outcome.case_results)

    assert {metric.execution_status.value for metric in metrics.metrics} == {"COMPLETED"}


def test_point_estimators_match_hand_calculated_top_five_values() -> None:
    observation = RetrievalObservation(
        case_id="case-1",
        slice_ids=("ALL",),
        independent_group_id="group-1",
        required_ids=("required",),
        relevant_ids=("required", "secondary"),
        ranked_ids=("unknown", "required", "secondary", "other", "last"),
    )

    assert recall_at_k(observation) == Decimal("1.000000")
    assert precision_at_k(observation) == Decimal("0.400000")
    assert reciprocal_rank(observation) == Decimal("0.500000")
    assert ndcg_at_k(observation) == Decimal("0.693426")
    assert no_hit(observation) == Decimal("0.000000")


def test_percentile_group_bootstrap_uses_fixed_seed_and_index_rule() -> None:
    group_scores = {
        "group-a": (Decimal("0.000000"),),
        "group-b": (Decimal("1.000000"),),
    }

    first = percentile_group_bootstrap_ci(group_scores, seed=7, iterations=4, level=Decimal("0.95"))
    second = percentile_group_bootstrap_ci(group_scores, seed=7, iterations=4, level=Decimal("0.95"))

    assert first == (Decimal("0.000000"), Decimal("0.500000"))
    assert second == first


@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        ("zero_required_evidence", "ZERO_DENOMINATOR"),
        ("four_independent_groups", "MINIMUM_INDEPENDENT_GROUP_COUNT_NOT_MET"),
        ("four_cases", "MINIMUM_CASE_COUNT_NOT_MET"),
    ],
)
def test_required_scope_never_passes_invalid_sample(fixture: str, reason: str) -> None:
    dataset = _required_dataset()
    results = ret_l_case_results()
    if fixture == "zero_required_evidence":
        case = dataset.cases[0]
        changed_case = case.model_copy(
            update={"expected": case.expected.model_copy(update={"required_evidence_refs": ()})}
        )
        dataset = replace(dataset, cases=(changed_case, *dataset.cases[1:]))
    elif fixture == "four_independent_groups":
        first_group = dataset.cases[0].leakage_group_ids.question_template
        case = dataset.cases[-1]
        changed_case = case.model_copy(
            update={"leakage_group_ids": case.leakage_group_ids.model_copy(update={"question_template": first_group})}
        )
        dataset = replace(dataset, cases=(*dataset.cases[:-1], changed_case))
    else:
        dataset = replace(dataset, cases=dataset.cases[:4])
        results = results[:4]

    metric = _metric(build_retrieval_metrics(dataset, results).metrics)

    assert metric.execution_status.value == "COMPLETED"
    assert metric.decision_status is not None
    assert metric.decision_status.value == "INCONCLUSIVE"
    assert metric.reason_code == reason


def test_empty_relevant_gold_only_invalidates_metrics_with_undefined_relevance_denominator() -> None:
    case = DATASET.cases[0]
    changed_case = case.model_copy(update={"expected": case.expected.model_copy(update={"relevant_evidence_refs": ()})})
    dataset = replace(DATASET, cases=(changed_case, *DATASET.cases[1:]))

    metrics = build_retrieval_metrics(dataset, ret_l_case_results()).metrics

    assert [
        (
            item.metric_id,
            item.execution_status.value,
            item.decision_status.value if item.decision_status is not None else None,
            item.numerator,
            item.denominator,
            item.metric_value,
            item.ci_lower,
            item.ci_upper,
            item.reason_code,
        )
        for item in metrics
    ] == [
        ("MRR", "COMPLETED", "INCONCLUSIVE", 0, 0, None, None, None, "ZERO_DENOMINATOR"),
        ("NDCG_AT_5", "COMPLETED", "INCONCLUSIVE", 0, 0, None, None, None, "ZERO_DENOMINATOR"),
        ("NO_HIT_RATE", "COMPLETED", "INCONCLUSIVE", 0, 0, None, None, None, "ZERO_DENOMINATOR"),
        ("PRECISION_AT_5", "COMPLETED", "N/A", 3, 25, "0.12", "0.04", "0.2", None),
        ("RECALL_AT_5", "COMPLETED", "N/A", 4, 5, "0.8", "0.4", "1", None),
    ]


def test_duplicate_ranked_evidence_marks_metrics_invalid() -> None:
    results = list(ret_l_case_results())
    duplicate = results[0].model_copy(update={"retrieved_evidence_ids": ("ev-ret-dev-med-a", "ev-ret-dev-med-a")})
    results[0] = cast(CaseResult, duplicate)

    metrics = build_retrieval_metrics(DATASET, tuple(results))

    assert {item.execution_status.value for item in metrics.metrics} == {"INVALID"}
    assert all(item.decision_status is None for item in metrics.metrics)
    assert all(
        (
            item.sample_case_count,
            item.sample_independent_group_count,
            item.numerator,
            item.denominator,
            item.metric_value,
            item.ci_lower,
            item.ci_upper,
            item.reason_code,
        )
        == (None,) * 8
        for item in metrics.metrics
    )


def test_duplicate_case_results_mark_metrics_invalid() -> None:
    results = ret_l_case_results()

    metrics = build_retrieval_metrics(DATASET, (*results[:-1], results[0]))

    assert {item.execution_status.value for item in metrics.metrics} == {"INVALID"}
    assert all(item.decision_status is None for item in metrics.metrics)


@pytest.mark.parametrize(
    ("field", "unsupported_value"),
    [
        ("metric_version", "9.9.9"),
        ("unit_of_analysis", "EVIDENCE"),
        ("estimator_id", "MICRO_AVERAGE"),
        ("estimator_version", "9.9.9"),
        ("independence_unit", "CASE"),
        ("cluster_dimension", LeakageAxis.SOURCE_SEGMENT),
        ("ci_method_id", "BCa"),
        ("ci_method_version", "9.9.9"),
        ("ci_parameters", (("iterations", 10_000), ("level", "0.95"))),
        (
            "ci_parameters",
            (("iterations", 10_000), ("level", "0.95"), ("sidedness", "ONE_SIDED")),
        ),
        (
            "ci_parameters",
            (
                ("iterations", 10_000),
                ("level", "0.95"),
                ("sidedness", "TWO_SIDED"),
                ("unknown", True),
            ),
        ),
        (
            "ci_parameters",
            (("iterations", True), ("level", "0.95"), ("sidedness", "TWO_SIDED")),
        ),
        (
            "ci_parameters",
            (("iterations", 10_000), ("level", 95), ("sidedness", "TWO_SIDED")),
        ),
        (
            "ci_parameters",
            (("iterations", 10_000), ("level", "not-a-level"), ("sidedness", "TWO_SIDED")),
        ),
        (
            "ci_parameters",
            (("iterations", 10_000), ("level", "0.950"), ("sidedness", "TWO_SIDED")),
        ),
        (
            "ci_parameters",
            (("iterations", 10_000), ("level", "0.95"), ("sidedness", True)),
        ),
        ("seed", None),
    ],
)
def test_unsupported_algorithm_signature_is_not_implemented(
    field: str,
    unsupported_value: object,
) -> None:
    target = DATASET.comparison_policy.scopes[0]
    changed_scope = target.model_copy(update={field: unsupported_value})
    changed_policy = DATASET.comparison_policy.model_copy(
        update={"scopes": (changed_scope, *DATASET.comparison_policy.scopes[1:])}
    )
    dataset = replace(DATASET, comparison_policy=changed_policy)

    metric = _metric(build_retrieval_metrics(dataset, ret_l_case_results()).metrics, target.metric_id)

    assert metric.execution_status.value == "NOT_IMPLEMENTED"
    assert metric.decision_status is None
    assert (
        metric.sample_case_count,
        metric.sample_independent_group_count,
        metric.numerator,
        metric.denominator,
        metric.metric_value,
        metric.ci_lower,
        metric.ci_upper,
        metric.reason_code,
    ) == (None,) * 8


def test_failed_case_marks_metrics_error_without_partial_values() -> None:
    results = list(ret_l_case_results())
    results[0] = _case_result(results[0].case_id, (), execution_status="ERROR")

    metrics = build_retrieval_metrics(DATASET, tuple(results))

    assert {item.execution_status.value for item in metrics.metrics} == {"ERROR"}
    assert all(item.decision_status is None for item in metrics.metrics)
    assert all(
        (
            item.sample_case_count,
            item.sample_independent_group_count,
            item.numerator,
            item.denominator,
            item.metric_value,
            item.ci_lower,
            item.ci_upper,
            item.reason_code,
        )
        == (None,) * 8
        for item in metrics.metrics
    )


def test_metric_counts_ci_and_serialization_are_deterministic() -> None:
    first = build_retrieval_metrics(DATASET, ret_l_case_results())
    second = build_retrieval_metrics(DATASET, tuple(reversed(ret_l_case_results())))

    assert [(item.metric_id, item.numerator, item.denominator) for item in first.metrics] == [
        ("MRR", 4, 5),
        ("NDCG_AT_5", 4, 5),
        ("NO_HIT_RATE", 1, 5),
        ("PRECISION_AT_5", 4, 25),
        ("RECALL_AT_5", 4, 5),
    ]
    assert all(item.sample_case_count == 5 for item in first.metrics)
    assert all(item.sample_independent_group_count == 5 for item in first.metrics)
    assert {
        item.metric_id: (Decimal(item.ci_lower), Decimal(item.ci_upper))
        for item in first.metrics
        if item.ci_lower is not None and item.ci_upper is not None
    } == {
        "MRR": (Decimal("0.150000"), Decimal("0.716667")),
        "NDCG_AT_5": (Decimal("0.208765"), Decimal("0.597631")),
        "NO_HIT_RATE": (Decimal("0.000000"), Decimal("0.600000")),
        "PRECISION_AT_5": (Decimal("0.080000"), Decimal("0.200000")),
        "RECALL_AT_5": (Decimal("0.400000"), Decimal("1.000000")),
    }
    assert canonical_json_bytes(cast(JsonValue, first.model_dump(mode="json"))) == canonical_json_bytes(
        cast(JsonValue, second.model_dump(mode="json"))
    )


def test_serialized_metric_results_round_trip_through_runtime_contract() -> None:
    metrics = build_retrieval_metrics(DATASET, ret_l_case_results())

    revalidated = MetricResults.model_validate_json(metrics.model_dump_json())

    assert revalidated == metrics
    assert {item.metric_id: item.metric_value for item in revalidated.metrics} == {
        "MRR": "0.416667",
        "NDCG_AT_5": "0.434951",
        "NO_HIT_RATE": "0.2",
        "PRECISION_AT_5": "0.16",
        "RECALL_AT_5": "0.8",
    }
    assert {item.metric_id: (item.ci_lower, item.ci_upper) for item in revalidated.metrics} == {
        "MRR": ("0.15", "0.716667"),
        "NDCG_AT_5": ("0.208765", "0.597631"),
        "NO_HIT_RATE": ("0", "0.6"),
        "PRECISION_AT_5": ("0.08", "0.2"),
        "RECALL_AT_5": ("0.4", "1"),
    }


def test_serialized_metric_results_match_checked_in_artifact_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = EVALS_ROOT / "schemas/1.0.0/artifacts/rag-eval.metrics.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    payload = json.loads(build_retrieval_metrics(DATASET, ret_l_case_results()).model_dump_json())

    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(payload))

    assert errors == []


def test_metric_results_use_contract_sort_key_not_policy_order() -> None:
    reversed_policy = DATASET.comparison_policy.model_copy(
        update={"scopes": tuple(reversed(DATASET.comparison_policy.scopes))}
    )
    dataset = replace(DATASET, comparison_policy=reversed_policy)

    metrics = build_retrieval_metrics(dataset, ret_l_case_results()).metrics

    assert [item.metric_id for item in metrics] == [
        "MRR",
        "NDCG_AT_5",
        "NO_HIT_RATE",
        "PRECISION_AT_5",
        "RECALL_AT_5",
    ]
