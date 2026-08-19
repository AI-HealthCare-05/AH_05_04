from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.databases import get_db_session
from app.repositories.user_repository import UserRepository
from app.services.auth import AuthService
from app.services.users import UserManageService


def get_user_repository(
    session: Annotated[
        AsyncSession,
        Depends(get_db_session),
    ],
) -> UserRepository:
    return UserRepository(session)


def get_auth_service(
    repository: Annotated[
        UserRepository,
        Depends(get_user_repository),
    ],
) -> AuthService:
    return AuthService(repository)


def get_user_manage_service(
    repository: Annotated[
        UserRepository,
        Depends(get_user_repository),
    ],
    auth_service: Annotated[
        AuthService,
        Depends(get_auth_service),
    ],
) -> UserManageService:
    return UserManageService(
        repository=repository,
        auth_service=auth_service,
    )
