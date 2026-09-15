import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.databases import get_db_session
from app.dependencies.security import get_request_user
from app.dtos.track_c import BarrierResponseEnvelope, SafetyAssessmentResponse
from app.main import app, fastapi_app
from app.models.async_jobs import IdempotencyRecord
from app.models.medication_schedules import MedicationCheckin, MedicationCheckinStatus, MedicationOccurrenceStatus
from app.models.prescriptions import Prescription
from app.models.track_c import (
    BarrierCode,
    BarrierResponse,
    BarrierResponseStatus,
    SafetyAssessment,
    SafetyDisposition,
    SafetyResponseLevel,
    SupportActionPlan,
    SupportActionPlanStatus,
    SupportCode,
)
from app.models.users import User
from app.repositories.idempotency_repository import IdempotencyRepository
from app.tests.conftest import test_engine
from app.tests.repositories.test_medication_checkin_repository_integration import _create_occurrence
from app.tests.repositories.test_medication_schedule_repository_integration import (
    _create_user_with_self_profile,
    _delete_committed_fixture,
)


@dataclass
class ApiCase:
    client: AsyncClient
    owner: User
    checkin: MedicationCheckin
    checkin_id: UUID
    session: AsyncSession

    async def safety(self, body: dict, *, key: str | None = "track-c-safety-key") -> Response:
        headers = {"Idempotency-Key": key} if key is not None else {}
        return await self.client.post("/api/v1/safety-assessments", json=body, headers=headers)

    async def barrier(self, body: dict, *, key: str | None = "track-c-barrier-key") -> Response:
        headers = {"Idempotency-Key": key} if key is not None else {}
        return await self.client.put(
            f"/api/v1/medication-checkins/{self.checkin_id}/barrier-response",
            json=body,
            headers=headers,
        )


@pytest.fixture
async def case(db_session: AsyncSession) -> AsyncIterator[ApiCase]:
    owner, profile = await _create_user_with_self_profile(db_session, label="track-c-api-owner")
    occurrence = await _create_occurrence(
        db_session,
        owner=owner,
        profile=profile,
        deadline_at=datetime(2026, 9, 10, 4, tzinfo=UTC),
    )
    occurrence.status = MedicationOccurrenceStatus.CLOSED
    checkin = MedicationCheckin(
        occurrence_id=occurrence.id,
        status=MedicationCheckinStatus.NOT_TAKEN,
        taken_at=None,
        revision=1,
    )
    db_session.add(checkin)
    await db_session.commit()
    owner_id = owner.id
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=owner_id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield ApiCase(client=client, owner=owner, checkin=checkin, checkin_id=checkin.id, session=db_session)
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)


def assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert set(response.json()) == {"code", "message", "details", "trace_id"}
    assert response.headers["cache-control"] == "no-store"


def safety_body(case: ApiCase, *, symptoms: object = None, expected_revision: int = 0) -> dict:
    return {
        "medication_checkin_id": str(case.checkin_id),
        "checkin_revision": 1,
        "symptom_codes": [] if symptoms is None else symptoms,
        "expected_revision": expected_revision,
    }


async def test_not_taken_empty_safety_then_answered_barrier_and_replays(case: ApiCase) -> None:
    first = await case.safety(safety_body(case))
    assert first.status_code == 200, first.text
    safety = SafetyAssessmentResponse.model_validate(first.json()).data
    assert safety.medication_checkin_id == case.checkin.id
    assert safety.response_level == SafetyResponseLevel.ROUTINE
    assert safety.safety_disposition == SafetyDisposition.NORMAL
    assert safety.revision == 1

    barrier_body = {
        "response_status": "ANSWERED",
        "barrier_code": "FORGOT",
        "checkin_revision": 1,
        "expected_revision": 0,
    }
    barrier_first = await case.barrier(barrier_body)
    assert barrier_first.status_code == 200, barrier_first.text
    barrier = BarrierResponseEnvelope.model_validate(barrier_first.json()).data
    assert barrier.safety_assessment_id == safety.assessment_id
    assert barrier.barrier_code == BarrierCode.FORGOT
    assert barrier.revision == 1

    assert (await case.safety(safety_body(case))).json() == first.json()
    assert (await case.barrier(barrier_body)).json() == barrier_first.json()
    assert await case.session.scalar(select(func.count()).select_from(SafetyAssessment)) == 1
    assert await case.session.scalar(select(func.count()).select_from(BarrierResponse)) == 1
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 2


