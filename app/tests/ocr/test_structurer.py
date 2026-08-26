import json

from app.services.ocr_ai.schemas import (
    GeneratedMedication,
    GeneratedPrescriptionDraft,
    GeneratedSourceValue,
    ProviderOcrStructureResponse,
)
from app.services.ocr_ai.structurer import LlmPrescriptionStructurer
from app.services.ocr_engine import RawRecognizedField


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def generate(
        self,
        **kwargs: object,
    ) -> ProviderOcrStructureResponse:
        self.calls.append(kwargs)

        return ProviderOcrStructureResponse(
            draft=GeneratedPrescriptionDraft(
                prescribed_date=GeneratedSourceValue(
                    value="2026-08-26",
                    source_ids=[1],
                ),
                medications=[
                    GeneratedMedication(
                        medication_name=GeneratedSourceValue(
                            value="합성의약품에이정",
                            source_ids=[2],
                        ),
                        strength_text=GeneratedSourceValue(
                            value="100mg",
                            source_ids=[3],
                        ),
                        dose_value=GeneratedSourceValue(
                            value="1",
                            source_ids=[4],
                        ),
                        dose_unit=GeneratedSourceValue(
                            value="정",
                            source_ids=[4],
                        ),
                        frequency_per_day=GeneratedSourceValue(
                            value="2",
                            source_ids=[5],
                        ),
                        duration_days=GeneratedSourceValue(
                            value="3",
                            source_ids=[6],
                        ),
                    )
                ],
            ),
            model_name="actual-test-model-id",
        )


def _raw(
    value: str,
    *,
    x: float,
    y: float,
) -> RawRecognizedField:
    return RawRecognizedField(
        raw_value=value,
        confidence_score=0.99,
        center_x=x,
        center_y=y,
        height=10,
    )


async def test_structurer_sends_all_clova_tokens_and_splits_strength() -> None:
    provider = RecordingProvider()
    structurer = LlmPrescriptionStructurer(
        provider=provider,
        model="configured-model",
        timeout_seconds=1,
    )

    raw_fields = [
        _raw("2026-08-26", x=10, y=10),
        _raw("합성의약품에이정", x=10, y=30),
        _raw("100mg", x=100, y=30),
        _raw("1정", x=200, y=30),
        _raw("2회", x=300, y=30),
        _raw("3일", x=400, y=30),
    ]

    result = await structurer.structure(raw_fields)

    call = provider.calls[0]
    payload = json.loads(str(call["input_json"]))

    assert [token["text"] for token in payload["tokens"]] == [
        field.raw_value
        for field in raw_fields
    ]

    fields = {
        field.field_type: field.raw_value
        for field in result.fields
    }

    assert fields["MEDICATION_NAME"] == "합성의약품에이정"
    assert fields["MEDICATION_STRENGTH"] == "100mg"
    assert fields["DOSE_VALUE"] == "1"
    assert fields["DOSE_UNIT"] == "정"
    assert result.model_name == "actual-test-model-id"
    assert result.prompt_version == "ocr-structure-prompt-v2"
