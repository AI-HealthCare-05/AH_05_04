from app.core.errors import ApiError, ErrorDetail
from app.core.utils.common import normalize_email
from app.dtos.users import UserUpdateRequest
from app.models.users import User
from app.repositories.user_repository import (
    DuplicateUserFieldError,
    UserRepository,
)
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

        try:
            return await self.repo.update_instance(
                user=user,
                data=update_data,
            )
        except DuplicateUserFieldError as exc:
            # 사전 중복 조회를 동시에 통과한 요청도 DB unique 제약에서
            # 충돌하면 회원가입과 동일한 409 계약으로 변환합니다.
            if exc.field == "email":
                message = "이미 사용중인 이메일입니다."
            else:
                message = "이미 사용중인 휴대폰 번호입니다."

            raise ApiError(
                status_code=409,
                code="CONFLICT",
                message=message,
                details=[
                    ErrorDetail(
                        field=exc.field,
                        reason="ALREADY_EXISTS",
                    )
                ],
            ) from exc
