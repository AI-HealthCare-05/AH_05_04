"""Job 종류별 Handler 등록과 조회를 담당합니다."""

from ai_worker.core.errors import (
    HandlerAlreadyRegisteredError,
    HandlerNotRegisteredError,
)
from ai_worker.core.handler import Handler
from ai_worker.schemas.messages import JobType


class HandlerRegistry:
    """하나의 Job 종류에 하나의 Handler만 등록합니다."""

    def __init__(self) -> None:
        self._handlers: dict[JobType, Handler] = {}

    def register(self, handler: Handler) -> None:
        """Handler를 등록하고 중복 등록을 차단합니다."""

        handler_type = handler.handler_type

        if handler_type in self._handlers:
            raise HandlerAlreadyRegisteredError(handler_type)

        self._handlers[handler_type] = handler

    def get(self, handler_type: JobType) -> Handler:
        """등록된 Handler를 반환합니다."""

        try:
            return self._handlers[handler_type]
        except KeyError:
            raise HandlerNotRegisteredError(handler_type) from None

    @property
    def registered_types(self) -> frozenset[JobType]:
        """외부에서 변경할 수 없는 등록 현황을 반환합니다."""

        return frozenset(self._handlers)
