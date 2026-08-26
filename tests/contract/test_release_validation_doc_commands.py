"""docs/validation/ai-one-cycle-release.md의 고정 명령이 backend/ 이동 이후에도
저장소 루트에서 그대로 실행 가능한지 확인합니다(PR #87 리뷰).

app/이 backend/app/으로 이동한 뒤 `python -m app.release_validation...`을 저장소
루트에서 그대로 실행하면 `ModuleNotFoundError: No module named 'app'`이 발생한다.
문서의 고정 명령에는 PYTHONPATH=backend가 포함되어야 하며, 문서 문자열만 확인하는
대신 실제로 그 명령을 실행해 회귀를 잡는다.
"""

import os
import re
import shlex
import subprocess
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DOC_PATH = PROJECT_ROOT / "docs" / "validation" / "ai-one-cycle-release.md"
_PYTHONPATH_PREFIX = "PYTHONPATH=backend "


def _release_validation_command_blocks() -> list[str]:
    text = DOC_PATH.read_text(encoding="utf-8")
    blocks = re.findall(r"```bash\n(.*?)```", text, re.DOTALL)
    return [block for block in blocks if "app.release_validation.ai_one_cycle_smoke" in block]


def _joined_command(block: str) -> str:
    return " ".join(line.rstrip("\\").strip() for line in block.strip().splitlines())


def test_all_release_validation_commands_declare_backend_pythonpath() -> None:
    blocks = _release_validation_command_blocks()

    assert len(blocks) == 4, f"문서의 release_validation 실행 명령 블록 수가 예상과 다릅니다: {len(blocks)}"
    for block in blocks:
        command = _joined_command(block)
        assert command.startswith(_PYTHONPATH_PREFIX), (
            "backend/ 이동 이후 저장소 루트에서 `python -m app...`을 실행하려면 "
            f"PYTHONPATH=backend가 필요합니다: {command!r}"
        )


def test_cleanup_only_command_runs_from_repository_root_without_module_error() -> None:
    """문서의 cleanup-only 고정 명령을 저장소 루트에서 실제로 실행한다."""
    blocks = [block for block in _release_validation_command_blocks() if "--cleanup-only" in block]
    command = _joined_command(blocks[0]).replace("<uuid>", str(uuid4()))

    assert command.startswith(_PYTHONPATH_PREFIX)
    argv = shlex.split(command.removeprefix(_PYTHONPATH_PREFIX))

    completed = subprocess.run(
        argv,
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": "backend"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert "ModuleNotFoundError" not in completed.stderr, completed.stderr
    assert completed.returncode == 2
    assert len(completed.stdout.splitlines()) == 1
