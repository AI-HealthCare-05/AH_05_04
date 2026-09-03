"""OCR Worker Handler의 정상 처리 계약 테스트입니다."""

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from ai_worker.core.dispatcher import Dispatcher
from ai_worker.core.errors import WorkerError
from ai_worker.core.handler import HandlerExecutionContext
from ai_worker.core.registry import HandlerRegistry
from ai_worker.schemas.messages import JobType, WorkerMessage
from ai_worker.tasks.ocr.handler import (
    OcrDomainInput,
    OcrHandler,
    OcrHandlerSuccess,
    OcrProviderInputError,
    OcrProviderResult,
    OcrProviderSafetyError,
    OcrProviderSchemaError,
    OcrProviderTimeoutError,
    OcrProviderUnavailableError,
    OcrRecognizedField,
)


@dataclass(frozen=True, slots=True)
class ProviderCall:
    object_key: str
    file_mime_type: str
    deadline: float


class FakeOcrInputRepository:
    def __init__(self, domain_input: OcrDomainInput | None) -> None:
        self._domain_input = domain_input
        self.received_lookups: list[tuple[UUID, UUID]] = []

    async def get_input(
        self,
        *,
        domain_id: UUID,
        job_id: UUID,
    ) -> OcrDomainInput | None:
        self.received_lookups.append((domain_id, job_id))
        return self._domain_input


class FakeOcrProvider:
    def __init__(self, result: OcrProviderResult) -> None:
        self._result = result
        self.calls: list[ProviderCall] = []

    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: float,
    ) -> OcrProviderResult:
        self.calls.append(
            ProviderCall(
                object_key=object_key,
                file_mime_type=file_mime_type,
                deadline=deadline,
            )
        )
        return self._result


class FailingOcrProvider:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls: list[ProviderCall] = []

    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: float,
    ) -> OcrProviderResult:
        self.calls.append(
            ProviderCall(
                object_key=object_key,
                file_mime_type=file_mime_type,
                deadline=deadline,
            )
        )
        raise self._error


def build_message(*, domain_id: UUID) -> WorkerMessage:
    return WorkerMessage.model_validate(
        {
            "schema_version": "1.0",
            "event_id": str(uuid4()),
            "event_kind": "JOB_EXECUTE",
            "job_id": str(uuid4()),
            "job_type": "OCR",
            "domain_type": "OCR_JOB",
            "domain_id": str(domain_id),
            "attempt": 1,
            "available_at": "2026-09-03T07:00:00+00:00",
            "enqueued_at": "2026-09-03T07:00:00+00:00",
            "trace_id": uuid4().hex,
        }
    )


@pytest.mark.asyncio
async def test_ocr_handler_loads_input_and_returns_normalized_result() -> None:
    domain_id = uuid4()
    message = build_message(domain_id=domain_id)
    domain_input = OcrDomainInput(
        object_key="synthetic/input.png",
        file_mime_type="image/png",
    )
    provider_result = OcrProviderResult(
        fields=(
            OcrRecognizedField(
                medication_index=1,
                field_type="MEDICATION_NAME",
                raw_value="합성 의약품",
                confidence_score=0.99,
                normalized_value=None,
                normalization_version=None,
            ),
        ),
        engine_name="CLOVA_OCR",
        model_version=None,
        prompt_version=None,
    )
    repository = FakeOcrInputRepository(domain_input)
    provider = FakeOcrProvider(provider_result)
    handler = OcrHandler(
        input_repository=repository,
        provider=provider,
        clock=lambda: 1000.0,
        provider_budget_seconds=55.0,
    )

    result = await handler.handle(
        message,
        context=HandlerExecutionContext(
            worker_deadline=1060.0,
        ),
    )

    assert repository.received_lookups == [
        (domain_id, message.job_id),
    ]
    assert provider.calls == [
        ProviderCall(
            object_key="synthetic/input.png",
            file_mime_type="image/png",
            deadline=1055.0,
        )
    ]
    assert result == OcrHandlerSuccess(
        event_id=message.event_id,
        job_id=message.job_id,
        handler_type=JobType.OCR,
        domain_id=domain_id,
        fields=provider_result.fields,
        engine_name="CLOVA_OCR",
        model_version=None,
        prompt_version=None,
    )


