import os

import pytest

from app.services.chat_ai.client import OpenAIResponsesClient
from app.services.chat_ai.exceptions import ChatGenerationError
from app.services.chat_ai.generator import ChatGenerator
from app.services.chat_ai.schemas import ChatGenerationInput, ChatMedicationInput


@pytest.mark.skipif(
    os.getenv("RUN_OPENAI_CHAT_SMOKE") != "1",
    reason="set RUN_OPENAI_CHAT_SMOKE=1 for the live synthetic chat smoke test",
)
async def test_gpt_4o_mini_synthetic_chat_smoke() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        pytest.fail("OPENAI_API_KEY must be configured when RUN_OPENAI_CHAT_SMOKE=1")
    model = os.getenv("OPENAI_MODEL")
    if model != "gpt-4o-mini":
        pytest.fail("OPENAI_MODEL must be explicitly set to gpt-4o-mini when RUN_OPENAI_CHAT_SMOKE=1")

    from openai import AsyncOpenAI

    timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))
    sdk_client = AsyncOpenAI(timeout=timeout_seconds, max_retries=0)
    try:
        generator = ChatGenerator(
            provider=OpenAIResponsesClient(sdk_client),
            model=model,
            timeout_seconds=timeout_seconds,
        )
        try:
            result = await generator.generate(
                ChatGenerationInput(
                    question="이 약을 복용할 때 일반적으로 주의할 점을 짧게 알려주세요.",
                    medications=[ChatMedicationInput(medication_name="합성의약품 에이", timing_text="식후")],
                )
            )
        except ChatGenerationError as error:
            pytest.fail(f"live synthetic chat smoke failed with {type(error).__name__}", pytrace=False)
    finally:
        await sdk_client.close()

    assert result.content
    assert result.model_name.startswith("gpt-4o-mini")
    assert result.prompt_version == "chat-prompt-v2"
