import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config
from app.core.db.databases import Base
from app.models.medication_schedules import MedicationOccurrence
from app.models.push import PushDelivery, PushSubscription
from app.models.user_consents import ConsentPurpose, ConsentStatus
from app.repositories.notification_repository import NotificationRepository
from app.repositories.push_repository import PushRepository
from app.repositories.user_consent_repository import UserConsentRepository
from app.services.notifications import NotificationScheduler
from app.services.push import PushDeliveryService, PushSubscriptionService
from app.services.push_transport import PushSendResult
from app.services.user_consent_policy import current_consent_policy_version
from app.tests.db_extensions import EXTENSION_SCHEMA, ensure_trigram_extension
from app.tests.push.conftest import NOW
from app.tests.repositories.test_medication_checkin_repository_integration import _create_occurrence
from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile


@pytest.fixture
async def push_database(push_settings, subscription_request, monkeypatch):
    schema = "push_race_" + uuid4().hex
    admin = create_async_engine(config.database_url, poolclass=NullPool)
    engine = create_async_engine(
        config.database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},{EXTENSION_SCHEMA}"}},
    )
    monkeypatch.setattr("app.services.push.resolve_endpoint", lambda *args: ("push.example.test", "8.8.8.8"))
    try:
        async with admin.begin() as connection:
            await ensure_trigram_extension(connection, EXTENSION_SCHEMA)
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as session:
            user, profile = await _create_user_with_self_profile(session, label="push-race")
            occurrence = await _create_occurrence(
                session, owner=user, profile=profile, deadline_at=NOW + timedelta(hours=4)
            )
            await UserConsentRepository(session).set_status(
                user_id=user.id,
                purpose=ConsentPurpose.NOTIFICATION,
                status=ConsentStatus.GRANTED,
                policy_version=current_consent_policy_version(ConsentPurpose.NOTIFICATION),
                changed_at=NOW,
            )
        yield factory, user.id, user.token_version, occurrence.id
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await admin.dispose()


async def seed_delivery(factory, user_id, version, settings, request):
    async with factory.begin() as session:
        repo = PushRepository(session)
        await PushSubscriptionService(repo, settings, clock=lambda: NOW).upsert(user_id, version, request)
        scheduler = NotificationScheduler(NotificationRepository(session))
        await scheduler.generate_once(now=NOW)
        await scheduler.publish_once(now=NOW)
        await repo.generate(NOW, 100)
        return await session.scalar(select(PushDelivery.id))


def freeze_push_command(monkeypatch):
    from app.commands import process_push
    from app.services import push

    class FrozenTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(process_push, "datetime", FrozenTime)
    monkeypatch.setattr(push, "datetime", FrozenTime)
    sender = Mock(return_value=PushSendResult("ACCEPTED"))
    monkeypatch.setattr(process_push, "send_push", sender)
    return process_push, sender


async def test_concurrent_registration_returns_one_generation(push_database, push_settings, subscription_request):
    factory, user_id, version, _ = push_database
    barrier = asyncio.Barrier(2)

    async def register():
        async with factory.begin() as session:
            await barrier.wait()
            return await PushSubscriptionService(PushRepository(session), push_settings, clock=lambda: NOW).upsert(
                user_id, version, subscription_request
            )

    first, second = await asyncio.wait_for(asyncio.gather(register(), register()), timeout=10)
    assert first == second
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(PushSubscription)) == 1


async def test_concurrent_dispatchers_only_one_claim(push_database, push_settings, subscription_request):
    factory, user_id, version, _ = push_database
    delivery_id = await seed_delivery(factory, user_id, version, push_settings, subscription_request)
    barrier = asyncio.Barrier(2)

    async def prepare():
        async with factory.begin() as session:
            await barrier.wait()
            return await PushDeliveryService(PushRepository(session)).prepare(delivery_id, clock=lambda: NOW)

    claims = await asyncio.wait_for(asyncio.gather(prepare(), prepare()), timeout=10)
    assert sum(claim is not None for claim in claims) == 1


@pytest.mark.parametrize("cancelled", [True, False])
async def test_occurrence_lock_rechecks_state_and_time_after_wait(
    push_database, push_settings, subscription_request, cancelled
):
    factory, user_id, version, occurrence_id = push_database
    delivery_id = await seed_delivery(factory, user_id, version, push_settings, subscription_request)
    started = asyncio.Event()
    instant = [NOW]

    async def prepare():
        async with factory.begin() as session:
            started.set()
            return await PushDeliveryService(PushRepository(session)).prepare(delivery_id, clock=lambda: instant[0])

    async with factory.begin() as writer:
        occurrence = await writer.scalar(
            select(MedicationOccurrence).where(MedicationOccurrence.id == occurrence_id).with_for_update()
        )
        task = asyncio.create_task(prepare())
        await started.wait()
        if cancelled:
            occurrence.status = "CANCELLED"
        else:
            instant[0] = NOW + timedelta(seconds=301)
    assert await asyncio.wait_for(task, timeout=10) is None
    async with factory() as session:
        delivery = await session.get(PushDelivery, delivery_id)
        assert delivery.status == "CANCELLED" and delivery.attempt_count == 0


