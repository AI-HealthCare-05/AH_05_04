"""검증된 Worker 메시지를 알맞은 Handler로 전달합니다."""

from ai_worker.core.errors import (
    HandlerExecutionError,
    HandlerResultMismatchError,
    WorkerError,
)
from ai_worker.core.registry import HandlerRegistry
from ai_worker.core.results import HandlerSuccess
from ai_worker.schemas.messages import WorkerMessage


class Dispatcher:
    """message.job_type에 따라 등록된 Handler를 실행합니다."""

    def __init__(self, registry: HandlerRegistry) -> None:
        self._registry = registry

    async def dispatch(self, message: WorkerMessage) -> HandlerSuccess:
        """Handler를 선택하고 결과 식별자의 일치 여부를 검증합니다."""

        # 외부 Stream 계약의 job_type을 내부 handler_type으로 사용합니다.
        handler = self._registry.get(message.job_type)

        try:
            result = await handler.handle(message)
        except WorkerError:
            # Handler가 이미 안전하게 분류한 오류는 그대로 전달합니다.
            raise
        except Exception:
            # 알 수 없는 예외의 원문에는 Provider 응답이나 secret이 포함될 수
            # 있으므로 exception chain을 외부 공통 오류에 연결하지 않습니다.
            raise HandlerExecutionError(message.job_type) from None

        # 다른 Job 또는 event의 결과가 현재 실행에 잘못 연결되는 것을 막습니다.
        if (
            result.event_id != message.event_id
            or result.job_id != message.job_id
            or result.handler_type != message.job_type
        ):
            raise HandlerResultMismatchError(message.job_type)

        return result
