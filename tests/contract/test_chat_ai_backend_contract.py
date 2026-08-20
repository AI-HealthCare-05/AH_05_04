import json
from decimal import Decimal
from uuid import uuid4

import pytest

from app.services.chat_ai import (
    ChatGenerationFailedError,
    ChatMedicationInput,
    ChatReplyInput,
    ChatServiceUnavailableError,
    ChatTimeoutError,
)
from app.services.chat_ai.exceptions import (
    ChatGenerationInvalidResponseError,
    ChatGenerationTimeoutError,
    ChatGenerationUnavailableError,
)
from app.services.chat_ai.schemas import ProviderChatResponse
from app.services.chat_generator_engine import ChatGeneratorEngine


class CapturingProvider:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[dict[str, object]] = []

    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderChatResponse:
        self.calls.append(
            {
                "model": model,
                "instructions": instructions,
                "input_json": input_json,
                "max_output_tokens": max_output_tokens,
            }
        )
        if self._error is not None:
            raise self._error
        return ProviderChatResponse(content="합성 계약 답변", model_name="synthetic-model-v1")


def _reply_input(*, medication_count: int = 2) -> ChatReplyInput:
    medications = [
        ChatMedicationInput(
            medication_name="  합성약\tA  ",
            dose_value=Decimal("0.500"),
            dose_unit="  mg  ",
            frequency_per_day=2,
            timing_text="\t아침\n식후  ",
            duration_days=7,
        ),
        ChatMedicationInput(
            medication_name="  합성약 B  ",
            dose_value=Decimal("1"),
            dose_unit=None,
            frequency_per_day=None,
            timing_text=None,
            duration_days=3,
        ),
    ]
    if medication_count != 2:
        medications = [
            ChatMedicationInput(
                medication_name=f"합성약 {index}",
                dose_value=Decimal("1"),
                dose_unit="mg",
                frequency_per_day=1,
                timing_text="아침 식후",
                duration_days=1,
            )
            for index in range(medication_count)
        ]
    return ChatReplyInput(
        prescription_id=uuid4(),
        medications=medications,
        content="\t합성 질문\n",
    )


async def test_backend_ai_contract_minimizes_provider_payload_and_preserves_reply_metadata() -> None:
    provider = CapturingProvider()
    engine = ChatGeneratorEngine(provider=provider, model="synthetic-model", timeout_seconds=1)

    result = await engine.reply(_reply_input())

    captured_input_json = str(provider.calls[0]["input_json"])
    payload = json.loads(captured_input_json)
    assert payload == {
        "question": "합성 질문",
        "medications": [
            {
                "medication_name": "합성약 A",
                "dose_value": "0.500",
                "dose_unit": "mg",
                "frequency_per_day": 2,
                "timing_text": "아침 식후",
                "duration_days": 7,
            },
            {
                "medication_name": "합성약 B",
                "duration_days": 3,
            },
        ],
    }
    assert result.content == "합성 계약 답변"
    assert result.model_name == "synthetic-model-v1"
    assert result.prompt_version == "chat-prompt-v1"
    assert "prescription_id" not in captured_input_json
    assert "session_id" not in captured_input_json
    assert "message_id" not in captured_input_json


@pytest.mark.parametrize(
    ("provider_error", "backend_error", "message"),
    [
        (
            ChatGenerationTimeoutError("raw timeout payload"),
            ChatTimeoutError,
            "챗봇 응답 생성 시간이 초과되었습니다.",
        ),
        (
            ChatGenerationUnavailableError("raw unavailable payload"),
            ChatServiceUnavailableError,
            "챗봇 생성 서비스를 사용할 수 없습니다.",
        ),
        (
            ChatGenerationInvalidResponseError("raw response payload"),
            ChatGenerationFailedError,
            "챗봇 응답 생성 처리에 실패했습니다.",
        ),
    ],
)
async def test_backend_ai_contract_maps_errors_without_provider_details_or_exception_chains(
    provider_error: Exception,
    backend_error: type[Exception],
    message: str,
) -> None:
    provider = CapturingProvider(error=provider_error)
    engine = ChatGeneratorEngine(provider=provider, model="synthetic-model", timeout_seconds=1)

    with pytest.raises(backend_error) as raised:
        await engine.reply(_reply_input())

    assert str(raised.value) == message
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert str(provider_error) not in str(raised.value)


async def test_backend_ai_contract_rejects_more_than_thirty_medications_before_provider_call() -> None:
    provider = CapturingProvider()
    engine = ChatGeneratorEngine(provider=provider, model="synthetic-model", timeout_seconds=1)

    with pytest.raises(ChatGenerationFailedError) as raised:
        await engine.reply(_reply_input(medication_count=31))

    assert str(raised.value) == "챗봇 응답 생성 처리에 실패했습니다."
    assert provider.calls == []
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
