"""HTTP coverage of original medication display across prescription versions."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.dependencies.security import get_request_user
from app.dtos.medication_schedules import MedicationOccurrenceMedicationResponse
from app.main import fastapi_app
from app.models.async_jobs import IdempotencyRecord
from app.models.medication_schedules import CheckinAudit, MedicationCheckin, MedicationOccurrenceStatus
from app.repositories.medication_checkin_repository import MedicationCheckinRepository
from app.services.medication_checkins import MedicationCheckinDeadlineScheduler
from app.tests.notifications.test_notifications import NOW, Case, error
from app.tests.notifications.test_notifications import case as notification_case_fixture
from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile

case = notification_case_fixture
PATH = "/api/v1/medication-occurrences/{occurrence_id}/medication"
FIELDS = {
    "occurrence_id",
    "prescription_version_id",
    "prescription_version_medication_id",
    "medication_name",
    "strength_text",
    "dose_value",
    "dose_unit",
}


async def mutation_state(case: Case) -> dict:
    return {
        "day": (await case.client.get("/api/v1/medication-occurrences", params={"date": "2026-09-10"})).json(),
        "notifications": (await case.client.get("/api/v1/notifications")).json(),
        "counts": [
            await case.session.scalar(select(func.count()).select_from(model))
            for model in (MedicationCheckin, CheckinAudit, IdempotencyRecord)
        ],
    }


async def test_original_medication_after_correction_and_midnight_reminder(case: Case):
    path = PATH.format(occurrence_id=case.occurrence.id)
    original = await case.client.get(path)
    assert original.status_code == 200, original.text
    parsed = MedicationOccurrenceMedicationResponse.model_validate(original.json()).data
    assert set(original.json()["data"]) == FIELDS
    assert parsed.occurrence_id == case.occurrence.id
    assert parsed.medication_name == "합성테스트약"
    assert parsed.strength_text is parsed.dose_value is parsed.dose_unit is None
    current = (await case.client.get("/api/v1/prescriptions/latest")).json()["data"]
    assert str(parsed.prescription_version_id) == current["prescription_version_id"]

    await case.scheduler.generate_once(now=NOW)
    await case.scheduler.publish_once(now=NOW)
    assert (await case.reminder()).status_code == 201
    await case.scheduler.publish_once(now=NOW + timedelta(hours=2))
    before = await mutation_state(case)
    notification = before["notifications"]["data"]["items"][0]
    assert notification["kind"] == "REMINDER"
    assert notification["occurrence_local_date"] == "2026-09-10"
    assert notification["occurrence_id"] == str(parsed.occurrence_id)
    for _ in range(2):
        response = await case.client.get(path)
        assert response.json() == original.json()
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-trace-id"]
    assert await mutation_state(case) == before

    correction = await case.client.patch(
        f"/api/v1/prescriptions/{current['prescription_id']}",
        json={
            "base_version_id": current["prescription_version_id"],
            "expected_revision": current["revision"],
            "prescribed_date": current["prescribed_date"],
            # Identical name/order must not join the old occurrence to the new dose.
            "medications": [
                {
                    "medication_name": parsed.medication_name,
                    "strength_text": "합성함량",
                    "dose_value": 2.5,
                    "dose_unit": "정",
                    "frequency_per_day": 1,
                    "display_order": 1,
                }
            ],
        },
        headers={"Idempotency-Key": "occurrence-medication-correction"},
    )
    assert correction.status_code == 200, correction.text
    latest = await case.client.get("/api/v1/prescriptions/latest")
    assert latest.json() == correction.json()
    assert latest.json()["data"]["prescription_version_id"] != str(parsed.prescription_version_id)
    assert latest.json()["data"]["medications"][0]["dose_value"] == 2.5
    history = await case.client.get(path)
    assert history.json() == original.json()
    before_checkin = await mutation_state(case)
    old_occurrence = next(
        item
        for item in before_checkin["day"]["data"]["occurrences"]
        if item["occurrence_id"] == str(parsed.occurrence_id)
    )
    for key in ("occurrence_id", "prescription_version_id", "prescription_version_medication_id"):
        assert old_occurrence[key] == history.json()["data"][key]
    assert old_occurrence["checkin"] is None
    checkin = await case.client.put(
        f"/api/v1/medication-occurrences/{parsed.occurrence_id}/check-in",
        json={"status": "TAKEN", "expected_revision": 0},
        headers={"Idempotency-Key": "occurrence-medication-taken"},
    )
    assert checkin.status_code == 200, checkin.text
    after_checkin = await mutation_state(case)
    assert (await case.client.get(path)).json() == original.json()
    assert await mutation_state(case) == after_checkin

    new_medication_id = latest.json()["data"]["medications"][0]["prescription_version_medication_id"]
    future = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
    schedule = await case.client.put(
        f"/api/v1/prescription-version-medications/{new_medication_id}/schedule",
        json={"start_local_date": future, "end_mode": "OPEN_ENDED", "local_times": ["09:00"], "expected_revision": 0},
        headers={"Idempotency-Key": "occurrence-new-version-schedule"},
    )
    assert schedule.status_code == 200, schedule.text
    new_day = (await case.client.get("/api/v1/medication-occurrences", params={"date": future})).json()
    new_occurrence = new_day["data"]["occurrences"][0]
    new_snapshot = await case.client.get(PATH.format(occurrence_id=new_occurrence["occurrence_id"]))
    assert new_snapshot.status_code == 200
    assert new_snapshot.json()["data"]["dose_value"] == 2.5
    assert new_snapshot.json()["data"]["dose_unit"] == "정"
    assert new_snapshot.json()["data"]["strength_text"] == "합성함량"
    assert new_snapshot.json()["data"]["prescription_version_medication_id"] == new_medication_id
    assert (await case.client.get(path)).json() == original.json()

    output = os.environ.get("TRACK_B_OCCURRENCE_MEDICATION_FIXTURE_OUTPUT")
    if output:
        Path(output).write_text(
            json.dumps(
                {
                    "classification": "SYNTHETIC",
                    "fixture_id": "track-b-occurrence-medication-v1",
                    "notifications": before["notifications"],
                    "historical_day": before_checkin["day"],
                    "historical_medication": history.json(),
                    "current_prescription": latest.json(),
                    "current_medication": new_snapshot.json(),
                    "checkin": checkin.json(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


@pytest.mark.parametrize("state", ["PENDING", "CANCELLED", "TAKEN", "NOT_TAKEN", "UNCONFIRMED"])
async def test_read_preserves_occurrence_and_checkin_states(case: Case, state: str):
    if state == "CANCELLED":
        case.occurrence.status = MedicationOccurrenceStatus.CANCELLED
    elif state == "UNCONFIRMED":
        await MedicationCheckinDeadlineScheduler(MedicationCheckinRepository(case.session)).generate_unconfirmed(
            now=NOW + timedelta(hours=5)
        )
    elif state in ("TAKEN", "NOT_TAKEN"):
        saved = await case.client.put(
            f"/api/v1/medication-occurrences/{case.occurrence.id}/check-in",
            json={"status": state, "expected_revision": 0},
            headers={"Idempotency-Key": "occurrence-medication-state"},
        )
        assert saved.status_code == 200, saved.text
    await case.session.commit()
    before = await mutation_state(case)
    assert (await case.client.get(PATH.format(occurrence_id=case.occurrence.id))).status_code == 200
    assert await mutation_state(case) == before


async def test_ownership_missing_validation_and_auth(case: Case):
    path = PATH.format(occurrence_id=case.occurrence.id)
    intruder, _ = await _create_user_with_self_profile(case.session, label="occurrence-medication-intruder")
    await case.session.commit()
    case.user.id = intruder.id
    hidden = await case.client.get(path)
    missing = await case.client.get(PATH.format(occurrence_id=uuid4()))
    for response in (hidden, missing):
        error(response, 404, "MEDICATION_OCCURRENCE_NOT_FOUND")
        assert response.headers["x-trace-id"] == response.json()["trace_id"]
    assert {k: v for k, v in hidden.json().items() if k != "trace_id"} == {
        k: v for k, v in missing.json().items() if k != "trace_id"
    }
    error(await case.client.get(PATH.format(occurrence_id="not-a-uuid")), 422, "VALIDATION_FAILED")
    fastapi_app.dependency_overrides.pop(get_request_user)
    response = await case.client.get(path)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_openapi_occurrence_medication_contract():
    schema = fastapi_app.openapi()
    operation = schema["paths"][PATH]["get"]
    assert operation["operationId"] == "medication-occurrences.medication.get"
    assert operation["security"]
    assert "requestBody" not in operation
    assert {p["name"] for p in operation["parameters"]} == {"occurrence_id"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/MedicationOccurrenceMedicationResponse"
    )
    dto = schema["components"]["schemas"]["MedicationOccurrenceMedicationData"]
    assert set(dto["required"]) == set(dto["properties"]) == FIELDS
    for field in ("strength_text", "dose_value", "dose_unit"):
        assert {item["type"] for item in dto["properties"][field]["anyOf"]} == {
            "number" if field == "dose_value" else "string",
            "null",
        }
    for code in ("401", "404", "422"):
        assert operation["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
