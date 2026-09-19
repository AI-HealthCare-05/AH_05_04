import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.dependencies.security import get_request_user
from app.dtos.medication_reports import MedicationReportResponse
from app.main import app, fastapi_app
from app.models.medication_schedules import MedicationCheckin, MedicationSchedule
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.track_c import BarrierResponse, SafetyAssessment
from app.repositories.medication_checkin_repository import MedicationCheckinRepository
from app.repositories.medication_report_repository import MedicationReportRepository
from app.services.medication_checkins import MedicationCheckinDeadlineScheduler
from app.services.medication_reports import MedicationReportService, _rate, _report_time_slot
from app.tests.fixtures.prescription_fingerprint import fingerprint_values
from app.tests.medication_checkins.test_medication_checkin_api import assert_error
from app.tests.repositories.test_medication_checkin_repository_integration import _create_occurrence
from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile

END = date(2026, 9, 13)
NOW = datetime(2026, 9, 13, 15, tzinfo=UTC)
URL = "/api/v1/medication-reports"


@pytest.fixture
async def case(db_session: AsyncSession):
    owner, profile = await _create_user_with_self_profile(db_session, label="report-owner")
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=owner.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(session=db_session, owner=owner, profile=profile, client=client)
    fastapi_app.dependency_overrides.pop(get_request_user, None)


async def seed(case, day: date, status: str, *, hour: int = 0):
    # Explicit synthetic schedule snapshots, including UTC on the previous date.
    scheduled = datetime.combine(day, datetime.min.time(), UTC) - timedelta(hours=9) + timedelta(hours=hour)
    occurrence = await _create_occurrence(
        case.session, owner=case.owner, profile=case.profile, deadline_at=scheduled + timedelta(days=1)
    )
    occurrence.scheduled_local_date = day
    occurrence.scheduled_at = scheduled
    occurrence.updated_at = scheduled
    occurrence.confirmation_deadline_at = max(
        scheduled + timedelta(hours=4),
        datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC) - timedelta(hours=9),
    )
    if status in ("TAKEN", "NOT_TAKEN", "UNCONFIRMED"):
        occurrence.status = "CLOSED"
        case.session.add(
            MedicationCheckin(
                occurrence_id=occurrence.id,
                status=status,
                revision=1,
                created_at=scheduled,
                updated_at=scheduled,
            )
        )
    else:
        occurrence.status = status
        if status == "CANCELLED":
            occurrence.cancelled_at = scheduled
    await case.session.flush()
    return occurrence


@pytest.mark.parametrize("period", [7, 30])
async def test_period_bounds_current_records_rates_and_self_ownership(case, period: int):
    start = END - timedelta(days=period - 1)
    included = [
        await seed(case, start, "TAKEN"),
        await seed(case, END, "TAKEN"),
        await seed(case, END, "NOT_TAKEN", hour=1),
        await seed(case, END, "UNCONFIRMED", hour=2),
        await seed(case, END, "PENDING", hour=3),
        await seed(case, END, "CANCELLED", hour=4),
    ]
    await seed(case, start - timedelta(days=1), "TAKEN")
    await seed(case, END + timedelta(days=1), "TAKEN")
    other, profile = await _create_user_with_self_profile(case.session, label="report-other")
    await seed(SimpleNamespace(session=case.session, owner=other, profile=profile), END, "NOT_TAKEN")
    await case.session.commit()
    response = await case.client.get(URL, params={"period_days": period, "end_date": END.isoformat()})
    assert response.status_code == 200, response.text
    data = MedicationReportResponse.model_validate(response.json()).data
    assert data.start_date == start and data.end_date == END
    assert data.counts.model_dump() == dict(
        taken_count=2, not_taken_count=1, unconfirmed_count=1, pending_count=1, cancelled_count=1
    )
    assert data.adherence_rate.model_dump() == dict(numerator=2, denominator=3, percentage=66.7)
    assert data.confirmation_rate.model_dump() == dict(numerator=3, denominator=4, percentage=75.0)
    assert {r.occurrence_id for r in data.records} == {r.id for r in included}
    assert [(r.scheduled_at, r.occurrence_id) for r in data.records] == sorted(
        (r.scheduled_at, r.occurrence_id) for r in data.records
    )
    assert data.records[0].scheduled_at.date() == start - timedelta(days=1)
    assert {r.medication_name for r in data.records} == {"합성테스트약"}
    assert {r.time_slot for r in data.records} == {"BEDTIME"}
    assert response.headers["cache-control"] == "no-store" and response.headers["x-trace-id"]


