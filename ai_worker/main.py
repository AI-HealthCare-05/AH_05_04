"""AI Worker Consumer runtime 진입점입니다(#233).

Redis Streams를 읽어 등록된 도메인 Handler로 전달하고, lease 소유권이 유지된 결과만
commit한 뒤 ACK합니다. 실행 경계 자체는 #141의 `LeaseAwareConsumerExecution`이 소유하고
이 모듈은 프로세스 수명주기(설정 검증, 조립, 종료 신호, 자원 정리)만 담당합니다.
"""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable
from datetime import datetime

from ai_worker.core import get_config, get_logger
from ai_worker.core.config import Config
from ai_worker.core.runtime_assembly import (
    AssembledWorkerRuntime,
    build_worker_runtime,
)
from provider_contracts.ocr import OcrEngine

_SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def _create_clock(config: Config) -> Callable[[], datetime]:
    """설정된 timezone 기준 현재 시각을 반환하는 clock을 만듭니다."""

    def clock() -> datetime:
        return datetime.now(config.TIMEZONE)

    return clock


def _install_shutdown_handlers(stop_event: asyncio.Event, logger: logging.Logger) -> None:
    """SIGTERM·SIGINT를 받으면 신규 read를 중단하도록 표시합니다."""

    loop = asyncio.get_running_loop()

    for shutdown_signal in _SHUTDOWN_SIGNALS:
        try:
            loop.add_signal_handler(shutdown_signal, stop_event.set)
        except NotImplementedError:
            # add_signal_handler를 지원하지 않는 플랫폼(Windows)에서는 기본 handler를 씁니다.
            signal.signal(shutdown_signal, lambda *_: stop_event.set())

    logger.info("worker shutdown handlers installed")


async def _run_worker_services(
    assembled: AssembledWorkerRuntime,
    stop_event: asyncio.Event,
) -> None:
    """Consumer와 복구 Scheduler를 하나의 프로세스 경계에서 실행합니다."""

    service_tasks: set[asyncio.Task[None]] = {asyncio.create_task(assembled.runtime.run(stop_event))}

    if assembled.recovery_scheduler is not None:
        service_tasks.add(
            asyncio.create_task(
                assembled.recovery_scheduler.run(
                    stop_event=stop_event,
                )
            )
        )

    try:
        done, _ = await asyncio.wait(
            service_tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # 어느 서비스든 예상보다 먼저 종료되거나 실패하면 결과를 전파합니다.
        for task in done:
            task.result()
    finally:
        for task in service_tasks:
            if not task.done():
                task.cancel()

        await asyncio.gather(
            *service_tasks,
            return_exceptions=True,
        )


async def _serve(
    assembled: AssembledWorkerRuntime,
    config: Config,
    logger: logging.Logger,
    stop_event: asyncio.Event,
) -> None:
    """종료 요청까지 Consumer loop를 유지하고, 요청 후에는 제한시간 안에 정리합니다.

    `stop_event`는 호출자가 만들어 넘깁니다. 신호 handler 설치는 `run()`이 담당하므로
    테스트는 signal 없이 종료 경로만 검증할 수 있습니다.
    """

    run_task = asyncio.create_task(
        _run_worker_services(
            assembled,
            stop_event,
        )
    )
    stop_task = asyncio.create_task(stop_event.wait())

    try:
        done, _ = await asyncio.wait(
            {run_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if run_task in done:
            # loop가 스스로 끝났다면(설정·연결 오류 등) 예외를 그대로 올립니다.
            run_task.result()
            return

        logger.info("worker shutdown requested")

        try:
            await asyncio.wait_for(
                run_task,
                timeout=config.WORKER_SHUTDOWN_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning("worker shutdown timeout reached; cancelling in-flight execution")
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task
    finally:
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task


async def run(*, ocr_engine: OcrEngine | None = None) -> None:
    """Worker runtime을 조립해 실행하고 종료 시 자원을 정리합니다.

    `ocr_engine`을 주입하지 않으면 OCR Handler가 등록되지 않고, OCR 메시지는 Provider
    호출 없이 승인된 실패로 처리됩니다. 저장소에 Worker에서 쓸 수 있는 승인된 CLOVA
    engine 구현이 아직 없어 기본값을 `None`으로 둡니다.
    """

    logger = get_logger()
    # 잘못된 설정은 메시지를 읽기 전에 기동 단계에서 드러냅니다.
    config = get_config()

    assembled = build_worker_runtime(
        config,
        logger=logger,
        clock=_create_clock(config),
        ocr_engine=ocr_engine,
    )

    logger.info(
        "worker runtime assembled",
        extra={
            "registered_job_types": sorted(job_type.value for job_type in assembled.registered_types),
            "consumer_group": config.REDIS_CONSUMER_GROUP,
            "stream": config.REDIS_STREAM_NAME,
        },
    )

    stop_event = asyncio.Event()
    _install_shutdown_handlers(stop_event, logger)

    try:
        await _serve(assembled, config, logger, stop_event)
    finally:
        await assembled.aclose()
        logger.info("worker runtime stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
