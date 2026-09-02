"""Fake Stream과 Consumer 실행 경계의 one-cycle 테스트입니다."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_worker.adapters.fake_stream import FakeStreamAdapter
from ai_worker.core.consumer_execution import ConsumerExecution
from ai_worker.core.dispatcher import Dispatcher
from ai_worker.core.errors import ConsumerPersistenceError
from ai_worker.core.registry import HandlerRegistry
from ai_worker.core.results import HandlerSuccess
from ai_worker.schemas.messages import JobType, WorkerMessage


def build_message() -> WorkerMessage:
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

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        return HandlerSuccess(
            event_id=message.event_id,
            job_id=message.job_id,
            handler_type=self.handler_type,
        )


class FakeResultStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def save(
        self,
        *,
        message: WorkerMessage,
        result: HandlerSuccess,
    ) -> None:
        self.events.append("save")


class FakeTransaction:
    def __init__(
        self,
        events: list[str],
        *,
        fail_commit: bool = False,
    ) -> None:
        self.events = events
        self.fail_commit = fail_commit

    async def commit(self) -> None:
        self.events.append("commit")

        if self.fail_commit:
            raise RuntimeError("synthetic commit failure")

    async def rollback(self) -> None:
        self.events.append("rollback")


class RecordingFakeStreamAdapter(FakeStreamAdapter):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    async def acknowledge(self, stream_message_id: str) -> None:
        self.events.append("ack")
        await super().acknowledge(stream_message_id)


def build_execution(
    *,
    adapter: RecordingFakeStreamAdapter,
    events: list[str],
    fail_commit: bool = False,
) -> ConsumerExecution:
    registry = HandlerRegistry()
    registry.register(FakeHandler())

    return ConsumerExecution(
        dispatcher=Dispatcher(registry),
        result_store=FakeResultStore(events),
        transaction=FakeTransaction(
            events,
            fail_commit=fail_commit,
        ),
        acknowledger=adapter,
    )


@pytest.mark.asyncio
async def test_fake_stream_one_cycle_acknowledges_after_commit() -> None:
    events: list[str] = []
    adapter = RecordingFakeStreamAdapter(events)
    execution = build_execution(
        adapter=adapter,
        events=events,
    )

    await adapter.ensure_consumer_group()
    message = build_message()
    await adapter.publish(message)
    deliveries = await adapter.read(consumer_name="worker-1")

    result = await execution.execute(deliveries[0])

    assert result.job_id == message.job_id
    assert events == ["save", "commit", "ack"]
    assert await adapter.list_pending() == ()


@pytest.mark.asyncio
async def test_commit_failure_keeps_message_pending_without_ack() -> None:
    events: list[str] = []
    adapter = RecordingFakeStreamAdapter(events)
    execution = build_execution(
        adapter=adapter,
        events=events,
        fail_commit=True,
    )

    await adapter.ensure_consumer_group()
    await adapter.publish(build_message())
    deliveries = await adapter.read(consumer_name="worker-1")

    with pytest.raises(ConsumerPersistenceError):
        await execution.execute(deliveries[0])

    assert events == ["save", "commit", "rollback"]

    pending = await adapter.list_pending()
    assert len(pending) == 1
    assert pending[0].delivery_count == 1
