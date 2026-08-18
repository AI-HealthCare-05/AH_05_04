from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from app.dependencies.services import get_user_repository
from app.models.users import User
from app.repositories.user_repository import UserRepository
from app.services.jwt import JwtService

security = HTTPBearer()


async def get_request_user(
    credential: Annotated[
        HTTPAuthorizationCredentials,
        Depends(security),
    ],
    repository: Annotated[
        UserRepository,
        Depends(get_user_repository),
    ],
) -> User:
    token = credential.credentials
    verified = JwtService().verify_jwt(
        token=token,
        token_type="access",
    )

    try:
        user_id = UUID(str(verified.payload["user_id"]))
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(
            detail="Authenticate Failed.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from error

    user = await repository.get_user(user_id)

    if user is None:
        raise HTTPException(
            detail="Authenticate Failed.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return user
