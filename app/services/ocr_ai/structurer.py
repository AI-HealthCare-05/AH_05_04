import asyncio
import math
from dataclasses import dataclass
from typing import Protocol

from app.services.medication_name_normalizer import (
    MedicationNameNormalizer,
)
from app.services.ocr_ai.client import OcrStructureProvider
from app.services.ocr_ai.prompt import (
    PROMPT_VERSION,
    SYSTEM_INSTRUCTIONS,
)
from app.services.ocr_ai.schemas import (
    OcrSourceToken,
    OcrStructureInput,
)
from app.services.ocr_ai.validator import (
    validate_and_convert_draft,
)
from app.services.ocr_engine import (
    OcrProcessingError,
    OcrProviderTimeoutError,
    RawRecognizedField,
    RecognizedField,
)


@dataclass(frozen=True)
class OcrStructureResult:
    """LLM 구조화 결과와 재현에 필요한 실행 정보입니다."""

    fields: list[RecognizedField]
    model_name: str
    prompt_version: str


class OcrStructurer(Protocol):
    async def structure(
        self,
        raw_fields: list[RawRecognizedField],
    ) -> OcrStructureResult: ...


class LlmPrescriptionStructurer:
    """CLOVA가 인식한 전체 token을 LLM Structured Outputs로 변환합니다."""

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

    async def structure(
        self,
        raw_fields: list[RawRecognizedField],
    ) -> OcrStructureResult:
        if not raw_fields:
            raise OcrProcessingError("구조화할 OCR token이 없습니다.")

        structure_input = OcrStructureInput(
            tokens=[
                OcrSourceToken(
                    # source_id는 LLM 결과와 CLOVA 원문을 연결하는 근거 ID입니다.
                    source_id=source_id,
                    text=field.raw_value,
                    center_x=field.center_x,
                    center_y=field.center_y,
                    height=field.height,
                    confidence=field.confidence_score,
                )
                for source_id, field in enumerate(
                    raw_fields,
                    start=1,
                )
            ]
        )

        try:
            async with asyncio.timeout(self._timeout_seconds):
                provider_response = await self._provider.generate(
                    model=self._model,
                    instructions=SYSTEM_INSTRUCTIONS,
                    # 특정 실패 항목이 아니라 CLOVA가 인식한 전체 token을 전달합니다.
                    input_json=structure_input.model_dump_json(),
                    max_output_tokens=4000,
                )
        except TimeoutError as error:
            raise OcrProviderTimeoutError("OCR 구조화 AI 응답 제한시간을 초과했습니다.") from error

        fields = validate_and_convert_draft(
            draft=provider_response.draft,
            raw_fields=raw_fields,
            normalizer=self._normalizer,
        )

        return OcrStructureResult(
            fields=fields,
            model_name=provider_response.model_name,
            prompt_version=PROMPT_VERSION,
        )
