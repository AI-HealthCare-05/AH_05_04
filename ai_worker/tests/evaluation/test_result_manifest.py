from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.config import RepositoryState, load_dev_execution_request
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import load_dataset
from ai_worker.tasks.evaluation.manifest import (
    ArtifactDraft,
    CaseInputBinding,
    RunMaterial,
    build_artifact_draft,
    build_content_manifest,
    case_input_sha256,
    finalize_artifacts,
    semantic_content_hash,
    serialize_jsonl,
    validate_published_artifact_contracts,
)
from ai_worker.tasks.evaluation.retrieval_replay import build_adapter_registry
from ai_worker.tasks.evaluation.runner import execute_dev_cases
from ai_worker.tasks.evaluation.schemas.artifacts import (
    CASE_RESULT_ADAPTER,
    ContentManifest,
    FailureRecord,
    MetricResults,
    RagEvaluationRun,
    SuiteResults,
)
from ai_worker.tasks.evaluation.schemas.common import ActorNamespace, ActorRef, ActorRole
from ai_worker.tests.evaluation.test_runner import CountingAdapter, StaticRegistry

REPOSITORY_ROOT = Path(__file__).parents[3]
SOURCE_MANIFEST = REPOSITORY_ROOT / "evals/retrieval/manifests/dev-foundation-v1.dataset.json"
RETRIEVAL_MANIFEST = REPOSITORY_ROOT / "evals/retrieval/manifests/rag-retrieval-dev-v1.dataset.json"
RUN_ID_A = "123e4567-e89b-42d3-a456-426614174000"
RUN_ID_B = "123e4567-e89b-42d3-a456-426614174001"
TIME_A = "2026-09-04T00:00:00.000000Z"
TIME_B = "2026-09-04T00:01:00.000000Z"


def _case_binding(*, resolved_hash: str) -> CaseInputBinding:
    return CaseInputBinding(
        case_id="case-001",
        task_type="RETRIEVAL",
        partition="DEV",
        case_resource_sha256="1" * 64,
        dataset_manifest_sha256="2" * 64,
        evidence_mapping_manifest_sha256="3" * 64,
        critical_claim_rubric_hash="4" * 64,
        resolved_evaluation_config_hash=resolved_hash,
    )


def test_case_input_hash_binds_case_task_dataset_evidence_rubric_and_config() -> None:
    first = case_input_sha256(_case_binding(resolved_hash="a" * 64))
    changed = case_input_sha256(_case_binding(resolved_hash="b" * 64))

    assert first != changed
    assert first == canonical_sha256(
        {
            "case_id": "case-001",
            "task_type": "RETRIEVAL",
            "partition": "DEV",
            "case_resource_sha256": "1" * 64,
            "dataset_manifest_sha256": "2" * 64,
            "evidence_mapping_manifest_sha256": "3" * 64,
            "critical_claim_rubric_hash": "4" * 64,
            "resolved_evaluation_config_hash": "a" * 64,
        }
    )


def _material(*, run_id: str, started_at: str, complete: bool) -> RunMaterial:
    dataset = load_dataset(SOURCE_MANIFEST, evals_root=REPOSITORY_ROOT / "evals")
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    outcome = execute_dev_cases(
        dataset,
        resolved,
        run_id=run_id,
        adapter_registry=StaticRegistry(CountingAdapter()) if complete else StaticRegistry(None),
    )
    return RunMaterial(
        outcome=outcome,
        dataset=dataset,
        resolved=resolved,
        run_id=run_id,
        executed_by=ActorRef(
            namespace=ActorNamespace.GITHUB_LOGIN,
            actor_id="ceohwj",
            role=ActorRole.EVALUATION_IMPLEMENTER,
        ),
        started_at=started_at,
    )


def _draft(*, complete: bool = True, run_id: str = RUN_ID_A, started_at: str = TIME_A) -> ArtifactDraft:
    return build_artifact_draft(_material(run_id=run_id, started_at=started_at, complete=complete))


