import importlib.util
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    PROJECT_ROOT / "backend" / "alembic" / "versions" / "206a1b2c3d4e_add_refresh_rotation_password_reset.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location(
        "refresh_rotation_password_reset_migration",
        MIGRATION_PATH,
    )

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
    def __init__(self, row_count: int) -> None:
        self._row_count = row_count
        self.execute_count = 0

    def execute(self, _statement: object) -> ScalarResult:
        self.execute_count += 1
        return ScalarResult(self._row_count)


def test_downgrade_guard_rejects_data_loss() -> None:
    migration = _load_migration()
    connection = FakeConnection(row_count=3)

    with pytest.raises(RuntimeError, match="Cannot downgrade revision 206a1b2c3d4e"):
        migration._ensure_password_reset_token_downgrade_is_data_safe(connection)

    # LOCK TABLE + SELECT count(*) — 두 statement 모두 실제로 실행됐는지 확인한다.
    assert connection.execute_count == 2


def test_downgrade_guard_allows_empty_table() -> None:
    migration = _load_migration()
    connection = FakeConnection(row_count=0)

    migration._ensure_password_reset_token_downgrade_is_data_safe(connection)

    assert connection.execute_count == 2
