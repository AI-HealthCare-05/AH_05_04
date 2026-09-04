"""Worker 프로세스 수명주기(종료 요청·제한시간·자원 정리)를 검증합니다(#233)."""

import asyncio
import logging
from typing import Any

import pytest

from ai_worker import main as worker_main
from ai_worker.core.config import Config
from ai_worker.core.runtime_assembly import AssembledWorkerRuntime
from provider_contracts.observability import DeploymentEnvironment

_BASE_SETTINGS: dict[str, Any] = {
    "ENV": DeploymentEnvironment.LOCAL,
    "DB_HOST": "127.0.0.1",
    "DB_NAME": "test",
    "DB_USER": "worker",
    "DB_PASSWORD": "worker-password",
    "CLOVA_OCR_INVOKE_URL": "https://clova.test/ocr",
    "CLOVA_OCR_SECRET": "synthetic-clova-secret",
    "STORAGE_DIR": "/tmp/medical-documents",
}


def _config(**overrides: Any) -> Config:
    return Config(_env_file=None, **{**_BASE_SETTINGS, **overrides})  # type: ignore[call-arg]


class _StopAwareRuntime:
    """stop_event가 설정되면 loop를 끝내는 runtime 대역입니다."""

    def __init__(self) -> None:
        self.started = False

    async def run(self, stop_event: asyncio.Event) -> None:
        self.started = True
        await stop_event.wait()


class _HangingRuntime:
    """종료 요청을 무시하고 계속 실행되는 runtime 대역입니다."""

    def __init__(self) -> None:
        self.cancelled = False

    async def run(self, stop_event: asyncio.Event) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _FailingRuntime:
    async def run(self, stop_event: asyncio.Event) -> None:
        raise RuntimeError("redis connection lost")


def _assembled(runtime: Any) -> tuple[AssembledWorkerRuntime, list[str]]:
    closed: list[str] = []

    async def aclose() -> None:
        closed.append("closed")

    return (
        AssembledWorkerRuntime(
            runtime=runtime,  # type: ignore[arg-type]
            registered_types=frozenset(),
            aclose=aclose,
        ),
        closed,
    )


@pytest.mark.asyncio
async def test_serve_returns_when_stop_is_requested() -> None:
    runtime = _StopAwareRuntime()
    assembled, _ = _assembled(runtime)
    stop_event = asyncio.Event()

    serve_task = asyncio.create_task(worker_main._serve(assembled, _config(), logging.getLogger("test"), stop_event))
    await asyncio.sleep(0)
    stop_event.set()

    await asyncio.wait_for(serve_task, timeout=1.0)

    assert runtime.started is True


@pytest.mark.asyncio
async def test_serve_cancels_execution_when_shutdown_timeout_is_reached() -> None:
    runtime = _HangingRuntime()
    assembled, _ = _assembled(runtime)
    stop_event = asyncio.Event()

    serve_task = asyncio.create_task(
        worker_main._serve(
            assembled,
            _config(WORKER_SHUTDOWN_TIMEOUT_SECONDS=0.05),
            logging.getLogger("test"),
            stop_event,
        )
    )
    await asyncio.sleep(0)
    stop_event.set()

    await asyncio.wait_for(serve_task, timeout=2.0)

    assert runtime.cancelled is True


@pytest.mark.asyncio
async def test_serve_propagates_runtime_failure() -> None:
    assembled, _ = _assembled(_FailingRuntime())

    with pytest.raises(RuntimeError):
        await worker_main._serve(
            assembled,
            _config(),
            logging.getLogger("test"),
            asyncio.Event(),
        )


@pytest.mark.asyncio
async def test_run_closes_resources_even_when_serve_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis·DB 연결은 실패 경로에서도 반드시 닫혀야 합니다."""

    assembled, closed = _assembled(_FailingRuntime())

    fake_ocr_provider = object()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        worker_main,
        "create_clova_ocr_provider",
        lambda config: fake_ocr_provider,
    )

    def build_runtime(*args, **kwargs):
        captured["ocr_engine"] = kwargs["ocr_engine"]
        captured["ocr_provider"] = kwargs["ocr_provider"]
        return assembled

    monkeypatch.setattr(worker_main, "get_config", _config)
    monkeypatch.setattr(worker_main, "get_logger", lambda: logging.getLogger("test"))
    monkeypatch.setattr(worker_main, "build_worker_runtime", build_runtime)

    with pytest.raises(RuntimeError):
        await worker_main.run()

    assert closed == ["closed"]
    assert captured["ocr_engine"] is None
    assert captured["ocr_provider"] is fake_ocr_provider