async def test_same_key_changed_payload_and_new_key_stale_revision(case: ApiCase) -> None:
    assert (await case.safety(safety_body(case))).status_code == 200
    assert_error(
        await case.safety(safety_body(case, symptoms=["SYNTHETIC_DIFFERENT_CODE"])),
        409,
        "IDEMPOTENCY_KEY_CONFLICT",
    )
    assert_error(
        await case.safety(safety_body(case), key="track-c-new-stale-key"),
        409,
        "SAFETY_ASSESSMENT_REVISION_CONFLICT",
    )
    assert await case.session.scalar(select(func.count()).select_from(SafetyAssessment)) == 1
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1


async def test_safety_and_barrier_corrections_are_append_only(case: ApiCase) -> None:
    assert (await case.safety(safety_body(case))).status_code == 200
    assert (
        await case.barrier(
            {
                "response_status": "ANSWERED",
                "barrier_code": "FORGOT",
                "checkin_revision": 1,
                "expected_revision": 0,
            }
        )
    ).status_code == 200

    corrected_safety = await case.safety(
        safety_body(case, expected_revision=1),
        key="safety-correction-key",
    )
    assert corrected_safety.status_code == 200
    assert corrected_safety.json()["data"]["revision"] == 2
    corrected_barrier = await case.barrier(
        {
            "response_status": "DECLINED",
            "barrier_code": None,
            "checkin_revision": 1,
            "expected_revision": 1,
        },
        key="barrier-correction-key",
    )
    assert corrected_barrier.status_code == 200
    assert corrected_barrier.json()["data"]["revision"] == 2
    assert corrected_barrier.json()["data"]["barrier_code"] is None
    assert await case.session.scalar(select(func.count()).select_from(SafetyAssessment)) == 2
    assert await case.session.scalar(select(func.count()).select_from(BarrierResponse)) == 2


async def test_nonempty_unapproved_symptom_fails_closed_and_blocks_barrier(case: ApiCase) -> None:
    response = await case.safety(safety_body(case, symptoms=["SYNTHETIC_UNAPPROVED_CODE"]))
    assert response.status_code == 200
    assert response.json()["data"]["response_level"] == "UNKNOWN"
    assert response.json()["data"]["safety_disposition"] == "UNKNOWN_RISK"
    assert_error(
        await case.barrier(
            {
                "response_status": "DECLINED",
                "barrier_code": None,
                "checkin_revision": 1,
                "expected_revision": 0,
            }
        ),
        409,
        "SAFETY_FLOW_PRECEDES_BARRIER",
    )
    assert await case.session.scalar(select(func.count()).select_from(BarrierResponse)) == 0


@pytest.mark.parametrize("snapshot_failure", [True, False])
async def test_nonroutine_safety_correction_cancels_active_plan(
    case: ApiCase, monkeypatch: pytest.MonkeyPatch, snapshot_failure: bool
) -> None:
    safety = SafetyAssessment(
        medication_checkin_id=case.checkin.id,
        checkin_revision=1,
        revision=1,
        symptom_codes=[],
        response_level=SafetyResponseLevel.ROUTINE,
        safety_disposition=SafetyDisposition.NORMAL,
        message_code="SYNTHETIC_ROUTINE",
        copy_version="synthetic-v1",
        source_version="synthetic-v1",
    )
    case.session.add(safety)
    await case.session.flush()
    barrier = BarrierResponse(
        medication_checkin_id=case.checkin.id,
        checkin_revision=1,
        safety_assessment_id=safety.id,
        revision=1,
        response_status=BarrierResponseStatus.ANSWERED,
        barrier_code=BarrierCode.FORGOT,
    )
    case.session.add(barrier)
    await case.session.flush()
    plan = SupportActionPlan(
        barrier_response_id=barrier.id,
        support_code=SupportCode.REMINDER_SETUP,
        rule_version="synthetic-v1",
        copy_version="synthetic-v1",
        action_config_snapshot={},
        status=SupportActionPlanStatus.ACTIVE,
    )
    case.session.add(plan)
    await case.session.commit()

    if snapshot_failure:
        monkeypatch.setattr("app.services.idempotency.SNAPSHOT_SIZE_CAP_BYTES", 1)
    response = await case.safety(
        safety_body(case, symptoms=["SYNTHETIC_UNAPPROVED_CODE"], expected_revision=1),
        key="safety-nonroutine-correction",
    )
    await case.session.refresh(plan)
    if snapshot_failure:
        assert_error(response, 503, "IDEMPOTENCY_RESPONSE_TOO_LARGE")
        assert plan.status == SupportActionPlanStatus.ACTIVE
        assert plan.cancelled_at is None
        assert await case.session.scalar(select(func.count()).select_from(SafetyAssessment)) == 1
    else:
        assert response.status_code == 200, response.text
        assert plan.status == SupportActionPlanStatus.CANCELLED
        assert plan.cancelled_at is not None
    assert await case.session.scalar(select(func.count()).select_from(BarrierResponse)) == 1


