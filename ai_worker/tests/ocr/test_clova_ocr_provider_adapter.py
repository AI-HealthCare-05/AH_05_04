"""기존 CLOVA OCR Engine과 Worker Handler 사이의 Adapter 테스트입니다."""

import asyncio
import time
from dataclasses import dataclass
from typing import cast

import pytest

from ai_worker.adapters.clova_ocr_provider import ClovaOcrProviderAdapter
from ai_worker.tasks.ocr.handler import (
    OcrProviderInputError as WorkerOcrProviderInputError,
)
from ai_worker.tasks.ocr.handler import OcrProviderResult, OcrRecognizedField
from ai_worker.tasks.ocr.handler import (
    OcrProviderSafetyError as WorkerOcrProviderSafetyError,
)
from ai_worker.tasks.ocr.handler import (
    OcrProviderSchemaError as WorkerOcrProviderSchemaError,
)
from ai_worker.tasks.ocr.handler import (
    OcrProviderTimeoutError as WorkerOcrProviderTimeoutError,
)
from ai_worker.tasks.ocr.handler import (
    OcrProviderUnavailableError as WorkerOcrProviderUnavailableError,
)
from provider_contracts.ocr import (
    OcrDeadline,
    OcrDeadlineExceededError,
    OcrEngine,
    OcrProcessingError,
    OcrProviderConnectionError,
    OcrProviderTimeoutError,
    OcrProviderUnavailableError,
    OcrRecognitionResult,
    RecognizedField,
)


@dataclass(frozen=True, slots=True)
class EngineCall:
    object_key: str
    file_mime_type: str
    deadline: OcrDeadline


class FakeOcrEngine:
    def __init__(self, result: OcrRecognitionResult) -> None:
        self._result = result
        self.calls: list[EngineCall] = []

    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: OcrDeadline,
    ) -> OcrRecognitionResult:
        self.calls.append(
            EngineCall(
                object_key=object_key,
                file_mime_type=file_mime_type,
                deadline=deadline,
            )
        )
        return self._result


class FailingOcrEngine:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: OcrDeadline,
    ) -> OcrRecognitionResult:
        _ = object_key, file_mime_type, deadline
        raise self._error


class HangingOcrEngine:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: OcrDeadline,
    ) -> OcrRecognitionResult:
        _ = object_key, file_mime_type, deadline
        self.started.set()

        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

        raise AssertionError("Engine 실행이 취소되지 않았습니다.")


def test_clova_adapter_rejects_missing_engine_source() -> None:
    with pytest.raises(
        ValueError,
        match="engine과 engine_factory 중 정확히 하나가 필요합니다",
    ):
        ClovaOcrProviderAdapter()


def test_clova_adapter_rejects_multiple_engine_sources() -> None:
    engine = FakeOcrEngine(OcrRecognitionResult(fields=[]))

    with pytest.raises(
        ValueError,
        match="engine과 engine_factory 중 정확히 하나가 필요합니다",
    ):
        ClovaOcrProviderAdapter(
            engine,
            engine_factory=lambda _trace_id: engine,
        )


