"""승인된 Adapter·Repository·Handler를 실행 가능한 Consumer runtime으로 조립합니다.

이 모듈은 Worker core나 도메인 Handler를 재구현하지 않고 연결만 담당합니다(#233).
Pending reclaim·retry Outbox·DLQ 전이는 #142가 소유하므로 여기서 다루지 않습니다.
"""

import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ai_worker.adapters.clova_ocr_provider import ClovaOcrProviderAdapter
from ai_worker.adapters.factory import create_redis_client, create_stream_adapter
from ai_worker.adapters.redis_dead_letter_stream import (
    RedisDeadLetterStreamPublisher,
)
from ai_worker.adapters.redis_stream import RedisStreamAdapter
from ai_worker.adapters.sqlalchemy_dlq_outbox_repository import (
    SqlAlchemyDlqOutboxRepository,
)
from ai_worker.adapters.sqlalchemy_job_execution_repository import SqlAlchemyJobExecutionRepository
from ai_worker.adapters.sqlalchemy_lease_heartbeat import SqlAlchemyLeaseHeartbeat
from ai_worker.adapters.sqlalchemy_ocr_execution_starter import (
    SqlAlchemyOcrExecutionStarter,
)
from ai_worker.adapters.sqlalchemy_ocr_input_repository import SqlAlchemyOcrInputRepository
from ai_worker.adapters.sqlalchemy_ocr_result_store import SqlAlchemyOcrResultStore
from ai_worker.adapters.sqlalchemy_quarantine_repository import (
    SqlAlchemyQuarantineRepository,
)
from ai_worker.adapters.sqlalchemy_recovery_repository import (
    SqlAlchemyRecoveryRepository,
)
from ai_worker.adapters.sqlalchemy_transaction import SqlAlchemyTransaction
from ai_worker.core.config import Config
from ai_worker.core.consumer_execution import LeaseAwareConsumerExecution
from ai_worker.core.consumer_runtime import ConsumerRuntime
from ai_worker.core.dispatcher import Dispatcher
from ai_worker.core.dlq import DlqOutboxPublisher
from ai_worker.core.errors import WorkerError
from ai_worker.core.quarantine import (
    QuarantineExecution,
    RejectedWorkerDelivery,
)
from ai_worker.core.reconciler import PendingMessageReconciler
from ai_worker.core.recovery_observability import (
    ObservedDlqPublisher,
    ObservedPendingReconciler,
    RecoveryMetricLogger,
)
from ai_worker.core.recovery_scheduler import RecoveryScheduler
from ai_worker.core.registry import HandlerRegistry
from ai_worker.core.results import HandlerSuccess
from ai_worker.core.stream import StreamAcknowledger, WorkerDelivery
from ai_worker.schemas.messages import JobType, WorkerMessage
from ai_worker.tasks.ocr.handler import OcrHandler, OcrProvider
from provider_contracts.ocr import OcrEngine


class Clock(Protocol):
    def __call__(self) -> datetime:
        """timezone-aware 현재 시각을 반환합니다."""
        ...


class RoutingResultStore:
    """`handler_type`으로 도메인 ResultStore를 선택합니다.

    `LeaseAwareConsumerExecution`은 ResultStore 하나만 받으므로, 도메인이 늘어날 때
    실행 계층을 바꾸지 않고 여기서 분기합니다.
    """

    def __init__(self, stores: dict[JobType, "ResultStoreLike"]) -> None:
        self._stores = dict(stores)

    async def save(
        self,
        *,
        message: WorkerMessage,
        result: HandlerSuccess,
    ) -> None:
        store = self._stores.get(result.handler_type)

        if store is None:
            # Handler는 등록됐지만 결과 저장 경계가 없는 조립 오류입니다.
            # Provider 응답이나 결과 내용을 노출하지 않는 승인된 코드로만 실패합니다.
            raise WorkerError(failure_code="INTERNAL_ERROR")

        await store.save(message=message, result=result)


