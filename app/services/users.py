from app.core.utils.common import normalize_phone_number
from app.dtos.users import UserUpdateRequest
from app.models.users import User
from app.repositories.user_repository import UserRepository
from app.services.auth import AuthService


class UserManageService:
    def __init__(
        self,
        repository: UserRepository,
    ) -> None:
        self.repo = repository
        self.auth_service = AuthService(repository)

    async def update_user(
        self,
        user: User,
        data: UserUpdateRequest,
    ) -> User:
        update_data = data.model_dump(exclude_none=True)

        if data.email and str(data.email) != user.email:
            await self.auth_service.check_email_exists(data.email)
            update_data["email"] = str(data.email)

        if data.phone_number:
            normalized_phone_number = normalize_phone_number(data.phone_number)

            if normalized_phone_number != user.phone_number:
                await self.auth_service.check_phone_number_exists(normalized_phone_number)

            update_data["phone_number"] = normalized_phone_number

        return await self.repo.update_instance(
            user=user,
            data=update_data,
        )
