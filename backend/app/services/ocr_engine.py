"""OCR Provider 공유 계약의 Backend 호환 진입점입니다."""

from provider_contracts.ocr import (
    OcrDeadline,
    OcrDeadlineExceededError,
    OcrEngine,
    OcrProcessingError,
    OcrProviderConnectionError,
    OcrProviderTimeoutError,
    OcrProviderUnavailableError,
    OcrRecognitionResult,
    RawRecognizedField,
    RecognizedField,
)

__all__ = [
    "NotConfiguredOcrEngine",
    "OcrDeadline",
    "OcrDeadlineExceededError",
    "OcrEngine",
    "OcrProcessingError",
    "OcrProviderConnectionError",
    "OcrProviderTimeoutError",
    "OcrProviderUnavailableError",
    "OcrRecognitionResult",
    "RawRecognizedField",
    "RecognizedField",
]


class NotConfiguredOcrEngine:
    """OCR Engine이 주입되지 않은 경우 사용하는 안전한 기본 구현입니다."""

    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: OcrDeadline,
    ) -> OcrRecognitionResult:
        _ = object_key, file_mime_type, deadline
        raise NotImplementedError("OcrEngine 구현이 아직 연결되지 않았습니다.")
