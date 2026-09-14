from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_worker import healthcheck
from ai_worker.core.config import Config


def config(storage: Path) -> Config:
    values: dict[str, Any] = {
        "ENV": "local",
        "DB_HOST": "localhost",
        "DB_NAME": "synthetic",
        "DB_USER": "worker",
        "DB_PASSWORD": "synthetic-password",
        "CLOVA_OCR_INVOKE_URL": "https://clova.test/ocr",
        "CLOVA_OCR_SECRET": "synthetic-secret",
        "STORAGE_DIR": str(storage),
    }
    return Config(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.mark.parametrize("failure", [None, "database", "redis", "group"])
async def test_readiness_checks_dependencies_without_consuming_messages(tmp_path, monkeypatch, failure):
    connection = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = context
    engine.dispose = AsyncMock()
    client = AsyncMock()
    client.xinfo_groups.return_value = [{"name": b"ai-workers"}]
    monkeypatch.setattr(healthcheck, "create_async_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(healthcheck, "create_redis_client", lambda _: client)
    if failure == "database":
        connection.execute.side_effect = RuntimeError("synthetic-private-error")
    elif failure == "redis":
        client.execute_command.side_effect = RuntimeError("synthetic-private-error")
    elif failure == "group":
        client.xinfo_groups.return_value = []
    if failure:
        with pytest.raises(RuntimeError):
            await healthcheck.check_readiness(config(tmp_path))
    else:
        await healthcheck.check_readiness(config(tmp_path))
    engine.dispose.assert_awaited_once()
    client.aclose.assert_awaited_once()
    client.xreadgroup.assert_not_called()
    client.xack.assert_not_called()
    client.xgroup_create.assert_not_called()


async def test_missing_storage_fails_before_network_access(tmp_path, monkeypatch):
    create = MagicMock()
    monkeypatch.setattr(healthcheck, "create_async_engine", create)
    with pytest.raises(RuntimeError, match="WORKER_STORAGE_UNAVAILABLE"):
        await healthcheck.check_readiness(config(tmp_path / "missing"))
    create.assert_not_called()


def test_cli_redacts_dependency_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(healthcheck, "get_config", lambda: config(tmp_path))
    monkeypatch.setattr(healthcheck, "check_readiness", AsyncMock(side_effect=RuntimeError("synthetic-private-error")))
    assert healthcheck.main() == 1
    assert capsys.readouterr().out == "worker readiness failed\n"
