from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.config import RepositoryState, load_dev_execution_request
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import load_dataset, parse_json_object_bytes
from ai_worker.tasks.evaluation.retrieval_replay import (
    ReplayRetrievalAdapter,
    build_adapter_registry,
    load_retrieval_replay,
)
from ai_worker.tasks.evaluation.runner import AdapterRequest, execute_dev_cases
from ai_worker.tasks.evaluation.schemas.common import DecisionStatus, ExecutionStatus, TaskType

REPOSITORY_ROOT = Path(__file__).parents[3]
REPLAY_PATH = Path("evals/retrieval/replays/rag-retrieval-dev-v1/ret-l-v1.replay.json")
DATASET_PATH = REPOSITORY_ROOT / "evals/retrieval/manifests/rag-retrieval-dev-v1.dataset.json"
RUN_ID = "123e4567-e89b-42d3-a456-426614174000"


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.retrieval-replay",
        "schema_version": "1.0.0",
        "dataset_code": "rag-retrieval-dev",
        "dataset_version": "1.0.0",
        "variant_id": "RET-L",
        "top_k": 5,
        "case_results": [
            {
                "case_id": "rag-ret-dev-001",
                "case_resource_sha256": "a" * 64,
                "ranked_evidence_ids": ["evidence-1", "evidence-2"],
            }
        ],
        "replay_sha256": "0" * 64,
    }
    payload.update(overrides)
    payload["replay_sha256"] = canonical_sha256(
        cast(JsonValue, payload),
        excluded_top_level_keys=frozenset({"replay_sha256"}),
    )
    return payload


def _write_replay(root: Path, payload: dict[str, Any]) -> Path:
    relative = Path("evals/retrieval/replays/test.replay.json")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(cast(JsonValue, payload)))
    return relative


def _copy_replay_config_tree(root: Path) -> Path:
    source_config = REPOSITORY_ROOT / "evals/configs/rag-retrieval-dev-ret-l-v1.execution.json"
    payload = parse_json_object_bytes(source_config.read_bytes())
    config_path = root / "evals/configs/rag-retrieval-dev-ret-l-v1.execution.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(source_config.read_bytes())
    for field in (
        "dataset_manifest_path",
        "profile_path",
        "comparison_policy_path",
        "evaluation_policy_path",
        "suite_path",
    ):
        relative = cast(str, payload[field])
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(REPOSITORY_ROOT.joinpath(relative).read_bytes())
    replay_relative = cast(dict[str, Any], payload["retrieval_variant"])["replay_artifact_path"]
    replay_destination = root / replay_relative
    replay_destination.parent.mkdir(parents=True, exist_ok=True)
    replay_destination.write_bytes(REPOSITORY_ROOT.joinpath(replay_relative).read_bytes())
    return config_path


def _resolve_replay_config(root: Path):
    return load_dev_execution_request(
        _copy_replay_config_tree(root),
        repository_root=root,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )


def _adapter_request(case_id: str, *, variant_id: str = "RET-L") -> AdapterRequest:
    dataset = load_dataset(DATASET_PATH, evals_root=REPOSITORY_ROOT / "evals")
    case = next(item for item in dataset.cases if item.case_id == case_id)
    case_resource_sha256 = next(item.sha256 for item in dataset.manifest.case_resources if item.case_id == case_id)
    return AdapterRequest(
        run_id=RUN_ID,
        case=case,
        task_type=TaskType.RETRIEVAL,
        input_sha256="a" * 64,
        case_resource_sha256=case_resource_sha256,
        variant_id=variant_id,
        variant_manifest_hash="b" * 64,
    )


def test_replay_adapter_returns_ranked_ids_for_exact_case_binding() -> None:
    replay = load_retrieval_replay(REPLAY_PATH, repository_root=REPOSITORY_ROOT)
    result = ReplayRetrievalAdapter(replay).execute(_adapter_request("rag-ret-dev-002"))

    assert result.execution_status.value == "COMPLETED"
    assert result.decision_status is DecisionStatus.NOT_APPLICABLE
    assert result.retrieved_evidence_ids is not None
    assert result.selected_evidence_ids is not None
    assert result.retrieved_evidence_ids[:2] == (
        "ev-ret-dev-noise-01",
        "ev-ret-dev-precaution-b",
    )
    assert result.selected_evidence_ids == result.retrieved_evidence_ids[:5]


def test_replay_rows_bind_rankings_to_the_case_resource_hash() -> None:
    replay = load_retrieval_replay(REPLAY_PATH, repository_root=REPOSITORY_ROOT)
    dataset = load_dataset(DATASET_PATH, evals_root=REPOSITORY_ROOT / "evals")
    expected_hashes = {item.case_id: item.sha256 for item in dataset.manifest.case_resources}

    assert {item.case_id: item.case_resource_sha256 for item in replay.case_results} == expected_hashes


