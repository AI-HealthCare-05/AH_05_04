from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "backend" / "alembic" / "versions" / "235a1b2c3d4e_idempotency_hmac_rotation_scope.py"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("idempotency_hmac_rotation_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Migration module을 불러올 수 없습니다.")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class FakeConnection:
    def __init__(self, *, async_conflicts: int = 0, sync_conflicts: int = 0) -> None:
        self.async_conflicts = async_conflicts
        self.sync_conflicts = sync_conflicts
        self.statements: list[str] = []

    def execute(self, statement: object) -> ScalarResult:
        text = str(statement)
        self.statements.append(text)
        if "LOCK TABLE idempotency_record" in text:
            return ScalarResult(0)
        if "record_type = 'ASYNC_JOB'" in text:
            return ScalarResult(self.async_conflicts)
        if "record_type = 'SYNC_MUTATION'" in text:
            return ScalarResult(self.sync_conflicts)
        return ScalarResult(0)


def test_migration_metadata_has_single_parent_revision() -> None:
    migration = _load_migration()

    assert migration.revision == "235a1b2c3d4e"
    assert isinstance(migration.down_revision, str)
    assert migration.down_revision


def test_downgrade_guard_allows_when_previous_unique_scope_has_no_conflict() -> None:
    migration = _load_migration()
    connection = FakeConnection()

    migration._ensure_downgrade_unique_scope_is_data_safe(connection)

    assert any("LOCK TABLE idempotency_record" in statement for statement in connection.statements)
    assert len(connection.statements) == 3


@pytest.mark.parametrize(
    ("async_conflicts", "sync_conflicts"),
    [
        (1, 0),
        (0, 1),
    ],
)
def test_downgrade_guard_rejects_when_previous_unique_scope_would_conflict(
    async_conflicts: int,
    sync_conflicts: int,
) -> None:
    migration = _load_migration()
    connection = FakeConnection(async_conflicts=async_conflicts, sync_conflicts=sync_conflicts)

    with pytest.raises(RuntimeError) as error:
        migration._ensure_downgrade_unique_scope_is_data_safe(connection)

    message = str(error.value)
    assert "Cannot downgrade revision 235a1b2c3d4e" in message
    assert f"async_conflicts={async_conflicts}" in message
    assert f"sync_conflicts={sync_conflicts}" in message
