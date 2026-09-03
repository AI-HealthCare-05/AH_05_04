from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from app.core.errors import ApiError, ErrorDetail
from app.dependencies.services import get_user_repository
from app.models.users import AccountStatus, User
from app.repositories.user_repository import UserRepository
from app.services.jwt import JwtService

security = HTTPBearer(auto_error=False)


def invalid_token_error(
    *,
    details: list[ErrorDetail] | None = None,
) -> ApiError:
    return ApiError(
        status_code=401,
        code="INVALID_TOKEN",
        message="인증 정보가 유효하지 않습니다. 다시 로그인해 주세요.",
        details=details,
        headers={"WWW-Authenticate": "Bearer"},
    )


def parse_token_user_id_and_version(payload: dict[str, object]) -> tuple[UUID, int]:
    try:
        user_id = UUID(str(payload["user_id"]))
        token_version_value = payload["token_version"]
        if not isinstance(token_version_value, int):
            raise TypeError("token_version must be an integer")
        return user_id, token_version_value
    except (ValueError, TypeError, KeyError) as error:
        raise invalid_token_error(details=[ErrorDetail(field="user_id", reason="INVALID")]) from error


def validate_active_token_user(*, user: User, token_version: int) -> None:
    if user.account_status != AccountStatus.ACTIVE or not user.is_active:
        raise invalid_token_error()

    if token_version != user.token_version:
        raise invalid_token_error()


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
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credential.credentials
    verified = JwtService().verify_jwt(
        token=token,
        token_type="access",
    )

    user_id, token_version = parse_token_user_id_and_version(verified.payload)

    user = await repository.get_user(user_id)

    if user is None:
        raise invalid_token_error()

    validate_active_token_user(user=user, token_version=token_version)
    return user
