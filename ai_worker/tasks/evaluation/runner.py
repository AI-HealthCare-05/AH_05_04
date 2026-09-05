from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from ai_worker.tasks.evaluation.canonical import JsonValue, sha256_hex
from ai_worker.tasks.evaluation.config import ResolvedDevExecution
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import EvaluationCaseContract, ValidatedDataset
from ai_worker.tasks.evaluation.manifest import CaseInputBinding, case_input_sha256
from ai_worker.tasks.evaluation.schemas.artifacts import (
    CASE_RESULT_ADAPTER,
    CaseResult,
    FailureRecord,
    FailureSummary,
)
from ai_worker.tasks.evaluation.schemas.common import (
    DecisionStatus,
    ExecutionStatus,
    ExperimentType,
    TaskType,
)

TASK_TYPES_BY_EXPERIMENT = {
    ExperimentType.KNOWLEDGE_RETRIEVAL: (TaskType.RETRIEVAL,),
    ExperimentType.ANSWER_GROUNDING_SAFETY: (
        TaskType.ANSWER_GROUNDING,
        TaskType.ANSWER_QUALITY,
        TaskType.SAFETY,
    ),
    ExperimentType.END_TO_END_RAG: (TaskType.END_TO_END_RAG,),
}
_BLOCKING_PRIORITY = {
    ExecutionStatus.INVALID: 0,
    ExecutionStatus.ERROR: 1,
    ExecutionStatus.NOT_IMPLEMENTED: 2,
    ExecutionStatus.NOT_EVALUATED: 3,
}
_EMPTY_ANSWER_SHA256 = sha256_hex(b"")


@dataclass(frozen=True, slots=True)
class AdapterRequest:
    run_id: str
    case: EvaluationCaseContract
    task_type: TaskType
    input_sha256: str
    case_resource_sha256: str
    variant_id: str
    variant_manifest_hash: str


class EvaluationAdapter(Protocol):
    def execute(self, request: AdapterRequest) -> CaseResult: ...


class AdapterRegistry(Protocol):
    def resolve(self, adapter_id: str) -> EvaluationAdapter | None: ...


class CaseSetValidator(Protocol):
    def validate_case_set(self, case_ids: Sequence[str]) -> None: ...


class EmptyAdapterRegistry:
    def resolve(self, adapter_id: str) -> None:
        del adapter_id
        return None


EMPTY_ADAPTER_REGISTRY = EmptyAdapterRegistry()


@dataclass(frozen=True, slots=True)
class RunOutcome:
    case_results: tuple[CaseResult, ...]
    failure_records: tuple[FailureRecord, ...]
    execution_status: ExecutionStatus
    decision_status: DecisionStatus | None
    blocking_execution_statuses: tuple[ExecutionStatus, ...]
    selected_case_ids: tuple[str, ...]
    task_types: tuple[TaskType, ...]


def aggregate_statuses(
    statuses: Sequence[ExecutionStatus],
) -> tuple[ExecutionStatus, DecisionStatus | None, tuple[ExecutionStatus, ...]]:
    blockers = tuple(
        sorted(
            {status for status in statuses if status is not ExecutionStatus.COMPLETED},
            key=_BLOCKING_PRIORITY.__getitem__,
        )
    )
    if not statuses:
        return ExecutionStatus.INVALID, None, (ExecutionStatus.INVALID,)
    if blockers:
        return blockers[0], None, blockers
    return ExecutionStatus.COMPLETED, DecisionStatus.NOT_APPLICABLE, ()


