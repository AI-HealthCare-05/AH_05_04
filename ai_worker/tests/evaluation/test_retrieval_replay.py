from pathlib import Path

import pytest

from ai_worker.tasks.evaluation.retrieval_replay import load_replay


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
