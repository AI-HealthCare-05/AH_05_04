from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ai_worker.tasks.evaluation.canonical import sha256_hex
from ai_worker.tasks.evaluation.config import RepositoryState, load_dev_execution_request
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import ValidatedDataset, load_dataset
from ai_worker.tasks.evaluation.retrieval_replay import build_adapter_registry
from ai_worker.tasks.evaluation.runner import (
    AdapterRequest,
    EvaluationAdapter,
    aggregate_statuses,
    execute_dev_cases,
)
from ai_worker.tasks.evaluation.schemas.artifacts import CASE_RESULT_ADAPTER, CaseResult
from ai_worker.tasks.evaluation.schemas.common import DecisionStatus, ExecutionStatus

REPOSITORY_ROOT = Path(__file__).parents[3]
SOURCE_MANIFEST = REPOSITORY_ROOT / "evals/retrieval/manifests/dev-foundation-v1.dataset.json"
RETRIEVAL_MANIFEST = REPOSITORY_ROOT / "evals/retrieval/manifests/rag-retrieval-dev-v1.dataset.json"
RUN_ID = "123e4567-e89b-42d3-a456-426614174000"


@pytest.fixture(scope="module")
def loaded_dev_dataset() -> ValidatedDataset:
    return load_dataset(SOURCE_MANIFEST, evals_root=REPOSITORY_ROOT / "evals")


def _resolved(experiment: str):
    names = {
        "KNOWLEDGE_RETRIEVAL": "dev-foundation-knowledge-retrieval-v1.execution.json",
        "ANSWER_GROUNDING_SAFETY": "dev-foundation-answer-grounding-safety-v1.execution.json",
        "END_TO_END_RAG": "dev-foundation-end-to-end-rag-v1.execution.json",
    }
    return load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs" / names[experiment],
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )


def _result_payload(request: AdapterRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.case-result",
        "schema_version": "1.0.0",
        "run_id": request.run_id,
        "case_id": request.case.case_id,
        "dataset_code": request.case.dataset_code,
        "dataset_version": request.case.dataset_version,
        "task_type": request.task_type.value,
        "partition": request.case.partition.value,
        "input_sha256": request.input_sha256,
        "execution_status": "COMPLETED",
        "decision_status": "N/A",
        "failure_codes": [],
        "retrieved_evidence_ids": [],
        "selected_evidence_ids": [],
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
        "latency_ms": None,
        "input_token_count": None,
        "output_token_count": None,
        "estimated_cost": None,
    }
    if request.task_type.value in {"ANSWER_GROUNDING", "ANSWER_QUALITY"}:
        payload.update(
            actual_claim_ids=[],
            actual_citation_evidence_ids=[],
            actual_rule_ids=[],
            actual_scope_codes=[],
            actual_retrieval_invocation=True,
            actual_sections=[],
            omitted_sections=[],
            answer_sha256=sha256_hex(b"synthetic answer"),
        )
    elif request.task_type.value in {"SAFETY", "END_TO_END_RAG"}:
        payload.update(
            actual_claim_ids=[],
            actual_citation_evidence_ids=[],
            actual_rule_ids=[],
            actual_scope_codes=[],
            actual_response_level="UNKNOWN",
            actual_safety_disposition="UNKNOWN_RISK",
            actual_execution_status="SUCCEEDED",
            actual_release_decision="LIMITED",
            actual_provider_invocation=False,
            actual_retrieval_invocation=True,
            actual_publication_allowed=False,
            actual_sections=[],
            omitted_sections=[],
            risk_level="INSUFFICIENT_DATA",
            answer_sha256=sha256_hex(b"synthetic answer"),
        )
    return payload


class CountingAdapter(EvaluationAdapter):
    def __init__(self, *, fail_case_id: str | None = None, corrupt_run_id: bool = False) -> None:
        self.fail_case_id = fail_case_id
        self.corrupt_run_id = corrupt_run_id
        self.calls: list[str] = []
        self.requests: list[AdapterRequest] = []

    def execute(self, request: AdapterRequest) -> CaseResult:
        self.calls.append(request.case.case_id)
        self.requests.append(request)
        if request.case.case_id == self.fail_case_id:
            raise RuntimeError("technical failure patient@example.com")
        payload = _result_payload(request)
        if self.corrupt_run_id:
            payload["run_id"] = "123e4567-e89b-42d3-a456-426614174999"
        return CASE_RESULT_ADAPTER.validate_python(payload)


class InvalidReplayAdapter(CountingAdapter):
    def execute(self, request: AdapterRequest) -> CaseResult:
        if request.case.case_id == "rag-dev-answer-quality-001":
            raise EvaluationValidationError(EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID)
        return super().execute(request)


class StaticRegistry:
    def __init__(self, adapter: EvaluationAdapter | None) -> None:
        self.adapter = adapter

    def resolve(self, adapter_id: str) -> EvaluationAdapter | None:
        assert adapter_id == "validation-only.v1"
        return self.adapter


@pytest.mark.parametrize(
    ("experiment_type", "expected_tasks"),
    [
        ("KNOWLEDGE_RETRIEVAL", ["RETRIEVAL"]),
        ("ANSWER_GROUNDING_SAFETY", ["ANSWER_GROUNDING", "ANSWER_QUALITY", "SAFETY"]),
        ("END_TO_END_RAG", ["END_TO_END_RAG"]),
    ],
)
def test_selects_only_tasks_for_experiment_in_utf16_order(
    loaded_dev_dataset: ValidatedDataset,
    experiment_type: str,
    expected_tasks: list[str],
) -> None:
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        _resolved(experiment_type),
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(CountingAdapter()),
    )

    assert [item.task_type.value for item in outcome.case_results] == expected_tasks
    assert outcome.execution_status is ExecutionStatus.COMPLETED
    assert outcome.decision_status is DecisionStatus.NOT_APPLICABLE


