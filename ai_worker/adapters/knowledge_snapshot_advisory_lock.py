from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SNAPSHOT_ADVISORY_LOCK_PREFIX = "knowledge-source-snapshot:"


def snapshot_advisory_lock_key(snapshot_id: UUID) -> str:
    """Snapshot ID로부터 결정적인 advisory lock key 문자열을 도출합니다."""
    return f"{SNAPSHOT_ADVISORY_LOCK_PREFIX}{snapshot_id}"


async def acquire_snapshot_advisory_locks(
    session: AsyncSession,
    snapshot_ids: Iterable[UUID],
) -> tuple[str, ...]:
    """Source Snapshot advisory lock을 UUID.bytes 오름차순으로 결정적 획득합니다."""
    unique_ids = set(snapshot_ids)
    sorted_ids = sorted(unique_ids, key=lambda u: u.bytes)
    locked_keys: list[str] = []

    for snapshot_id in sorted_ids:
        lock_key = snapshot_advisory_lock_key(snapshot_id)
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:snapshot_lock_key, 0))"),
            {"snapshot_lock_key": lock_key},
        )
        locked_keys.append(lock_key)

    return tuple(locked_keys)