async def test_stale_checkin_revision_and_safety_revision_do_not_mutate(case: ApiCase) -> None:
    stale = safety_body(case)
    stale["checkin_revision"] = 2
    assert_error(await case.safety(stale), 409, "CHECKIN_FLOW_STALE")
    assert (await case.safety(safety_body(case), key="current-safety-key")).status_code == 200
    assert_error(
        await case.safety(safety_body(case), key="stale-safety-revision"),
        409,
        "SAFETY_ASSESSMENT_REVISION_CONFLICT",
    )
    assert await case.session.scalar(select(func.count()).select_from(SafetyAssessment)) == 1


@pytest.mark.parametrize(
    "status",
    [MedicationCheckinStatus.TAKEN, MedicationCheckinStatus.UNCONFIRMED],
)
async def test_non_not_taken_checkin_is_rejected(case: ApiCase, status: MedicationCheckinStatus) -> None:
    case.checkin.status = status
    await case.session.commit()
    assert_error(await case.safety(safety_body(case)), 409, "CHECKIN_FLOW_STALE")


async def test_free_text_and_barrier_status_combinations_are_rejected(case: ApiCase) -> None:
    assert_error(await case.safety(safety_body(case, symptoms="free text")), 422, "FREE_TEXT_SYMPTOM_NOT_SUPPORTED")
    assert_error(
        await case.barrier(
            {
                "response_status": "ANSWERED",
                "barrier_code": None,
                "checkin_revision": 1,
                "expected_revision": 0,
            }
        ),
        422,
        "VALIDATION_FAILED",
    )
    assert await case.session.scalar(select(func.count()).select_from(SafetyAssessment)) == 0
    assert await case.session.scalar(select(func.count()).select_from(BarrierResponse)) == 0


async def test_other_user_and_missing_checkin_are_indistinguishable(case: ApiCase) -> None:
    intruder, _ = await _create_user_with_self_profile(case.session, label="track-c-api-intruder")
    intruder_id = intruder.id
    await case.session.commit()
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=intruder_id)
    hidden = await case.safety(safety_body(case))
    missing_body = safety_body(case)
    missing_body["medication_checkin_id"] = str(uuid4())
    missing = await case.safety(missing_body)
    assert_error(hidden, 404, "MEDICATION_CHECKIN_NOT_FOUND")
    assert_error(missing, 404, "MEDICATION_CHECKIN_NOT_FOUND")
    assert {k: v for k, v in hidden.json().items() if k != "trace_id"} == {
        k: v for k, v in missing.json().items() if k != "trace_id"
    }


def test_openapi_track_c_contract() -> None:
    schema = fastapi_app.openapi()
    safety = schema["paths"]["/api/v1/safety-assessments"]["post"]
    barrier = schema["paths"]["/api/v1/medication-checkins/{checkin_id}/barrier-response"]["put"]
    assert safety["operationId"] == "safety-assessment.create"
    assert barrier["operationId"] == "barrier-response.put"
    for operation in (safety, barrier):
        header = next(parameter for parameter in operation["parameters"] if parameter["name"] == "Idempotency-Key")
        assert header["required"] is True
        for status in ["400", "401", "404", "409", "422", "503"]:
            assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith(
                "/ErrorResponse"
            )
    safety_request = schema["components"]["schemas"]["CreateSafetyAssessmentRequest"]
    barrier_request = schema["components"]["schemas"]["PutBarrierResponseRequest"]
    assert safety_request["additionalProperties"] is False
    assert set(safety_request["required"]) == {
        "medication_checkin_id",
        "checkin_revision",
        "symptom_codes",
        "expected_revision",
    }
    assert barrier_request["additionalProperties"] is False
    assert set(barrier_request["required"]) == {"response_status", "checkin_revision", "expected_revision"}


