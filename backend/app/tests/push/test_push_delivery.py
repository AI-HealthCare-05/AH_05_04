from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models.medication_schedules import MedicationCheckin, MedicationCheckinStatus, MedicationOccurrenceStatus
from app.models.notifications import NotificationRecord
from app.models.push import PushDelivery, PushSubscription
from app.repositories.notification_repository import NotificationRepository
from app.repositories.push_repository import PushRepository
from app.services.notifications import NotificationScheduler
from app.services.push import PushDeliveryService
from app.services.push_transport import PushSendResult
from app.tests.push.conftest import NOW, register
from app.tests.repositories.test_medication_checkin_repository_integration import _create_occurrence


@pytest.fixture
async def delivery(case, db_session, subscription_request):
    registered = await register(case, subscription_request)
    occurrence = await _create_occurrence(
        db_session, owner=case.owner, profile=case.profile, deadline_at=NOW + timedelta(hours=4)
    )
    scheduler = NotificationScheduler(NotificationRepository(db_session))
    await scheduler.generate_once(now=NOW)
    await scheduler.publish_once(now=NOW)
    repo = PushRepository(db_session)
    assert await repo.generate(NOW, 100) == 1
    row = await db_session.scalar(select(PushDelivery))
    assert row.subscription_id == UUID(registered["id"])
    return row, occurrence, PushDeliveryService(repo)


async def test_duplicate_generation_claim_and_result_leave_medication_unchanged(delivery, db_session):
    row, occurrence, service = delivery
    notification = await db_session.get(NotificationRecord, row.notification_id)
    before = (
        notification.status,
        notification.attempt,
        notification.delivered_at,
        notification.read_at,
        occurrence.status,
    )
    assert await service.repository.generate(NOW, 100) == 0
    claim = await service.prepare(row.id, clock=lambda: NOW)
    assert claim is not None
    assert await service.prepare(row.id, clock=lambda: NOW) is None
    await service.finish(claim, PushSendResult("ACCEPTED"), NOW)
    await service.finish(claim, PushSendResult("FAILED", "PROVIDER_REJECTED"), NOW)
    assert row.status == "ACCEPTED" and row.attempt_count == 1
    assert before == (
        notification.status,
        notification.attempt,
        notification.delivered_at,
        notification.read_at,
        occurrence.status,
    )
    assert set(claim.payload) == {"title", "body", "notification_id", "generation"}


@pytest.mark.parametrize(
    "state", ["cancel", "close", "checkin", "logout", "withdraw", "revoke", "deadline", "generation"]
)
async def test_pre_send_rechecks_block_stale_delivery(delivery, db_session, case, state):
    row, occurrence, service = delivery
    subscription = await db_session.get(PushSubscription, row.subscription_id)
    now = NOW
    if state == "cancel":
        occurrence.status = MedicationOccurrenceStatus.CANCELLED
    elif state == "close":
        occurrence.status = MedicationOccurrenceStatus.CLOSED
    elif state == "checkin":
        db_session.add(
            MedicationCheckin(occurrence_id=occurrence.id, status=MedicationCheckinStatus.NOT_TAKEN, revision=1)
        )
    elif state == "logout":
        case.owner.token_version += 1
    elif state == "withdraw":
        case.owner.is_active = False
    elif state == "revoke":
        await service.repository.revoke(subscription, NOW)
    elif state == "generation":
        from uuid import uuid4

        subscription.generation = uuid4()
    else:
        now = NOW + timedelta(hours=4)
    await db_session.flush()
    assert await service.prepare(row.id, clock=lambda: now) is None
    assert row.status == "CANCELLED" and row.attempt_count == 0


async def test_new_subscription_does_not_receive_old_notifications(delivery, db_session, case):
    row, _, service = delivery
    subscription = await db_session.get(PushSubscription, row.subscription_id)
    from uuid import uuid4

    subscription.generation = uuid4()
    subscription.activated_at = NOW + timedelta(seconds=1)
    await db_session.flush()
    assert await service.repository.generate(NOW + timedelta(seconds=2), 100) == 0


