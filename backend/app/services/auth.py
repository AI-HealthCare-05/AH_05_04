from pydantic import EmailStr

from app.core.errors import ApiError, ErrorDetail
from app.core.jwt.tokens import AccessToken, RefreshToken
from app.core.utils.security import hash_password, verify_password
from app.dtos.auth import LoginRequest, SignUpRequest
from app.models.users import User
from app.repositories.user_repository import (
    DuplicateUserFieldError,
    UserRepository,
)
from app.services.jwt import JwtService


def _invalid_credentials_error() -> ApiError:
    return ApiError(
        status_code=401,
        code="UNAUTHORIZED",
        message="이메일 또는 비밀번호가 올바르지 않습니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )


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

        try:
            return await self.user_repo.create_user(
                email=data.email,
                hashed_password=hash_password(data.password),
                name=data.name,
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
            raise _invalid_credentials_error()

        if not verify_password(
            data.password,
            user.hashed_password,
        ):
            raise _invalid_credentials_error()

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

        # 인증(authenticate()) 시점에 읽은 user.token_version은 그 이후 동시 로그아웃이
        # 커밋하면 이미 낡은 값일 수 있습니다. 토큰 발급 직전에 row lock으로 다시 읽어,
        # 동시 로그아웃 커밋 전이면 그 커밋을 기다렸다가 최신 token_version으로 발급하고,
        # 이미 커밋됐으면 그 값을 바로 반영합니다.
        fresh_user = await self.user_repo.get_user_for_update(user.id)
        if fresh_user is None:
            raise _invalid_credentials_error()
        return self.jwt_service.issue_jwt_pair(fresh_user)

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
