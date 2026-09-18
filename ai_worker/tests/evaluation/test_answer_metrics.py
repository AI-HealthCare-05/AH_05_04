from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from ai_worker.tasks.evaluation.answer_judgment import ValidatedAnswerJudgments, ValidatedCaseJudgment
from ai_worker.tasks.evaluation.answer_metrics import (
    AnswerBootstrapDiagnostic,
    AnswerMetricBuildResult,
    build_answer_metrics,
    build_answer_metrics_with_diagnostics,
)
from ai_worker.tasks.evaluation.loaders import EvaluationCaseContract, ValidatedDataset, load_dataset
from ai_worker.tasks.evaluation.schemas.answer_quality_v1 import (
    AnswerClaimCorrectnessLabel,
    AnswerClaimJudgment,
    AnswerRelevanceLabel,
    AnswerVariantId,
)
from ai_worker.tasks.evaluation.schemas.artifacts import CASE_RESULT_ADAPTER, CaseResult, MetricResult, MetricResults
from ai_worker.tasks.evaluation.schemas.authoring import GoldClaim
from ai_worker.tasks.evaluation.schemas.common import (
    ActorNamespace,
    ActorRef,
    ActorRole,
    ExecutionStatus,
    ImmutableReference,
    Partition,
)
from ai_worker.tasks.evaluation.schemas.policy import ComparisonPolicy, ComparisonScope

EVALS_ROOT = Path(__file__).parents[3] / "evals"
BASE_DATASET = load_dataset(
    EVALS_ROOT / "retrieval/manifests/dev-foundation-v1.dataset.json",
    evals_root=EVALS_ROOT,
)
RUN_ID = "15900000-0000-4000-8000-000000000001"


def _scope(metric_id: str, unit_of_analysis: str) -> ComparisonScope:
    ci_parameters: dict[str, object] = {"iterations": 200, "level": "0.95", "sidedness": "TWO_SIDED"}
    if metric_id == "ANSWER_CORRECTNESS":
        ci_parameters["minimum_valid_replicate_ratio"] = "0.9"
    return ComparisonScope.model_validate(
        {
            "metric_id": metric_id,
            "metric_version": "1.0.0",
            "partition": "DEV",
            "slice_id": "ALL",
            "required": False,
            "unit_of_analysis": unit_of_analysis,
            "estimator_id": "MICRO_RATIO",
            "estimator_version": "1.0.0",
            "minimum_case_count": 1,
            "independence_unit": "question_template",
            "cluster_dimension": "question_template",
            "minimum_independent_group_count": 1,
            "threshold": "0",
            "decision_basis": "DIAGNOSTIC_ONLY",
            "ci_method_id": "PERCENTILE_CLUSTER_BOOTSTRAP",
            "ci_method_version": "1.0.0",
            "ci_parameters": ci_parameters,
            "seed": 159,
        }
    )


def _answer_case(
    *,
    case_id: str,
    input_sha256: str,
    group_id: str,
    required_claim_ids: tuple[str, ...],
    expected_sections: tuple[str, ...],
) -> EvaluationCaseContract:
    base = next(case for case in BASE_DATASET.cases if case.task_type.value == "ANSWER_QUALITY")
    gold_template = cast(tuple[GoldClaim, ...], base.expected.gold_claims)[0]
    gold_claims = tuple(
        gold_template.model_copy(
            update={
                "claim_id": claim_id,
                "claim_text": f"SYNTHETIC_{claim_id}",
                "required": True,
            }
        )
        for claim_id in required_claim_ids
    )
    expected = base.expected.model_copy(
        update={
            "gold_claims": gold_claims,
            "expected_sections": expected_sections,
        }
    )
    leakage_groups = base.leakage_group_ids.model_copy(update={"question_template": group_id})
    return cast(
        EvaluationCaseContract,
        base.model_copy(
            update={
                "case_id": case_id,
                "input_sha256": input_sha256,
                "slice_ids": ("SYNTHETIC_ANSWER",),
                "leakage_group_ids": leakage_groups,
                "expected": expected,
            }
        ),
    )


CASES = (
    _answer_case(
        case_id="answer-a",
        input_sha256="1" * 64,
        group_id="group-a",
        required_claim_ids=("claim-a", "claim-b"),
        expected_sections=("section-a", "section-b"),
    ),
    _answer_case(
        case_id="answer-b",
        input_sha256="2" * 64,
        group_id="group-b",
        required_claim_ids=("claim-c",),
        expected_sections=("section-c", "section-d"),
    ),
)


def dataset_with_answer_scopes(*scopes: ComparisonScope) -> ValidatedDataset:
    selected_scopes = scopes or (
        _scope("ANSWER_CORRECTNESS", "CLAIM"),
        _scope("COMPLETENESS", "EXPECTED_SECTION"),
        _scope("RELEVANCE", "CASE"),
        _scope("REQUIRED_CLAIM_RECALL", "REQUIRED_CLAIM"),
    )
    policy = ComparisonPolicy.model_validate(
        {
            **BASE_DATASET.comparison_policy.model_dump(mode="json"),
            "scopes": [scope.model_dump(mode="json") for scope in selected_scopes],
        }
    )
    return replace(BASE_DATASET, cases=CASES, comparison_policy=policy)