async def test_retry_backoff_exhaustion(delivery):
    row, _, service = delivery
    instant = NOW
    for attempt, delay in [(1, 30), (2, 120), (3, 120)]:
        claim = await service.prepare(row.id, clock=lambda instant=instant: instant)
        assert claim is not None
        await service.finish(claim, PushSendResult("PENDING", "PROVIDER_RETRY"), instant)
        assert row.attempt_count == attempt
        if attempt < 3:
            assert row.next_attempt_at == instant + timedelta(seconds=delay)
            assert await service.prepare(row.id, clock=lambda instant=instant: instant) is None
        instant += timedelta(seconds=delay)
    assert row.status == "FAILED" and row.failure_reason == "RETRY_EXHAUSTED"


async def test_retry_after_past_expiry_is_terminal(delivery):
    row, _, service = delivery
    claim = await service.prepare(row.id, clock=lambda: NOW)
    await service.finish(claim, PushSendResult("PENDING", "PROVIDER_RETRY", 600), NOW)
    assert row.status == "FAILED"


@pytest.mark.parametrize("reason", ["timeout", "crash"])
async def test_unknown_is_never_retried(delivery, reason):
    row, _, service = delivery
    claim = await service.prepare(row.id, clock=lambda: NOW)
    if reason == "timeout":
        await service.finish(claim, PushSendResult("UNKNOWN", "TRANSPORT_UNKNOWN"), NOW)
    else:
        await service.repository.cleanup(NOW + timedelta(seconds=31), 100)
        # Late success cannot overwrite reclaimed UNKNOWN.
        await service.finish(claim, PushSendResult("ACCEPTED"), NOW + timedelta(seconds=32))
    assert row.status == "UNKNOWN"
    assert await service.prepare(row.id, clock=lambda: NOW + timedelta(seconds=40)) is None


async def test_expired_subscription_scrubs_ciphertext(delivery, db_session):
    row, _, service = delivery
    claim = await service.prepare(row.id, clock=lambda: NOW)
    await service.finish(claim, PushSendResult("FAILED", "SUBSCRIPTION_EXPIRED"), NOW)
    subscription = await db_session.get(PushSubscription, row.subscription_id)
    assert subscription.revoked_at == NOW and subscription.ciphertext is None


async def test_late_expiry_cannot_revoke_new_generation(delivery, case, db_session, subscription_request):
    row, _, service = delivery
    claim = await service.prepare(row.id, clock=lambda: NOW)
    await case.service.revoke(case.owner.id, case.owner.token_version, row.subscription_id)
    await register(case, subscription_request)
    await service.finish(claim, PushSendResult("FAILED", "SUBSCRIPTION_EXPIRED"), NOW)
    subscription = await db_session.get(PushSubscription, row.subscription_id)
    assert subscription.revoked_at is None and subscription.ciphertext is not None


async def test_invalid_sessions_are_scrubbed_and_retention_is_bounded(delivery, db_session, case):
    row, _, service = delivery
    case.owner.token_version += 1
    await db_session.flush()
    await service.revoke_invalid(NOW, 100)
    subscription = await db_session.get(PushSubscription, row.subscription_id)
    assert subscription.ciphertext is None
    assert row.status == "CANCELLED"
    await service.repository.cleanup(NOW + timedelta(days=8), 100)
    await service.repository.purge_subscriptions(NOW + timedelta(days=8), 100)
    assert list(await db_session.scalars(select(PushSubscription))) == []


async def test_auth_invalidation_erases_keys_in_same_transaction(delivery, db_session, case):
    from app.repositories.user_repository import UserRepository

    row, _, _ = delivery
    await UserRepository(db_session).increment_token_version(case.owner)
    subscription = await db_session.get(PushSubscription, row.subscription_id)
    assert subscription.revoked_at is not None and subscription.ciphertext is None
    assert row.status == "CANCELLED"


async def test_late_result_after_lease_expiry_is_unknown_without_cleanup(delivery):
    row, _, service = delivery
    claim = await service.prepare(row.id, clock=lambda: NOW)
    await service.finish(claim, PushSendResult("ACCEPTED"), NOW + timedelta(seconds=30))
    assert row.status == "UNKNOWN" and row.accepted_at is None
