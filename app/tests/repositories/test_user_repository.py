from datetime import date
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.users import Gender
from app.repositories.user_repository import (
    DuplicateUserFieldError,
    UserRepository,
)


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