class ResultStoreLike(Protocol):
    async def save(
        self,
        *,
        message: WorkerMessage,
        result: HandlerSuccess,
    ) -> None: ...


def create_worker_engine(config: Config) -> AsyncEngine:
    """Worker 전용 AsyncEngine을 생성합니다."""

    return create_async_engine(
        config.database_url,
        echo=config.SQLALCHEMY_ECHO,
        pool_size=config.DB_CONNECTION_POOL_MAXSIZE,
        connect_args={"timeout": config.DB_CONNECT_TIMEOUT},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """delivery마다 새 session을 만드는 factory를 반환합니다."""

    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def create_ocr_provider(engine: OcrEngine) -> OcrProvider:
    """승인된 OCR engine을 Worker Provider Adapter로 감쌉니다."""

    return ClovaOcrProviderAdapter(engine)


class SessionScopedDeliveryExecution:
    """delivery 하나를 자체 session 안에서 처리하고 실패를 그 경계에 격리합니다.

    한 메시지의 실패가 Consumer process를 종료시키지 않아야 하므로(#233 장애 경계),
    승인된 `WorkerError`와 예상치 못한 예외 모두 이 경계에서 흡수합니다. 메시지 원문,
    OCR 원문, Provider 응답, 내부 예외 문구는 로그에 남기지 않고 `failure_code`와
    Stream message id만 남깁니다.
    """

    def __init__(
        self,
        *,
        config: Config,
        session_factory: async_sessionmaker[AsyncSession],
        acknowledger: StreamAcknowledger,
        clock: Clock,
        logger: logging.Logger,
        ocr_provider: OcrProvider | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._session_factory = session_factory
        self._acknowledger = acknowledger
        self._clock = clock
        self._logger = logger
        self._ocr_provider = ocr_provider
        self._monotonic_clock = monotonic_clock
        self._heartbeat = SqlAlchemyLeaseHeartbeat(
            session_factory=session_factory,
            lease_duration=config.lease_duration,
            heartbeat_interval=config.heartbeat_interval,
            clock=clock,
        )

    @property
    def registered_types(self) -> frozenset[JobType]:
        """조립된 Handler 종류입니다. 등록되지 않은 종류는 Provider를 호출하지 않습니다."""

        if self._ocr_provider is None:
            return frozenset()

        return frozenset({JobType.OCR})

    async def execute(self, delivery: WorkerDelivery) -> object:
        async with self._session_factory() as session:
            execution = self._build_execution(session)

            try:
                return await execution.execute(delivery)
            except WorkerError as error:
                self._logger.warning(
                    "worker delivery failed",
                    extra={
                        "stream_message_id": delivery.stream_message_id,
                        "failure_code": error.failure_code,
                    },
                )
                return None
            except Exception:
                # 예외 문구·traceback을 남기지 않습니다. Provider 응답이나 메시지 원문이
                # 섞여 들어갈 수 있기 때문입니다.
                self._logger.error(
                    "worker delivery failed with an unexpected error",
                    extra={
                        "stream_message_id": delivery.stream_message_id,
                        "failure_code": "INTERNAL_ERROR",
                    },
                )
                return None

    def _build_execution(self, session: AsyncSession) -> LeaseAwareConsumerExecution:
        registry = self._build_registry(session=session)
        stores: dict[JobType, ResultStoreLike] = {}
        execution_starter = None

        if JobType.OCR in registry.registered_types:
            stores[JobType.OCR] = SqlAlchemyOcrResultStore(
                session,
                clock=self._clock,
            )
            execution_starter = SqlAlchemyOcrExecutionStarter(session)

        keyword_arguments = {
            "dispatcher": Dispatcher(registry),
            "result_store": RoutingResultStore(stores),
            "transaction": SqlAlchemyTransaction(session),
            "acknowledger": self._acknowledger,
            "job_repository": SqlAlchemyJobExecutionRepository(session),
            "heartbeat": self._heartbeat,
            "lease_duration": self._config.lease_duration,
            "clock": self._clock,
            "hard_timeout_seconds": self._config.WORKER_HARD_TIMEOUT_SECONDS,
            "execution_starter": execution_starter,
        }

        if self._monotonic_clock is not None:
            keyword_arguments["monotonic_clock"] = self._monotonic_clock

        return LeaseAwareConsumerExecution(**keyword_arguments)  # type: ignore[arg-type]

    def _build_registry(self, *, session: AsyncSession | None) -> HandlerRegistry:
        registry = HandlerRegistry()

        if self._ocr_provider is None:
            # OCR engine이 조립되지 않은 배포에서는 OCR Handler를 등록하지 않습니다.
            # 등록되지 않은 job_type은 Dispatcher가 Provider 호출 없이 승인된 실패로
            # 처리합니다(HandlerNotRegisteredError -> INTERNAL_ERROR).
            return registry

        if session is not None:
            registry.register(
                OcrHandler(
                    input_repository=SqlAlchemyOcrInputRepository(session),
                    provider=self._ocr_provider,
                    clock=self._monotonic_clock if self._monotonic_clock is not None else _default_monotonic,
                    provider_budget_seconds=self._config.OCR_PROVIDER_BUDGET_SECONDS,
                    completion_budget_seconds=self._config.OCR_RESPONSE_MARGIN_SECONDS,
                )
            )

        return registry


class SessionScopedRejectedDeliveryExecution:
    """거부된 delivery마다 독립된 DB transaction으로 격리합니다."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        acknowledger: StreamAcknowledger,
        clock: Clock,
        logger: logging.Logger,
    ) -> None:
        self._session_factory = session_factory
        self._acknowledger = acknowledger
        self._clock = clock
        self._logger = logger

    async def execute(
        self,
        delivery: RejectedWorkerDelivery,
    ) -> object:
        async with self._session_factory() as session:
            execution = self._build_execution(session)

            try:
                request = delivery.to_quarantine_request(
                    received_at=self._clock(),
                )
                return await execution.execute(request)
            except Exception:
                # Redis 원문이나 예외 메시지는 로그에 포함하지 않습니다.
                self._logger.error(
                    "rejected worker delivery quarantine failed",
                    extra={
                        "stream_message_id": delivery.stream_entry_id,
                        "failure_code": delivery.failure_code.value,
                    },
                )
                return None

    def _build_execution(
        self,
        session: AsyncSession,
    ) -> QuarantineExecution:
        return QuarantineExecution(
            repository=SqlAlchemyQuarantineRepository(session),
            transaction=SqlAlchemyTransaction(session),
            acknowledger=self._acknowledger,
        )


def _default_monotonic() -> float:
    return time.monotonic()


@dataclass(frozen=True, slots=True)
class AssembledRecoveryScheduler:
    scheduler: RecoveryScheduler
    aclose: Callable[[], Awaitable[None]]


def build_recovery_scheduler(
    config: Config,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    stream: RedisStreamAdapter,
    execution: SessionScopedDeliveryExecution,
    clock: Clock,
    logger: logging.Logger,
) -> AssembledRecoveryScheduler:
    """Pending 복구와 DLQ 발행 Scheduler를 실제 Adapter로 조립합니다."""

    # 두 주기 작업은 동시에 실행되므로 AsyncSession을 공유하지 않습니다.
    recovery_session = session_factory()
    dlq_session = session_factory()

    metrics = RecoveryMetricLogger(logger)

    reconciler = PendingMessageReconciler(
        repository=SqlAlchemyRecoveryRepository(
            recovery_session,
        ),
        transaction=SqlAlchemyTransaction(
            recovery_session,
        ),
        stream=stream,
        executor=execution,
        consumer_name=config.RECONCILER_CONSUMER_NAME,
        min_idle_ms=config.RECONCILER_MIN_IDLE_MS,
        batch_size=config.RECONCILER_BATCH_SIZE,
        clock=clock,
        random_value=random.random,
    )

    dlq_publisher = DlqOutboxPublisher(
        repository=SqlAlchemyDlqOutboxRepository(
            dlq_session,
        ),
        transaction=SqlAlchemyTransaction(
            dlq_session,
        ),
        stream=RedisDeadLetterStreamPublisher(
            redis_client,
            stream_name=config.REDIS_DLQ_STREAM_NAME,
        ),
        alerter=metrics,
        claim_ttl=timedelta(
            seconds=config.DLQ_OUTBOX_CLAIM_TTL_SECONDS,
        ),
        clock=clock,
        random_value=random.random,
    )

    scheduler = RecoveryScheduler(
        reconciler=ObservedPendingReconciler(
            task=reconciler,
            metrics=metrics,
        ),
        dlq_publisher=ObservedDlqPublisher(
            task=dlq_publisher,
            metrics=metrics,
        ),
        failure_reporter=metrics,
        reconciler_interval_seconds=(config.RECONCILER_INTERVAL_SECONDS),
        dlq_publisher_interval_seconds=(config.DLQ_PUBLISHER_INTERVAL_SECONDS),
    )

    async def aclose() -> None:
        await recovery_session.close()
        await dlq_session.close()

    return AssembledRecoveryScheduler(
        scheduler=scheduler,
        aclose=aclose,
    )


@dataclass(frozen=True, slots=True)
class AssembledWorkerRuntime:
    """조립된 runtime과 정상 종료 절차입니다."""

    runtime: ConsumerRuntime
    registered_types: frozenset[JobType]
    aclose: Callable[[], Awaitable[None]]
    recovery_scheduler: RecoveryScheduler | None = None


def build_worker_runtime(
    config: Config,
    *,
    logger: logging.Logger,
    clock: Clock,
    ocr_engine: OcrEngine | None = None,
    redis_client: Redis | None = None,
    engine: AsyncEngine | None = None,
) -> AssembledWorkerRuntime:
    """설정으로 Redis·DB·Handler를 조립한 Consumer runtime을 만듭니다.

    `ocr_engine`이 없으면 OCR Handler를 등록하지 않습니다. 현재 저장소에는 Worker에서
    쓸 수 있는 승인된 CLOVA engine 구현이 없어, 실제 engine 조립은 별도 결정이
    필요합니다(리뷰 노트 참고).
    """

    owned_redis = redis_client is None
    owned_engine = engine is None

    resolved_redis = create_redis_client(config) if redis_client is None else redis_client
    resolved_engine = create_worker_engine(config) if engine is None else engine

    stream: RedisStreamAdapter = create_stream_adapter(config, client=resolved_redis)
    session_factory = create_session_factory(resolved_engine)

    execution = SessionScopedDeliveryExecution(
        config=config,
        session_factory=session_factory,
        acknowledger=stream,
        clock=clock,
        logger=logger,
        ocr_provider=None if ocr_engine is None else create_ocr_provider(ocr_engine),
    )
    rejected_execution = SessionScopedRejectedDeliveryExecution(
        session_factory=session_factory,
        acknowledger=stream,
        clock=clock,
        logger=logger,
    )
    assembled_recovery = build_recovery_scheduler(
        config,
        session_factory=session_factory,
        redis_client=resolved_redis,
        stream=stream,
        execution=execution,
        clock=clock,
        logger=logger,
    )

    runtime = ConsumerRuntime(
        stream=stream,
        execution=execution,
        rejected_execution=rejected_execution,
        consumer_name=config.REDIS_CONSUMER_NAME,
        batch_size=config.WORKER_CONCURRENCY,
        block_ms=config.REDIS_BLOCK_MS,
    )

    async def aclose() -> None:
        """Redis와 DB 연결을 정상 종료합니다. 소유하지 않은 자원은 닫지 않습니다."""
        await assembled_recovery.aclose()

        if owned_redis:
            await resolved_redis.aclose()

        if owned_engine:
            await resolved_engine.dispose()

    return AssembledWorkerRuntime(
        runtime=runtime,
        registered_types=execution.registered_types,
        aclose=aclose,
        recovery_scheduler=assembled_recovery.scheduler,
    )
