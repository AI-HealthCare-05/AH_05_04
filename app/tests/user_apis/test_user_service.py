from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.dtos.users import UserUpdateRequest
from app.models.users import User
from app.repositories.user_repository import (
    DuplicateUserFieldError,
    UserRepository,
)
from app.services.auth import AuthService
from app.services.users import UserManageService


async def test_update_user_maps_concurrent_email_conflict_to_409() -> None:
    """사전 조회 이후 발생한 DB 이메일 충돌도 API의 409 계약으로 변환합니다."""
    repository = AsyncMock(spec=UserRepository)
    auth_service = AsyncMock(spec=AuthService)

    repository.update_instance.side_effect = DuplicateUserFieldError("email")

    service = UserManageService(
        repository=repository,
        auth_service=auth_service,
    )
    user = User(
        id=uuid4(),
        email="current-user@example.com",
        hashed_password="synthetic-hashed-password",
        name="동시수정테스트",
    )

    with pytest.raises(ApiError) as exc_info:
        await service.update_user(
            user=user,
            data=UserUpdateRequest(
                name=None,
                email="shared-target@example.com",
            ),
        )

    error = exc_info.value
    assert error.status_code == 409
    assert error.code == "CONFLICT"
    assert error.message == "이미 사용중인 이메일입니다."
    assert len(error.details) == 1
    assert error.details[0].field == "email"
    assert error.details[0].reason == "ALREADY_EXISTS"
    assert error.details[0].rejected_value is None
