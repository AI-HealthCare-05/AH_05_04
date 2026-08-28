"""Consumer의 저장·commit·ACK 실행 순서를 검증합니다."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_worker.core.consumer_execution import ConsumerExecution, WorkerDelivery
from ai_worker.core.dispatcher import Dispatcher
from ai_worker.core.errors import (
    ConsumerAcknowledgementError,
    ConsumerPersistenceError,
    HandlerResultMismatchError,
)
from ai_worker.core.handler import Handler
from ai_worker.core.registry import HandlerRegistry
from ai_worker.core.results import HandlerSuccess
from ai_worker.schemas.messages import JobType, WorkerMessage


def build_message() -> WorkerMessage:
    """의료정보를 포함하지 않는 합성 Worker 메시지를 생성합니다."""

    now = datetime.now(UTC)

    return WorkerMessage.model_validate(
        {
            "schema_version": "1.0",
            "event_id": str(uuid4()),
            "event_kind": "JOB_EXECUTE",
            "job_id": str(uuid4()),
            "job_type": "OCR",
            "domain_type": "OCR_JOB",
            "domain_id": str(uuid4()),
            "attempt": 1,
            "available_at": now.isoformat(),
            "enqueued_at": now.isoformat(),
            "trace_id": "consumer-execution-test-trace",
        }
    )


class FakeHandler:
    handler_type = JobType.OCR

    def __init__(self, *, mismatched: bool = False) -> None:
        self._mismatched = mismatched

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        return HandlerSuccess(
            event_id=message.event_id,
            job_id=uuid4() if self._mismatched else message.job_id,
            handler_type=self.handler_type,
        )


class FakeResultStore:
    def __init__(
        self,
        events: list[str],
        *,
        fail_save: bool = False,
    ) -> None:
        self.events = events
        self._fail_save = fail_save

    async def save(
        self,
        *,
        message: WorkerMessage,
        result: HandlerSuccess,
    ) -> None:
        self.events.append("save")

        if self._fail_save:
            raise RuntimeError("synthetic sensitive store failure")


class FakeTransaction:
    def __init__(
        self,
        events: list[str],
        *,
        fail_commit: bool = False,
        fail_rollback: bool = False,
    ) -> None:
        self.events = events
        self._fail_commit = fail_commit
        self._fail_rollback = fail_rollback

    async def commit(self) -> None:
        self.events.append("commit")

        if self._fail_commit:
            raise RuntimeError("synthetic commit failure")

    async def rollback(self) -> None:
        self.events.append("rollback")

        if self._fail_rollback:
            raise RuntimeError("synthetic sensitive rollback failure")


class FakeAcknowledger:
    def __init__(
        self,
        events: list[str],
        *,
        fail_acknowledge: bool = False,
    ) -> None:
        self.events = events
        self._fail_acknowledge = fail_acknowledge
        self.acknowledged_ids: list[str] = []

    async def acknowledge(self, stream_message_id: str) -> None:
        self.events.append("ack")

        if self._fail_acknowledge:
            raise RuntimeError("synthetic sensitive ack failure")

        self.acknowledged_ids.append(stream_message_id)


def build_execution(
    *,
    handler: Handler,
    events: list[str],
    fail_save: bool = False,
    fail_commit: bool = False,
    fail_rollback: bool = False,
    fail_acknowledge: bool = False,
) -> tuple[ConsumerExecution, FakeAcknowledger]:
    registry = HandlerRegistry()
    registry.register(handler)

    acknowledger = FakeAcknowledger(
        events,
        fail_acknowledge=fail_acknowledge,
    )

    execution = ConsumerExecution(
        dispatcher=Dispatcher(registry),
        result_store=FakeResultStore(
            events,
            fail_save=fail_save,
        ),
        transaction=FakeTransaction(
            events,
            fail_commit=fail_commit,
            fail_rollback=fail_rollback,
        ),
        acknowledger=acknowledger,
    )

    return execution, acknowledger


@pytest.mark.asyncio
async def test_consumer_acknowledges_only_after_save_and_commit() -> None:
    events: list[str] = []
    execution, acknowledger = build_execution(
        handler=FakeHandler(),
        events=events,
    )
    message = build_message()
    delivery = WorkerDelivery(
        stream_message_id="1000-0",
        message=message,
    )

    result = await execution.execute(delivery)

    assert result.job_id == message.job_id
    assert events == ["save", "commit", "ack"]
    assert acknowledger.acknowledged_ids == ["1000-0"]


@pytest.mark.asyncio
async def test_consumer_does_not_run_side_effects_for_mismatched_result() -> None:
    events: list[str] = []
    execution, acknowledger = build_execution(
        handler=FakeHandler(mismatched=True),
        events=events,
    )
    delivery = WorkerDelivery(
        stream_message_id="1001-0",
        message=build_message(),
    )

    with pytest.raises(HandlerResultMismatchError):
        await execution.execute(delivery)

    assert events == []
    assert acknowledger.acknowledged_ids == []


@pytest.mark.asyncio
async def test_consumer_does_not_ack_when_commit_fails() -> None:
    events: list[str] = []
    execution, acknowledger = build_execution(
        handler=FakeHandler(),
        events=events,
        fail_commit=True,
    )
    delivery = WorkerDelivery(
        stream_message_id="1002-0",
        message=build_message(),
    )

    with pytest.raises(ConsumerPersistenceError) as exc_info:
        await execution.execute(delivery)

    assert events == ["save", "commit", "rollback"]
    assert acknowledger.acknowledged_ids == []

    error = exc_info.value
    assert error.failure_code == "DEPENDENCY_UNAVAILABLE"
    assert "synthetic commit failure" not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.asyncio
async def test_consumer_does_not_commit_or_ack_when_store_fails() -> None:
    events: list[str] = []
    execution, acknowledger = build_execution(
        handler=FakeHandler(),
        events=events,
        fail_save=True,
    )
    delivery = WorkerDelivery(
        stream_message_id="1003-0",
        message=build_message(),
    )

    with pytest.raises(ConsumerPersistenceError) as exc_info:
        await execution.execute(delivery)

    assert events == ["save", "rollback"]
    assert acknowledger.acknowledged_ids == []

    error = exc_info.value
    assert error.failure_code == "DEPENDENCY_UNAVAILABLE"
    assert "synthetic sensitive store failure" not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.asyncio
async def test_consumer_hides_rollback_failure_and_does_not_ack() -> None:
    events: list[str] = []
    execution, acknowledger = build_execution(
        handler=FakeHandler(),
        events=events,
        fail_commit=True,
        fail_rollback=True,
    )
    delivery = WorkerDelivery(
        stream_message_id="1004-0",
        message=build_message(),
    )

    with pytest.raises(ConsumerPersistenceError) as exc_info:
        await execution.execute(delivery)

    assert events == ["save", "commit", "rollback"]
    assert acknowledger.acknowledged_ids == []

    error = exc_info.value
    assert "synthetic commit failure" not in str(error)
    assert "synthetic sensitive rollback failure" not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.asyncio
async def test_consumer_does_not_rollback_when_ack_fails_after_commit() -> None:
    events: list[str] = []
    execution, acknowledger = build_execution(
        handler=FakeHandler(),
        events=events,
        fail_acknowledge=True,
    )
    delivery = WorkerDelivery(
        stream_message_id="1005-0",
        message=build_message(),
    )

    with pytest.raises(ConsumerAcknowledgementError) as exc_info:
        await execution.execute(delivery)

    assert events == ["save", "commit", "ack"]
    assert acknowledger.acknowledged_ids == []

    error = exc_info.value
    assert error.failure_code == "DEPENDENCY_UNAVAILABLE"
    assert "synthetic sensitive ack failure" not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize("stream_message_id", ["", " ", "\t", "\n"])
def test_worker_delivery_rejects_blank_stream_message_id(
    stream_message_id: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="stream_message_id는 비어 있을 수 없습니다",
    ):
        WorkerDelivery(
            stream_message_id=stream_message_id,
            message=build_message(),
        )
