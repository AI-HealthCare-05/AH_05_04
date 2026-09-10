from pathlib import Path

from scripts.ci.check_protected_table_writes import protected_writes, violations


def test_detects_orm_constructor_and_core_dml(tmp_path: Path) -> None:
    source = """
from sqlalchemy import Table, update
from app.models.rag_runtime import RagRuntimeEnvironmentTransition

transition = RagRuntimeEnvironmentTransition(environment_revision=2)
snapshot = Table("rag_source_snapshot", metadata)
statement = update(snapshot).values(verification_status="CURRENT")
"""

    assert protected_writes(tmp_path / "writer.py", source) == {
        ("rag_runtime_environment_transition", 5),
        ("rag_source_snapshot", 7),
    }


def test_rejects_unapproved_writer_and_accepts_reviewed_repository(tmp_path: Path) -> None:
    unapproved = tmp_path / "backend/app/services/runtime_bypass.py"
    unapproved.parent.mkdir(parents=True)
    unapproved.write_text(
        "from app.models.rag_runtime import RagRuntimeEnvironmentTransition\n"
        "row = RagRuntimeEnvironmentTransition(environment_revision=2)\n"
    )
    approved = tmp_path / "backend/app/repositories/rag_runtime_repository.py"
    approved.parent.mkdir(parents=True)
    approved.write_text(unapproved.read_text())

    assert violations(tmp_path, [str(unapproved.relative_to(tmp_path))]) == [
        "backend/app/services/runtime_bypass.py:2: unapproved write to rag_runtime_environment_transition"
    ]
    assert violations(tmp_path, [str(approved.relative_to(tmp_path))]) == []


def test_detects_raw_sql_write(tmp_path: Path) -> None:
    path = tmp_path / "ai_worker/tasks/unsafe_writer.py"
    path.parent.mkdir(parents=True)
    path.write_text('statement = "DELETE FROM prescription_version WHERE id=:id"\n')

    assert violations(tmp_path, [str(path.relative_to(tmp_path))]) == [
        "ai_worker/tasks/unsafe_writer.py:1: unapproved write to prescription_version"
    ]


def test_draft_pr_372_catalog_tables_have_no_preapproved_writer(tmp_path: Path) -> None:
    path = tmp_path / "ai_worker/adapters/sqlalchemy_catalog_write_support.py"
    path.parent.mkdir(parents=True)
    path.write_text('statement = "INSERT INTO rag_catalog_set (id) VALUES (:id)"\n')

    assert violations(tmp_path, [str(path.relative_to(tmp_path))]) == [
        "ai_worker/adapters/sqlalchemy_catalog_write_support.py:1: unapproved write to rag_catalog_set"
    ]


def test_repository_tree_has_no_unapproved_protected_writes() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = [str(path.relative_to(root)) for path in root.rglob("*.py")]

    assert violations(root, paths) == []


def test_ci_and_local_runner_enforce_protected_write_policy() -> None:
    root = Path(__file__).resolve().parents[2]
    command = "python scripts/ci/check_protected_table_writes.py"

    assert command in (root / ".github/workflows/checks.yml").read_text()
    assert command in (root / "scripts/ci/run_test.sh").read_text()
