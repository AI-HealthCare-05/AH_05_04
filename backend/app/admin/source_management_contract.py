from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TargetKind(StrEnum):
    SOURCE = "source"
    ENDPOINT = "endpoint"
    OPERATION = "operation"
    SNAPSHOT = "snapshot"
    PRODUCT = "product"
    INGREDIENT = "ingredient"
    ALIAS = "alias"
    COMPONENT = "component"


class ReasonCode(StrEnum):
    CORRECTION = "CORRECTION"
    DUPLICATE = "DUPLICATE"
    WITHDRAWAL = "WITHDRAWAL"


class ManagementCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0, strict=True)
    expected_hash: Digest
    reason_code: ReasonCode
    approval_hash: Digest
    request_id: UUID


class UpdateCommand(ManagementCommand):
    changes: dict[str, str | None] = Field(min_length=1, max_length=8)


class ManagementResult(BaseModel):
    event_id: UUID | None = None
    target_kind: TargetKind
    target_id: UUID
    revision: int | None
    hash: str | None
