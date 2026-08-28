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
        """검증된 Worker 메시지를 처리하고 공통 성공 결과를 반환합니다.

        Handler는 결과를 직접 commit하지 않습니다. 저장과 commit은
        Consumer가 소유한 ResultStore·Transaction이 담당합니다.
        Handler가 같은 transaction에 남긴 변경은 결과 식별자 검증에
        실패하면 Consumer의 rollback 대상이 됩니다.

        오류는 승인된 failure_code만 선택하며 Provider 응답이나
        의료 원문을 오류 메시지·예외 chain에 담지 않습니다.
        """
        ...
