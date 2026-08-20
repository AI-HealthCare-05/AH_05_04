from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from app.core.errors import ApiError, ErrorDetail
from app.dependencies.services import get_user_repository
from app.models.users import User
from app.repositories.user_repository import UserRepository
from app.services.jwt import JwtService

security = HTTPBearer(auto_error=False)


async def get_request_user(
    credential: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(security),
    ],
    repository: Annotated[
        UserRepository,
        Depends(get_user_repository),
    ],
) -> User:
    if credential is None:
        raise ApiError(
            status_code=401,
            code="UNAUTHORIZED",
            message="로그인이 필요합니다.",
        )

    token = credential.credentials
    verified = JwtService().verify_jwt(
        token=token,
        token_type="access",
    )

    try:
        user_id = UUID(str(verified.payload["user_id"]))
    except (ValueError, TypeError, KeyError) as error:
        raise ApiError(
            status_code=401,
            code="INVALID_TOKEN",
            message="인증 정보가 유효하지 않습니다. 다시 로그인해 주세요.",
            details=[ErrorDetail(field="user_id", reason="INVALID")],
        ) from error

    user = await repository.get_user(user_id)

    if user is None:
        raise ApiError(
            status_code=401,
            code="INVALID_TOKEN",
            message="인증 정보가 유효하지 않습니다. 다시 로그인해 주세요.",
        )

    return user
