"""Worker 공통 실행 계층에서 사용하는 안전한 오류 타입입니다."""

from collections.abc import Mapping
from types import MappingProxyType

from ai_worker.core.retry import ALL_FAILURE_CODES, FailureCode
from ai_worker.schemas.messages import JobType

# Handler가 Provider 응답이나 의료 원문을 오류 메시지로 전달하지 못하도록
# 승인된 failure_code별 외부 노출 가능 문구를 이 모듈에서 고정합니다.
SAFE_MESSAGE_BY_FAILURE_CODE: Mapping[FailureCode, str] = MappingProxyType(
    {
        "TIMEOUT": "Worker 처리 시간이 초과되었습니다.",
        "DEPENDENCY_UNAVAILABLE": "Worker 의존 서비스를 사용할 수 없습니다.",
        "INVALID_INPUT": "Worker 입력값이 올바르지 않습니다.",
        "UNSUPPORTED_SCHEMA": "지원하지 않는 Worker 메시지 형식입니다.",
        "SAFETY_VALIDATION_FAILED": "Worker 안전성 검증에 실패했습니다.",
        "RETRY_EXHAUSTED": "Worker 재시도 횟수를 초과했습니다.",
        "INTERNAL_ERROR": "Worker 내부 처리에 실패했습니다.",
    }
)


class WorkerError(Exception):
    """Worker가 분류 가능한 공통 오류입니다.

    Handler는 failure_code만 선택할 수 있습니다.
    외부에 노출할 메시지는 승인된 코드별 고정 문구를 사용하므로
    API Key, Provider 응답, OCR 원문 등의 자유 문자열을 포함할 수 없습니다.
    """

    def __init__(
        self,
        *,
        failure_code: FailureCode,
    ) -> None:
        # FailureCode는 정적 타입 계약이므로 런타임 입력도 별도로 검증합니다.
        if failure_code not in ALL_FAILURE_CODES:
            raise ValueError("승인되지 않은 failure_code입니다.")

        self._failure_code = failure_code
        self._safe_message = SAFE_MESSAGE_BY_FAILURE_CODE[failure_code]
        super().__init__(self._safe_message)

    @property
    def failure_code(self) -> FailureCode:
        """재시도 판단에 사용하는 승인된 오류 코드입니다."""

        return self._failure_code

    @property
    def safe_message(self) -> str:
        """오류 코드에 대응하는 고정된 외부 노출 가능 문구입니다."""

        return self._safe_message

    def has_safe_contract(self) -> bool:
        """오류 코드와 메시지가 승인된 조합인지 확인합니다."""

        expected_message = SAFE_MESSAGE_BY_FAILURE_CODE.get(self.failure_code)

        return (
            self.failure_code in ALL_FAILURE_CODES
            and expected_message is not None
            and self.safe_message == expected_message
            and self.args == (expected_message,)
        )


class HandlerNotRegisteredError(WorkerError):
    """요청된 Job 종류의 Handler가 등록되지 않은 경우입니다."""

    def __init__(self, handler_type: JobType) -> None:
        super().__init__(
            failure_code="INTERNAL_ERROR",
        )
        self.handler_type = handler_type


class HandlerAlreadyRegisteredError(WorkerError):
    """같은 Job 종류의 Handler를 중복 등록한 경우입니다."""

    def __init__(self, handler_type: JobType) -> None:
        super().__init__(
            failure_code="INTERNAL_ERROR",
        )
        self.handler_type = handler_type


class HandlerResultMismatchError(WorkerError):
    """Handler 결과가 입력 메시지 식별자와 일치하지 않는 경우입니다."""

    def __init__(self, handler_type: JobType) -> None:
        super().__init__(
            failure_code="INTERNAL_ERROR",
        )
        self.handler_type = handler_type


class HandlerExecutionError(WorkerError):
    """분류되지 않은 Handler 예외를 안전한 오류로 변환합니다."""

    def __init__(self, handler_type: JobType) -> None:
        super().__init__(
            failure_code="INTERNAL_ERROR",
        )
        self.handler_type = handler_type


class ConsumerPersistenceError(WorkerError):
    """Worker 결과 저장 또는 DB commit 실패를 안전하게 변환합니다."""

    def __init__(self) -> None:
        super().__init__(
            failure_code="DEPENDENCY_UNAVAILABLE",
        )


class ConsumerAcknowledgementError(WorkerError):
    """DB commit 이후 Stream ACK 실패를 안전하게 변환합니다."""

    def __init__(self) -> None:
        super().__init__(
            failure_code="DEPENDENCY_UNAVAILABLE",
        )
