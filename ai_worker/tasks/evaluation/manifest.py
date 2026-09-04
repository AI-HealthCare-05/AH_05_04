from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ValidationError

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.config import ResolvedDevExecution
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import ValidatedDataset
from ai_worker.tasks.evaluation.retrieval_metrics import (
    aggregate_metric_scores,
    build_retrieval_metrics,
    metric_result_fields,
    metric_scores,
    observations_from_case_results,
)
from ai_worker.tasks.evaluation.schema_exports import schema_documents
from ai_worker.tasks.evaluation.schemas.artifacts import (
    CASE_RESULT_ADAPTER,
    CaseResult,
    ContentArtifact,
    ContentArtifactPath,
    ContentManifest,
    FailureRecord,
    MetricResult,
    MetricResults,
    RagEvaluationRun,
    SuiteCaseResult,
    SuiteResults,
)
from ai_worker.tasks.evaluation.schemas.common import (
    ActorRef,
    DecisionStatus,
    ExecutionStatus,
    ExperimentType,
    ImmutableReference,
)

if TYPE_CHECKING:
    from ai_worker.tasks.evaluation.runner import RunOutcome


@dataclass(frozen=True, slots=True)
class CaseInputBinding:
    case_id: str
    task_type: str
    partition: str
    case_resource_sha256: str
    dataset_manifest_sha256: str
    evidence_mapping_manifest_sha256: str
    critical_claim_rubric_hash: str
    resolved_evaluation_config_hash: str


def case_input_sha256(binding: CaseInputBinding) -> str:
    return canonical_sha256(asdict(binding))


@dataclass(frozen=True, slots=True)
class RunMaterial:
    outcome: RunOutcome
    dataset: ValidatedDataset
    resolved: ResolvedDevExecution
    run_id: str
    executed_by: ActorRef
    started_at: str


@dataclass(frozen=True, slots=True)
class ReportData:
    run_id: str
    experiment_id: str
    experiment_type: ExperimentType
    variant_id: str
    dataset_code: str
    dataset_version: str
    evaluation_profile_ref: ImmutableReference
    comparison_policy_ref: ImmutableReference
    evaluation_policy_ref: ImmutableReference
    suite_ref: ImmutableReference
    execution_status: ExecutionStatus
    decision_status: DecisionStatus | None
    blocking_execution_statuses: Sequence[ExecutionStatus]
    task_case_counts: Mapping[str, int]
    failure_codes: Sequence[str]


@dataclass(frozen=True, slots=True)
class ArtifactDraft:
    report_data: ReportData
    run_payload: Mapping[str, JsonValue]
    cases: Sequence[CaseResult]
    metrics: MetricResults
    suite_results: SuiteResults
    failures: Sequence[FailureRecord]


@dataclass(frozen=True, slots=True)
class PublishedArtifacts:
    run: RagEvaluationRun
    content_manifest: ContentManifest
    files: Mapping[str, bytes]

    def semantic_files(self) -> dict[str, bytes]:
        return {
            "run.json": canonical_json_bytes(cast(JsonValue, self.run.model_dump(mode="json"))),
            "cases.jsonl": self.files["cases.jsonl"],
            "metrics.json": self.files["metrics.json"],
            "suite-results.json": self.files["suite-results.json"],
            "failures.jsonl": self.files["failures.jsonl"],
        }


def serialize_jsonl(records: Sequence[BaseModel]) -> bytes:
    if not records:
        return b""
    return b"".join(canonical_json_bytes(cast(JsonValue, item.model_dump(mode="json"))) + b"\n" for item in records)


def _reference(resource_id: str, version: str, resource_hash: str) -> ImmutableReference:
    return ImmutableReference(id=resource_id, version=version, hash=resource_hash)


def _partition_manifest_hash(dataset: ValidatedDataset) -> str:
    resources: list[JsonValue] = [
        {"case_id": item.case_id, "path": item.path, "sha256": item.sha256}
        for item in dataset.manifest.case_resources
        if item.partition.value == "DEV"
    ]
    return canonical_sha256({"partition": "DEV", "resources": resources})


