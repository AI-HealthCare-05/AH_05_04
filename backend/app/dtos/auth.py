from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.core.validators import validate_password


class SignUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[
        EmailStr,
        Field(max_length=40),
    ]
    password: Annotated[str, Field(min_length=8, max_length=72), AfterValidator(validate_password)]
    name: Annotated[str, Field(min_length=1, max_length=20)]


class LoginRequest(BaseModel):
    email: Annotated[
        EmailStr,
        Field(max_length=40),
    ]
    password: Annotated[str, Field(min_length=8)]


class LoginResponse(BaseModel):
    access_token: str


class EmailAvailabilityResponse(BaseModel):
    available: bool


class TokenRefreshResponse(LoginResponse): ...


class LogoutResponse(BaseModel):
    detail: str


class PasswordResetRequestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[
        EmailStr,
        Field(max_length=40),
    ]


class PasswordResetRequestResponse(BaseModel):
    detail: str
    # LOCAL 환경에서만 채워진다(실제 이메일 발송 Provider 연동 전까지의 임시 확인 경로).
    # 그 외 환경에서는 계정 존재 여부가 새지 않도록 항상 비운다.
    reset_token: str | None = None


class PasswordResetConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 계정 존재 여부를 노출하지 않는 요청과 달리, 이 값들은 이 요청 자체의 정책 위반 여부만
    # 다룬다. 두 필드 모두 실제 정책·유효성 검증은 AuthService.reset_password()에서
    # PD-206 결정 3의 고정된 오류 코드(PASSWORD_POLICY_VIOLATION/RESET_TOKEN_INVALID)로
    # 수행한다 — DTO 레벨 제약을 두면 FastAPI 기본 422가 그 코드 대신 나가버린다.
    token: str
    new_password: str


class PasswordResetConfirmResponse(BaseModel):
    detail: str