def test_adapter_exception_is_recorded_once_and_next_case_runs(
    loaded_dev_dataset: ValidatedDataset,
    capsys: pytest.CaptureFixture[str],
) -> None:
    adapter = CountingAdapter(fail_case_id="rag-dev-answer-quality-001")

    outcome = execute_dev_cases(
        loaded_dev_dataset,
        _resolved("ANSWER_GROUNDING_SAFETY"),
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )

    assert adapter.calls == [
        "rag-dev-answer-grounding-001",
        "rag-dev-answer-quality-001",
        "rag-dev-safety-001",
    ]
    failed = next(item for item in outcome.case_results if item.case_id == "rag-dev-answer-quality-001")
    assert (failed.execution_status.value, failed.decision_status) == ("ERROR", None)
    assert failed.failure_codes == ("EVAL_INTERNAL_ERROR",)
    assert outcome.blocking_execution_statuses == (ExecutionStatus.ERROR,)
    assert outcome.failure_records == ()
    assert "patient@example.com" not in repr(outcome)
    assert "patient@example.com" not in capsys.readouterr().err


def test_replay_validation_error_is_invalid_and_next_case_runs(loaded_dev_dataset: ValidatedDataset) -> None:
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        _resolved("ANSWER_GROUNDING_SAFETY"),
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(InvalidReplayAdapter()),
    )

    failed = next(item for item in outcome.case_results if item.case_id == "rag-dev-answer-quality-001")
    assert failed.execution_status is ExecutionStatus.INVALID
    assert failed.failure_codes == ("EVAL_RETRIEVAL_REPLAY_INVALID",)
    assert outcome.execution_status is ExecutionStatus.INVALID


def test_adapter_request_is_bound_to_active_variant(loaded_dev_dataset: ValidatedDataset) -> None:
    adapter = CountingAdapter()
    resolved = _resolved("KNOWLEDGE_RETRIEVAL")

    execute_dev_cases(
        loaded_dev_dataset,
        resolved,
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )

    assert adapter.requests[0].variant_id == "dev-synthetic-retrieval-v1"
    assert adapter.requests[0].variant_manifest_hash == resolved.retrieval_variant_manifest_hash


def test_retrieval_miss_creates_stable_non_sensitive_failure_record() -> None:
    dataset = load_dataset(RETRIEVAL_MANIFEST, evals_root=REPOSITORY_ROOT / "evals")
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/rag-retrieval-dev-ret-l-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )

    outcome = execute_dev_cases(
        dataset,
        resolved,
        run_id=RUN_ID,
        adapter_registry=build_adapter_registry(resolved),
    )

    assert len(outcome.failure_records) == 1
    failure = outcome.failure_records[0]
    assert failure.case_id == "rag-ret-dev-004"
    assert failure.failure_stage == "RETRIEVAL_MISS"
    assert failure.failure_code == "REQUIRED_EVIDENCE_NOT_IN_TOP_5"
    assert failure.expected_summary.value == "EXPECTED_REQUIRED_EVIDENCE"
    assert failure.actual_summary.value == "ACTUAL_REQUIRED_EVIDENCE_MISSING"
    assert failure.root_cause_code is None
    assert failure.followup_issue_ref is None


def test_missing_adapter_produces_not_implemented_without_fake_answer(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        _resolved("ANSWER_GROUNDING_SAFETY"),
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(None),
    )

    assert all(item.execution_status is ExecutionStatus.NOT_IMPLEMENTED for item in outcome.case_results)
    assert all(item.decision_status is None for item in outcome.case_results)
    answer = next(item for item in outcome.case_results if item.task_type.value == "ANSWER_QUALITY")
    assert answer.answer_sha256 == sha256_hex(b"")
    assert answer.actual_claim_ids == ()
    assert answer.failure_codes == ()
    assert outcome.decision_status is None


def test_adapter_result_with_wrong_binding_becomes_invalid(loaded_dev_dataset: ValidatedDataset) -> None:
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        _resolved("KNOWLEDGE_RETRIEVAL"),
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(CountingAdapter(corrupt_run_id=True)),
    )

    assert outcome.case_results[0].execution_status is ExecutionStatus.INVALID
    assert outcome.case_results[0].failure_codes == ("EVAL_MANIFEST_INVALID",)
    assert outcome.execution_status is ExecutionStatus.INVALID
    assert outcome.decision_status is None


@pytest.mark.parametrize(
    ("statuses", "expected_status", "expected_blockers"),
    [
        (
            [ExecutionStatus.NOT_EVALUATED, ExecutionStatus.ERROR, ExecutionStatus.NOT_IMPLEMENTED],
            ExecutionStatus.ERROR,
            (ExecutionStatus.ERROR, ExecutionStatus.NOT_IMPLEMENTED, ExecutionStatus.NOT_EVALUATED),
        ),
        (
            [ExecutionStatus.ERROR, ExecutionStatus.INVALID],
            ExecutionStatus.INVALID,
            (ExecutionStatus.INVALID, ExecutionStatus.ERROR),
        ),
        (
            [ExecutionStatus.COMPLETED],
            ExecutionStatus.COMPLETED,
            (),
        ),
    ],
)
def test_aggregate_keeps_all_blockers_in_normative_order(
    statuses: list[ExecutionStatus],
    expected_status: ExecutionStatus,
    expected_blockers: tuple[ExecutionStatus, ...],
) -> None:
    status, decision, blockers = aggregate_statuses(statuses)

    assert status is expected_status
    assert decision is (DecisionStatus.NOT_APPLICABLE if status is ExecutionStatus.COMPLETED else None)
    assert blockers == expected_blockers
