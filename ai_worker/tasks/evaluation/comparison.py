from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import cast
from uuid import UUID

from pydantic import ValidationError

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.manifest import (
    ArtifactDraft,
    PublishedArtifacts,
    machine_artifact_files,
    semantic_content_hash,
)
from ai_worker.tasks.evaluation.schemas.artifacts import (
    CASE_RESULT_ADAPTER,
    CaseResult,
    ComparisonDecision,
    ComparisonResult,
    ContentManifest,
    ControlledVariableCheck,
    FailureRecord,
    MetricResults,
    RagEvaluationRun,
    ScopeComparison,
    SuiteResults,
)
from ai_worker.tasks.evaluation.schemas.common import DecisionStatus, ExecutionStatus

_REQUIRED_FILENAMES = frozenset(
    {
        "run.json",
        "cases.jsonl",
        "metrics.json",
        "suite-results.json",
        "failures.jsonl",
        "report.md",
        "result-content-manifest.json",
    }
)
_OPTIONAL_FILENAMES = frozenset({"comparison.json"})
_SUPPORTED_CONTROLLED_VARIABLE_KEYS = frozenset(
    {
        "CASE_SET",
        "DATASET",
        "GOLD",
        "METRIC_POLICY",
        "SOURCE_INDEX_FILTER_MODEL",
    }
)


@dataclass(frozen=True, slots=True)
class LoadedRunBundle:
    root: Path
    run: RagEvaluationRun
    cases: tuple[CaseResult, ...]
    metrics: MetricResults
    failures: tuple[FailureRecord, ...]
    comparison: ComparisonResult | None
    content_manifest: ContentManifest
    files: Mapping[str, bytes]
    semantic_hash: str


def _baseline_invalid() -> EvaluationValidationError:
    return EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)


def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise _baseline_invalid()
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _read_regular_file(directory_fd: int, name: str) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise _baseline_invalid()
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _validate_run_id(run_id: str) -> None:
    try:
        parsed = UUID(run_id)
    except (TypeError, ValueError, AttributeError):
        raise _baseline_invalid() from None
    if str(parsed) != run_id:
        raise _baseline_invalid()


def _validate_runtime_files(
    files: Mapping[str, bytes],
    expected_run_id: str,
) -> tuple[
    RagEvaluationRun,
    tuple[CaseResult, ...],
    MetricResults,
    tuple[FailureRecord, ...],
    ComparisonResult | None,
    ContentManifest,
]:
    run = RagEvaluationRun.model_validate_json(files["run.json"])
    metrics = MetricResults.model_validate_json(files["metrics.json"])
    suite = SuiteResults.model_validate_json(files["suite-results.json"])
    content_manifest = ContentManifest.model_validate_json(files["result-content-manifest.json"])
    cases = tuple(CASE_RESULT_ADAPTER.validate_json(line) for line in files["cases.jsonl"].splitlines())
    failures = tuple(FailureRecord.model_validate_json(line) for line in files["failures.jsonl"].splitlines())
    comparison = ComparisonResult.model_validate_json(files["comparison.json"]) if "comparison.json" in files else None
    record_run_ids = {
        run.run_id,
        metrics.run_id,
        suite.run_id,
        content_manifest.run_id,
        *(case.run_id for case in cases),
        *(failure.run_id for failure in failures),
    }
    if comparison is not None:
        record_run_ids.update({comparison.run_id, comparison.candidate_run_id})
    if record_run_ids != {expected_run_id}:
        raise _baseline_invalid()
    return run, cases, metrics, failures, comparison, content_manifest


def _read_bundle_files(result_root: Path, run_id: str) -> tuple[Path, dict[str, bytes]]:
    root = Path(os.path.abspath(result_root))
    root_fd = os.open(root, _directory_flags())
    try:
        run_fd = os.open(run_id, _directory_flags(), dir_fd=root_fd)
        try:
            names = set(os.listdir(run_fd))
            if not _REQUIRED_FILENAMES.issubset(names) or not names <= _REQUIRED_FILENAMES | _OPTIONAL_FILENAMES:
                raise _baseline_invalid()
            return root / run_id, {name: _read_regular_file(run_fd, name) for name in names}
        finally:
            os.close(run_fd)
    finally:
        os.close(root_fd)


