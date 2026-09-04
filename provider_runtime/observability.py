import json
import logging
import sys
import time
from datetime import UTC, datetime
from uuid import uuid4

from provider_contracts.observability import (
    DeploymentEnvironment,
    Provider,
    ProviderCallContext,
    ProviderCallDescriptor,
    ProviderErrorCode,
    ProviderFailurePhase,
    ProviderOperation,
)

__all__ = [
    "Provider",
    "ProviderCallContext",
    "ProviderCallDescriptor",
    "ProviderCallLogger",
    "ProviderCallObserver",
    "ProviderCallSpan",
    "ProviderErrorCode",
    "ProviderFailurePhase",
    "ProviderOperation",
]


class ProviderCallSpan:
    def __init__(
        self,
        *,
        owner: "ProviderCallLogger",
        context: ProviderCallContext,
        descriptor: ProviderCallDescriptor,
        requested_model: str | None,
        provider_request_id: str | None,
    ) -> None:
        self._owner = owner
        self._context = context
        self._descriptor = descriptor
        self._requested_model = requested_model
        self._provider_call_id = str(uuid4())
        self._started_at = time.monotonic()
        self._terminal_emitted = False
        self._owner._emit(
            self._event(
                event="provider.call.started",
                outcome="STARTED",
                provider_request_id=provider_request_id,
            )
        )

    @property
    def terminal_emitted(self) -> bool:
        return self._terminal_emitted

    def succeeded(
        self,
        *,
        model_name: str | None,
        provider_request_id: str | None = None,
        provider_response_id: str | None = None,
        provider_response_received: bool,
        http_status: int | None = None,
    ) -> None:
        if self._terminal_emitted:
            return
        self._terminal_emitted = True
        self._owner._emit(
            self._event(
                event="provider.call.succeeded",
                outcome="SUCCESS",
                model_name=model_name,
                provider_request_id=provider_request_id,
                provider_response_id=provider_response_id,
                provider_response_received=provider_response_received,
                http_status=http_status,
                latency_ms=self._latency_ms(),
            )
        )

    def failed(
        self,
        *,
        failure_phase: ProviderFailurePhase,
        error_code: ProviderErrorCode,
        provider_request_id: str | None = None,
        provider_response_id: str | None = None,
        provider_response_received: bool = False,
        http_status: int | None = None,
        model_name: str | None = None,
    ) -> None:
        if self._terminal_emitted:
            return
        self._terminal_emitted = True
        self._owner._emit(
            self._event(
                event="provider.call.failed",
                outcome="FAILED",
                model_name=model_name,
                provider_request_id=provider_request_id,
                provider_response_id=provider_response_id,
                provider_response_received=provider_response_received,
                http_status=http_status,
                latency_ms=self._latency_ms(),
                failure_phase=failure_phase,
                error_code=error_code,
            )
        )

    def _latency_ms(self) -> int:
        return max(0, int((time.monotonic() - self._started_at) * 1000))

    def _event(
        self,
        *,
        event: str,
        outcome: str,
        model_name: str | None = None,
        provider_request_id: str | None = None,
        provider_response_id: str | None = None,
        provider_response_received: bool = False,
        http_status: int | None = None,
        latency_ms: int | None = None,
        failure_phase: ProviderFailurePhase | None = None,
        error_code: ProviderErrorCode | None = None,
    ) -> dict[str, object]:
        include_provider_ids = (
            self._context.validation_run_id is not None
            and self._context.validation_enabled
            and self._context.environment is DeploymentEnvironment.LOCAL
        )
        return {
            "schema_version": "provider-call-log-v1",
            "event": event,
            "occurred_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "environment": self._context.environment.value,
            "validation_run_id": (
                str(self._context.validation_run_id) if self._context.validation_run_id is not None else None
            ),
            "trace_id": self._context.trace_id,
            "provider_call_id": self._provider_call_id,
            "provider": self._descriptor.provider.value,
            "operation": self._descriptor.operation.value,
            "requested_model": self._requested_model,
            "model_name": model_name,
            "prompt_version": self._descriptor.prompt_version,
            "provider_request_id": provider_request_id if include_provider_ids else None,
            "provider_response_id": provider_response_id if include_provider_ids else None,
            "provider_response_received": provider_response_received,
            "http_status": http_status,
            "latency_ms": latency_ms,
            "outcome": outcome,
            "failure_phase": failure_phase.value if failure_phase is not None else None,
            "error_code": error_code.value if error_code is not None else None,
        }


