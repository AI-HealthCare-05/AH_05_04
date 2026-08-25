import asyncio
import math

from app.services.chat_ai.client import ChatProvider
from app.services.chat_ai.exceptions import (
    ChatGenerationConfigurationError,
    ChatGenerationInvalidResponseError,
    ChatGenerationTimeoutError,
)
from app.services.chat_ai.prompt import CHAT_SYSTEM_INSTRUCTIONS, PROMPT_VERSION
from app.services.chat_ai.schemas import (
    ChatGenerationInput,
    ChatGenerationResult,
    ChatMedicationPromptItem,
    ChatPromptPayload,
)

MAX_OUTPUT_TOKENS = 800


class ChatGenerator:
    def __init__(self, *, provider: ChatProvider, model: str, timeout_seconds: float) -> None:
        if not model.strip() or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ChatGenerationConfigurationError("Chat generation configuration is invalid")
        self._provider = provider
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def generate(self, chat_input: ChatGenerationInput) -> ChatGenerationResult:
        input_json = self._build_provider_input(chat_input)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                provider_response = await self._provider.generate(
                    model=self._model,
                    instructions=CHAT_SYSTEM_INSTRUCTIONS,
                    input_json=input_json,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                )
        except TimeoutError as error:
            raise ChatGenerationTimeoutError("Chat provider call timed out") from error

        content = provider_response.content.strip()
        model_name = provider_response.model_name
        if not content or len(content) > 10_000 or not model_name.strip() or len(model_name) > 100:
            raise ChatGenerationInvalidResponseError("Chat provider result is invalid")

        return ChatGenerationResult(
            content=content,
            model_name=model_name,
            prompt_version=PROMPT_VERSION,
        )

    @staticmethod
    def _build_provider_input(chat_input: ChatGenerationInput) -> str:
        medication_items: list[ChatMedicationPromptItem] = []
        for medication in chat_input.medications:
            has_complete_dose = medication.dose_value is not None and medication.dose_unit is not None
            medication_items.append(
                ChatMedicationPromptItem(
                    medication_name=medication.medication_name,
                    dose_value=medication.dose_value if has_complete_dose else None,
                    dose_unit=medication.dose_unit if has_complete_dose else None,
                    frequency_per_day=medication.frequency_per_day,
                    timing_text=medication.timing_text,
                    duration_days=medication.duration_days,
                )
            )
        payload = ChatPromptPayload(question=chat_input.question, medications=medication_items)
        return payload.model_dump_json(exclude_none=True)
