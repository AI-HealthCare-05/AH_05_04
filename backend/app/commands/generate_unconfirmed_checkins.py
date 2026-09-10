"""기한이 지난 occurrence를 UNCONFIRMED Check-in으로 닫는 one-shot command."""

import asyncio
from datetime import UTC, datetime

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


async def run() -> UnconfirmedGenerationResult:
    result = await generate_unconfirmed_checkins_once()
    default_logger.info(
        "due medication occurrences closed as unconfirmed",
        extra={
            "processed_count": result.processed_count,
            "duplicate_count": result.duplicate_count,
        },
    )
    return result


async def _run_and_close() -> None:
    try:
        await run()
    finally:
        await close_database()


def main() -> None:
    asyncio.run(_run_and_close())


if __name__ == "__main__":
    main()
