"""Worker Consumer runtime loop 테스트입니다."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_worker.core.consumer_runtime import ConsumerRuntime
from ai_worker.core.stream import WorkerDelivery
from ai_worker.schemas.messages import WorkerMessage


def build_delivery() -> WorkerDelivery:
    now = datetime.now(UTC)
    message = WorkerMessage.model_validate(
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

    return WorkerDelivery(
        stream_message_id="1-0",
        message=message,
    )


class FakeStreamConsumer:
    def __init__(
        self,
        deliveries: tuple[WorkerDelivery, ...] = (),
    ) -> None:
        self.deliveries = deliveries
        self.events: list[str] = []
        self.read_arguments: list[tuple[str, int, int]] = []

    async def ensure_consumer_group(self) -> None:
        self.events.append("ensure_consumer_group")

    async def read(
        self,
        *,
        consumer_name: str,
        count: int = 1,
        block_ms: int = 5000,
    ) -> tuple[WorkerDelivery, ...]:
        self.events.append("read")
        self.read_arguments.append(
            (
                consumer_name,
                count,
                block_ms,
            )
        )
        return self.deliveries


class FakeExecution:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.deliveries: list[WorkerDelivery] = []

    async def execute(self, delivery: WorkerDelivery) -> None:
        self.events.append("execute")
        self.deliveries.append(delivery)


@pytest.mark.asyncio
async def test_runtime_initializes_consumer_group_before_read() -> None:
    stream = FakeStreamConsumer()
    execution = FakeExecution(stream.events)
    runtime = ConsumerRuntime(
        stream=stream,
        execution=execution,
        consumer_name="worker-1",
        batch_size=1,
        block_ms=5000,
    )

    await runtime.initialize()
    processed_count = await runtime.run_once()

    assert processed_count == 0
    assert stream.events == [
        "ensure_consumer_group",
        "read",
    ]


@pytest.mark.asyncio
async def test_runtime_reads_with_configured_consumer_settings() -> None:
    delivery = build_delivery()
    stream = FakeStreamConsumer((delivery,))
    execution = FakeExecution(stream.events)
    runtime = ConsumerRuntime(
        stream=stream,
        execution=execution,
        consumer_name="worker-ocr-1",
        batch_size=10,
        block_ms=3000,
    )

    await runtime.initialize()
    processed_count = await runtime.run_once()

    assert processed_count == 1
    assert stream.read_arguments == [
        (
            "worker-ocr-1",
            10,
            3000,
        )
    ]
    assert execution.deliveries == [delivery]


@pytest.mark.asyncio
async def test_runtime_processes_every_delivery_in_batch() -> None:
    first = build_delivery()
    second = WorkerDelivery(
        stream_message_id="2-0",
        message=build_delivery().message,
    )
    stream = FakeStreamConsumer((first, second))
    execution = FakeExecution(stream.events)
    runtime = ConsumerRuntime(
        stream=stream,
        execution=execution,
        consumer_name="worker-1",
        batch_size=2,
        block_ms=1000,
    )

    await runtime.initialize()
    processed_count = await runtime.run_once()

    assert processed_count == 2
    assert execution.deliveries == [
        first,
        second,
    ]
    assert stream.events == [
        "ensure_consumer_group",
        "read",
        "execute",
        "execute",
    ]


class StoppingExecution(FakeExecution):
    def __init__(
        self,
        events: list[str],
        stop_event: asyncio.Event,
    ) -> None:
        super().__init__(events)
        self._stop_event = stop_event

    async def execute(self, delivery: WorkerDelivery) -> None:
        await super().execute(delivery)
        self._stop_event.set()


@pytest.mark.asyncio
async def test_runtime_repeats_until_stop_is_requested() -> None:
    delivery = build_delivery()
    stream = FakeStreamConsumer((delivery,))
    stop_event = asyncio.Event()
    execution = StoppingExecution(
        stream.events,
        stop_event,
    )
    runtime = ConsumerRuntime(
        stream=stream,
        execution=execution,
        consumer_name="worker-1",
        batch_size=1,
        block_ms=1000,
    )

    await runtime.run(stop_event)

    assert execution.deliveries == [delivery]
    assert stream.events == [
        "ensure_consumer_group",
        "read",
        "execute",
    ]


@pytest.mark.asyncio
async def test_runtime_does_not_read_when_stop_was_already_requested() -> None:
    stream = FakeStreamConsumer()
    execution = FakeExecution(stream.events)
    stop_event = asyncio.Event()
    stop_event.set()
    runtime = ConsumerRuntime(
        stream=stream,
        execution=execution,
        consumer_name="worker-1",
    )

    await runtime.run(stop_event)

    assert stream.events == [
        "ensure_consumer_group",
    ]


class BlockingExecution:
    """두 delivery가 모두 시작될 때까지 실행 완료를 막습니다."""

    def __init__(self) -> None:
        self.started_count = 0
        self.all_started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, delivery: WorkerDelivery) -> None:
        _ = delivery
        self.started_count += 1

        if self.started_count == 2:
            self.all_started.set()

        await self.release.wait()


@pytest.mark.asyncio
async def test_runtime_processes_batch_with_configured_concurrency() -> None:
    first = build_delivery()
    second = WorkerDelivery(
        stream_message_id="2-0",
        message=build_delivery().message,
    )
    stream = FakeStreamConsumer((first, second))
    execution = BlockingExecution()
    runtime = ConsumerRuntime(
        stream=stream,
        execution=execution,
        consumer_name="worker-1",
        batch_size=2,
        block_ms=1000,
    )

    run_task = asyncio.create_task(runtime.run_once())

    try:
        await asyncio.wait_for(
            execution.all_started.wait(),
            timeout=0.1,
        )
    finally:
        execution.release.set()

    processed_count = await run_task

    assert processed_count == 2
    assert execution.started_count == 2
