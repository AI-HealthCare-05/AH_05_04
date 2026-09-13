"""Generate and publish a bounded batch of app-internal notifications; no external delivery."""

import asyncio
from datetime import UTC, datetime
from time import monotonic

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


BATCH_TIMEOUT_SECONDS = 45


async def run() -> bool:
    """Return success without exposing exception text, SQL or notification payloads."""
    started = monotonic()
    try:
        async with asyncio.timeout(BATCH_TIMEOUT_SECONDS):
            result = await process_notifications_once()
        default_logger.info(
            "notification_batch status=success completed_at=%s duration_seconds=%.3f "
            "created_count=%d delivered_count=%d cancelled_count=%d",
            datetime.now(UTC).isoformat(),
            monotonic() - started,
            result.created_count,
            result.delivered_count,
            result.cancelled_count,
        )
        return True
    except Exception as exc:
        # Partial generation may already be committed. Counts are deliberately
        # omitted on failure; the next invocation recovers pending records.
        reason = "timeout" if isinstance(exc, TimeoutError) else "batch_error"
        default_logger.error(
            "notification_batch status=failed completed_at=%s duration_seconds=%.3f reason=%s",
            datetime.now(UTC).isoformat(),
            monotonic() - started,
            reason,
        )
        return False
    finally:
        await close_database()


def main() -> None:
    raise SystemExit(0 if asyncio.run(run()) else 1)


if __name__ == "__main__":
    main()
