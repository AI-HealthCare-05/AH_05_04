from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.security import get_request_user
from app.dtos.medication_schedules import MedicationDayResponse, MedicationScheduleResponse
from app.main import app, fastapi_app
from app.models.async_jobs import IdempotencyRecord
from app.models.medication_schedules import (
    MedicationOccurrence,
    MedicationSchedule,
    MedicationScheduleAudit,
    MedicationScheduleTime,
)
from app.models.notifications import NotificationKind, NotificationRecord, NotificationStatus
from app.repositories.notification_repository import NotificationRepository
from app.services import idempotency
from app.tests.medication_checkins.test_medication_checkin_api import assert_error
from app.tests.repositories.test_medication_schedule_repository_integration import (
    _create_active_version_medication,
    _create_user_with_self_profile,
)


@dataclass
class Case:
    client: AsyncClient
    session: AsyncSession
    owner_id: UUID
    medication_id: UUID
    day: date

    @property
    def body(self) -> dict:
        return {
            "start_local_date": self.day.isoformat(),
            "end_mode": "OPEN_ENDED",
            "local_times": ["09:00"],
            "expected_revision": 0,
        }

    async def write(
        self,
        body: dict,
        *,
        method: str = "PUT",
        key: str | None = "schedule-first-key",
        medication_id: UUID | None = None,
    ) -> Response:
        return await self.client.request(
            method,
            f"/api/v1/prescription-version-medications/{medication_id or self.medication_id}/schedule",
            json=body,
            headers={"Idempotency-Key": key} if key else {},
        )

    async def read(self) -> Response:
        return await self.client.get("/api/v1/medication-occurrences", params={"date": self.day.isoformat()})


@pytest.fixture
async def case(db_session: AsyncSession) -> AsyncIterator[Case]:
    owner, profile = await _create_user_with_self_profile(db_session, label="schedule-api")
    _, medication = await _create_active_version_medication(db_session, owner=owner, profile=profile)
    await db_session.commit()
    authenticated = SimpleNamespace(id=owner.id)
    fastapi_app.dependency_overrides[get_request_user] = lambda: authenticated
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            yield Case(client, db_session, owner.id, medication.id, datetime.now(UTC).date() + timedelta(days=2))
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)


async def counts(case: Case) -> list[int | None]:
    return [
        await case.session.scalar(select(func.count()).select_from(model))
        for model in (
            MedicationSchedule,
            MedicationScheduleTime,
            MedicationOccurrence,
            MedicationScheduleAudit,
            IdempotencyRecord,
        )
    ]


