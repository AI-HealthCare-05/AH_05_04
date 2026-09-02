"""Consumer의 저장·commit·ACK 실행 순서를 검증합니다."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ai_worker.core.consumer_execution import (
    ConsumerExecution,
    LeaseAwareConsumerExecution,
    WorkerDelivery,
)
from ai_worker.core.dispatcher import Dispatcher, HandlerExecutionError
from ai_worker.core.errors import (
    ConsumerAcknowledgementError,
    ConsumerPersistenceError,
    HandlerResultMismatchError,
)
from ai_worker.core.handler import Handler
from ai_worker.core.job_execution import (
    CommittedDelivery,
    ExecutionLease,
    LeaseAcquisitionResult,
    LeaseNotAcquired,
)
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
            "trace_id": uuid4().hex,
        }
    )


class FakeHandler:
    handler_type = JobType.OCR

    def __init__(
        self,
        *,
        mismatched: bool = False,
        error: BaseException | None = None,
    ) -> None:
        self._mismatched = mismatched
        self._error = error

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        if self._error is not None:
            raise self._error

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


class FakeJobExecutionRepository:
    def __init__(
        self,
        events: list[str],
        *,
        complete_successfully: bool,
        acquisition_result: LeaseAcquisitionResult | None = None,
    ) -> None:
        self._events = events
        self._complete_successfully = complete_successfully
        self._acquisition_result = acquisition_result

    async def acquire_lease(
        self,
        message: WorkerMessage,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> LeaseAcquisitionResult:
        self._events.append("acquire")
        if self._acquisition_result is not None:
            return self._acquisition_result
        return ExecutionLease(
            job_id=message.job_id,
            event_id=message.event_id,
            attempt=message.attempt,
            lease_token=uuid4().hex,
            lease_expires_at=now + lease_duration,
        )

    async def refresh_heartbeat(
        self,
        lease: ExecutionLease,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> ExecutionLease | None:
        return lease

    async def complete_execution(
        self,
        lease: ExecutionLease,
        *,
        completed_at: datetime,
    ) -> bool:
        self._events.append("complete")
        return self._complete_successfully


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
async def test_consumer_rolls_back_without_side_effects_for_mismatched_result() -> None:
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

    # 저장·commit·ACK는 실행되지 않고 rollback만 수행합니다.
    assert events == ["rollback"]
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


@pytest.mark.asyncio
async def test_mismatched_result_keeps_original_error_when_rollback_fails() -> None:
    """rollback 실패가 원래 검증 오류를 덮어쓰지 않게 합니다."""

    events: list[str] = []
    execution, acknowledger = build_execution(
        handler=FakeHandler(mismatched=True),
        events=events,
        fail_rollback=True,
    )
    delivery = WorkerDelivery(
        stream_message_id="1005-0",
        message=build_message(),
    )

    with pytest.raises(HandlerResultMismatchError) as exc_info:
        await execution.execute(delivery)

    error = exc_info.value

    assert events == ["rollback"]
    assert acknowledger.acknowledged_ids == []
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.asyncio
async def test_consumer_rolls_back_and_does_not_ack_when_handler_raises() -> None:
    """Handler 예외에서도 rollback하고 ACK하지 않습니다."""

    events: list[str] = []
    execution, acknowledger = build_execution(
        handler=FakeHandler(
            error=RuntimeError("synthetic sensitive handler failure"),
        ),
        events=events,
    )
    delivery = WorkerDelivery(
        stream_message_id="1006-0",
        message=build_message(),
    )

    with pytest.raises(HandlerExecutionError) as exc_info:
        await execution.execute(delivery)

    error = exc_info.value

    assert events == ["rollback"]
    assert acknowledger.acknowledged_ids == []
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "synthetic sensitive handler failure" not in str(error)


@pytest.mark.asyncio
async def test_consumer_rolls_back_and_does_not_ack_when_cancelled() -> None:
    """실행 취소에서도 rollback하고 ACK하지 않습니다."""

    events: list[str] = []
    execution, acknowledger = build_execution(
        handler=FakeHandler(error=asyncio.CancelledError()),
        events=events,
    )
    delivery = WorkerDelivery(
        stream_message_id="1007-0",
        message=build_message(),
    )

    with pytest.raises(asyncio.CancelledError):
        await execution.execute(delivery)

    assert events == ["rollback"]
    assert acknowledger.acknowledged_ids == []


@pytest.mark.asyncio
async def test_lost_fencing_rolls_back_result_and_does_not_ack() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    registry = HandlerRegistry()
    registry.register(FakeHandler())
    acknowledger = FakeAcknowledger(events)

    execution = LeaseAwareConsumerExecution(
        dispatcher=Dispatcher(registry),
        result_store=FakeResultStore(events),
        transaction=FakeTransaction(events),
        acknowledger=acknowledger,
        job_repository=FakeJobExecutionRepository(
            events,
            complete_successfully=False,
        ),
        lease_duration=timedelta(seconds=30),
        clock=lambda: now,
    )

    delivery = WorkerDelivery(
        stream_message_id="2000-0",
        message=build_message(),
    )

    result = await execution.execute(delivery)

    assert isinstance(result, LeaseNotAcquired)
    assert events == [
        "acquire",
        "save",
        "complete",
        "rollback",
    ]
    assert acknowledger.acknowledged_ids == []


@pytest.mark.asyncio
async def test_leased_consumer_commits_before_acknowledgement() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    registry = HandlerRegistry()
    registry.register(FakeHandler())
    acknowledger = FakeAcknowledger(events)

    execution = LeaseAwareConsumerExecution(
        dispatcher=Dispatcher(registry),
        result_store=FakeResultStore(events),
        transaction=FakeTransaction(events),
        acknowledger=acknowledger,
        job_repository=FakeJobExecutionRepository(
            events,
            complete_successfully=True,
        ),
        lease_duration=timedelta(seconds=30),
        clock=lambda: now,
    )

    message = build_message()
    delivery = WorkerDelivery(
        stream_message_id="2001-0",
        message=message,
    )

    result = await execution.execute(delivery)

    assert isinstance(result, HandlerSuccess)
    assert result.job_id == message.job_id
    assert events == [
        "acquire",
        "save",
        "complete",
        "commit",
        "ack",
    ]
    assert acknowledger.acknowledged_ids == ["2001-0"]


@pytest.mark.asyncio
async def test_committed_redelivery_skips_handler_and_only_acknowledges() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    message = build_message()
    committed = CommittedDelivery(
        job_id=message.job_id,
        event_id=message.event_id,
        attempt=message.attempt,
    )

    registry = HandlerRegistry()
    registry.register(FakeHandler())
    acknowledger = FakeAcknowledger(events)

    execution = LeaseAwareConsumerExecution(
        dispatcher=Dispatcher(registry),
        result_store=FakeResultStore(events),
        transaction=FakeTransaction(events),
        acknowledger=acknowledger,
        job_repository=FakeJobExecutionRepository(
            events,
            complete_successfully=True,
            acquisition_result=committed,
        ),
        lease_duration=timedelta(seconds=30),
        clock=lambda: now,
    )

    result = await execution.execute(
        WorkerDelivery(
            stream_message_id="2002-0",
            message=message,
        )
    )

    assert result == committed
    assert events == [
        "acquire",
        "commit",
        "ack",
    ]
    assert acknowledger.acknowledged_ids == ["2002-0"]


@pytest.mark.asyncio
async def test_unacquired_lease_skips_handler_and_does_not_ack() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    registry = HandlerRegistry()
    registry.register(FakeHandler())
    acknowledger = FakeAcknowledger(events)

    execution = LeaseAwareConsumerExecution(
        dispatcher=Dispatcher(registry),
        result_store=FakeResultStore(events),
        transaction=FakeTransaction(events),
        acknowledger=acknowledger,
        job_repository=FakeJobExecutionRepository(
            events,
            complete_successfully=False,
            acquisition_result=LeaseNotAcquired(),
        ),
        lease_duration=timedelta(seconds=30),
        clock=lambda: now,
    )

    result = await execution.execute(
        WorkerDelivery(
            stream_message_id="2003-0",
            message=build_message(),
        )
    )

    assert isinstance(result, LeaseNotAcquired)
    assert events == [
        "acquire",
        "rollback",
    ]
    assert acknowledger.acknowledged_ids == []


@pytest.mark.asyncio
async def test_leased_consumer_does_not_rollback_after_ack_failure() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    registry = HandlerRegistry()
    registry.register(FakeHandler())

    execution = LeaseAwareConsumerExecution(
        dispatcher=Dispatcher(registry),
        result_store=FakeResultStore(events),
        transaction=FakeTransaction(events),
        acknowledger=FakeAcknowledger(
            events,
            fail_acknowledge=True,
        ),
        job_repository=FakeJobExecutionRepository(
            events,
            complete_successfully=True,
        ),
        lease_duration=timedelta(seconds=30),
        clock=lambda: now,
    )

    with pytest.raises(ConsumerAcknowledgementError):
        await execution.execute(
            WorkerDelivery(
                stream_message_id="2004-0",
                message=build_message(),
            )
        )

    assert events == [
        "acquire",
        "save",
        "complete",
        "commit",
        "ack",
    ]
    assert "rollback" not in events
