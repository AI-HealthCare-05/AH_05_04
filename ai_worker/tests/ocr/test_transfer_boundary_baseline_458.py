"""Current-boundary evidence for #458, not acceptance of a minimized transfer policy.

The full-token characterization must be replaced with non-disclosure assertions
when the reviewed selector is connected. No real provider or patient data is used.
"""

import json
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


async def test_current_payload_still_contains_non_medication_sentinel():
    provider = AsyncMock()
    provider.generate.return_value = ProviderOcrStructureResponse(draft=draft(), model_name="synthetic-model")
    structurer = LlmPrescriptionStructurer(provider=provider, model="synthetic-model", timeout_seconds=1)

    result = await structurer.structure(synthetic_fields())

    provider.generate.assert_awaited_once()
    tokens = json.loads(provider.generate.call_args.kwargs["input_json"])["tokens"]
    # Evidence of the current gap, not a privacy pass or a desired future contract.
    assert tokens[0]["text"] == "SYNTHETIC_PRIVATE_SENTINEL_458"
    assert [token["source_id"] for token in tokens] == [1, 2]
    assert {"center_x", "center_y", "height", "confidence"} <= tokens[1].keys()
    assert any(field.raw_value == "합성의약품에이정" for field in result.fields)


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
