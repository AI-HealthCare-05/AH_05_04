"""Backend와 AI Worker가 공유하는 OCR Provider 계약입니다."""

from dataclasses import dataclass, field
from time import monotonic
from typing import Protocol


@dataclass(frozen=True)
class RawRecognizedField:
    raw_value: str
    confidence_score: float | None
    center_x: float
    center_y: float
    height: float = 20.0


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
    engine_name: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None


class OcrProviderUnavailableError(Exception):
    """연결 실패·timeout 등 일시적인 OCR Provider 장애입니다."""


class OcrProcessingError(Exception):
    """Provider 응답 처리 또는 구조화에 실패했습니다."""


@dataclass(frozen=True, slots=True)
class OcrDeadline:
    """OCR Provider 경로에 적용하는 monotonic absolute deadline입니다."""

    provider_path_deadline: float

    @classmethod
    def start(
        cls,
        *,
        total_seconds: float,
        response_margin_seconds: float,
    ) -> "OcrDeadline":
        return cls(provider_path_deadline=(monotonic() + total_seconds - response_margin_seconds))

    def remaining(self) -> float:
        return max(0.0, self.provider_path_deadline - monotonic())

    def timeout_for(self, provider_limit_seconds: float) -> float:
        return min(provider_limit_seconds, self.remaining())


class OcrEngine(Protocol):
    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: OcrDeadline,
    ) -> OcrRecognitionResult:
        """최소 Provider 입력만 사용해 OCR을 실행합니다."""
        ...


class OcrDeadlineExceededError(OcrProcessingError):
    """남은 OCR 실행 예산이 없습니다."""


class OcrProviderConnectionError(OcrProviderUnavailableError):
    """OCR Provider 연결에 실패했습니다."""


class OcrProviderTimeoutError(OcrProviderUnavailableError):
    """OCR Provider 응답 제한시간을 초과했습니다."""
