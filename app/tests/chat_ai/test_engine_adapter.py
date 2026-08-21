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
    ChatGenerationConfigurationError,
    ChatGenerationInvalidResponseError,
    ChatGenerationTimeoutError,
    ChatGenerationUnavailableError,
)
from app.services.chat_ai.schemas import ProviderChatResponse
from app.services.chat_generator_engine import ChatGeneratorEngine


class StubProvider:
    def __init__(
        self,
        *,
        response: ProviderChatResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = (
            response
            if response is not None
            else ProviderChatResponse(content=" 안전한 합성 답변 ", model_name="model-actual")
        )
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def generate(self, **kwargs: object) -> ProviderChatResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _reply_input(*, medications: list[ChatMedicationInput] | None = None) -> ChatReplyInput:
    return ChatReplyInput(
        prescription_id=uuid4(),
        medications=medications
        if medications is not None
        else [
            ChatMedicationInput(
                medication_name="합성의약품 에이",
                dose_value=Decimal("0.1234567890123456789"),
                dose_unit="mg",
                frequency_per_day=2,
                timing_text="아침·저녁 식후",
                duration_days=14,
            )
        ],
        content="이 약을 먹으면 졸릴 수 있나요?",
    )


async def test_reply_maps_all_medication_fields_and_result_metadata() -> None:
    provider = StubProvider()
    engine = ChatGeneratorEngine(provider=provider, model="model-requested", timeout_seconds=1)

    result = await engine.reply(_reply_input())

    assert result.content == "안전한 합성 답변"
    assert result.model_name == "model-actual"
    assert result.prompt_version == "chat-prompt-v1"
    payload = json.loads(str(provider.calls[0]["input_json"]))
    assert payload == {
        "question": "이 약을 먹으면 졸릴 수 있나요?",
        "medications": [
            {
                "medication_name": "합성의약품 에이",
                "dose_value": "0.1234567890123456789",
                "dose_unit": "mg",
                "frequency_per_day": 2,
                "timing_text": "아침·저녁 식후",
                "duration_days": 14,
            }
        ],
    }
    assert "prescription_id" not in payload


@pytest.mark.parametrize(
    ("source_error", "expected_error"),
    [
        (ChatGenerationTimeoutError("provider details"), ChatTimeoutError),
        (ChatGenerationUnavailableError("provider details"), ChatServiceUnavailableError),
        (ChatGenerationConfigurationError("provider details"), ChatGenerationFailedError),
        (ChatGenerationInvalidResponseError("provider details"), ChatGenerationFailedError),
    ],
)
async def test_reply_maps_known_generation_errors_without_exception_chain(
    source_error: Exception,
    expected_error: type[Exception],
) -> None:
    engine = ChatGeneratorEngine(
        provider=StubProvider(error=source_error),
        model="model-requested",
        timeout_seconds=1,
    )

    with pytest.raises(expected_error) as exc_info:
        await engine.reply(_reply_input())

    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


@pytest.mark.parametrize(
    ("model", "timeout_seconds"),
    [(" ", 1), ("model-requested", 0), ("model-requested", float("nan"))],
)
async def test_invalid_configuration_fails_lazily_inside_reply(
    model: str,
    timeout_seconds: float,
) -> None:
    provider = StubProvider()
    engine = ChatGeneratorEngine(provider=provider, model=model, timeout_seconds=timeout_seconds)

    with pytest.raises(ChatGenerationFailedError) as exc_info:
        await engine.reply(_reply_input())

    assert provider.calls == []
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


async def test_invalid_backend_input_maps_validation_error_without_provider_call() -> None:
    provider = StubProvider()
    engine = ChatGeneratorEngine(provider=provider, model="model-requested", timeout_seconds=1)
    medications = [
        ChatMedicationInput(
            medication_name=f"합성약 {index}",
            dose_value=None,
            dose_unit=None,
            frequency_per_day=None,
            timing_text=None,
            duration_days=None,
        )
        for index in range(31)
    ]

    with pytest.raises(ChatGenerationFailedError) as exc_info:
        await engine.reply(_reply_input(medications=medications))

    assert provider.calls == []
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


async def test_unexpected_programming_error_propagates() -> None:
    programming_error = RuntimeError("synthetic programming error")
    engine = ChatGeneratorEngine(
        provider=StubProvider(error=programming_error),
        model="model-requested",
        timeout_seconds=1,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await engine.reply(_reply_input())

    assert exc_info.value is programming_error
