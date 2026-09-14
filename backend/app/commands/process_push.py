"""Bounded opt-in Web Push delivery; app-internal publication commits separately."""

import asyncio
import time
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import config, default_logger
from app.core.db.databases import AsyncSessionFactory, close_database
from app.core.push import PushSettings, get_push_settings
from app.repositories.push_repository import PushRepository
from app.services.push import PushDeliveryService
from app.services.push_transport import PushSendResult, send_push


async def process_push_once(
    *, settings: PushSettings, session_factory: async_sessionmaker[AsyncSession] = AsyncSessionFactory, limit: int = 100
) -> int:
    if not settings.enabled or config.ENV == "production":
        return 0
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    async with session_factory.begin() as session:
        await PushDeliveryService(PushRepository(session)).revoke_invalid(datetime.now(UTC), limit)
    async with session_factory.begin() as session:
        await PushRepository(session).cleanup(datetime.now(UTC), limit)
    async with session_factory.begin() as session:
        await PushRepository(session).purge_subscriptions(datetime.now(UTC), limit)
    async with session_factory.begin() as session:
        repo = PushRepository(session)
        await repo.generate(datetime.now(UTC), limit)
        candidates = await repo.candidate_ids(datetime.now(UTC), limit)
    sent = 0
    outcomes: Counter[tuple[str, str]] = Counter()
    for delivery_id in candidates:
        async with session_factory.begin() as session:
            claim = await PushDeliveryService(PushRepository(session)).prepare(delivery_id)
        if claim is None:
            continue
        ttl = min(300, int((claim.expires_at - datetime.now(UTC)).total_seconds()))
        if ttl < 1:
            result = PushSendResult("CANCELLED", "EXPIRED")
        else:
            try:
                subscription = settings.decrypt(claim.key_id, claim.ciphertext)
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        send_push, settings, subscription, claim.payload, ttl, deadline=time.monotonic() + min(10, ttl)
                    ),
                    timeout=12,
                )
            except TimeoutError:
                result = PushSendResult("UNKNOWN", "TRANSPORT_UNKNOWN")
            except Exception:
                result = PushSendResult("FAILED", "CONFIGURATION_ERROR")
        async with session_factory.begin() as session:
            persisted_status = await PushDeliveryService(PushRepository(session)).finish(
                claim, result, datetime.now(UTC)
            )
        sent += persisted_status == "ACCEPTED"
        outcomes[(persisted_status or "FENCED", result.reason or "NONE")] += 1
    for (status, reason), count in sorted(outcomes.items()):
        default_logger.info("push_batch outcome=%s reason=%s count=%d", status, reason, count)
    return sent


async def run() -> bool:
    try:
        settings = get_push_settings()
        if not settings.enabled or config.ENV == "production":
            return True
        async with asyncio.timeout(45):
            accepted = await process_push_once(settings=settings)
        default_logger.info("push_batch status=success accepted_count=%d", accepted)
        return True
    except Exception:
        default_logger.error("push_batch status=failed reason=batch_error")
        return False
    finally:
        await close_database()


def main() -> None:
    raise SystemExit(0 if asyncio.run(run()) else 1)


if __name__ == "__main__":
    main()
