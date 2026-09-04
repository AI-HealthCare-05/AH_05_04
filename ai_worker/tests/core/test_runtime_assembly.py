"""Consumer runtime 조립과 실패 격리 경계를 검증합니다(#233)."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_worker.core.config import Config
from ai_worker.core.consumer_execution import LeaseAwareConsumerExecution
from ai_worker.core.errors import WorkerError
from ai_worker.core.quarantine import (
    QuarantineExecution,
    QuarantineFailureCode,
    QuarantineRequest,
    RejectedWorkerDelivery,
)
from ai_worker.core.results import HandlerSuccess
from ai_worker.core.runtime_assembly import (
    RoutingResultStore,
    SessionScopedDeliveryExecution,
    SessionScopedRejectedDeliveryExecution,
    build_worker_runtime,
    create_session_factory,
)
from ai_worker.core.stream import WorkerDelivery
from ai_worker.schemas.messages import JobType
from provider_contracts.observability import DeploymentEnvironment
from provider_contracts.ocr import OcrEngine

_BASE_SETTINGS: dict[str, Any] = {
    "ENV": DeploymentEnvironment.LOCAL,
    "DB_HOST": "127.0.0.1",
    "DB_NAME": "test",
    "DB_USER": "worker",
    "DB_PASSWORD": "worker-password",
}


def _config(**overrides: Any) -> Config:
    return Config(_env_file=None, **{**_BASE_SETTINGS, **overrides})  # type: ignore[call-arg]


def _delivery() -> WorkerDelivery:
    return WorkerDelivery(stream_message_id="1-0", message=None)  # type: ignore[arg-type]


class _StubSession:
    async def __aenter__(self) -> "_StubSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _RaisingExecution:
    def __init__(self, error: BaseException) -> None:
        self._error = error
        self.calls = 0

    async def execute(self, delivery: WorkerDelivery) -> object:
        self.calls += 1
        raise self._error


class _RecordingQuarantineExecution:
    def __init__(self) -> None:
        self.requests: list[QuarantineRequest] = []

    async def execute(self, request: QuarantineRequest) -> object:
        self.requests.append(request)
        return request


# --- 설정 검증 -------------------------------------------------------------


def test_config_requires_database_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for setting_name in (
        "DB_HOST",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
    ):
        monkeypatch.delenv(setting_name, raising=False)

    with pytest.raises(ValidationError):
        Config(
            _env_file=None,
            ENV=DeploymentEnvironment.LOCAL,
        )  # type: ignore[call-arg]


def test_config_builds_database_url_with_special_characters() -> None:
    config = _config(DB_PASSWORD="p@ss/w%rd")

    rendered = config.database_url.render_as_string(hide_password=False)

    assert rendered.startswith("postgresql+asyncpg://worker:")
    assert "p%40ss%2Fw%25rd" in rendered


def test_config_rejects_heartbeat_interval_not_shorter_than_lease() -> None:
    with pytest.raises(ValidationError):
        _config(
            WORKER_LEASE_DURATION_SECONDS=30.0,
            WORKER_HEARTBEAT_INTERVAL_SECONDS=30.0,
        )


def test_config_rejects_hard_timeout_not_shorter_than_lease() -> None:
    with pytest.raises(ValidationError):
        _config(
            WORKER_LEASE_DURATION_SECONDS=30.0,
            WORKER_HEARTBEAT_INTERVAL_SECONDS=5.0,
            WORKER_HARD_TIMEOUT_SECONDS=30.0,
        )


def test_config_has_approved_worker_runtime_defaults() -> None:
    config = _config()

    assert config.WORKER_HARD_TIMEOUT_SECONDS == 60.0
    assert config.WORKER_LEASE_DURATION_SECONDS == 75.0
    assert config.WORKER_HEARTBEAT_INTERVAL_SECONDS == 10.0
    assert config.lease_duration.total_seconds() == 75.0
    assert config.heartbeat_interval.total_seconds() == 10.0


def test_config_rejects_hard_timeout_plus_completion_budget_exceeding_lease() -> None:
    with pytest.raises(
        ValidationError,
        match="WORKER_HARD_TIMEOUT_SECONDS와 OCR_RESPONSE_MARGIN_SECONDS",
    ):
        _config(
            WORKER_LEASE_DURATION_SECONDS=64.0,
            WORKER_HARD_TIMEOUT_SECONDS=60.0,
            OCR_RESPONSE_MARGIN_SECONDS=5.0,
        )


def test_config_rejects_ocr_deadline_exceeding_worker_hard_timeout() -> None:
    with pytest.raises(
        ValidationError,
        match="OCR_REQUEST_DEADLINE_SECONDS",
    ):
        _config(
            OCR_REQUEST_DEADLINE_SECONDS=61.0,
            WORKER_HARD_TIMEOUT_SECONDS=60.0,
        )


# --- Handler 등록 경계 -----------------------------------------------------


def test_ocr_handler_is_not_registered_without_a_provider() -> None:
    execution = SessionScopedDeliveryExecution(
        config=_config(),
        session_factory=create_session_factory(_engine_stub()),
        acknowledger=_AcknowledgerStub(),
        clock=lambda: datetime.now(UTC),
        logger=logging.getLogger("test"),
        ocr_provider=None,
    )

    assert execution.registered_types == frozenset()


def test_routing_result_store_rejects_unknown_handler_type() -> None:
    store = RoutingResultStore({})
    result = HandlerSuccess(
        event_id=uuid4(),
        job_id=uuid4(),
        handler_type=JobType.OCR,
    )

    with pytest.raises(WorkerError) as exc_info:
        asyncio.run(store.save(message=None, result=result))  # type: ignore[arg-type]

    assert exc_info.value.failure_code == "INTERNAL_ERROR"


# --- 실패 격리 -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        WorkerError(failure_code="DEPENDENCY_UNAVAILABLE"),
        RuntimeError("provider raw response should never be logged"),
    ],
)
async def test_delivery_failure_does_not_escape_execution_boundary(
    error: BaseException,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """한 메시지 실패가 Consumer process를 종료시키지 않아야 합니다."""

    execution = SessionScopedDeliveryExecution(
        config=_config(),
        session_factory=lambda: _StubSession(),  # type: ignore[arg-type, return-value]
        acknowledger=_AcknowledgerStub(),
        clock=lambda: datetime.now(UTC),
        logger=logging.getLogger("ai_worker.test"),
        ocr_provider=None,
    )
    inner = _RaisingExecution(error)
    # 실행 경계만 검증하므로 lease·DB 조립 없이 실패하는 실행으로 바꿉니다.
    execution._build_execution = lambda session: cast(  # type: ignore[method-assign]
        LeaseAwareConsumerExecution, inner
    )

    with caplog.at_level(logging.WARNING, logger="ai_worker.test"):
        assert await execution.execute(_delivery()) is None

    assert inner.calls == 1
    assert "provider raw response should never be logged" not in caplog.text


@pytest.mark.asyncio
async def test_rejected_delivery_is_converted_to_quarantine_request() -> None:
    received_at = datetime.now(UTC)
    rejected = RejectedWorkerDelivery(
        stream_name="oryak:jobs",
        stream_entry_id="1000-0",
        message_digest="a" * 64,
        failure_code=QuarantineFailureCode.INVALID_MESSAGE_SCHEMA,
        job_id=None,
        original_event_id=None,
        original_schema_version="1.0",
        trace_id=uuid4().hex,
    )
    execution = SessionScopedRejectedDeliveryExecution(
        session_factory=lambda: _StubSession(),  # type: ignore[arg-type, return-value]
        acknowledger=_AcknowledgerStub(),
        clock=lambda: received_at,
        logger=logging.getLogger("ai_worker.test"),
    )
    inner = _RecordingQuarantineExecution()
    execution._build_execution = lambda session: cast(  # type: ignore[method-assign]
        QuarantineExecution,
        inner,
    )

    result = await execution.execute(rejected)

    assert result is inner.requests[0]
    assert inner.requests == [rejected.to_quarantine_request(received_at=received_at)]


# --- 조립과 종료 -----------------------------------------------------------


@pytest.mark.asyncio
async def test_build_worker_runtime_closes_only_owned_resources() -> None:
    redis_client = _RedisStub()
    engine = _engine_stub()
    original_pool = engine.sync_engine.pool

    try:
        assembled = build_worker_runtime(
            _config(),
            logger=logging.getLogger("test"),
            clock=lambda: datetime.now(UTC),
            redis_client=redis_client,  # type: ignore[arg-type]
            engine=engine,
        )

        await assembled.aclose()

        assert redis_client.closed is False
        assert engine.sync_engine.pool is original_pool
        assert assembled.registered_types == frozenset()
        assert assembled.recovery_scheduler is not None
    finally:
        await engine.dispose()


class _AcknowledgerStub:
    def __init__(self) -> None:
        self.acknowledged: list[str] = []

    async def acknowledge(self, stream_message_id: str) -> None:
        self.acknowledged.append(stream_message_id)


class _RedisStub:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _engine_stub() -> Any:
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:5432/test")


@pytest.mark.asyncio
async def test_build_worker_runtime_registers_ocr_handler_when_engine_is_provided() -> None:
    engine = _engine_stub()
    assembled = None

    try:
        assembled = build_worker_runtime(
            _config(),
            logger=logging.getLogger("test"),
            clock=lambda: datetime.now(UTC),
            ocr_engine=cast(OcrEngine, object()),
            redis_client=_RedisStub(),  # type: ignore[arg-type]
            engine=engine,
        )

        assert assembled.registered_types == frozenset(
            {
                JobType.OCR,
            }
        )
        assert assembled.recovery_scheduler is not None
    finally:
        if assembled is not None:
            await assembled.aclose()

        await engine.dispose()
