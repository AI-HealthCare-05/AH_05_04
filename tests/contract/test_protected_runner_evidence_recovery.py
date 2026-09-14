"""Issue #368 증빙 해시가 재현 가능하고, 불일치가 파일을 고치지 않고 보고되는지 확인합니다."""

import json
from pathlib import Path

import pytest

from ai_worker.tasks.evaluation.protected_retrieval_infrastructure_evidence import (
    EVIDENCE_JSON_PATH,
    EVIDENCE_MARKDOWN_PATH,
)
from scripts.verify_protected_runner_evidence import RECOVERY_GUIDANCE, verify_protected_runner_evidence

PROJECT_ROOT = Path(__file__).resolve().parents[2]

IMPLEMENTATION_PATHS = tuple(
    record["path"] for record in json.loads((PROJECT_ROOT / EVIDENCE_JSON_PATH).read_text())["implementation_files"]
)
EVIDENCE_PATHS = (EVIDENCE_JSON_PATH, EVIDENCE_MARKDOWN_PATH)


@pytest.fixture
def copied_project(tmp_path: Path) -> Path:
    for relative in (*EVIDENCE_PATHS, *IMPLEMENTATION_PATHS):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((PROJECT_ROOT / relative).read_bytes())
    return tmp_path


def test_repository_protected_runner_evidence_is_reproducible() -> None:
    assert (
        verify_protected_runner_evidence(PROJECT_ROOT)
        == json.loads((PROJECT_ROOT / EVIDENCE_JSON_PATH).read_text())["evidence_sha256"]
    )


def test_rendered_markdown_exposes_every_hash_input() -> None:
    markdown = (PROJECT_ROOT / EVIDENCE_MARKDOWN_PATH).read_text(encoding="utf-8")
    for record in json.loads((PROJECT_ROOT / EVIDENCE_JSON_PATH).read_text())["implementation_files"]:
        assert record["path"] in markdown
        assert record["raw_sha256"] in markdown


@pytest.mark.parametrize("changed", [*EVIDENCE_PATHS, IMPLEMENTATION_PATHS[0]])
def test_mismatch_reports_recovery_without_writing(copied_project: Path, changed: str) -> None:
    path = copied_project / changed
    path.write_bytes(path.read_bytes() + b"\n# synthetic drift\n")
    before = {relative: (copied_project / relative).read_bytes() for relative in EVIDENCE_PATHS}

    with pytest.raises(ValueError) as failure:
        verify_protected_runner_evidence(copied_project)

    assert RECOVERY_GUIDANCE in str(failure.value)
    assert {relative: (copied_project / relative).read_bytes() for relative in EVIDENCE_PATHS} == before
