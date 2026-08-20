from pydantic import ValidationError

from app.services.chat_ai import (
    ChatEngine,
    ChatGenerationFailedError,
    ChatProvider,
    ChatReplyInput,
    ChatReplyOutput,
    ChatServiceUnavailableError,
    ChatTimeoutError,
)
from app.services.chat_ai import (
    ChatMedicationInput as BackendMedicationInput,
)
from app.services.chat_ai.exceptions import (
    ChatGenerationError,
    ChatGenerationTimeoutError,
    ChatGenerationUnavailableError,
)
from app.services.chat_ai.generator import ChatGenerator
from app.services.chat_ai.schemas import (
    ChatGenerationInput,
    ChatGenerationResult,
)
from app.services.chat_ai.schemas import (
    ChatMedicationInput as GenerationMedicationInput,
)


class ChatGeneratorEngine(ChatEngine):
    def __init__(self, *, provider: ChatProvider, model: str, timeout_seconds: float) -> None:
        self._provider = provider
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        mapped_error: ChatTimeoutError | ChatServiceUnavailableError | ChatGenerationFailedError | None = None
        result: ChatGenerationResult | None = None
        try:
            generator = ChatGenerator(
                provider=self._provider,
                model=self._model,
                timeout_seconds=self._timeout_seconds,
            )
            generation_input = ChatGenerationInput(
                question=chat_input.content,
                medications=[self._to_generation_medication(item) for item in chat_input.medications],
            )
            result = await generator.generate(generation_input)
        except ChatGenerationTimeoutError:
            mapped_error = ChatTimeoutError("챗봇 응답 생성 시간이 초과되었습니다.")
        except ChatGenerationUnavailableError:
            mapped_error = ChatServiceUnavailableError("챗봇 생성 서비스를 사용할 수 없습니다.")
        except (ChatGenerationError, ValidationError):
            mapped_error = ChatGenerationFailedError("챗봇 응답 생성 처리에 실패했습니다.")

        if mapped_error is not None:
            raise mapped_error
        if result is None:
            raise RuntimeError("ChatGenerator returned without a result")
        return ChatReplyOutput(
            content=result.content,
            model_name=result.model_name,
            prompt_version=result.prompt_version,
        )

    @staticmethod
    def _to_generation_medication(item: BackendMedicationInput) -> GenerationMedicationInput:
        return GenerationMedicationInput(
            medication_name=item.medication_name,
            dose_value=item.dose_value,
            dose_unit=item.dose_unit,
            frequency_per_day=item.frequency_per_day,
            timing_text=item.timing_text,
            duration_days=item.duration_days,
        )
