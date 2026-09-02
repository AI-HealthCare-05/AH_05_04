"""실제 SQLAlchemy session을 연결했을 때의 Consumer 실행 경계를 검증합니다.

단위 테스트(`ai_worker/tests/core/test_consumer_execution.py`)는 Fake 객체로
save·commit·ACK 호출 순서를 고정합니다. 이 통합 테스트는 그 계약이 실제
`AsyncSession`에서도 유지되는지, 특히 Handler·ResultStore·Transaction이
**같은 session과 transaction을 공유**해야만 원자성이 성립한다는 점을 고정합니다.

의료정보나 사용자 식별정보를 사용하지 않고 전용 probe 테이블만 사용합니다.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_worker.adapters.sqlalchemy_transaction import (
    SqlAlchemyTransaction,
)
from ai_worker.core.consumer_execution import ConsumerExecution
from ai_worker.core.dispatcher import Dispatcher
from ai_worker.core.errors import (
    ConsumerAcknowledgementError,
    ConsumerPersistenceError,
    HandlerExecutionError,
    HandlerResultMismatchError,
    WorkerError,
)
from ai_worker.core.registry import HandlerRegistry
from ai_worker.core.results import HandlerSuccess
from ai_worker.core.stream import WorkerDelivery
from ai_worker.schemas.messages import JobType, WorkerMessage
from app.core import config


PROBE_TABLE = "worker_consumer_session_probe"

TEST_DATABASE_URL = URL.create(
    drivername="postgresql+asyncpg",
    username=config.DB_USER,
    password=config.DB_PASSWORD,
    port=config.DB_EXPOSE_PORT,
    host="127.0.0.1",
    database="test",
)

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
)


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
            # Backend와 동일한 32자리 hex 형식을 사용하고,
            # 테스트마다 다른 값으로 probe 행을 분리합니다.
            "trace_id": uuid4().hex,
        }
    )


class RecordingSqlAlchemyTransaction(SqlAlchemyTransaction):
    """실제 transaction 어댑터의 commit·rollback 횟수를 기록합니다."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.session = session
        self.rollback_count = 0
        self.commit_count = 0

    async def commit(self) -> None:
        self.commit_count += 1
        await super().commit()

    async def rollback(self) -> None:
        self.rollback_count += 1
        await super().rollback()


class SqlAlchemyResultStore:
    """결과를 현재 session에 기록하고 직접 commit하지 않습니다."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        events: list[str] | None = None,
        failing: bool = False,
    ) -> None:
        self.session = session
        self._events = events
        self._failing = failing

    async def save(
        self,
        *,
        message: WorkerMessage,
        result: HandlerSuccess,
    ) -> None:
        if self._events is not None:
            self._events.append("save")

        if self._failing:
            # 실제 어댑터의 DB 오류를 대신하는 저장 실패입니다.
            raise RuntimeError("result store failure")

        await self.session.execute(
            text(f"INSERT INTO {PROBE_TABLE} (trace_id, writer) VALUES (:trace_id, 'result_store')"),
            {"trace_id": message.trace_id},
        )


class SessionWritingHandler:
    """Consumer와 같은 session에 변경을 남기는 Handler입니다."""

    handler_type = JobType.OCR

    def __init__(
        self,
        session: AsyncSession,
        *,
        mismatched: bool = False,
        error: BaseException | None = None,
    ) -> None:
        self.session = session
        self._mismatched = mismatched
        self._error = error

    async def handle(self, message: WorkerMessage) -> HandlerSuccess:
        # 오류·불일치 경로에서도 Handler가 이미 session에 쓴 뒤라는
        # 실제 상황을 만들기 위해 write를 먼저 수행합니다.
        await self.session.execute(
            text(f"INSERT INTO {PROBE_TABLE} (trace_id, writer) VALUES (:trace_id, 'handler')"),
            {"trace_id": message.trace_id},
        )

        if self._error is not None:
            raise self._error

        return HandlerSuccess(
            event_id=message.event_id,
            job_id=uuid4() if self._mismatched else message.job_id,
            handler_type=self.handler_type,
        )


class RecordingAcknowledger:
    """ACK 호출 시점과 실패를 관찰합니다."""

    def __init__(
        self,
        *,
        events: list[str] | None = None,
        failing: bool = False,
    ) -> None:
        self.acknowledged: list[str] = []
        self._events = events
        self._failing = failing

    async def acknowledge(self, stream_message_id: str) -> None:
        if self._events is not None:
            self._events.append("ack")

        if self._failing:
            raise RuntimeError("stream acknowledgement failure")

        self.acknowledged.append(stream_message_id)


def build_execution(
    *,
    handler: SessionWritingHandler,
    result_store: SqlAlchemyResultStore,
    transaction: RecordingSqlAlchemyTransaction,
    acknowledger: RecordingAcknowledger,
) -> ConsumerExecution:
    """Handler를 등록한 Dispatcher로 Consumer 실행 경계를 조립합니다."""

    registry = HandlerRegistry()
    registry.register(handler)

    return ConsumerExecution(
        dispatcher=Dispatcher(registry),
        result_store=result_store,
        transaction=transaction,
        acknowledger=acknowledger,
    )


@pytest_asyncio.fixture(scope="session", autouse=True)
async def probe_table() -> AsyncIterator[None]:
    """테스트 전용 probe 테이블만 생성하고 정리합니다."""

    async with test_engine.begin() as connection:
        await connection.execute(text(f"DROP TABLE IF EXISTS {PROBE_TABLE}"))
        await connection.execute(
            text(
                f"CREATE TABLE {PROBE_TABLE} ( id BIGSERIAL PRIMARY KEY, trace_id TEXT NOT NULL, writer TEXT NOT NULL)"
            )
        )

    yield

    async with test_engine.begin() as connection:
        await connection.execute(text(f"DROP TABLE IF EXISTS {PROBE_TABLE}"))

    await test_engine.dispose()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Consumer가 소유하는 실제 session입니다."""

    async with AsyncSession(bind=test_engine, expire_on_commit=False) as session:
        yield session


