"""OCR·Guide·Chat Handler가 구현할 공통 인터페이스입니다."""

from typing import Protocol

from ai_worker.core.results import HandlerSuccess
from ai_worker.schemas.messages import JobType, WorkerMessage


class Handler(Protocol):
    """Dispatcher에 등록할 비동기 Handler 계약입니다."""

    # 외부 envelope에 handler_type을 추가하지 않습니다.
    # 승인된 message.job_type을 내부 Handler 선택 키로 사용합니다.
    handler_type: JobType

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        """검증된 Worker 메시지를 처리하고 공통 성공 결과를 반환합니다."""
        ...
