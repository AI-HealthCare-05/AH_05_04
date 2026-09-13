from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from app.commands import generate_unconfirmed_checkins as command
from app.services.medication_checkins import UnconfirmedGenerationResult


def _session_factory(session: AsyncMock) -> Mock:
    context = AsyncMock()
    context.__aenter__.return_value = session
    context.__aexit__.return_value = None
    return Mock(return_value=context)


async def test_command_generates_unconfirmed_and_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = UnconfirmedGenerationResult(processed_count=2, duplicate_count=1)
    scheduler = AsyncMock()
    scheduler.generate_unconfirmed.return_value = expected
    session = AsyncMock()
    repository_type = Mock(return_value=object())
    monkeypatch.setattr(command, "MedicationCheckinRepository", repository_type)
    monkeypatch.setattr(command, "MedicationCheckinDeadlineScheduler", Mock(return_value=scheduler))
    now = datetime(2026, 9, 9, tzinfo=UTC)

    result = await command.generate_unconfirmed_checkins_once(
        now=now,
        session_factory=_session_factory(session),
    )

    assert result == expected
    repository_type.assert_called_once_with(session)
    scheduler.generate_unconfirmed.assert_awaited_once_with(now=now)
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


async def test_command_rolls_back_failed_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = AsyncMock()
    scheduler.generate_unconfirmed.side_effect = RuntimeError("synthetic failure")
    session = AsyncMock()
    monkeypatch.setattr(command, "MedicationCheckinRepository", Mock(return_value=object()))
    monkeypatch.setattr(command, "MedicationCheckinDeadlineScheduler", Mock(return_value=scheduler))

    with pytest.raises(RuntimeError, match="synthetic failure"):
        await command.generate_unconfirmed_checkins_once(
            now=datetime(2026, 9, 9, tzinfo=UTC),
            session_factory=_session_factory(session),
        )

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()
