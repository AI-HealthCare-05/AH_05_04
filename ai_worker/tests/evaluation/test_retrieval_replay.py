from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.config import RepositoryState, load_dev_execution_request
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import load_dataset
from ai_worker.tasks.evaluation.retrieval_replay import (
    ReplayRetrievalAdapter,
    build_adapter_registry,
    load_retrieval_replay,
)
from ai_worker.tasks.evaluation.runner import AdapterRequest
from ai_worker.tasks.evaluation.schemas.common import DecisionStatus, TaskType

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
            {"case_id": "rag-ret-dev-001", "ranked_evidence_ids": ["evidence-1", "evidence-2"]}
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


def _adapter_request(case_id: str, *, variant_id: str = "RET-L") -> AdapterRequest:
    dataset = load_dataset(DATASET_PATH, evals_root=REPOSITORY_ROOT / "evals")
    case = next(item for item in dataset.cases if item.case_id == case_id)
    return AdapterRequest(
        run_id=RUN_ID,
        case=case,
        task_type=TaskType.RETRIEVAL,
        input_sha256="a" * 64,
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
        variant_id=request.variant_id,
        variant_manifest_hash=request.variant_manifest_hash,
    )

    with pytest.raises(EvaluationValidationError) as caught:
        ReplayRetrievalAdapter(replay).execute(request)

    assert caught.value.code is EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID


def test_replay_adapter_rejects_unknown_case() -> None:
    replay = load_retrieval_replay(REPLAY_PATH, repository_root=REPOSITORY_ROOT)
    request = _adapter_request("rag-ret-dev-001")
    request = AdapterRequest(
        run_id=request.run_id,
        case=request.case.model_copy(update={"case_id": "rag-ret-dev-999"}),
        task_type=request.task_type,
        input_sha256=request.input_sha256,
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
