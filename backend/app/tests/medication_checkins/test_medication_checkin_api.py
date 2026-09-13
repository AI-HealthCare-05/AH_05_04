from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.security import get_request_user
from app.dtos.medication_checkins import MedicationCheckinResponse
from app.main import app, fastapi_app
from app.models.async_jobs import IdempotencyRecord
from app.models.medication_schedules import (
    CheckinAudit,
    MedicationCheckin,
    MedicationOccurrence,
    MedicationOccurrenceStatus,
)
from app.models.users import User
from app.repositories.medication_checkin_repository import MedicationCheckinRepository
from app.services import idempotency
from app.services.medication_checkins import MedicationCheckinDeadlineScheduler
from app.tests.repositories.test_medication_checkin_repository_integration import _create_occurrence
from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile


@dataclass
class ApiCase:
    client: AsyncClient
    occurrence: MedicationOccurrence
    owner: User
    occurrence_id: UUID
    session: AsyncSession

    async def put(self, body: dict, *, key: str | None = "checkin-test-key") -> Response:
        headers = {"Idempotency-Key": key} if key is not None else {}
        return await self.client.put(
            f"/api/v1/medication-occurrences/{self.occurrence_id}/check-in", json=body, headers=headers
        )


@pytest.fixture
async def case(db_session: AsyncSession) -> AsyncIterator[ApiCase]:
    owner, profile = await _create_user_with_self_profile(db_session, label="api-owner")
    occurrence = await _create_occurrence(
        db_session, owner=owner, profile=profile, deadline_at=datetime(2026, 9, 10, 4, tzinfo=UTC)
    )
    await db_session.commit()
    authenticated_user = SimpleNamespace(id=owner.id)
    fastapi_app.dependency_overrides[get_request_user] = lambda: authenticated_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield ApiCase(client, occurrence, owner, occurrence.id, db_session)
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)


def assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert set(response.json()) == {"code", "message", "details", "trace_id"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-trace-id"] == response.json()["trace_id"]


@pytest.mark.parametrize("status", ["TAKEN", "NOT_TAKEN"])
async def test_create_correct_and_replay_original_snapshot(case: ApiCase, status: str) -> None:
    first_request = {"status": status, "expected_revision": 0}
    first = await case.put(first_request)
    assert first.status_code == 200, first.text
    parsed = MedicationCheckinResponse.model_validate(first.json())
    assert parsed.data.occurrence_id == case.occurrence.id
    assert parsed.data.revision == 1
    assert not parsed.data.corrected
    assert first.headers["cache-control"] == "no-store"
    assert first.headers["x-trace-id"]

    corrected = await case.put({"status": "TAKEN", "expected_revision": 1}, key="checkin-correction-key")
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["data"]["revision"] == 2
    assert corrected.json()["data"]["corrected"] is True
    replay = await case.put(first_request)
    assert replay.status_code == 200
    assert replay.json() == first.json()

    current = await case.session.scalar(select(MedicationCheckin))
    assert current is not None and current.revision == 2
    assert await case.session.scalar(select(func.count()).select_from(CheckinAudit)) == 1
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 2
    assert case.occurrence.status == MedicationOccurrenceStatus.CLOSED


async def test_same_key_changed_request_and_new_key_stale_revision(case: ApiCase) -> None:
    assert (await case.put({"status": "TAKEN", "expected_revision": 0})).status_code == 200
    assert_error(await case.put({"status": "NOT_TAKEN", "expected_revision": 0}), 409, "IDEMPOTENCY_KEY_CONFLICT")
    assert_error(
        await case.put({"status": "TAKEN", "expected_revision": 0}, key="checkin-new-stale-key"),
        409,
        "CHECKIN_REVISION_CONFLICT",
    )
    assert await case.session.scalar(select(func.count()).select_from(CheckinAudit)) == 0
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1


@pytest.mark.parametrize("status", ["TAKEN", "NOT_TAKEN"])
async def test_scheduler_unconfirmed_can_be_corrected(case: ApiCase, status: str) -> None:
    await MedicationCheckinDeadlineScheduler(MedicationCheckinRepository(case.session)).generate_unconfirmed(
        now=datetime(2026, 9, 10, 4, tzinfo=UTC)
    )
    await case.session.commit()
    response = await case.put({"status": status, "expected_revision": 1})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["revision"] == 2
    audit = await case.session.scalar(select(CheckinAudit))
    assert audit is not None and audit.from_status == "UNCONFIRMED" and audit.to_status == status


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"status": "UNCONFIRMED", "expected_revision": 0}, "CHECKIN_STATUS_NOT_USER_SETTABLE"),
        ({"status": "SKIPPED", "expected_revision": 0}, "VALIDATION_FAILED"),
        ({"status": "TAKEN"}, "VALIDATION_FAILED"),
        ({"status": "TAKEN", "expected_revision": -1}, "VALIDATION_FAILED"),
        ({"status": "TAKEN", "expected_revision": True}, "VALIDATION_FAILED"),
        ({"status": "TAKEN", "expected_revision": "0"}, "VALIDATION_FAILED"),
        ({"status": "TAKEN", "expected_revision": 0, "reason_code": "synthetic"}, "VALIDATION_FAILED"),
        ({"status": "TAKEN", "expected_revision": 0, "taken_at": "2026-09-10T12:00:00"}, "VALIDATION_FAILED"),
        ({"status": "NOT_TAKEN", "expected_revision": 0, "taken_at": "2026-09-10T12:00:00Z"}, "VALIDATION_FAILED"),
    ],
)
async def test_invalid_input_never_mutates(case: ApiCase, body: dict, code: str) -> None:
    assert_error(await case.put(body), 422, code)
    assert await case.session.scalar(select(func.count()).select_from(MedicationCheckin)) == 0
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


