"""Fake Handler를 이용한 Registry·Dispatcher 단위 테스트입니다."""

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest

from ai_worker.core.dispatcher import Dispatcher
from ai_worker.core.errors import (
    HandlerAlreadyRegisteredError,
    HandlerExecutionError,
    HandlerNotRegisteredError,
    HandlerResultMismatchError,
    WorkerError,
)
from ai_worker.core.handler import HandlerExecutionContext
from ai_worker.core.registry import HandlerRegistry
from ai_worker.core.results import HandlerSuccess
from ai_worker.core.retry import FailureCode, calculate_retry_decision
from ai_worker.schemas.messages import JobType, WorkerMessage


def build_message(
    *,
    job_type: JobType = JobType.OCR,
) -> WorkerMessage:
    """의료정보를 포함하지 않는 합성 Worker 메시지를 생성합니다."""

    domain_type_by_job_type = {
        JobType.OCR: "OCR_JOB",
        JobType.GUIDE: "GUIDE",
        JobType.CHAT: "CHAT_MESSAGE",
    }
    now = datetime.now(UTC)

    return WorkerMessage.model_validate(
        {
            "schema_version": "1.0",
            "event_id": str(uuid4()),
            "event_kind": "JOB_EXECUTE",
            "job_id": str(uuid4()),
            "job_type": job_type.value,
            "domain_type": domain_type_by_job_type[job_type],
            "domain_id": str(uuid4()),
            "attempt": 1,
            "available_at": now.isoformat(),
            "enqueued_at": now.isoformat(),
            "trace_id": uuid4().hex,
        }
    )


class FakeHandler:
    """호출된 메시지를 기록하고 정상 결과를 반환하는 Fake입니다."""

    def __init__(self, handler_type: JobType) -> None:
        self.handler_type = handler_type
        self.received_messages: list[WorkerMessage] = []

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        self.received_messages.append(message)

        return HandlerSuccess(
            event_id=message.event_id,
            job_id=message.job_id,
            handler_type=self.handler_type,
        )


class DeadlineAwareFakeHandler:
    """Worker의 monotonic absolute deadline을 전달받는 Fake입니다."""

    handler_type = JobType.OCR

    def __init__(self) -> None:
        self.received_context: HandlerExecutionContext | None = None

    async def handle(
        self,
        message: WorkerMessage,
        *,
        context: HandlerExecutionContext | None = None,
    ) -> HandlerSuccess:
        self.received_context = context

        return HandlerSuccess(
            event_id=message.event_id,
            job_id=message.job_id,
            handler_type=self.handler_type,
        )


class FakeTimeoutHandler:
    """재시도 가능한 timeout 오류를 발생시키는 Fake입니다."""

    handler_type = JobType.OCR

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        # Handler는 오류 코드만 선택하며 메시지는 고정 계약에서 가져옵니다.
        raise WorkerError(
            failure_code="TIMEOUT",
        )


class FakeContextLeakingTimeoutHandler:
    """활성 예외 구간에서 분류 오류를 만드는 Fake입니다."""

    handler_type = JobType.OCR

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        try:
            raise RuntimeError("SYNTHETIC_PROVIDER_TIMEOUT_BODY")
        except RuntimeError:
            # from None을 써도 __context__에는 원본이 남습니다.
            raise WorkerError(failure_code="TIMEOUT") from None


class FakeInvalidFailureCodeHandler:
    """승인되지 않은 오류 코드를 발생시키는 잘못된 Handler입니다."""

    handler_type = JobType.OCR

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        # 정적 타입 검사를 우회해 런타임 방어가 동작하는지 확인합니다.
        invalid_code = cast(
            FailureCode,
            "SYNTHETIC_API_KEY_NOT_ALLOWED",
        )
        raise WorkerError(failure_code=invalid_code)


class FakeUnknownErrorHandler:
    """분류되지 않은 내부 예외를 재현하는 Fake입니다."""

    handler_type = JobType.OCR

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        raise RuntimeError("sensitive provider response must not escape")


class FakeMismatchedResultHandler:
    """다른 Job 결과가 반환되는 잘못된 Handler를 재현합니다."""

    handler_type = JobType.OCR

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        return HandlerSuccess(
            event_id=message.event_id,
            job_id=uuid4(),
            handler_type=self.handler_type,
        )


class FakeInvalidResultHandler:
    """공통 성공 결과가 아닌 값을 반환하는 잘못된 Handler입니다."""

    handler_type = JobType.OCR

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        # Handler 구현이 반환 타입 계약을 위반한 상황을 의도적으로 재현합니다.
        return None  # type: ignore[return-value]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "job_type",
    [
        JobType.OCR,
        JobType.GUIDE,
        JobType.CHAT,
    ],
)
async def test_dispatcher_routes_message_to_registered_handler(
    job_type: JobType,
) -> None:
    registry = HandlerRegistry()
    handler = FakeHandler(job_type)
    registry.register(handler)

    dispatcher = Dispatcher(registry)
    message = build_message(job_type=job_type)

    result = await dispatcher.dispatch(message)

    assert result == HandlerSuccess(
        event_id=message.event_id,
        job_id=message.job_id,
        handler_type=job_type,
    )
    assert handler.received_messages == [message]


@pytest.mark.asyncio
async def test_dispatcher_passes_execution_context_to_handler() -> None:
    registry = HandlerRegistry()
    handler = DeadlineAwareFakeHandler()
    registry.register(handler)

    dispatcher = Dispatcher(registry)
    message = build_message()
    context = HandlerExecutionContext(
        worker_deadline=1234.5,
    )

    result = await dispatcher.dispatch(
        message,
        context=context,
    )

    assert result == HandlerSuccess(
        event_id=message.event_id,
        job_id=message.job_id,
        handler_type=JobType.OCR,
    )
    assert handler.received_context is context


