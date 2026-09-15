import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.db.databases import Base
from app.core.errors import ApiError
from app.models.medication_schedules import CheckinAudit, MedicationCheckin, MedicationCheckinStatus
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
from app.repositories.medication_checkin_repository import MedicationCheckinRepository
from app.repositories.track_c_storage_repository import TrackCStorageRepository
from app.services.medication_checkins import MedicationCheckinService
from app.services.track_c_revision_invalidation import TrackCCheckinRevisionInvalidation
from app.tests.conftest import test_engine
from app.tests.db_extensions import EXTENSION_SCHEMA, ensure_trigram_extension, ensure_vector_extension
from app.tests.repositories.test_medication_checkin_repository_integration import _create_occurrence
from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile

INVALIDATED_AT = datetime(2026, 9, 15, 4, tzinfo=UTC)


async def _seed_not_taken_with_history(session: AsyncSession):
    owner, profile = await _create_user_with_self_profile(session, label="c4-invalidation")
    occurrence = await _create_occurrence(
        session,
        owner=owner,
        profile=profile,
        deadline_at=datetime(2026, 9, 15, 8, tzinfo=UTC),
    )
    checkins = MedicationCheckinRepository(session)
    service = MedicationCheckinService(
        checkins,
        revision_invalidation=TrackCCheckinRevisionInvalidation(TrackCStorageRepository(session)),
    )
    await service.put_owned(
        occurrence_id=occurrence.id,
        user_id=owner.id,
        status=MedicationCheckinStatus.NOT_TAKEN,
        taken_at=None,
        expected_revision=0,
    )
    checkin = await session.scalar(select(MedicationCheckin).where(MedicationCheckin.occurrence_id == occurrence.id))
    assert checkin is not None
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
    session.add(safety)
    await session.flush()
    barrier = BarrierResponse(
        medication_checkin_id=checkin.id,
        checkin_revision=1,
        safety_assessment_id=safety.id,
        revision=1,
        response_status=BarrierResponseStatus.ANSWERED,
        barrier_code=BarrierCode.FORGOT,
    )
    session.add(barrier)
    await session.flush()
    active = SupportActionPlan(
        barrier_response_id=barrier.id,
        support_code=SupportCode.REMINDER_SETUP,
        rule_version="synthetic-v1",
        copy_version="synthetic-v1",
        action_config_snapshot={},
        status=SupportActionPlanStatus.ACTIVE,
    )
    completed = SupportActionPlan(
        barrier_response_id=barrier.id,
        support_code=SupportCode.INSTRUCTION_REVIEW,
        rule_version="synthetic-v1",
        copy_version="synthetic-v1",
        action_config_snapshot={},
        status=SupportActionPlanStatus.COMPLETED,
        completed_at=datetime(2026, 9, 15, 3, tzinfo=UTC),
    )
    session.add_all([active, completed])
    await session.flush()
    return owner, occurrence, checkin, service, active, completed


@pytest.fixture
async def revision_race_database() -> AsyncIterator[tuple]:
    schema = "checkin_revision_race_" + uuid4().hex
    admin = create_async_engine(test_engine.url, poolclass=NullPool)
    engine = create_async_engine(
        test_engine.url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},{EXTENSION_SCHEMA}"}},
    )
    try:
        async with admin.begin() as connection:
            await ensure_trigram_extension(connection, EXTENSION_SCHEMA)
            await ensure_vector_extension(connection, EXTENSION_SCHEMA)
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory.begin() as session:
            owner, occurrence, checkin, _, _, _ = await _seed_not_taken_with_history(session)
            user_id, occurrence_id, checkin_id = owner.id, occurrence.id, checkin.id
        yield factory, user_id, occurrence_id, checkin_id
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await admin.dispose()


@pytest.mark.parametrize("new_status", [MedicationCheckinStatus.TAKEN, MedicationCheckinStatus.NOT_TAKEN])
async def test_correction_cancels_only_active_plan_and_preserves_history(
    db_session: AsyncSession,
    new_status: MedicationCheckinStatus,
) -> None:
    owner, occurrence, checkin, service, active, completed = await _seed_not_taken_with_history(db_session)

    result = await service.put_owned(
        occurrence_id=occurrence.id,
        user_id=owner.id,
        status=new_status,
        taken_at=INVALIDATED_AT if new_status == MedicationCheckinStatus.TAKEN else None,
        expected_revision=1,
        changed_at=INVALIDATED_AT,
    )

    assert result.revision == 2 and result.status == new_status
    assert active.status == SupportActionPlanStatus.CANCELLED
    assert active.cancelled_at == INVALIDATED_AT
    assert completed.status == SupportActionPlanStatus.COMPLETED
    assert completed.cancelled_at is None
    assert await db_session.scalar(select(func.count()).select_from(SafetyAssessment)) == 1
    assert await db_session.scalar(select(func.count()).select_from(BarrierResponse)) == 1
    assert await db_session.scalar(select(func.count()).select_from(SupportActionPlan)) == 2
    assert await db_session.scalar(select(func.count()).select_from(CheckinAudit)) == 1
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(SafetyAssessment)
            .where(
                SafetyAssessment.medication_checkin_id == checkin.id,
                SafetyAssessment.checkin_revision == 2,
            )
        )
        == 0
    )