@pytest.mark.parametrize(
    "case_results",
    [
        [{"case_id": "case-1", "ranked_evidence_ids": ["evidence-1", "evidence-1"]}],
        [{"case_id": "case-2", "ranked_evidence_ids": []}, {"case_id": "case-1", "ranked_evidence_ids": []}],
        [{"case_id": "case-1", "ranked_evidence_ids": [f"evidence-{index}" for index in range(6)]}],
    ],
)
def test_replay_loader_rejects_invalid_rank_contract(tmp_path: Path, case_results: list[dict[str, Any]]) -> None:
    relative = _write_replay(tmp_path, _payload(case_results=case_results))

    with pytest.raises(EvaluationValidationError) as caught:
        load_retrieval_replay(relative, repository_root=tmp_path)

    assert caught.value.code is EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID


def test_replay_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    relative = Path("evals/retrieval/replays/test.replay.json")
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_id":"rag-eval.retrieval-replay","schema_id":"duplicate"}', encoding="utf-8")

    with pytest.raises(EvaluationValidationError) as caught:
        load_retrieval_replay(relative, repository_root=tmp_path)

    assert caught.value.code is EvaluationErrorCode.JSON_DUPLICATE_KEY


def test_replay_loader_rejects_self_hash_mismatch(tmp_path: Path) -> None:
    payload = _payload()
    payload["replay_sha256"] = "f" * 64
    relative = _write_replay(tmp_path, payload)

    with pytest.raises(EvaluationValidationError) as caught:
        load_retrieval_replay(relative, repository_root=tmp_path)

    assert caught.value.code is EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID


@pytest.mark.parametrize("path", [Path("../replay.json"), Path("evals/../replay.json"), Path("/tmp/replay.json")])
def test_replay_loader_rejects_noncanonical_or_traversing_path(tmp_path: Path, path: Path) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        load_retrieval_replay(path, repository_root=tmp_path)

    assert caught.value.code is EvaluationErrorCode.RESOURCE_PATH_INVALID


def test_replay_adapter_rejects_variant_mismatch() -> None:
    replay = load_retrieval_replay(REPLAY_PATH, repository_root=REPOSITORY_ROOT)

    with pytest.raises(EvaluationValidationError) as caught:
        ReplayRetrievalAdapter(replay).execute(_adapter_request("rag-ret-dev-001", variant_id="RET-HR"))

    assert caught.value.code is EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID


def test_replay_adapter_rejects_dataset_mismatch() -> None:
    replay = load_retrieval_replay(REPLAY_PATH, repository_root=REPOSITORY_ROOT)
    request = _adapter_request("rag-ret-dev-001")
    request = AdapterRequest(
        run_id=request.run_id,
        case=request.case.model_copy(update={"dataset_code": "another-dataset"}),
        task_type=request.task_type,
        input_sha256=request.input_sha256,
        case_resource_sha256=request.case_resource_sha256,
        variant_id=request.variant_id,
        variant_manifest_hash=request.variant_manifest_hash,
    )

    with pytest.raises(EvaluationValidationError) as caught:
        ReplayRetrievalAdapter(replay).execute(request)

    assert caught.value.code is EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID


def test_replay_adapter_rejects_dataset_version_mismatch() -> None:
    replay = load_retrieval_replay(REPLAY_PATH, repository_root=REPOSITORY_ROOT)
    request = _adapter_request("rag-ret-dev-001")
    request = replace(request, case=request.case.model_copy(update={"dataset_version": "9.9.9"}))

    with pytest.raises(EvaluationValidationError) as caught:
        ReplayRetrievalAdapter(replay).execute(request)

    assert caught.value.code is EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID


def test_replay_adapter_rejects_case_resource_hash_mismatch() -> None:
    replay = load_retrieval_replay(REPLAY_PATH, repository_root=REPOSITORY_ROOT)
    request = replace(_adapter_request("rag-ret-dev-001"), case_resource_sha256="f" * 64)

    with pytest.raises(EvaluationValidationError) as caught:
        ReplayRetrievalAdapter(replay).execute(request)

    assert caught.value.code is EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID


def test_runner_invalidates_replay_when_bound_case_resource_hash_changes() -> None:
    dataset = load_dataset(DATASET_PATH, evals_root=REPOSITORY_ROOT / "evals")
    changed_resource = dataset.manifest.case_resources[0].model_copy(update={"sha256": "f" * 64})
    changed_manifest = dataset.manifest.model_copy(
        update={"case_resources": (changed_resource, *dataset.manifest.case_resources[1:])}
    )
    changed_dataset = replace(dataset, manifest=changed_manifest)
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/rag-retrieval-dev-ret-l-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )

    outcome = execute_dev_cases(
        changed_dataset,
        resolved,
        run_id=RUN_ID,
        adapter_registry=build_adapter_registry(resolved),
    )

    invalid = next(item for item in outcome.case_results if item.case_id == changed_resource.case_id)
    assert invalid.execution_status is ExecutionStatus.INVALID
    assert invalid.failure_codes == ("EVAL_RETRIEVAL_REPLAY_INVALID",)
    assert outcome.execution_status is ExecutionStatus.INVALID
    failure = next(item for item in outcome.failure_records if item.case_id == invalid.case_id)
    assert (failure.failure_stage, failure.failure_code) == (
        "RETRIEVAL_EXECUTION",
        "EVAL_RETRIEVAL_REPLAY_INVALID",
    )


