"""Backend와 AI Worker가 공유하는 OCR 구조화 계약과 규칙 기반 구현입니다."""

from dataclasses import dataclass
from typing import Protocol

from ocr_runtime.medication_name_normalizer import MedicationNameNormalizer
from ocr_runtime.prescription_ocr_structurer import PrescriptionOcrStructurer
from provider_contracts.ocr import RawRecognizedField, RecognizedField


@dataclass(frozen=True)
class OcrStructureResult:
    """구조화 결과와 재현에 필요한 실행 정보입니다."""

    fields: list[RecognizedField]
    model_name: str | None
    prompt_version: str | None


class OcrStructurer(Protocol):
    async def structure(
        self,
        raw_fields: list[RawRecognizedField],
    ) -> OcrStructureResult: ...


class RuleBasedPrescriptionStructurer:
    """외부 LLM을 호출하지 않는 규칙 기반 처방전 구조화기입니다."""

    def __init__(
        self,
        normalizer: MedicationNameNormalizer | None = None,
    ) -> None:
        self._structurer = PrescriptionOcrStructurer(
            normalizer=normalizer,
        )

    async def structure(
        self,
        raw_fields: list[RawRecognizedField],
    ) -> OcrStructureResult:
        fields = self._structurer.structure(raw_fields)

        return OcrStructureResult(
            fields=fields,
            model_name=None,
            prompt_version=None,
        )
