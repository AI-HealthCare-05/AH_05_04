import importlib.util
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "alembic" / "versions" / "529b2a36b677_add_medication_strength_and_ocr_prompt_.py"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location(
        "medication_strength_migration",
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
    def __init__(self, counts: tuple[int, int, int]) -> None:
        self._counts = iter(counts)
        self.execute_count = 0

    def execute(self, _statement: object) -> ScalarResult:
        self.execute_count += 1
        return ScalarResult(next(self._counts))


@pytest.mark.parametrize(
    "counts",
    [
        (1, 0, 0),  # MEDICATION_STRENGTH extracted field 존재
        (0, 1, 0),  # medication.strength_text 존재
        (0, 0, 1),  # ocr_job.prompt_version 존재
    ],
)
def test_downgrade_guard_rejects_data_loss(
    counts: tuple[int, int, int],
) -> None:
    migration = _load_migration()
    connection = FakeConnection(counts)

    with pytest.raises(
        RuntimeError,
        match="Cannot downgrade revision 529b2a36b677",
    ):
        migration._ensure_downgrade_is_data_safe(connection)

    assert connection.execute_count == 3


def test_downgrade_guard_allows_empty_new_fields() -> None:
    migration = _load_migration()
    connection = FakeConnection((0, 0, 0))

    migration._ensure_downgrade_is_data_safe(connection)

    assert connection.execute_count == 3
