from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.services.chat_ai.client import ChatProvider, OpenAIResponsesClient
from app.services.chat_ai.generator import ChatGenerator
from app.services.chat_ai.schemas import (
    ChatGenerationInput,
    ChatGenerationResult,
)


@dataclass(frozen=True)
class ChatMedicationInput:
    medication_name: str
    dose_value: Decimal | None
    dose_unit: str | None
    frequency_per_day: int | None
    timing_text: str | None
    duration_days: int | None


@dataclass(frozen=True)
class ChatReplyInput:
    prescription_id: UUID
    medications: list[ChatMedicationInput]
    content: str


@dataclass(frozen=True)
class ChatReplyOutput:
    content: str
    model_name: str
    prompt_version: str


class ChatServiceUnavailableError(Exception):
    """챗봇 LLM 호출 자체가 실패했을 때 발생합니다. (-> 503 SERVICE_UNAVAILABLE)"""


class ChatTimeoutError(Exception):
    """챗봇 응답 대기 시간이 초과됐을 때 발생합니다. (-> 504 GATEWAY_TIMEOUT)"""


class ChatGenerationFailedError(Exception):
    """Backend-safe chat input/configuration/response processing failure."""


class ChatEngine(Protocol):
    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput: ...


__all__ = [
    "ChatEngine",
    "ChatGenerationFailedError",
    "ChatGenerationInput",
    "ChatGenerationResult",
    "ChatGenerator",
    "ChatMedicationInput",
    "ChatProvider",
    "ChatReplyInput",
    "ChatReplyOutput",
    "ChatServiceUnavailableError",
    "ChatTimeoutError",
    "OpenAIResponsesClient",
]
