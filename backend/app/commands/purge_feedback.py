"""Delete feedback older than 30 days. Run daily in the synthetic Local environment."""

import asyncio
from datetime import UTC, datetime

from app.core import config
from app.core.config import Env
from app.core.db.databases import AsyncSessionFactory, close_database
from app.repositories.feedback_repository import FeedbackRepository


async def purge() -> int:
    if config.ENV is not Env.LOCAL:
        raise RuntimeError("Feedback operations are available only in Local.")
    async with AsyncSessionFactory() as session, session.begin():
        return await FeedbackRepository(session).purge_expired(datetime.now(UTC))


async def main() -> None:
    try:
        count = await purge()
        print(f"Expired feedback deleted: {count}")
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
