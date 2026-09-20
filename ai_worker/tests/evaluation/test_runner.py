from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from ai_worker.tasks.evaluation.answer_runtime_binding import (
    AnswerRuntimeSupplementalCarrierSnapshot,
    MaterializedAnswerRuntimeSupplementalBindings,
    ProviderInvocationObservation,
    compute_input_context_binding_hash_from_cases,
)
from ai_worker.tasks.evaluation.canonical import sha256_hex
from ai_worker.tasks.evaluation.config import (
    DevExecutionRequest,
    DevVariant,
    RepositoryState,
    ResolvedDevExecution,
    load_dev_execution_request,
)
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import ValidatedDataset, load_dataset
from ai_worker.tasks.evaluation.retrieval_metrics import build_retrieval_metrics
from ai_worker.tasks.evaluation.retrieval_replay import build_adapter_registry
from ai_worker.tasks.evaluation.runner import (
    AdapterRequest,
    AnswerRuntimeSupplementalCarrierProvider,
    AsyncEvaluationAdapter,
    EvaluationAdapter,
    aggregate_statuses,
    execute_dev_cases,
    execute_dev_cases_async,
    materialize_answer_runtime_supplemental_from_outcome,
)
from ai_worker.tasks.evaluation.schemas.artifacts import CASE_RESULT_ADAPTER, CaseResult
from ai_worker.tasks.evaluation.schemas.common import DecisionStatus, ExecutionStatus, ExperimentType
from ai_worker.tasks.rag.guideline_card import GuidelineGenerationProvenance, ImmutableArtifactRef

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


class DuplicateRankedIdsAdapter(CountingAdapter):
    def execute(self, request: AdapterRequest) -> CaseResult:
        result = super().execute(request)
        if request.case.case_id == "rag-ret-dev-001":
            result = result.model_copy(
                update={
                    "retrieved_evidence_ids": ("duplicate-evidence", "duplicate-evidence"),
                    "selected_evidence_ids": ("duplicate-evidence", "duplicate-evidence"),
                }
            )
        return cast(CaseResult, result)


class StaticRegistry:
    def __init__(self, adapter: EvaluationAdapter | AsyncEvaluationAdapter | None) -> None:
        self.adapter = adapter

    def resolve(self, adapter_id: str) -> EvaluationAdapter | AsyncEvaluationAdapter | None:
        assert adapter_id == "validation-only.v1"
        return self.adapter


class RetrievalRegistry:
    def __init__(self, adapter: EvaluationAdapter | None) -> None:
        self.adapter = adapter

    def resolve(self, adapter_id: str) -> EvaluationAdapter | None:
        assert adapter_id == "retrieval-replay.v1"
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


def test_retrieval_adapter_error_creates_stable_non_sensitive_failure_record() -> None:
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
        adapter_registry=RetrievalRegistry(CountingAdapter(fail_case_id="rag-ret-dev-001")),
    )

    failure = next(item for item in outcome.failure_records if item.case_id == "rag-ret-dev-001")
    assert failure.failure_stage == "RETRIEVAL_EXECUTION"
    assert failure.failure_code == "EVAL_INTERNAL_ERROR"
    assert failure.expected_summary.value == "EXPECTED_REQUIRED_EVIDENCE"
    assert failure.actual_summary.value == "ACTUAL_REQUIRED_EVIDENCE_MISSING"