@pytest_asyncio.fixture
async def other_session() -> AsyncIterator[AsyncSession]:
    """공유되지 않은 별도 session을 재현하기 위한 두 번째 session입니다."""

    async with AsyncSession(bind=test_engine, expire_on_commit=False) as session:
        yield session


async def committed_writers(trace_id: str) -> list[str]:
    """Consumer session 밖에서 실제로 commit된 행만 조회합니다."""

    async with AsyncSession(bind=test_engine, expire_on_commit=False) as reader:
        result = await reader.execute(
            text(f"SELECT writer FROM {PROBE_TABLE} WHERE trace_id = :trace_id ORDER BY writer"),
            {"trace_id": trace_id},
        )

        return [row[0] for row in result.fetchall()]


async def test_handler_and_result_store_commit_in_one_transaction(
    session: AsyncSession,
) -> None:
    """같은 session을 공유하면 Handler와 ResultStore 변경이 함께 commit됩니다."""

    message = build_message()
    delivery = WorkerDelivery(stream_message_id="1-0", message=message)

    handler = SessionWritingHandler(session)
    result_store = SqlAlchemyResultStore(session)
    transaction = RecordingSqlAlchemyTransaction(session)
    acknowledger = RecordingAcknowledger()

    # 실제 어댑터 조립 시 세 협력자가 같은 session을 참조해야 합니다.
    assert handler.session is result_store.session is transaction.session

    execution = build_execution(
        handler=handler,
        result_store=result_store,
        transaction=transaction,
        acknowledger=acknowledger,
    )

    result = await execution.execute(delivery)

    assert result.job_id == message.job_id
    assert acknowledger.acknowledged == ["1-0"]
    assert await committed_writers(message.trace_id) == ["handler", "result_store"]


async def test_execution_order_is_save_commit_then_acknowledge(
    session: AsyncSession,
) -> None:
    """정상 경로에서 save → commit → ACK 순서를 유지합니다."""

    message = build_message()
    delivery = WorkerDelivery(stream_message_id="2-0", message=message)
    events: list[str] = []

    class RecordingTransaction(RecordingSqlAlchemyTransaction):
        async def commit(self) -> None:
            events.append("commit")
            await super().commit()

    handler = SessionWritingHandler(session)
    result_store = SqlAlchemyResultStore(session, events=events)
    transaction = RecordingTransaction(session)
    acknowledger = RecordingAcknowledger(events=events)

    execution = build_execution(
        handler=handler,
        result_store=result_store,
        transaction=transaction,
        acknowledger=acknowledger,
    )

    await execution.execute(delivery)

    assert events == ["save", "commit", "ack"]


