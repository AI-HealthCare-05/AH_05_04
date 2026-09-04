"""Redis Client와 Stream Adapter 생성 경계 테스트입니다."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr
from redis.asyncio import Redis

from ai_worker.adapters.factory import (
    create_redis_client,
    create_stream_adapter,
)
from ai_worker.adapters.redis_stream import RedisStreamAdapter
from ai_worker.core.config import Config
from provider_contracts.observability import DeploymentEnvironment


def test_create_redis_client_uses_worker_config() -> None:
    config = Config(  # type: ignore[call-arg]
        _env_file=None,
        ENV=DeploymentEnvironment.LOCAL,
        DB_HOST="127.0.0.1",
        DB_NAME="test",
        DB_USER="worker",
        DB_PASSWORD="worker-password",
        CLOVA_OCR_INVOKE_URL="https://clova.test/ocr",
        CLOVA_OCR_SECRET=SecretStr("synthetic-clova-secret"),
        STORAGE_DIR="/tmp/medical-documents",
        REDIS_HOST="redis-test",
        REDIS_PORT=6380,
        REDIS_PASSWORD="synthetic-password",
        REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS=2.5,
        REDIS_SOCKET_TIMEOUT_SECONDS=8.0,
    )

    with patch(
        "ai_worker.adapters.factory.Redis",
    ) as redis_class:
        client = MagicMock(spec=Redis)
        redis_class.return_value = client

        result = create_redis_client(config)

    assert result is client
    redis_class.assert_called_once_with(
        host="redis-test",
        port=6380,
        password="synthetic-password",
        decode_responses=False,
        socket_connect_timeout=2.5,
        socket_timeout=8.0,
    )


@pytest.mark.asyncio
async def test_create_stream_adapter_uses_stream_config() -> None:
    config = Config(  # type: ignore[call-arg]
        _env_file=None,
        ENV=DeploymentEnvironment.LOCAL,
        DB_HOST="127.0.0.1",
        DB_NAME="test",
        DB_USER="worker",
        DB_PASSWORD="worker-password",
        CLOVA_OCR_INVOKE_URL="https://clova.test/ocr",
        CLOVA_OCR_SECRET=SecretStr("synthetic-clova-secret"),
        STORAGE_DIR="/tmp/medical-documents",
        REDIS_STREAM_NAME="test:jobs",
        REDIS_CONSUMER_GROUP="test-workers",
    )
    client = MagicMock(spec=Redis)
    client.xgroup_create = AsyncMock(return_value=True)

    adapter = create_stream_adapter(
        config,
        client=cast(Redis, client),
    )

    assert isinstance(adapter, RedisStreamAdapter)

    await adapter.ensure_consumer_group()

    client.xgroup_create.assert_awaited_once_with(
        name="test:jobs",
        groupname="test-workers",
        id="0",
        mkstream=True,
    )
