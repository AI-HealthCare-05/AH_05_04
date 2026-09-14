import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CLASSIFIER = PROJECT_ROOT / "scripts" / "ci" / "classify_ci_scope.py"


def _load_classifier() -> dict[str, Any]:
    assert CLASSIFIER.is_file(), "CI scope classifier must exist"
    return runpy.run_path(str(CLASSIFIER))


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("frontend/src/App.tsx", {"frontend", "contract"}),
        ("backend/app/main.py", {"lint", "backend", "contract"}),
        (
            "backend/alembic/versions/revision.py",
            {"lint", "migration", "backend", "contract"},
        ),
        ("backend/alembic.ini", {"lint", "migration", "backend", "contract"}),
        ("ai_worker/tasks/ocr.py", {"lint", "worker", "contract"}),
        ("tests/contract/test_new_policy.py", {"lint", "contract"}),
        ("tests/services/test_new_service.py", {"lint", "contract"}),
        (
            "unknown/new_runtime.py",
            {"lint", "frontend", "migration", "backend", "contract", "worker"},
        ),
    ],
)
def test_classify_paths_routes_each_domain_without_hiding_unknown_paths(path: str, expected: set[str]) -> None:
    classifier = _load_classifier()

    scope = classifier["classify_paths"]([path])

    assert scope.enabled_names() == expected


def test_classify_paths_unions_multiple_domains() -> None:
    classifier = _load_classifier()

    scope = classifier["classify_paths"](["frontend/src/App.tsx", "ai_worker/tasks/ocr.py"])

    assert scope.enabled_names() == {"lint", "frontend", "contract", "worker"}


@pytest.mark.parametrize("path", ["docs/testing.md", "README.md"])
def test_classify_paths_runs_contracts_that_consume_repository_documentation(path: str) -> None:
    classifier = _load_classifier()

    scope = classifier["classify_paths"]([path])

    assert scope.enabled_names() == {"contract"}


@pytest.mark.parametrize("path", [".github/PULL_REQUEST_TEMPLATE.md", ".github/ISSUE_TEMPLATE/task.md"])
def test_classify_paths_skips_runtime_suites_for_collaboration_templates(path: str) -> None:
    classifier = _load_classifier()

    scope = classifier["classify_paths"]([path])

    assert scope.enabled_names() == set()


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/checks.yml",
        "scripts/ci/run_test.sh",
        "pyproject.toml",
        "uv.lock",
        "provider_contracts/example.py",
        "tests/integration/test_unclassified.py",
    ],
)
def test_classify_paths_runs_everything_for_shared_or_ci_sensitive_paths(path: str) -> None:
    classifier = _load_classifier()

    scope = classifier["classify_paths"]([path])

    assert scope.enabled_names() == {"lint", "frontend", "migration", "backend", "contract", "worker"}
    assert scope.full is True


def test_classify_paths_normalizes_windows_separators() -> None:
    classifier = _load_classifier()

    scope = classifier["classify_paths"]([r"ai_worker\tests\core\test_new.py"])

    assert scope.enabled_names() == {"lint", "worker", "contract"}


def test_classify_paths_fails_closed_for_an_empty_diff() -> None:
    classifier = _load_classifier()

    scope = classifier["classify_paths"]([])

    assert scope.full is True


def test_cli_classifies_a_real_git_diff_and_writes_github_outputs(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "CI Test"], cwd=repository, check=True)
    source = repository / "backend" / "app" / "service.py"
    source.parent.mkdir(parents=True)
    source.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    source.write_text("after\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "head"], cwd=repository, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    output = tmp_path / "github-output"

    result = subprocess.run(
        [
            sys.executable,
            str(CLASSIFIER),
            "--event-name",
            "pull_request",
            "--base-sha",
            base_sha,
            "--head-sha",
            head_sha,
            "--github-output",
            str(output),
        ],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert output.read_text(encoding="utf-8").splitlines() == [
        "lint=true",
        "frontend=false",
        "migration=false",
        "backend=true",
        "contract=true",
        "worker=false",
        "full=false",
    ]


def test_cli_uses_the_pull_request_merge_base_instead_of_unrelated_base_branch_commits(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "CI Test"], cwd=repository, check=True)
    readme = repository / "README.md"
    readme.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "common base"], cwd=repository, check=True)
    subprocess.run(["git", "branch", "feature"], cwd=repository, check=True)
    frontend = repository / "frontend" / "src" / "App.tsx"
    frontend.parent.mkdir(parents=True)
    frontend.write_text("base branch only\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "advance base"], cwd=repository, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "switch", "-q", "feature"], cwd=repository, check=True)
    backend = repository / "backend" / "app" / "service.py"
    backend.parent.mkdir(parents=True)
    backend.write_text("feature branch only\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "feature change"], cwd=repository, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    output = tmp_path / "github-output"

    result = subprocess.run(
        [
            sys.executable,
            str(CLASSIFIER),
            "--event-name",
            "pull_request",
            "--base-sha",
            base_sha,
            "--head-sha",
            head_sha,
            "--github-output",
            str(output),
        ],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    parsed = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert parsed["frontend"] == "false"
    assert parsed["backend"] == "true"
    assert parsed["contract"] == "true"


def test_cli_classifies_both_sides_of_a_cross_domain_rename(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "CI Test"], cwd=repository, check=True)
    backend = repository / "backend" / "app" / "service.py"
    backend.parent.mkdir(parents=True)
    backend.write_text("unchanged content\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    worker = repository / "ai_worker" / "tasks" / "service.py"
    worker.parent.mkdir(parents=True)
    subprocess.run(["git", "mv", str(backend), str(worker)], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "move service"], cwd=repository, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    output = tmp_path / "github-output"

    result = subprocess.run(
        [
            sys.executable,
            str(CLASSIFIER),
            "--event-name",
            "pull_request",
            "--base-sha",
            base_sha,
            "--head-sha",
            head_sha,
            "--github-output",
            str(output),
        ],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    parsed = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert parsed == {
        "lint": "true",
        "frontend": "false",
        "migration": "false",
        "backend": "true",
        "contract": "true",
        "worker": "true",
        "full": "false",
    }


def test_cli_uses_full_scope_for_non_pull_request_events(tmp_path: Path) -> None:
    output = tmp_path / "github-output"

    result = subprocess.run(
        [
            sys.executable,
            str(CLASSIFIER),
            "--event-name",
            "merge_group",
            "--github-output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    parsed = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert parsed == {
        "lint": "true",
        "frontend": "true",
        "migration": "true",
        "backend": "true",
        "contract": "true",
        "worker": "true",
        "full": "true",
    }


def test_cli_requires_both_pull_request_shas(tmp_path: Path) -> None:
    output = tmp_path / "github-output"

    result = subprocess.run(
        [
            sys.executable,
            str(CLASSIFIER),
            "--event-name",
            "pull_request",
            "--base-sha",
            "base-only",
            "--github-output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "--base-sha and --head-sha are required" in result.stderr
    assert not output.exists()