def retrieval_run_material(
    variant: str,
    *,
    run_id: str = RUN_ID_A,
    started_at: str = TIME_A,
    rank_override: Mapping[str, Sequence[str]] | None = None,
) -> RunMaterial:
    config_name = {
        "RET-L": "rag-retrieval-dev-ret-l-v1.execution.json",
        "RET-HR": "rag-retrieval-dev-ret-hr-v1.execution.json",
    }[variant]
    dataset = load_dataset(RETRIEVAL_MANIFEST, evals_root=REPOSITORY_ROOT / "evals")
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs" / config_name,
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    outcome = execute_dev_cases(
        dataset,
        resolved,
        run_id=run_id,
        adapter_registry=build_adapter_registry(resolved),
        failure_created_at=started_at,
    )
    if rank_override:
        case_results = tuple(
            result.model_copy(
                update={
                    "retrieved_evidence_ids": tuple(rank_override[result.case_id]),
                    "selected_evidence_ids": tuple(rank_override[result.case_id]),
                }
            )
            if result.case_id in rank_override
            else result
            for result in outcome.case_results
        )
        outcome = replace(outcome, case_results=case_results)
    return RunMaterial(
        outcome=outcome,
        dataset=dataset,
        resolved=resolved,
        run_id=run_id,
        executed_by=ActorRef(
            namespace=ActorNamespace.GITHUB_LOGIN,
            actor_id="ceohwj",
            role=ActorRole.EVALUATION_IMPLEMENTER,
        ),
        started_at=started_at,
    )


def ret_l_artifacts(
    *,
    run_id: str = RUN_ID_A,
    started_at: str = TIME_A,
    completed_at: str = TIME_B,
    rank_override: Mapping[str, Sequence[str]] | None = None,
):
    draft = build_artifact_draft(
        retrieval_run_material(
            "RET-L",
            run_id=run_id,
            started_at=started_at,
            rank_override=rank_override,
        )
    )
    return finalize_artifacts(draft, b"safe retrieval report\n", completed_at=completed_at)


def _validate_jsonl(adapter: Any, payload: bytes) -> None:
    for line in payload.splitlines():
        validator = getattr(adapter, "validate_python", None)
        if validator is None:
            adapter.model_validate(json.loads(line))
        else:
            validator(json.loads(line))


def test_jsonl_is_canonical_and_lf_terminated() -> None:
    draft = _draft()
    payload = serialize_jsonl(tuple(reversed(draft.cases)))

    assert payload.endswith(b"\n")
    assert b"\r" not in payload
    assert all(line == canonical_json_bytes(json.loads(line)) for line in payload.splitlines())
    assert serialize_jsonl(()) == b""


def test_machine_artifacts_validate_against_models_and_exported_schemas() -> None:
    artifacts = finalize_artifacts(_draft(), b"safe report\n", completed_at=TIME_B)

    validate_published_artifact_contracts(
        artifacts.files,
        schema_root=REPOSITORY_ROOT / "evals/schemas/1.0.0",
        schema_set_version="1.0.0",
    )

    run = RagEvaluationRun.model_validate_json(artifacts.files["run.json"])
    metrics = MetricResults.model_validate_json(artifacts.files["metrics.json"])
    suite = SuiteResults.model_validate_json(artifacts.files["suite-results.json"])
    _validate_jsonl(CASE_RESULT_ADAPTER, artifacts.files["cases.jsonl"])
    _validate_jsonl(FailureRecord, artifacts.files["failures.jsonl"])
    assert run.schema_id == "rag-eval.run"
    assert metrics.schema_id == "rag-eval.metrics"
    assert suite.schema_id == "rag-eval.suite-results"


def test_retrieval_artifact_draft_contains_completed_metric_counts_and_ci() -> None:
    draft = build_artifact_draft(retrieval_run_material("RET-L"))
    recall = next(metric for metric in draft.metrics.metrics if metric.metric_id == "RECALL_AT_5")

    assert recall.execution_status.value == "COMPLETED"
    assert (recall.numerator, recall.denominator) == (4, 5)
    assert recall.metric_value == "0.8"
    assert recall.ci_lower is not None
    assert recall.ci_upper is not None


def test_non_retrieval_artifact_draft_keeps_placeholder_metrics() -> None:
    dataset = load_dataset(SOURCE_MANIFEST, evals_root=REPOSITORY_ROOT / "evals")
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/dev-foundation-answer-grounding-safety-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    outcome = execute_dev_cases(
        dataset,
        resolved,
        run_id=RUN_ID_A,
        adapter_registry=StaticRegistry(CountingAdapter()),
    )
    material = replace(
        _material(run_id=RUN_ID_A, started_at=TIME_A, complete=True),
        outcome=outcome,
        resolved=resolved,
    )

    draft = build_artifact_draft(material)

    assert {metric.execution_status.value for metric in draft.metrics.metrics} == {"NOT_IMPLEMENTED"}