@pytest.mark.asyncio
async def test_ocr_handler_rejects_missing_domain_input() -> None:
    domain_id = uuid4()
    message = build_message(domain_id=domain_id)
    repository = FakeOcrInputRepository(None)
    provider = FakeOcrProvider(
        OcrProviderResult(
            fields=(),
            engine_name="CLOVA_OCR",
            model_version=None,
            prompt_version=None,
        )
    )
    handler = OcrHandler(
        input_repository=repository,
        provider=provider,
        clock=lambda: 1000.0,
        provider_budget_seconds=55.0,
    )

    with pytest.raises(WorkerError) as exc_info:
        await handler.handle(
            message,
            context=HandlerExecutionContext(
                worker_deadline=1060.0,
            ),
        )

    assert exc_info.value.failure_code == "INVALID_INPUT"
    assert repository.received_lookups == [
        (domain_id, message.job_id),
    ]
    assert provider.calls == []


@pytest.mark.asyncio
async def test_ocr_handler_rejects_exhausted_provider_budget() -> None:
    domain_id = uuid4()
    message = build_message(domain_id=domain_id)
    repository = FakeOcrInputRepository(
        OcrDomainInput(
            object_key="synthetic/input.png",
            file_mime_type="image/png",
        )
    )
    provider = FakeOcrProvider(
        OcrProviderResult(
            fields=(),
            engine_name="CLOVA_OCR",
            model_version=None,
            prompt_version=None,
        )
    )
    handler = OcrHandler(
        input_repository=repository,
        provider=provider,
        clock=lambda: 1000.0,
        provider_budget_seconds=55.0,
    )

    with pytest.raises(WorkerError) as exc_info:
        await handler.handle(
            message,
            context=HandlerExecutionContext(
                worker_deadline=1005.0,
            ),
        )

    assert exc_info.value.failure_code == "TIMEOUT"
    assert repository.received_lookups == [
        (domain_id, message.job_id),
    ]
    assert provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error", "expected_failure_code"),
    [
        (
            OcrProviderTimeoutError("SYNTHETIC_PROVIDER_TIMEOUT_DETAIL"),
            "TIMEOUT",
        ),
        (
            OcrProviderUnavailableError("SYNTHETIC_PROVIDER_SECRET"),
            "DEPENDENCY_UNAVAILABLE",
        ),
        (
            OcrProviderInputError("SYNTHETIC_PRIVATE_OBJECT_KEY"),
            "INVALID_INPUT",
        ),
        (
            OcrProviderSchemaError("SYNTHETIC_PROVIDER_RESPONSE"),
            "UNSUPPORTED_SCHEMA",
        ),
        (
            OcrProviderSafetyError("SYNTHETIC_OCR_CONTENT"),
            "SAFETY_VALIDATION_FAILED",
        ),
    ],
)
async def test_ocr_handler_normalizes_provider_errors(
    provider_error: Exception,
    expected_failure_code: str,
) -> None:
    domain_id = uuid4()
    message = build_message(domain_id=domain_id)
    repository = FakeOcrInputRepository(
        OcrDomainInput(
            object_key="synthetic/input.png",
            file_mime_type="image/png",
        )
    )
    provider = FailingOcrProvider(provider_error)
    handler = OcrHandler(
        input_repository=repository,
        provider=provider,
        clock=lambda: 1000.0,
        provider_budget_seconds=55.0,
    )

    with pytest.raises(WorkerError) as exc_info:
        await handler.handle(
            message,
            context=HandlerExecutionContext(
                worker_deadline=1060.0,
            ),
        )

    error = exc_info.value

    assert error.failure_code == expected_failure_code
    assert error.has_safe_contract()
    assert "SYNTHETIC" not in str(error)
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_registered_ocr_handler_is_dispatched_with_worker_context() -> None:
    domain_id = uuid4()
    message = build_message(domain_id=domain_id)
    repository = FakeOcrInputRepository(
        OcrDomainInput(
            object_key="synthetic/input.png",
            file_mime_type="image/png",
        )
    )
    provider = FakeOcrProvider(
        OcrProviderResult(
            fields=(),
            engine_name="CLOVA_OCR",
            model_version=None,
            prompt_version=None,
        )
    )
    handler = OcrHandler(
        input_repository=repository,
        provider=provider,
        clock=lambda: 1000.0,
        provider_budget_seconds=55.0,
    )
    registry = HandlerRegistry()
    registry.register(handler)
    dispatcher = Dispatcher(registry)

    result = await dispatcher.dispatch(
        message,
        context=HandlerExecutionContext(
            worker_deadline=1060.0,
        ),
    )

    assert registry.registered_types == frozenset({JobType.OCR})
    assert isinstance(result, OcrHandlerSuccess)
    assert result.event_id == message.event_id
    assert result.job_id == message.job_id
    assert result.handler_type is JobType.OCR
    assert result.domain_id == domain_id
    assert len(provider.calls) == 1
