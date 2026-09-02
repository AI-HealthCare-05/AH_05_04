from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_jobs import (
    AiJob,
    AiJobStatus,
    AiJobType,
    DomainType,
    OutboxEvent,
    OutboxEventKind,
    OutboxEventStatus,
)
from app.models.users import Gender, User
from app.repositories.async_job_repository import AsyncJobRepository
from app.tests.conftest import test_engine


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    # 다른 repository 테스트와 동일한 savepoint 격리 방식입니다.
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


async def _create_user(session: AsyncSession) -> User:
    user = User(
        email=f"async-job-repo-{uuid4().hex[:12]}@example.com",
        hashed_password="hashed-password",
        name="테스트 사용자",
        gender=Gender.MALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    session.add(user)
    await session.flush()
    return user


async def _create_job(session: AsyncSession, *, user: User) -> AiJob:
    job = AiJob(
        user_id=user.id,
        job_type=AiJobType.OCR,
        status=AiJobStatus.PENDING,
        max_attempts=3,
        available_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    return job


async def test_get_interim_domain_reference_returns_none_without_expected_event(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    job = await _create_job(db_session, user=user)

    reference = await AsyncJobRepository(db_session).get_interim_domain_reference(job=job)

    assert reference is None


async def test_get_interim_domain_reference_reads_expected_outbox_event(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    job = await _create_job(db_session, user=user)
    domain_id = uuid4()
    event = OutboxEvent(
        job_id=job.id,
        attempt=1,
        event_kind=OutboxEventKind.JOB_EXECUTE,
        status=OutboxEventStatus.PENDING,
        domain_type=DomainType.OCR_JOB,
        domain_id=domain_id,
    )
    db_session.add(event)
    await db_session.flush()
    job.expected_event_id = event.event_id
    await db_session.flush()

    reference = await AsyncJobRepository(db_session).get_interim_domain_reference(job=job)

    assert reference == (DomainType.OCR_JOB, domain_id)


async def test_get_interim_domain_reference_returns_none_when_outbox_event_lacks_domain_fields(
    db_session: AsyncSession,
) -> None:
    """domain_type/domain_id가 nullable이라, 접수 시점에 채워지지 않은 Outbox row(예: 이번 세션
    이전에 만들어진 레거시 row)를 가리키면 None을 반환해 잘못된 값을 조립하지 않아야 합니다."""
    user = await _create_user(db_session)
    job = await _create_job(db_session, user=user)
    event = OutboxEvent(
        job_id=job.id,
        attempt=1,
        event_kind=OutboxEventKind.JOB_EXECUTE,
        status=OutboxEventStatus.PENDING,
    )
    db_session.add(event)
    await db_session.flush()
    job.expected_event_id = event.event_id
    await db_session.flush()

    reference = await AsyncJobRepository(db_session).get_interim_domain_reference(job=job)

    assert reference is None
