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
    WorkerError,
)
from ai_worker.core.handler import Handler, HandlerExecutionContext
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
        events: list[str] | None = None,
        mismatched: bool = False,
        error: BaseException | None = None,
    ) -> None:
        self._events = events
        self._mismatched = mismatched
        self._error = error

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        if self._events is not None:
            self._events.append("handle")

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


class FakeDomainExecutionStarter:
    def __init__(
        self,
        events: list[str],
        *,
        start_successfully: bool = True,
    ) -> None:
        self._events = events
        self._start_successfully = start_successfully

    async def start(
        self,
        *,
        message: WorkerMessage,
        started_at: datetime,
    ) -> bool:
        _ = message, started_at
        self._events.append("start")
        return self._start_successfully


class FakeLeaseHeartbeatHandle:
    def __init__(
        self,
        events: list[str],
        *,
        ownership_retained: bool,
        fail_stop: bool = False,
    ) -> None:
        self._events = events
        self._ownership_retained = ownership_retained
        self._fail_stop = fail_stop
        self._finished = asyncio.Event()

    async def wait(self) -> bool:
        await self._finished.wait()
        return self._ownership_retained

    async def stop(self) -> bool:
        self._events.append("heartbeat_stop")
        self._finished.set()

        if self._fail_stop:
            raise RuntimeError("synthetic heartbeat stop failure")

        return self._ownership_retained


class BackgroundLeaseHeartbeatHandle:
    """실제 background task 종료 여부를 검증하는 테스트 handle입니다."""

    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._stop_event = asyncio.Event()
        self.task = asyncio.create_task(self._maintain())

    async def _maintain(self) -> bool:
        await self._stop_event.wait()
        return True

    async def wait(self) -> bool:
        return await asyncio.shield(self.task)

    async def stop(self) -> bool:
        self._events.append("heartbeat_stop")
        self._stop_event.set()
        return await self.task


class LosingLeaseHeartbeatHandle:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._ownership_lost = asyncio.Event()

    def lose_ownership(self) -> None:
        self._ownership_lost.set()

    async def wait(self) -> bool:
        await self._ownership_lost.wait()
        return False

    async def stop(self) -> bool:
        self._events.append("heartbeat_stop")
        return not self._ownership_lost.is_set()


class LosingLeaseHeartbeat:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.handle = LosingLeaseHeartbeatHandle(events)

    async def start(
        self,
        lease: ExecutionLease,
    ) -> LosingLeaseHeartbeatHandle:
        self._events.append("heartbeat_start")
        return self.handle


class BackgroundLeaseHeartbeat:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.handle: BackgroundLeaseHeartbeatHandle | None = None

    async def start(
        self,
        lease: ExecutionLease,
    ) -> BackgroundLeaseHeartbeatHandle:
        self._events.append("heartbeat_start")
        self.handle = BackgroundLeaseHeartbeatHandle(self._events)
        return self.handle


class FakeLeaseHeartbeat:
    def __init__(
        self,
        events: list[str],
        *,
        ownership_retained: bool = True,
        fail_stop: bool = False,
    ) -> None:
        self._events = events
        self._ownership_retained = ownership_retained
        self._fail_stop = fail_stop

    async def start(
        self,
        lease: ExecutionLease,
    ) -> FakeLeaseHeartbeatHandle:
        self._events.append("heartbeat_start")
        return FakeLeaseHeartbeatHandle(
            self._events,
            ownership_retained=self._ownership_retained,
            fail_stop=self._fail_stop,
        )


class DirectFailureDispatcher(Dispatcher):
    """Dispatcher 경계 밖의 예외가 발생하는 교체 구현입니다."""

    def __init__(self) -> None:
        super().__init__(HandlerRegistry())

    async def dispatch(
        self,
        message: WorkerMessage,
        *,
        context: HandlerExecutionContext | None = None,
    ) -> HandlerSuccess:
        _ = message, context
        raise RuntimeError("synthetic unwrapped dispatcher failure")


class CancellableHandler:
    handler_type = JobType.OCR

    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self._release = asyncio.Event()

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        self._events.append("handle")
        self.started.set()

        try:
            await self._release.wait()
        except asyncio.CancelledError:
            self._events.append("handler_cancelled")
            self.cancelled.set()
            raise

        raise AssertionError("테스트 Handler가 예기치 않게 해제됐습니다.")


class DeadlineRecordingHandler:
    handler_type = JobType.OCR

    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.received_context: HandlerExecutionContext | None = None

    async def handle(
        self,
        message: WorkerMessage,
        *,
        context: HandlerExecutionContext | None = None,
    ) -> HandlerSuccess:
        self._events.append("handle")
        self.received_context = context

        return HandlerSuccess(
            event_id=message.event_id,
            job_id=message.job_id,
            handler_type=self.handler_type,
        )


