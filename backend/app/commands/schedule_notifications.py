"""Opt-in Compose runtime for the existing bounded notification command."""

import asyncio
import signal

from app.commands.process_notifications import run
from app.core import default_logger

INTERVAL_SECONDS = 60


async def serve() -> None:
    # Fixed delay after each attempt prevents a local backlog of overlapping jobs.
    while True:
        await run()
        await asyncio.sleep(INTERVAL_SECONDS)


async def run_scheduler() -> None:
    task = asyncio.create_task(serve())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        default_logger.info("notification_scheduler status=stopped")
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def main() -> None:
    asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()
