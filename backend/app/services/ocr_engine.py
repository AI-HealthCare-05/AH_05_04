from dataclasses import dataclass, field
from time import monotonic
from typing import Protocol


@dataclass(frozen=True)
class RawRecognizedField:
    raw_value: str
    confidence_score: float | None
    center_x: float
    center_y: float
    height: float = 20.0  # height에 기본값을 둬서 기존 테스트와 다른 코드가 바로 깨지지 않게 합니다.


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

    # 실제 OCR 및 LLM 구조화 실행 정보를 OcrJob에 저장합니다.
    engine_name: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None


class OcrProviderUnavailableError(Exception):
    """CLOVA OCR 연결 실패·타임아웃 등 외부 서비스 장애. (-> 503 OCR_PROVIDER_UNAVAILABLE)"""


class OcrProcessingError(Exception):
    """CLOVA 호출은 성공했으나 응답 처리·구조화에 실패. (-> 500 OCR_PROCESSING_FAILED)"""


@dataclass(frozen=True, slots=True)
class OcrDeadline:
    """OCR 요청 전체에 적용되는 monotonic 예산입니다.

    wall clock 변경에 영향받지 않도록 time.monotonic 기준으로 계산합니다.
    각 Provider 호출 직전에 remaining()을 다시 읽어 개별 상한과 비교합니다.
    """

    provider_path_deadline: float

    @classmethod
    def start(cls, *, total_seconds: float, response_margin_seconds: float) -> "OcrDeadline":
        # 응답 생성과 실패 상태 저장 여유를 제외한 시점을 Provider 경로의 hard stop으로 둡니다.
        return cls(provider_path_deadline=monotonic() + total_seconds - response_margin_seconds)

    def remaining(self) -> float:
        return max(0.0, self.provider_path_deadline - monotonic())

    def timeout_for(self, provider_limit_seconds: float) -> float:
        """개별 Provider 상한과 남은 예산 중 작은 값을 돌려줍니다."""
        return min(provider_limit_seconds, self.remaining())


class OcrEngine(Protocol):
    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: OcrDeadline,
    ) -> OcrRecognitionResult: ...


class OcrDeadlineExceededError(OcrProcessingError):
    """남은 예산이 없어 Provider를 호출하지 않고 종료한 경우입니다."""


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

    async def recognize(self, *, object_key: str, file_mime_type: str, deadline: OcrDeadline) -> OcrRecognitionResult:
        _ = object_key, file_mime_type, deadline
        raise NotImplementedError("OcrEngine 구현이 아직 연결되지 않았습니다.")
