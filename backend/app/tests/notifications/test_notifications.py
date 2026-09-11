from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.security import get_request_user
from app.dependencies.services import get_notification_service
from app.main import app, fastapi_app
from app.models.async_jobs import IdempotencyRecord
from app.models.medication_schedules import MedicationOccurrence, MedicationOccurrenceStatus
from app.models.notifications import NotificationRecord, NotificationStatus
from app.repositories.idempotency_repository import IdempotencyRepository
from app.repositories.medication_schedule_repository import MedicationScheduleRepository
from app.repositories.notification_repository import NotificationRepository
from app.services import idempotency
from app.services.idempotency import SyncMutationIdempotencyService, get_default_snapshot_cipher
from app.services.medication_occurrences import PrescriptionVersionMedicationInvalidationService
from app.services.notifications import NotificationScheduler, NotificationService
from app.tests.repositories.test_medication_checkin_repository_integration import _create_occurrence
from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile

NOW = datetime(2026, 9, 10, 14, tzinfo=UTC)  # KST 23:00; reminder crosses local midnight.


@dataclass
class Case:
    session: AsyncSession
    client: AsyncClient
    occurrence: MedicationOccurrence
    repository: NotificationRepository
    scheduler: NotificationScheduler
    user: SimpleNamespace

    async def reminder(self, *, at: datetime = NOW + timedelta(hours=2), key: str = "reminder-test-key"):
        return await self.client.post(
            f"/api/v1/medication-occurrences/{self.occurrence.id}/reminders",
            json={"scheduled_at": at.isoformat()},
            headers={"Idempotency-Key": key},
        )

    async def read(self, notification_id, *, key="read-test-key-203", body=None):
        return await self.client.patch(
            f"/api/v1/notifications/{notification_id}/read",
            json={} if body is None else body,
            headers={"Idempotency-Key": key},
        )


@pytest.fixture
async def case(db_session: AsyncSession) -> AsyncIterator[Case]:
    owner, profile = await _create_user_with_self_profile(db_session, label="notification-owner")
    occurrence = await _create_occurrence(
        db_session, owner=owner, profile=profile, deadline_at=NOW + timedelta(hours=4)
    )
    occurrence.scheduled_local_date = NOW.date()
    await db_session.commit()
    user = SimpleNamespace(id=owner.id)
    repository = NotificationRepository(db_session)
    service = NotificationService(
        repository,
        SyncMutationIdempotencyService(IdempotencyRepository(db_session), get_default_snapshot_cipher()),
        clock=lambda: NOW,
    )
    fastapi_app.dependency_overrides[get_request_user] = lambda: user
    fastapi_app.dependency_overrides[get_notification_service] = lambda: service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield Case(db_session, client, occurrence, repository, NotificationScheduler(repository), user)
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)
        fastapi_app.dependency_overrides.pop(get_notification_service, None)


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert set(response.json()) == {"code", "message", "details", "trace_id"}
    assert response.headers["cache-control"] == "no-store"


async def test_generate_publish_read_replay_and_midnight_date(case: Case):
    first = await case.scheduler.generate_once(now=NOW)
    assert first.created_count == 1
    assert (await case.scheduler.generate_once(now=NOW)).created_count == 0
    assert (await case.client.get("/api/v1/notifications")).json()["data"]["items"] == []
    assert (await case.scheduler.publish_once(now=NOW)).delivered_count == 1
    assert (await case.scheduler.publish_once(now=NOW)).delivered_count == 0
    reminder = await case.reminder()
    assert reminder.status_code == 201, reminder.text
    replay = await case.reminder()
    assert replay.json() == reminder.json()
    assert (await case.scheduler.publish_once(now=NOW + timedelta(hours=2))).delivered_count == 1
    response = await case.client.get("/api/v1/notifications?limit=1")
    assert response.headers["cache-control"] == "no-store"
    page = response.json()["data"]
    assert page["next_offset"] == 1
    item = page["items"][0]
    assert item["kind"] == "REMINDER" and item["occurrence_local_date"] == "2026-09-10"
    assert item["read_at"] is None
    read = await case.read(item["id"])
    assert read.status_code == 200, read.text
    assert read.json()["data"]["read_at"] is not None
    assert (await case.read(item["id"])).json() == read.json()
    assert (await case.read(item["id"], key="another-read-key")).json() == read.json()
    assert (await case.client.get("/api/v1/notifications?offset=1&limit=1")).json()["data"]["next_offset"] is None
    # Original response is replayed even after delivery; no new notification.
    assert (await case.reminder()).json() == reminder.json()
    error(await case.reminder(key="new-reminder-key"), 409, "REMINDER_LIMIT_REACHED")
    assert await case.session.scalar(select(func.count()).select_from(NotificationRecord)) == 2