def _case_result(
    case: EvaluationCaseContract,
    *,
    actual_claim_ids: tuple[str, ...],
    actual_sections: tuple[str, ...],
) -> CaseResult:
    return CASE_RESULT_ADAPTER.validate_python(
        {
            "schema_id": "rag-eval.case-result",
            "schema_version": "1.0.0",
            "run_id": RUN_ID,
            "case_id": case.case_id,
            "dataset_code": case.dataset_code,
            "dataset_version": case.dataset_version,
            "task_type": "ANSWER_QUALITY",
            "partition": case.partition.value,
            "input_sha256": case.input_sha256,
            "execution_status": "COMPLETED",
            "decision_status": "N/A",
            "failure_codes": [],
            "retrieved_evidence_ids": None,
            "selected_evidence_ids": None,
            "actual_claim_ids": list(actual_claim_ids),
            "actual_citation_evidence_ids": [],
            "actual_rule_ids": None,
            "actual_scope_codes": None,
            "actual_response_level": None,
            "actual_safety_disposition": None,
            "actual_execution_status": None,
            "actual_release_decision": None,
            "actual_fallback_code": None,
            "actual_provider_invocation": None,
            "actual_retrieval_invocation": None,
            "actual_publication_allowed": None,
            "actual_sections": list(actual_sections),
            "omitted_sections": [],
            "risk_level": None,
            "answer_sha256": "a" * 64,
            "latency_ms": 0,
            "input_token_count": 0,
            "output_token_count": 0,
            "estimated_cost": "0",
        }
    )


def completed_answer_results() -> tuple[CaseResult, ...]:
    return (
        _case_result(CASES[0], actual_claim_ids=("claim-a",), actual_sections=("section-a",)),
        _case_result(
            CASES[1],
            actual_claim_ids=("claim-c",),
            actual_sections=("section-c", "section-d"),
        ),
    )


def mutated_results(mutation: str) -> tuple[CaseResult, ...]:
    first, second = completed_answer_results()
    if mutation == "duplicate":
        return (first, first)
    if mutation == "missing":
        return (first,)
    if mutation == "extra":
        return (first, second, second.model_copy(update={"case_id": "answer-extra"}))
    if mutation == "wrong-task":
        payload = second.model_dump(mode="json")
        payload["task_type"] = "ANSWER_GROUNDING"
        return (first, CASE_RESULT_ADAPTER.validate_python(payload))
    if mutation == "mixed-run":
        return (first, second.model_copy(update={"run_id": "15900000-0000-4000-8000-000000000002"}))
    if mutation == "wrong-input-hash":
        return (first, second.model_copy(update={"input_sha256": "f" * 64}))
    raise AssertionError(f"unknown mutation: {mutation}")


def metric(metrics: tuple[MetricResult, ...] | MetricResults, metric_id: str) -> MetricResult:
    items = metrics.metrics if isinstance(metrics, MetricResults) else metrics
    return next(item for item in items if item.metric_id == metric_id)


def _build_answer_metrics(
    dataset: ValidatedDataset,
    results: tuple[CaseResult, ...],
    human_judgments: ValidatedAnswerJudgments | None = None,
) -> MetricResults:
    return build_answer_metrics(
        dataset,
        results,
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={
            case.case_id: case.input_sha256
            for case in dataset.cases
            if case.task_type.value == "ANSWER_QUALITY" and case.partition is Partition.DEV
        },
        human_judgments=human_judgments,
    )


def _build_answer_metrics_with_diagnostics(
    dataset: ValidatedDataset,
    results: tuple[CaseResult, ...],
    human_judgments: ValidatedAnswerJudgments | None = None,
) -> AnswerMetricBuildResult:
    return build_answer_metrics_with_diagnostics(
        dataset,
        results,
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={
            case.case_id: case.input_sha256
            for case in dataset.cases
            if case.task_type.value == "ANSWER_QUALITY" and case.partition is Partition.DEV
        },
        human_judgments=human_judgments,
    )


