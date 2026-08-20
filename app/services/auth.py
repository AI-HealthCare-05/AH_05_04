from pydantic import EmailStr

from app.core.errors import ApiError, ErrorDetail
from app.core.jwt.tokens import AccessToken, RefreshToken
from app.core.utils.common import normalize_phone_number
from app.core.utils.security import hash_password, verify_password
from app.dtos.auth import LoginRequest, SignUpRequest
from app.models.users import User
from app.repositories.user_repository import (
    DuplicateUserFieldError,
    UserRepository,
)
from app.services.jwt import JwtService


class AuthService:
    def __init__(
        self,
        user_repository: UserRepository,
    ) -> None:
        self.user_repo = user_repository
        self.jwt_service = JwtService()

    async def signup(
        self,
        data: SignUpRequest,
    ) -> User:
        await self.check_email_exists(data.email)

        normalized_phone_number = normalize_phone_number(data.phone_number)
        await self.check_phone_number_exists(normalized_phone_number)

        try:
            return await self.user_repo.create_user(
                email=data.email,
                hashed_password=hash_password(data.password),
                name=data.name,
                phone_number=normalized_phone_number,
                gender=data.gender,
                birthday=data.birth_date,
            )
        except DuplicateUserFieldError as exc:
            if exc.field == "email":
                detail = "이미 사용중인 이메일입니다."
            else:
                detail = "이미 사용중인 휴대폰 번호입니다."

            raise ApiError(
                status_code=409,
                code="CONFLICT",
                message=detail,
                details=[ErrorDetail(field=exc.field, reason="ALREADY_EXISTS")],
            ) from exc

    async def authenticate(
        self,
        data: LoginRequest,
    ) -> User:
        user = await self.user_repo.get_user_by_email(str(data.email))

        if user is None:
            raise ApiError(
                status_code=401,
                code="UNAUTHORIZED",
                message="이메일 또는 비밀번호가 올바르지 않습니다.",
            )

        if not verify_password(
            data.password,
            user.hashed_password,
        ):
            raise ApiError(
                status_code=401,
                code="UNAUTHORIZED",
                message="이메일 또는 비밀번호가 올바르지 않습니다.",
            )

        if not user.is_active:
            raise ApiError(
                status_code=403,
                code="FORBIDDEN",
                message="비활성화된 계정입니다.",
            )

        return user

    async def login(
        self,
        user: User,
    ) -> dict[str, AccessToken | RefreshToken]:
        await self.user_repo.update_last_login(user.id)
        return self.jwt_service.issue_jwt_pair(user)

    async def check_email_exists(
        self,
        email: str | EmailStr,
    ) -> None:
        if await self.user_repo.exists_by_email(email):
            raise ApiError(
                status_code=409,
                code="CONFLICT",
                message="이미 사용중인 이메일입니다.",
                details=[ErrorDetail(field="email", reason="ALREADY_EXISTS")],
            )

    async def check_phone_number_exists(
        self,
        phone_number: str,
    ) -> None:
        if await self.user_repo.exists_by_phone_number(phone_number):
            raise ApiError(
                status_code=409,
                code="CONFLICT",
                message="이미 사용중인 휴대폰 번호입니다.",
                details=[ErrorDetail(field="phone_number", reason="ALREADY_EXISTS")],
            )
