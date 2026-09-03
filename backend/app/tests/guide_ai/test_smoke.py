import os

import pytest

from app.services.guide_ai.client import OpenAIResponsesClient
from app.services.guide_ai.exceptions import GuideGenerationError
from app.services.guide_ai.generator import GuideGenerator
from app.services.guide_ai.schemas import GuideGenerationInput, MedicationInput


@pytest.mark.skipif(
    os.getenv("RUN_OPENAI_SMOKE") != "1", reason="set RUN_OPENAI_SMOKE=1 for the live synthetic smoke test"
)
async def test_gpt_4o_mini_synthetic_smoke() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        pytest.fail("OPENAI_API_KEY must be configured when RUN_OPENAI_SMOKE=1")
    model = os.getenv("OPENAI_MODEL")
    if model != "gpt-4o-mini":
        pytest.fail("OPENAI_MODEL must be explicitly set to gpt-4o-mini when RUN_OPENAI_SMOKE=1")

    from openai import AsyncOpenAI

    timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
    sdk_client = AsyncOpenAI(timeout=timeout_seconds, max_retries=0)
    try:
        generator = GuideGenerator(
            provider=OpenAIResponsesClient(sdk_client, observability_disabled=True),
            model=model,
            timeout_seconds=timeout_seconds,
        )
        try:
            result = await generator.generate(
                GuideGenerationInput(
                    medications=[
                        MedicationInput(
                            medication_name="합성의약품 에이",
                            frequency_per_day=1,
                            timing_text="식후",
                        )
                    ]
                )
            )
        except GuideGenerationError as error:
            pytest.fail(f"live synthetic smoke failed with {type(error).__name__}", pytrace=False)
    finally:
        await sdk_client.close()

    assert result.content
    assert "합성의약품 에이" in result.content
    assert "복용 시점: 식후" in result.content
    assert result.model_name.startswith("gpt-4o-mini")
    assert result.prompt_version == "guide-prompt-v3"