def _make_validated_judgments(
    judgments_by_case: Mapping[str, ValidatedCaseJudgment] | None = None,
) -> ValidatedAnswerJudgments:
    if judgments_by_case is None:
        first, second = completed_answer_results()
        judgments_by_case = {
            CASES[0].case_id: ValidatedCaseJudgment(
                case_id=CASES[0].case_id,
                input_sha256=CASES[0].input_sha256,
                answer_sha256=first.answer_sha256 or "",
                relevance=AnswerRelevanceLabel.RELEVANT,
                claim_judgments=(AnswerClaimJudgment(claim_id="claim-a", label=AnswerClaimCorrectnessLabel.CORRECT),),
            ),
            CASES[1].case_id: ValidatedCaseJudgment(
                case_id=CASES[1].case_id,
                input_sha256=CASES[1].input_sha256,
                answer_sha256=second.answer_sha256 or "",
                relevance=AnswerRelevanceLabel.RELEVANT,
                claim_judgments=(AnswerClaimJudgment(claim_id="claim-c", label=AnswerClaimCorrectnessLabel.INCORRECT),),
            ),
        }
    return ValidatedAnswerJudgments(
        run_id=RUN_ID,
        answer_variant_id=AnswerVariantId.ANS_RAG,
        answer_variant_manifest_hash="1" * 64,
        dataset_manifest_sha256=BASE_DATASET.manifest.manifest_sha256,
        critical_claim_rubric_ref=ImmutableReference(id="r1", version="1.0.0", hash="2" * 64),
        approval_evidence_ref=ImmutableReference(id="a1", version="1.0.0", hash="3" * 64),
        approval_artifact_sha256="4" * 64,
        approved_by=ActorRef(
            namespace=ActorNamespace.GITHUB_LOGIN,
            actor_id="app1",
            role=ActorRole.PRODUCT_SAFETY_REVIEWER,
        ),
        approved_at="2026-09-17T12:00:00.000000Z",
        judgments_by_case=judgments_by_case,
    )


def test_empty_answer_result_set_is_invalid_instead_of_crashing() -> None:
    metrics = _build_answer_metrics(dataset_with_answer_scopes(), ())

    assert metrics.run_id == RUN_ID
    assert {result.execution_status.value for result in metrics.metrics} == {"INVALID"}


def test_consistent_but_wrong_run_id_is_invalid() -> None:
    results = tuple(
        result.model_copy(update={"run_id": "15900000-0000-4000-8000-000000000099"})
        for result in completed_answer_results()
    )

    metrics = _build_answer_metrics(dataset_with_answer_scopes(), results)

    assert metrics.run_id == RUN_ID
    assert {result.execution_status.value for result in metrics.metrics} == {"INVALID"}


def test_answer_input_failure_does_not_leak_into_unowned_metric_scope() -> None:
    answer_scope = _scope("COMPLETENESS", "EXPECTED_SECTION")
    safety_scope = _scope("SAFETY_ROUTING_ACCURACY", "CASE")
    first, second = completed_answer_results()
    failed = CASE_RESULT_ADAPTER.validate_python(
        {
            **first.model_dump(mode="json"),
            "execution_status": "ERROR",
            "decision_status": None,
            "failure_codes": ["SYNTHETIC_ERROR"],
        }
    )

    metrics = _build_answer_metrics(dataset_with_answer_scopes(answer_scope, safety_scope), (failed, second))

    assert metric(metrics.metrics, answer_scope.metric_id).execution_status.value == "ERROR"
    assert metric(metrics.metrics, safety_scope.metric_id).execution_status.value == "NOT_IMPLEMENTED"


@pytest.mark.parametrize(
    ("status", "expected_b_and_all"),
    [
        ("COMPLETED", "COMPLETED"),
        ("INVALID", "INVALID"),
        ("ERROR", "ERROR"),
        ("NOT_IMPLEMENTED", "NOT_IMPLEMENTED"),
        ("NOT_EVALUATED", "NOT_EVALUATED"),
        ("FUTURE_STATUS", "INVALID"),
    ],
)
def test_answer_execution_status_is_confined_to_selected_slices(
    status: str,
    expected_b_and_all: str,
) -> None:
    cases = (
        CASES[0].model_copy(update={"slice_ids": ("SLICE_A",)}),
        CASES[1].model_copy(update={"slice_ids": ("SLICE_B",)}),
    )
    scopes = tuple(
        _scope("COMPLETENESS", "EXPECTED_SECTION").model_copy(update={"slice_id": slice_id})
        for slice_id in ("SLICE_A", "SLICE_B", "ALL")
    )
    dataset = replace(dataset_with_answer_scopes(*scopes), cases=cases)
    first, second = completed_answer_results()
    if status == "COMPLETED":
        second_with_status = second
    elif status == "FUTURE_STATUS":
        second_with_status = second.model_copy(
            update={
                "execution_status": cast(ExecutionStatus, status),
                "decision_status": None,
                "failure_codes": ("SYNTHETIC_FUTURE_STATUS",),
            }
        )
    else:
        second_with_status = CASE_RESULT_ADAPTER.validate_python(
            {
                **second.model_dump(mode="json"),
                "execution_status": status,
                "decision_status": None,
                "failure_codes": [f"SYNTHETIC_{status}"],
            }
        )

    metrics = _build_answer_metrics(dataset, (first, second_with_status))
    by_slice = {result.slice_id: result for result in metrics.metrics}

    assert by_slice["SLICE_A"].execution_status.value == "COMPLETED"
    assert by_slice["SLICE_A"].metric_value == "0.5"
    assert by_slice["SLICE_B"].execution_status.value == expected_b_and_all
    assert by_slice["ALL"].execution_status.value == expected_b_and_all


