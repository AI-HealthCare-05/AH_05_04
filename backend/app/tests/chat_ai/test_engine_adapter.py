import json
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.closed_demo_retrieval import ClosedDemoRetrievalExecutionError
from app.services.chat_ai import (
    ChatGenerationFailedError,
    ChatHistoryPair,
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


class StubClosedDemoEvidence:
    def __init__(self, content: str = "검증된 합성 MFDS 근거") -> None:
        self.display_order = 1
        self.source_code = "mfds"
        self.source_version = "v1"
        self.locator = "synthetic-locator"

        class _Content:
            def reveal(self) -> str:
                return content

        self.content = _Content()


class StubClosedDemoRetriever:
    def __init__(self, content: str = "검증된 합성 MFDS 근거") -> None:
        self._content = content

    async def retrieve(self, question: str) -> tuple[StubClosedDemoEvidence, ...]:
        assert question == "이 약을 먹으면 졸릴 수 있나요?"
        return (StubClosedDemoEvidence(self._content),)


class RejectingClosedDemoRetriever:
    async def retrieve(self, question: str) -> tuple[object, ...]:
        raise ClosedDemoRetrievalExecutionError("fail closed")


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
    assert result.prompt_version == "chat-prompt-v6"
    payload = json.loads(str(provider.calls[0]["input_json"]))
    assert payload == {
        "question": "이 약을 먹으면 졸릴 수 있나요?",
        "history": [],
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


async def test_reply_maps_history_without_backend_identifiers() -> None:
    provider = StubProvider()
    engine = ChatGeneratorEngine(provider=provider, model="model-requested", timeout_seconds=1)
    chat_input = _reply_input()
    chat_input = ChatReplyInput(
        prescription_id=chat_input.prescription_id,
        medications=chat_input.medications,
        content="현재 질문",
        history=[ChatHistoryPair(question="과거 질문", answer="과거 답변")],
    )

    result = await engine.reply(chat_input)

    payload = json.loads(str(provider.calls[0]["input_json"]))
    assert payload["history"] == [{"question": "과거 질문", "answer": "과거 답변"}]
    assert "prescription_id" not in payload
    assert result.prompt_version == "chat-prompt-v6"


async def test_reply_sends_only_hydrated_closed_demo_evidence_with_new_prompt_version() -> None:
    provider = StubProvider()
    engine = ChatGeneratorEngine(
        provider=provider,
        model="model-requested",
        timeout_seconds=1,
        closed_demo_retriever=StubClosedDemoRetriever(),  # type: ignore[arg-type]
    )

    result = await engine.reply(_reply_input())

    payload = json.loads(str(provider.calls[0]["input_json"]))
    assert payload["evidence"] == [
        {
            "display_order": 1,
            "source_code": "mfds",
            "source_version": "v1",
            "locator": "synthetic-locator",
            "content": "검증된 합성 MFDS 근거",
        }
    ]
    assert result.prompt_version == "chat-prompt-v7-closed-demo-evidence"
    assert "CLOSED_DEMO 검증 근거" in str(provider.calls[0]["instructions"])


async def test_reply_forwards_large_hydrated_closed_demo_evidence_without_truncation() -> None:
    large_content = "가" * 44_286
    provider = StubProvider()
    engine = ChatGeneratorEngine(
        provider=provider,
        model="model-requested",
        timeout_seconds=1,
        closed_demo_retriever=StubClosedDemoRetriever(large_content),  # type: ignore[arg-type]
    )

    result = await engine.reply(_reply_input())

    assert len(provider.calls) == 1
    payload = json.loads(str(provider.calls[0]["input_json"]))
    assert payload["evidence"] == [
        {
            "display_order": 1,
            "source_code": "mfds",
            "source_version": "v1",
            "locator": "synthetic-locator",
            "content": large_content,
        }
    ]
    assert set(payload["evidence"][0]) == {
        "display_order",
        "source_code",
        "source_version",
        "locator",
        "content",
    }
    assert len(payload["evidence"][0]["content"]) == 44_286
    assert result.prompt_version == "chat-prompt-v7-closed-demo-evidence"


async def test_reply_does_not_call_provider_when_closed_demo_retrieval_fails() -> None:
    provider = StubProvider()
    engine = ChatGeneratorEngine(
        provider=provider,
        model="model-requested",
        timeout_seconds=1,
        closed_demo_retriever=RejectingClosedDemoRetriever(),  # type: ignore[arg-type]
    )

    with pytest.raises(ChatGenerationFailedError):
        await engine.reply(_reply_input())

    assert provider.calls == []


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
