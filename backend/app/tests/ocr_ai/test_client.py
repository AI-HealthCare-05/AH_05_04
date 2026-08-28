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

from app.services.ocr_ai.client import OpenAIOcrStructureClient
from app.services.ocr_ai.schemas import (
    GeneratedMedication,
    GeneratedPrescriptionDraft,
    GeneratedSourceValue,
)
from app.services.ocr_engine import (
    OcrProcessingError,
    OcrProviderTimeoutError,
    OcrProviderUnavailableError,
)


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
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def parse(self, **kwargs: Any) -> object:
        raise self.error


class RaisingAsyncOpenAI:
    def __init__(self, error: Exception) -> None:
        self.responses = RaisingResponses(error)


def _draft() -> GeneratedPrescriptionDraft:
    return GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
            )
        ]
    )


def _response(
    *,
    status: str = "completed",
    output_parsed: object | None = None,
    model: object = "gpt-4o-mini-2024-07-18",
    output: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        output_parsed=_draft() if output_parsed is None else output_parsed,
        model=model,
        output=[] if output is None else output,
    )


async def test_client_uses_structured_parse_without_provider_storage() -> None:
    sdk_client = FakeAsyncOpenAI(_response())
    client = OpenAIOcrStructureClient(sdk_client)

    result = await client.generate(
        model="gpt-4o-mini",
        instructions="system rules",
        input_json='{"tokens":[]}',
        max_output_tokens=1200,
    )

    assert result.draft == _draft()
    assert result.model_name == "gpt-4o-mini-2024-07-18"
    assert sdk_client.responses.kwargs == {
        "model": "gpt-4o-mini",
        "instructions": "system rules",
        "input": [
            {
                "role": "user",
                "content": '{"tokens":[]}',
            }
        ],
        "text_format": GeneratedPrescriptionDraft,
        "max_output_tokens": 1200,
        "store": False,
    }


async def test_client_rejects_refusal() -> None:
    response = _response(
        output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(
                        type="refusal",
                        refusal="provider detail",
                    )
                ],
            )
        ]
    )

    with pytest.raises(
        OcrProcessingError,
        match="응답 생성을 거부",
    ):
        await OpenAIOcrStructureClient(FakeAsyncOpenAI(response)).generate(
            model="gpt-4o-mini",
            instructions="rules",
            input_json="{}",
            max_output_tokens=1200,
        )


@pytest.mark.parametrize(
    "response",
    [
        _response(status="incomplete"),
        _response(output_parsed="invalid structured output"),
        _response(model=None),
        _response(model=""),
        _response(model=123),
    ],
)
async def test_client_rejects_incomplete_or_malformed_response(
    response: object,
) -> None:
    with pytest.raises(OcrProcessingError):
        await OpenAIOcrStructureClient(FakeAsyncOpenAI(response)).generate(
            model="gpt-4o-mini",
            instructions="rules",
            input_json="{}",
            max_output_tokens=1200,
        )


def _status_error(
    error_type: type[Exception],
    status_code: int,
) -> Exception:
    response = httpx2.Response(
        status_code,
        request=httpx2.Request(
            "POST",
            "https://api.openai.com/v1/responses",
        ),
    )

    return error_type(
        "provider detail",
        response=response,
        body=None,
    )  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("provider_error", "domain_error"),
    [
        (
            APITimeoutError(
                request=httpx2.Request(
                    "POST",
                    "https://api.openai.com/v1/responses",
                )
            ),
            OcrProviderTimeoutError,
        ),
        (
            APIConnectionError(
                request=httpx2.Request(
                    "POST",
                    "https://api.openai.com/v1/responses",
                )
            ),
            OcrProviderUnavailableError,
        ),
        (
            _status_error(RateLimitError, 429),
            OcrProviderUnavailableError,
        ),
        (
            _status_error(InternalServerError, 500),
            OcrProviderUnavailableError,
        ),
        (
            _status_error(AuthenticationError, 401),
            OcrProcessingError,
        ),
        (
            _status_error(BadRequestError, 400),
            OcrProcessingError,
        ),
        (
            APIResponseValidationError(
                response=httpx2.Response(
                    200,
                    request=httpx2.Request(
                        "POST",
                        "https://api.openai.com/v1/responses",
                    ),
                ),
                body=None,
            ),
            OcrProcessingError,
        ),
    ],
)
async def test_client_maps_provider_errors(
    provider_error: Exception,
    domain_error: type[Exception],
) -> None:
    with pytest.raises(domain_error) as exc_info:
        await OpenAIOcrStructureClient(RaisingAsyncOpenAI(provider_error)).generate(
            model="gpt-4o-mini",
            instructions="rules",
            input_json="{}",
            max_output_tokens=1200,
        )

    # Provider 내부 오류 메시지가 사용자 영역으로 노출되지 않아야 합니다.
    assert "provider detail" not in str(exc_info.value)


async def test_client_does_not_wrap_programming_errors() -> None:
    error = RuntimeError("programming failure")

    with pytest.raises(
        RuntimeError,
        match="programming failure",
    ):
        await OpenAIOcrStructureClient(RaisingAsyncOpenAI(error)).generate(
            model="gpt-4o-mini",
            instructions="rules",
            input_json="{}",
            max_output_tokens=1200,
        )