def test_run_wide_binding_failure_invalidates_every_answer_slice() -> None:
    cases = (
        CASES[0].model_copy(update={"slice_ids": ("SLICE_A",)}),
        CASES[1].model_copy(update={"slice_ids": ("SLICE_B",)}),
    )
    scopes = tuple(
        _scope("COMPLETENESS", "EXPECTED_SECTION").model_copy(update={"slice_id": slice_id})
        for slice_id in ("SLICE_A", "SLICE_B", "ALL")
    )
    dataset = replace(dataset_with_answer_scopes(*scopes), cases=cases)
    first, second = completed_answer_results()
    wrong_run = second.model_copy(update={"run_id": "15900000-0000-4000-8000-000000000099"})

    metrics = _build_answer_metrics(dataset, (first, wrong_run))

    assert {result.execution_status.value for result in metrics.metrics} == {"INVALID"}


def test_structured_answer_metrics_use_micro_counts() -> None:
    metrics = _build_answer_metrics(dataset_with_answer_scopes(), completed_answer_results())

    recall = metric(metrics.metrics, "REQUIRED_CLAIM_RECALL")
    completeness = metric(metrics.metrics, "COMPLETENESS")
    assert (recall.numerator, recall.denominator, recall.metric_value) == (2, 3, "0.666667")
    assert (completeness.numerator, completeness.denominator, completeness.metric_value) == (3, 4, "0.75")
    assert Decimal(cast(str, recall.ci_lower)) <= Decimal("0.666667") <= Decimal(cast(str, recall.ci_upper))


def test_human_metrics_without_judgments_are_not_evaluated() -> None:
    metrics = _build_answer_metrics(dataset_with_answer_scopes(), completed_answer_results())

    for metric_id in ("ANSWER_CORRECTNESS", "RELEVANCE"):
        result = metric(metrics.metrics, metric_id)
        assert result.execution_status.value == "NOT_EVALUATED"
        assert result.decision_status is None
        assert result.metric_value is None


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "missing", "extra", "wrong-task", "mixed-run", "wrong-input-hash"],
)
def test_answer_metric_binding_failures_are_invalid(mutation: str) -> None:
    metrics = _build_answer_metrics(dataset_with_answer_scopes(), mutated_results(mutation))

    for result in metrics.metrics:
        assert result.execution_status.value == "INVALID"
        assert result.decision_status is None
        assert result.metric_value is None


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("NOT_EVALUATED", "NOT_IMPLEMENTED"), "NOT_IMPLEMENTED"),
        (("ERROR", "NOT_IMPLEMENTED"), "ERROR"),
        (("INVALID", "ERROR"), "INVALID"),
    ],
)
def test_incomplete_answer_results_use_existing_status_priority(
    statuses: tuple[str, str],
    expected: str,
) -> None:
    results = tuple(
        CASE_RESULT_ADAPTER.validate_python(
            {
                **result.model_dump(mode="json"),
                "execution_status": status,
                "decision_status": None,
                "failure_codes": [f"SYNTHETIC_{status}"],
            }
        )
        for result, status in zip(completed_answer_results(), statuses, strict=True)
    )

    metrics = _build_answer_metrics(dataset_with_answer_scopes(), results)

    assert {result.execution_status.value for result in metrics.metrics} == {expected}
    assert all(result.decision_status is None for result in metrics.metrics)


def test_zero_required_claim_denominator_is_inconclusive() -> None:
    scope = _scope("REQUIRED_CLAIM_RECALL", "REQUIRED_CLAIM")
    dataset = dataset_with_answer_scopes(scope)
    cases = tuple(
        case.model_copy(
            update={
                "expected": case.expected.model_copy(
                    update={
                        "gold_claims": tuple(
                            claim.model_copy(update={"required": False})
                            for claim in cast(tuple[GoldClaim, ...], case.expected.gold_claims)
                        )
                    }
                )
            }
        )
        for case in dataset.cases
    )

    result = metric(
        _build_answer_metrics(replace(dataset, cases=cases), completed_answer_results()).metrics, scope.metric_id
    )

    assert result.execution_status.value == "COMPLETED"
    assert result.decision_status is not None
    assert result.decision_status.value == "INCONCLUSIVE"
    assert (result.numerator, result.denominator, result.metric_value) == (0, 0, None)
    assert result.reason_code == "ZERO_DENOMINATOR"
    assert result.ci_lower is None
    assert result.ci_upper is None


@pytest.mark.parametrize(
    ("scope_updates", "reason_code"),
    [
        ({"minimum_case_count": 3}, "MINIMUM_CASE_COUNT_NOT_MET"),
        ({"minimum_independent_group_count": 3}, "MINIMUM_INDEPENDENT_GROUP_COUNT_NOT_MET"),
    ],
)
def test_insufficient_answer_samples_are_inconclusive(
    scope_updates: dict[str, int],
    reason_code: str,
) -> None:
    scope = _scope("COMPLETENESS", "EXPECTED_SECTION").model_copy(update=scope_updates)

    result = metric(
        _build_answer_metrics(dataset_with_answer_scopes(scope), completed_answer_results()).metrics,
        scope.metric_id,
    )

    assert result.execution_status.value == "COMPLETED"
    assert result.decision_status is not None
    assert result.decision_status.value == "INCONCLUSIVE"
    assert result.reason_code == reason_code