def _build_metrics(material: RunMaterial) -> MetricResults:
    if material.resolved.request.experiment_type is ExperimentType.KNOWLEDGE_RETRIEVAL:
        return build_retrieval_metrics(material.dataset, material.outcome.case_results)

    metrics: list[MetricResult] = []
    retrieval_cases = {
        case.case_id: (
            tuple(case.expected.required_evidence_refs or ()),
            tuple(case.expected.relevant_evidence_refs or ()),
        )
        for case in material.dataset.cases
        if case.task_type.value == "RETRIEVAL"
    }
    retrieval_results = {
        result.case_id: tuple(result.retrieved_evidence_ids or result.selected_evidence_ids or ())
        for result in material.outcome.case_results
        if result.case_id in retrieval_cases and result.execution_status is ExecutionStatus.COMPLETED
    }
    for scope in material.dataset.comparison_policy.scopes:
        ci_parameters = dict(scope.ci_parameters)
        aggregate = None
        if scope.metric_id in {"RECALL_AT_5", "PRECISION_AT_5", "MRR", "NDCG_AT_5", "NO_HIT_RATE"} and set(
            retrieval_cases
        ) == set(retrieval_results):
            observations = observations_from_case_results(retrieval_cases, retrieval_results)
            aggregate = aggregate_metric_scores(
                [metric_scores(observation) for observation in observations],
                minimum_case_count=scope.minimum_case_count,
            ).get(scope.metric_id)
        if aggregate is not None:
            calculated = metric_result_fields(aggregate, sample_group_count=len(retrieval_results))
            metrics.append(
                MetricResult(
                    metric_id=scope.metric_id,
                    metric_version=scope.metric_version,
                    partition=scope.partition,
                    slice_id=scope.slice_id,
                    required=scope.required,
                    unit_of_analysis=scope.unit_of_analysis,
                    estimator_id=scope.estimator_id,
                    estimator_version=scope.estimator_version,
                    independence_unit=scope.independence_unit,
                    cluster_dimension=None if scope.cluster_dimension is None else scope.cluster_dimension.value,
                    ci_lower=None,
                    ci_upper=None,
                    ci_method_id=scope.ci_method_id,
                    ci_method_version=scope.ci_method_version,
                    ci_level=cast(str | None, ci_parameters.get("level")),
                    ci_sidedness=cast(str | None, ci_parameters.get("sidedness")),
                    threshold=scope.threshold,
                    **calculated,
                )
            )
            continue
        metrics.append(
            MetricResult(
                metric_id=scope.metric_id,
                metric_version=scope.metric_version,
                partition=scope.partition,
                slice_id=scope.slice_id,
                required=scope.required,
                execution_status=ExecutionStatus.NOT_IMPLEMENTED,
                decision_status=None,
                sample_case_count=None,
                sample_independent_group_count=None,
                numerator=None,
                denominator=None,
                metric_value=None,
                unit_of_analysis=scope.unit_of_analysis,
                estimator_id=scope.estimator_id,
                estimator_version=scope.estimator_version,
                independence_unit=scope.independence_unit,
                cluster_dimension=None if scope.cluster_dimension is None else scope.cluster_dimension.value,
                ci_lower=None,
                ci_upper=None,
                ci_method_id=scope.ci_method_id,
                ci_method_version=scope.ci_method_version,
                ci_level=cast(str | None, ci_parameters.get("level")),
                ci_sidedness=cast(str | None, ci_parameters.get("sidedness")),
                threshold=scope.threshold,
                reason_code=None,
            )
        )
    metrics.sort(key=lambda item: item.sort_key)
    return MetricResults(
        schema_id="rag-eval.metrics",
        schema_version="1.0.0",
        run_id=material.run_id,
        metrics=tuple(metrics),
    )


