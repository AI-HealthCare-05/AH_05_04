from app.services.ocr_ai.schemas import (
    GeneratedMedication,
    GeneratedPrescriptionDraft,
    GeneratedSourceValue,
    ProviderOcrStructureResponse,
)
from app.services.ocr_ai.structurer import LlmPrescriptionStructurer, RuleBasedPrescriptionStructurer
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


async def test_structurer_skips_llm_without_reviewed_minimization_selector() -> None:
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

    assert provider.calls == []

    assert result.fields == (await RuleBasedPrescriptionStructurer().structure(raw_fields)).fields
    assert result.model_name is None
    assert result.prompt_version is None
    assert result.llm_processing == "SKIPPED_MINIMIZATION"


async def test_rule_based_structurer_does_not_report_llm_metadata() -> None:
    structurer = RuleBasedPrescriptionStructurer()

    result = await structurer.structure(
        [
            _raw("2026-08-26", x=10, y=10),
        ]
    )

    # 규칙 기반 경로에서는 OpenAI 모델과 프롬프트가 실행되지 않습니다.
    assert result.model_name is None
    assert result.prompt_version is None
    assert result.llm_processing == "NOT_REQUESTED"
    assert result.fields[0].field_type == "PRESCRIBED_DATE"
    assert result.fields[0].raw_value == "2026-08-26"
