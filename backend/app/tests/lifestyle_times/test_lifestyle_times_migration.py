import importlib
from typing import Any

import pytest


class FakeResult:
    def __init__(self, *, has_row: bool) -> None:
        self._has_row = has_row

    def first(self) -> tuple[int] | None:
        return (1,) if self._has_row else None


class FakeConnection:
    def __init__(self, *, has_row: bool) -> None:
        self._has_row = has_row
        self.statements: list[str] = []

    def execute(self, statement: object) -> FakeResult:
        statement_text = str(statement)
        self.statements.append(statement_text)
        return FakeResult(has_row=self._has_row and statement_text.startswith("SELECT 1"))


def _migration() -> Any:
    return importlib.import_module("backend.alembic.versions.556a1b2c3d4e_create_lifestyle_times")


def test_downgrade_guard_rejects_existing_lifestyle_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _migration()
    connection = FakeConnection(has_row=True)
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    with pytest.raises(RuntimeError, match="Cannot downgrade lifestyle_times"):
        migration._raise_if_lifestyle_times_exists()

    assert connection.statements == [
        "LOCK TABLE lifestyle_times IN ACCESS EXCLUSIVE MODE",
        "SELECT 1 FROM lifestyle_times LIMIT 1",
    ]


def test_empty_downgrade_checks_then_drops_table(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _migration()
    connection = FakeConnection(has_row=False)
    dropped: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration.op, "drop_table", dropped.append)

    migration.downgrade()

    assert dropped == ["lifestyle_times"]
    assert connection.statements == [
        "LOCK TABLE lifestyle_times IN ACCESS EXCLUSIVE MODE",
        "SELECT 1 FROM lifestyle_times LIMIT 1",
    ]