async def test_ownership_and_missing_pending_resources_are_hidden(case: Case):
    own = await case.reminder()
    notification_id = own.json()["data"]["id"]
    error(await case.read(notification_id), 404, "NOTIFICATION_NOT_FOUND")
    await case.scheduler.publish_once(now=NOW + timedelta(hours=2))
    intruder, _ = await _create_user_with_self_profile(case.session, label="notification-intruder")
    await case.session.commit()
    case.user.id = intruder.id
    assert (await case.client.get("/api/v1/notifications")).json()["data"]["items"] == []
    error(await case.read(notification_id), 404, "NOTIFICATION_NOT_FOUND")
    error(await case.read(uuid4()), 404, "NOTIFICATION_NOT_FOUND")
    error(await case.reminder(), 404, "MEDICATION_OCCURRENCE_NOT_FOUND")


@pytest.mark.parametrize("state", [MedicationOccurrenceStatus.CLOSED, MedicationOccurrenceStatus.CANCELLED])
async def test_closed_cancelled_occurrences_cancel_pending_and_reject_reminders(case: Case, state):
    await case.scheduler.generate_once(now=NOW)
    original = await case.reminder()
    case.occurrence.status = state
    await case.session.commit()
    result = await case.scheduler.publish_once(now=NOW)
    assert result.cancelled_count == 2 and result.delivered_count == 0
    assert (await case.reminder()).json() == original.json()
    code = "OCCURRENCE_CANCELLED" if state == MedicationOccurrenceStatus.CANCELLED else "REMINDER_NOT_ALLOWED"
    error(await case.reminder(key="changed-reminder-key"), 409, code)
    assert (await case.client.get("/api/v1/notifications")).json()["data"]["items"] == []


async def test_deadline_cancels_undelivered_but_preserves_delivered_history(case: Case):
    await case.scheduler.generate_once(now=NOW)
    await case.scheduler.publish_once(now=NOW)
    await case.reminder()
    published = await case.scheduler.publish_once(now=NOW + timedelta(hours=4))
    assert published.cancelled_count == 1 and published.delivered_count == 0
    items = (await case.client.get("/api/v1/notifications")).json()["data"]["items"]
    assert len(items) == 1 and items[0]["kind"] == "SCHEDULED"


@pytest.mark.parametrize("hours", [0, -1, 4, 5])
async def test_reminder_time_bounds_do_not_store_mutation(case: Case, hours):
    error(await case.reminder(at=NOW + timedelta(hours=hours)), 422, "VALIDATION_FAILED")
    assert await case.session.scalar(select(func.count()).select_from(NotificationRecord)) == 0
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


async def test_reminder_duplicate_fingerprint_and_snapshot_rollback(case: Case, monkeypatch):
    monkeypatch.setattr(idempotency, "SNAPSHOT_SIZE_CAP_BYTES", 1)
    error(await case.reminder(), 503, "IDEMPOTENCY_RESPONSE_TOO_LARGE")
    assert await case.session.scalar(select(func.count()).select_from(NotificationRecord)) == 0
    monkeypatch.setattr(idempotency, "SNAPSHOT_SIZE_CAP_BYTES", 1024 * 1024)
    response = await case.reminder()
    assert response.status_code == 201
    error(await case.reminder(at=NOW + timedelta(hours=3)), 409, "IDEMPOTENCY_KEY_CONFLICT")
    error(await case.reminder(key="other-reminder-key"), 409, "REMINDER_ALREADY_SCHEDULED")
    record = await case.session.scalar(select(IdempotencyRecord))
    assert record is not None
    assert record.response_body_snapshot and str(case.occurrence.id).encode() not in record.response_body_snapshot


async def test_version_cancellation_uses_same_transaction_and_can_roll_back(case: Case):
    await case.scheduler.generate_once(now=NOW)
    await case.session.commit()
    schedule_repo = MedicationScheduleRepository(case.session)
    from app.models.medication_schedules import MedicationSchedule
    from app.models.prescriptions import PrescriptionVersionMedication

    version_id = await case.session.scalar(
        select(PrescriptionVersionMedication.prescription_version_id)
        .join(
            MedicationSchedule,
            MedicationSchedule.prescription_version_medication_id == PrescriptionVersionMedication.id,
        )
        .where(MedicationSchedule.id == case.occurrence.medication_schedule_id)
    )
    assert version_id is not None
    service = PrescriptionVersionMedicationInvalidationService(schedule_repo, notification_cancellation=case.repository)
    with pytest.raises(RuntimeError):
        async with case.session.begin_nested():
            ids = await service.cancel_future_for_prescription_version(
                prescription_version_id=version_id, effective_at=NOW
            )
            assert tuple(ids) == (case.occurrence.id,)
            raise RuntimeError("synthetic rollback")
    await case.session.refresh(case.occurrence)
    notification = await case.session.scalar(select(NotificationRecord))
    assert notification is not None
    await case.session.refresh(notification)
    assert case.occurrence.status == MedicationOccurrenceStatus.PENDING
    assert notification.status == NotificationStatus.PENDING
    await service.cancel_future_for_prescription_version(prescription_version_id=version_id, effective_at=NOW)
    assert notification.status == NotificationStatus.CANCELLED


