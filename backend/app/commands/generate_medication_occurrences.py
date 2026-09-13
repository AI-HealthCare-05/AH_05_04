"""14일 medication occurrence rolling horizon을 한 번 갱신하는 management command."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import config, default_logger
from app.core.db.databases import AsyncSessionFactory, close_database
from app.repositories.medication_schedule_repository import MedicationScheduleRepository
from app.services.medication_occurrences import (
    MedicationOccurrenceScheduler,
    RollingOccurrenceGenerationResult,
)


async def generate_medication_occurrences_once(
    *,
    now: datetime | None = None,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionFactory,
) -> RollingOccurrenceGenerationResult:
    """독립 transaction에서 현재 14일 horizon을 갱신하고 commit한다."""

    async with session_factory() as session:
        try:
            result = await MedicationOccurrenceScheduler(
                MedicationScheduleRepository(session),
                service_timezone=config.TIMEZONE,
            ).generate(now=now or datetime.now(UTC))
            await session.commit()
        except BaseException:
            await session.rollback()
            raise

    return result


async def run() -> RollingOccurrenceGenerationResult:
    """운영 scheduler가 호출하는 one-shot 비동기 진입점."""

    result = await generate_medication_occurrences_once()
    default_logger.info(
        "medication occurrence rolling horizon generated",
        extra={
            "horizon_start": result.horizon_start.isoformat(),
            "horizon_end": result.horizon_end.isoformat(),
            "created_count": result.created_count,
            "duplicate_count": result.duplicate_count,
            "ended_schedule_count": result.ended_schedule_count,
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
