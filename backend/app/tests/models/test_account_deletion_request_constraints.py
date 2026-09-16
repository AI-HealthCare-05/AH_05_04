from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.users import Gender, User
from app.tests.conftest import test_engine


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    # test_idempotency_repository.py와 동일한 savepoint 격리 방식입니다.
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
        email=f"adr-{uuid4().hex[:12]}@example.com",
        hashed_password="hashed-password",
        name="테스트 사용자",
        gender=Gender.FEMALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    session.add(user)
    await session.flush()
    return user


async def _insert_account_deletion_request(
    session: AsyncSession, *, user_id: UUID, status: str, retry_count: int = 0
) -> None:
    # 앱이 아니라 DB 자체가 값을 거부하는지 확인하는 테스트라, ORM Enum의 Python 레벨 검증을
    # 우회하도록 raw SQL로 삽입합니다.
    await session.execute(
        text(
            "INSERT INTO account_deletion_request "
            "(id, user_id, status, requested_at, retry_count, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :user_id, :status, now(), :retry_count, now(), now())"
        ),
        {"user_id": str(user_id), "status": status, "retry_count": retry_count},
    )


async def test_status_check_constraint_rejects_value_outside_allowlist(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)

    with pytest.raises(IntegrityError, match="chk_account_deletion_request_status"):
        await _insert_account_deletion_request(db_session, user_id=user.id, status="BOGUS")


async def test_retry_count_check_constraint_rejects_negative_value(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)

    with pytest.raises(IntegrityError, match="chk_account_deletion_request_retry_count"):
        await _insert_account_deletion_request(db_session, user_id=user.id, status="PENDING", retry_count=-1)


async def test_partial_unique_index_rejects_second_active_request_for_same_user(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    await _insert_account_deletion_request(db_session, user_id=user.id, status="PENDING")
    await db_session.flush()

    with pytest.raises(IntegrityError, match="uq_account_deletion_request_active_per_user"):
        await _insert_account_deletion_request(db_session, user_id=user.id, status="IN_PROGRESS")


async def test_partial_unique_index_allows_completed_request_alongside_active_one(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    await _insert_account_deletion_request(db_session, user_id=user.id, status="PENDING")
    await db_session.flush()

    # COMPLETED는 partial index 대상이 아니라 기존 활성 요청과 공존할 수 있어야 합니다
    # (예: 과거 완료 이력을 보존한 채 새 활성 요청이 생기는 경로).
    await _insert_account_deletion_request(db_session, user_id=user.id, status="COMPLETED")
    await db_session.flush()

    count = await db_session.scalar(
        text("SELECT count(*) FROM account_deletion_request WHERE user_id = :user_id"),
        {"user_id": str(user.id)},
    )
    assert count == 2
