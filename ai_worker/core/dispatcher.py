"""검증된 Worker 메시지를 알맞은 Handler로 전달합니다."""

from ai_worker.core.errors import (
    HandlerExecutionError,
    HandlerResultMismatchError,
    WorkerError,
)
from ai_worker.core.registry import HandlerRegistry
from ai_worker.core.results import HandlerSuccess
from ai_worker.core.retry import FailureCode
from ai_worker.schemas.messages import WorkerMessage


class Dispatcher:
    """message.job_type에 따라 등록된 Handler를 실행합니다."""

    def __init__(self, registry: HandlerRegistry) -> None:
        self._registry = registry

    async def dispatch(self, message: WorkerMessage) -> HandlerSuccess:
        """Handler를 선택하고 결과 식별자의 일치 여부를 검증합니다."""

        # 외부 Stream 계약의 job_type을 내부 handler_type으로 사용합니다.
        handler = self._registry.get(message.job_type)

        result: HandlerSuccess | None = None
        classified_failure_code: FailureCode | None = None
        execution_failed = False

        try:
            result = await handler.handle(message)
        except WorkerError as exc:
            # Handler 오류도 승인된 코드·고정 메시지 조합일 때만 전달합니다.
            # 계약을 위반한 오류에는 Provider 응답이나 secret이 포함될 수
            # 있으므로 failure_code만 꺼내고 예외 객체는 버립니다.
            if exc.has_safe_contract():
                classified_failure_code = exc.failure_code
            else:
                execution_failed = True
        except Exception:
            # 알 수 없는 예외의 원문에는 Provider 응답이나 secret이 포함될 수
            # 있으므로 예외 객체 자체를 밖으로 전달하지 않습니다.
            execution_failed = True

        # 활성 예외 처리 구간을 벗어난 뒤 새 오류를 만들어
        # __cause__와 __context__ 어디에도 원본 예외가 남지 않게 합니다.
        if execution_failed:
            raise HandlerExecutionError(message.job_type)

        if classified_failure_code is not None:
            raise WorkerError(failure_code=classified_failure_code)

        # Handler가 반환 타입 계약을 위반해도 내부 예외를 노출하지 않습니다.
        if not isinstance(result, HandlerSuccess):
            raise HandlerResultMismatchError(message.job_type)

        # 다른 Job 또는 event의 결과가 현재 실행에 잘못 연결되는 것을 막습니다.
        if (
            result.event_id != message.event_id
            or result.job_id != message.job_id
            or result.handler_type != message.job_type
        ):
            raise HandlerResultMismatchError(message.job_type)

        return result