def _neutral_result(
    request: AdapterRequest,
    status: ExecutionStatus,
    code: EvaluationErrorCode | None,
) -> CaseResult:
    task_type = request.task_type
    payload: dict[str, JsonValue] = {
        "schema_id": "rag-eval.case-result",
        "schema_version": "1.0.0",
        "run_id": request.run_id,
        "case_id": request.case.case_id,
        "dataset_code": request.case.dataset_code,
        "dataset_version": request.case.dataset_version,
        "task_type": task_type.value,
        "partition": request.case.partition.value,
        "input_sha256": request.input_sha256,
        "execution_status": status.value,
        "decision_status": None,
        "failure_codes": [] if code is None else [code.value],
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
        "actual_retrieval_invocation": False,
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
    if task_type in {TaskType.ANSWER_GROUNDING, TaskType.ANSWER_QUALITY}:
        payload.update(
            actual_claim_ids=[],
            actual_citation_evidence_ids=[],
            actual_rule_ids=[],
            actual_scope_codes=[],
            actual_sections=[],
            omitted_sections=[],
            answer_sha256=_EMPTY_ANSWER_SHA256,
        )
    elif task_type in {TaskType.SAFETY, TaskType.END_TO_END_RAG}:
        payload.update(
            actual_claim_ids=[],
            actual_citation_evidence_ids=[],
            actual_rule_ids=[],
            actual_scope_codes=[],
            actual_response_level="UNKNOWN",
            actual_safety_disposition="UNKNOWN_RISK",
            actual_execution_status="NO_RESULT",
            actual_release_decision="REJECTED",
            actual_provider_invocation=False,
            actual_retrieval_invocation=False,
            actual_publication_allowed=False,
            actual_sections=[],
            omitted_sections=[],
            risk_level="INSUFFICIENT_DATA",
        )
    return CASE_RESULT_ADAPTER.validate_python(payload)


def _case_request(
    case: EvaluationCaseContract,
    dataset: ValidatedDataset,
    resolved: ResolvedDevExecution,
    run_id: str,
) -> AdapterRequest:
    if case.task_type is TaskType.RETRIEVAL:
        variant = resolved.request.retrieval_variant
        variant_hash = resolved.retrieval_variant_manifest_hash
    else:
        variant = resolved.request.answer_variant
        variant_hash = resolved.answer_variant_manifest_hash
    if variant is None or variant_hash is None:
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)
    resource_hashes = {item.case_id: item.sha256 for item in dataset.manifest.case_resources}
    binding = CaseInputBinding(
        case_id=case.case_id,
        task_type=case.task_type.value,
        partition=case.partition.value,
        case_resource_sha256=resource_hashes[case.case_id],
        dataset_manifest_sha256=dataset.manifest.manifest_sha256,
        evidence_mapping_manifest_sha256=dataset.evidence_mapping.manifest_sha256,
        critical_claim_rubric_hash=dataset.rubric.rubric_hash,
        resolved_evaluation_config_hash=resolved.resolved_evaluation_config_hash,
    )
    return AdapterRequest(
        run_id=run_id,
        case=case,
        task_type=case.task_type,
        input_sha256=case_input_sha256(binding),
        case_resource_sha256=binding.case_resource_sha256,
        variant_id=variant.variant_id,
        variant_manifest_hash=variant_hash,
    )


def _binding_matches(result: CaseResult, request: AdapterRequest) -> bool:
    return (
        result.run_id == request.run_id
        and result.case_id == request.case.case_id
        and result.task_type is request.task_type
        and result.dataset_code == request.case.dataset_code
        and result.dataset_version == request.case.dataset_version
        and result.partition is request.case.partition
        and result.input_sha256 == request.input_sha256
    )


def _result_contract_matches(result: CaseResult) -> bool:
    if result.task_type is not TaskType.RETRIEVAL:
        return True
    ranked_fields = (result.retrieved_evidence_ids or (), result.selected_evidence_ids or ())
    return all(len(evidence_ids) == len(set(evidence_ids)) for evidence_ids in ranked_fields)


def _execute_once(request: AdapterRequest, adapter: EvaluationAdapter | None) -> CaseResult:
    if adapter is None:
        return _neutral_result(request, ExecutionStatus.NOT_IMPLEMENTED, None)
    try:
        result = CASE_RESULT_ADAPTER.validate_python(adapter.execute(request))
    except EvaluationValidationError as error:
        if error.code is EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID:
            return _neutral_result(request, ExecutionStatus.INVALID, error.code)
        return _neutral_result(request, ExecutionStatus.ERROR, EvaluationErrorCode.INTERNAL_ERROR)
    except Exception:
        return _neutral_result(request, ExecutionStatus.ERROR, EvaluationErrorCode.INTERNAL_ERROR)
    if not _binding_matches(result, request):
        return _neutral_result(request, ExecutionStatus.INVALID, EvaluationErrorCode.MANIFEST_INVALID)
    if not _result_contract_matches(result):
        return _neutral_result(
            request,
            ExecutionStatus.INVALID,
            EvaluationErrorCode.RETRIEVAL_RESULT_INVALID,
        )
    return result


def _select_cases(
    dataset: ValidatedDataset,
    experiment_type: ExperimentType,
) -> tuple[EvaluationCaseContract, ...]:
    task_types = TASK_TYPES_BY_EXPERIMENT[experiment_type]
    selected = tuple(
        sorted(
            (case for case in dataset.cases if case.task_type in task_types),
            key=lambda case: (case.case_id.encode("utf-16-be"), case.task_type.value.encode("utf-16-be")),
        )
    )
    selected_keys = {(case.case_id, case.task_type) for case in selected}
    if len(selected_keys) != len(selected) or not set(task_types).issubset({case.task_type for case in selected}):
        return ()
    return selected


