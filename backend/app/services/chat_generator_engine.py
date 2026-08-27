from pydantic import ValidationError

from app.services.chat_ai import (
    ChatEngine,
    ChatGenerationFailedError,
    ChatMedicationInput,
    ChatReplyInput,
    ChatReplyOutput,
    ChatServiceUnavailableError,
    ChatTimeoutError,
)
from app.services.chat_ai.client import ChatProvider
from app.services.chat_ai.exceptions import (
    ChatGenerationError,
    ChatGenerationTimeoutError,
    ChatGenerationUnavailableError,
)
from app.services.chat_ai.generator import ChatGenerator
from app.services.chat_ai.schemas import ChatGenerationInput
from app.services.chat_ai.schemas import ChatMedicationInput as GenerationMedicationInput


class ChatGeneratorEngine(ChatEngine):
    def __init__(self, *, provider: ChatProvider, model: str, timeout_seconds: float) -> None:
        self._provider = provider
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        mapped_error: Exception | None = None
        try:
            generator = ChatGenerator(
                provider=self._provider,
                model=self._model,
                timeout_seconds=self._timeout_seconds,
            )
            generation_input = ChatGenerationInput(
                question=chat_input.content,
                medications=[self._to_generation_medication(medication) for medication in chat_input.medications],
            )
            result = await generator.generate(generation_input)
        except ChatGenerationTimeoutError:
            mapped_error = ChatTimeoutError("챗봇 응답 대기 시간이 초과됐습니다.")
        except ChatGenerationUnavailableError:
            mapped_error = ChatServiceUnavailableError("챗봇 LLM 호출에 실패했습니다.")
        except (ChatGenerationError, ValidationError):
            mapped_error = ChatGenerationFailedError("챗봇 응답 생성 처리 중 오류가 발생했습니다.")

        if mapped_error is not None:
            raise mapped_error

        return ChatReplyOutput(
            content=result.content,
            model_name=result.model_name,
            prompt_version=result.prompt_version,
        )

    @staticmethod
    def _to_generation_medication(medication: ChatMedicationInput) -> GenerationMedicationInput:
        return GenerationMedicationInput(
            medication_name=medication.medication_name,
            strength_text=medication.strength_text,
            dose_value=medication.dose_value,
            dose_unit=medication.dose_unit,
            frequency_per_day=medication.frequency_per_day,
            timing_text=medication.timing_text,
            duration_days=medication.duration_days,
        )