@pytest.mark.parametrize(
    "statuses,expected_confirmation", [([], None), (["PENDING", "CANCELLED"], None), (["UNCONFIRMED"], 0.0)]
)
async def test_zero_denominators(case, statuses, expected_confirmation):
    for status in statuses:
        await seed(case, END, status)
    response = await case.client.get(URL, params={"period_days": 7, "end_date": END.isoformat()})
    data = response.json()["data"]
    assert data["adherence_rate"] == dict(numerator=0, denominator=0, percentage=None)
    assert data["confirmation_rate"]["percentage"] == expected_confirmation
    assert len(data["records"]) == len(statuses)


async def test_deadline_read_only_then_scheduler_and_correction_on_original_date(case):
    occurrence = await seed(case, END, "PENDING", hour=23)
    deadline = occurrence.confirmation_deadline_at
    service = MedicationReportService(MedicationReportRepository(case.session))
    for offset, expected in [(-1, 0), (0, 1), (1, 1)]:
        data = (
            await service.report(
                user_id=case.owner.id, period_days=7, end_date=END, now=deadline + timedelta(seconds=offset)
            )
        ).data
        assert data.overdue_pending_count == expected
        assert data.counts.pending_count == 1 and data.counts.unconfirmed_count == 0
    assert await case.session.scalar(select(func.count()).select_from(MedicationCheckin)) == 0
    await MedicationCheckinDeadlineScheduler(MedicationCheckinRepository(case.session)).generate_unconfirmed(
        now=deadline
    )
    await case.session.commit()
    for revision, status, expected in [(1, "TAKEN", 100.0), (2, "NOT_TAKEN", 0.0)]:
        response = await case.client.put(
            f"/api/v1/medication-occurrences/{occurrence.id}/check-in",
            json={"status": status, "expected_revision": revision},
            headers={"Idempotency-Key": f"report-correction-{revision}"},
        )
        assert response.status_code == 200, response.text
        data = (await case.client.get(URL, params={"period_days": 7, "end_date": END.isoformat()})).json()["data"]
        assert data["adherence_rate"]["percentage"] == expected
        assert data["confirmation_rate"]["percentage"] == 100.0
        assert len(data["records"]) == 1
        record = data["records"][0]
        assert record["scheduled_local_date"] == END.isoformat()
        assert record["checkin"]["corrected"] and record["checkin"]["revision"] == revision + 1
        assert record["updated_at"] == record["checkin"]["updated_at"]


async def test_old_version_survives_replacement_and_cancelled_schedule(case):
    occurrence = await seed(case, END, "TAKEN")
    schedule = await case.session.get(MedicationSchedule, occurrence.medication_schedule_id)
    old_medication = await case.session.get(PrescriptionVersionMedication, schedule.prescription_version_medication_id)
    old_medication.strength_text = "100mg"
    old_version = await case.session.get(PrescriptionVersion, old_medication.prescription_version_id)
    prescription = await case.session.get(Prescription, old_version.prescription_id)
    replacement = PrescriptionVersion(
        id=uuid4(),
        prescription_id=prescription.id,
        version_number=2,
        prescribed_date=END,
        confirmed_at=NOW,
        **fingerprint_values(END, [{"medication_name": "합성교체약", "frequency_per_day": 1, "display_order": 1}]),
    )
    case.session.add(replacement)
    await case.session.flush()
    case.session.add(
        PrescriptionVersionMedication(
            prescription_version_id=replacement.id,
            medication_count=1,
            medication_name="합성교체약",
            frequency_per_day=1,
            display_order=1,
        )
    )
    prescription.active_version_id = replacement.id
    schedule.status = "CANCELLED"
    await case.session.commit()
    data = (await case.client.get(URL, params={"period_days": 7, "end_date": END.isoformat()})).json()["data"]
    assert data["counts"]["taken_count"] == 1
    assert data["records"][0]["prescription_version_id"] == str(old_version.id)
    assert data["records"][0]["prescription_version_medication_id"] == str(old_medication.id)
    assert data["records"][0]["medication_name"] == "합성테스트약"
    assert data["records"][0]["strength_text"] == "100mg"


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"period_days": 8},
        {"period_days": "abc"},
        {"period_days": "7.1"},
        {"period_days": 7, "end_date": "9999-12-31"},
        {"period_days": 30, "end_date": "0001-01-01"},
        {"period_days": 7, "end_date": "2026-99-99"},
    ],
)
async def test_query_validation(case, params):
    assert_error(await case.client.get(URL, params=params), 422, "VALIDATION_FAILED")