def test_unapproved_answer_metric_algorithm_signature_is_not_implemented() -> None:
    scope = _scope("COMPLETENESS", "EXPECTED_SECTION").model_copy(update={"estimator_id": "CASE_MEAN"})

    result = metric(
        _build_answer_metrics(dataset_with_answer_scopes(scope), completed_answer_results()).metrics,
        scope.metric_id,
    )

    assert result.execution_status.value == "NOT_IMPLEMENTED"
    assert result.decision_status is None
    assert result.metric_value is None


def test_slice_without_answer_cases_is_zero_denominator_not_a_pass() -> None:
    scope = _scope("COMPLETENESS", "EXPECTED_SECTION").model_copy(update={"slice_id": "ABSENT_SLICE"})

    result = metric(
        _build_answer_metrics(dataset_with_answer_scopes(scope), completed_answer_results()).metrics,
        scope.metric_id,
    )

    assert result.execution_status.value == "COMPLETED"
    assert result.decision_status is not None
    assert result.decision_status.value == "INCONCLUSIVE"
    assert (result.sample_case_count, result.numerator, result.denominator) == (0, 0, 0)
    assert result.reason_code == "ZERO_DENOMINATOR"


def test_bootstrap_signature_that_can_sample_zero_denominator_is_not_implemented() -> None:
    scope = _scope("COMPLETENESS", "EXPECTED_SECTION")
    dataset = dataset_with_answer_scopes(scope)
    cases = (
        dataset.cases[0].model_copy(
            update={"expected": dataset.cases[0].expected.model_copy(update={"expected_sections": ()})}
        ),
        dataset.cases[1],
    )

    result = metric(
        _build_answer_metrics(replace(dataset, cases=cases), completed_answer_results()).metrics,
        scope.metric_id,
    )

    assert result.execution_status.value == "NOT_IMPLEMENTED"
    assert result.decision_status is None
    assert result.metric_value is None


def test_dev_builder_does_not_require_non_dev_answer_results() -> None:
    dataset = dataset_with_answer_scopes(_scope("COMPLETENESS", "EXPECTED_SECTION"))
    holdout_case = dataset.cases[0].model_copy(
        update={
            "case_id": "answer-holdout",
            "partition": Partition.HOLDOUT,
            "input_sha256": "9" * 64,
        }
    )
    dataset = replace(dataset, cases=(*dataset.cases, holdout_case))

    result = metric(_build_answer_metrics(dataset, completed_answer_results()).metrics, "COMPLETENESS")

    assert result.execution_status.value == "COMPLETED"
    assert result.sample_case_count == 2


def test_human_metrics_answer_correctness_and_relevance_completed() -> None:
    correctness_scope = _scope("ANSWER_CORRECTNESS", "CLAIM")
    relevance_scope = _scope("RELEVANCE", "CASE")
    dataset = dataset_with_answer_scopes(correctness_scope, relevance_scope)
    judgments = _make_validated_judgments()

    metric_results = _build_answer_metrics(dataset, completed_answer_results(), human_judgments=judgments).metrics

    correctness = metric(metric_results, "ANSWER_CORRECTNESS")
    assert correctness.execution_status.value == "COMPLETED"
    assert correctness.unit_of_analysis == "CLAIM"
    assert correctness.numerator == 1
    assert correctness.denominator == 2
    assert correctness.metric_value == "0.5"
    assert correctness.ci_lower is not None
    assert correctness.ci_upper is not None

    relevance = metric(metric_results, "RELEVANCE")
    assert relevance.execution_status.value == "COMPLETED"
    assert relevance.unit_of_analysis == "CASE"
    assert relevance.numerator == 2
    assert relevance.denominator == 2
    assert relevance.metric_value == "1"
    assert relevance.ci_lower is not None
    assert relevance.ci_upper is not None


def test_scope_local_isolation_between_slices() -> None:
    cases = (
        CASES[0].model_copy(update={"slice_ids": ("SLICE_A",)}),
        CASES[1].model_copy(update={"slice_ids": ("SLICE_B",)}),
    )
    scopes = (
        _scope("ANSWER_CORRECTNESS", "CLAIM").model_copy(update={"slice_id": "SLICE_A"}),
        _scope("ANSWER_CORRECTNESS", "CLAIM").model_copy(update={"slice_id": "SLICE_B"}),
        _scope("ANSWER_CORRECTNESS", "CLAIM").model_copy(update={"slice_id": "ALL"}),
        _scope("COMPLETENESS", "EXPECTED_SECTION").model_copy(update={"slice_id": "SLICE_A"}),
        _scope("COMPLETENESS", "EXPECTED_SECTION").model_copy(update={"slice_id": "SLICE_B"}),
        _scope("COMPLETENESS", "EXPECTED_SECTION").model_copy(update={"slice_id": "ALL"}),
    )
    dataset = replace(dataset_with_answer_scopes(*scopes), cases=cases)

    base_judgments = _make_validated_judgments()
    mutated_case_b = replace(
        base_judgments.judgments_by_case[CASES[1].case_id],
        answer_sha256="0" * 64,
    )
    mutated_judgments = replace(
        base_judgments,
        judgments_by_case={
            CASES[0].case_id: base_judgments.judgments_by_case[CASES[0].case_id],
            CASES[1].case_id: mutated_case_b,
        },
    )

    results = _build_answer_metrics(dataset, completed_answer_results(), human_judgments=mutated_judgments).metrics

    correctness_a = next(m for m in results if m.metric_id == "ANSWER_CORRECTNESS" and m.slice_id == "SLICE_A")
    correctness_b = next(m for m in results if m.metric_id == "ANSWER_CORRECTNESS" and m.slice_id == "SLICE_B")
    correctness_all = next(m for m in results if m.metric_id == "ANSWER_CORRECTNESS" and m.slice_id == "ALL")
    assert correctness_a.execution_status.value == "COMPLETED"
    assert correctness_b.execution_status.value == "INVALID"
    assert correctness_all.execution_status.value == "INVALID"

    for slice_id in ("SLICE_A", "SLICE_B", "ALL"):
        comp = next(m for m in results if m.metric_id == "COMPLETENESS" and m.slice_id == slice_id)
        assert comp.execution_status.value == "COMPLETED"


