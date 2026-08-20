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
        self.error = error
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
        if self.error is not None:
            raise self.error
        return ProviderChatResponse(content="합성 답변", model_name="gpt-4o-mini-2024-07-18")


def reply_input(*, count: int = 1) -> ChatReplyInput:
    return ChatReplyInput(
        prescription_id=uuid4(),
        medications=[
            ChatMedicationInput(
                medication_name=f"합성약 {index}",
                dose_value=Decimal("0.500"),
                dose_unit="mg",
                frequency_per_day=2,
                timing_text="아침 식후",
                duration_days=7,
            )
            for index in range(count)
        ],
        content="현재 질문",
    )


async def test_reply_preserves_decimal_duration_and_excludes_backend_identifiers() -> None:
    provider = CapturingProvider()
    engine = ChatGeneratorEngine(provider=provider, model="gpt-4o-mini", timeout_seconds=1)

    result = await engine.reply(reply_input())
    payload = json.loads(str(provider.calls[0]["input_json"]))

    assert payload == {
        "question": "현재 질문",
        "medications": [
            {
                "medication_name": "합성약 0",
                "dose_value": "0.500",
                "dose_unit": "mg",
                "frequency_per_day": 2,
                "timing_text": "아침 식후",
                "duration_days": 7,
            }
        ],
    }
    assert "prescription_id" not in str(provider.calls[0]["input_json"])
    assert result.content == "합성 답변"
    assert result.model_name == "gpt-4o-mini-2024-07-18"
    assert result.prompt_version == "chat-prompt-v1"


@pytest.mark.parametrize(
    ("provider_error", "backend_error"),
    [
        (ChatGenerationTimeoutError("raw timeout payload"), ChatTimeoutError),
        (ChatGenerationUnavailableError("raw unavailable payload"), ChatServiceUnavailableError),
        (ChatGenerationInvalidResponseError("raw response payload"), ChatGenerationFailedError),
    ],
)
async def test_reply_maps_known_errors_without_retaining_raw_exception_chain(
    provider_error: Exception,
    backend_error: type[Exception],
) -> None:
    engine = ChatGeneratorEngine(
        provider=CapturingProvider(error=provider_error),
        model="gpt-4o-mini",
        timeout_seconds=1,
    )

    with pytest.raises(backend_error) as raised:
        await engine.reply(reply_input())

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


async def test_reply_rejects_thirty_one_medications_without_calling_provider() -> None:
    provider = CapturingProvider()
    engine = ChatGeneratorEngine(provider=provider, model="gpt-4o-mini", timeout_seconds=1)

    with pytest.raises(ChatGenerationFailedError) as raised:
        await engine.reply(reply_input(count=31))

    assert provider.calls == []
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("model", "timeout_seconds"),
    [
        ("", 1.0),
        ("gpt-4o-mini", 0.0),
        ("gpt-4o-mini", -1.0),
        ("gpt-4o-mini", float("nan")),
        ("gpt-4o-mini", float("inf")),
    ],
)
async def test_reply_sanitizes_invalid_configuration(model: str, timeout_seconds: float) -> None:
    provider = CapturingProvider()
    engine = ChatGeneratorEngine(provider=provider, model=model, timeout_seconds=timeout_seconds)

    with pytest.raises(ChatGenerationFailedError) as raised:
        await engine.reply(reply_input())

    assert provider.calls == []
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


async def test_reply_sanitizes_empty_medication_input() -> None:
    provider = CapturingProvider()
    engine = ChatGeneratorEngine(provider=provider, model="gpt-4o-mini", timeout_seconds=1)
    chat_input = ChatReplyInput(prescription_id=uuid4(), medications=[], content="현재 질문")

    with pytest.raises(ChatGenerationFailedError) as raised:
        await engine.reply(chat_input)

    assert provider.calls == []
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


async def test_reply_does_not_hide_unexpected_programming_error() -> None:
    programming_error = RuntimeError("synthetic programming failure")
    engine = ChatGeneratorEngine(
        provider=CapturingProvider(error=programming_error),
        model="gpt-4o-mini",
        timeout_seconds=1,
    )

    with pytest.raises(RuntimeError) as raised:
        await engine.reply(reply_input())

    assert raised.value is programming_error


async def test_reply_sanitizes_invalid_medication_without_calling_provider() -> None:
    provider = CapturingProvider()
    engine = ChatGeneratorEngine(provider=provider, model="gpt-4o-mini", timeout_seconds=1)
    chat_input = reply_input()
    invalid = ChatMedicationInput(
        medication_name="합성약",
        dose_value=Decimal("0"),
        dose_unit="mg",
        frequency_per_day=1,
        timing_text="저녁 식후",
        duration_days=7,
    )
    chat_input = ChatReplyInput(
        prescription_id=chat_input.prescription_id,
        medications=[invalid],
        content=chat_input.content,
    )

    with pytest.raises(ChatGenerationFailedError) as raised:
        await engine.reply(chat_input)

    assert provider.calls == []
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
