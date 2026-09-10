import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config
from app.core.db.databases import Base
from app.core.errors import ApiError
from app.dtos.notifications import CreateReminderRequest
from app.models.notifications import NotificationRecord, NotificationStatus
from app.repositories.idempotency_repository import IdempotencyRepository
from app.repositories.medication_checkin_repository import MedicationCheckinRepository
from app.repositories.notification_repository import NotificationRepository
from app.services.idempotency import SyncMutationIdempotencyService, get_default_snapshot_cipher
from app.services.notifications import NotificationScheduler, NotificationService
from app.tests.notifications.test_notifications import NOW
from app.tests.repositories.test_medication_checkin_repository_integration import _create_occurrence
from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile


@pytest.fixture
async def race_database() -> AsyncIterator[tuple]:
    # Independent real transactions must see committed seed rows. A private schema
    # keeps them separate from the API suite's outer rollback transaction.
    schema = "notification_race_" + uuid4().hex
    admin = create_async_engine(config.database_url, poolclass=NullPool)
    engine = create_async_engine(
        config.database_url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as session:
            owner, profile = await _create_user_with_self_profile(session, label="race-owner")
            occurrence = await _create_occurrence(
                session, owner=owner, profile=profile, deadline_at=NOW + timedelta(hours=4)
            )
            occurrence_id, user_id = occurrence.id, owner.id
        yield factory, occurrence_id, user_id
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await admin.dispose()


@pytest.mark.parametrize("same_key", [True, False])
async def test_concurrent_reminders_one_row_and_same_key_replay(race_database, same_key):
    factory, occurrence_id, user_id = race_database
    ready = asyncio.Barrier(2)

    async def create(key):
        async with factory() as session:
            service = NotificationService(
                NotificationRepository(session),
                SyncMutationIdempotencyService(IdempotencyRepository(session), get_default_snapshot_cipher()),
                clock=lambda: NOW,
            )
            await ready.wait()
            try:
                result = await service.create_reminder(
                    user_id=user_id,
                    occurrence_id=occurrence_id,
                    request=CreateReminderRequest(scheduled_at=NOW + timedelta(hours=2)),
                    idempotency_key=key,
                )
                await session.commit()
                return result
            except ApiError as exc:
                await session.rollback()
                return exc

    first, second = await asyncio.wait_for(
        asyncio.gather(
            create("concurrent-key-one"),
            create("concurrent-key-one" if same_key else "concurrent-key-two"),
        ),
        timeout=10,
    )
    if same_key:
        assert not isinstance(first, ApiError) and not isinstance(second, ApiError)
        assert first.response_body == second.response_body
        assert first.is_replay != second.is_replay
    else:
        assert sum(isinstance(result, ApiError) for result in [first, second]) == 1
        failure = first if isinstance(first, ApiError) else second
        assert failure.code == "REMINDER_ALREADY_SCHEDULED"
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationRecord)) == 1


async def test_checkin_lock_defers_publication_then_closed_occurrence_is_cancelled(race_database):
    from app.models.medication_schedules import MedicationOccurrenceStatus

    factory, occurrence_id, user_id = race_database
    async with factory.begin() as session:
        await NotificationScheduler(NotificationRepository(session)).generate_once(now=NOW)
    async with factory.begin() as writer:
        occurrence = await MedicationCheckinRepository(writer).lock_occurrence_owned(
            occurrence_id=occurrence_id, user_id=user_id
        )
        assert occurrence is not None
        async with factory.begin() as publisher:
            result = await NotificationScheduler(NotificationRepository(publisher)).publish_once(now=NOW)
            assert result.delivered_count == 0
        occurrence.status = MedicationOccurrenceStatus.CLOSED
    async with factory.begin() as publisher:
        result = await NotificationScheduler(NotificationRepository(publisher)).publish_once(now=NOW)
        assert result.cancelled_count == 1 and result.delivered_count == 0
        notification = await publisher.scalar(select(NotificationRecord))
        assert notification is not None and notification.status == NotificationStatus.CANCELLED


async def test_concurrent_one_shot_commands_publish_only_once(race_database):
    from app.commands.process_notifications import process_notifications_once

    factory, _, _ = race_database
    results = await asyncio.wait_for(
        asyncio.gather(
            process_notifications_once(now=NOW, session_factory=factory),
            process_notifications_once(now=NOW, session_factory=factory),
        ),
        timeout=10,
    )
    assert sum(result.created_count for result in results) == 1
    assert sum(result.delivered_count for result in results) == 1
    assert (await process_notifications_once(now=NOW, session_factory=factory)).delivered_count == 0
    async with factory() as session:
        records = (await session.scalars(select(NotificationRecord))).all()
        assert len(records) == 1 and records[0].attempt == 1
