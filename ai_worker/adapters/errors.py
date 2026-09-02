"""Stream Adapter에서 사용하는 안전한 오류입니다."""

from ai_worker.core.errors import WorkerError


class StreamMessageEncodingError(WorkerError):
    def __init__(self) -> None:
        super().__init__(failure_code="INVALID_INPUT")


class StreamMessageDecodingError(WorkerError):
    def __init__(self) -> None:
        super().__init__(failure_code="UNSUPPORTED_SCHEMA")


class StreamOperationError(WorkerError):
    def __init__(self) -> None:
        super().__init__(failure_code="DEPENDENCY_UNAVAILABLE")
