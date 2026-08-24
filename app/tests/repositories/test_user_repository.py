import asyncio
from datetime import date
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.models.users import Gender, User
from app.repositories.user_repository import (
    DuplicateUserFieldError,
    UserRepository,
)
from app.tests.conftest import test_engine


class PostgreSQLUniqueViolationTestError(Exception):
    """민감한 SQL·입력값 없이 asyncpg unique 오류 구조를 재현합니다."""

    sqlstate = "23505"

    def __init__(self, constraint_name: str) -> None:
        self.constraint_name = constraint_name
        super().__init__("PostgreSQL unique violation")


@pytest.mark.parametrize(
    ("unique_key", "expected_field"),
    [
        ("ix_user_email", "email"),
        ("phone_number", "phone_number"),
    ],
)
async def test_create_user_converts_duplicate_entry(
    unique_key: str,
    expected_field: str,
):
    session = AsyncMock(spec=AsyncSession)
    session.flush.side_effect = IntegrityError(
        statement="INSERT INTO user ...",
        params={},
        orig=Exception(
            1062,
            (f"Duplicate entry 'duplicate-value' for key 'user.{unique_key}'"),
        ),
    )

    repository = UserRepository(session)

    with pytest.raises(DuplicateUserFieldError) as exc_info:
        await repository.create_user(
            email="duplicate@example.com",
            hashed_password="hashed-password",
            name="중복테스트",
            phone_number="01012345678",
            gender=Gender.MALE,
            birthday=date(1990, 1, 1),
        )

    assert exc_info.value.field == expected_field


@pytest.mark.parametrize(
    ("constraint_name", "expected_field"),
    [
        ("ix_user_email", "email"),
        ("user_phone_number_key", "phone_number"),
    ],
)
async def test_create_user_converts_postgresql_unique_violation(
    constraint_name: str,
    expected_field: str,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.flush.side_effect = IntegrityError(
        statement=None,
        params=None,
        orig=PostgreSQLUniqueViolationTestError(constraint_name),
    )

    repository = UserRepository(session)

    with pytest.raises(DuplicateUserFieldError) as exc_info:
        await repository.create_user(
            email="duplicate@example.com",
            hashed_password="hashed-password",
            name="중복테스트",
        )

    assert exc_info.value.field == expected_field


async def test_create_user_reraises_unknown_postgresql_unique_violation() -> None:
    original_error = PostgreSQLUniqueViolationTestError("unrelated_unique_constraint")

    session = AsyncMock(spec=AsyncSession)
    session.flush.side_effect = IntegrityError(
        statement=None,
        params=None,
        orig=original_error,
    )

    repository = UserRepository(session)

    with pytest.raises(IntegrityError) as exc_info:
        await repository.create_user(
            email="unknown-constraint@example.com",
            hashed_password="hashed-password",
            name="제약조건테스트",
        )

    assert exc_info.value.orig is original_error


async def test_create_user_reraises_unknown_integrity_error():
    original_error = Exception(
        1048,
        "Column cannot be null",
    )

    session = AsyncMock(spec=AsyncSession)
    session.flush.side_effect = IntegrityError(
        statement="INSERT INTO user ...",
        params={},
        orig=original_error,
    )

    repository = UserRepository(session)

    with pytest.raises(IntegrityError) as exc_info:
        await repository.create_user(
            email="test@example.com",
            hashed_password="hashed-password",
            name="테스트",
            phone_number="01012345678",
            gender=Gender.MALE,
            birthday=date(1990, 1, 1),
        )

    assert exc_info.value.orig is original_error


async def test_create_user_normalizes_email_to_lowercase() -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = UserRepository(session)

    user = await repository.create_user(
        email="Case-Sensitive@Example.COM",
        hashed_password="hashed-password",
        name="정규화테스트",
    )

    assert user.email == "case-sensitive@example.com"
    session.flush.assert_awaited_once()


async def test_postgresql_rejects_concurrent_case_variant_emails() -> None:
    """실제 PostgreSQL unique 인덱스에서 동시 가입 경쟁을 검증합니다."""

    # User.email의 최대 길이 40자를 넘지 않도록 합성 식별자를 12자로 제한합니다.
    normalized_email = f"pg-race-{uuid4().hex[:12]}@example.com"
    case_variant_email = normalized_email.upper()

    session_factory = async_sessionmaker(
        test_engine,
        expire_on_commit=False,
    )

    async def create_user(email: str) -> str:
        # 서로 다른 DB session을 사용해야 실제 동시 transaction 경쟁이 발생합니다.
        async with session_factory() as session:
            repository = UserRepository(session)

            try:
                await repository.create_user(
                    email=email,
                    hashed_password="synthetic-hashed-password",
                    name="동시가입테스트",
                )
                await session.commit()
                return "created"
            except DuplicateUserFieldError as exc:
                await session.rollback()

                assert exc.field == "email"
                return "duplicate"

    try:
        results = await asyncio.gather(
            create_user(normalized_email),
            create_user(case_variant_email),
        )
    finally:
        # 이 테스트는 독립 session에서 실제 commit하므로 합성 테스트 행을 직접 정리합니다.
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(delete(User).where(User.email == normalized_email))
            await cleanup_session.commit()

    assert sorted(results) == ["created", "duplicate"]
