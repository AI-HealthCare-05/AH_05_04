from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.apis.v1.medication_checkin_backlog_routers import medication_checkin_backlog_router
from app.core.db.databases import get_db_session
from app.core.errors import ApiError, register_exception_handlers
from app.core.no_store_middleware import NoStoreMiddleware
from app.core.validation_trace_middleware import RequestTraceMiddleware
from app.dependencies.security import get_request_user
from app.main import fastapi_app
from app.models.medication_schedules import CheckinAudit, MedicationCheckin, MedicationCheckinStatus, MedicationSchedule
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.repositories.medication_checkin_repository import MedicationCheckinRepository
from app.services.medication_checkin_backlog import MedicationCheckinBacklogService
from app.services.medication_checkins import MedicationCheckinService, NoopCheckinRevisionInvalidation
from app.tests.repositories.test_medication_checkin_repository_integration import _create_occurrence
from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile


async def _seed(session, *, count=3):
    owner, profile = await _create_user_with_self_profile(session, label="backlog")
    repository = MedicationCheckinRepository(session)
    records = []
    for index in range(count):
        occurrence = await _create_occurrence(
            session,
            owner=owner,
            profile=profile,
            deadline_at=datetime(2026, 9, 9, 4, tzinfo=UTC) + timedelta(days=index // 2),
        )
        checkin = await repository.create_if_absent(
            occurrence_id=occurrence.id,
            status=MedicationCheckinStatus.UNCONFIRMED,
            taken_at=None,
        )
        await repository.close_occurrence(occurrence=occurrence)
        records.append((checkin, occurrence))
    return owner, profile, records


async def test_keyset_survives_corrections_and_uses_id_tiebreaker(db_session: AsyncSession):
    owner, _, records = await _seed(db_session, count=5)
    repository = MedicationCheckinRepository(db_session)
    backlog = MedicationCheckinBacklogService(repository)
    correction = MedicationCheckinService(repository, revision_invalidation=NoopCheckinRevisionInvalidation())
    expected = sorted(records, key=lambda row: (row[1].scheduled_at, row[0].id))
    seen = []
    cursor = None
    while True:
        page = await backlog.list_owned(user_id=owner.id, limit=2, cursor=cursor)
        for item in page.items:
            seen.append(item.checkin_id)
            await correction.put_owned(
                occurrence_id=item.occurrence_id,
                user_id=owner.id,
                status=MedicationCheckinStatus.TAKEN if len(seen) % 2 else MedicationCheckinStatus.NOT_TAKEN,
                taken_at=None,
                expected_revision=item.revision,
            )
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == [row[0].id for row in expected]
    assert (await backlog.list_owned(user_id=owner.id)).items == []
    assert await db_session.scalar(select(func.count()).select_from(CheckinAudit)) == 5


async def test_historical_snapshot_and_refetch_after_stale_revision(db_session: AsyncSession):
    owner, _, records = await _seed(db_session, count=1)
    checkin, occurrence = records[0]
    schedule = await db_session.get(MedicationSchedule, occurrence.medication_schedule_id)
    assert schedule is not None
    medication = await db_session.get(PrescriptionVersionMedication, schedule.prescription_version_medication_id)
    assert medication is not None
    version = await db_session.get(PrescriptionVersion, medication.prescription_version_id)
    assert version is not None
    prescription = await db_session.get(Prescription, version.prescription_id)
    assert prescription is not None
    replacement = PrescriptionVersion(
        prescription_id=prescription.id,
        version_number=2,
        prescribed_date=version.prescribed_date,
        confirmed_at=version.confirmed_at,
    )
    db_session.add(replacement)
    await db_session.flush()
    prescription.active_version_id = replacement.id
    checkin.revision = 3
    await db_session.flush()
    repository = MedicationCheckinRepository(db_session)
    backlog = MedicationCheckinBacklogService(repository)
    page = await backlog.list_owned(user_id=owner.id)
    item = page.items[0]
    assert item.prescription_version_id == version.id != replacement.id
    assert item.prescription_id == prescription.id
    assert item.prescription_version_medication_id == medication.id
    assert item.medication_name == medication.medication_name
    assert item.strength_text is None
    assert item.scheduled_at == occurrence.scheduled_at
    assert item.confirmation_deadline_at == occurrence.confirmation_deadline_at
    assert item.revision == 3
    service = MedicationCheckinService(repository, revision_invalidation=NoopCheckinRevisionInvalidation())
    await service.put_owned(
        occurrence_id=occurrence.id,
        user_id=owner.id,
        status=MedicationCheckinStatus.TAKEN,
        taken_at=None,
        expected_revision=3,
    )
    with pytest.raises(ApiError) as conflict:
        await service.put_owned(
            occurrence_id=occurrence.id,
            user_id=owner.id,
            status=MedicationCheckinStatus.NOT_TAKEN,
            taken_at=None,
            expected_revision=item.revision,
        )
    assert conflict.value.status_code == 409
    assert (await backlog.list_owned(user_id=owner.id)).items == []


async def test_proposed_http_contract_auth_ownership_validation_and_no_mutation(db_session: AsyncSession):
    owner, _, records = await _seed(db_session, count=3)
    intruder, _ = await _create_user_with_self_profile(db_session, label="backlog-other")
    owner_id, intruder_id = owner.id, intruder.id
    checkin_id = records[0][0].id
    candidate = FastAPI()
    candidate.include_router(medication_checkin_backlog_router, prefix="/api/v1")
    register_exception_handlers(candidate)
    candidate.dependency_overrides[get_db_session] = lambda: db_session
    wrapped = RequestTraceMiddleware(NoStoreMiddleware(candidate))
    url = "/api/v1/medication-checkins/unconfirmed"
    async with AsyncClient(transport=ASGITransport(app=wrapped), base_url="http://test") as client:
        unauthorized = await client.get(url)
        assert unauthorized.status_code == 401
        assert unauthorized.json()["code"] == "UNAUTHORIZED"
        assert unauthorized.headers["cache-control"] == "no-store"
        invalid_token = await client.get(url, headers={"Authorization": "Bearer synthetic-invalid"})
        assert invalid_token.status_code == 401
        assert invalid_token.json()["code"] == "INVALID_TOKEN"
        assert invalid_token.headers["cache-control"] == "no-store"
        candidate.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=owner_id)
        first = await client.get(url)
        again = await client.get(url)
        assert first.json() == again.json()
        assert {item["checkin_id"] for item in first.json()["data"]["items"]} == {str(row[0].id) for row in records}
        paged_ids = []
        params = {"limit": "1"}
        while True:
            response = await client.get(url, params=params)
            assert response.status_code == 200
            data = response.json()["data"]
            assert len(data["items"]) == 1
            paged_ids.append(data["items"][0]["checkin_id"])
            if data["next_cursor"] is None:
                break
            params["cursor"] = data["next_cursor"]
        assert paged_ids == [item["checkin_id"] for item in first.json()["data"]["items"]]
        assert first.json()["data"]["next_cursor"] is None
        assert first.headers["cache-control"] == "no-store"
        for query in ("limit=0", "limit=101", "limit=abc", "cursor=bad"):
            response = await client.get(f"{url}?{query}")
            assert response.status_code == 422
            assert response.json()["code"] == "VALIDATION_FAILED"
            assert response.json()["trace_id"]
            assert response.headers["cache-control"] == "no-store"
        candidate.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=intruder_id)
        assert (await client.get(url)).json() == {"data": {"items": [], "next_cursor": None}}
        for cursor in (checkin_id, uuid4()):
            response = await client.get(url, params={"cursor": str(cursor)})
            assert response.status_code == 404
            assert response.json()["code"] == "CHECKIN_CURSOR_NOT_FOUND"
            assert response.headers["cache-control"] == "no-store"
    await db_session.refresh(records[0][0])
    assert records[0][0].status == MedicationCheckinStatus.UNCONFIRMED
    assert records[0][0].revision == 1
    assert await db_session.scalar(select(func.count()).select_from(CheckinAudit)) == 0
    assert await db_session.scalar(select(func.count()).select_from(MedicationCheckin)) == 3
    schema = candidate.openapi()
    operation = schema["paths"][url]["get"]
    for status in ("401", "404", "422"):
        assert operation["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
    assert url not in fastapi_app.openapi()["paths"]


@pytest.mark.parametrize("limit", [0, 101])
async def test_service_rejects_unbounded_page(db_session: AsyncSession, limit: int):
    with pytest.raises(ValueError):
        await MedicationCheckinBacklogService(MedicationCheckinRepository(db_session)).list_owned(
            user_id=uuid4(), limit=limit
        )
