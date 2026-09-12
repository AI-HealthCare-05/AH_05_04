"""Actual HTTP handoff from delivered notification to the original occurrence."""

import json
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import func, select

from app.core.errors import ErrorResponse
from app.dependencies.security import get_request_user
from app.dtos.medication_checkins import MedicationCheckinResponse
from app.dtos.medication_schedules import MedicationDayResponse
from app.dtos.notifications import NotificationListResponse, NotificationResponse
from app.dtos.prescriptions import PrescriptionResponse
from app.main import fastapi_app
from app.models.medication_schedules import CheckinAudit, MedicationCheckin
from app.services import idempotency
from app.tests.notifications.test_notifications import NOW, Case, error
from app.tests.notifications.test_notifications import case as notification_case_fixture
from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile

case = notification_case_fixture

FIXTURE_PATH = Path(__file__).resolve().parents[4] / "docs/validation/track-b/issue-202-notification-handoff.json"


async def day_for_notification(case: Case, item: dict):
    response = await case.client.get("/api/v1/medication-occurrences", params={"date": item["occurrence_local_date"]})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    occurrence = next(
        row for row in response.json()["data"]["occurrences"] if row["occurrence_id"] == item["occurrence_id"]
    )
    return response, occurrence


async def test_midnight_reminder_read_checkin_and_historical_version_handoff(case: Case, monkeypatch):
    current = await case.client.get("/api/v1/prescriptions/latest")
    assert current.status_code == 200, current.text
    await case.scheduler.generate_once(now=NOW)
    await case.scheduler.publish_once(now=NOW)
    assert (await case.reminder()).status_code == 201
    await case.scheduler.publish_once(now=NOW + timedelta(hours=2))
    listing = await case.client.get("/api/v1/notifications")
    item = listing.json()["data"]["items"][0]
    assert item["kind"] == "REMINDER"
    # KST reminder is next day 01:00, but the original occurrence remains Sep 10.
    assert item["occurrence_local_date"] == "2026-09-10"
    before, occurrence = await day_for_notification(case, item)
    medication_id = occurrence["prescription_version_medication_id"]
    assert any(
        med["prescription_version_medication_id"] == medication_id for med in current.json()["data"]["medications"]
    )
    assert occurrence["checkin"] is None

    with monkeypatch.context() as patch:
        patch.setattr(idempotency, "SNAPSHOT_SIZE_CAP_BYTES", 1)
        failed_read = await case.read(item["id"])
        error(failed_read, 503, "IDEMPOTENCY_RESPONSE_TOO_LARGE")
    unread = (await case.client.get("/api/v1/notifications")).json()["data"]["items"][0]
    assert unread["read_at"] is None
    read = await case.read(item["id"])
    assert read.status_code == 200
    assert (await case.read(item["id"])).json() == read.json()
    after_read, unchanged = await day_for_notification(case, item)
    assert unchanged == occurrence
    assert await case.session.scalar(select(func.count()).select_from(MedicationCheckin)) == 0
    assert await case.session.scalar(select(func.count()).select_from(CheckinAudit)) == 0

    request = {"status": "TAKEN", "expected_revision": 0}
    checkin = await case.client.put(
        f"/api/v1/medication-occurrences/{item['occurrence_id']}/check-in",
        json=request,
        headers={"Idempotency-Key": "handoff-checkin-key"},
    )
    assert checkin.status_code == 200, checkin.text
    conflict = await case.client.put(
        f"/api/v1/medication-occurrences/{item['occurrence_id']}/check-in",
        json=request,
        headers={"Idempotency-Key": "handoff-stale-checkin-key"},
    )
    error(conflict, 409, "CHECKIN_REVISION_CONFLICT")
    _, recorded = await day_for_notification(case, item)
    assert recorded["checkin"] == checkin.json()["data"]
    assert (await case.read(item["id"], key="handoff-read-after-checkin")).status_code == 200
    assert await case.session.scalar(select(func.count()).select_from(MedicationCheckin)) == 1
    assert await case.session.scalar(select(func.count()).select_from(CheckinAudit)) == 0

    old = current.json()["data"]
    replacement = await case.client.patch(
        f"/api/v1/prescriptions/{old['prescription_id']}",
        json={
            "base_version_id": old["prescription_version_id"],
            "expected_revision": old["revision"],
            "prescribed_date": old["prescribed_date"],
            "medications": [{"medication_name": "합성정정약", "frequency_per_day": 1, "display_order": 1}],
        },
        headers={"Idempotency-Key": "handoff-prescription-correction"},
    )
    assert replacement.status_code == 200, replacement.text
    history, historical = await day_for_notification(case, item)
    assert historical["prescription_version_id"] == old["prescription_version_id"]
    assert historical["prescription_version_medication_id"] == medication_id
    assert historical["checkin"] == checkin.json()["data"]
    detail = await case.client.get(f"/api/v1/prescriptions/{old['prescription_id']}")
    latest = await case.client.get("/api/v1/prescriptions/latest")
    assert detail.status_code == latest.status_code == 200
    assert detail.json() == latest.json() == replacement.json()
    assert all(
        med["prescription_version_medication_id"] != medication_id for med in detail.json()["data"]["medications"]
    )
    retained = await case.client.get("/api/v1/notifications")
    assert {row["id"] for row in retained.json()["data"]["items"]} == {
        row["id"] for row in listing.json()["data"]["items"]
    }
    for response in (current, listing, read, checkin, replacement, detail, latest, retained):
        assert response.headers["cache-control"] == "no-store"

    # Opt-in export only: committed synthetic examples remain unchanged in CI.
    output = os.environ.get("TRACK_B_HANDOFF_FIXTURE_OUTPUT")
    if output:
        payload = {
            "fixture_id": "track-b-notification-record-handoff-v1",
            "classification": "SYNTHETIC",
            "status": "observed-current-behavior-not-new-contract",
            "current_prescription": current.json(),
            "notifications": listing.json(),
            "day_before_read": before.json(),
            "read_failure": failed_read.json(),
            "read_response": read.json(),
            "day_after_read": after_read.json(),
            "checkin_request": request,
            "checkin_response": checkin.json(),
            "checkin_conflict": conflict.json(),
            "current_prescription_after_correction": detail.json(),
            "historical_day": history.json(),
            "notifications_after_correction": retained.json(),
        }
        Path(output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


async def test_foreign_notification_read_and_record_link_remain_hidden(case: Case, monkeypatch):
    await case.scheduler.generate_once(now=NOW)
    await case.scheduler.publish_once(now=NOW)
    item = (await case.client.get("/api/v1/notifications")).json()["data"]["items"][0]
    intruder, _ = await _create_user_with_self_profile(case.session, label="handoff-intruder")
    await case.session.commit()
    case.user.id = intruder.id
    monkeypatch.setitem(fastapi_app.dependency_overrides, get_request_user, lambda: SimpleNamespace(id=intruder.id))
    assert (await case.client.get("/api/v1/notifications")).json()["data"]["items"] == []
    error(await case.read(item["id"]), 404, "NOTIFICATION_NOT_FOUND")
    day = await case.client.get("/api/v1/medication-occurrences", params={"date": item["occurrence_local_date"]})
    assert day.status_code == 200 and day.json()["data"]["occurrences"] == []
    response = await case.client.put(
        f"/api/v1/medication-occurrences/{item['occurrence_id']}/check-in",
        json={"status": "TAKEN", "expected_revision": 0},
        headers={"Idempotency-Key": "foreign-handoff-checkin"},
    )
    error(response, 404, "MEDICATION_OCCURRENCE_NOT_FOUND")
    assert await case.session.scalar(select(func.count()).select_from(MedicationCheckin)) == 0


def test_handoff_fixture_matches_dtos_and_keeps_historical_identity():
    payload = json.loads(FIXTURE_PATH.read_text())
    assert payload["classification"] == "SYNTHETIC"
    assert ErrorResponse.model_validate(payload["read_failure"]).code == "IDEMPOTENCY_RESPONSE_TOO_LARGE"
    assert ErrorResponse.model_validate(payload["checkin_conflict"]).code == "CHECKIN_REVISION_CONFLICT"
    original = PrescriptionResponse.model_validate(payload["current_prescription"]).data
    latest = PrescriptionResponse.model_validate(payload["current_prescription_after_correction"]).data
    listing = NotificationListResponse.model_validate(payload["notifications"]).data
    read = NotificationResponse.model_validate(payload["read_response"]).data
    checkin = MedicationCheckinResponse.model_validate(payload["checkin_response"]).data
    before = MedicationDayResponse.model_validate(payload["day_before_read"]).data
    after = MedicationDayResponse.model_validate(payload["day_after_read"]).data
    historical = MedicationDayResponse.model_validate(payload["historical_day"]).data
    NotificationListResponse.model_validate(payload["notifications_after_correction"])
    assert before == after and before.occurrences[0].checkin is None
    assert read.id == listing.items[0].id and read.read_at is not None
    row = next(row for row in historical.occurrences if row.occurrence_id == checkin.occurrence_id)
    assert row.checkin == checkin
    assert row.prescription_version_id == original.prescription_version_id != latest.prescription_version_id
    assert row.prescription_version_medication_id in {
        med.prescription_version_medication_id for med in original.medications
    }
    assert row.prescription_version_medication_id not in {
        med.prescription_version_medication_id for med in latest.medications
    }
    assert UUID(payload["notifications"]["data"]["items"][0]["occurrence_id"]) == row.occurrence_id