async def test_duplicate_invalidation_is_idempotent(db_session: AsyncSession) -> None:
    _, _, checkin, _, active, _ = await _seed_not_taken_with_history(db_session)
    adapter = TrackCCheckinRevisionInvalidation(TrackCStorageRepository(db_session))

    await adapter.invalidate_for_checkin_revision(
        checkin_id=checkin.id,
        invalidated_revision=1,
        invalidated_at=INVALIDATED_AT,
    )
    await adapter.invalidate_for_checkin_revision(
        checkin_id=checkin.id,
        invalidated_revision=1,
        invalidated_at=datetime(2026, 9, 15, 5, tzinfo=UTC),
    )

    assert active.status == SupportActionPlanStatus.CANCELLED
    assert active.cancelled_at == INVALIDATED_AT


async def test_invalidation_failure_rolls_back_checkin_audit_and_plan(db_session: AsyncSession) -> None:
    owner, occurrence, checkin, _, active, _ = await _seed_not_taken_with_history(db_session)
    checkin_id = checkin.id
    plan_id = active.id
    repository = TrackCStorageRepository(db_session)

    class FailingInvalidation(TrackCCheckinRevisionInvalidation):
        async def invalidate_for_checkin_revision(self, **kwargs) -> None:
            await super().invalidate_for_checkin_revision(**kwargs)
            raise RuntimeError("synthetic invalidation failure")

    service = MedicationCheckinService(
        MedicationCheckinRepository(db_session),
        revision_invalidation=FailingInvalidation(repository),
    )
    with pytest.raises(RuntimeError, match="synthetic invalidation failure"):
        async with db_session.begin_nested():
            await service.put_owned(
                occurrence_id=occurrence.id,
                user_id=owner.id,
                status=MedicationCheckinStatus.TAKEN,
                taken_at=INVALIDATED_AT,
                expected_revision=1,
                changed_at=INVALIDATED_AT,
            )

    db_session.expire_all()
    persisted_checkin = await db_session.get(MedicationCheckin, checkin_id)
    persisted_plan = await db_session.get(SupportActionPlan, plan_id)
    assert persisted_checkin is not None
    assert (persisted_checkin.status, persisted_checkin.revision) == (MedicationCheckinStatus.NOT_TAKEN, 1)
    assert persisted_plan is not None
    assert persisted_plan.status == SupportActionPlanStatus.ACTIVE
    assert persisted_plan.cancelled_at is None
    assert await db_session.scalar(select(func.count()).select_from(CheckinAudit)) == 0


async def test_concurrent_corrections_serialize_without_deadlock(revision_race_database) -> None:
    factory, user_id, occurrence_id, checkin_id = revision_race_database
    ready = asyncio.Barrier(2)

    async def correct(status: MedicationCheckinStatus):
        async with factory() as session:
            service = MedicationCheckinService(
                MedicationCheckinRepository(session),
                revision_invalidation=TrackCCheckinRevisionInvalidation(TrackCStorageRepository(session)),
            )
            await ready.wait()
            try:
                result = await service.put_owned(
                    occurrence_id=occurrence_id,
                    user_id=user_id,
                    status=status,
                    taken_at=INVALIDATED_AT if status == MedicationCheckinStatus.TAKEN else None,
                    expected_revision=1,
                    changed_at=INVALIDATED_AT,
                )
                await session.commit()
                return result
            except ApiError as error:
                await session.rollback()
                return error

    results = await asyncio.wait_for(
        asyncio.gather(
            correct(MedicationCheckinStatus.TAKEN),
            correct(MedicationCheckinStatus.NOT_TAKEN),
        ),
        timeout=10,
    )

    assert sum(isinstance(result, ApiError) for result in results) == 1
    failure = next(result for result in results if isinstance(result, ApiError))
    assert failure.code == "CHECKIN_REVISION_CONFLICT"
    async with factory() as session:
        checkin = await session.get(MedicationCheckin, checkin_id)
        assert checkin is not None and checkin.revision == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SupportActionPlan)
                .where(SupportActionPlan.status == SupportActionPlanStatus.CANCELLED)
            )
            == 1
        )
        assert await session.scalar(select(func.count()).select_from(CheckinAudit)) == 1
