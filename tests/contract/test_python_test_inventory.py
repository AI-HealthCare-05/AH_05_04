import runpy
import subprocess
import sys
from pathlib import Path, PureWindowsPath

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INVENTORY_CHECKER = PROJECT_ROOT / "scripts" / "ci" / "check_python_test_inventory.py"


def test_python_test_inventory_normalizes_windows_execution_targets_to_posix_tokens() -> None:
    execution_target = runpy.run_path(str(INVENTORY_CHECKER))["_execution_target"]

    assert execution_target(PureWindowsPath("tests/integration/test_outbox_publisher.py")) == (
        "tests/integration/test_outbox_publisher.py"
    )


def _run_inventory_check(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(INVENTORY_CHECKER), "--root", str(root)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_python_test_inventory_rejects_unclassified_test_file(tmp_path: Path) -> None:
    known_test = tmp_path / "backend" / "app" / "tests" / "test_known.py"
    unknown_test = tmp_path / "tests" / "new_suite" / "test_unknown.py"
    known_test.parent.mkdir(parents=True)
    unknown_test.parent.mkdir(parents=True)
    known_test.touch()
    unknown_test.touch()

    result = _run_inventory_check(tmp_path)

    assert result.returncode == 1
    assert "Unclassified Python test files:" in result.stderr
    assert "tests/new_suite/test_unknown.py" in result.stderr
    assert "backend/app/tests/test_known.py" not in result.stderr


def test_python_test_inventory_rejects_node_specific_default_selection(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "checks.yml"
    runner = tmp_path / "scripts" / "ci" / "run_test.sh"
    workflow.parent.mkdir(parents=True)
    runner.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "jobs:\n  fake:\n    steps:\n      - run: |\n"
        "          uv run pytest tests/integration/test_worker_job_execution_repository.py::test_one\n",
        encoding="utf-8",
    )
    runner.write_text("uv run pytest backend/app\n", encoding="utf-8")

    result = _run_inventory_check(tmp_path)

    assert result.returncode == 1
    assert "Node-specific pytest selections prevent automatic collection:" in result.stderr
    assert "test_worker_job_execution_repository.py::test_one" in result.stderr


def test_python_test_inventory_rejects_missing_execution_configs(tmp_path: Path) -> None:
    known_test = tmp_path / "backend" / "app" / "tests" / "test_known.py"
    known_test.parent.mkdir(parents=True)
    known_test.touch()

    result = _run_inventory_check(tmp_path)

    assert result.returncode == 1
    assert "Missing Python test execution config:" in result.stderr
    assert "scripts/ci/run_test.sh" in result.stderr
    assert ".github/workflows/checks.yml" in result.stderr


def test_python_test_inventory_requires_exact_pytest_arguments(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "checks.yml"
    runner = tmp_path / "scripts" / "ci" / "run_test.sh"
    workflow.parent.mkdir(parents=True)
    runner.parent.mkdir(parents=True, exist_ok=True)
    misleading_config = """
    # uv run pytest backend/app tests/integration/test_cors_and_errors.py
    uv run pytest backend/app.backup
    TARGET=tests/integration/test_worker_job_execution_repository.py
    uv run pytest "$TARGET::test_one"
    """
    workflow.write_text(misleading_config, encoding="utf-8")
    runner.write_text(misleading_config, encoding="utf-8")

    result = _run_inventory_check(tmp_path)

    assert result.returncode == 1
    assert "Node-specific pytest selections prevent automatic collection:" in result.stderr
    assert "$TARGET::test_one" in result.stderr
    assert "Default Python test targets missing from" in result.stderr
    assert "backend/app" in result.stderr
    assert "Opt-in Python tests unexpectedly included" not in result.stderr


def test_python_test_inventory_rejects_echoed_and_yaml_metadata_commands(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "checks.yml"
    runner = tmp_path / "scripts" / "ci" / "run_test.sh"
    workflow.parent.mkdir(parents=True)
    runner.parent.mkdir(parents=True, exist_ok=True)
    fake_command = (
        "uv run pytest backend/app tests/contract tests/migration tests/integration/rag "
        "ai_worker/tests/core ai_worker/tests/ocr ai_worker/tests/rag ai_worker/tests/evaluation "
        "tests/integration/test_worker_ocr_persistence.py tests/integration/test_outbox_publisher.py "
        "tests/integration/test_worker_job_execution_repository.py "
        "tests/integration/test_worker_dlq_outbox_repository.py "
        "tests/integration/test_worker_recovery_repository.py"
    )
    workflow.write_text(
        f"jobs:\n  fake:\n    env:\n      run: {fake_command}\n    steps:\n"
        f"      - name: {fake_command}\n        run: uv run echo pytest {fake_command}\n"
        "      - run: echo python scripts/ci/check_python_test_inventory.py\n",
        encoding="utf-8",
    )
    runner.write_text(
        f"uv run echo pytest {fake_command}\n"
        f"run_with_backend_test_database echo pytest {fake_command}\n"
        "echo python scripts/ci/check_python_test_inventory.py\n",
        encoding="utf-8",
    )

    result = _run_inventory_check(tmp_path)

    assert result.returncode == 1
    assert "Default Python test targets missing from" in result.stderr
    assert "Python test inventory preflight missing from" in result.stderr


def test_python_test_inventory_rejects_unsupported_pytest_options(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "checks.yml"
    runner = tmp_path / "scripts" / "ci" / "run_test.sh"
    workflow.parent.mkdir(parents=True)
    runner.parent.mkdir(parents=True, exist_ok=True)
    filtered_command = "uv run pytest -konly_old_test -mSMOKE --rootdir backend/app -o addopts=-kold"
    workflow.write_text(
        f"jobs:\n  fake:\n    steps:\n      - run: {filtered_command}\n",
        encoding="utf-8",
    )
    runner.write_text(f"{filtered_command}\n", encoding="utf-8")

    result = _run_inventory_check(tmp_path)

    assert result.returncode == 1
    assert "Unsupported pytest options may prevent automatic collection:" in result.stderr
    assert "-konly_old_test" in result.stderr
    assert "-mSMOKE" in result.stderr
    assert "--rootdir" in result.stderr
    assert "-o addopts=-kold" in result.stderr
    assert "backend/app" in result.stderr


def test_python_test_inventory_rejects_pytest_addopts_from_every_configuration(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "checks.yml"
    runner = tmp_path / "scripts" / "ci" / "run_test.sh"
    workflow.parent.mkdir(parents=True)
    runner.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "env:\n  PYTEST_ADDOPTS: -konly_old\njobs:\n  fake:\n    steps:\n"
        "      - run: export PYTEST_ADDOPTS=-kfrom_step\n"
        "      - run: uv run pytest backend/app\n",
        encoding="utf-8",
    )
    runner.write_text(
        "PYTEST_ADDOPTS=-konly_old uv run pytest backend/app\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-m SMOKE"\n',
        encoding="utf-8",
    )

    result = _run_inventory_check(tmp_path)

    assert result.returncode == 1
    assert "GitHub Actions sets non-empty env.PYTEST_ADDOPTS" in result.stderr
    assert "Command sets PYTEST_ADDOPTS in .github/workflows/checks.yml" in result.stderr
    assert "Command sets PYTEST_ADDOPTS in scripts/ci/run_test.sh" in result.stderr
    assert "Inherited PYTEST_ADDOPTS is not pinned empty by scripts/ci/run_test.sh" in result.stderr
    assert "Pytest addopts may prevent automatic collection: -m SMOKE" in result.stderr
    assert "backend/app" in result.stderr


def test_python_test_inventory_splits_background_shell_commands(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "checks.yml"
    runner = tmp_path / "scripts" / "ci" / "run_test.sh"
    workflow.parent.mkdir(parents=True)
    runner.parent.mkdir(parents=True, exist_ok=True)
    background_command = "uv run pytest backend/app & echo pytest tests/contract"
    workflow.write_text(
        f"jobs:\n  fake:\n    steps:\n      - run: {background_command}\n",
        encoding="utf-8",
    )
    runner.write_text(f"{background_command}\n", encoding="utf-8")

    result = _run_inventory_check(tmp_path)

    assert result.returncode == 1
    assert "Default Python test targets missing from" in result.stderr
    assert "tests/contract" in result.stderr


def test_python_test_inventory_ignores_directories_named_like_tests(tmp_path: Path) -> None:
    fake_test_directory = tmp_path / "misc" / "test_not_a_file.py"
    fake_test_directory.mkdir(parents=True)

    result = _run_inventory_check(tmp_path)

    assert "misc/test_not_a_file.py" not in result.stderr


def test_repository_python_tests_are_all_classified_for_default_or_opt_in_execution() -> None:
    result = _run_inventory_check(PROJECT_ROOT)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Python test inventory is fully classified." in result.stdout
