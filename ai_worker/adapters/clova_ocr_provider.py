"""기존 CLOVA OCR Engine을 Worker Provider 계약에 연결합니다."""

from ai_worker.tasks.ocr.handler import (
    OcrProviderResult,
    OcrProviderSchemaError,
    OcrRecognizedField,
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
)


class ClovaOcrProviderAdapter:
    """기존 OCR Engine 결과와 오류를 Worker 계약으로 변환합니다."""

    def __init__(self, engine: OcrEngine) -> None:
        self._engine = engine

    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: float,
    ) -> OcrProviderResult:
        """최소 입력만 전달하고 원문을 제외한 결과를 반환합니다."""

        engine_result: OcrRecognitionResult | None = None
        normalized_error: Exception | None = None

        try:
            engine_result = await self._engine.recognize(
                object_key=object_key,
                file_mime_type=file_mime_type,
                deadline=OcrDeadline(
                    provider_path_deadline=deadline,
                ),
            )
        except (
            OcrDeadlineExceededError,
            OcrProviderTimeoutError,
        ):
            normalized_error = WorkerOcrProviderTimeoutError()
        except (
            OcrProviderConnectionError,
            OcrProviderUnavailableError,
        ):
            normalized_error = WorkerOcrProviderUnavailableError()
        except OcrProcessingError:
            normalized_error = OcrProviderSchemaError()

        # 활성 Provider 예외 처리 구간 밖에서 새 오류를 발생시켜
        # Provider 응답·Secret·object key의 예외 연결을 제거합니다.
        if normalized_error is not None:
            raise normalized_error

        if engine_result is None:
            raise OcrProviderSchemaError()

        return OcrProviderResult(
            fields=tuple(
                OcrRecognizedField(
                    medication_index=field.medication_index,
                    field_type=field.field_type,
                    raw_value=field.raw_value,
                    confidence_score=field.confidence_score,
                    normalized_value=field.normalized_value,
                    normalization_version=field.normalization_version,
                )
                for field in engine_result.fields
            ),
            engine_name=engine_result.engine_name,
            model_version=engine_result.model_version,
            prompt_version=engine_result.prompt_version,
        )
