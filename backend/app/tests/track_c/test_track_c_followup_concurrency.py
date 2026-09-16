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
    ActionPlanFollowup,
    ActionPlanFollowupAudit,
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


def assert_followup_race(rival, first, second):
    if rival in ("same_key", "checkin", "safety"):
        assert first.status_code == second.status_code == 200, (first.text, second.text)
        if rival == "same_key":
            assert first.json() == second.json()
    else:
        assert sorted([first.status_code, second.status_code]) == [200, 409], (first.text, second.text)
        loser = first if first.status_code == 409 else second
        expected = (
            "IDEMPOTENCY_KEY_CONFLICT" if rival == "changed_payload" else "ACTION_PLAN_FOLLOWUP_REVISION_CONFLICT"
        )
        assert loser.json()["code"] == expected


@pytest.mark.parametrize("rival", ["same_key", "new_key", "correction", "changed_payload", "checkin", "safety"])
async def test_followup_race_with_independent_transactions(monkeypatch: pytest.MonkeyPatch, rival: str) -> None:
    async with AsyncSession(test_engine, expire_on_commit=False) as seed:
        owner, profile = await _create_user_with_self_profile(seed, label="followup-concurrency-synthetic")
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

        previous_session = fastapi_app.dependency_overrides[get_db_session]
        fastapi_app.dependency_overrides[get_db_session] = request_session
        fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=owner.id)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                created = await client.post(
                    "/api/v1/support-action-plans",
                    json={
                        "barrier_response_id": str(barrier.id),
                        "support_code": "REMINDER_SETUP",
                        "rule_version": ACTIVE_RULE_VERSION,
                        "copy_version": ACTIVE_COPY_VERSION,
                        "confirmed": True,
                    },
                    headers={"Idempotency-Key": "terminal-race-create-plan"},
                )
                assert created.status_code == 200, created.text
                plan_id = created.json()["data"]["support_action_plan_id"]
                completed = await client.patch(
                    f"/api/v1/support-action-plans/{plan_id}",
                    json={"status": "COMPLETED", "confirmed": True},
                    headers={"Idempotency-Key": "followup-race-complete-key"},
                )
                assert completed.status_code == 200, completed.text
                url = f"/api/v1/support-action-plans/{plan_id}/followups"
                if rival == "correction":
                    initial = await client.post(
                        url,
                        json={"response": "NOT_SURE", "expected_revision": 0},
                        headers={"Idempotency-Key": "followup-race-initial-key"},
                    )
                    assert initial.status_code == 200, initial.text
                revision = 1 if rival == "correction" else 0
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
                first_request = client.post(
                    url,
                    json={"response": "HELPED", "expected_revision": revision},
                    headers={"Idempotency-Key": "followup-race-first-key"},
                )
                if rival == "checkin":
                    other = client.put(
                        f"/api/v1/medication-occurrences/{occurrence.id}/check-in",
                        json={"status": "TAKEN", "expected_revision": 1},
                        headers={"Idempotency-Key": "terminal-race-correction"},
                    )
                elif rival == "safety":
                    other = client.post(
                        "/api/v1/safety-assessments",
                        json={
                            "medication_checkin_id": str(checkin.id),
                            "checkin_revision": 1,
                            "expected_revision": 1,
                            "symptom_codes": ["SYNTHETIC"],
                        },
                        headers={"Idempotency-Key": "terminal-race-new-safety"},
                    )
                else:
                    other = client.post(
                        url,
                        json={
                            "response": "HELPED" if rival == "same_key" else "NOT_HELPED",
                            "expected_revision": revision,
                        },
                        headers={
                            "Idempotency-Key": "followup-race-first-key"
                            if rival in ("same_key", "changed_payload")
                            else "followup-race-second-key"
                        },
                    )
                first, second = await asyncio.wait_for(asyncio.gather(first_request, other), timeout=15)
                assert_followup_race(rival, first, second)
                final = await client.get(url)
                assert final.status_code == 200
                assert final.json()["data"]["revision"] == revision + 1
                accepted = first if first.status_code == 200 else second
                assert final.json() == accepted.json()
                plan = await client.get(f"/api/v1/support-action-plans/{plan_id}")
                assert plan.json() == completed.json()
                assert (
                    await seed.scalar(
                        select(func.count())
                        .select_from(ActionPlanFollowup)
                        .where(ActionPlanFollowup.support_action_plan_id == plan_id)
                    )
                    == 1
                )
                audit_rows = (
                    await seed.scalars(
                        select(ActionPlanFollowupAudit)
                        .join(ActionPlanFollowup)
                        .where(ActionPlanFollowup.support_action_plan_id == plan_id)
                    )
                ).all()
                assert len(audit_rows) == revision
                if audit_rows:
                    assert (
                        audit_rows[0].from_revision,
                        audit_rows[0].to_revision,
                        audit_rows[0].from_response,
                        audit_rows[0].to_response,
                    ) == (1, 2, "NOT_SURE", final.json()["data"]["response"])
                expected_snapshots = 3 + revision + int(rival in ("checkin", "safety"))
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
            plan_ids = select(SupportActionPlan.id).where(SupportActionPlan.barrier_response_id == barrier.id)
            followup_ids = select(ActionPlanFollowup.id).where(ActionPlanFollowup.support_action_plan_id.in_(plan_ids))
            await seed.execute(
                delete(ActionPlanFollowupAudit).where(ActionPlanFollowupAudit.followup_id.in_(followup_ids))
            )
            await seed.execute(
                delete(ActionPlanFollowup).where(ActionPlanFollowup.support_action_plan_id.in_(plan_ids))
            )
            await seed.execute(delete(SupportActionPlan).where(SupportActionPlan.barrier_response_id == barrier.id))
            await seed.execute(delete(BarrierResponse).where(BarrierResponse.medication_checkin_id == checkin.id))
            await seed.execute(delete(SafetyAssessment).where(SafetyAssessment.medication_checkin_id == checkin.id))
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
