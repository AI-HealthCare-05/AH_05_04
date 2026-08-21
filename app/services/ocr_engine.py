from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class RawRecognizedField:
    raw_value: str
    confidence_score: float | None
    center_x: float
    center_y: float


@dataclass(frozen=True)
class RecognizedField:
    medication_index: int
    field_type: str
    raw_value: str | None
    confidence_score: float | None
    normalized_value: str | None = None
    normalization_version: str | None = None


@dataclass(frozen=True)
class OcrRecognitionResult:
    raw_text: str = ""
    raw_fields: list[RawRecognizedField] = field(default_factory=list)
    fields: list[RecognizedField] = field(default_factory=list)


class OcrProviderUnavailableError(Exception):
    """CLOVA OCR 연결 실패·타임아웃 등 외부 서비스 장애. (-> 503 OCR_PROVIDER_UNAVAILABLE)"""


class OcrProcessingError(Exception):
    """CLOVA 호출은 성공했으나 응답 처리·구조화에 실패. (-> 500 OCR_PROCESSING_FAILED)"""


class OcrEngine(Protocol):
    async def recognize(self, *, object_key: str, file_mime_type: str) -> OcrRecognitionResult: ...


class OcrProviderConnectionError(OcrProviderUnavailableError):
    """CLOVA OCR 연결에 실패한 경우."""


class OcrProviderTimeoutError(OcrProviderUnavailableError):
    """CLOVA OCR 응답 제한시간을 초과한 경우."""


class NotConfiguredOcrEngine:
    """
    TODO(김지혜): NAVER CLOVA OCR 실제 연동.
    - object_key로 저장된 이미지를 읽어 CLOVA OCR API에 전송
    - 인식 결과를 RecognizedField 목록(field_type: MEDICATION_NAME/DOSE_VALUE/DOSE_UNIT/
      FREQUENCY_PER_DAY/TIMING/PRESCRIBED_DATE/DURATION_DAYS)으로 변환
    - 연결 실패·타임아웃은 OcrProviderUnavailableError, 응답 처리 실패는 OcrProcessingError를
      발생시켜야 OcrService가 명세된 503/500 오류로 변환합니다.
    """

    async def recognize(self, *, object_key: str, file_mime_type: str) -> OcrRecognitionResult:
        _ = object_key, file_mime_type
        raise NotImplementedError("OcrEngine 구현이 아직 연결되지 않았습니다.")
