"""Guide-AI Job mapping migration downgrade guard tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

MIGRATION_PATH = (
    Path(__file__).parents[2] / "backend" / "alembic" / "versions" / "20fd11d29ecc_add_guide_ai_job_mapping.py"
)


class ScalarResult:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class FakeConnection:
    def __init__(self, linked_guide_count: int) -> None:
        self.linked_guide_count = linked_guide_count
        self.statements: list[Any] = []

    def execute(self, statement: Any) -> ScalarResult:
        self.statements.append(statement)
        return ScalarResult(self.linked_guide_count)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "guide_ai_job_mapping_migration",
        MIGRATION_PATH,
    )
    assert spec is not None
    assert spec.loader is not None

    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _compiled_sql(statement: Any) -> str:
    return " ".join(
        str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).split()
    ).lower()


def test_downgrade_guard_rejects_existing_ai_job_links() -> None:
    migration = _load_migration()
    connection = FakeConnection(linked_guide_count=1)

    with pytest.raises(RuntimeError, match="contains linked AI Jobs"):
        migration._ensure_downgrade_is_data_safe(connection)

    assert len(connection.statements) == 2


def test_downgrade_guard_allows_no_ai_job_links() -> None:
    migration = _load_migration()
    connection = FakeConnection(linked_guide_count=0)

    migration._ensure_downgrade_is_data_safe(connection)

    assert len(connection.statements) == 2


def test_downgrade_guard_locks_guide_before_counting() -> None:
    migration = _load_migration()
    connection = FakeConnection(linked_guide_count=0)

    migration._ensure_downgrade_is_data_safe(connection)

    lock_sql = _compiled_sql(connection.statements[0])
    count_sql = _compiled_sql(connection.statements[1])

    assert lock_sql == "lock table guide in access exclusive mode"
    assert "count(*)" in count_sql
    assert "guide.ai_job_id is not null" in count_sql


def test_downgrade_guard_counts_only_non_null_ai_job_ids() -> None:
    migration = _load_migration()
    connection = FakeConnection(linked_guide_count=0)

    migration._ensure_downgrade_is_data_safe(connection)

    statement_sql = _compiled_sql(connection.statements[1])

    assert "count(*)" in statement_sql
    assert "guide.ai_job_id is not null" in statement_sql
    assert "count(guide.ai_job_id is not null)" not in statement_sql