def test_replay_adapter_rejects_unknown_case() -> None:
    replay = load_retrieval_replay(REPLAY_PATH, repository_root=REPOSITORY_ROOT)
    request = _adapter_request("rag-ret-dev-001")
    request = AdapterRequest(
        run_id=request.run_id,
        case=request.case.model_copy(update={"case_id": "rag-ret-dev-999"}),
        task_type=request.task_type,
        input_sha256=request.input_sha256,
        case_resource_sha256=request.case_resource_sha256,
        variant_id=request.variant_id,
        variant_manifest_hash=request.variant_manifest_hash,
    )

    with pytest.raises(EvaluationValidationError) as caught:
        ReplayRetrievalAdapter(replay).execute(request)

    assert caught.value.code is EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID


def test_registry_is_built_from_resolved_replay_config() -> None:
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/rag-retrieval-dev-ret-l-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )

    adapter = build_adapter_registry(resolved).resolve("retrieval-replay.v1")

    assert adapter is not None
    request = replace(
        _adapter_request("rag-ret-dev-002"),
        variant_manifest_hash=cast(str, resolved.retrieval_variant_manifest_hash),
    )
    result = adapter.execute(request)
    assert result.retrieved_evidence_ids is not None
    assert result.retrieved_evidence_ids[:2] == (
        "ev-ret-dev-noise-01",
        "ev-ret-dev-precaution-b",
    )


def test_registry_adapter_rejects_wrong_variant_manifest_hash() -> None:
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/rag-retrieval-dev-ret-l-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    adapter = build_adapter_registry(resolved).resolve("retrieval-replay.v1")

    assert adapter is not None
    with pytest.raises(EvaluationValidationError) as caught:
        adapter.execute(_adapter_request("rag-ret-dev-001"))

    assert caught.value.code is EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID


def test_registry_uses_replay_snapshot_bound_during_config_resolution(tmp_path: Path) -> None:
    resolved = _resolve_replay_config(tmp_path)
    replay_path = tmp_path / cast(str, resolved.request.retrieval_variant.replay_artifact_path)
    replay_path.write_bytes(b"changed after resolution")
    adapter = build_adapter_registry(resolved).resolve("retrieval-replay.v1")
    request = replace(
        _adapter_request("rag-ret-dev-002"),
        variant_manifest_hash=cast(str, resolved.retrieval_variant_manifest_hash),
    )

    assert adapter is not None
    assert adapter.execute(request).retrieved_evidence_ids == (
        "ev-ret-dev-noise-01",
        "ev-ret-dev-precaution-b",
        "ev-ret-dev-noise-02",
        "ev-ret-dev-noise-03",
        "ev-ret-dev-noise-04",
    )


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_runner_rejects_replay_case_set_mismatch_before_case_execution(tmp_path: Path, mutation: str) -> None:
    config_path = _copy_replay_config_tree(tmp_path)
    replay_path = tmp_path / REPLAY_PATH
    payload = parse_json_object_bytes(replay_path.read_bytes())
    case_results = cast(list[JsonValue], payload["case_results"])
    if mutation == "missing":
        case_results.pop()
    else:
        case_results.append({"case_id": "rag-ret-dev-999", "case_resource_sha256": "f" * 64, "ranked_evidence_ids": []})
    payload["replay_sha256"] = canonical_sha256(
        cast(JsonValue, payload),
        excluded_top_level_keys=frozenset({"replay_sha256"}),
    )
    replay_path.write_bytes(canonical_json_bytes(cast(JsonValue, payload)))

    resolved = load_dev_execution_request(
        config_path,
        repository_root=tmp_path,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    dataset = load_dataset(DATASET_PATH, evals_root=REPOSITORY_ROOT / "evals")

    outcome = execute_dev_cases(
        dataset,
        resolved,
        run_id=RUN_ID,
        adapter_registry=build_adapter_registry(resolved),
    )

    assert outcome.execution_status is ExecutionStatus.INVALID
    assert len(outcome.case_results) == 5
    assert {result.failure_codes for result in outcome.case_results} == {("EVAL_RETRIEVAL_REPLAY_INVALID",)}
    assert len(outcome.failure_records) == 5
    assert {failure.failure_stage for failure in outcome.failure_records} == {"RETRIEVAL_EXECUTION"}
    assert {failure.failure_code for failure in outcome.failure_records} == {"EVAL_RETRIEVAL_REPLAY_INVALID"}


def test_registry_preserves_validation_only_configs_as_unimplemented() -> None:
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )

    assert build_adapter_registry(resolved).resolve("validation-only.v1") is None


def test_replay_result_does_not_copy_query_or_evidence_text() -> None:
    replay = load_retrieval_replay(REPLAY_PATH, repository_root=REPOSITORY_ROOT)
    result = ReplayRetrievalAdapter(replay).execute(_adapter_request("rag-ret-dev-001"))
    serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)

    assert "SYNTHETIC_QUERY" not in serialized
    assert "SYNTHETIC_EVIDENCE" not in serialized