async def test_authentication_and_no_self_profile(case):
    await seed(case, END, "TAKEN")
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    data = (await case.client.get(URL, params={"period_days": 7})).json()["data"]
    assert data["records"] == [] and data["adherence_rate"]["percentage"] is None
    fastapi_app.dependency_overrides.pop(get_request_user)
    assert (await case.client.get(URL, params={"period_days": 7})).status_code == 401


async def test_kst_default_date_and_underflow_before_query():
    repository = AsyncMock(spec=MedicationReportRepository)
    repository.list_owned.return_value = []
    service = MedicationReportService(repository)
    for now, end in [(datetime(2026, 9, 13, 14, 59, 59, tzinfo=UTC), END), (NOW, END + timedelta(days=1))]:
        data = (await service.report(user_id=uuid4(), period_days=7, now=now)).data
        assert data.end_date == end and data.start_date == end - timedelta(days=6)
        assert data.timezone == "Asia/Seoul"
    repository.list_owned.reset_mock()
    with pytest.raises(ApiError):
        await service.report(user_id=uuid4(), period_days=30, end_date=date.min, now=NOW)
    repository.list_owned.assert_not_called()


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(2026, 9, 12, 19, 59, tzinfo=UTC), "BEDTIME"),
        (datetime(2026, 9, 12, 20, 0, tzinfo=UTC), "BREAKFAST"),
        (datetime(2026, 9, 13, 1, 59, tzinfo=UTC), "BREAKFAST"),
        (datetime(2026, 9, 13, 2, 0, tzinfo=UTC), "LUNCH"),
        (datetime(2026, 9, 13, 5, 59, tzinfo=UTC), "LUNCH"),
        (datetime(2026, 9, 13, 6, 0, tzinfo=UTC), "DINNER"),
        (datetime(2026, 9, 13, 11, 59, tzinfo=UTC), "DINNER"),
        (datetime(2026, 9, 13, 12, 0, tzinfo=UTC), "BEDTIME"),
    ],
)
def test_report_time_slot_uses_kst_boundaries(instant: datetime, expected: str):
    assert _report_time_slot(instant) == expected


def test_half_up_and_frontend_contract():
    assert _rate(1, 16).percentage == 6.3
    schema = fastapi_app.openapi()
    operation = schema["paths"][URL]["get"]
    period = next(p for p in operation["parameters"] if p["name"] == "period_days")
    assert period["required"] and period["schema"]["enum"] == [7, 30]
    assert operation["operationId"] == "medication-reports.get"
    for status in (401, 422):
        assert operation["responses"][str(status)]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/ErrorResponse"
        )
    record = schema["components"]["schemas"]["MedicationReportRecord"]
    for field in ("medication_name", "strength_text", "time_slot"):
        assert field in record["required"]
    assert record["properties"]["time_slot"]["enum"] == ["BREAKFAST", "LUNCH", "DINNER", "BEDTIME"]
    checkin = schema["components"]["schemas"]["MedicationReportCheckin"]
    assert "updated_at" in checkin["required"]
    fixtures = json.loads(
        (Path(__file__).resolve().parents[4] / "docs/validation/track-b/issue-419-report-fixtures.json").read_text()
    )
    for fixture in fixtures.values():
        parsed = MedicationReportResponse.model_validate(fixture).data
        counts = parsed.counts
        assert parsed.adherence_rate == _rate(counts.taken_count, counts.taken_count + counts.not_taken_count)
        assert parsed.confirmation_rate == _rate(
            counts.taken_count + counts.not_taken_count,
            counts.taken_count + counts.not_taken_count + counts.unconfirmed_count,
        )