class DeadlineAwareCancellableHandler:
    handler_type = JobType.OCR

    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def handle(
        self,
        message: WorkerMessage,
        *,
        context: HandlerExecutionContext | None = None,
    ) -> HandlerSuccess:
        assert context is not None

        self._events.append("handle")
        self.started.set()

        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self._events.append("handler_cancelled")
            self.cancelled.set()
            raise

        raise AssertionError("취소되지 않은 Handler가 반환됐습니다.")


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
        heartbeat=FakeLeaseHeartbeat(events),
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
        "commit",
        "heartbeat_start",
        "heartbeat_stop",
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
    registry.register(FakeHandler(events=events))
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
        heartbeat=FakeLeaseHeartbeat(events),
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
        "commit",
        "heartbeat_start",
        "handle",
        "heartbeat_stop",
        "save",
        "complete",
        "commit",
        "ack",
    ]
    assert acknowledger.acknowledged_ids == ["2001-0"]


@pytest.mark.asyncio
async def test_lost_heartbeat_discards_handler_result_and_does_not_ack() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    registry = HandlerRegistry()
    registry.register(FakeHandler(events=events))
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
        heartbeat=FakeLeaseHeartbeat(
            events,
            ownership_retained=False,
        ),
        lease_duration=timedelta(seconds=30),
        clock=lambda: now,
    )

    result = await execution.execute(
        WorkerDelivery(
            stream_message_id="2001-2",
            message=build_message(),
        )
    )

    assert isinstance(result, LeaseNotAcquired)
    assert events == [
        "acquire",
        "commit",
        "heartbeat_start",
        "handle",
        "heartbeat_stop",
        "rollback",
    ]
    assert acknowledger.acknowledged_ids == []


@pytest.mark.asyncio
async def test_lost_heartbeat_cancels_running_handler() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    handler = CancellableHandler(events)
    heartbeat = LosingLeaseHeartbeat(events)
    registry = HandlerRegistry()
    registry.register(handler)
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
        heartbeat=heartbeat,
        lease_duration=timedelta(seconds=30),
        clock=lambda: now,
    )

    execution_task = asyncio.create_task(
        execution.execute(
            WorkerDelivery(
                stream_message_id="2001-5",
                message=build_message(),
            )
        )
    )

    await asyncio.wait_for(handler.started.wait(), timeout=1)
    heartbeat.handle.lose_ownership()

    result = await asyncio.wait_for(execution_task, timeout=1)

    assert isinstance(result, LeaseNotAcquired)
    assert handler.cancelled.is_set()
    assert "handler_cancelled" in events
    assert "heartbeat_stop" in events
    assert "save" not in events
    assert "complete" not in events
    assert acknowledger.acknowledged_ids == []


@pytest.mark.asyncio
async def test_leased_consumer_stops_heartbeat_when_dispatcher_raises() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    acknowledger = FakeAcknowledger(events)
    heartbeat = BackgroundLeaseHeartbeat(events)

    execution = LeaseAwareConsumerExecution(
        dispatcher=DirectFailureDispatcher(),
        result_store=FakeResultStore(events),
        transaction=FakeTransaction(events),
        acknowledger=acknowledger,
        job_repository=FakeJobExecutionRepository(
            events,
            complete_successfully=True,
        ),
        heartbeat=heartbeat,
        lease_duration=timedelta(seconds=30),
        clock=lambda: now,
    )

    with pytest.raises(
        RuntimeError,
        match="synthetic unwrapped dispatcher failure",
    ):
        await execution.execute(
            WorkerDelivery(
                stream_message_id="2001-3",
                message=build_message(),
            )
        )

    assert events == [
        "acquire",
        "commit",
        "heartbeat_start",
        "heartbeat_stop",
        "rollback",
    ]
    assert acknowledger.acknowledged_ids == []
    assert heartbeat.handle is not None
    assert heartbeat.handle.task.done()


@pytest.mark.asyncio
async def test_heartbeat_stop_failure_does_not_hide_dispatcher_error() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    acknowledger = FakeAcknowledger(events)

    execution = LeaseAwareConsumerExecution(
        dispatcher=DirectFailureDispatcher(),
        result_store=FakeResultStore(events),
        transaction=FakeTransaction(events),
        acknowledger=acknowledger,
        job_repository=FakeJobExecutionRepository(
            events,
            complete_successfully=True,
        ),
        heartbeat=FakeLeaseHeartbeat(
            events,
            fail_stop=True,
        ),
        lease_duration=timedelta(seconds=30),
        clock=lambda: now,
    )

    with pytest.raises(
        RuntimeError,
        match="synthetic unwrapped dispatcher failure",
    ):
        await execution.execute(
            WorkerDelivery(
                stream_message_id="2001-4",
                message=build_message(),
            )
        )

    assert events == [
        "acquire",
        "commit",
        "heartbeat_start",
        "heartbeat_stop",
        "rollback",
    ]
    assert acknowledger.acknowledged_ids == []