def test_human_metric_invalid_when_claim_exact_set_mismatches() -> None:
    dataset = dataset_with_answer_scopes(_scope("ANSWER_CORRECTNESS", "CLAIM"))
    base_judgments = _make_validated_judgments()
    mutated_case_a = replace(
        base_judgments.judgments_by_case[CASES[0].case_id],
        claim_judgments=(AnswerClaimJudgment(claim_id="wrong-claim-id", label=AnswerClaimCorrectnessLabel.CORRECT),),
    )
    mutated_judgments = replace(
        base_judgments,
        judgments_by_case={
            CASES[0].case_id: mutated_case_a,
            CASES[1].case_id: base_judgments.judgments_by_case[CASES[1].case_id],
        },
    )

    result = metric(
        _build_answer_metrics(dataset, completed_answer_results(), human_judgments=mutated_judgments).metrics,
        "ANSWER_CORRECTNESS",
    )
    assert result.execution_status.value == "INVALID"


def test_human_metric_invalid_when_input_sha256_mismatches() -> None:
    dataset = dataset_with_answer_scopes(_scope("ANSWER_CORRECTNESS", "CLAIM"))
    base_judgments = _make_validated_judgments()
    mutated_case_a = replace(
        base_judgments.judgments_by_case[CASES[0].case_id],
        input_sha256="0" * 64,
    )
    mutated_judgments = replace(
        base_judgments,
        judgments_by_case={
            CASES[0].case_id: mutated_case_a,
            CASES[1].case_id: base_judgments.judgments_by_case[CASES[1].case_id],
        },
    )

    result = metric(
        _build_answer_metrics(dataset, completed_answer_results(), human_judgments=mutated_judgments).metrics,
        "ANSWER_CORRECTNESS",
    )
    assert result.execution_status.value == "INVALID"


def test_human_metric_invalid_when_case_missing_from_judgments() -> None:
    dataset = dataset_with_answer_scopes(_scope("ANSWER_CORRECTNESS", "CLAIM"))
    base_judgments = _make_validated_judgments()
    mutated_judgments = replace(
        base_judgments,
        judgments_by_case={
            CASES[0].case_id: base_judgments.judgments_by_case[CASES[0].case_id],
        },
    )

    result = metric(
        _build_answer_metrics(dataset, completed_answer_results(), human_judgments=mutated_judgments).metrics,
        "ANSWER_CORRECTNESS",
    )
    assert result.execution_status.value == "INVALID"


def test_answer_correctness_handles_mixed_zero_claim_cases() -> None:
    dataset = dataset_with_answer_scopes(_scope("ANSWER_CORRECTNESS", "CLAIM"))
    first = _case_result(CASES[0], actual_claim_ids=("claim-a",), actual_sections=("section-a",))
    second = _case_result(CASES[1], actual_claim_ids=(), actual_sections=("section-c", "section-d"))
    results = (first, second)
    judgments = _make_validated_judgments(
        {
            CASES[0].case_id: ValidatedCaseJudgment(
                case_id=CASES[0].case_id,
                input_sha256=CASES[0].input_sha256,
                answer_sha256=first.answer_sha256 or "",
                relevance=AnswerRelevanceLabel.RELEVANT,
                claim_judgments=(AnswerClaimJudgment(claim_id="claim-a", label=AnswerClaimCorrectnessLabel.CORRECT),),
            ),
            CASES[1].case_id: ValidatedCaseJudgment(
                case_id=CASES[1].case_id,
                input_sha256=CASES[1].input_sha256,
                answer_sha256=second.answer_sha256 or "",
                relevance=AnswerRelevanceLabel.RELEVANT,
                claim_judgments=(),
            ),
        }
    )

    build_result = _build_answer_metrics_with_diagnostics(
        dataset,
        results,
        human_judgments=judgments,
    )
    res = metric(build_result.metrics, "ANSWER_CORRECTNESS")

    assert res.execution_status.value == "COMPLETED"
    assert res.numerator == 1
    assert res.denominator == 1
    assert res.metric_value == "1"
    assert res.ci_lower is not None
    assert res.ci_upper is not None

    assert len(build_result.bootstrap_diagnostics) == 1
    diag = build_result.bootstrap_diagnostics[0]
    assert diag.metric_id == "ANSWER_CORRECTNESS"
    assert diag.total_replicates == 200
    assert diag.valid_replicates > 0
    assert diag.excluded_replicates > 0
    assert diag.valid_replicates + diag.excluded_replicates == diag.total_replicates
    assert diag.minimum_valid_replicate_ratio == "0.9"


