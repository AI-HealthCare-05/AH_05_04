import base64
import re
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, Field, field_validator


def decode_key(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid subscription key")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p256dh: str = Field(min_length=87, max_length=87, repr=False)
    auth: str = Field(min_length=22, max_length=22, repr=False)

    @field_validator("p256dh")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        raw = decode_key(value)
        if len(raw) != 65 or raw[0] != 4:
            raise ValueError("Invalid subscription key")
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        return value

    @field_validator("auth")
    @classmethod
    def validate_auth(cls, value: str) -> str:
        if len(decode_key(value)) != 16:
            raise ValueError("Invalid subscription key")
        return value


class PushSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=1, max_length=2048, repr=False)
    keys: PushKeys = Field(repr=False)


class PushSubscriptionData(BaseModel):
    id: UUID
    generation: UUID


class PushSubscriptionResponse(BaseModel):
    data: PushSubscriptionData


class PushConfigData(BaseModel):
    public_key: str


class PushConfigResponse(BaseModel):
    data: PushConfigData