@pytest.mark.parametrize(("key", "code"), [(None, "IDEMPOTENCY_KEY_REQUIRED"), (" ", "IDEMPOTENCY_KEY_INVALID")])
async def test_header_validation(case: ApiCase, key: str | None, code: str) -> None:
    assert_error(await case.put({"status": "TAKEN", "expected_revision": 0}, key=key), 400, code)


async def test_utc_normalization_and_equivalent_replay(case: ApiCase) -> None:
    body = {"status": "TAKEN", "taken_at": "2026-09-10T12:00:00+09:00", "expected_revision": 0}
    first = await case.put(body)
    assert first.status_code == 200, first.text
    assert first.json()["data"]["taken_at"] == "2026-09-10T03:00:00Z"
    body["taken_at"] = "2026-09-10T03:00:00Z"
    replay = await case.put(body)
    assert replay.status_code == 200 and replay.json() == first.json()


async def test_cancelled_occurrence_is_not_mutated(case: ApiCase) -> None:
    case.occurrence.status = MedicationOccurrenceStatus.CANCELLED
    await case.session.commit()
    assert_error(await case.put({"status": "TAKEN", "expected_revision": 0}), 409, "OCCURRENCE_CANCELLED")
    assert await case.session.scalar(select(func.count()).select_from(MedicationCheckin)) == 0
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


async def test_other_user_and_missing_occurrence_have_same_404(case: ApiCase) -> None:
    intruder, _ = await _create_user_with_self_profile(case.session, label="api-intruder")
    await case.session.commit()
    # The owner's snapshot must never bypass ownership for another authenticated user.
    assert (await case.put({"status": "TAKEN", "expected_revision": 0})).status_code == 200
    authenticated_intruder = SimpleNamespace(id=intruder.id)
    fastapi_app.dependency_overrides[get_request_user] = lambda: authenticated_intruder
    hidden = await case.put({"status": "TAKEN", "expected_revision": 0})
    missing = await case.client.put(
        f"/api/v1/medication-occurrences/{uuid4()}/check-in",
        json={"status": "TAKEN", "expected_revision": 0},
        headers={"Idempotency-Key": "checkin-test-key"},
    )
    assert_error(hidden, 404, "MEDICATION_OCCURRENCE_NOT_FOUND")
    assert_error(missing, 404, "MEDICATION_OCCURRENCE_NOT_FOUND")
    assert {k: v for k, v in hidden.json().items() if k != "trace_id"} == {
        k: v for k, v in missing.json().items() if k != "trace_id"
    }


