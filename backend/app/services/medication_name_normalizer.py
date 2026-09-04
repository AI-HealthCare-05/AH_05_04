"""공용 OCR 약품명 정규화 구현의 Backend 호환 진입점입니다."""

from ocr_runtime.medication_name_normalizer import (
    MedicationNameNormalizer,
    NormalizedMedicationName,
)

__all__ = [
    "MedicationNameNormalizer",
    "NormalizedMedicationName",
]
