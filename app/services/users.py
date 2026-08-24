from app.core.utils.common import normalize_email
from app.dtos.users import UserUpdateRequest
from app.models.users import User
from app.repositories.user_repository import UserRepository
from app.services.auth import AuthService


class UserManageService:
    def __init__(
        self,
        repository: UserRepository,
        auth_service: AuthService,
    ) -> None:
        self.repo = repository
        self.auth_service = auth_service

    async def update_user(
        self,
        user: User,
        data: UserUpdateRequest,
    ) -> User:
        update_data = data.model_dump(exclude_none=True)

        if data.email:
            normalized_email = normalize_email(str(data.email))

            if normalized_email != user.email:
                await self.auth_service.check_email_exists(normalized_email)
                update_data["email"] = normalized_email

        return await self.repo.update_instance(
            user=user,
            data=update_data,
        )