def test_duplicate_ranked_ids_invalidate_case_run_and_metrics_with_stable_failure() -> None:
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
        adapter_registry=RetrievalRegistry(DuplicateRankedIdsAdapter()),
    )

    invalid = next(item for item in outcome.case_results if item.case_id == "rag-ret-dev-001")
    assert invalid.execution_status is ExecutionStatus.INVALID
    assert invalid.decision_status is None
    assert invalid.failure_codes == ("EVAL_RETRIEVAL_RESULT_INVALID",)
    assert outcome.execution_status is ExecutionStatus.INVALID
    failure = next(item for item in outcome.failure_records if item.case_id == invalid.case_id)
    assert (failure.failure_stage, failure.failure_code) == (
        "RETRIEVAL_EXECUTION",
        "EVAL_RETRIEVAL_RESULT_INVALID",
    )

    metrics = build_retrieval_metrics(dataset, outcome.case_results)
    assert {item.execution_status for item in metrics.metrics} == {ExecutionStatus.INVALID}
    assert all(item.decision_status is None and item.metric_value is None for item in metrics.metrics)


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


def _provenance() -> GuidelineGenerationProvenance:
    return GuidelineGenerationProvenance(
        prompt_ref=ImmutableArtifactRef("guideline-prompt", "1.0.0", "1" * 64),
        model_ref=ImmutableArtifactRef("guideline-model", "1.0.0", "2" * 64),
        parser_ref=ImmutableArtifactRef("guideline-parser", "1.0.0", "3" * 64),
        validator_ref=ImmutableArtifactRef("guideline-validator", "1.0.0", "4" * 64),
    )