async def test_create_update_cancel_reactivate_and_replay(case: Case) -> None:
    before = await case.read()
    assert before.status_code == 200
    assert before.json()["data"]["schedule_status"] == "SETUP_REQUIRED"
    assert before.json()["data"]["schedule_items"][0]["setup_reason"] == "MISSING_START_DATE"
    first = await case.write(case.body)
    assert first.status_code == 200, first.text
    assert MedicationScheduleResponse.model_validate(first.json()).data.revision == 1
    schedule_id = first.json()["data"]["schedule_id"]
    second = await case.write({**case.body, "expected_revision": 1}, key="schedule-second-key")
    assert second.status_code == 200, second.text
    assert second.json()["data"]["revision"] == 2
    replay = await case.write(case.body)
    assert replay.json() == first.json()
    assert_error(await case.write(case.body, key="schedule-stale-key"), 409, "SCHEDULE_REVISION_CONFLICT")
    assert_error(await case.write({**case.body, "local_times": ["10:00"]}), 409, "IDEMPOTENCY_KEY_CONFLICT")
    cancelled = await case.write(
        {"status": "CANCELLED", "expected_revision": 2}, method="PATCH", key="schedule-cancel-key"
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["revision"] == 3
    noop = await case.write({"status": "CANCELLED", "expected_revision": 3}, method="PATCH", key="schedule-repeat-key")
    assert noop.json() == cancelled.json()
    inactive = await case.read()
    assert inactive.json()["data"]["schedule_status"] == "INACTIVE"
    assert inactive.json()["data"]["schedule_items"][0]["setup_reason"] is None
    reactivated = await case.write({**case.body, "expected_revision": 3}, key="schedule-reactivate-key")
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["data"]["schedule_id"] == schedule_id
    assert reactivated.json()["data"]["revision"] == 4
    assert await case.session.scalar(select(func.count()).select_from(MedicationScheduleAudit)) == 4
    day = await case.read()
    parsed = MedicationDayResponse.model_validate(day.json()).data
    assert parsed.schedule_status == "READY"
    assert parsed.schedule_items[0].setup_reason is None
    assert any(o.status == "PENDING" and o.scheduled_local_date == case.day for o in parsed.occurrences)
    assert day.headers["cache-control"] == "no-store"
    assert day.headers["x-trace-id"]


@pytest.mark.parametrize(
    "delta",
    [
        {"local_times": []},
        {"local_times": ["09:00", "09:00"]},
        {"local_times": ["9:00"]},
        {"local_times": ["24:00"]},
        {"local_times": ["09:00:00"]},
        {"local_times": ["09:00", "10:00"]},
        {"expected_revision": True},
        {"expected_revision": "0"},
        {"expected_revision": -1},
        {"end_mode": "DATE"},
        {"end_local_date": "2000-01-01"},
        {"reason_code": "synthetic"},
    ],
)
async def test_invalid_request_has_no_storage(case: Case, delta: dict) -> None:
    response = await case.write({**case.body, **delta})
    assert_error(response, 422, "VALIDATION_FAILED")
    assert await counts(case) == [0, 0, 0, 0, 0]


async def test_patch_only_allows_cancel_and_key_required(case: Case) -> None:
    assert_error(
        await case.write({"status": "ENDED", "expected_revision": 0}, method="PATCH"), 422, "VALIDATION_FAILED"
    )
    assert_error(await case.write(case.body, key=None), 400, "IDEMPOTENCY_KEY_REQUIRED")
    assert_error(await case.write(case.body, key=" "), 400, "IDEMPOTENCY_KEY_INVALID")


async def seed_notifications(case: Case) -> list[UUID]:
    assert (await case.write(case.body)).status_code == 200
    occurrences = list(
        (await case.session.scalars(select(MedicationOccurrence).order_by(MedicationOccurrence.scheduled_at))).all()
    )
    ids = []
    for occurrence, status in zip(
        occurrences, (NotificationStatus.PENDING, NotificationStatus.DELIVERED), strict=False
    ):
        row = NotificationRecord(
            occurrence_id=occurrence.id,
            kind=NotificationKind.SCHEDULED,
            scheduled_at=occurrence.scheduled_at,
            status=status,
            attempt=1 if status == NotificationStatus.DELIVERED else 0,
            delivered_at=datetime.now(UTC) if status == NotificationStatus.DELIVERED else None,
        )
        case.session.add(row)
        await case.session.flush()
        ids.append(row.id)
    await case.session.commit()
    return ids


@pytest.mark.parametrize("method", ["PUT", "PATCH"])
async def test_real_adapter_cancels_only_undelivered(case: Case, method: str) -> None:
    ids = await seed_notifications(case)
    body = {**case.body, "expected_revision": 1} if method == "PUT" else {"status": "CANCELLED", "expected_revision": 1}
    response = await case.write(body, method=method, key="schedule-notification-key")
    assert response.status_code == 200, response.text
    case.session.expire_all()
    pending, delivered = [await case.session.get(NotificationRecord, row_id) for row_id in ids]
    assert pending is not None and pending.status == "CANCELLED" and pending.cancelled_at is not None
    assert delivered is not None and delivered.status == "DELIVERED" and delivered.read_at is None
    assert (await case.write(body, method=method, key="schedule-notification-key")).json() == response.json()


@pytest.mark.parametrize("failure", ["adapter", "snapshot", "audit", "generation"])
async def test_failure_rolls_back_every_table(case: Case, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    from app.repositories.medication_schedule_repository import MedicationScheduleRepository

    ids = await seed_notifications(case)
    before = await counts(case)
    if failure == "snapshot":
        monkeypatch.setattr(idempotency, "SNAPSHOT_SIZE_CAP_BYTES", 1)
    else:
        cls, name = (
            (NotificationRepository, "cancel_undelivered_for_occurrences")
            if failure == "adapter"
            else (MedicationScheduleRepository, "append_audit" if failure == "audit" else "create_occurrence_if_absent")
        )
        original = getattr(cls, name)

        async def fail_after_write(self, *args, **kwargs):
            await original(self, *args, **kwargs)
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(cls, name, fail_after_write)
    response = await case.write({**case.body, "expected_revision": 1}, key="schedule-rollback-key")
    assert response.status_code == (503 if failure == "snapshot" else 500), response.text
    case.session.expire_all()
    assert await counts(case) == before
    schedule = await case.session.scalar(select(MedicationSchedule))
    assert schedule is not None and schedule.revision == 1 and schedule.status == "ACTIVE"
    assert all(
        o.status == "PENDING" and o.cancelled_at is None
        for o in (await case.session.scalars(select(MedicationOccurrence))).all()
    )
    pending = await case.session.get(NotificationRecord, ids[0])
    assert pending is not None and pending.status == "PENDING" and pending.cancelled_at is None


async def test_other_user_has_empty_day_and_same_404_as_missing(case: Case) -> None:
    assert (await case.write(case.body)).status_code == 200
    other, _ = await _create_user_with_self_profile(case.session, label="schedule-intruder")
    await case.session.commit()
    authenticated = SimpleNamespace(id=other.id)
    fastapi_app.dependency_overrides[get_request_user] = lambda: authenticated
    for method in ("PUT", "PATCH"):
        body = case.body if method == "PUT" else {"status": "CANCELLED", "expected_revision": 1}
        for medication_id in (case.medication_id, uuid4()):
            assert_error(
                await case.write(body, method=method, medication_id=medication_id),
                404,
                "PRESCRIPTION_MEDICATION_NOT_FOUND",
            )
    response = await case.read()
    assert response.json()["data"] == {
        "schedule_status": "NO_ACTIVE_PRESCRIPTION",
        "schedule_items": [],
        "occurrences": [],
    }


def test_openapi_schedule_contract() -> None:
    schema = fastapi_app.openapi()
    path = schema["paths"]["/api/v1/prescription-version-medications/{prescription_version_medication_id}/schedule"]
    for method in ("put", "patch"):
        op = path[method]
        assert op["operationId"] == f"medication-schedule.{method}"
        assert next(p for p in op["parameters"] if p["name"] == "Idempotency-Key")["required"]
        for status in (400, 401, 404, 409, 422, 503):
            assert op["responses"][str(status)]["content"]["application/json"]["schema"]["$ref"].endswith(
                "/ErrorResponse"
            )
    put = schema["components"]["schemas"]["PutMedicationScheduleRequest"]
    assert set(put["required"]) == {"start_local_date", "end_mode", "local_times", "expected_revision"}
    assert put["additionalProperties"] is False
    assert "reason_code" not in put["properties"]
    query = schema["paths"]["/api/v1/medication-occurrences"]["get"]
    assert next(p for p in query["parameters"] if p["name"] == "date")["required"]


async def test_partial_keeps_ready_occurrences_and_current_checkin(case: Case) -> None:
    from app.models.profiles import Profile
    from app.models.users import User

    assert (await case.write(case.body)).status_code == 200
    owner = await case.session.get(User, case.owner_id)
    profile = await case.session.scalar(select(Profile).where(Profile.user_id == case.owner_id))
    assert owner is not None and profile is not None
    await _create_active_version_medication(case.session, owner=owner, profile=profile)
    await case.session.commit()
    day = (await case.read()).json()["data"]
    assert day["schedule_status"] == "PARTIAL"
    assert len(day["schedule_items"]) == 2
    occurrence = next(o for o in day["occurrences"] if o["status"] == "PENDING")
    response = await case.client.put(
        f"/api/v1/medication-occurrences/{occurrence['occurrence_id']}/check-in",
        json={"status": "TAKEN", "expected_revision": 0},
        headers={"Idempotency-Key": "schedule-checkin-key"},
    )
    assert response.status_code == 200, response.text
    row = next(
        o
        for o in (await case.read()).json()["data"]["occurrences"]
        if o["occurrence_id"] == occurrence["occurrence_id"]
    )
    assert row["checkin"] == response.json()["data"]
    assert row["status"] == "CLOSED"
    assert row["scheduled_local_date"] == case.day.isoformat()


async def test_inactive_keeps_past_pending_and_original_local_date(case: Case) -> None:
    from zoneinfo import ZoneInfo

    assert (await case.write(case.body)).status_code == 200
    occurrence = await case.session.scalar(select(MedicationOccurrence).order_by(MedicationOccurrence.scheduled_at))
    assert occurrence is not None
    now = datetime.now(UTC)
    past_day = (now - timedelta(minutes=1)).astimezone(ZoneInfo("Asia/Seoul")).date()
    occurrence.scheduled_at = now - timedelta(minutes=1)
    occurrence.scheduled_local_date = past_day
    occurrence.confirmation_deadline_at = now + timedelta(hours=4)
    occurrence_id = occurrence.id
    await case.session.commit()
    assert (
        await case.write(
            {"status": "CANCELLED", "expected_revision": 1}, method="PATCH", key="schedule-past-cancel-key"
        )
    ).status_code == 200
    response = await case.client.get("/api/v1/medication-occurrences", params={"date": past_day.isoformat()})
    data = response.json()["data"]
    assert data["schedule_status"] == "INACTIVE"
    row = next(o for o in data["occurrences"] if o["occurrence_id"] == str(occurrence_id))
    assert row["status"] == "PENDING" and row["scheduled_local_date"] == past_day.isoformat()


async def test_ended_patch_noop_and_put_explicitly_reactivates(case: Case) -> None:
    from app.models.medication_schedules import MedicationScheduleStatus

    assert (await case.write(case.body)).status_code == 200
    schedule = await case.session.scalar(select(MedicationSchedule))
    assert schedule is not None
    schedule.status = MedicationScheduleStatus.ENDED
    await case.session.commit()
    response = await case.write(
        {"status": "CANCELLED", "expected_revision": 1}, method="PATCH", key="schedule-ended-patch-key"
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ENDED"
    assert response.json()["data"]["revision"] == 1
    response = await case.write({**case.body, "expected_revision": 1}, key="ended-reactivate-key")
    assert response.json()["data"]["status"] == "ACTIVE"
    assert response.json()["data"]["revision"] == 2


async def test_null_frequency_date_and_normalized_time_order(case: Case) -> None:
    from app.models.prescriptions import PrescriptionVersion, PrescriptionVersionMedication
    from app.tests.fixtures.prescription_fingerprint import fingerprint_values

    medication = await case.session.get(PrescriptionVersionMedication, case.medication_id)
    assert medication is not None
    medication.frequency_per_day = None
    version = await case.session.get(PrescriptionVersion, medication.prescription_version_id)
    assert version is not None
    for field, value in fingerprint_values(
        version.prescribed_date,
        [{"medication_name": medication.medication_name, "frequency_per_day": None, "display_order": 1}],
    ).items():
        setattr(version, field, value)
    await case.session.commit()
    body = {**case.body, "end_mode": "DATE", "end_local_date": case.day.isoformat(), "local_times": ["23:00", "09:00"]}
    response = await case.write(body)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["local_times"] == ["09:00", "23:00"]
    assert (await case.write({**body, "local_times": ["09:00", "23:00"]})).json() == response.json()
    occurrences = (await case.read()).json()["data"]["occurrences"]
    assert len(occurrences) == 2
    late = next(o for o in occurrences if o["scheduled_at"].endswith("14:00:00Z"))
    assert late["confirmation_deadline_at"].endswith("18:00:00Z")


async def test_read_validation_and_authentication(case: Case) -> None:
    for params in ({}, {"date": "2026-99-99"}):
        assert_error(await case.client.get("/api/v1/medication-occurrences", params=params), 422, "VALIDATION_FAILED")
    fastapi_app.dependency_overrides.pop(get_request_user)
    assert (await case.read()).status_code == 401
    assert (await case.write(case.body)).status_code == 401


def test_frontend_synthetic_fixtures_match_dtos() -> None:
    import json
    from pathlib import Path

    from app.dtos.medication_schedules import PutMedicationScheduleRequest

    fixtures = json.loads(
        (Path(__file__).resolve().parents[4] / "docs/validation/track-b/issue-202-schedule-fixtures.json").read_text()
    )
    PutMedicationScheduleRequest.model_validate(fixtures["put_request"])
    MedicationScheduleResponse.model_validate(fixtures["put_response"])
    for name in ("partial_day", "inactive_with_past_pending"):
        MedicationDayResponse.model_validate(fixtures[name])


async def test_old_version_replays_but_new_write_conflicts_and_history_stays(case: Case) -> None:
    from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
    from app.tests.fixtures.prescription_fingerprint import fingerprint_values

    first = await case.write(case.body)
    assert first.status_code == 200
    prescription = await case.session.scalar(select(Prescription))
    assert prescription is not None
    replacement = PrescriptionVersion(
        id=uuid4(),
        prescription_id=prescription.id,
        version_number=2,
        prescribed_date=prescription.prescribed_date,
        confirmed_at=prescription.confirmed_at,
        **fingerprint_values(
            prescription.prescribed_date,
            [{"medication_name": "합성테스트약", "frequency_per_day": 1, "display_order": 1}],
        ),
    )
    case.session.add(replacement)
    await case.session.flush()
    medication = PrescriptionVersionMedication(
        prescription_version_id=replacement.id,
        medication_count=1,
        medication_name="합성테스트약",
        frequency_per_day=1,
        display_order=1,
    )
    case.session.add(medication)
    prescription.active_version_id = replacement.id
    await case.session.commit()
    replacement_medication_id = str(medication.id)
    before = await counts(case)
    assert (await case.write(case.body)).json() == first.json()
    assert_error(
        await case.write({**case.body, "expected_revision": 1}, key="old-version-new-key"),
        409,
        "PRESCRIPTION_VERSION_CONFLICT",
    )
    assert_error(
        await case.write({"status": "CANCELLED", "expected_revision": 1}, method="PATCH", key="old-version-patch-key"),
        409,
        "PRESCRIPTION_VERSION_CONFLICT",
    )
    assert await counts(case) == before
    data = (await case.read()).json()["data"]
    assert data["schedule_status"] == "SETUP_REQUIRED"
    assert data["schedule_items"][0]["prescription_version_medication_id"] == replacement_medication_id
    assert data["occurrences"]
    assert all(o["prescription_version_medication_id"] == str(case.medication_id) for o in data["occurrences"])