def load_published_run_bundle(result_root: Path, run_id: str) -> LoadedRunBundle:
    """Load one immutable run directory without following symbolic links."""

    try:
        _validate_run_id(run_id)
        bundle_root, files = _read_bundle_files(result_root, run_id)
        run, cases, metrics, failures, comparison, content_manifest = _validate_runtime_files(files, run_id)
        if run.run_id != run_id or run.result_content_manifest_hash != content_manifest.manifest_sha256:
            raise _baseline_invalid()
        expected_payload_names = set(files) - {"run.json", "result-content-manifest.json"}
        manifest_payload_names = {entry.relative_path for entry in content_manifest.artifacts}
        if manifest_payload_names != expected_payload_names:
            raise _baseline_invalid()
        for entry in content_manifest.artifacts:
            payload = files[entry.relative_path]
            if entry.size_bytes != len(payload) or entry.sha256 != sha256_hex(payload):
                raise _baseline_invalid()
        immutable_files = MappingProxyType(dict(files))
        semantic_hash = semantic_content_hash(immutable_files)
        if comparison is not None:
            _validate_candidate_comparison_binding(comparison, run, metrics, semantic_hash)
        return LoadedRunBundle(
            root=bundle_root,
            run=run,
            cases=cases,
            metrics=metrics,
            failures=failures,
            comparison=comparison,
            content_manifest=content_manifest,
            files=immutable_files,
            semantic_hash=semantic_hash,
        )
    except EvaluationValidationError as error:
        if error.code is EvaluationErrorCode.BASELINE_ARTIFACT_INVALID:
            raise
        raise _baseline_invalid() from None
    except (OSError, KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError):
        raise _baseline_invalid() from None


def _controlled_values(run: RagEvaluationRun | Mapping[str, JsonValue]) -> dict[str, str]:
    if isinstance(run, RagEvaluationRun):
        return {
            "CASE_SET": run.partition_manifest_hash,
            "DATASET": run.dataset_manifest_sha256,
            "GOLD": canonical_sha256(
                {
                    "resource_set_hash": run.resource_set_hash,
                    "evidence_mapping_manifest_sha256": run.evidence_mapping_manifest_sha256,
                }
            ),
            "METRIC_POLICY": run.comparison_policy_ref.hash,
            "SOURCE_INDEX_FILTER_MODEL": run.model_config_hash,
        }

    def field(name: str) -> JsonValue:
        return run[name]

    comparison_ref = field("comparison_policy_ref")
    if not isinstance(comparison_ref, Mapping):
        raise ValueError("comparison policy reference must be an object")
    metric_policy_hash = cast(str, comparison_ref["hash"])
    return {
        "CASE_SET": cast(str, field("partition_manifest_hash")),
        "DATASET": cast(str, field("dataset_manifest_sha256")),
        "GOLD": canonical_sha256(
            {
                "resource_set_hash": field("resource_set_hash"),
                "evidence_mapping_manifest_sha256": field("evidence_mapping_manifest_sha256"),
            }
        ),
        "METRIC_POLICY": metric_policy_hash,
        "SOURCE_INDEX_FILTER_MODEL": cast(str, field("model_config_hash")),
    }


def _validate_candidate_comparison_binding(
    comparison: ComparisonResult,
    run: RagEvaluationRun,
    metrics: MetricResults,
    semantic_hash: str,
) -> None:
    if (
        comparison.candidate_run_hash != semantic_hash
        or comparison.candidate_run_id != run.run_id
        or comparison.experiment_id != run.experiment_id
    ):
        raise _baseline_invalid()
    candidate_values = _controlled_values(run)
    checks = {check.variable_key: check for check in comparison.controlled_variable_checks}
    if (
        len(checks) != len(comparison.controlled_variable_checks)
        or not set(checks) <= _SUPPORTED_CONTROLLED_VARIABLE_KEYS
    ):
        raise _baseline_invalid()
    if any(check.candidate_value_hash != candidate_values[key] for key, check in checks.items()):
        raise _baseline_invalid()
    metric_values = {
        (metric.metric_id, metric.partition, metric.slice_id): metric.metric_value for metric in metrics.metrics
    }
    scope_values = {
        (scope.metric_id, scope.partition, scope.slice_id): scope.candidate_value
        for scope in comparison.scope_comparisons
    }
    if len(metric_values) != len(metrics.metrics) or scope_values != metric_values:
        raise _baseline_invalid()


