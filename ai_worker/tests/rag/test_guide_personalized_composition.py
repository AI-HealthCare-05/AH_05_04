from __future__ import annotations

import asyncio

import pytest

from ai_worker.tasks.rag.guide_personalized_composition import compose_personalized_guide
from ai_worker.tasks.rag.guideline_card import GuidelineGenerationFailure
from ai_worker.tasks.rag.guideline_generator import GuidelineGenerationRequest
from ai_worker.tests.rag.test_guideline_generator import make_synthetic_draft, make_synthetic_request


class RecordingGenerator:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[GuidelineGenerationRequest] = []

    async def generate(self, request: GuidelineGenerationRequest) -> object:
        self.calls.append(request)
        return self.result


@pytest.mark.parametrize(
    "result",
    (
        make_synthetic_draft(),
        GuidelineGenerationFailure.PROVIDER_TIMEOUT,
        object(),
    ),
)
def test_composer_forwards_the_same_request_and_provider_result_unchanged(result: object) -> None:
    request = make_synthetic_request()
    generator = RecordingGenerator(result)

    outcome = asyncio.run(compose_personalized_guide(request, generator=generator))  # type: ignore[arg-type]

    assert generator.calls == [request]
    assert generator.calls[0] is request
    assert outcome is result
