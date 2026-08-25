"""Worker 공통 실행 계층에서 사용하는 안전한 오류 타입입니다."""

from ai_worker.core.retry import FailureCode
from ai_worker.schemas.messages import JobType


class WorkerError(Exception):
    """Worker가 분류 가능한 공통 오류입니다.

    `safe_message`에는 API Key, Provider 원문, 처방 내용과 사용자 질문을
    포함하지 않습니다. 재시도 계층은 `failure_code`만 사용합니다.
    """

    def __init__(
        self,
        *,
        failure_code: FailureCode,
        safe_message: str,
    ) -> None:
        super().__init__(safe_message)
        self.failure_code = failure_code
        self.safe_message = safe_message


class HandlerNotRegisteredError(WorkerError):
    """요청된 Job 종류의 Handler가 등록되지 않은 경우입니다."""

    def __init__(self, handler_type: JobType) -> None:
        super().__init__(
            failure_code="INTERNAL_ERROR",
            safe_message=f"{handler_type.value} Handler가 등록되지 않았습니다.",
        )
        self.handler_type = handler_type


class HandlerAlreadyRegisteredError(WorkerError):
    """같은 Job 종류의 Handler를 중복 등록한 경우입니다."""

    def __init__(self, handler_type: JobType) -> None:
        super().__init__(
            failure_code="INTERNAL_ERROR",
            safe_message=f"{handler_type.value} Handler가 이미 등록되어 있습니다.",
        )
        self.handler_type = handler_type


class HandlerResultMismatchError(WorkerError):
    """Handler 결과가 입력 메시지 식별자와 일치하지 않는 경우입니다."""

    def __init__(self, handler_type: JobType) -> None:
        super().__init__(
            failure_code="INTERNAL_ERROR",
            safe_message=f"{handler_type.value} Handler 결과가 요청과 일치하지 않습니다.",
        )
        self.handler_type = handler_type


class HandlerExecutionError(WorkerError):
    """분류되지 않은 Handler 예외를 안전한 오류로 변환합니다."""

    def __init__(self, handler_type: JobType) -> None:
        super().__init__(
            failure_code="INTERNAL_ERROR",
            safe_message=f"{handler_type.value} Handler 실행에 실패했습니다.",
        )
        self.handler_type = handler_type
