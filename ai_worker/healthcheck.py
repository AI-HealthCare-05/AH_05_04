"""Read-only deployment readiness; OCR completion is verified by a separate smoke test."""

import asyncio
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from ai_worker.adapters.factory import create_redis_client
from ai_worker.core import get_config
from ai_worker.core.config import Config


async def check_readiness(config: Config) -> None:
    storage = Path(config.STORAGE_DIR)
    if not storage.is_dir() or not os.access(storage, os.R_OK | os.X_OK):
        raise RuntimeError("WORKER_STORAGE_UNAVAILABLE")

    engine = create_async_engine(config.database_url, echo=False, poolclass=NullPool)
    client = create_redis_client(config)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await client.execute_command("PING")
        # Do not create groups or consume/ACK messages from a health probe.
        groups = await client.xinfo_groups(config.REDIS_STREAM_NAME)
        if not any(group.get("name") == config.REDIS_CONSUMER_GROUP.encode() for group in groups):
            raise RuntimeError("WORKER_GROUP_UNAVAILABLE")
    finally:
        try:
            await client.aclose()
        finally:
            await engine.dispose()


def main() -> int:
    try:
        config = get_config()
        asyncio.run(asyncio.wait_for(check_readiness(config), timeout=8))
    except Exception:
        # Config/DB/Redis exceptions can contain credentials; never print their details.
        print("worker readiness failed")
        return 1
    print("worker readiness passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
