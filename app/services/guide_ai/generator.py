import asyncio
import json
import math

from app.services.guide_ai.client import GuideProvider
from app.services.guide_ai.exceptions import (
    GuideGenerationConfigurationError,
    GuideGenerationInvalidResponseError,
    GuideGenerationTimeoutError,
)
from app.services.guide_ai.prompt import GUIDE_SYSTEM_INSTRUCTIONS, PROMPT_VERSION
from app.services.guide_ai.renderer import render_plaintext_guide
from app.services.guide_ai.schemas import (
    GuideGenerationInput,
    GuideGenerationResult,
)
from app.services.guide_ai.validators import validate_generated_draft


class GuideGenerator:
    def __init__(self, *, provider: GuideProvider, model: str, timeout_seconds: float) -> None:
        if not model.strip() or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise GuideGenerationConfigurationError("Guide generation configuration is invalid")
        self._provider = provider
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def generate(self, guide_input: GuideGenerationInput) -> GuideGenerationResult:
        input_json = self._build_provider_input(guide_input)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                provider_response = await self._provider.generate(
                    model=self._model,
                    instructions=GUIDE_SYSTEM_INSTRUCTIONS,
                    input_json=input_json,
                    max_output_tokens=400 + 160 * len(guide_input.medications),
                )
        except TimeoutError as error:
            raise GuideGenerationTimeoutError("Guide provider call timed out") from error

        if not provider_response.model_name.strip() or len(provider_response.model_name) > 100:
            raise GuideGenerationInvalidResponseError("Provider model identifier is invalid")

        validate_generated_draft(provider_response.draft, medication_count=len(guide_input.medications))
        content = render_plaintext_guide(guide_input, provider_response.draft)
        return GuideGenerationResult(
            content=content,
            model_name=provider_response.model_name,
            prompt_version=PROMPT_VERSION,
        )

    @staticmethod
    def _build_provider_input(guide_input: GuideGenerationInput) -> str:
        # 확정 처방값은 Backend renderer가 붙이므로 Provider에는 순서 locator만 전달합니다.
        # 불필요한 처방값 노출과 모델의 숫자·단위 재생성을 함께 차단합니다.
        return json.dumps(
            [{"source_index": source_index} for source_index, _ in enumerate(guide_input.medications)],
            separators=(",", ":"),
        )
