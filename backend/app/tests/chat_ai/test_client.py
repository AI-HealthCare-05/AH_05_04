from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from app.services.chat_ai.client import OpenAIResponsesClient
from app.services.chat_ai.exceptions import (
    ChatGenerationConfigurationError,
    ChatGenerationInvalidResponseError,
    ChatGenerationTimeoutError,
    ChatGenerationUnavailableError,
)


class FakeResponses:
    def __init__(self, response: object) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> object:
        self.kwargs = kwargs
        return self.response


class FakeAsyncOpenAI:
    def __init__(self, response: object) -> None:
        self.responses = FakeResponses(response)


class RaisingResponses:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def create(self, **kwargs: Any) -> object:
        raise self.error


class RaisingAsyncOpenAI:
    def __init__(self, error: Exception) -> None:
        self.responses = RaisingResponses(error)


def _client(client: object) -> OpenAIResponsesClient:
    return OpenAIResponsesClient(client, observability_disabled=True)


def _response(
    *,
    status: str = "completed",
    output_text: object = "  합성 답변입니다.  ",
    model: object = "gpt-4o-mini-2024-07-18",
    output: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        output_text=output_text,
        model=model,
        output=(
            [SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=output_text)])]
            if output is None
            else output
        ),
        error=None,
    )


async def test_client_uses_non_streaming_create_and_returns_plain_text() -> None:
    sdk_client = FakeAsyncOpenAI(_response())
    client = _client(sdk_client)

    result = await client.generate(
        model="gpt-4o-mini",
        instructions="system rules",
        input_json='{"question":"질문","medications":[]}',
        max_output_tokens=800,
    )

    assert result.content == "합성 답변입니다."
    assert result.model_name == "gpt-4o-mini-2024-07-18"
    assert sdk_client.responses.kwargs == {
        "model": "gpt-4o-mini",
        "instructions": "system rules",
        "input": [{"role": "user", "content": '{"question":"질문","medications":[]}'}],
        "max_output_tokens": 800,
        "store": False,
        "stream": False,
    }


async def test_client_rejects_refusal_before_other_response_errors() -> None:
    response = _response(
        status="failed",
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal", refusal="hidden")])],
    )
    response.error = SimpleNamespace(code="server_error", message="must not escape")

    with pytest.raises(ChatGenerationInvalidResponseError) as exc_info:
        await _client(FakeAsyncOpenAI(response)).generate(
            model="gpt-4o-mini", instructions="rules", input_json="{}", max_output_tokens=800
        )

    assert "hidden" not in str(exc_info.value)
    assert "must not escape" not in str(exc_info.value)


@pytest.mark.parametrize("error_code", ["server_error", "rate_limit_exceeded"])
async def test_client_maps_returned_provider_availability_errors(error_code: str) -> None:
    response = _response(status="failed")
    response.error = SimpleNamespace(code=error_code, message="must not escape")

    with pytest.raises(ChatGenerationUnavailableError) as exc_info:
        await _client(FakeAsyncOpenAI(response)).generate(
            model="gpt-4o-mini", instructions="rules", input_json="{}", max_output_tokens=800
        )

    assert "must not escape" not in str(exc_info.value)


@pytest.mark.parametrize(
    "response",
    [
        _response(status="incomplete"),
        _response(output_text=""),
        _response(output_text="   "),
        _response(output_text=None),
        _response(model=None),
        _response(model=123),
    ],
)
async def test_client_rejects_incomplete_or_malformed_response(response: object) -> None:
    with pytest.raises(ChatGenerationInvalidResponseError):
        await _client(FakeAsyncOpenAI(response)).generate(
            model="gpt-4o-mini", instructions="rules", input_json="{}", max_output_tokens=800
        )


def _status_error(error_type: type[Exception], status_code: int) -> Exception:
    response = httpx2.Response(
        status_code,
        request=httpx2.Request("POST", "https://api.openai.com/v1/responses"),
    )
    return error_type("provider detail", response=response, body=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("provider_error", "domain_error"),
    [
        (
            APITimeoutError(request=httpx2.Request("POST", "https://api.openai.com/v1/responses")),
            ChatGenerationTimeoutError,
        ),
        (
            APIConnectionError(request=httpx2.Request("POST", "https://api.openai.com/v1/responses")),
            ChatGenerationUnavailableError,
        ),
        (_status_error(RateLimitError, 429), ChatGenerationUnavailableError),
        (_status_error(InternalServerError, 500), ChatGenerationUnavailableError),
        (_status_error(AuthenticationError, 401), ChatGenerationConfigurationError),
        (_status_error(BadRequestError, 400), ChatGenerationConfigurationError),
        (
            APIResponseValidationError(
                response=httpx2.Response(
                    200,
                    request=httpx2.Request("POST", "https://api.openai.com/v1/responses"),
                ),
                body=None,
            ),
            ChatGenerationInvalidResponseError,
        ),
    ],
)
async def test_client_maps_sdk_errors_to_provider_neutral_errors(
    provider_error: Exception,
    domain_error: type[Exception],
) -> None:
    with pytest.raises(domain_error) as exc_info:
        await _client(RaisingAsyncOpenAI(provider_error)).generate(
            model="gpt-4o-mini", instructions="rules", input_json="{}", max_output_tokens=800
        )

    assert "provider detail" not in str(exc_info.value)


async def test_client_does_not_wrap_programming_errors() -> None:
    error = RuntimeError("programming failure")

    with pytest.raises(RuntimeError, match="programming failure"):
        await _client(RaisingAsyncOpenAI(error)).generate(
            model="gpt-4o-mini", instructions="rules", input_json="{}", max_output_tokens=800
        )
