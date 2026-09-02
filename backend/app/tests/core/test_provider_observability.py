import io
import json
import logging
from uuid import UUID

import pytest

from app.core.config import Env
from app.core.provider_observability import (
    Provider,
    ProviderCallContext,
    ProviderCallDescriptor,
    ProviderCallLogger,
    ProviderCallObserver,
    ProviderErrorCode,
    ProviderFailurePhase,
    ProviderOperation,
)


def _logger(stream: io.StringIO) -> ProviderCallLogger:
    logger = logging.Logger("provider-observability-test", level=logging.INFO)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return ProviderCallLogger(logger)


def _context(*, validation: bool = True) -> ProviderCallContext:
    return ProviderCallContext(
        trace_id="a" * 32,
        validation_run_id=(UUID("61a10000-0000-4000-8000-000000000003") if validation else None),
        environment=Env.LOCAL,
        validation_enabled=validation,
    )


def test_provider_logger_emits_prefixless_started_and_single_success_terminal() -> None:
    stream = io.StringIO()
    logger = _logger(stream)
    span = logger.start(
        context=_context(),
        descriptor=ProviderCallDescriptor(
            provider=Provider.OPENAI,
            operation=ProviderOperation.GUIDE_GENERATION,
            prompt_version="guide-prompt-v3",
        ),
        requested_model="gpt-4o-mini",
    )

    span.succeeded(
        model_name="gpt-4o-mini-2024-07-18",
        provider_response_id="resp_synthetic",
        provider_response_received=True,
    )
    span.failed(
        failure_phase=ProviderFailurePhase.UNKNOWN_INTERNAL,
        error_code=ProviderErrorCode.PROVIDER_INTERNAL_FAILURE,
    )

    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    events = [json.loads(line) for line in lines]
    assert [event["event"] for event in events] == ["provider.call.started", "provider.call.succeeded"]
    assert events[0]["outcome"] == "STARTED"
    assert events[0]["latency_ms"] is None
    assert events[1]["outcome"] == "SUCCESS"
    assert isinstance(events[1]["latency_ms"], int)
    assert events[0]["provider_call_id"] == events[1]["provider_call_id"]
    assert events[1]["validation_run_id"] == "61a10000-0000-4000-8000-000000000003"
    assert events[1]["provider_response_id"] == "resp_synthetic"
    assert all(set(event) == ProviderCallLogger.FIELD_NAMES for event in events)


def test_provider_logger_omits_provider_ids_for_general_requests_and_never_logs_payload() -> None:
    stream = io.StringIO()
    logger = _logger(stream)
    sentinel = "SYNTHETIC_SECRET_PAYLOAD_SENTINEL"
    span = logger.start(
        context=_context(validation=False),
        descriptor=ProviderCallDescriptor(
            provider=Provider.CLOVA_OCR,
            operation=ProviderOperation.PRESCRIPTION_RECOGNITION,
            prompt_version=None,
        ),
        requested_model=None,
        provider_request_id=sentinel,
    )

    span.failed(
        failure_phase=ProviderFailurePhase.HTTP_STATUS,
        error_code=ProviderErrorCode.PROVIDER_REQUEST_REJECTED,
        provider_request_id=sentinel,
        http_status=400,
        provider_response_received=True,
    )

    output = stream.getvalue()
    events = [json.loads(line) for line in output.splitlines()]
    assert sentinel not in output
    assert events[0]["provider_request_id"] is None
    assert events[1]["provider_request_id"] is None
    assert events[1]["http_status"] == 400


def test_provider_logger_failure_is_observational_only() -> None:
    class RaisingLogger:
        def info(self, _message: str) -> None:
            raise RuntimeError("synthetic logger failure")

        def warning(self, message: str) -> None:
            assert message == "provider_log_emit_failed=true"

    logger = ProviderCallLogger(RaisingLogger())  # type: ignore[arg-type]
    span = logger.start(
        context=_context(),
        descriptor=ProviderCallDescriptor(
            provider=Provider.OPENAI,
            operation=ProviderOperation.CHAT_GENERATION,
            prompt_version="chat-prompt-v2",
        ),
        requested_model="gpt-4o-mini",
    )

    span.succeeded(model_name="gpt-4o-mini", provider_response_received=True)


def test_provider_descriptor_rejects_prompt_on_clova_operation() -> None:
    with pytest.raises(ValueError):
        ProviderCallDescriptor(
            provider=Provider.CLOVA_OCR,
            operation=ProviderOperation.PRESCRIPTION_RECOGNITION,
            prompt_version="must-not-exist",
        )


def test_provider_observer_allows_explicit_unobserved_compatibility_mode() -> None:
    observer = ProviderCallObserver(context=None, descriptor=None, call_logger=_logger(io.StringIO()))

    assert observer.start(requested_model="gpt-4o-mini") is None


@pytest.mark.parametrize("missing", ["context", "descriptor"])
def test_provider_observer_rejects_partial_observability_configuration(missing: str) -> None:
    context = _context()
    descriptor = ProviderCallDescriptor(
        provider=Provider.OPENAI,
        operation=ProviderOperation.CHAT_GENERATION,
        prompt_version="chat-prompt-v2",
    )

    with pytest.raises(ValueError, match="context and descriptor must be provided together"):
        ProviderCallObserver(
            context=None if missing == "context" else context,
            descriptor=None if missing == "descriptor" else descriptor,
            call_logger=_logger(io.StringIO()),
        )