def _candidate_material(
    candidate: ArtifactDraft | PublishedArtifacts | LoadedRunBundle,
) -> tuple[Mapping[str, JsonValue] | RagEvaluationRun, MetricResults, Mapping[str, bytes], str, str]:
    if isinstance(candidate, ArtifactDraft):
        files = machine_artifact_files(candidate)
        files["run.json"] = canonical_json_bytes(cast(JsonValue, dict(candidate.run_payload)))
        return (
            candidate.run_payload,
            candidate.metrics,
            files,
            candidate.report_data.run_id,
            candidate.report_data.experiment_id,
        )
    if isinstance(candidate, LoadedRunBundle):
        return candidate.run, candidate.metrics, candidate.files, candidate.run.run_id, candidate.run.experiment_id
    metrics = MetricResults.model_validate_json(candidate.files["metrics.json"])
    return candidate.run, metrics, candidate.files, candidate.run.run_id, candidate.run.experiment_id


def _decimal(value: Decimal) -> str:
    rendered = format(value.quantize(Decimal("0.000001")), "f").rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _scope_comparisons(baseline: MetricResults, candidate: MetricResults) -> tuple[ScopeComparison, ...]:
    baseline_metrics = {
        (item.metric_id, item.metric_version, item.partition.value, item.slice_id): item for item in baseline.metrics
    }
    candidate_metrics = {
        (item.metric_id, item.metric_version, item.partition.value, item.slice_id): item for item in candidate.metrics
    }
    if baseline_metrics.keys() != candidate_metrics.keys():
        return ()
    if any(
        metric.execution_status is not ExecutionStatus.COMPLETED or metric.metric_value is None
        for metric in (*baseline.metrics, *candidate.metrics)
    ):
        return ()
    comparisons: list[ScopeComparison] = []
    for key in sorted(baseline_metrics):
        baseline_metric = baseline_metrics[key]
        candidate_metric = candidate_metrics[key]
        baseline_value = baseline_metric.metric_value
        candidate_value = candidate_metric.metric_value
        absolute_delta = None
        relative_delta = None
        if baseline_value is not None and candidate_value is not None:
            baseline_decimal = Decimal(baseline_value)
            candidate_decimal = Decimal(candidate_value)
            absolute_delta = _decimal(candidate_decimal - baseline_decimal)
            if baseline_decimal != 0:
                relative_delta = _decimal((candidate_decimal - baseline_decimal) / abs(baseline_decimal))
        comparisons.append(
            ScopeComparison(
                metric_id=baseline_metric.metric_id,
                partition=baseline_metric.partition,
                slice_id=baseline_metric.slice_id,
                baseline_value=baseline_value,
                candidate_value=candidate_value,
                absolute_delta=absolute_delta,
                relative_delta=relative_delta,
                paired_test_method=None,
                p_value=None,
                comparison_decision=ComparisonDecision.INCONCLUSIVE,
            )
        )
    return tuple(comparisons)


def build_retrieval_comparison(
    baseline: LoadedRunBundle,
    candidate: ArtifactDraft | PublishedArtifacts | LoadedRunBundle,
    controlled_variable_keys: Sequence[str],
) -> ComparisonResult:
    candidate_run, candidate_metrics, candidate_files, candidate_run_id, experiment_id = _candidate_material(candidate)
    baseline_values = _controlled_values(baseline.run)
    candidate_values = _controlled_values(candidate_run)
    if not controlled_variable_keys or not set(controlled_variable_keys) <= _SUPPORTED_CONTROLLED_VARIABLE_KEYS:
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)
    checks = tuple(
        ControlledVariableCheck(
            variable_key=key,
            baseline_value_hash=baseline_values[key],
            candidate_value_hash=candidate_values[key],
            matched=baseline_values[key] == candidate_values[key],
        )
        for key in controlled_variable_keys
    )
    scopes = _scope_comparisons(baseline.metrics, candidate_metrics)
    baseline_completed = baseline.run.execution_status is ExecutionStatus.COMPLETED
    candidate_status = (
        candidate_run.execution_status
        if isinstance(candidate_run, RagEvaluationRun)
        else ExecutionStatus(cast(str, candidate_run["execution_status"]))
    )
    valid = (
        baseline_completed
        and candidate_status is ExecutionStatus.COMPLETED
        and bool(scopes)
        and all(check.matched for check in checks)
    )
    return ComparisonResult(
        schema_id="rag-eval.comparison",
        schema_version="1.0.0",
        run_id=candidate_run_id,
        experiment_id=experiment_id,
        baseline_run_id=baseline.run.run_id,
        baseline_run_hash=baseline.semantic_hash,
        candidate_run_id=candidate_run_id,
        candidate_run_hash=semantic_content_hash(candidate_files),
        controlled_variable_checks=checks,
        scope_comparisons=scopes,
        execution_status=ExecutionStatus.COMPLETED if valid else ExecutionStatus.INVALID,
        decision_status=DecisionStatus.INCONCLUSIVE if valid else None,
    )