async def answer_barrier(case, occurrence, barrier_code: str | None, subreason_code: str | None, revision: int):
    """Append one Barrier revision for the occurrence's Check-in, as the flow does."""
    checkin = await case.session.scalar(
        select(MedicationCheckin).where(MedicationCheckin.occurrence_id == occurrence.id)
    )
    assert checkin is not None
    safety = await case.session.scalar(
        select(SafetyAssessment).where(SafetyAssessment.medication_checkin_id == checkin.id)
    )
    if safety is None:
        safety = SafetyAssessment(
            medication_checkin_id=checkin.id,
            checkin_revision=checkin.revision,
            revision=1,
            symptom_codes=[],
            response_level="ROUTINE",
            safety_disposition="NORMAL",
            message_code="SYNTHETIC",
            copy_version="synthetic-v1",
            source_version="synthetic-v1",
        )
        case.session.add(safety)
        await case.session.flush()
    response = BarrierResponse(
        medication_checkin_id=checkin.id,
        checkin_revision=checkin.revision,
        safety_assessment_id=safety.id,
        revision=revision,
        response_status="ANSWERED" if barrier_code else "DECLINED",
        barrier_code=barrier_code,
        subreason_code=subreason_code,
    )
    case.session.add(response)
    await case.session.flush()
    return response


async def clinic_data(case, *, view: str | None = "CLINIC"):
    query = f"?period_days=7&view={view}" if view else "?period_days=7"
    response = await case.client.get(f"{URL}{query}")
    assert response.status_code == 200
    return response.json()["data"]


async def test_clinic_view_returns_current_barrier_and_default_view_does_not(case):
    occurrence = await seed(case, END, "NOT_TAKEN")
    await answer_barrier(case, occurrence, "FORGOT", "MISSED_ALERT", 1)

    clinic = (await clinic_data(case))["clinic"]
    assert [(item["barrier_code"], item["subreason_code"]) for item in clinic["barriers"]] == [
        ("FORGOT", "MISSED_ALERT")
    ]
    assert clinic["consultation_questions"] == []

    # The reason is clinic-only: the default report must not carry it at all.
    assert (await clinic_data(case, view=None))["clinic"] is None


async def test_declined_correction_removes_the_replaced_reason_from_the_clinic_view(case):
    """A newer DECLINED revision withdraws the reason; the clinic view must follow.

    Ranking has to run over every revision. If the answered filter were applied first,
    the DECLINED correction would be dropped and the ANSWERED revision it replaced
    would win, showing a clinician a reason the user already withdrew.
    """
    occurrence = await seed(case, END, "NOT_TAKEN")
    await answer_barrier(case, occurrence, "FORGOT", "MISSED_ALERT", 1)
    assert len((await clinic_data(case))["clinic"]["barriers"]) == 1

    await answer_barrier(case, occurrence, None, None, 2)

    assert (await clinic_data(case))["clinic"]["barriers"] == []


async def test_clinic_view_follows_a_reason_corrected_to_another_reason(case):
    occurrence = await seed(case, END, "NOT_TAKEN")
    await answer_barrier(case, occurrence, "FORGOT", "MISSED_ALERT", 1)
    await answer_barrier(case, occurrence, "MEDICATION_CONCERN", "LONG_TERM_USE", 2)

    clinic = (await clinic_data(case))["clinic"]
    assert [(item["barrier_code"], item["subreason_code"]) for item in clinic["barriers"]] == [
        ("MEDICATION_CONCERN", "LONG_TERM_USE")
    ]
