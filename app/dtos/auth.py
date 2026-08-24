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
    email: EmailStr
    password: Annotated[str, Field(min_length=8)]


class LoginResponse(BaseModel):
    access_token: str


class TokenRefreshResponse(LoginResponse): ...
