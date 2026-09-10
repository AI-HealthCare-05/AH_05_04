from pathlib import Path

import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_ci_python_test_lanes_use_their_required_import_boundaries() -> None:
    workflow = yaml.safe_load((PROJECT_ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    backend_step = next(
        step for step in jobs["test-backend"]["steps"] if step["name"] == "Run Backend Tests with Coverage"
    )
    worker_step = next(
        step for step in jobs["test-worker"]["steps"] if step["name"] == "Run AI Worker Unit Tests with Coverage"
    )

    assert backend_step["env"]["PYTHONPATH"] == "${{ github.workspace }}/backend:${{ github.workspace }}"
    assert worker_step["env"]["PYTHONPATH"] == "${{ github.workspace }}"
