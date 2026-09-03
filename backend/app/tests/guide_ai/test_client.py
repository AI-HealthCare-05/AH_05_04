import asyncio
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

from app.services.guide_ai.client import OpenAIResponsesClient
from app.services.guide_ai.exceptions import (
    GuideGenerationConfigurationError,
    GuideGenerationInvalidResponseError,
    GuideGenerationSafetyError,
    GuideGenerationTimeoutError,
    GuideGenerationUnavailableError,
)
from app.services.guide_ai.schemas import GeneratedGuideDraft, GeneratedMedicationGuidance, GuideGuidanceIntent


class FakeResponses:
    def __init__(self, response: object) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    async def parse(self, **kwargs: Any) -> object:
        self.kwargs = kwargs
        return self.response


class FakeAsyncOpenAI:
    def __init__(self, response: object) -> None:
        self.responses = FakeResponses(response)


class RaisingResponses:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def parse(self, **kwargs: Any) -> object:
        raise self.error


class RaisingAsyncOpenAI:
    def __init__(self, error: BaseException) -> None:
        self.responses = RaisingResponses(error)


def _client(client: object) -> OpenAIResponsesClient:
    return OpenAIResponsesClient(client, observability_disabled=True)


def _draft() -> GeneratedGuideDraft:
    return GeneratedGuideDraft(
        medications=[
            GeneratedMedicationGuidance(
                source_index=0,
                guidance_intent=GuideGuidanceIntent.FOLLOW_CONFIRMED_TIMING,
                guidance="안내된 복용 시점을 확인해 그대로 따라 주세요.",
            )
        ],
        general_notice="불명확한 내용은 의료진 또는 약사에게 확인해 주세요.",
    )


def _response(
    *, status: str = "completed", content: list[object] | None = None, incomplete_reason: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        model="gpt-4o-mini-2024-07-18",
        incomplete_details=SimpleNamespace(reason=incomplete_reason) if incomplete_reason else None,
        output=[
            SimpleNamespace(
                type="message",
                status="completed",
                content=content if content is not None else [SimpleNamespace(type="output_text", parsed=_draft())],
            )
        ],
    )


async def test_client_uses_non_streaming_parse_and_returns_single_parsed_draft() -> None:
    sdk_client = FakeAsyncOpenAI(_response())
    client = _client(sdk_client)

    result = await client.generate(
        model="gpt-4o-mini",
        instructions="system rules",
        input_json='{"medications":[{"source_index":0,"guidance_intent":"FOLLOW_CONFIRMED_TIMING"}]}',
        max_output_tokens=560,
    )

    assert result.draft == _draft()
    assert result.model_name == "gpt-4o-mini-2024-07-18"
    assert sdk_client.responses.kwargs == {
        "model": "gpt-4o-mini",
        "instructions": "system rules",
        "input": [
            {
                "role": "user",
                "content": '{"medications":[{"source_index":0,"guidance_intent":"FOLLOW_CONFIRMED_TIMING"}]}',
            }
        ],
        "text_format": GeneratedGuideDraft,
        "max_output_tokens": 560,
        "store": False,
    }


async def test_client_rejects_refusal() -> None:
    client = _client(FakeAsyncOpenAI(_response(content=[SimpleNamespace(type="refusal", refusal="refused")])))

    with pytest.raises(GuideGenerationSafetyError):
        await client.generate(model="gpt-4o-mini", instructions="rules", input_json="[]", max_output_tokens=400)


async def test_client_maps_provider_content_filter_to_safety_error() -> None:
    client = _client(FakeAsyncOpenAI(_response(status="incomplete", incomplete_reason="content_filter")))

    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        await client.generate(model="gpt-4o-mini", instructions="rules", input_json="[]", max_output_tokens=400)

    assert exc_info.value.rule_id == "PROVIDER_SAFETY_FILTER"


async def test_client_maps_refusal_before_generic_incomplete_status() -> None:
    response = _response(
        status="incomplete",
        content=[SimpleNamespace(type="refusal", refusal="refused")],
        incomplete_reason="max_output_tokens",
    )
    client = _client(FakeAsyncOpenAI(response))

    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        await client.generate(model="gpt-4o-mini", instructions="rules", input_json="[]", max_output_tokens=400)

    assert exc_info.value.rule_id == "PROVIDER_REFUSAL"


async def test_client_rejects_incomplete_message_inside_completed_response() -> None:
    response = _response()
    response.output[0].status = "incomplete"
    client = _client(FakeAsyncOpenAI(response))

    with pytest.raises(GuideGenerationInvalidResponseError):
        await client.generate(model="gpt-4o-mini", instructions="rules", input_json="[]", max_output_tokens=400)