def _observations_for_case_ids(
    run_id: str,
    case_ids: tuple[str, ...],
    *,
    variant_id: str = "ANS-RAG",
    temperature: str = "0",
    max_output_tokens: int = 2048,
    timeout_seconds: Decimal = Decimal("30"),
) -> tuple[ProviderInvocationObservation, ...]:
    return tuple(
        ProviderInvocationObservation(
            case_id=cid,
            run_id=run_id,
            variant_id=variant_id,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        for cid in case_ids
    )


class SyntheticAnswerCarrierAdapter(EvaluationAdapter, AnswerRuntimeSupplementalCarrierProvider):
    def __init__(
        self,
        *,
        run_id: str = RUN_ID,
        variant_id: str = "ANS-RAG",
        temperature: str = "0",
        max_output_tokens: int = 2048,
        timeout_seconds: Decimal = Decimal("30"),
        fail_case_id: str | None = None,
        snapshot_override: Any = None,
        raise_on_snapshot: Exception | None = None,
    ) -> None:
        self.run_id = run_id
        self.variant_id = variant_id
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.fail_case_id = fail_case_id
        self.snapshot_override = snapshot_override
        self.raise_on_snapshot = raise_on_snapshot
        self.executed_case_ids: list[str] = []
        self.snapshot_calls: int = 0

    def execute(self, request: AdapterRequest) -> CaseResult:
        self.executed_case_ids.append(request.case.case_id)
        if request.case.case_id == self.fail_case_id:
            raise RuntimeError(f"technical failure for case {request.case.case_id}")
        payload = _result_payload(request)
        return CASE_RESULT_ADAPTER.validate_python(payload)

    def snapshot_answer_runtime_supplemental_carrier(
        self,
    ) -> AnswerRuntimeSupplementalCarrierSnapshot:
        self.snapshot_calls += 1
        if self.raise_on_snapshot is not None:
            raise self.raise_on_snapshot
        if self.snapshot_override is not None:
            return cast(AnswerRuntimeSupplementalCarrierSnapshot, self.snapshot_override)
        return AnswerRuntimeSupplementalCarrierSnapshot(
            guideline_provenance=_provenance(),
            provider_observations=_observations_for_case_ids(
                self.run_id,
                tuple(self.executed_case_ids),
                variant_id=self.variant_id,
                temperature=self.temperature,
                max_output_tokens=self.max_output_tokens,
                timeout_seconds=self.timeout_seconds,
            ),
        )


class AsyncSyntheticAnswerCarrierAdapter(AsyncEvaluationAdapter, AnswerRuntimeSupplementalCarrierProvider):
    def __init__(
        self,
        *,
        run_id: str = RUN_ID,
        variant_id: str = "ANS-RAG",
        temperature: str = "0",
        max_output_tokens: int = 2048,
        timeout_seconds: Decimal = Decimal("30"),
        fail_case_id: str | None = None,
        snapshot_override: Any = None,
        raise_on_snapshot: Exception | None = None,
    ) -> None:
        self.run_id = run_id
        self.variant_id = variant_id
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.fail_case_id = fail_case_id
        self.snapshot_override = snapshot_override
        self.raise_on_snapshot = raise_on_snapshot
        self.executed_case_ids: list[str] = []
        self.snapshot_calls: int = 0

    async def execute(self, request: AdapterRequest) -> CaseResult:
        self.executed_case_ids.append(request.case.case_id)
        if request.case.case_id == self.fail_case_id:
            raise RuntimeError(f"technical failure for case {request.case.case_id}")
        payload = _result_payload(request)
        return CASE_RESULT_ADAPTER.validate_python(payload)

    def snapshot_answer_runtime_supplemental_carrier(
        self,
    ) -> AnswerRuntimeSupplementalCarrierSnapshot:
        self.snapshot_calls += 1
        if self.raise_on_snapshot is not None:
            raise self.raise_on_snapshot
        if self.snapshot_override is not None:
            return cast(AnswerRuntimeSupplementalCarrierSnapshot, self.snapshot_override)
        return AnswerRuntimeSupplementalCarrierSnapshot(
            guideline_provenance=_provenance(),
            provider_observations=_observations_for_case_ids(
                self.run_id,
                tuple(self.executed_case_ids),
                variant_id=self.variant_id,
                temperature=self.temperature,
                max_output_tokens=self.max_output_tokens,
                timeout_seconds=self.timeout_seconds,
            ),
        )


def _synthetic_answer_variant(
    variant_id: str = "ANS-RAG",
    *,
    token_limit: int = 2048,
    timeout: int = 30,
) -> DevVariant:
    return DevVariant(
        variant_id=variant_id,
        variant_version="1.0.0",
        kind="ANSWER",
        model_config={"model": "gpt-4o"},
        prompt_version="1.0.0",
        parameters={"token_limit": token_limit, "timeout": timeout},
    )


def _resolved_with_answer_variant(
    base_resolved: ResolvedDevExecution,
    *,
    variant_id: str = "ANS-RAG",
    token_limit: int = 2048,
    timeout: int = 30,
    experiment_type: ExperimentType | None = None,
) -> ResolvedDevExecution:
    var = _synthetic_answer_variant(variant_id, token_limit=token_limit, timeout=timeout)
    req_data = base_resolved.request.model_dump(by_alias=True)
    req_data["variant_id"] = variant_id
    req_data["answer_variant"] = var.model_dump(by_alias=True)
    if experiment_type is not None:
        req_data["experiment_type"] = experiment_type.value
    new_request = DevExecutionRequest.model_validate(req_data)
    return replace(base_resolved, request=new_request)


# --- Section 34: Runner Carrier Transport Tests ---


def test_runner_carrier_none_when_adapter_has_no_carrier(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    adapter = CountingAdapter()
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        _resolved("ANSWER_GROUNDING_SAFETY"),
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    assert outcome.execution_status is ExecutionStatus.COMPLETED
    assert outcome.answer_runtime_supplemental_carrier is None


def test_runner_carrier_captured_exactly_once_on_completed_sync(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    adapter = SyntheticAnswerCarrierAdapter()
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        _resolved("ANSWER_GROUNDING_SAFETY"),
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    assert outcome.execution_status is ExecutionStatus.COMPLETED
    assert adapter.snapshot_calls == 1
    carrier = outcome.answer_runtime_supplemental_carrier
    assert carrier is not None
    assert isinstance(carrier, AnswerRuntimeSupplementalCarrierSnapshot)
    assert len(carrier.provider_observations) == len(outcome.case_results)


@pytest.mark.asyncio
async def test_runner_carrier_captured_in_async_execution(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    adapter = AsyncSyntheticAnswerCarrierAdapter()
    outcome = await execute_dev_cases_async(
        loaded_dev_dataset,
        _resolved("ANSWER_GROUNDING_SAFETY"),
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    assert outcome.execution_status is ExecutionStatus.COMPLETED
    assert adapter.snapshot_calls == 1
    assert outcome.answer_runtime_supplemental_carrier is not None

    # Parity check: sync runner wrapping async adapter produces identical carrier
    sync_wrap_adapter = AsyncSyntheticAnswerCarrierAdapter()
    outcome_sync = execute_dev_cases(
        loaded_dev_dataset,
        _resolved("ANSWER_GROUNDING_SAFETY"),
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(sync_wrap_adapter),
    )
    assert outcome_sync.execution_status is ExecutionStatus.COMPLETED
    assert sync_wrap_adapter.snapshot_calls == 1
    assert outcome_sync.answer_runtime_supplemental_carrier == outcome.answer_runtime_supplemental_carrier


def test_runner_carrier_not_promoted_on_incomplete_or_error_outcome(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    adapter = SyntheticAnswerCarrierAdapter(fail_case_id="rag-dev-answer-quality-001")
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        _resolved("ANSWER_GROUNDING_SAFETY"),
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    assert outcome.execution_status is ExecutionStatus.ERROR
    assert adapter.snapshot_calls == 0
    assert outcome.answer_runtime_supplemental_carrier is None


def test_runner_carrier_fails_closed_on_wrong_snapshot_type(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    # 1) Non-snapshot object returned
    bad_type_adapter = SyntheticAnswerCarrierAdapter(snapshot_override="invalid-snapshot-payload")
    with pytest.raises(EvaluationValidationError) as exc:
        execute_dev_cases(
            loaded_dev_dataset,
            _resolved("ANSWER_GROUNDING_SAFETY"),
            run_id=RUN_ID,
            adapter_registry=StaticRegistry(bad_type_adapter),
        )
    assert exc.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID

    # 2) None returned when carrier snapshot method exists
    class NoneCarrierAdapter(CountingAdapter):
        def snapshot_answer_runtime_supplemental_carrier(self) -> Any:
            return None

    with pytest.raises(EvaluationValidationError) as exc_none:
        execute_dev_cases(
            loaded_dev_dataset,
            _resolved("ANSWER_GROUNDING_SAFETY"),
            run_id=RUN_ID,
            adapter_registry=StaticRegistry(NoneCarrierAdapter()),
        )
    assert exc_none.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID

    # 3) Attribute exists but is uncallable
    class UncallableCarrierAdapter(CountingAdapter):
        snapshot_answer_runtime_supplemental_carrier = "not-callable"

    with pytest.raises(EvaluationValidationError) as exc_uncallable:
        execute_dev_cases(
            loaded_dev_dataset,
            _resolved("ANSWER_GROUNDING_SAFETY"),
            run_id=RUN_ID,
            adapter_registry=StaticRegistry(UncallableCarrierAdapter()),
        )
    assert exc_uncallable.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_runner_carrier_fails_closed_when_snapshot_raises(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    adapter = SyntheticAnswerCarrierAdapter(
        raise_on_snapshot=RuntimeError("transient provider snapshot failure"),
    )
    with pytest.raises(EvaluationValidationError) as exc:
        execute_dev_cases(
            loaded_dev_dataset,
            _resolved("ANSWER_GROUNDING_SAFETY"),
            run_id=RUN_ID,
            adapter_registry=StaticRegistry(adapter),
        )
    assert exc.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_runner_carrier_not_consumed_in_knowledge_retrieval(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    adapter = SyntheticAnswerCarrierAdapter()
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        _resolved("KNOWLEDGE_RETRIEVAL"),
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    assert outcome.execution_status is ExecutionStatus.COMPLETED
    assert adapter.snapshot_calls == 0
    assert outcome.answer_runtime_supplemental_carrier is None


# --- Section 35: Bridge Materialization Tests ---


def test_bridge_completed_outcome_with_valid_carrier_generates_7_hashes(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    resolved = _resolved_with_answer_variant(
        _resolved("ANSWER_GROUNDING_SAFETY"),
        variant_id="ANS-RAG",
    )
    adapter = SyntheticAnswerCarrierAdapter(run_id=RUN_ID, variant_id="ANS-RAG")
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        resolved,
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    bindings = materialize_answer_runtime_supplemental_from_outcome(
        resolved=resolved,
        run_id=RUN_ID,
        outcome=outcome,
    )
    assert isinstance(bindings, MaterializedAnswerRuntimeSupplementalBindings)
    assert len(bindings.input_context_hash) == 64
    assert len(bindings.seed_hash) == 64
    assert len(bindings.prompt_structure_hash) == 64
    assert len(bindings.parser_hash) == 64
    assert len(bindings.sampling_parameters_hash) == 64
    assert len(bindings.token_limit_hash) == 64
    assert len(bindings.timeout_hash) == 64


def test_bridge_required_case_ids_supplied_from_outcome_selected_case_ids(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    resolved = _resolved_with_answer_variant(
        _resolved("ANSWER_GROUNDING_SAFETY"),
        variant_id="ANS-RAG",
    )
    adapter = SyntheticAnswerCarrierAdapter(run_id=RUN_ID, variant_id="ANS-RAG")
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        resolved,
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    bindings = materialize_answer_runtime_supplemental_from_outcome(
        resolved=resolved,
        run_id=RUN_ID,
        outcome=outcome,
    )
    expected_input_hash = compute_input_context_binding_hash_from_cases(
        outcome.case_results,
    )
    assert bindings.input_context_hash == expected_input_hash
    # Also verify that if outcome has required_case_ids that don't match cases, D1 materializer rejects it
    mismatched_case_outcome = replace(
        outcome,
        case_results=(outcome.case_results[0],),
    )
    with pytest.raises(EvaluationValidationError) as exc:
        materialize_answer_runtime_supplemental_from_outcome(
            resolved=resolved,
            run_id=RUN_ID,
            outcome=mismatched_case_outcome,
        )
    assert exc.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_bridge_fails_closed_when_carrier_is_none(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    resolved = _resolved_with_answer_variant(
        _resolved("ANSWER_GROUNDING_SAFETY"),
        variant_id="ANS-RAG",
    )
    adapter = CountingAdapter()
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        resolved,
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    assert outcome.answer_runtime_supplemental_carrier is None
    with pytest.raises(EvaluationValidationError) as exc:
        materialize_answer_runtime_supplemental_from_outcome(
            resolved=resolved,
            run_id=RUN_ID,
            outcome=outcome,
        )
    assert exc.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_bridge_fails_closed_when_outcome_not_completed(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    resolved = _resolved_with_answer_variant(
        _resolved("ANSWER_GROUNDING_SAFETY"),
        variant_id="ANS-RAG",
    )
    adapter = SyntheticAnswerCarrierAdapter(run_id=RUN_ID, variant_id="ANS-RAG")
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        resolved,
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    # Non-COMPLETED status
    err_outcome = replace(outcome, execution_status=ExecutionStatus.ERROR)
    with pytest.raises(EvaluationValidationError) as exc:
        materialize_answer_runtime_supplemental_from_outcome(
            resolved=resolved,
            run_id=RUN_ID,
            outcome=err_outcome,
        )
    assert exc.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID

    # Non-empty blockers
    blocking_outcome = replace(outcome, blocking_execution_statuses=(ExecutionStatus.ERROR,))
    with pytest.raises(EvaluationValidationError) as exc2:
        materialize_answer_runtime_supplemental_from_outcome(
            resolved=resolved,
            run_id=RUN_ID,
            outcome=blocking_outcome,
        )
    assert exc2.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_bridge_fails_closed_when_selected_case_ids_duplicate_or_mismatched(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    resolved = _resolved_with_answer_variant(
        _resolved("ANSWER_GROUNDING_SAFETY"),
        variant_id="ANS-RAG",
    )
    adapter = SyntheticAnswerCarrierAdapter(run_id=RUN_ID, variant_id="ANS-RAG")
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        resolved,
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    # Duplicate selected_case_ids
    dup_outcome = replace(
        outcome,
        selected_case_ids=(outcome.selected_case_ids[0], outcome.selected_case_ids[0]),
    )
    with pytest.raises(EvaluationValidationError) as exc:
        materialize_answer_runtime_supplemental_from_outcome(
            resolved=resolved,
            run_id=RUN_ID,
            outcome=dup_outcome,
        )
    assert exc.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID

    # Empty selected_case_ids
    empty_outcome = replace(outcome, selected_case_ids=())
    with pytest.raises(EvaluationValidationError) as exc2:
        materialize_answer_runtime_supplemental_from_outcome(
            resolved=resolved,
            run_id=RUN_ID,
            outcome=empty_outcome,
        )
    assert exc2.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_bridge_fails_closed_on_wrong_variant_or_wrong_experiment(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    resolved = _resolved_with_answer_variant(
        _resolved("ANSWER_GROUNDING_SAFETY"),
        variant_id="ANS-RAG",
    )
    adapter = SyntheticAnswerCarrierAdapter(run_id=RUN_ID, variant_id="ANS-RAG")
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        resolved,
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    # Wrong experiment: KNOWLEDGE_RETRIEVAL
    retrieval_resolved = _resolved("KNOWLEDGE_RETRIEVAL")
    with pytest.raises(EvaluationValidationError) as exc:
        materialize_answer_runtime_supplemental_from_outcome(
            resolved=retrieval_resolved,
            run_id=RUN_ID,
            outcome=outcome,
        )
    assert exc.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_bridge_fails_closed_on_config_observation_mismatch(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    # Config specifies token_limit=4096, but carrier provides max_output_tokens=2048
    resolved = _resolved_with_answer_variant(
        _resolved("ANSWER_GROUNDING_SAFETY"),
        variant_id="ANS-RAG",
        token_limit=4096,
    )
    adapter = SyntheticAnswerCarrierAdapter(
        run_id=RUN_ID,
        variant_id="ANS-RAG",
        max_output_tokens=2048,
    )
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        resolved,
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    with pytest.raises(EvaluationValidationError) as exc:
        materialize_answer_runtime_supplemental_from_outcome(
            resolved=resolved,
            run_id=RUN_ID,
            outcome=outcome,
        )
    assert exc.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_bridge_fails_closed_on_invalid_guideline_provenance(
    loaded_dev_dataset: ValidatedDataset,
) -> None:
    resolved = _resolved_with_answer_variant(
        _resolved("ANSWER_GROUNDING_SAFETY"),
        variant_id="ANS-RAG",
    )
    bad_prov = replace(
        _provenance(),
        prompt_ref=ImmutableArtifactRef("wrong-prompt-name", "1.0.0", "1" * 64),
    )
    adapter = SyntheticAnswerCarrierAdapter(
        run_id=RUN_ID,
        variant_id="ANS-RAG",
        snapshot_override=AnswerRuntimeSupplementalCarrierSnapshot(
            guideline_provenance=bad_prov,
            provider_observations=_observations_for_case_ids(
                RUN_ID,
                ("rag-dev-answer-grounding-001", "rag-dev-answer-quality-001", "rag-dev-safety-001"),
            ),
        ),
    )
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        resolved,
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    with pytest.raises(EvaluationValidationError) as exc:
        materialize_answer_runtime_supplemental_from_outcome(
            resolved=resolved,
            run_id=RUN_ID,
            outcome=outcome,
        )
    assert exc.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID
