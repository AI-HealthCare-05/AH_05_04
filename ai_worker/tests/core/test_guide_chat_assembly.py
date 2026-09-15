"""#577: 실제 assembly와 Consumer를 합성 의존성으로 연결합니다."""

import logging
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.core import runtime_assembly as assembly
from ai_worker.core.errors import ConsumerPersistenceError
from ai_worker.core.handler import HandlerExecutionContext
from ai_worker.core.results import HandlerSuccess
from ai_worker.core.stream import WorkerDelivery
from ai_worker.schemas.messages import JobType, WorkerMessage
from ai_worker.tests.core.test_consumer_execution import (
    FakeAcknowledger,
    FakeJobExecutionRepository,
    FakeLeaseHeartbeat,
    FakeResultStore,
    FakeTransaction,
    build_message,
)
from ai_worker.tests.core.test_runtime_assembly import _config


class SyntheticHandler:
    def __init__(self, kind, events):
        self.handler_type = kind
        self.events = events

    async def handle(self, message, *, context: HandlerExecutionContext | None = None):
        assert context is not None
        self.events.append("handle")
        return HandlerSuccess(message.event_id, message.job_id, self.handler_type)


class StagedStore(FakeResultStore):
    def __init__(self, events, *, fail_save=False):
        super().__init__(events, fail_save=fail_save)
        self.pending = []
        self.persisted = []

    async def save(self, *, message, result):
        self.pending.append(result)
        await super().save(message=message, result=result)


class StagedTransaction(FakeTransaction):
    def __init__(self, events, store, fail_result_commit):
        super().__init__(events)
        self.store = store
        self.fail_result_commit = fail_result_commit

    async def commit(self):
        await super().commit()
        if self.store.pending and self.fail_result_commit:
            raise RuntimeError("synthetic commit failure")
        self.store.persisted.extend(self.store.pending)
        self.store.pending.clear()

    async def rollback(self):
        self.store.pending.clear()
        await super().rollback()


def assemble(monkeypatch, kind, *, fail_save=False, fail_commit=False, invalid=None):
    events = []
    session = cast(AsyncSession, MagicMock(spec=AsyncSession))
    store = StagedStore(events, fail_save=fail_save)
    transaction = StagedTransaction(events, store, fail_commit)
    handler = SyntheticHandler(kind, events)

    def factory(received):
        assert received is session
        return (None if invalid == "handler" else handler, None if invalid == "store" else store)

    monkeypatch.setattr(assembly, "SqlAlchemyTransaction", lambda received: transaction)
    monkeypatch.setattr(
        assembly,
        "SqlAlchemyJobExecutionRepository",
        lambda received: FakeJobExecutionRepository(events, complete_successfully=True),
    )
    monkeypatch.setattr(assembly, "SqlAlchemyLeaseHeartbeat", lambda **kwargs: FakeLeaseHeartbeat(events))
    execution = assembly.SessionScopedDeliveryExecution(
        config=_config(),
        session_factory=MagicMock(),
        acknowledger=FakeAcknowledger(events),
        clock=lambda: datetime.now(UTC),
        logger=logging.getLogger(__name__),
        guide_chat_factories={kind: factory},
    )
    return execution, session, store, events


@pytest.mark.parametrize("kind", [JobType.GUIDE, JobType.CHAT])
@pytest.mark.parametrize("failure", [None, "save", "commit"])
@pytest.mark.parametrize("with_ocr", [False, True])
@pytest.mark.asyncio
async def test_delivery_save_commit_ack(monkeypatch, kind, failure, with_ocr):
    execution, session, store, events = assemble(
        monkeypatch, kind, fail_save=failure == "save", fail_commit=failure == "commit"
    )
    if with_ocr:
        execution._ocr_provider = MagicMock()
    assert execution.registered_types == frozenset({kind} | ({JobType.OCR} if with_ocr else set()))
    consumer = execution._build_execution(session, job_type=kind)
    message = WorkerMessage.model_validate(
        build_message().model_dump()
        | {"job_type": kind, "domain_type": "GUIDE" if kind == JobType.GUIDE else "CHAT_MESSAGE"}
    )
    delivery = WorkerDelivery(stream_message_id="577-0", message=message)
    if failure:
        with pytest.raises(ConsumerPersistenceError):
            await consumer.execute(delivery)
        assert "rollback" in events
        assert "ack" not in events
        assert not store.pending and not store.persisted
    else:
        await consumer.execute(delivery)
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
        assert len(store.persisted) == 1


