import json
from decimal import Decimal
from uuid import uuid4

from app.services.chat_ai import ChatMedicationInput, ChatReplyInput
from app.services.chat_ai.schemas import ProviderChatResponse
from app.services.chat_generator_engine import ChatGeneratorEngine


class RecordingProvider:
    def __init__(self) -> None:
        self.input_json: str | None = None

    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderChatResponse:
        self.input_json = input_json
        return ProviderChatResponse(content="합성 계약 답변", model_name="provider-model-2026-08")


async def test_backend_dto_crosses_adapter_and_real_generator_without_identifiers_or_history() -> None:
    prescription_id = uuid4()
    provider = RecordingProvider()
    engine = ChatGeneratorEngine(provider=provider, model="configured-model", timeout_seconds=1)
    backend_input = ChatReplyInput(
        prescription_id=prescription_id,
        content="현재 질문만 전달해 주세요.",
        medications=[
            ChatMedicationInput(
                medication_name="합성약 정밀",
                dose_value=Decimal("1.23000000000000000001"),
                dose_unit="mg",
                frequency_per_day=3,
                timing_text="식후",
                duration_days=9,
            ),
            ChatMedicationInput(
                medication_name="합성약 불완전",
                dose_value=Decimal("5"),
                dose_unit=None,
                frequency_per_day=None,
                timing_text=None,
                duration_days=None,
            ),
        ],
    )

    result = await engine.reply(backend_input)

    assert provider.input_json is not None
    assert json.loads(provider.input_json) == {
        "question": "현재 질문만 전달해 주세요.",
        "medications": [
            {
                "medication_name": "합성약 정밀",
                "dose_value": "1.23000000000000000001",
                "dose_unit": "mg",
                "frequency_per_day": 3,
                "timing_text": "식후",
                "duration_days": 9,
            },
            {"medication_name": "합성약 불완전"},
        ],
    }
    assert str(prescription_id) not in provider.input_json
    assert "history" not in provider.input_json
    assert result.content == "합성 계약 답변"
    assert result.model_name == "provider-model-2026-08"
    assert result.prompt_version == "chat-prompt-v1"