@pytest.mark.asyncio
async def test_clova_adapter_forwards_only_minimum_provider_input() -> None:
    engine = FakeOcrEngine(
        OcrRecognitionResult(
            fields=[
                RecognizedField(
                    medication_index=1,
                    field_type="MEDICATION_NAME",
                    raw_value="합성 의약품",
                    confidence_score=0.98,
                    normalized_value=None,
                    normalization_version=None,
                )
            ],
            engine_name="CLOVA_OCR",
            model_version=None,
            prompt_version=None,
        )
    )
    adapter = ClovaOcrProviderAdapter(
        engine,
        clock=lambda: 1000.0,
    )

    result = await adapter.recognize(
        object_key="synthetic/input.png",
        file_mime_type="image/png",
        trace_id="a" * 32,
        deadline=1055.0,
    )

    assert engine.calls == [
        EngineCall(
            object_key="synthetic/input.png",
            file_mime_type="image/png",
            deadline=OcrDeadline(
                provider_path_deadline=1055.0,
            ),
        )
    ]
    assert result == OcrProviderResult(
        fields=(
            OcrRecognizedField(
                medication_index=1,
                field_type="MEDICATION_NAME",
                raw_value="합성 의약품",
                confidence_score=0.98,
                normalized_value=None,
                normalization_version=None,
            ),
        ),
        engine_name="CLOVA_OCR",
        model_version=None,
        prompt_version=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_error", "expected_error_type"),
    [
        (
            OcrDeadlineExceededError("SYNTHETIC_DEADLINE_DETAIL"),
            WorkerOcrProviderTimeoutError,
        ),
        (
            OcrProviderTimeoutError("SYNTHETIC_TIMEOUT_DETAIL"),
            WorkerOcrProviderTimeoutError,
        ),
        (
            OcrProviderConnectionError("SYNTHETIC_CONNECTION_DETAIL"),
            WorkerOcrProviderUnavailableError,
        ),
        (
            OcrProviderUnavailableError("SYNTHETIC_PROVIDER_SECRET"),
            WorkerOcrProviderUnavailableError,
        ),
        (
            OcrProcessingError("SYNTHETIC_PROVIDER_RESPONSE"),
            WorkerOcrProviderSchemaError,
        ),
    ],
)
async def test_clova_adapter_normalizes_existing_engine_errors(
    engine_error: Exception,
    expected_error_type: type[Exception],
) -> None:
    adapter = ClovaOcrProviderAdapter(
        FailingOcrEngine(engine_error),
        clock=lambda: 1000.0,
    )

    with pytest.raises(expected_error_type) as exc_info:
        await adapter.recognize(
            object_key="synthetic/input.png",
            file_mime_type="image/png",
            trace_id="a" * 32,
            deadline=1055.0,
        )

    assert "SYNTHETIC" not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("object_key", "file_mime_type"),
    [
        ("", "image/png"),
        ("   ", "image/png"),
        ("synthetic/input.png", ""),
        ("synthetic/input.txt", "text/plain"),
    ],
)
async def test_clova_adapter_rejects_invalid_input_before_engine_call(
    object_key: str,
    file_mime_type: str,
) -> None:
    engine = FakeOcrEngine(OcrRecognitionResult())
    adapter = ClovaOcrProviderAdapter(
        engine,
        clock=lambda: 1000.0,
    )

    with pytest.raises(WorkerOcrProviderInputError):
        await adapter.recognize(
            object_key=object_key,
            file_mime_type=file_mime_type,
            trace_id="a" * 32,
            deadline=1055.0,
        )

    assert engine.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fields", "expected_error_type"),
    [
        (
            [
                RecognizedField(
                    medication_index=1,
                    field_type="UNKNOWN_FIELD",
                    raw_value="synthetic",
                    confidence_score=0.9,
                )
            ],
            WorkerOcrProviderSchemaError,
        ),
        (
            [
                RecognizedField(
                    medication_index=0,
                    field_type="MEDICATION_NAME",
                    raw_value="synthetic",
                    confidence_score=0.9,
                )
            ],
            WorkerOcrProviderSchemaError,
        ),
        (
            [
                RecognizedField(
                    medication_index=1,
                    field_type="MEDICATION_NAME",
                    raw_value="synthetic",
                    confidence_score=1.1,
                )
            ],
            WorkerOcrProviderSafetyError,
        ),
        (
            [
                RecognizedField(
                    medication_index=1,
                    field_type="MEDICATION_NAME",
                    raw_value="synthetic",
                    confidence_score=0.9,
                ),
                RecognizedField(
                    medication_index=1,
                    field_type="MEDICATION_NAME",
                    raw_value="synthetic duplicate",
                    confidence_score=0.8,
                ),
            ],
            WorkerOcrProviderSchemaError,
        ),
    ],
)
async def test_clova_adapter_rejects_invalid_normalized_result(
    fields: list[RecognizedField],
    expected_error_type: type[Exception],
) -> None:
    engine = FakeOcrEngine(
        OcrRecognitionResult(
            fields=fields,
            engine_name="CLOVA_OCR",
        )
    )
    adapter = ClovaOcrProviderAdapter(
        engine,
        clock=lambda: 1000.0,
    )

    with pytest.raises(expected_error_type):
        await adapter.recognize(
            object_key="synthetic/input.png",
            file_mime_type="image/png",
            trace_id="a" * 32,
            deadline=1055.0,
        )

    assert len(engine.calls) == 1


@pytest.mark.asyncio
async def test_clova_adapter_enforces_absolute_deadline() -> None:
    engine = HangingOcrEngine()
    adapter = ClovaOcrProviderAdapter(
        engine,
        clock=time.monotonic,
    )

    with pytest.raises(WorkerOcrProviderTimeoutError):
        await asyncio.wait_for(
            adapter.recognize(
                object_key="synthetic/input.png",
                file_mime_type="image/png",
                trace_id="a" * 32,
                deadline=time.monotonic() + 0.01,
            ),
            timeout=1,
        )

    assert engine.started.is_set()
    assert engine.cancelled.is_set()


@pytest.mark.asyncio
async def test_clova_adapter_rejects_expired_deadline_before_engine_call() -> None:
    engine = FakeOcrEngine(OcrRecognitionResult())
    adapter = ClovaOcrProviderAdapter(
        engine,
        clock=lambda: 1000.0,
    )

    with pytest.raises(WorkerOcrProviderTimeoutError):
        await adapter.recognize(
            object_key="synthetic/input.png",
            file_mime_type="image/png",
            trace_id="a" * 32,
            deadline=1000.0,
        )

    assert engine.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "medication_index",
    [
        True,
        1.5,
    ],
)
async def test_clova_adapter_rejects_non_integer_medication_index(
    medication_index: object,
) -> None:
    engine = FakeOcrEngine(
        OcrRecognitionResult(
            fields=[
                RecognizedField(
                    medication_index=cast(int, medication_index),
                    field_type="MEDICATION_NAME",
                    raw_value="synthetic",
                    confidence_score=0.9,
                )
            ],
            engine_name="CLOVA_OCR",
        )
    )
    adapter = ClovaOcrProviderAdapter(
        engine,
        clock=lambda: 1000.0,
    )

    with pytest.raises(WorkerOcrProviderSchemaError):
        await adapter.recognize(
            object_key="synthetic/input.png",
            file_mime_type="image/png",
            trace_id="a" * 32,
            deadline=1055.0,
        )

    assert len(engine.calls) == 1


@pytest.mark.asyncio
async def test_clova_adapter_builds_engine_for_each_trace_id() -> None:
    created_trace_ids: list[str] = []

    def create_engine(trace_id: str) -> OcrEngine:
        created_trace_ids.append(trace_id)
        return FakeOcrEngine(OcrRecognitionResult(fields=[]))

    adapter = ClovaOcrProviderAdapter(
        engine_factory=create_engine,
        clock=lambda: 1_000.0,
    )

    await adapter.recognize(
        object_key="prescriptions/a.png",
        file_mime_type="image/png",
        trace_id="a" * 32,
        deadline=1_055.0,
    )
    await adapter.recognize(
        object_key="prescriptions/b.png",
        file_mime_type="image/png",
        trace_id="b" * 32,
        deadline=1_055.0,
    )

    assert created_trace_ids == ["a" * 32, "b" * 32]