def _build_suite_results(material: RunMaterial) -> SuiteResults:
    case_results: list[SuiteCaseResult] = []
    for result in material.outcome.case_results:
        result_bytes = canonical_json_bytes(cast(JsonValue, result.model_dump(mode="json")))
        case_results.append(
            SuiteCaseResult(
                case_code=result.case_id,
                case_input_hash=result.input_sha256,
                execution_status=result.execution_status,
                decision_status=result.decision_status,
                artifact_ref=_reference(result.case_id, "1.0.0", sha256_hex(result_bytes)),
                failure_code=result.failure_codes[0] if result.failure_codes else None,
            )
        )
    return SuiteResults(
        schema_id="rag-eval.suite-results",
        schema_version="1.0.0",
        run_id=material.run_id,
        suite_id=material.dataset.suite.suite_id,
        suite_version=material.dataset.suite.suite_version,
        suite_definition_hash=material.dataset.suite.suite_hash,
        required=material.dataset.suite.required,
        expected_case_set_hash=material.dataset.suite.expected_case_set_hash,
        executed_case_set_hash=canonical_sha256(
            {"case_ids": [result.case_id for result in material.outcome.case_results]}
        ),
        case_results=tuple(case_results),
        aggregate_execution_status=material.outcome.execution_status,
        aggregate_decision_status=material.outcome.decision_status,
        blocking_execution_statuses=material.outcome.blocking_execution_statuses,
        artifact_hash=None,
    )


def build_artifact_draft(material: RunMaterial) -> ArtifactDraft:
    dataset = material.dataset
    resolved = material.resolved
    profile_ref = _reference(
        dataset.profile.evaluation_profile_id,
        dataset.profile.evaluation_profile_version,
        dataset.profile.evaluation_profile_hash,
    )
    comparison_ref = _reference(
        dataset.comparison_policy.comparison_policy_id,
        dataset.comparison_policy.comparison_policy_version,
        dataset.comparison_policy.comparison_policy_hash,
    )
    evaluation_ref = _reference(
        dataset.evaluation_policy.evaluation_policy_id,
        dataset.evaluation_policy.evaluation_policy_version,
        dataset.evaluation_policy.evaluation_policy_hash,
    )
    suite_ref = _reference(dataset.suite.suite_id, dataset.suite.suite_version, dataset.suite.suite_hash)
    task_case_counts = Counter(result.task_type.value for result in material.outcome.case_results)
    failure_codes = tuple(
        sorted(
            {code for result in material.outcome.case_results for code in result.failure_codes},
            key=lambda value: value.encode("utf-16-be"),
        )
    )
    report_data = ReportData(
        run_id=material.run_id,
        experiment_id=resolved.request.experiment_id,
        experiment_type=resolved.request.experiment_type,
        variant_id=resolved.request.variant_id,
        dataset_code=dataset.manifest.dataset_code,
        dataset_version=dataset.manifest.dataset_version,
        evaluation_profile_ref=profile_ref,
        comparison_policy_ref=comparison_ref,
        evaluation_policy_ref=evaluation_ref,
        suite_ref=suite_ref,
        execution_status=material.outcome.execution_status,
        decision_status=material.outcome.decision_status,
        blocking_execution_statuses=material.outcome.blocking_execution_statuses,
        task_case_counts=dict(task_case_counts),
        failure_codes=failure_codes,
    )
    run_payload: dict[str, JsonValue] = {
        "schema_id": "rag-eval.run",
        "schema_version": "1.0.0",
        "run_id": material.run_id,
        "experiment_id": resolved.request.experiment_id,
        "variant_id": resolved.request.variant_id,
        "experiment_type": resolved.request.experiment_type.value,
        "task_types": [item.value for item in material.outcome.task_types],
        "evaluation_profile_ref": cast(JsonValue, profile_ref.model_dump(mode="json")),
        "comparison_policy_ref": cast(JsonValue, comparison_ref.model_dump(mode="json")),
        "evaluation_policy_ref": cast(JsonValue, evaluation_ref.model_dump(mode="json")),
        "artifact_schema_set_ref": cast(
            JsonValue,
            dataset.evaluation_policy.artifact_schema_set_ref.reference.model_dump(mode="json"),
        ),
        "dataset_code": dataset.manifest.dataset_code,
        "dataset_version": dataset.manifest.dataset_version,
        "dataset_manifest_sha256": dataset.manifest.manifest_sha256,
        "resource_set_hash": dataset.manifest.resource_set_hash,
        "evidence_mapping_manifest_sha256": dataset.evidence_mapping.manifest_sha256,
        "critical_claim_rubric_ref": cast(
            JsonValue,
            dataset.manifest.critical_claim_rubric_ref.model_dump(mode="json"),
        ),
        "fixture_git_commit_sha": dataset.manifest.fixture_git_commit_sha,
        "protected_artifact_receipt_ref": (
            None
            if dataset.manifest.protected_artifact_receipt_ref is None
            else cast(JsonValue, dataset.manifest.protected_artifact_receipt_ref.model_dump(mode="json"))
        ),
        "resolved_evaluation_config_hash": resolved.resolved_evaluation_config_hash,
        "upstream_contract_manifest_hash": resolved.request.upstream_contract_manifest_hash,
        "retrieval_variant_manifest_hash": resolved.retrieval_variant_manifest_hash,
        "answer_variant_manifest_hash": resolved.answer_variant_manifest_hash,
        "model_config_hash": resolved.model_config_hash,
        "prompt_version": resolved.prompt_version,
        "evaluated_partitions": ["DEV"],
        "partition_manifest_hash": _partition_manifest_hash(dataset),
        "environment": resolved.request.environment,
        "runtime_eligible": False,
        "candidate_bundle_id": None,
        "candidate_bundle_manifest_hash": None,
        "candidate_guard_decision_id": None,
        "candidate_guard_decision": None,
        "required_case_guard_coverage_manifest_hash": None,
        "executed_by": cast(JsonValue, material.executed_by.model_dump(mode="json")),
        "started_at": material.started_at,
        "completed_at": None,
        "execution_status": material.outcome.execution_status.value,
        "decision_status": (
            None if material.outcome.decision_status is None else material.outcome.decision_status.value
        ),
        "blocking_execution_statuses": [item.value for item in material.outcome.blocking_execution_statuses],
        "result_content_manifest_hash": None,
    }
    return ArtifactDraft(
        report_data=report_data,
        run_payload=run_payload,
        cases=material.outcome.case_results,
        metrics=_build_metrics(material),
        suite_results=_build_suite_results(material),
        failures=material.outcome.failure_records,
    )


