import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy.dialects import postgresql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_FILE = PROJECT_ROOT / "backend" / "alembic" / "versions" / "c3f8a12d9e47_add_ocr_ai_job_mapping.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "ocr_ai_job_mapping_migration",
        MIGRATION_FILE,
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class FakeConnection:
    def __init__(self, count: int) -> None:
        self.count = count
        self.statements: list[object] = []

    def execute(self, statement: object) -> ScalarResult:
        self.statements.append(statement)
        return ScalarResult(self.count)


def test_downgrade_guard_rejects_existing_ai_job_links() -> None:
    migration = _load_migration()
    connection = FakeConnection(count=1)

    with pytest.raises(
        RuntimeError,
        match="Cannot downgrade revision c3f8a12d9e47",
    ):
        migration._ensure_downgrade_is_data_safe(connection)

    assert len(connection.statements) == 1


def test_downgrade_guard_allows_no_ai_job_links() -> None:
    migration = _load_migration()
    connection = FakeConnection(count=0)

    migration._ensure_downgrade_is_data_safe(connection)

    assert len(connection.statements) == 1


def test_downgrade_guard_counts_only_non_null_ai_job_ids() -> None:
    migration = _load_migration()
    connection = FakeConnection(count=0)

    migration._ensure_downgrade_is_data_safe(connection)

    statement = connection.statements[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    normalized_sql = " ".join(sql.split()).lower()

    assert "count(*)" in normalized_sql
    assert "where ocr_job.ai_job_id is not null" in normalized_sql
    assert "count(ocr_job.ai_job_id is not null)" not in normalized_sql
