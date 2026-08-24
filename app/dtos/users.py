from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.dtos.base import BaseSerializerModel
from app.models.users import Gender


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(None, min_length=2, max_length=20)]
    email: Annotated[
        EmailStr | None,
        Field(None, max_length=40),
    ]


class UserInfoResponse(BaseSerializerModel):
    id: UUID
    name: str
    email: str
    phone_number: str | None
    birthday: date | None
    gender: Gender | None
    created_at: datetime
