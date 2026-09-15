import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.databases import get_db_session
from app.dependencies.security import get_request_user
from app.main import app, fastapi_app
from app.models.async_jobs import IdempotencyRecord
from app.models.medication_schedules import (
    CheckinAudit,
    MedicationCheckin,
    MedicationCheckinStatus,
    MedicationOccurrenceStatus,
)
from app.models.prescriptions import Prescription
from app.models.track_c import (
    BarrierCode,
    BarrierResponse,
    BarrierResponseStatus,
    SafetyAssessment,
    SafetyDisposition,
    SafetyResponseLevel,
    SupportActionPlan,
)
from app.repositories.idempotency_repository import IdempotencyRepository
from app.services.track_c_handler_config import ACTIVE_COPY_VERSION, ACTIVE_RULE_VERSION
from app.tests.conftest import test_engine
from app.tests.repositories.test_medication_checkin_repository_integration import _create_occurrence
from app.tests.repositories.test_medication_schedule_repository_integration import (
    _create_user_with_self_profile,
    _delete_committed_fixture,
)


@pytest.mark.parametrize("same_key,correct_checkin", [(True, False), (False, False), (False, True)])
async def test_concurrent_plan_requests_and_checkin_correction_serialize(
    monkeypatch: pytest.MonkeyPatch, same_key: bool, correct_checkin: bool
) -> None:
    async with AsyncSession(test_engine, expire_on_commit=False) as seed:
        owner, profile = await _create_user_with_self_profile(seed, label="support-concurrency-synthetic")
        occurrence = await _create_occurrence(
            seed, owner=owner, profile=profile, deadline_at=datetime(2026, 9, 10, 4, tzinfo=UTC)
        )
        occurrence.status = MedicationOccurrenceStatus.CLOSED
        checkin = MedicationCheckin(occurrence_id=occurrence.id, status=MedicationCheckinStatus.NOT_TAKEN, revision=1)
        seed.add(checkin)
        await seed.flush()
        safety = SafetyAssessment(
            medication_checkin_id=checkin.id,
            checkin_revision=1,
            revision=1,
            symptom_codes=[],
            response_level=SafetyResponseLevel.ROUTINE,
            safety_disposition=SafetyDisposition.NORMAL,
            message_code="SYNTHETIC_ROUTINE",
            copy_version="synthetic-v1",
            source_version="synthetic-v1",
        )
        seed.add(safety)
        await seed.flush()
        barrier = BarrierResponse(
            medication_checkin_id=checkin.id,
            checkin_revision=1,
            revision=1,
            safety_assessment_id=safety.id,
            response_status=BarrierResponseStatus.ANSWERED,
            barrier_code=BarrierCode.FORGOT,
        )
        seed.add(barrier)
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
        ready = asyncio.Event()
        reads = 0

        async def synchronized_find(repository, **kwargs):
            nonlocal reads
            result = await original_find(repository, **kwargs)
            if reads < 2:
                reads += 1
                if reads == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), timeout=10)
            return result

        monkeypatch.setattr(IdempotencyRepository, "find_sync_idempotency_record", synchronized_find)
        previous_session = fastapi_app.dependency_overrides[get_db_session]
        fastapi_app.dependency_overrides[get_db_session] = request_session
        fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=owner.id)
        body = {
            "barrier_response_id": str(barrier.id),
            "support_code": "REMINDER_SETUP",
            "rule_version": ACTIVE_RULE_VERSION,
            "copy_version": ACTIVE_COPY_VERSION,
            "confirmed": True,
        }
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                first, second = await asyncio.wait_for(
                    asyncio.gather(
                        *(
                            client.put(
                                f"/api/v1/medication-occurrences/{occurrence.id}/check-in",
                                json={"status": "NOT_TAKEN", "expected_revision": 1},
                                headers={"Idempotency-Key": "concurrent-checkin-correction"},
                            )
                            if correct_checkin and index == 1
                            else client.post(
                                "/api/v1/support-action-plans",
                                json=body,
                                headers={"Idempotency-Key": f"concurrent-plan-{0 if same_key else index}"},
                            )
                            for index in range(2)
                        )
                    ),
                    timeout=15,
                )
            expected_plans = 1
            expected_snapshots = 1
            if correct_checkin:
                assert second.status_code == 200, second.text
                assert first.status_code in (200, 409), first.text
                expected_plans = int(first.status_code == 200)
                expected_snapshots += expected_plans
                if first.status_code == 409:
                    assert first.json()["code"] == "CHECKIN_FLOW_STALE"
                else:
                    assert (
                        await seed.scalar(
                            select(SupportActionPlan.status).where(SupportActionPlan.barrier_response_id == barrier.id)
                        )
                        == "CANCELLED"
                    )
            elif same_key:
                assert first.status_code == second.status_code == 200, (first.text, second.text)
                assert first.json() == second.json()
            else:
                assert sorted([first.status_code, second.status_code]) == [200, 409], (first.text, second.text)
                loser = first if first.status_code == 409 else second
                assert loser.json()["code"] == "ACTION_PLAN_ALREADY_ACTIVE"
            assert (
                await seed.scalar(
                    select(func.count())
                    .select_from(SupportActionPlan)
                    .where(SupportActionPlan.barrier_response_id == barrier.id)
                )
                == expected_plans
            )
            assert (
                await seed.scalar(
                    select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.user_id == owner.id)
                )
                == expected_snapshots
            )
        finally:
            fastapi_app.dependency_overrides[get_db_session] = previous_session
            fastapi_app.dependency_overrides.pop(get_request_user, None)
            await seed.execute(delete(IdempotencyRecord).where(IdempotencyRecord.user_id == owner.id))
            await seed.execute(delete(SupportActionPlan).where(SupportActionPlan.barrier_response_id == barrier.id))
            await seed.execute(delete(BarrierResponse).where(BarrierResponse.id == barrier.id))
            await seed.execute(delete(SafetyAssessment).where(SafetyAssessment.id == safety.id))
            await seed.execute(delete(CheckinAudit).where(CheckinAudit.checkin_id == checkin.id))
            await seed.execute(delete(MedicationCheckin).where(MedicationCheckin.id == checkin.id))
            await _delete_committed_fixture(
                seed,
                owner_id=owner.id,
                profile_id=profile.id,
                document_id=prescription.document_id,
                ocr_job_id=prescription.source_ocr_job_id,
                prescription_id=prescription.id,
                schedule_id=occurrence.medication_schedule_id,
            )
