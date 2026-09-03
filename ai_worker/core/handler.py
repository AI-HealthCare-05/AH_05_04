"""OCR·Guide·Chat Handler가 구현할 공통 인터페이스입니다."""

import math
from dataclasses import dataclass
from typing import Protocol

from ai_worker.core.results import HandlerSuccess
from ai_worker.schemas.messages import JobType, WorkerMessage


@dataclass(frozen=True, slots=True)
class HandlerExecutionContext:
    """외부 Stream에 노출하지 않는 Worker 내부 실행 경계입니다."""

    worker_deadline: float

    def __post_init__(self) -> None:
        if isinstance(self.worker_deadline, bool) or not isinstance(
            self.worker_deadline,
            int | float,
        ):
            raise TypeError("worker_deadline은 monotonic clock 숫자여야 합니다.")

        if not math.isfinite(self.worker_deadline):
            raise ValueError("worker_deadline은 유한한 값이어야 합니다.")

        if self.worker_deadline <= 0:
            raise ValueError("worker_deadline은 0보다 커야 합니다.")


class ContextAwareHandler(Protocol):
    """Worker 내부 실행 context를 명시적으로 받는 Handler 계약입니다."""

    handler_type: JobType

    async def handle(
        self,
        message: WorkerMessage,
        *,
        context: HandlerExecutionContext,
    ) -> HandlerSuccess:
        """Worker deadline이 포함된 내부 context로 실행합니다."""
        ...


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
