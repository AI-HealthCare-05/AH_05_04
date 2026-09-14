import math

from ocr_runtime.llm.client import OcrStructureProvider
from ocr_runtime.medication_name_normalizer import MedicationNameNormalizer
from ocr_runtime.structuring import OcrStructurer as OcrStructurer
from ocr_runtime.structuring import OcrStructureResult as OcrStructureResult
from ocr_runtime.structuring import RuleBasedPrescriptionStructurer as RuleBasedPrescriptionStructurer
from provider_contracts.ocr import (
    OcrProcessingError,
    RawRecognizedField,
)


class LlmPrescriptionStructurer:
    """검증된 최소 전송 selector가 연결되기 전에는 LLM 전송을 차단합니다."""

    def __init__(
        self,
        *,
        provider: OcrStructureProvider,
        model: str,
        timeout_seconds: float,
        normalizer: MedicationNameNormalizer | None = None,
    ) -> None:
        if not model.strip() or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("OCR 구조화 AI 설정이 올바르지 않습니다.")

        self._provider = provider
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._normalizer = normalizer if normalizer is not None else MedicationNameNormalizer()
        self._fallback = RuleBasedPrescriptionStructurer(normalizer=self._normalizer)

    async def structure(
        self,
        raw_fields: list[RawRecognizedField],
    ) -> OcrStructureResult:
        if not raw_fields:
            raise OcrProcessingError("구조화할 OCR token이 없습니다.")
        # 약품명은 자유 텍스트이므로 숫자/단위 정규식만으로는 식별정보와
        # 안전하게 분리할 수 없습니다. 승인된 selector가 없는 상태에서
        # 일부 토큰이나 OCR 원문을 LLM에 보내지 않습니다.
        fallback = await self._fallback.structure(raw_fields)
        return OcrStructureResult(
            fields=fallback.fields,
            model_name=None,
            prompt_version=None,
            llm_processing="SKIPPED_MINIMIZATION",
        )
