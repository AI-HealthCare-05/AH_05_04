"""Generate and publish a bounded batch of app-internal notifications; no external delivery."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import default_logger
from app.core.db.databases import AsyncSessionFactory, close_database
from app.repositories.notification_repository import NotificationRepository
from app.services.notifications import NotificationBatchResult, NotificationScheduler


async def process_notifications_once(
    *,
    now: datetime | None = None,
    limit: int = 500,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionFactory,
) -> NotificationBatchResult:
    instant = now or datetime.now(UTC)
    async with session_factory() as session:
        async with session.begin():
            generated = await NotificationScheduler(NotificationRepository(session)).generate_once(
                now=instant, limit=limit
            )
    # Release generation locks before acquiring the publication set. Committed
    # PENDING rows survive a crash here and are picked up by the next invocation.
    async with session_factory() as session:
        async with session.begin():
            published = await NotificationScheduler(NotificationRepository(session)).publish_once(
                now=instant, limit=limit
            )
    return NotificationBatchResult(generated.created_count, published.delivered_count, published.cancelled_count)


async def run() -> None:
    try:
        result = await process_notifications_once()
        default_logger.info(
            "app notifications processed",
            extra={
                "created_count": result.created_count,
                "delivered_count": result.delivered_count,
                "cancelled_count": result.cancelled_count,
            },
        )
    finally:
        await close_database()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
