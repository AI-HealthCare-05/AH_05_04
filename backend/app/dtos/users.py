from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.dtos.base import BaseSerializerModel
from app.models.user_consents import ConsentPurpose, ConsentStatus
from app.models.users import Gender


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(min_length=2, max_length=20)] = None
    email: Annotated[
        EmailStr | None,
        Field(max_length=40),
    ] = None
    phone_number: str | None = None
    birthday: date | None = None
    gender: Gender | None = None

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, value: str | None) -> str | None:
        if value is None:
            return None

        if not value.isdigit():
            raise ValueError("phone_number must contain digits only")

        return value

    @field_validator("birthday")
    @classmethod
    def validate_birthday(cls, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise ValueError("birthday must not be in the future")

        return value


class UserInfoResponse(BaseSerializerModel):
    id: UUID
    name: str
    email: str
    phone_number: str | None
    birthday: date | None
    gender: Gender | None
    created_at: datetime


class UserConsentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ConsentStatus
    policy_version: Annotated[str, Field(min_length=1, max_length=100)]


class UserConsentData(BaseSerializerModel):
    purpose: ConsentPurpose
    status: ConsentStatus | None
    policy_version: str | None
    current_policy_version: str
    is_granted: bool
    granted_at: datetime | None
    withdrawn_at: datetime | None
    updated_at: datetime | None


class UserConsentResponse(BaseModel):
    data: UserConsentData


class UserConsentListResponse(BaseModel):
    data: list[UserConsentData]
