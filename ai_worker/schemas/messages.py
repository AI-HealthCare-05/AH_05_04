"""Redis 연결과 독립적인 Worker Stream 메시지 schema입니다.

승인된 `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md`의 v1 envelope만 표현합니다.
처방 내용, 약품명, 질문, OCR 원문과 사용자 식별정보는 허용하지 않습니다.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

type SchemaVersion = Literal["1.0"]
type EventKind = Literal["JOB_EXECUTE"]

# Redis에서 받은 trace_id의 앞뒤 공백은 제거하되,
# 빈 값은 관측성 연결 키로 사용할 수 없으므로 거부합니다.
type TraceId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

# bool은 Python에서 int의 하위 타입이므로 strict=True로 차단합니다.
type Attempt = Annotated[int, Field(strict=True, ge=1)]


class JobType(StrEnum):
    """공통 Worker가 처리할 승인된 Job 종류입니다."""

    OCR = "OCR"
    GUIDE = "GUIDE"
    CHAT = "CHAT"


class DomainType(StrEnum):
    """Job 결과가 저장될 도메인 대상을 나타냅니다."""

    OCR_JOB = "OCR_JOB"
    GUIDE = "GUIDE"
    CHAT_MESSAGE = "CHAT_MESSAGE"


DOMAIN_TYPE_BY_JOB_TYPE: dict[JobType, DomainType] = {
    JobType.OCR: DomainType.OCR_JOB,
    JobType.GUIDE: DomainType.GUIDE,
    JobType.CHAT: DomainType.CHAT_MESSAGE,
}


class WorkerMessage(BaseModel):
    """Worker Dispatcher에 전달되는 검증 완료 메시지입니다.

    Redis Stream의 bytes 변환과 ACK는 후속 Consumer 계층의 책임입니다.
    이 모델은 외부 I/O를 수행하지 않습니다.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    schema_version: SchemaVersion
    event_id: UUID
    event_kind: EventKind
    job_id: UUID
    job_type: JobType
    domain_type: DomainType
    domain_id: UUID
    attempt: Attempt
    available_at: AwareDatetime
    enqueued_at: AwareDatetime
    trace_id: TraceId

    @model_validator(mode="after")
    def validate_domain_type(self) -> Self:
        """Job 종류와 결과 대상의 잘못된 조합을 차단합니다."""

        expected_domain_type = DOMAIN_TYPE_BY_JOB_TYPE[self.job_type]

        if self.domain_type != expected_domain_type:
            raise ValueError("job_type과 domain_type 조합이 일치하지 않습니다.")

        return self
