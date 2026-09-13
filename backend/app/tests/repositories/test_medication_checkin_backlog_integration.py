import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.dependencies.security import get_request_user
from app.dtos.medication_checkin_backlog import UnconfirmedCheckinResponse
from app.dtos.medication_checkins import MedicationCheckinData, PutMedicationCheckinRequest
from app.main import app, fastapi_app
from app.models.medication_schedules import CheckinAudit, MedicationCheckin, MedicationCheckinStatus, MedicationSchedule
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.repositories.medication_checkin_repository import MedicationCheckinRepository
from app.services.medication_checkin_backlog import MedicationCheckinBacklogService
from app.services.medication_checkins import MedicationCheckinService, NoopCheckinRevisionInvalidation
from app.tests.fixtures.prescription_fingerprint import fingerprint_values
from app.tests.repositories.test_medication_checkin_repository_integration import _create_occurrence
from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile


async def test_backlog_is_registered_once_in_actual_v1_app():
    url = "/api/v1/medication-checkins/unconfirmed"
    assert url in fastapi_app.openapi()["paths"]
    routes = [route for route in fastapi_app.routes if getattr(route, "path", None) == url]
    assert len(routes) == 1
    assert routes[0].methods == {"GET"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(url)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert response.headers["cache-control"] == "no-store"


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
    replacement_medication_values = {"medication_name": "합성교체약", "frequency_per_day": 1, "display_order": 1}
    replacement = PrescriptionVersion(
        **fingerprint_values(version.prescribed_date, [replacement_medication_values]),
        prescription_id=prescription.id,
        version_number=2,
        prescribed_date=version.prescribed_date,
        confirmed_at=version.confirmed_at,
    )
    db_session.add(replacement)
    await db_session.flush()
    db_session.add(
        PrescriptionVersionMedication(
            prescription_version_id=replacement.id,
            medication_count=replacement.medication_count,
            **replacement_medication_values,
        )
    )
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


async def test_http_contract_auth_ownership_validation_and_no_mutation(db_session: AsyncSession, monkeypatch):
    owner, _, records = await _seed(db_session, count=3)
    intruder, _ = await _create_user_with_self_profile(db_session, label="backlog-other")
    owner_id, intruder_id = owner.id, intruder.id
    checkin_id = records[0][0].id
    record_ids = {str(row[0].id) for row in records}
    await db_session.commit()
    url = "/api/v1/medication-checkins/unconfirmed"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unauthorized = await client.get(url)
        assert unauthorized.status_code == 401
        assert unauthorized.json()["code"] == "UNAUTHORIZED"
        assert unauthorized.headers["cache-control"] == "no-store"
        invalid_token = await client.get(url, headers={"Authorization": "Bearer synthetic-invalid"})
        assert invalid_token.status_code == 401
        assert invalid_token.json()["code"] == "INVALID_TOKEN"
        assert invalid_token.headers["cache-control"] == "no-store"
        monkeypatch.setitem(fastapi_app.dependency_overrides, get_request_user, lambda: SimpleNamespace(id=owner_id))
        first = await client.get(url)
        again = await client.get(url)
        assert first.json() == again.json()
        assert {item["checkin_id"] for item in first.json()["data"]["items"]} == record_ids
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
        monkeypatch.setitem(fastapi_app.dependency_overrides, get_request_user, lambda: SimpleNamespace(id=intruder_id))
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
    schema = fastapi_app.openapi()
    operation = schema["paths"][url]["get"]
    for status in ("401", "404", "422"):
        assert operation["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


@pytest.mark.parametrize("limit", [0, 101])
async def test_service_rejects_unbounded_page(db_session: AsyncSession, limit: int):
    with pytest.raises(ValueError):
        await MedicationCheckinBacklogService(MedicationCheckinRepository(db_session)).list_owned(
            user_id=uuid4(), limit=limit
        )


@pytest.mark.parametrize("status", ["TAKEN", "NOT_TAKEN"])
async def test_http_put_then_backlog_refetch_and_cursor_recovery(db_session: AsyncSession, monkeypatch, status: str):
    owner, _, records = await _seed(db_session, count=3)
    owner_id = owner.id
    record_ids = {str(row[0].id) for row in records}
    await db_session.commit()
    monkeypatch.setitem(fastapi_app.dependency_overrides, get_request_user, lambda: SimpleNamespace(id=owner_id))
    url = "/api/v1/medication-checkins/unconfirmed"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get(url, params={"limit": 1})
        assert first.status_code == 200
        item = first.json()["data"]["items"][0]
        cursor = first.json()["data"]["next_cursor"]
        assert cursor == item["checkin_id"]
        put_url = f"/api/v1/medication-occurrences/{item['occurrence_id']}/check-in"
        body = {"status": status, "expected_revision": item["revision"]}
        headers = {"Idempotency-Key": "backlog-http-correction"}
        corrected = await client.put(put_url, json=body, headers=headers)
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["data"]["revision"] == item["revision"] + 1
        assert corrected.json()["data"]["status"] == status
        assert corrected.json()["data"]["corrected"] is True
        replay = await client.put(put_url, json=body, headers=headers)
        assert replay.status_code == 200 and replay.json() == corrected.json()
        conflict = await client.put(put_url, json=body, headers={"Idempotency-Key": "backlog-stale-revision"})
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "CHECKIN_REVISION_CONFLICT"
        remaining = await client.get(url)
        assert remaining.status_code == 200
        remaining_items = remaining.json()["data"]["items"]
        expected_ids = record_ids - {item["checkin_id"]}
        assert {row["checkin_id"] for row in remaining_items} == expected_ids
        assert all(row["revision"] == 1 and row["status"] == "UNCONFIRMED" for row in remaining_items)
        following = await client.get(url, params={"cursor": cursor})
        assert following.status_code == 200
        assert following.json() == remaining.json()
        missing = await client.get(url, params={"cursor": str(uuid4()), "limit": 1})
        assert missing.status_code == 404
        assert missing.json()["code"] == "CHECKIN_CURSOR_NOT_FOUND"
        # Consumer recovery omits the invalid cursor and replaces its page from this response.
        recovered = await client.get(url, params={"limit": 1})
        assert recovered.status_code == 200
        assert recovered.json()["data"]["items"] == remaining_items[:1]
        assert recovered.json()["data"]["next_cursor"] == remaining_items[0]["checkin_id"]
        for response in (first, corrected, replay, conflict, remaining, following, missing, recovered):
            assert response.headers["cache-control"] == "no-store"
    assert await db_session.scalar(select(func.count()).select_from(CheckinAudit)) == 1


async def test_http_multiple_corrections_keep_page_order_and_day_checkin_consistent(
    db_session: AsyncSession, monkeypatch
):
    owner, _, records = await _seed(db_session, count=5)
    owner_id = owner.id
    expected_ids = [str(row[0].id) for row in sorted(records, key=lambda row: (row[1].scheduled_at, row[0].id))]
    await db_session.commit()
    monkeypatch.setitem(fastapi_app.dependency_overrides, get_request_user, lambda: SimpleNamespace(id=owner_id))
    url = "/api/v1/medication-checkins/unconfirmed"
    seen = []
    params = {"limit": "2"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        while True:
            page = await client.get(url, params=params)
            assert page.status_code == 200
            data = page.json()["data"]
            for item in data["items"]:
                seen.append(item["checkin_id"])
                before = await client.get(
                    "/api/v1/medication-occurrences", params={"date": item["scheduled_local_date"]}
                )
                assert before.status_code == 200
                occurrence = next(
                    row for row in before.json()["data"]["occurrences"] if row["occurrence_id"] == item["occurrence_id"]
                )
                assert occurrence["checkin"]["status"] == "UNCONFIRMED"
                assert occurrence["checkin"]["revision"] == item["revision"]
                corrected = await client.put(
                    f"/api/v1/medication-occurrences/{item['occurrence_id']}/check-in",
                    json={"status": "TAKEN" if len(seen) % 2 else "NOT_TAKEN", "expected_revision": item["revision"]},
                    headers={"Idempotency-Key": f"backlog-multiple-{item['checkin_id']}"},
                )
                assert corrected.status_code == 200, corrected.text
                after = await client.get(
                    "/api/v1/medication-occurrences", params={"date": item["scheduled_local_date"]}
                )
                assert after.status_code == 200
                occurrence = next(
                    row for row in after.json()["data"]["occurrences"] if row["occurrence_id"] == item["occurrence_id"]
                )
                assert occurrence["checkin"] == corrected.json()["data"]
                assert occurrence["checkin"]["revision"] == item["revision"] + 1
                for response in (page, before, corrected, after):
                    assert response.headers["cache-control"] == "no-store"
                    assert response.headers["x-trace-id"]
            remaining = await client.get(url)
            assert remaining.status_code == 200
            assert [row["checkin_id"] for row in remaining.json()["data"]["items"]] == expected_ids[len(seen) :]
            if data["next_cursor"] is None:
                break
            params["cursor"] = data["next_cursor"]
        assert (await client.get(url)).json() == {"data": {"items": [], "next_cursor": None}}
    assert seen == expected_ids
    assert await db_session.scalar(select(func.count()).select_from(CheckinAudit)) == 5


def test_actual_openapi_and_frontend_fixture_match_backlog_contract():
    schema = fastapi_app.openapi()
    operation = schema["paths"]["/api/v1/medication-checkins/unconfirmed"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert set(parameters) == {"limit", "cursor"}
    assert parameters["limit"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 20,
        "title": "Limit",
    }
    assert parameters["cursor"]["required"] is False
    assert {"type": "string", "format": "uuid"} in parameters["cursor"]["schema"]["anyOf"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/UnconfirmedCheckinResponse"
    }
    fixture_path = Path(__file__).resolve().parents[4] / "docs/validation/track-b/issue-418-unconfirmed-fixtures.json"
    fixture = json.loads(fixture_path.read_text())
    page = UnconfirmedCheckinResponse.model_validate(fixture["first_page"]).data
    assert set(schema["components"]["schemas"]["UnconfirmedCheckinItem"]["properties"]) == set(
        fixture["first_page"]["data"]["items"][0]
    )
    request = PutMedicationCheckinRequest.model_validate(fixture["correction_request"])
    correction = MedicationCheckinData.model_validate(fixture["correction_response"]["data"])
    assert request.expected_revision == page.items[0].revision
    assert correction.occurrence_id == page.items[0].occurrence_id
    assert correction.revision == page.items[0].revision + 1
    assert UnconfirmedCheckinResponse.model_validate(fixture["empty"]).data.items == []
