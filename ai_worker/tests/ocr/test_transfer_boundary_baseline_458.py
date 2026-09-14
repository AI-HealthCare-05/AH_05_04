"""전송 대상을 검증할 수 없으면 LLM을 생략하는 #458 회귀 테스트입니다."""

from unittest.mock import AsyncMock

import pytest

from ocr_runtime.llm.schemas import (
    GeneratedMedication,
    GeneratedPrescriptionDraft,
    GeneratedSourceValue,
    ProviderOcrStructureResponse,
)
from ocr_runtime.llm.structurer import LlmPrescriptionStructurer
from ocr_runtime.llm.validator import validate_and_convert_draft
from ocr_runtime.medication_name_normalizer import MedicationNameNormalizer
from ocr_runtime.structuring import RuleBasedPrescriptionStructurer
from provider_contracts.ocr import OcrProcessingError, RawRecognizedField


def synthetic_fields():
    return [
        RawRecognizedField(
            raw_value="SYNTHETIC_PRIVATE_SENTINEL_458",
            center_x=10,
            center_y=10,
            height=10,
            confidence_score=0.9,
        ),
        RawRecognizedField(
            raw_value="합성의약품에이정",
            center_x=10,
            center_y=60,
            height=10,
            confidence_score=0.99,
        ),
    ]


def draft(source_id=2):
    return GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(medication_name=GeneratedSourceValue(value="합성의약품에이정", source_ids=[source_id]))
        ]
    )


async def test_unapproved_transfer_never_calls_llm_and_keeps_local_review_fields():
    provider = AsyncMock()
    provider.generate.return_value = ProviderOcrStructureResponse(draft=draft(), model_name="synthetic-model")
    structurer = LlmPrescriptionStructurer(provider=provider, model="synthetic-model", timeout_seconds=1)

    result = await structurer.structure(synthetic_fields())

    provider.generate.assert_not_awaited()
    assert result.llm_processing == "SKIPPED_MINIMIZATION"
    assert result.model_name is None
    assert result.prompt_version is None
    assert result.fields == (await RuleBasedPrescriptionStructurer().structure(synthetic_fields())).fields


def test_filtering_list_without_preserving_source_ids_breaks_original_reference():
    with pytest.raises(OcrProcessingError):
        validate_and_convert_draft(
            draft=draft(),
            raw_fields=synthetic_fields()[1:],
            normalizer=MedicationNameNormalizer(),
        )


def test_current_validator_has_no_sent_token_allowlist():
    # Even if a caller withheld token 2, the validator knows only the full list.
    # A future integration must separately restrict generated IDs to sent IDs.
    result = validate_and_convert_draft(
        draft=draft(), raw_fields=synthetic_fields(), normalizer=MedicationNameNormalizer()
    )
    assert any(field.raw_value == "합성의약품에이정" for field in result)


def test_unknown_source_id_is_rejected():
    with pytest.raises(OcrProcessingError):
        validate_and_convert_draft(
            draft=draft(999), raw_fields=synthetic_fields(), normalizer=MedicationNameNormalizer()
        )