async def test_snapshot_failure_rolls_back_checkin_and_occurrence(
    case: ApiCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(idempotency, "SNAPSHOT_SIZE_CAP_BYTES", 1)
    assert_error(await case.put({"status": "TAKEN", "expected_revision": 0}), 503, "IDEMPOTENCY_RESPONSE_TOO_LARGE")
    await case.session.refresh(case.occurrence)
    assert case.occurrence.status == MedicationOccurrenceStatus.PENDING
    assert await case.session.scalar(select(func.count()).select_from(MedicationCheckin)) == 0
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


async def test_authentication_is_required(case: ApiCase) -> None:
    fastapi_app.dependency_overrides.pop(get_request_user)
    response = await case.put({"status": "TAKEN", "expected_revision": 0})
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-trace-id"]


def test_openapi_checkin_contract() -> None:
    schema = fastapi_app.openapi()
    operation = schema["paths"]["/api/v1/medication-occurrences/{occurrence_id}/check-in"]["put"]
    assert operation["operationId"] == "medication-checkin.put"
    header = next(p for p in operation["parameters"] if p["name"] == "Idempotency-Key")
    assert header["required"] is True
    request = schema["components"]["schemas"]["PutMedicationCheckinRequest"]
    assert request["additionalProperties"] is False
    assert request["properties"]["status"]["enum"] == ["TAKEN", "NOT_TAKEN"]
    assert "reason_code" not in request["properties"]
    assert set(request["required"]) == {"status", "expected_revision"}
    for status in ["400", "401", "404", "409", "422", "503"]:
        assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/ErrorResponse"
        )
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/MedicationCheckinResponse"
    )


async def test_concurrent_same_key_first_requests_commit_one_checkin(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from sqlalchemy import delete

    from app.core.db.databases import get_db_session
    from app.models.prescriptions import Prescription
    from app.repositories.idempotency_repository import IdempotencyRepository
    from app.tests.conftest import test_engine
    from app.tests.repositories.test_medication_schedule_repository_integration import _delete_committed_fixture

    async with AsyncSession(test_engine, expire_on_commit=False) as seed:
        owner, profile = await _create_user_with_self_profile(seed, label="concurrent-api")
        occurrence = await _create_occurrence(
            seed, owner=owner, profile=profile, deadline_at=datetime(2026, 9, 10, 4, tzinfo=UTC)
        )
        prescription = await seed.scalar(select(Prescription).where(Prescription.profile_id == profile.id))
        assert prescription is not None
        await seed.commit()

        async def request_session() -> AsyncIterator[AsyncSession]:
            async with AsyncSession(test_engine, expire_on_commit=False) as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        original_find = IdempotencyRepository.find_sync_idempotency_record
        both_requests_read = asyncio.Event()
        initial_reads = 0

        async def synchronized_find(repository, **kwargs):
            nonlocal initial_reads
            result = await original_find(repository, **kwargs)
            if initial_reads < 2:
                initial_reads += 1
                if initial_reads == 2:
                    both_requests_read.set()
                await asyncio.wait_for(both_requests_read.wait(), timeout=10)
            return result

        monkeypatch.setattr(IdempotencyRepository, "find_sync_idempotency_record", synchronized_find)
        previous_session = fastapi_app.dependency_overrides.get(get_db_session)
        fastapi_app.dependency_overrides[get_db_session] = request_session
        fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=owner.id)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:

                async def send() -> Response:
                    return await client.put(
                        f"/api/v1/medication-occurrences/{occurrence.id}/check-in",
                        json={"status": "TAKEN", "expected_revision": 0},
                        headers={"Idempotency-Key": "concurrent-checkin-key"},
                    )

                first, second = await asyncio.wait_for(asyncio.gather(send(), send()), timeout=15)
            assert first.status_code == second.status_code == 200
            assert first.json() == second.json()
            assert (
                await seed.scalar(
                    select(func.count())
                    .select_from(MedicationCheckin)
                    .where(MedicationCheckin.occurrence_id == occurrence.id)
                )
                == 1
            )
            assert (
                await seed.scalar(
                    select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.user_id == owner.id)
                )
                == 1
            )
        finally:
            fastapi_app.dependency_overrides.pop(get_request_user, None)
            if previous_session is not None:
                fastapi_app.dependency_overrides[get_db_session] = previous_session
            else:
                fastapi_app.dependency_overrides.pop(get_db_session, None)
            await seed.execute(delete(IdempotencyRecord).where(IdempotencyRecord.user_id == owner.id))
            await seed.execute(delete(MedicationCheckin).where(MedicationCheckin.occurrence_id == occurrence.id))
            await _delete_committed_fixture(
                seed,
                owner_id=owner.id,
                profile_id=profile.id,
                document_id=prescription.document_id,
                ocr_job_id=prescription.source_ocr_job_id,
                prescription_id=prescription.id,
                schedule_id=occurrence.medication_schedule_id,
            )