@pytest.mark.asyncio
async def test_leased_consumer_does_not_run_handler_when_lease_commit_fails() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    registry = HandlerRegistry()
    registry.register(FakeHandler(events=events))
    acknowledger = FakeAcknowledger(events)

    execution = LeaseAwareConsumerExecution(
        dispatcher=Dispatcher(registry),
        result_store=FakeResultStore(events),
        transaction=FakeTransaction(
            events,
            fail_commit=True,
        ),
        acknowledger=acknowledger,
        job_repository=FakeJobExecutionRepository(
            events,
            complete_successfully=True,
        ),
        heartbeat=FakeLeaseHeartbeat(events),
        lease_duration=timedelta(seconds=30),
        clock=lambda: now,
    )

    with pytest.raises(ConsumerPersistenceError):
        await execution.execute(
            WorkerDelivery(
                stream_message_id="2001-1",
                message=build_message(),
            )
        )

    assert events == [
        "acquire",
        "commit",
        "rollback",
    ]
    assert acknowledger.acknowledged_ids == []


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
        heartbeat=FakeLeaseHeartbeat(events),
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
        heartbeat=FakeLeaseHeartbeat(events),
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
        heartbeat=FakeLeaseHeartbeat(events),
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
        "commit",
        "heartbeat_start",
        "heartbeat_stop",
        "save",
        "complete",
        "commit",
        "ack",
    ]
    assert "rollback" not in events


@pytest.mark.asyncio
async def test_leased_consumer_passes_worker_deadline_to_handler() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    monotonic_now = 1000.0
    handler = DeadlineRecordingHandler(events)
    registry = HandlerRegistry()
    registry.register(handler)
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
        heartbeat=FakeLeaseHeartbeat(events),
        lease_duration=timedelta(seconds=75),
        clock=lambda: now,
        hard_timeout_seconds=60.0,
        monotonic_clock=lambda: monotonic_now,
    )

    await execution.execute(
        WorkerDelivery(
            stream_message_id="3000-0",
            message=build_message(),
        )
    )

    assert handler.received_context == HandlerExecutionContext(
        worker_deadline=1060.0,
    )
    assert events == [
        "acquire",
        "commit",
        "heartbeat_start",
        "handle",
        "heartbeat_stop",
        "save",
        "complete",
        "commit",
        "ack",
    ]


@pytest.mark.asyncio
async def test_hard_timeout_cancels_handler_without_result_commit_or_ack() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    handler = DeadlineAwareCancellableHandler(events)
    registry = HandlerRegistry()
    registry.register(handler)
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
        heartbeat=FakeLeaseHeartbeat(events),
        lease_duration=timedelta(seconds=75),
        clock=lambda: now,
        hard_timeout_seconds=0.01,
    )

    with pytest.raises(WorkerError) as captured:
        await asyncio.wait_for(
            execution.execute(
                WorkerDelivery(
                    stream_message_id="3001-0",
                    message=build_message(),
                )
            ),
            timeout=1,
        )

    assert captured.value.failure_code == "TIMEOUT"
    assert handler.started.is_set()
    assert handler.cancelled.is_set()
    assert events == [
        "acquire",
        "commit",
        "heartbeat_start",
        "handle",
        "handler_cancelled",
        "heartbeat_stop",
        "rollback",
    ]
    assert "save" not in events
    assert "complete" not in events
    assert "ack" not in events
    assert acknowledger.acknowledged_ids == []


@pytest.mark.asyncio
async def test_leased_consumer_commits_domain_processing_before_handler() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    registry = HandlerRegistry()
    registry.register(FakeHandler(events=events))
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
        heartbeat=FakeLeaseHeartbeat(events),
        lease_duration=timedelta(seconds=75),
        clock=lambda: now,
        execution_starter=FakeDomainExecutionStarter(events),
    )

    result = await execution.execute(
        WorkerDelivery(
            stream_message_id="2010-0",
            message=build_message(),
        )
    )

    assert isinstance(result, HandlerSuccess)
    assert events == [
        "acquire",
        "start",
        "commit",
        "heartbeat_start",
        "handle",
        "heartbeat_stop",
        "save",
        "complete",
        "commit",
        "ack",
    ]


@pytest.mark.asyncio
async def test_leased_consumer_does_not_run_handler_when_domain_start_fails() -> None:
    events: list[str] = []
    now = datetime.now(UTC)
    registry = HandlerRegistry()
    registry.register(FakeHandler(events=events))
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
        heartbeat=FakeLeaseHeartbeat(events),
        lease_duration=timedelta(seconds=75),
        clock=lambda: now,
        execution_starter=FakeDomainExecutionStarter(
            events,
            start_successfully=False,
        ),
    )

    result = await execution.execute(
        WorkerDelivery(
            stream_message_id="2011-0",
            message=build_message(),
        )
    )

    assert isinstance(result, LeaseNotAcquired)
    assert events == [
        "acquire",
        "start",
        "rollback",
    ]
    assert acknowledger.acknowledged_ids == []
