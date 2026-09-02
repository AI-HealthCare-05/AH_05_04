import asyncio
import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import httpx2
import pytest
from openai import APIConnectionError, APITimeoutError, AuthenticationError, InternalServerError, RateLimitError

from app.core.config import Env
from app.core.provider_observability import (
    Provider,
    ProviderCallContext,
    ProviderCallDescriptor,
    ProviderCallLogger,
    ProviderOperation,
)
from app.services.chat_ai.client import OpenAIResponsesClient as ChatOpenAIResponsesClient
from app.services.clova_ocr_engine import ClovaOcrEngine
from app.services.guide_ai.client import OpenAIResponsesClient as GuideOpenAIResponsesClient
from app.services.guide_ai.exceptions import (
    GuideGenerationConfigurationError,
    GuideGenerationSafetyError,
    GuideGenerationTimeoutError,
    GuideGenerationUnavailableError,
)
from app.services.guide_ai.schemas import GeneratedGuideDraft, GeneratedMedicationGuidance, GuideGuidanceIntent
from app.services.ocr_ai import OcrStructureResult
from app.services.ocr_ai.client import OpenAIOcrStructureClient
from app.services.ocr_ai.schemas import GeneratedMedication, GeneratedPrescriptionDraft, GeneratedSourceValue
from app.services.ocr_engine import OcrDeadline, OcrProcessingError


class FakeResponses:
    def __init__(self, response: object | None = None, error: BaseException | None = None) -> None:
        self._response = response
        self._error = error

    async def parse(self, **_kwargs: Any) -> object:
        if self._error is not None:
            raise self._error
        return self._response

    async def create(self, **_kwargs: Any) -> object:
        if self._error is not None:
            raise self._error
        return self._response


class FakeAsyncOpenAI:
    def __init__(self, response: object | None = None, error: BaseException | None = None) -> None:
        self.responses = FakeResponses(response, error)


class FakeStructurer:
    async def structure(self, _raw_fields: list[object]) -> OcrStructureResult:
        return OcrStructureResult(fields=[], model_name=None, prompt_version=None)


def _observer(operation: ProviderOperation, prompt_version: str | None) -> tuple[ProviderCallLogger, io.StringIO]:
    stream = io.StringIO()
    logger = logging.Logger(f"provider-adapter-{operation}", level=logging.INFO)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return ProviderCallLogger(logger), stream


def _context() -> ProviderCallContext:
    return ProviderCallContext(
        trace_id="b" * 32,
        validation_run_id=UUID("61a10000-0000-4000-8000-000000000003"),
        environment=Env.LOCAL,
        validation_enabled=True,
    )


def _descriptor(operation: ProviderOperation, prompt_version: str | None) -> ProviderCallDescriptor:
    return ProviderCallDescriptor(
        provider=(Provider.CLOVA_OCR if operation is ProviderOperation.PRESCRIPTION_RECOGNITION else Provider.OPENAI),
        operation=operation,
        prompt_version=prompt_version,
    )