@pytest.mark.parametrize("kind", [JobType.GUIDE, JobType.CHAT])
@pytest.mark.parametrize("missing", ["handler", "store"])
def test_incomplete_binding_is_not_registered(monkeypatch, kind, missing):
    """불완전한 binding은 예외를 올리지 않고 등록만 건너뜁니다.

    예외를 delivery 밖으로 올리면 실패 기록도 ACK도 없이 reclaim이 반복됩니다.
    등록되지 않은 job_type은 Dispatcher가 승인된 실패로 처리합니다.
    """

    execution, session, store, events = assemble(monkeypatch, kind, invalid=missing)
    consumer = execution._build_execution(session, job_type=kind)
    assert kind not in consumer._dispatcher._registry.registered_types
    assert not events
    assert not store.persisted


def test_wrong_handler_kind_is_not_registered(monkeypatch):
    execution, session, _, _ = assemble(monkeypatch, JobType.GUIDE)
    execution._guide_chat_factories[JobType.CHAT] = execution._guide_chat_factories.pop(JobType.GUIDE)
    consumer = execution._build_execution(session, job_type=JobType.CHAT)
    assert JobType.CHAT not in consumer._dispatcher._registry.registered_types


def test_both_factories_and_ocr_registration(monkeypatch):
    execution, session, _, _ = assemble(monkeypatch, JobType.GUIDE)
    execution._guide_chat_factories[JobType.CHAT] = lambda s: (SyntheticHandler(JobType.CHAT, []), FakeResultStore([]))
    execution._ocr_provider = MagicMock()
    assert execution.registered_types == frozenset({JobType.OCR, JobType.GUIDE, JobType.CHAT})

    # delivery마다 해당 종류만 조립하므로 OCR과 그 delivery의 종류만 등록됩니다.
    for kind in (JobType.GUIDE, JobType.CHAT):
        consumer = execution._build_execution(session, job_type=kind)
        assert consumer._dispatcher._registry.registered_types == frozenset({JobType.OCR, kind})


@pytest.mark.parametrize(
    ("kind", "expected_lease_seconds", "expected_hard_timeout_seconds"),
    [
        (JobType.OCR, 75.0, 60.0),
        (JobType.GUIDE, 75.0, 60.0),
        (JobType.CHAT, 60.0, 45.0),
    ],
)
def test_execution_limits_follow_async_job_contract(
    monkeypatch,
    kind,
    expected_lease_seconds,
    expected_hard_timeout_seconds,
):
    """PD-91-20260831: OCR·GUIDE는 60초/75초, CHAT은 45초/60초로 고정합니다."""

    execution, session, _, _ = assemble(monkeypatch, JobType.GUIDE)
    execution._guide_chat_factories[JobType.CHAT] = lambda s: (SyntheticHandler(JobType.CHAT, []), FakeResultStore([]))
    execution._ocr_provider = MagicMock()

    consumer = execution._build_execution(session, job_type=kind)

    assert consumer._lease_duration == timedelta(seconds=expected_lease_seconds)
    assert consumer._hard_timeout_seconds == expected_hard_timeout_seconds


