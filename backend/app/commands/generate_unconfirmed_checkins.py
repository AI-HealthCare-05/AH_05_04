"""기한이 지난 occurrence를 UNCONFIRMED Check-in으로 닫는 bounded one-shot command."""

import asyncio
from datetime import UTC, datetime
from time import monotonic

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import default_logger
from app.core.db.databases import AsyncSessionFactory, close_database
from app.repositories.medication_checkin_repository import MedicationCheckinRepository
from app.services.medication_checkins import MedicationCheckinDeadlineScheduler, UnconfirmedGenerationResult


async def generate_unconfirmed_checkins_once(
    *,
    now: datetime | None = None,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionFactory,
) -> UnconfirmedGenerationResult:
    async with session_factory() as session:
        try:
            result = await MedicationCheckinDeadlineScheduler(
                MedicationCheckinRepository(session)
            ).generate_unconfirmed(now=now or datetime.now(UTC))
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
    return result


BATCH_TIMEOUT_SECONDS = 45


async def run() -> bool:
    """Return success without exposing exception text, SQL or occurrence identifiers."""
    started = monotonic()
    try:
        async with asyncio.timeout(BATCH_TIMEOUT_SECONDS):
            result = await generate_unconfirmed_checkins_once()
        default_logger.info(
            "checkin_deadline_batch status=success completed_at=%s duration_seconds=%.3f "
            "processed_count=%d duplicate_count=%d",
            datetime.now(UTC).isoformat(),
            monotonic() - started,
            result.processed_count,
            result.duplicate_count,
        )
        return True
    except Exception as exc:
        # The batch transaction is rolled back, so counts are deliberately omitted
        # on failure; the next invocation re-selects the same due occurrences.
        reason = "timeout" if isinstance(exc, TimeoutError) else "batch_error"
        default_logger.error(
            "checkin_deadline_batch status=failed completed_at=%s duration_seconds=%.3f reason=%s",
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
