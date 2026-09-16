from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.admin.source_artifact_cleanup import CleanupExecutorConfig, validate_cleanup_executor_session


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "SOURCE_CLEANUP_EXECUTOR_HOST": "localhost",
        "SOURCE_CLEANUP_EXECUTOR_PORT": "5432",
        "SOURCE_CLEANUP_EXECUTOR_NAME": "synthetic",
        "SOURCE_CLEANUP_EXECUTOR_USER": "synthetic_executor",
        "SOURCE_CLEANUP_EXECUTOR_PASSWORD": "synthetic-password",
        "SOURCE_CLEANUP_EXECUTOR_ACTOR": "synthetic-executor",
        "SOURCE_ARTIFACT_LOCAL_ROOT": str(tmp_path / "artifacts"),
        "SOURCE_CLEANUP_JOURNAL_ROOT": str(tmp_path / "journal"),
    }


def test_executor_config_hides_private_values(tmp_path: Path) -> None:
    config = CleanupExecutorConfig.from_environment(_environment(tmp_path))
    rendered = repr(config)
    assert "synthetic-password" not in rendered
    assert str(tmp_path) not in rendered


@pytest.mark.parametrize(
    "change",
    [
        {"SOURCE_CLEANUP_EXECUTOR_PASSWORD": ""},
        {"SOURCE_ARTIFACT_LOCAL_ROOT": "relative"},
        {"SOURCE_CLEANUP_JOURNAL_ROOT": "relative"},
        {"SOURCE_WRITER_PASSWORD": "mixed-secret"},
    ],
)
def test_executor_config_fails_closed_on_incomplete_or_mixed_environment(
    tmp_path: Path, change: dict[str, str]
) -> None:
    with pytest.raises(ValueError):
        CleanupExecutorConfig.from_environment({**_environment(tmp_path), **change})


@pytest.mark.asyncio
@pytest.mark.parametrize(("unsafe", "readable"), [(True, True), (False, False), (None, True)])
async def test_executor_session_rejects_privileged_or_unreadable_role(unsafe: object, readable: object) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.side_effect = [unsafe, readable]
    with pytest.raises(ValueError, match="read-only"):
        await validate_cleanup_executor_session(session)


@pytest.mark.asyncio
async def test_executor_session_accepts_only_read_only_source_role() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.side_effect = [False, True]
    await validate_cleanup_executor_session(session)
    assert session.scalar.await_count == 2
