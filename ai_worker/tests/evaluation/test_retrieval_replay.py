from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_worker.tasks.evaluation.retrieval_replay import load_replay
from ai_worker.tasks.evaluation.retrieval_replay import ReplayRetrievalAdapter
from ai_worker.tasks.evaluation.runner import AdapterRequest
from ai_worker.tasks.evaluation.schemas.common import TaskType


def test_load_replay_rejects_duplicate_ranked_evidence_ids(tmp_path: Path) -> None:
    fixture = tmp_path / "replay.json"
    fixture.write_text(
        '{"case_results":[{"case_id":"case-1","ranked_evidence_ids":["evidence-1","evidence-1"]}]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique"):
        load_replay(fixture)


def test_load_replay_returns_ranked_ids_by_case(tmp_path: Path) -> None:
    fixture = tmp_path / "replay.json"
    fixture.write_text(
        '{"case_results":[{"case_id":"case-1","ranked_evidence_ids":["evidence-1","evidence-2"]}]}',
        encoding="utf-8",
    )

    assert load_replay(fixture)["case-1"] == ("evidence-1", "evidence-2")


def test_replay_adapter_creates_retrieval_case_result() -> None:
    adapter = ReplayRetrievalAdapter({"case-1": ("evidence-1", "evidence-2")})
    request = SimpleNamespace(
        run_id="123e4567-e89b-42d3-a456-426614174000",
        case=SimpleNamespace(
            case_id="case-1",
            dataset_code="synthetic-dataset",
            dataset_version="1.0.0",
            partition=SimpleNamespace(value="DEV"),
        ),
        task_type=TaskType.RETRIEVAL,
        input_sha256="a" * 64,
    )

    result = adapter.execute(request)  # type: ignore[arg-type]

    assert result.retrieved_evidence_ids == ("evidence-1", "evidence-2")
    assert result.selected_evidence_ids == ("evidence-1", "evidence-2")
    assert result.execution_status.value == "COMPLETED"