def _events(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def _guide_response(*, refusal: bool = False) -> SimpleNamespace:
    draft = GeneratedGuideDraft(
        medications=[
            GeneratedMedicationGuidance(
                source_index=0,
                guidance_intent=GuideGuidanceIntent.FOLLOW_CONFIRMED_TIMING,
                guidance="안내된 복용 시점을 확인해 그대로 따라 주세요.",
            )
        ],
        general_notice="불명확한 내용은 의료진 또는 약사에게 확인해 주세요.",
    )
    content = (
        SimpleNamespace(type="refusal", refusal="SENSITIVE_PROVIDER_TEXT")
        if refusal
        else SimpleNamespace(type="output_text", parsed=draft)
    )
    return SimpleNamespace(
        id="resp_guide_synthetic",
        status="completed",
        model="gpt-4o-mini-2024-07-18",
        incomplete_details=None,
        output=[SimpleNamespace(type="message", status="completed", content=[content])],
    )


async def test_guide_client_logs_success_with_response_id_and_actual_model() -> None:
    logger, stream = _observer(ProviderOperation.GUIDE_GENERATION, "guide-prompt-v3")
    client = GuideOpenAIResponsesClient(
        FakeAsyncOpenAI(_guide_response()),
        context=_context(),
        descriptor=_descriptor(ProviderOperation.GUIDE_GENERATION, "guide-prompt-v3"),
        call_logger=logger,
    )

    await client.generate(
        model="gpt-4o-mini", instructions="SENSITIVE_PROMPT", input_json="SENSITIVE_INPUT", max_output_tokens=560
    )

    events = _events(stream)
    assert [event["event"] for event in events] == ["provider.call.started", "provider.call.succeeded"]
    assert events[1]["operation"] == "GUIDE_GENERATION"
    assert events[1]["provider_response_id"] == "resp_guide_synthetic"
    assert events[1]["model_name"] == "gpt-4o-mini-2024-07-18"
    assert "SENSITIVE_PROMPT" not in stream.getvalue()
    assert "SENSITIVE_INPUT" not in stream.getvalue()


async def test_guide_client_logs_timeout_without_sdk_error_text() -> None:
    logger, stream = _observer(ProviderOperation.GUIDE_GENERATION, "guide-prompt-v3")
    error = APITimeoutError(request=httpx2.Request("POST", "https://api.openai.com/v1/responses"))
    client = GuideOpenAIResponsesClient(
        FakeAsyncOpenAI(error=error),
        context=_context(),
        descriptor=_descriptor(ProviderOperation.GUIDE_GENERATION, "guide-prompt-v3"),
        call_logger=logger,
    )

    with pytest.raises(GuideGenerationTimeoutError):
        await client.generate(model="gpt-4o-mini", instructions="rules", input_json="{}", max_output_tokens=560)

    failed = _events(stream)[1]
    assert failed["failure_phase"] == "TRANSPORT_TIMEOUT"
    assert failed["error_code"] == "PROVIDER_TIMEOUT"
    assert failed["provider_response_received"] is False
    assert str(error) not in stream.getvalue()


def _status_error(error_type: type[Exception], status_code: int) -> Exception:
    response = httpx2.Response(
        status_code,
        request=httpx2.Request("POST", "https://api.openai.com/v1/responses"),
    )
    return error_type("SENSITIVE_SDK_ERROR", response=response, body=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("provider_error", "domain_error", "phase", "code"),
    [
        (
            APIConnectionError(request=httpx2.Request("POST", "https://api.openai.com/v1/responses")),
            GuideGenerationUnavailableError,
            "TRANSPORT_CONNECTION",
            "PROVIDER_CONNECTION_FAILED",
        ),
        (
            _status_error(RateLimitError, 429),
            GuideGenerationUnavailableError,
            "HTTP_STATUS",
            "PROVIDER_RATE_LIMITED",
        ),
        (
            _status_error(AuthenticationError, 401),
            GuideGenerationConfigurationError,
            "HTTP_STATUS",
            "PROVIDER_REQUEST_REJECTED",
        ),
        (
            _status_error(InternalServerError, 500),
            GuideGenerationUnavailableError,
            "HTTP_STATUS",
            "PROVIDER_UNAVAILABLE",
        ),
    ],
)
async def test_guide_client_logs_safe_transport_and_status_classification(
    provider_error: Exception,
    domain_error: type[Exception],
    phase: str,
    code: str,
) -> None:
    logger, stream = _observer(ProviderOperation.GUIDE_GENERATION, "guide-prompt-v3")
    client = GuideOpenAIResponsesClient(
        FakeAsyncOpenAI(error=provider_error),
        context=_context(),
        descriptor=_descriptor(ProviderOperation.GUIDE_GENERATION, "guide-prompt-v3"),
        call_logger=logger,
    )

    with pytest.raises(domain_error):
        await client.generate(model="gpt-4o-mini", instructions="rules", input_json="{}", max_output_tokens=560)

    failed = _events(stream)[1]
    assert failed["failure_phase"] == phase
    assert failed["error_code"] == code
    assert "SENSITIVE_SDK_ERROR" not in stream.getvalue()


async def test_guide_client_logs_provider_refusal_and_cancellation_terminal() -> None:
    logger, stream = _observer(ProviderOperation.GUIDE_GENERATION, "guide-prompt-v3")
    refused = GuideOpenAIResponsesClient(
        FakeAsyncOpenAI(_guide_response(refusal=True)),
        context=_context(),
        descriptor=_descriptor(ProviderOperation.GUIDE_GENERATION, "guide-prompt-v3"),
        call_logger=logger,
    )
    with pytest.raises(GuideGenerationSafetyError):
        await refused.generate(model="gpt-4o-mini", instructions="rules", input_json="{}", max_output_tokens=560)

    cancelled = GuideOpenAIResponsesClient(
        FakeAsyncOpenAI(error=asyncio.CancelledError()),
        context=_context(),
        descriptor=_descriptor(ProviderOperation.GUIDE_GENERATION, "guide-prompt-v3"),
        call_logger=logger,
    )
    with pytest.raises(asyncio.CancelledError):
        await cancelled.generate(model="gpt-4o-mini", instructions="rules", input_json="{}", max_output_tokens=560)

    events = _events(stream)
    assert events[1]["failure_phase"] == "PROVIDER_POLICY"
    assert events[1]["error_code"] == "PROVIDER_REFUSAL"
    assert events[3]["error_code"] == "PROVIDER_CALL_ABORTED"


async def test_ocr_and_chat_openai_clients_log_their_distinct_operations() -> None:
    ocr_logger, ocr_stream = _observer(ProviderOperation.OCR_STRUCTURING, "ocr-structure-prompt-v2")
    ocr_response = SimpleNamespace(
        id="resp_ocr_synthetic",
        status="completed",
        model="gpt-4o-mini-2024-07-18",
        output=[],
        output_parsed=GeneratedPrescriptionDraft(
            medications=[
                GeneratedMedication(medication_name=GeneratedSourceValue(value="합성의약품에이정", source_ids=[1]))
            ]
        ),
    )
    ocr_client = OpenAIOcrStructureClient(
        FakeAsyncOpenAI(ocr_response),
        context=_context(),
        descriptor=_descriptor(ProviderOperation.OCR_STRUCTURING, "ocr-structure-prompt-v2"),
        call_logger=ocr_logger,
    )
    await ocr_client.generate(model="gpt-4o-mini", instructions="rules", input_json="{}", max_output_tokens=1200)

    chat_logger, chat_stream = _observer(ProviderOperation.CHAT_GENERATION, "chat-prompt-v2")
    chat_response = SimpleNamespace(
        id="resp_chat_synthetic",
        status="completed",
        model="gpt-4o-mini-2024-07-18",
        output_text="합성 답변",
        output=[],
        error=None,
    )
    chat_client = ChatOpenAIResponsesClient(
        FakeAsyncOpenAI(chat_response),
        context=_context(),
        descriptor=_descriptor(ProviderOperation.CHAT_GENERATION, "chat-prompt-v2"),
        call_logger=chat_logger,
    )
    await chat_client.generate(model="gpt-4o-mini", instructions="rules", input_json="{}", max_output_tokens=800)

    assert _events(ocr_stream)[1]["operation"] == "OCR_STRUCTURING"
    assert _events(ocr_stream)[1]["provider_response_id"] == "resp_ocr_synthetic"
    assert _events(chat_stream)[1]["operation"] == "CHAT_GENERATION"
    assert _events(chat_stream)[1]["provider_response_id"] == "resp_chat_synthetic"


async def test_clova_client_logs_response_validation_failure_without_body(tmp_path: Path) -> None:
    image = tmp_path / "synthetic.png"
    image.write_bytes(b"synthetic-image")
    logger, stream = _observer(ProviderOperation.PRESCRIPTION_RECOGNITION, None)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"SENSITIVE_OCR_BODY": "must-not-be-logged"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        engine = ClovaOcrEngine(
            invoke_url="https://synthetic.example/ocr",
            secret_key="SENSITIVE_CLOVA_SECRET",
            storage_dir=str(tmp_path),
            timeout_seconds=5,
            structurer=FakeStructurer(),  # type: ignore[arg-type]
            client=http_client,
            context=_context(),
            descriptor=_descriptor(ProviderOperation.PRESCRIPTION_RECOGNITION, None),
            call_logger=logger,
        )
        with pytest.raises(OcrProcessingError):
            await engine.recognize(
                object_key=image.name,
                file_mime_type="image/png",
                deadline=OcrDeadline.start(total_seconds=60, response_margin_seconds=0),
            )

    events = _events(stream)
    assert [event["event"] for event in events] == ["provider.call.started", "provider.call.failed"]
    assert events[1]["failure_phase"] == "RESPONSE_VALIDATION"
    assert events[1]["error_code"] == "PROVIDER_RESPONSE_INVALID"
    assert events[1]["http_status"] == 200
    assert "SENSITIVE_CLOVA_SECRET" not in stream.getvalue()
    assert "SENSITIVE_OCR_BODY" not in stream.getvalue()
