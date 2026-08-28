"""Handler가 Dispatcher에 반환하는 공통 성공 결과입니다."""

from dataclasses import dataclass
from uuid import UUID

from ai_worker.schemas.messages import JobType


@dataclass(frozen=True, slots=True)
class HandlerSuccess:
    """Handler 실행이 정상적으로 끝났음을 나타내는 내부 결과입니다.

    이 객체는 HTTP 응답이나 DB 결과 모델이 아닙니다.
    실제 OCR·Guide·Chat 결과 저장은 각 Handler의 후속 구현에서 담당합니다.
    """

    event_id: UUID
    job_id: UUID
    handler_type: JobType
