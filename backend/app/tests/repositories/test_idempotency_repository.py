from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.async_jobs import IdempotencyRecord, IdempotencyRecordType
from app.models.users import Gender, User
from app.repositories.idempotency_repository import (
    IdempotencyRepository,
    is_sync_idempotency_scope_conflict,
)
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
        email=f"idem-repo-{uuid4().hex[:12]}@example.com",
        hashed_password="hashed-password",
        name="테스트 사용자",
        gender=Gender.MALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    session.add(user)
    await session.flush()
    return user


async def test_find_sync_idempotency_record_returns_none_without_matching_record(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)

    record = await IdempotencyRepository(db_session).find_sync_idempotency_record(
        user_id=user.id,
        operation_id="medication-candidate.confirm",
        parent_resource_id=uuid4(),
        key_hmac="digest",
    )

    assert record is None


async def test_create_sync_idempotency_record_persists_expected_fields(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    parent_resource_id = uuid4()

    record = await IdempotencyRepository(db_session).create_sync_idempotency_record(
        user_id=user.id,
        operation_id="medication-candidate.confirm",
        parent_resource_id=parent_resource_id,
        key_hmac="digest",
        request_hash="request-fingerprint",
        response_status=200,
        response_body_snapshot=b"encrypted-bytes",
        encryption_key_version="v1",
    )

    assert record.record_type == IdempotencyRecordType.SYNC_MUTATION
    assert record.job_id is None
    assert record.parent_resource_id == parent_resource_id
    assert record.response_status == 200
    assert record.response_body_snapshot == b"encrypted-bytes"
    assert record.encryption_key_version == "v1"
    assert record.key_hmac_version == config.IDEMPOTENCY_HMAC_KEY_VERSION
    assert record.expires_at - datetime.now(config.TIMEZONE) < timedelta(
        days=config.IDEMPOTENCY_RECORD_TTL_DAYS, hours=1
    )


async def test_find_sync_idempotency_record_returns_created_record(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    parent_resource_id = uuid4()
    repository = IdempotencyRepository(db_session)
    created = await repository.create_sync_idempotency_record(
        user_id=user.id,
        operation_id="medication-candidate.confirm",
        parent_resource_id=parent_resource_id,
        key_hmac="digest",
        request_hash="request-fingerprint",
        response_status=200,
        response_body_snapshot=b"encrypted-bytes",
        encryption_key_version="v1",
    )

    found = await repository.find_sync_idempotency_record(
        user_id=user.id,
        operation_id="medication-candidate.confirm",
        parent_resource_id=parent_resource_id,
        key_hmac="digest",
    )

    assert found is not None
    assert found.id == created.id


async def test_find_sync_idempotency_record_scopes_by_parent_resource_id(
    db_session: AsyncSession,
) -> None:
    """같은 user·operation_id·key_hmac라도 parent_resource_id가 다르면 다른 레코드다 —
    같은 Idempotency-Key 문자열을 서로 다른 대상(prescription_version_medication_id)에
    재사용해도 서로의 응답을 재현하지 않아야 한다."""
    user = await _create_user(db_session)
    repository = IdempotencyRepository(db_session)
    await repository.create_sync_idempotency_record(
        user_id=user.id,
        operation_id="medication-candidate.confirm",
        parent_resource_id=uuid4(),
        key_hmac="digest",
        request_hash="request-fingerprint",
        response_status=200,
        response_body_snapshot=b"encrypted-bytes",
        encryption_key_version="v1",
    )

    found = await repository.find_sync_idempotency_record(
        user_id=user.id,
        operation_id="medication-candidate.confirm",
        parent_resource_id=uuid4(),
        key_hmac="digest",
    )

    assert found is None


async def test_delete_expired_idempotency_record_removes_row_when_expired(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    repository = IdempotencyRepository(db_session)
    record = await repository.create_sync_idempotency_record(
        user_id=user.id,
        operation_id="medication-candidate.confirm",
        parent_resource_id=uuid4(),
        key_hmac="digest",
        request_hash="request-fingerprint",
        response_status=200,
        response_body_snapshot=b"encrypted-bytes",
        encryption_key_version="v1",
    )
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    await repository.delete_expired_idempotency_record(record_id=record.id)

    assert await db_session.get(IdempotencyRecord, record.id) is None


async def test_delete_expired_idempotency_record_keeps_row_when_not_expired(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    repository = IdempotencyRepository(db_session)
    record = await repository.create_sync_idempotency_record(
        user_id=user.id,
        operation_id="medication-candidate.confirm",
        parent_resource_id=uuid4(),
        key_hmac="digest",
        request_hash="request-fingerprint",
        response_status=200,
        response_body_snapshot=b"encrypted-bytes",
        encryption_key_version="v1",
    )

    await repository.delete_expired_idempotency_record(record_id=record.id)

    assert await db_session.get(IdempotencyRecord, record.id) is not None


async def test_is_sync_idempotency_scope_conflict_detects_unique_violation(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    repository = IdempotencyRepository(db_session)
    parent_resource_id = uuid4()
    await repository.create_sync_idempotency_record(
        user_id=user.id,
        operation_id="medication-candidate.confirm",
        parent_resource_id=parent_resource_id,
        key_hmac="digest",
        request_hash="request-fingerprint",
        response_status=200,
        response_body_snapshot=b"encrypted-bytes",
        encryption_key_version="v1",
    )

    with pytest.raises(IntegrityError) as exc_info:
        await repository.create_sync_idempotency_record(
            user_id=user.id,
            operation_id="medication-candidate.confirm",
            parent_resource_id=parent_resource_id,
            key_hmac="digest",
            request_hash="different-request-fingerprint",
            response_status=200,
            response_body_snapshot=b"encrypted-bytes-2",
            encryption_key_version="v1",
        )

    assert is_sync_idempotency_scope_conflict(exc_info.value)