@pytest.mark.parametrize(
    ("failure_mode", "expected_error"),
    [
        ("result_mismatch", HandlerResultMismatchError),
        ("handler_exception", HandlerExecutionError),
        ("handler_cancellation", asyncio.CancelledError),
        ("result_store_failure", ConsumerPersistenceError),
    ],
)
async def test_failed_delivery_does_not_leak_into_next_message(
    session: AsyncSession,
    failure_mode: str,
    expected_error: type[BaseException],
) -> None:
    """실패한 메시지의 Handler 변경이 다음 메시지 commit에 섞이지 않습니다.

    session은 다음 메시지 처리에 그대로 재사용되므로, rollback이 없으면
    실패한 메시지의 pending write가 다음 메시지의 commit에 함께 반영됩니다.
    실패 직후 조회만으로는 session을 닫을 때의 암묵적 정리와 구분되지 않아
    같은 session에서 후속 메시지를 실제로 commit해 확인합니다.
    """

    failed_message = build_message()
    failed_delivery = WorkerDelivery(
        stream_message_id="10-0",
        message=failed_message,
    )
    transaction = RecordingSqlAlchemyTransaction(session)
    acknowledger = RecordingAcknowledger()

    handler = SessionWritingHandler(
        session,
        mismatched=failure_mode == "result_mismatch",
        error={
            "handler_exception": RuntimeError("provider raw failure"),
            "handler_cancellation": asyncio.CancelledError(),
        }.get(failure_mode),
    )

    failing_execution = build_execution(
        handler=handler,
        result_store=SqlAlchemyResultStore(
            session,
            failing=failure_mode == "result_store_failure",
        ),
        transaction=transaction,
        acknowledger=acknowledger,
    )

    with pytest.raises(expected_error):
        await failing_execution.execute(failed_delivery)

    assert transaction.rollback_count == 1
    assert transaction.commit_count == 0
    assert acknowledger.acknowledged == []

    # 같은 session으로 다음 메시지를 정상 처리해 commit을 발생시킵니다.
    next_message = build_message()
    next_delivery = WorkerDelivery(
        stream_message_id="10-1",
        message=next_message,
    )

    healthy_execution = build_execution(
        handler=SessionWritingHandler(session),
        result_store=SqlAlchemyResultStore(session),
        transaction=transaction,
        acknowledger=acknowledger,
    )

    await healthy_execution.execute(next_delivery)

    assert transaction.commit_count == 1
    assert acknowledger.acknowledged == ["10-1"]
    # 실패한 메시지의 Handler write가 이 commit에 섞이지 않아야 합니다.
    assert await committed_writers(failed_message.trace_id) == []
    assert await committed_writers(next_message.trace_id) == [
        "handler",
        "result_store",
    ]


@pytest.mark.parametrize(
    ("failing_store", "handler_error", "expected_error"),
    [
        (False, RuntimeError("provider raw failure"), HandlerExecutionError),
        (True, None, ConsumerPersistenceError),
    ],
)
async def test_failure_does_not_expose_original_exception_chain(
    session: AsyncSession,
    failing_store: bool,
    handler_error: BaseException | None,
    expected_error: type[WorkerError],
) -> None:
    """Handler·저장 실패의 원본 예외가 chain으로 노출되지 않습니다."""

    delivery = WorkerDelivery(stream_message_id="11-0", message=build_message())

    execution = build_execution(
        handler=SessionWritingHandler(session, error=handler_error),
        result_store=SqlAlchemyResultStore(session, failing=failing_store),
        transaction=RecordingSqlAlchemyTransaction(session),
        acknowledger=RecordingAcknowledger(),
    )

    with pytest.raises(expected_error) as exc_info:
        await execution.execute(delivery)

    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


async def test_acknowledgement_failure_keeps_committed_rows(
    session: AsyncSession,
) -> None:
    """commit 이후 ACK 실패는 rollback하지 않고 결과를 보존합니다."""

    message = build_message()
    delivery = WorkerDelivery(stream_message_id="7-0", message=message)
    transaction = RecordingSqlAlchemyTransaction(session)

    execution = build_execution(
        handler=SessionWritingHandler(session),
        result_store=SqlAlchemyResultStore(session),
        transaction=transaction,
        acknowledger=RecordingAcknowledger(failing=True),
    )

    with pytest.raises(ConsumerAcknowledgementError) as exc_info:
        await execution.execute(delivery)

    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    # commit 이후 rollback은 실제 session에서 no-op이므로 행 존재만으로는
    # 계약을 고정할 수 없습니다. rollback을 호출하지 않는 것 자체를 검증합니다.
    assert transaction.commit_count == 1
    assert transaction.rollback_count == 0
    # at-least-once 재전달로 처리해야 하며 commit된 결과는 남아 있어야 합니다.
    assert await committed_writers(message.trace_id) == ["handler", "result_store"]


async def test_separate_sessions_break_atomicity(
    session: AsyncSession,
    other_session: AsyncSession,
) -> None:
    """session을 공유하지 않으면 Handler 변경이 함께 commit되지 않습니다.

    실제 어댑터를 조립할 때 Handler에 다른 session을 주입하면 어떤 계약이
    깨지는지 고정하는 음성 대조 테스트입니다.
    """

    message = build_message()
    delivery = WorkerDelivery(stream_message_id="8-0", message=message)

    # Handler만 다른 session을 사용하고 ResultStore·Transaction은 공유합니다.
    handler = SessionWritingHandler(other_session)
    result_store = SqlAlchemyResultStore(session)
    transaction = RecordingSqlAlchemyTransaction(session)

    assert handler.session is not transaction.session

    execution = build_execution(
        handler=handler,
        result_store=result_store,
        transaction=transaction,
        acknowledger=RecordingAcknowledger(),
    )

    await execution.execute(delivery)

    try:
        # Consumer의 commit은 자신의 session만 commit하므로
        # Handler 변경은 commit되지 않은 채 남습니다.
        assert await committed_writers(message.trace_id) == ["result_store"]
    finally:
        await other_session.rollback()

    assert await committed_writers(message.trace_id) == ["result_store"]
