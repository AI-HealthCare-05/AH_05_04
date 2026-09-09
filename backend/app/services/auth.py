from datetime import datetime, timedelta
from uuid import uuid4

from pydantic import EmailStr

from app.core import config
from app.core.config import Env
from app.core.errors import ApiError, ErrorDetail
from app.core.jwt.tokens import AccessToken, RefreshToken
from app.core.utils.security import (
    generate_password_reset_token,
    hash_password,
    hash_password_reset_token,
    verify_password,
)
from app.core.validators import validate_password
from app.dtos.auth import LoginRequest, SignUpRequest
from app.models.users import User
from app.repositories.password_reset_repository import PasswordResetRepository
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


def _reset_token_invalid_error() -> ApiError:
    return ApiError(
        status_code=422,
        code="VALIDATION_FAILED",
        message="재설정 링크가 유효하지 않습니다. 다시 요청해 주세요.",
        details=[ErrorDetail(field="token", reason="RESET_TOKEN_INVALID")],
    )


class AuthService:
    def __init__(
        self,
        user_repository: UserRepository,
        password_reset_repository: PasswordResetRepository,
    ) -> None:
        self.user_repo = user_repository
        self.password_reset_repo = password_reset_repository
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

        tokens = self.jwt_service.issue_jwt_pair(fresh_user)
        refresh_token = tokens["refresh_token"]
        await self.user_repo.set_active_refresh_jti(fresh_user.id, str(refresh_token.payload["jti"]))
        return tokens

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

    async def request_password_reset(self, email: str | EmailStr) -> str | None:
        """PD-206 결정 3: 계정 존재 여부를 노출하지 않기 위해 계정이 없거나 쿨다운
        중이어도 예외를 던지지 않고 조용히 반환한다(호출자는 항상 같은 성공 응답을 준다).
        원문 token은 이번 범위에서 실제 이메일 발송 대신 로컬 환경에서만 호출자에게
        돌려주고(#206 제외 범위: 실제 이메일 발송 Provider 연동), 그 외 환경에서는 항상
        `None`을 반환해 존재 여부가 새지 않게 한다.

        응답 형태뿐 아니라 처리시간으로도 계정 존재 여부가 새지 않도록, 계정이 없어도
        있는 경우와 같은 수의 DB 조회(사용자 조회, 쿨다운 조회)와 같은 해싱 연산을
        수행한다. 다만 실제 INSERT 하나까지 동일하게 맞추지는 않는다 — 단일 행 INSERT의
        비용 차이는 네트워크 왕복 지연에 비해 미미해 실질적 위협으로 보기 어렵다고
        판단했다."""
        now = datetime.now(config.TIMEZONE)
        cooldown_since = now - timedelta(seconds=config.PASSWORD_RESET_REQUEST_COOLDOWN_SECONDS)

        user = await self.user_repo.get_user_by_email(str(email))
        # 계정이 없으면 실재하지 않을 무작위 user_id로 같은 조회를 수행해 쿼리 횟수를
        # 맞춘다 — 결과는 항상 없고 아무 것도 저장하지 않는다.
        lookup_user_id = user.id if user is not None else uuid4()
        recent_token = await self.password_reset_repo.find_recent_token_for_user(
            user_id=lookup_user_id, since=cooldown_since
        )

        raw_token = generate_password_reset_token()
        token_hash = hash_password_reset_token(raw_token)

        if user is None or recent_token is not None:
            return None

        await self.password_reset_repo.create_token(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=config.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
        )

        return raw_token if config.ENV == Env.LOCAL else None

    async def reset_password(self, *, token: str, new_password: str) -> None:
        """PD-206 결정 3의 lock 순서를 그대로 따른다: token_hash로 candidate를 잠금 없이
        조회해 대상 user_id만 얻고, user row를 먼저 FOR UPDATE로 잠근 뒤, 같은 사용자의
        미사용·미만료 token 전체를 id 오름차순으로 잠가 제출된 token의 유효성을 다시
        확인하면서 함께 소비한다."""
        try:
            validate_password(new_password)
        except ValueError as exc:
            raise ApiError(
                status_code=422,
                code="VALIDATION_FAILED",
                message="비밀번호 정책을 만족하지 않습니다.",
                details=[ErrorDetail(field="new_password", reason="PASSWORD_POLICY_VIOLATION")],
            ) from exc

        candidate = await self.password_reset_repo.find_by_hash(hash_password_reset_token(token))
        if candidate is None:
            raise _reset_token_invalid_error()

        user = await self.user_repo.get_user_for_update(candidate.user_id)
        if user is None:
            raise _reset_token_invalid_error()

        valid_tokens = await self.password_reset_repo.lock_unused_unexpired_tokens_for_user(user.id)
        if not any(valid_token.id == candidate.id for valid_token in valid_tokens):
            raise _reset_token_invalid_error()

        await self.password_reset_repo.mark_tokens_used(valid_tokens, used_at=datetime.now(config.TIMEZONE))
        user.hashed_password = hash_password(new_password)
        await self.user_repo.increment_token_version(user)
