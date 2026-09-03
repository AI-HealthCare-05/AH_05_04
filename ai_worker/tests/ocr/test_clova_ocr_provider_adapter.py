"""기존 CLOVA OCR Engine과 Worker Handler 사이의 Adapter 테스트입니다."""

from dataclasses import dataclass

import pytest

from ai_worker.adapters.clova_ocr_provider import ClovaOcrProviderAdapter
from ai_worker.tasks.ocr.handler import OcrProviderResult, OcrRecognizedField
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
    adapter = ClovaOcrProviderAdapter(engine)

    result = await adapter.recognize(
        object_key="synthetic/input.png",
        file_mime_type="image/png",
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
    adapter = ClovaOcrProviderAdapter(FailingOcrEngine(engine_error))

    with pytest.raises(expected_error_type) as exc_info:
        await adapter.recognize(
            object_key="synthetic/input.png",
            file_mime_type="image/png",
            deadline=1055.0,
        )

    assert "SYNTHETIC" not in str(exc_info.value)