@pytest.mark.parametrize("symptoms", [["아파요"], ["free text"], [""], ["A" * 65]])
async def test_free_text_inside_array_is_not_persisted(case: ApiCase, symptoms: list[str]) -> None:
    assert_error(await case.safety(safety_body(case, symptoms=symptoms)), 422, "FREE_TEXT_SYMPTOM_NOT_SUPPORTED")
    assert await case.session.scalar(select(func.count()).select_from(SafetyAssessment)) == 0


@pytest.mark.parametrize(
    "level,disposition",
    [
        (SafetyResponseLevel.URGENT, SafetyDisposition.URGENT_ROUTED),
        (SafetyResponseLevel.EMERGENCY, SafetyDisposition.EMERGENCY_ROUTED),
        (SafetyResponseLevel.UNKNOWN, SafetyDisposition.UNKNOWN_RISK),
    ],
)
async def test_each_nonroutine_level_blocks_barrier(case: ApiCase, level, disposition) -> None:
    case.session.add(
        SafetyAssessment(
            medication_checkin_id=case.checkin_id,
            checkin_revision=1,
            revision=1,
            symptom_codes=["SYNTHETIC"],
            response_level=level,
            safety_disposition=disposition,
            message_code="SYNTHETIC",
            copy_version="synthetic-v1",
            source_version="synthetic-v1",
        )
    )
    await case.session.commit()
    assert_error(
        await case.barrier(
            {
                "response_status": "DECLINED",
                "checkin_revision": 1,
                "expected_revision": 0,
            }
        ),
        409,
        "SAFETY_FLOW_PRECEDES_BARRIER",
    )


async def test_replay_survives_checkin_correction_but_new_key_is_stale(case: ApiCase) -> None:
    body = safety_body(case)
    first = await case.safety(body)
    case.checkin.revision = 2
    await case.session.commit()
    assert (await case.safety(body)).json() == first.json()
    assert_error(await case.safety(body, key="new-key-after-correction"), 409, "CHECKIN_FLOW_STALE")
    assert_error(
        await case.barrier(
            {
                "response_status": "DECLINED",
                "checkin_revision": 2,
                "expected_revision": 0,
            }
        ),
        409,
        "SAFETY_FLOW_PRECEDES_BARRIER",
    )
    fresh = {**body, "checkin_revision": 2}
    restarted = await case.safety(fresh, key="new-checkin-flow-key")
    assert restarted.status_code == 200, restarted.text
    assert restarted.json()["data"]["revision"] == 1