def test_broken_factory_does_not_block_other_kinds(monkeypatch):
    """한 종류의 factory 예외가 다른 종류의 조립을 막지 않습니다."""

    def broken(received):
        raise RuntimeError("synthetic factory failure")

    execution, session, _, _ = assemble(monkeypatch, JobType.GUIDE)
    execution._guide_chat_factories[JobType.CHAT] = broken
    execution._ocr_provider = MagicMock()

    chat_consumer = execution._build_execution(session, job_type=JobType.CHAT)
    assert chat_consumer._dispatcher._registry.registered_types == frozenset({JobType.OCR})

    guide_consumer = execution._build_execution(session, job_type=JobType.GUIDE)
    assert guide_consumer._dispatcher._registry.registered_types == frozenset({JobType.OCR, JobType.GUIDE})

    ocr_consumer = execution._build_execution(session, job_type=JobType.OCR)
    assert ocr_consumer._dispatcher._registry.registered_types == frozenset({JobType.OCR})


@pytest.mark.asyncio
async def test_public_builder_forwards_factories_without_ocr():
    from ai_worker.tests.core.test_runtime_assembly import _engine_stub, _RedisStub

    engine = _engine_stub()
    factories = {JobType.GUIDE: lambda session: (SyntheticHandler(JobType.GUIDE, []), FakeResultStore([]))}
    runtime = assembly.build_worker_runtime(
        _config(),
        logger=logging.getLogger(__name__),
        clock=lambda: datetime.now(UTC),
        guide_chat_factories=factories,
        engine=engine,
        redis_client=_RedisStub(),
    )
    try:
        factories.clear()
        assert runtime.registered_types == frozenset({JobType.GUIDE})
    finally:
        await runtime.aclose()
        await engine.dispose()


def test_factories_are_delivery_scoped(monkeypatch):
    execution, session, _, _ = assemble(monkeypatch, JobType.GUIDE)
    calls = []

    def factory(received):
        binding = (SyntheticHandler(JobType.GUIDE, []), FakeResultStore([]))
        calls.append((received, binding))
        return binding

    execution._guide_chat_factories[JobType.GUIDE] = factory
    other_session = cast(AsyncSession, MagicMock(spec=AsyncSession))
    execution._build_execution(session, job_type=JobType.GUIDE)
    execution._build_execution(other_session, job_type=JobType.GUIDE)
    assert calls[0][0] is session and calls[1][0] is other_session
    assert calls[0][1][0] is not calls[1][1][0]
    assert calls[0][1][1] is not calls[1][1][1]


class _SessionFactoryStub:
    """`async with self._session_factory() as session` 경계를 흉내냅니다."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_):
        return False


@pytest.mark.parametrize("failure", ["raises", "invalid"])
@pytest.mark.asyncio
async def test_public_execute_records_failure_and_acks_for_broken_factory(monkeypatch, failure):
    """조립 실패도 Job 실패 경계 안에서 끝나야 합니다.

    public execute()가 예외를 삼키고 None으로 끝나면 실패 기록도 ACK도 없이
    reclaim이 반복됩니다. 등록되지 않은 종류로 떨어뜨려 기존 실패 정책을 태웁니다.
    """

    execution, session, _, events = assemble(monkeypatch, JobType.GUIDE)
    repository = FakeJobExecutionRepository(events, complete_successfully=True)
    monkeypatch.setattr(assembly, "SqlAlchemyJobExecutionRepository", lambda received: repository)

    def broken(received):
        raise RuntimeError("synthetic factory failure")

    execution._guide_chat_factories[JobType.GUIDE] = broken if failure == "raises" else (lambda received: (None, None))
    execution._session_factory = _SessionFactoryStub(session)

    message = WorkerMessage.model_validate(
        build_message().model_dump() | {"job_type": JobType.GUIDE, "domain_type": "GUIDE"}
    )
    await execution.execute(WorkerDelivery(stream_message_id="577-1", message=message))

    assert repository.recorded_failure is not None
    assert repository.recorded_failure[0] == "INTERNAL_ERROR"
    assert "ack" in events
