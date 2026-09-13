from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from app.commands import generate_medication_occurrences as command
from app.services.medication_occurrences import RollingOccurrenceGenerationResult


def _session_factory(session: AsyncMock) -> Mock:
    context = AsyncMock()
    context.__aenter__.return_value = session
    context.__aexit__.return_value = None
    return Mock(return_value=context)


async def test_management_command_generates_and_commits_once(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 9, 9, tzinfo=UTC)
    expected = RollingOccurrenceGenerationResult(
        horizon_start=date(2026, 9, 9),
        horizon_end=date(2026, 9, 22),
        created_count=14,
        duplicate_count=0,
        ended_schedule_count=1,
    )
    scheduler = AsyncMock()
    scheduler.generate.return_value = expected
    scheduler_type = Mock(return_value=scheduler)
    repository_type = Mock(return_value=object())
    session = AsyncMock()
    factory = _session_factory(session)
    monkeypatch.setattr(command, "MedicationOccurrenceScheduler", scheduler_type)
    monkeypatch.setattr(command, "MedicationScheduleRepository", repository_type)

    result = await command.generate_medication_occurrences_once(now=now, session_factory=factory)

    assert result == expected
    repository_type.assert_called_once_with(session)
    scheduler_type.assert_called_once_with(repository_type.return_value, service_timezone=command.config.TIMEZONE)
    scheduler.generate.assert_awaited_once_with(now=now)
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


async def test_management_command_rolls_back_failed_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("synthetic failure")
    scheduler = AsyncMock()
    scheduler.generate.side_effect = failure
    session = AsyncMock()
    factory = _session_factory(session)
    monkeypatch.setattr(command, "MedicationOccurrenceScheduler", Mock(return_value=scheduler))
    monkeypatch.setattr(command, "MedicationScheduleRepository", Mock(return_value=object()))

    with pytest.raises(RuntimeError, match="synthetic failure"):
        await command.generate_medication_occurrences_once(
            now=datetime(2026, 9, 9, tzinfo=UTC),
            session_factory=factory,
        )

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()