class ProviderCallLogger:
    FIELD_NAMES = frozenset(
        {
            "schema_version",
            "event",
            "occurred_at",
            "environment",
            "validation_run_id",
            "trace_id",
            "provider_call_id",
            "provider",
            "operation",
            "requested_model",
            "model_name",
            "prompt_version",
            "provider_request_id",
            "provider_response_id",
            "provider_response_received",
            "http_status",
            "latency_ms",
            "outcome",
            "failure_phase",
            "error_code",
        }
    )

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def start(
        self,
        *,
        context: ProviderCallContext,
        descriptor: ProviderCallDescriptor,
        requested_model: str | None,
        provider_request_id: str | None = None,
    ) -> ProviderCallSpan:
        return ProviderCallSpan(
            owner=self,
            context=context,
            descriptor=descriptor,
            requested_model=requested_model,
            provider_request_id=provider_request_id,
        )

    def _emit(self, event: dict[str, object]) -> None:
        try:
            self._logger.info(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        except Exception:
            try:
                self._logger.warning("provider_log_emit_failed=true")
            except Exception:
                pass


class ProviderCallObserver:
    def __init__(
        self,
        *,
        context: ProviderCallContext | None,
        descriptor: ProviderCallDescriptor | None,
        call_logger: ProviderCallLogger,
        observability_disabled: bool = False,
    ) -> None:
        if observability_disabled:
            if context is not None or descriptor is not None:
                raise ValueError("disabled observability must not include context or descriptor")
        elif context is None and descriptor is None:
            raise ValueError("active observability requires context and descriptor")
        elif (context is None) is not (descriptor is None):
            raise ValueError("context and descriptor must be provided together")
        self._context = context
        self._descriptor = descriptor
        self._call_logger = call_logger
        self._observability_disabled = observability_disabled

    def start(
        self,
        *,
        requested_model: str | None,
        provider_request_id: str | None = None,
    ) -> ProviderCallSpan | None:
        if self._observability_disabled:
            return None
        # __init__이 active 상태의 두 값을 보장합니다. 향후 alternate construction이나
        # 내부 상태 변경이 이 불변식을 우회해도 무기록 Provider 호출로 진행하지 않습니다.
        if self._context is None or self._descriptor is None:
            raise RuntimeError("active Provider observability is not configured")
        return self._call_logger.start(
            context=self._context,
            descriptor=self._descriptor,
            requested_model=requested_model,
            provider_request_id=provider_request_id,
        )

    @staticmethod
    def succeeded(
        span: ProviderCallSpan | None,
        *,
        response: object | None = None,
        model_name: str | None,
        provider_request_id: str | None = None,
        provider_response_received: bool = True,
        http_status: int | None = None,
    ) -> None:
        if span is None:
            return
        span.succeeded(
            model_name=model_name,
            provider_request_id=provider_request_id,
            provider_response_id=ProviderCallObserver._string_attribute(response, "id"),
            provider_response_received=provider_response_received,
            http_status=http_status,
        )

    @staticmethod
    def failed(
        span: ProviderCallSpan | None,
        phase: ProviderFailurePhase,
        code: ProviderErrorCode,
        *,
        response: object | None = None,
        provider_response_received: bool = False,
        http_status: int | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        if span is None:
            return
        span.failed(
            failure_phase=phase,
            error_code=code,
            provider_request_id=provider_request_id,
            provider_response_id=ProviderCallObserver._string_attribute(response, "id"),
            provider_response_received=provider_response_received,
            http_status=http_status,
            model_name=ProviderCallObserver._string_attribute(response, "model"),
        )

    @classmethod
    def failed_http_status(
        cls,
        span: ProviderCallSpan | None,
        error: object,
        code: ProviderErrorCode,
    ) -> None:
        cls.failed(
            span,
            ProviderFailurePhase.HTTP_STATUS,
            code,
            provider_response_received=True,
            http_status=cls._integer_attribute(error, "status_code"),
            provider_request_id=cls._string_attribute(error, "request_id"),
        )

    @classmethod
    def failed_response_validation(cls, span: ProviderCallSpan | None, error: object) -> None:
        response = getattr(error, "response", None)
        cls.failed(
            span,
            ProviderFailurePhase.RESPONSE_VALIDATION,
            ProviderErrorCode.PROVIDER_RESPONSE_INVALID,
            provider_response_received=response is not None,
            http_status=cls._integer_attribute(response, "status_code"),
        )

    @staticmethod
    def error_code_for_http_status(status_code: int) -> ProviderErrorCode:
        if status_code == 429:
            return ProviderErrorCode.PROVIDER_RATE_LIMITED
        if status_code >= 500:
            return ProviderErrorCode.PROVIDER_UNAVAILABLE
        return ProviderErrorCode.PROVIDER_REQUEST_REJECTED

    @staticmethod
    def _string_attribute(value: object | None, name: str) -> str | None:
        attribute = getattr(value, name, None)
        return attribute if isinstance(attribute, str) and attribute else None

    @staticmethod
    def _integer_attribute(value: object | None, name: str) -> int | None:
        attribute = getattr(value, name, None)
        return attribute if isinstance(attribute, int) else None


def _build_provider_logger() -> logging.Logger:
    logger = logging.getLogger("provider.calls")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(getattr(handler, "_provider_jsonl", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._provider_jsonl = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    return logger


provider_call_logger = ProviderCallLogger(_build_provider_logger())