def _retrieval_failure_records(
    dataset: ValidatedDataset,
    case_results: tuple[CaseResult, ...],
    *,
    created_at: str,
) -> tuple[FailureRecord, ...]:
    cases_by_id = {case.case_id: case for case in dataset.cases}
    failures: list[FailureRecord] = []
    for result in case_results:
        case = cases_by_id[result.case_id]
        required_ids = set(case.expected.required_evidence_refs or ())
        ranked_ids = set((result.retrieved_evidence_ids or ())[:5])
        if result.task_type is TaskType.RETRIEVAL and result.execution_status is not ExecutionStatus.COMPLETED:
            failure_code = (
                result.failure_codes[0] if result.failure_codes else f"RETRIEVAL_{result.execution_status.value}"
            )
            failures.append(
                FailureRecord(
                    schema_id="rag-eval.failure",
                    schema_version="1.0.0",
                    run_id=result.run_id,
                    case_id=result.case_id,
                    failure_code=failure_code,
                    failure_stage="RETRIEVAL_EXECUTION",
                    expected_summary=FailureSummary.EXPECTED_REQUIRED_EVIDENCE,
                    actual_summary=FailureSummary.ACTUAL_REQUIRED_EVIDENCE_MISSING,
                    root_cause_code=None,
                    followup_issue_ref=None,
                    created_at=created_at,
                )
            )
            continue
        if result.task_type is not TaskType.RETRIEVAL or not required_ids or required_ids.issubset(ranked_ids):
            continue
        failures.append(
            FailureRecord(
                schema_id="rag-eval.failure",
                schema_version="1.0.0",
                run_id=result.run_id,
                case_id=result.case_id,
                failure_code="REQUIRED_EVIDENCE_NOT_IN_TOP_5",
                failure_stage="RETRIEVAL_MISS",
                expected_summary=FailureSummary.EXPECTED_REQUIRED_EVIDENCE,
                actual_summary=FailureSummary.ACTUAL_REQUIRED_EVIDENCE_MISSING,
                root_cause_code=None,
                followup_issue_ref=None,
                created_at=created_at,
            )
        )
    return tuple(failures)


def execute_dev_cases(
    dataset: ValidatedDataset,
    resolved: ResolvedDevExecution,
    *,
    run_id: str,
    adapter_registry: AdapterRegistry,
    failure_created_at: str | None = None,
) -> RunOutcome:
    task_types = TASK_TYPES_BY_EXPERIMENT[resolved.request.experiment_type]
    selected = _select_cases(dataset, resolved.request.experiment_type)
    if not selected:
        return RunOutcome(
            case_results=(),
            failure_records=(),
            execution_status=ExecutionStatus.INVALID,
            decision_status=None,
            blocking_execution_statuses=(ExecutionStatus.INVALID,),
            selected_case_ids=(),
            task_types=task_types,
        )
    adapter = adapter_registry.resolve(dataset.suite.adapter_id)
    requests = tuple(_case_request(case, dataset, resolved, run_id) for case in selected)
    validator = getattr(adapter, "validate_case_set", None)
    if callable(validator):
        try:
            cast(CaseSetValidator, adapter).validate_case_set(tuple(case.case_id for case in selected))
        except EvaluationValidationError as error:
            status = (
                ExecutionStatus.INVALID
                if error.code is EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID
                else ExecutionStatus.ERROR
            )
            code = error.code if status is ExecutionStatus.INVALID else EvaluationErrorCode.INTERNAL_ERROR
            case_results = tuple(_neutral_result(request, status, code) for request in requests)
        else:
            case_results = tuple(_execute_once(request, adapter) for request in requests)
    else:
        case_results = tuple(_execute_once(request, adapter) for request in requests)
    status, decision, blockers = aggregate_statuses([result.execution_status for result in case_results])
    failure_records = _retrieval_failure_records(
        dataset,
        case_results,
        created_at=failure_created_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    )
    return RunOutcome(
        case_results=case_results,
        failure_records=failure_records,
        execution_status=status,
        decision_status=decision,
        blocking_execution_statuses=blockers,
        selected_case_ids=tuple(result.case_id for result in case_results),
        task_types=task_types,
    )