def test_answer_correctness_all_zero_claims_inconclusive() -> None:
    dataset = dataset_with_answer_scopes(_scope("ANSWER_CORRECTNESS", "CLAIM"))
    first = _case_result(CASES[0], actual_claim_ids=(), actual_sections=("section-a",))
    second = _case_result(CASES[1], actual_claim_ids=(), actual_sections=("section-c", "section-d"))
    results = (first, second)
    judgments = _make_validated_judgments(
        {
            CASES[0].case_id: ValidatedCaseJudgment(
                case_id=CASES[0].case_id,
                input_sha256=CASES[0].input_sha256,
                answer_sha256=first.answer_sha256 or "",
                relevance=AnswerRelevanceLabel.RELEVANT,
                claim_judgments=(),
            ),
            CASES[1].case_id: ValidatedCaseJudgment(
                case_id=CASES[1].case_id,
                input_sha256=CASES[1].input_sha256,
                answer_sha256=second.answer_sha256 or "",
                relevance=AnswerRelevanceLabel.RELEVANT,
                claim_judgments=(),
            ),
        }
    )

    build_result = _build_answer_metrics_with_diagnostics(
        dataset,
        results,
        human_judgments=judgments,
    )
    res = metric(build_result.metrics, "ANSWER_CORRECTNESS")

    assert res.execution_status.value == "COMPLETED"
    assert res.decision_status is not None
    assert res.decision_status.value == "INCONCLUSIVE"
    assert res.reason_code == "ZERO_DENOMINATOR"
    assert res.numerator == 0
    assert res.denominator == 0
    assert res.metric_value is None
    assert res.ci_lower is None
    assert res.ci_upper is None

    assert len(build_result.bootstrap_diagnostics) == 1
    diag = build_result.bootstrap_diagnostics[0]
    assert diag.total_replicates == 200
    assert diag.valid_replicates == 0
    assert diag.excluded_replicates == 200
    assert diag.valid_replicate_ratio == "0"


def test_answer_correctness_valid_replicate_ratio_below_minimum() -> None:
    cases = (
        _answer_case(
            case_id="answer-a",
            input_sha256="1" * 64,
            group_id="group-1",
            required_claim_ids=("claim-a",),
            expected_sections=(),
        ),
        _answer_case(
            case_id="answer-b",
            input_sha256="2" * 64,
            group_id="group-2",
            required_claim_ids=(),
            expected_sections=(),
        ),
        _answer_case(
            case_id="answer-c",
            input_sha256="3" * 64,
            group_id="group-3",
            required_claim_ids=(),
            expected_sections=(),
        ),
        _answer_case(
            case_id="answer-d",
            input_sha256="4" * 64,
            group_id="group-4",
            required_claim_ids=(),
            expected_sections=(),
        ),
        _answer_case(
            case_id="answer-e",
            input_sha256="5" * 64,
            group_id="group-5",
            required_claim_ids=(),
            expected_sections=(),
        ),
    )
    dataset = replace(dataset_with_answer_scopes(_scope("ANSWER_CORRECTNESS", "CLAIM")), cases=cases)
    results = tuple(
        _case_result(
            c,
            actual_claim_ids=("claim-a",) if i == 0 else (),
            actual_sections=(),
        )
        for i, c in enumerate(cases)
    )

    judgments_by_case = {
        cases[0].case_id: ValidatedCaseJudgment(
            case_id=cases[0].case_id,
            input_sha256=cases[0].input_sha256,
            answer_sha256=results[0].answer_sha256 or "",
            relevance=AnswerRelevanceLabel.RELEVANT,
            claim_judgments=(AnswerClaimJudgment(claim_id="claim-a", label=AnswerClaimCorrectnessLabel.CORRECT),),
        ),
    }
    for c, r in zip(cases[1:], results[1:], strict=True):
        judgments_by_case[c.case_id] = ValidatedCaseJudgment(
            case_id=c.case_id,
            input_sha256=c.input_sha256,
            answer_sha256=r.answer_sha256 or "",
            relevance=AnswerRelevanceLabel.RELEVANT,
            claim_judgments=(),
        )
    judgments = _make_validated_judgments(judgments_by_case)

    build_result = _build_answer_metrics_with_diagnostics(
        dataset,
        results,
        human_judgments=judgments,
    )
    res = metric(build_result.metrics, "ANSWER_CORRECTNESS")

    assert res.execution_status.value == "COMPLETED"
    assert res.decision_status is not None
    assert res.decision_status.value == "INCONCLUSIVE"
    assert res.reason_code == "MINIMUM_VALID_BOOTSTRAP_REPLICATE_RATIO_NOT_MET"
    assert res.metric_value == "1"
    assert res.ci_lower is not None
    assert res.ci_upper is not None

    diag = build_result.bootstrap_diagnostics[0]
    assert diag.valid_replicates < 180
    assert Decimal(diag.valid_replicate_ratio) < Decimal("0.9")