def content_artifact_entries(files: Mapping[str, bytes]) -> tuple[ContentArtifact, ...]:
    excluded = {"run.json", "result-content-manifest.json"}
    return tuple(
        ContentArtifact(
            relative_path=cast(ContentArtifactPath, path),
            sha256=sha256_hex(files[path]),
            size_bytes=len(files[path]),
        )
        for path in sorted((set(files) - excluded), key=lambda value: value.encode("utf-16-be"))
    )


def build_content_manifest(run_id: str, files: Mapping[str, bytes]) -> tuple[ContentManifest, bytes]:
    entries = content_artifact_entries(files)
    payload: dict[str, JsonValue] = {
        "schema_id": "rag-eval.content-manifest",
        "schema_version": "1.0.0",
        "run_id": run_id,
        "hash_algorithm": "SHA-256",
        "artifacts": [cast(JsonValue, entry.model_dump(mode="json")) for entry in entries],
        "artifact_count": len(entries),
        "manifest_sha256": "0" * 64,
    }
    payload["manifest_sha256"] = canonical_sha256(
        payload,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    manifest = ContentManifest.model_validate(payload)
    return manifest, canonical_json_bytes(cast(JsonValue, manifest.model_dump(mode="json")))


def machine_artifact_files(draft: ArtifactDraft) -> dict[str, bytes]:
    return {
        "cases.jsonl": serialize_jsonl(draft.cases),
        "metrics.json": canonical_json_bytes(cast(JsonValue, draft.metrics.model_dump(mode="json"))),
        "suite-results.json": canonical_json_bytes(cast(JsonValue, draft.suite_results.model_dump(mode="json"))),
        "failures.jsonl": serialize_jsonl(draft.failures),
    }


def finalize_artifacts(
    draft: ArtifactDraft,
    report_bytes: bytes,
    *,
    completed_at: str,
) -> PublishedArtifacts:
    files = machine_artifact_files(draft)
    files["report.md"] = report_bytes
    content_manifest, content_bytes = build_content_manifest(draft.report_data.run_id, files)
    run_payload = dict(draft.run_payload)
    if draft.report_data.execution_status is ExecutionStatus.COMPLETED:
        run_payload.update(
            completed_at=completed_at,
            result_content_manifest_hash=content_manifest.manifest_sha256,
        )
    else:
        run_payload.update(completed_at=None, decision_status=None, result_content_manifest_hash=None)
    run = RagEvaluationRun.model_validate(run_payload)
    files["run.json"] = canonical_json_bytes(cast(JsonValue, run.model_dump(mode="json")))
    files["result-content-manifest.json"] = content_bytes
    return PublishedArtifacts(run=run, content_manifest=content_manifest, files=files)


_PUBLISHED_SCHEMA_PATHS = {
    "run.json": "artifacts/rag-eval.run.schema.json",
    "cases.jsonl": "artifacts/rag-eval.case-result.schema.json",
    "metrics.json": "artifacts/rag-eval.metrics.schema.json",
    "suite-results.json": "artifacts/rag-eval.suite-results.schema.json",
    "failures.jsonl": "artifacts/rag-eval.failure.schema.json",
    "result-content-manifest.json": "artifacts/rag-eval.content-manifest.schema.json",
}


def validate_published_artifact_contracts(
    files: Mapping[str, bytes],
    *,
    schema_root: Path,
    schema_set_version: str,
) -> None:
    """Bind finalized bytes to the checked-in schemas and their exact runtime models."""

    if not set(_PUBLISHED_SCHEMA_PATHS).issubset(files):
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    try:
        generated = schema_documents(schema_set_version)
    except KeyError:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None
    for schema_path in _PUBLISHED_SCHEMA_PATHS.values():
        try:
            committed_bytes = (schema_root / schema_path).read_bytes()
        except FileNotFoundError:
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING) from None
        expected_bytes = canonical_json_bytes(generated[schema_path])
        if committed_bytes != expected_bytes:
            raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)

    try:
        RagEvaluationRun.model_validate_json(files["run.json"])
        MetricResults.model_validate_json(files["metrics.json"])
        SuiteResults.model_validate_json(files["suite-results.json"])
        ContentManifest.model_validate_json(files["result-content-manifest.json"])
        for line in files["cases.jsonl"].splitlines():
            CASE_RESULT_ADAPTER.validate_json(line)
        for line in files["failures.jsonl"].splitlines():
            FailureRecord.model_validate_json(line)
    except (ValidationError, ValueError):
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None


