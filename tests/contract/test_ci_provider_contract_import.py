from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_ci_test_job_adds_repository_root_to_pythonpath() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
    test_job = workflow.split("  test:\n", maxsplit=1)[1].split("    services:\n", maxsplit=1)[0]

    assert "      PYTHONPATH: ${{ github.workspace }}" in test_job