@pytest.mark.parametrize(
    "body",
    [
        {"scheduled_at": "2026-09-10T16:00:00"},
        {"scheduled_at": 123},
        {"scheduled_at": "2026-09-10T16:00:00Z", "extra": 1},
        {},
    ],
)
async def test_validation(case: Case, body):
    response = await case.client.post(
        f"/api/v1/medication-occurrences/{case.occurrence.id}/reminders",
        json=body,
        headers={"Idempotency-Key": "test-key"},
    )
    error(response, 422, "VALIDATION_FAILED")


async def test_header_pagination_read_body_and_openapi(case: Case):
    response = await case.client.post(
        f"/api/v1/medication-occurrences/{case.occurrence.id}/reminders",
        json={"scheduled_at": (NOW + timedelta(hours=2)).isoformat()},
    )
    assert response.status_code == 400
    for url in ["/api/v1/notifications?limit=0", "/api/v1/notifications?limit=101", "/api/v1/notifications?offset=-1"]:
        error(await case.client.get(url), 422, "VALIDATION_FAILED")
    error(await case.read(uuid4(), body={"read_at": NOW.isoformat()}), 422, "VALIDATION_FAILED")
    schema = fastapi_app.openapi()
    path = schema["paths"]["/api/v1/medication-occurrences/{occurrence_id}/reminders"]["post"]
    assert path["operationId"] == "medication-reminder.create"
    assert any(p["name"] == "Idempotency-Key" and p["required"] for p in path["parameters"])
    assert "201" in path["responses"]
    assert "occurrence_local_date" in schema["components"]["schemas"]["NotificationData"]["required"]


@pytest.mark.parametrize("status", ["TAKEN", "NOT_TAKEN"])
async def test_real_checkin_then_correction_never_reopens_pending_notification(case: Case, status):
    from app.models.medication_schedules import MedicationCheckinStatus
    from app.repositories.medication_checkin_repository import MedicationCheckinRepository
    from app.services.medication_checkins import MedicationCheckinService, NoopCheckinRevisionInvalidation

    await case.scheduler.generate_once(now=NOW)
    await case.reminder()
    checkins = MedicationCheckinService(
        MedicationCheckinRepository(case.session), revision_invalidation=NoopCheckinRevisionInvalidation()
    )
    await checkins.put_owned(
        user_id=case.user.id,
        occurrence_id=case.occurrence.id,
        status=MedicationCheckinStatus(status),
        taken_at=None,
        expected_revision=0,
    )
    assert (await case.scheduler.publish_once(now=NOW)).cancelled_count == 2
    await checkins.put_owned(
        user_id=case.user.id,
        occurrence_id=case.occurrence.id,
        status=MedicationCheckinStatus.TAKEN,
        taken_at=None,
        expected_revision=1,
    )
    assert (await case.scheduler.generate_once(now=NOW)).created_count == 0
    assert (await case.scheduler.publish_once(now=NOW)).delivered_count == 0


async def test_expired_idempotency_record_cannot_bypass_lifetime_reminder_limit(case: Case):
    assert (await case.reminder()).status_code == 201
    record = await case.session.scalar(select(IdempotencyRecord))
    assert record is not None
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await case.session.commit()
    await case.scheduler.publish_once(now=NOW + timedelta(hours=2))
    error(await case.reminder(), 409, "REMINDER_LIMIT_REACHED")


async def test_notification_dependencies_share_the_invalidation_transaction(case: Case):
    from app.dependencies.services import get_prescription_version_medication_invalidation_service

    service = get_prescription_version_medication_invalidation_service(MedicationScheduleRepository(case.session))
    assert isinstance(service._notification_cancellation, NotificationRepository)
    assert service._notification_cancellation.session is case.session


async def test_read_snapshot_failure_rolls_back_read_at(case: Case, monkeypatch):
    await case.scheduler.generate_once(now=NOW)
    await case.scheduler.publish_once(now=NOW)
    response = await case.client.get("/api/v1/notifications")
    notification_id = response.json()["data"]["items"][0]["id"]
    monkeypatch.setattr(idempotency, "SNAPSHOT_SIZE_CAP_BYTES", 1)
    error(await case.read(notification_id), 503, "IDEMPOTENCY_RESPONSE_TOO_LARGE")
    assert (await case.client.get("/api/v1/notifications")).json()["data"]["items"][0]["read_at"] is None


async def test_notifications_require_authentication(case: Case):
    fastapi_app.dependency_overrides.pop(get_request_user)
    assert (await case.client.get("/api/v1/notifications")).status_code == 401


async def test_deadline_scheduler_closes_then_notification_scheduler_cancels(case: Case):
    from app.repositories.medication_checkin_repository import MedicationCheckinRepository
    from app.services.medication_checkins import MedicationCheckinDeadlineScheduler

    await case.scheduler.generate_once(now=NOW)
    result = await MedicationCheckinDeadlineScheduler(MedicationCheckinRepository(case.session)).generate_unconfirmed(
        now=NOW + timedelta(hours=4)
    )
    assert result.processed_count == 1
    published = await case.scheduler.publish_once(now=NOW + timedelta(hours=4))
    assert published.cancelled_count == 1 and published.delivered_count == 0
