from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "backend" / "alembic" / "versions" / "431a1b2c3d4e_create_email_verification_token.py"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("email_verification_token_migration", MIGRATION_PATH)
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
    def __init__(self, count: int) -> None:
        self._count = count
        self.execute_count = 0

    def execute(self, statement: object) -> ScalarResult:
        self.execute_count += 1
        text = str(statement)
        if "SELECT count(*) FROM email_verification_token" in text:
            return ScalarResult(self._count)
        return ScalarResult(0)


def test_downgrade_guard_rejects_when_email_verification_token_has_data() -> None:
    migration = _load_migration()
    connection = FakeConnection(count=2)

    with pytest.raises(RuntimeError, match="Cannot downgrade revision 431a1b2c3d4e"):
        migration._ensure_email_verification_downgrade_is_data_safe(connection)

    assert connection.execute_count == 2


def test_downgrade_guard_allows_when_email_verification_token_empty() -> None:
    migration = _load_migration()
    connection = FakeConnection(count=0)

    migration._ensure_email_verification_downgrade_is_data_safe(connection)

    assert connection.execute_count == 2