def test_artifact_contract_validation_rejects_checked_in_schema_drift(tmp_path: Path) -> None:
    artifacts = finalize_artifacts(_draft(), b"safe report\n", completed_at=TIME_B)
    source_root = REPOSITORY_ROOT / "evals/schemas/1.0.0"
    schema_root = tmp_path / "schemas"
    for relative_path in (
        "artifacts/rag-eval.run.schema.json",
        "artifacts/rag-eval.case-result.schema.json",
        "artifacts/rag-eval.metrics.schema.json",
        "artifacts/rag-eval.suite-results.schema.json",
        "artifacts/rag-eval.failure.schema.json",
        "artifacts/rag-eval.content-manifest.schema.json",
    ):
        destination = schema_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source_root / relative_path).read_bytes())
    (schema_root / "artifacts/rag-eval.run.schema.json").write_bytes(b"{}")

    with pytest.raises(EvaluationValidationError) as caught:
        validate_published_artifact_contracts(
            artifacts.files,
            schema_root=schema_root,
            schema_set_version="1.0.0",
        )

    assert caught.value.code is EvaluationErrorCode.HASH_MISMATCH


def test_artifact_contract_validation_rejects_finalized_payload_drift() -> None:
    artifacts = finalize_artifacts(_draft(), b"safe report\n", completed_at=TIME_B)
    files = dict(artifacts.files)
    run_payload = json.loads(files["run.json"])
    run_payload["unexpected"] = True
    files["run.json"] = canonical_json_bytes(run_payload)

    with pytest.raises(EvaluationValidationError) as caught:
        validate_published_artifact_contracts(
            files,
            schema_root=REPOSITORY_ROOT / "evals/schemas/1.0.0",
            schema_set_version="1.0.0",
        )

    assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID


def test_content_manifest_excludes_run_and_self_but_includes_report() -> None:
    files = {
        "run.json": b"ignored",
        "cases.jsonl": b"case\n",
        "failures.jsonl": b"",
        "metrics.json": b"metrics",
        "report.md": b"safe report\n",
        "result-content-manifest.json": b"ignored",
        "suite-results.json": b"suite",
    }

    manifest, payload = build_content_manifest(RUN_ID_A, files)

    paths = [item.relative_path for item in manifest.artifacts]
    assert paths == sorted(
        ["cases.jsonl", "failures.jsonl", "metrics.json", "report.md", "suite-results.json"],
        key=lambda value: value.encode("utf-16-be"),
    )
    assert manifest.manifest_sha256 == canonical_sha256(
        manifest.model_dump(mode="json"),
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    assert ContentManifest.model_validate_json(payload) == manifest


def test_completed_run_links_content_manifest_but_incomplete_run_does_not() -> None:
    completed = finalize_artifacts(_draft(complete=True), b"safe\n", completed_at=TIME_B).run
    incomplete = finalize_artifacts(_draft(complete=False), b"safe\n", completed_at=TIME_B).run

    assert completed.result_content_manifest_hash is not None
    assert completed.completed_at == TIME_B
    assert incomplete.result_content_manifest_hash is None
    assert incomplete.completed_at is None
    assert incomplete.decision_status is None


def test_semantic_hash_ignores_run_identity_and_all_artifact_clocks() -> None:
    first = ret_l_artifacts(run_id=RUN_ID_A, started_at=TIME_A, completed_at=TIME_A)
    second = ret_l_artifacts(run_id=RUN_ID_B, started_at=TIME_B, completed_at=TIME_B)

    assert semantic_content_hash(first.files) == semantic_content_hash(second.files)
    changed_files = dict(second.files)
    changed_run = json.loads(changed_files["run.json"])
    changed_run["resolved_evaluation_config_hash"] = "b" * 64
    changed_files["run.json"] = canonical_json_bytes(changed_run)
    assert semantic_content_hash(first.files) != semantic_content_hash(changed_files)


def test_semantic_hash_changes_when_ranked_retrieval_result_changes() -> None:
    before = semantic_content_hash(ret_l_artifacts().files)
    after = semantic_content_hash(ret_l_artifacts(rank_override={"rag-ret-dev-004": ["ev-ret-dev-storage-d"]}).files)

    assert before != after