@pytest.mark.parametrize(
    "invalid_ratio",
    ["0", "-0.1", "1.1", "0.90", "1.0", "abc", 0.9, "NaN", "Infinity"],
)
def test_answer_correctness_algorithm_signature_rejects_invalid_ratio(invalid_ratio: object) -> None:
    scope = _scope("ANSWER_CORRECTNESS", "CLAIM")
    params = dict(scope.ci_parameters or {})
    params["minimum_valid_replicate_ratio"] = cast(Any, invalid_ratio)
    invalid_scope = scope.model_copy(update={"ci_parameters": params})

    base_dataset = dataset_with_answer_scopes()
    policy = base_dataset.comparison_policy.model_copy(update={"scopes": (invalid_scope,)})
    dataset = replace(base_dataset, comparison_policy=policy)
    results = _build_answer_metrics(dataset, completed_answer_results(), human_judgments=_make_validated_judgments())
    res = metric(results, "ANSWER_CORRECTNESS")
    assert res.execution_status.value == "NOT_IMPLEMENTED"


def test_answer_correctness_algorithm_signature_rejects_missing_ratio() -> None:
    scope = _scope("ANSWER_CORRECTNESS", "CLAIM")
    params = dict(scope.ci_parameters or {})
    params.pop("minimum_valid_replicate_ratio", None)
    invalid_scope = scope.model_copy(update={"ci_parameters": params})

    dataset = dataset_with_answer_scopes(invalid_scope)
    results = _build_answer_metrics(dataset, completed_answer_results(), human_judgments=_make_validated_judgments())
    res = metric(results, "ANSWER_CORRECTNESS")
    assert res.execution_status.value == "NOT_IMPLEMENTED"


def test_relevance_algorithm_signature_rejects_extra_replicate_ratio() -> None:
    scope = _scope("RELEVANCE", "CASE")
    params = dict(scope.ci_parameters or {})
    params["minimum_valid_replicate_ratio"] = "0.9"
    invalid_scope = scope.model_copy(update={"ci_parameters": params})

    dataset = dataset_with_answer_scopes(invalid_scope)
    results = _build_answer_metrics(dataset, completed_answer_results(), human_judgments=_make_validated_judgments())
    res = metric(results, "RELEVANCE")
    assert res.execution_status.value == "NOT_IMPLEMENTED"


def test_build_answer_metrics_compatibility_wrapper() -> None:
    dataset = dataset_with_answer_scopes(_scope("ANSWER_CORRECTNESS", "CLAIM"))
    judgments = _make_validated_judgments()
    expected_inputs = {
        case.case_id: case.input_sha256
        for case in dataset.cases
        if case.task_type.value == "ANSWER_QUALITY" and case.partition is Partition.DEV
    }
    detailed = build_answer_metrics_with_diagnostics(
        dataset,
        completed_answer_results(),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case=expected_inputs,
        human_judgments=judgments,
    )
    wrapped = build_answer_metrics(
        dataset,
        completed_answer_results(),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case=expected_inputs,
        human_judgments=judgments,
    )

    assert isinstance(detailed, AnswerMetricBuildResult)
    assert wrapped == detailed.metrics
    assert all(isinstance(diag, AnswerBootstrapDiagnostic) for diag in detailed.bootstrap_diagnostics)


def test_answer_metrics_rejects_foreign_run_id_even_if_internally_consistent() -> None:
    dataset = dataset_with_answer_scopes()
    foreign_run_id = "15900000-0000-4000-8000-000000000099"
    results = tuple(r.model_copy(update={"run_id": foreign_run_id}) for r in completed_answer_results())
    expected_inputs = {
        case.case_id: case.input_sha256
        for case in dataset.cases
        if case.task_type.value == "ANSWER_QUALITY" and case.partition is Partition.DEV
    }

    metrics = build_answer_metrics(
        dataset,
        results,
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case=expected_inputs,
    )
    assert {result.execution_status.value for result in metrics.metrics} == {"INVALID"}


def test_answer_metrics_rejects_mismatched_expected_input_sha256() -> None:
    dataset = dataset_with_answer_scopes()
    expected_inputs = {
        case.case_id: "wrong_sha_value"
        for case in dataset.cases
        if case.task_type.value == "ANSWER_QUALITY" and case.partition is Partition.DEV
    }

    metrics = build_answer_metrics(
        dataset,
        completed_answer_results(),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case=expected_inputs,
    )
    assert {result.execution_status.value for result in metrics.metrics} == {"INVALID"}


def test_answer_metrics_requires_authoritative_bindings() -> None:
    dataset = dataset_with_answer_scopes()
    results = completed_answer_results()

    with pytest.raises(TypeError):
        build_answer_metrics(dataset, results)  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        build_answer_metrics_with_diagnostics(dataset, results)  # type: ignore[call-arg]