async def test_client_rejects_multiple_messages_or_parsed_content_items() -> None:
    multiple_messages = _response()
    multiple_messages.output.append(multiple_messages.output[0])
    multiple_content_items = _response(
        content=[
            SimpleNamespace(type="output_text", parsed=_draft()),
            SimpleNamespace(type="output_text", parsed=_draft()),
        ]
    )

    for response in (multiple_messages, multiple_content_items):
        with pytest.raises(GuideGenerationInvalidResponseError):
            await _client(FakeAsyncOpenAI(response)).generate(
                model="gpt-4o-mini",
                instructions="rules",
                input_json="[]",
                max_output_tokens=400,
            )


@pytest.mark.parametrize("error_code", ["server_error", "rate_limit_exceeded"])
async def test_client_maps_returned_provider_availability_errors(error_code: str) -> None:
    response = _response(status="failed")
    response.error = SimpleNamespace(code=error_code, message="must not escape")
    client = _client(FakeAsyncOpenAI(response))

    with pytest.raises(GuideGenerationUnavailableError) as exc_info:
        await client.generate(model="gpt-4o-mini", instructions="rules", input_json="[]", max_output_tokens=400)

    assert "must not escape" not in str(exc_info.value)


@pytest.mark.parametrize(
    "response",
    [
        _response(status="incomplete", incomplete_reason="max_output_tokens"),
        _response(content=[]),
        _response(content=[SimpleNamespace(type="output_text", parsed=None)]),
        SimpleNamespace(status="completed", model="gpt-4o-mini", incomplete_details=None, output=[]),
    ],
)
async def test_client_rejects_incomplete_or_unparseable_output(response: object) -> None:
    client = _client(FakeAsyncOpenAI(response))

    with pytest.raises(GuideGenerationInvalidResponseError):
        await client.generate(model="gpt-4o-mini", instructions="rules", input_json="[]", max_output_tokens=400)


async def test_client_maps_missing_actual_model_id_to_invalid_response() -> None:
    response = _response()
    response.model = None
    client = _client(FakeAsyncOpenAI(response))

    with pytest.raises(GuideGenerationInvalidResponseError):
        await client.generate(model="gpt-4o-mini", instructions="rules", input_json="[]", max_output_tokens=400)


@pytest.mark.parametrize(
    ("provider_error", "domain_error"),
    [
        (
            APITimeoutError(request=httpx2.Request("POST", "https://api.openai.com/v1/responses")),
            GuideGenerationTimeoutError,
        ),
        (
            APIConnectionError(request=httpx2.Request("POST", "https://api.openai.com/v1/responses")),
            GuideGenerationUnavailableError,
        ),
        (
            RateLimitError(
                "limited",
                response=httpx2.Response(429, request=httpx2.Request("POST", "https://api.openai.com/v1/responses")),
                body=None,
            ),
            GuideGenerationUnavailableError,
        ),
        (
            AuthenticationError(
                "unauthorized",
                response=httpx2.Response(401, request=httpx2.Request("POST", "https://api.openai.com/v1/responses")),
                body=None,
            ),
            GuideGenerationConfigurationError,
        ),
        (
            APIStatusError(
                "server error",
                response=httpx2.Response(500, request=httpx2.Request("POST", "https://api.openai.com/v1/responses")),
                body=None,
            ),
            GuideGenerationUnavailableError,
        ),
        (
            APIResponseValidationError(
                response=httpx2.Response(200, request=httpx2.Request("POST", "https://api.openai.com/v1/responses")),
                body=None,
            ),
            GuideGenerationInvalidResponseError,
        ),
    ],
)
async def test_client_maps_sdk_errors_to_provider_neutral_errors(
    provider_error: Exception, domain_error: type[Exception]
) -> None:
    client = _client(RaisingAsyncOpenAI(provider_error))

    with pytest.raises(domain_error):
        await client.generate(model="gpt-4o-mini", instructions="rules", input_json="[]", max_output_tokens=400)


async def test_client_does_not_mask_unexpected_programming_errors() -> None:
    client = _client(RaisingAsyncOpenAI(RuntimeError("unexpected programming error")))

    with pytest.raises(RuntimeError, match="unexpected programming error"):
        await client.generate(model="gpt-4o-mini", instructions="rules", input_json="[]", max_output_tokens=400)


async def test_client_propagates_cancellation() -> None:
    client = _client(RaisingAsyncOpenAI(asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        await client.generate(model="gpt-4o-mini", instructions="rules", input_json="[]", max_output_tokens=400)