_SEMANTIC_FILENAMES = (
    "run.json",
    "cases.jsonl",
    "metrics.json",
    "suite-results.json",
    "failures.jsonl",
)


def _semantic_record(value: JsonValue, *, is_run: bool, is_failure: bool = False) -> JsonValue:
    if not isinstance(value, dict):
        return value
    projected = dict(value)
    projected.pop("run_id", None)
    if is_run:
        for field in ("started_at", "completed_at", "result_content_manifest_hash"):
            projected.pop(field, None)
    if is_failure:
        projected.pop("created_at", None)
    return projected


def semantic_content_hash(files: Mapping[str, bytes]) -> str:
    case_records = [
        _semantic_record(cast(JsonValue, json.loads(line)), is_run=False) for line in files["cases.jsonl"].splitlines()
    ]
    case_hashes = {
        cast(str, record["case_id"]): canonical_sha256(record) for record in case_records if isinstance(record, dict)
    }
    artifacts: list[JsonValue] = []
    for path in _SEMANTIC_FILENAMES:
        raw_bytes = files[path]
        content: JsonValue
        if path == "cases.jsonl":
            content = case_records
        elif path.endswith(".jsonl"):
            content = [
                _semantic_record(
                    cast(JsonValue, json.loads(line)),
                    is_run=False,
                    is_failure=path == "failures.jsonl",
                )
                for line in raw_bytes.splitlines()
            ]
        else:
            content = _semantic_record(cast(JsonValue, json.loads(raw_bytes)), is_run=path == "run.json")
        if path == "suite-results.json" and isinstance(content, dict):
            for case_result in cast(list[JsonValue], content["case_results"]):
                if isinstance(case_result, dict) and isinstance(case_result.get("artifact_ref"), dict):
                    artifact_ref = cast(dict[str, JsonValue], case_result["artifact_ref"])
                    artifact_ref["hash"] = case_hashes[cast(str, case_result["case_code"])]
        artifacts.append({"relative_path": path, "content": content})
    return canonical_sha256({"artifacts": artifacts})