async def test_revoke_during_http_does_not_hold_medication_locks(push_database, push_settings, subscription_request):
    factory, user_id, version, _ = push_database
    delivery_id = await seed_delivery(factory, user_id, version, push_settings, subscription_request)
    async with factory.begin() as session:
        claim = await PushDeliveryService(PushRepository(session)).prepare(delivery_id, clock=lambda: NOW)
    # The provider request is outside the claim transaction; revoke can commit.
    async with factory.begin() as session:
        await PushSubscriptionService(PushRepository(session), push_settings, clock=lambda: NOW).revoke(
            user_id, version, claim.subscription_id
        )
    async with factory.begin() as session:
        await PushDeliveryService(PushRepository(session)).finish(
            claim, PushSendResult("PENDING", "PROVIDER_RETRY"), NOW
        )
        assert (await session.get(PushDelivery, delivery_id)).status == "CANCELLED"


async def test_one_shot_command_does_not_send_without_notification_consent_row(
    push_database, push_settings, subscription_request, monkeypatch
):
    process_push, sender = freeze_push_command(monkeypatch)
    factory, user_id, version, _ = push_database
    delivery_id = await seed_delivery(factory, user_id, version, push_settings, subscription_request)
    async with factory.begin() as session:
        row = await UserConsentRepository(session).get_current(user_id=user_id, purpose=ConsentPurpose.NOTIFICATION)
        assert row is not None
        await session.delete(row)

    assert await process_push.process_push_once(settings=push_settings, session_factory=factory) == 0
    sender.assert_not_called()
    async with factory() as session:
        delivery = await session.get(PushDelivery, delivery_id)
        assert delivery.status == "CANCELLED"
        assert delivery.failure_reason == "NO_LONGER_ELIGIBLE"
        assert delivery.attempt_count == 0


async def test_production_enabled_flag_still_requires_notification_consent(
    push_database, push_settings, subscription_request, monkeypatch
):
    process_push, sender = freeze_push_command(monkeypatch)
    push_settings.production_enabled = True
    monkeypatch.setattr(config, "ENV", "production")
    factory, user_id, version, _ = push_database
    delivery_id = await seed_delivery(factory, user_id, version, push_settings, subscription_request)
    async with factory.begin() as session:
        row = await UserConsentRepository(session).get_current(user_id=user_id, purpose=ConsentPurpose.NOTIFICATION)
        assert row is not None
        await session.delete(row)

    assert await process_push.process_push_once(settings=push_settings, session_factory=factory) == 0
    sender.assert_not_called()
    async with factory() as session:
        delivery = await session.get(PushDelivery, delivery_id)
        assert delivery.status == "CANCELLED"
        assert delivery.failure_reason == "NO_LONGER_ELIGIBLE"
        assert delivery.attempt_count == 0


async def test_one_shot_command_does_not_send_when_notification_policy_unavailable(
    push_database, push_settings, subscription_request, monkeypatch
):
    from app.services import user_consent_policy

    process_push, sender = freeze_push_command(monkeypatch)
    monkeypatch.setitem(user_consent_policy.STATIC_CONSENT_POLICY_VERSIONS, ConsentPurpose.NOTIFICATION, "")
    factory, user_id, version, _ = push_database
    delivery_id = await seed_delivery(factory, user_id, version, push_settings, subscription_request)

    assert await process_push.process_push_once(settings=push_settings, session_factory=factory) == 0
    sender.assert_not_called()
    async with factory() as session:
        delivery = await session.get(PushDelivery, delivery_id)
        assert delivery.status == "CANCELLED"
        assert delivery.failure_reason == "NO_LONGER_ELIGIBLE"
        assert delivery.attempt_count == 0


async def test_one_shot_command_does_not_send_without_notification_consent(
    push_database, push_settings, subscription_request, monkeypatch
):
    process_push, sender = freeze_push_command(monkeypatch)
    factory, user_id, version, _ = push_database
    delivery_id = await seed_delivery(factory, user_id, version, push_settings, subscription_request)
    async with factory.begin() as session:
        row = await UserConsentRepository(session).get_current(user_id=user_id, purpose=ConsentPurpose.NOTIFICATION)
        assert row is not None
        row.status = ConsentStatus.WITHDRAWN
        row.withdrawn_at = NOW
        row.granted_at = None

    assert await process_push.process_push_once(settings=push_settings, session_factory=factory) == 0
    sender.assert_not_called()
    async with factory() as session:
        delivery = await session.get(PushDelivery, delivery_id)
        assert delivery.status == "CANCELLED"
        assert delivery.failure_reason == "NO_LONGER_ELIGIBLE"
        assert delivery.attempt_count == 0


async def test_one_shot_command_sends_once_with_committed_ledger(
    push_database, push_settings, subscription_request, monkeypatch
):
    process_push, sender = freeze_push_command(monkeypatch)
    factory, user_id, version, _ = push_database
    await seed_delivery(factory, user_id, version, push_settings, subscription_request)
    assert await process_push.process_push_once(settings=push_settings, session_factory=factory) == 1
    assert await process_push.process_push_once(settings=push_settings, session_factory=factory) == 0
    assert sender.call_count == 1
