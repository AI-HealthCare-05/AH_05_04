"""#577: 실제 assembly와 Consumer를 합성 의존성으로 연결합니다."""

import logging
from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.core import runtime_assembly as assembly
from ai_worker.core.errors import ConsumerPersistenceError, WorkerError
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
    consumer = execution._build_execution(session)
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
def test_incomplete_binding_fails_before_execution(monkeypatch, kind, missing):
    execution, session, store, events = assemble(monkeypatch, kind, invalid=missing)
    with pytest.raises(WorkerError):
        execution._build_execution(session)
    assert not events
    assert not store.persisted


def test_wrong_handler_kind_fails_closed(monkeypatch):
    execution, session, _, _ = assemble(monkeypatch, JobType.GUIDE)
    execution._guide_chat_factories[JobType.CHAT] = execution._guide_chat_factories.pop(JobType.GUIDE)
    with pytest.raises(WorkerError):
        execution._build_execution(session)


def test_both_factories_and_ocr_registration(monkeypatch):
    execution, session, _, _ = assemble(monkeypatch, JobType.GUIDE)
    execution._guide_chat_factories[JobType.CHAT] = lambda s: (SyntheticHandler(JobType.CHAT, []), FakeResultStore([]))
    execution._ocr_provider = MagicMock()
    assert execution.registered_types == frozenset({JobType.OCR, JobType.GUIDE, JobType.CHAT})
    consumer = execution._build_execution(session)
    assert consumer._dispatcher._registry.registered_types == execution.registered_types


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
    execution._build_execution(session)
    execution._build_execution(other_session)
    assert calls[0][0] is session and calls[1][0] is other_session
    assert calls[0][1][0] is not calls[1][1][0]
    assert calls[0][1][1] is not calls[1][1][1]