async def test_snapshot_failure_rolls_back_assessment(case: ApiCase, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.idempotency.SNAPSHOT_SIZE_CAP_BYTES", 1)
    assert_error(await case.safety(safety_body(case)), 503, "IDEMPOTENCY_RESPONSE_TOO_LARGE")
    assert await case.session.scalar(select(func.count()).select_from(SafetyAssessment)) == 0
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


async def test_barrier_precondition_replay_conflict_and_ownership(case: ApiCase) -> None:
    body = {"response_status": "DECLINED", "checkin_revision": 1, "expected_revision": 0}
    assert_error(await case.barrier(body), 409, "SAFETY_FLOW_PRECEDES_BARRIER")
    assert_error(await case.safety(safety_body(case), key=None), 400, "IDEMPOTENCY_KEY_REQUIRED")
    assert (await case.safety(safety_body(case))).status_code == 200
    first = await case.barrier(body)
    assert first.status_code == 200
    changed = {**body, "response_status": "ANSWERED", "barrier_code": "FORGOT"}
    assert_error(await case.barrier(changed), 409, "IDEMPOTENCY_KEY_CONFLICT")
    assert_error(await case.barrier(body, key="new-stale-barrier-key"), 409, "CHECKIN_FLOW_STALE")
    await case.session.refresh(case.checkin)
    case.checkin.revision = 2
    await case.session.commit()
    assert (await case.barrier(body)).json() == first.json()
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    assert_error(await case.barrier(body), 404, "MEDICATION_CHECKIN_NOT_FOUND")


@pytest.mark.parametrize("same_key", [True, False])
@pytest.mark.parametrize("barrier_request", [True, False])
async def test_concurrent_requests_commit_one_revision(
    monkeypatch: pytest.MonkeyPatch, same_key: bool, barrier_request: bool
) -> None:
    async with AsyncSession(test_engine, expire_on_commit=False) as seed:
        owner, profile = await _create_user_with_self_profile(seed, label="concurrent-track-c-api")
        occurrence = await _create_occurrence(
            seed,
            owner=owner,
            profile=profile,
            deadline_at=datetime(2026, 9, 10, 4, tzinfo=UTC),
        )
        occurrence.status = MedicationOccurrenceStatus.CLOSED
        checkin = MedicationCheckin(
            occurrence_id=occurrence.id,
            status=MedicationCheckinStatus.NOT_TAKEN,
            taken_at=None,
            revision=1,
        )
        seed.add(checkin)
        prescription = await seed.scalar(select(Prescription).where(Prescription.profile_id == profile.id))
        assert prescription is not None
        owner_id = owner.id
        profile_id = profile.id
        checkin_id = checkin.id
        schedule_id = occurrence.medication_schedule_id
        document_id = prescription.document_id
        ocr_job_id = prescription.source_ocr_job_id
        prescription_id = prescription.id
        await seed.commit()

        if barrier_request:
            seed.add(
                SafetyAssessment(
                    medication_checkin_id=checkin_id,
                    checkin_revision=1,
                    revision=1,
                    symptom_codes=[],
                    response_level=SafetyResponseLevel.ROUTINE,
                    safety_disposition=SafetyDisposition.NORMAL,
                    message_code="SYNTHETIC_ROUTINE",
                    copy_version="synthetic-v1",
                    source_version="synthetic-v1",
                )
            )
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
        assert previous_session is not None
        fastapi_app.dependency_overrides[get_db_session] = request_session
        fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=owner_id)
        body = {
            "medication_checkin_id": str(checkin_id),
            "checkin_revision": 1,
            "symptom_codes": [],
            "expected_revision": 0,
        }
        if barrier_request:
            body = {"response_status": "DECLINED", "checkin_revision": 1, "expected_revision": 0}
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:

                async def send(index: int) -> Response:
                    return await client.request(
                        "PUT" if barrier_request else "POST",
                        f"/api/v1/medication-checkins/{checkin_id}/barrier-response"
                        if barrier_request
                        else "/api/v1/safety-assessments",
                        json=body,
                        headers={"Idempotency-Key": f"concurrent-key-{0 if same_key else index}"},
                    )

                first, second = await asyncio.wait_for(asyncio.gather(send(0), send(1)), timeout=15)
            if same_key:
                assert first.status_code == second.status_code == 200
                assert first.json() == second.json()
            else:
                assert sorted([first.status_code, second.status_code]) == [200, 409]
                loser = first if first.status_code == 409 else second
                assert_error(
                    loser, 409, "CHECKIN_FLOW_STALE" if barrier_request else "SAFETY_ASSESSMENT_REVISION_CONFLICT"
                )
            assert (
                await seed.scalar(
                    select(func.count())
                    .select_from(SafetyAssessment)
                    .where(SafetyAssessment.medication_checkin_id == checkin_id)
                )
                == 1
            )
            assert (
                await seed.scalar(
                    select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.user_id == owner_id)
                )
                == 1
            )
            assert await seed.scalar(
                select(func.count())
                .select_from(BarrierResponse)
                .where(BarrierResponse.medication_checkin_id == checkin_id)
            ) == int(barrier_request)
        finally:
            fastapi_app.dependency_overrides.pop(get_request_user, None)
            fastapi_app.dependency_overrides[get_db_session] = previous_session
            await seed.execute(delete(IdempotencyRecord).where(IdempotencyRecord.user_id == owner_id))
            await seed.execute(delete(BarrierResponse).where(BarrierResponse.medication_checkin_id == checkin_id))
            await seed.execute(delete(SafetyAssessment).where(SafetyAssessment.medication_checkin_id == checkin_id))
            await seed.execute(delete(MedicationCheckin).where(MedicationCheckin.id == checkin_id))
            await _delete_committed_fixture(
                seed,
                owner_id=owner_id,
                profile_id=profile_id,
                document_id=document_id,
                ocr_job_id=ocr_job_id,
                prescription_id=prescription_id,
                schedule_id=schedule_id,
            )