@pytest.mark.asyncio
async def test_unregistered_handler_is_rejected() -> None:
    dispatcher = Dispatcher(HandlerRegistry())
    message = build_message()

    with pytest.raises(HandlerNotRegisteredError) as exc_info:
        await dispatcher.dispatch(message)

    assert exc_info.value.handler_type is JobType.OCR
    assert exc_info.value.failure_code == "INTERNAL_ERROR"


def test_duplicate_handler_registration_is_rejected() -> None:
    registry = HandlerRegistry()
    registry.register(FakeHandler(JobType.OCR))

    with pytest.raises(HandlerAlreadyRegisteredError):
        registry.register(FakeHandler(JobType.OCR))


def test_registry_exposes_immutable_registered_types() -> None:
    registry = HandlerRegistry()
    registry.register(FakeHandler(JobType.OCR))
    registry.register(FakeHandler(JobType.GUIDE))

    assert registry.registered_types == frozenset(
        {
            JobType.OCR,
            JobType.GUIDE,
        }
    )


@pytest.mark.asyncio
async def test_mismatched_handler_result_is_rejected() -> None:
    registry = HandlerRegistry()
    registry.register(FakeMismatchedResultHandler())

    dispatcher = Dispatcher(registry)

    with pytest.raises(HandlerResultMismatchError):
        await dispatcher.dispatch(build_message())


@pytest.mark.asyncio
async def test_invalid_handler_result_type_is_rejected() -> None:
    registry = HandlerRegistry()
    registry.register(FakeInvalidResultHandler())

    dispatcher = Dispatcher(registry)

    with pytest.raises(HandlerResultMismatchError) as exc_info:
        await dispatcher.dispatch(build_message())

    assert exc_info.value.handler_type is JobType.OCR
    assert exc_info.value.failure_code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_classified_worker_error_is_preserved() -> None:
    registry = HandlerRegistry()
    registry.register(FakeTimeoutHandler())

    dispatcher = Dispatcher(registry)

    with pytest.raises(WorkerError) as exc_info:
        await dispatcher.dispatch(build_message())

    assert exc_info.value.failure_code == "TIMEOUT"

    # Dispatcher가 새 retry 정책을 만들지 않고,
    # 기존 #73 계산 로직이 같은 failure_code를 사용함을 확인합니다.
    decision = calculate_retry_decision(
        attempt_count=1,
        max_attempts=3,
        failure_code=exc_info.value.failure_code,
        random_value=lambda: 0.0,
    )

    assert decision.should_retry is True
    assert decision.delay_seconds == 5.0


def test_worker_error_rejects_unapproved_failure_code() -> None:
    """정적 타입을 우회한 오류 코드도 런타임에서 차단합니다."""

    invalid_code = cast(
        FailureCode,
        "SYNTHETIC_API_KEY_NOT_ALLOWED",
    )

    with pytest.raises(
        ValueError,
        match="승인되지 않은 failure_code",
    ):
        WorkerError(failure_code=invalid_code)


def test_worker_error_rejects_freeform_safe_message() -> None:
    """Handler가 민감정보를 자유 오류 문구로 전달하지 못하게 합니다."""

    with pytest.raises(TypeError):
        WorkerError(  # type: ignore[call-arg]
            failure_code="TIMEOUT",
            safe_message="SYNTHETIC_PROVIDER_SECRET",
        )


@pytest.mark.asyncio
async def test_invalid_failure_code_is_converted_without_sensitive_value() -> None:
    """잘못된 오류 코드가 Dispatcher 외부로 전달되지 않게 합니다."""

    registry = HandlerRegistry()
    registry.register(FakeInvalidFailureCodeHandler())

    dispatcher = Dispatcher(registry)

    with pytest.raises(HandlerExecutionError) as exc_info:
        await dispatcher.dispatch(build_message())

    assert exc_info.value.failure_code == "INTERNAL_ERROR"
    assert "SYNTHETIC_API_KEY_NOT_ALLOWED" not in str(exc_info.value)
    assert "SYNTHETIC_API_KEY_NOT_ALLOWED" not in exc_info.value.safe_message


@pytest.mark.asyncio
async def test_unknown_exception_is_converted_without_raw_message() -> None:
    registry = HandlerRegistry()
    registry.register(FakeUnknownErrorHandler())

    dispatcher = Dispatcher(registry)

    with pytest.raises(HandlerExecutionError) as exc_info:
        await dispatcher.dispatch(build_message())

    assert exc_info.value.failure_code == "INTERNAL_ERROR"
    assert "sensitive provider response" not in exc_info.value.safe_message
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


@pytest.mark.asyncio
async def test_classified_worker_error_does_not_carry_original_context() -> None:
    """분류된 오류에도 Provider 원문이 예외 chain으로 남지 않게 합니다."""

    registry = HandlerRegistry()
    registry.register(FakeContextLeakingTimeoutHandler())

    dispatcher = Dispatcher(registry)

    with pytest.raises(WorkerError) as exc_info:
        await dispatcher.dispatch(build_message())

    error = exc_info.value

    assert error.failure_code == "TIMEOUT"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "SYNTHETIC_PROVIDER_TIMEOUT_BODY" not in str(error)


@pytest.mark.asyncio
async def test_unclassified_error_does_not_carry_original_context() -> None:
    """미분류 예외의 원문이 예외 chain으로 남지 않게 합니다."""

    registry = HandlerRegistry()
    registry.register(FakeUnknownErrorHandler())

    dispatcher = Dispatcher(registry)

    with pytest.raises(HandlerExecutionError) as exc_info:
        await dispatcher.dispatch(build_message())

    error = exc_info.value

    assert error.__cause__ is None
    assert error.__context__ is None
