from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.config import RepositoryState, load_dev_execution_request
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
)
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


def _validate_jsonl(adapter: Any, payload: bytes) -> None:
    for line in payload.splitlines():
        adapter.validate_python(json.loads(line))


def test_jsonl_is_canonical_and_lf_terminated() -> None:
    draft = _draft()
    payload = serialize_jsonl(tuple(reversed(draft.cases)))

    assert payload.endswith(b"\n")
    assert b"\r" not in payload
    assert all(line == canonical_json_bytes(json.loads(line)) for line in payload.splitlines())
    assert serialize_jsonl(()) == b""


def test_machine_artifacts_validate_against_models_and_exported_schemas() -> None:
    artifacts = finalize_artifacts(_draft(), b"safe report\n", completed_at=TIME_B)

    run = RagEvaluationRun.model_validate_json(artifacts.files["run.json"])
    metrics = MetricResults.model_validate_json(artifacts.files["metrics.json"])
    suite = SuiteResults.model_validate_json(artifacts.files["suite-results.json"])
    _validate_jsonl(CASE_RESULT_ADAPTER, artifacts.files["cases.jsonl"])
    _validate_jsonl(FailureRecord, artifacts.files["failures.jsonl"])
    assert run.schema_id == "rag-eval.run"
    assert metrics.schema_id == "rag-eval.metrics"
    assert suite.schema_id == "rag-eval.suite-results"


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


def test_semantic_hash_ignores_only_run_identity_and_clock() -> None:
    first = finalize_artifacts(
        _draft(run_id=RUN_ID_A, started_at=TIME_A),
        b"first report\n",
        completed_at=TIME_A,
    )
    second = finalize_artifacts(
        _draft(run_id=RUN_ID_B, started_at=TIME_B),
        b"second report\n",
        completed_at=TIME_B,
    )

    assert semantic_content_hash(first.files) == semantic_content_hash(second.files)
    changed_files = dict(second.files)
    changed_run = json.loads(changed_files["run.json"])
    changed_run["resolved_evaluation_config_hash"] = "b" * 64
    changed_files["run.json"] = canonical_json_bytes(changed_run)
    assert semantic_content_hash(first.files) != semantic_content_hash(changed_files)
