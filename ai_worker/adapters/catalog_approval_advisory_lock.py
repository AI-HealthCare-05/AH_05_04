"""Catalog 승인 row에 대한 transaction-scoped advisory lock (#526 Phase 2).

Catalog Writer에는 승인 표 UPDATE 권한이 없으므로 `SELECT ... FOR UPDATE`로 승인을 잠글 수
없다. 대신 기존 Knowledge Snapshot lock과 같은 PostgreSQL advisory lock 방식을 승인 ID 기준으로
재사용한다. 저장·소비·철회가 모두 같은 key와 같은 획득 순서를 쓰므로 deadlock이 생기지 않는다.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CATALOG_APPROVAL_ADVISORY_LOCK_PREFIX = "catalog-approval:"


class CatalogApprovalLockKeyError(ValueError):
    """승인 식별자가 lock key로 쓸 수 없는 형태입니다. 잠그지 않고 통과시키지 않습니다."""


def catalog_approval_advisory_lock_key(approval_id: UUID | str) -> str:
    """승인 ID로부터 결정적인 advisory lock key 문자열을 도출합니다."""
    return f"{CATALOG_APPROVAL_ADVISORY_LOCK_PREFIX}{_approval_uuid(approval_id)}"


def _approval_uuid(approval_id: UUID | str) -> UUID:
    if isinstance(approval_id, UUID):
        return approval_id
    try:
        return UUID(str(approval_id))
    except (ValueError, AttributeError, TypeError):
        raise CatalogApprovalLockKeyError() from None


async def acquire_catalog_approval_advisory_locks(
    session: AsyncSession,
    approval_ids: Iterable[UUID | str],
) -> tuple[str, ...]:
    """승인 advisory lock을 UUID.bytes 오름차순으로 결정적 획득합니다.

    저장·소비·철회가 같은 정렬을 쓰기 때문에 두 경로가 서로를 기다려도 순환 대기가 없습니다.
    """
    unique_ids = {_approval_uuid(value) for value in approval_ids}
    locked_keys: list[str] = []
    for approval_id in sorted(unique_ids, key=lambda value: value.bytes):
        lock_key = catalog_approval_advisory_lock_key(approval_id)
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:approval_lock_key, 0))"),
            {"approval_lock_key": lock_key},
        )
        locked_keys.append(lock_key)
    return tuple(locked_keys)


CATALOG_APPROVAL_TARGET_ADVISORY_LOCK_PREFIX = "catalog-approval-target:"


async def acquire_catalog_approval_target_locks(
    session: AsyncSession,
    target_keys: Iterable[str],
) -> tuple[str, ...]:
    """아직 승인 ID가 없는 발급 대상을 직렬화합니다.

    발급은 승인 row를 만들기 전이라 ID 기준으로 잠글 수 없으므로 대상 기준 key를 씁니다.
    철회·저장·소비가 쓰는 ID 기준 lock과 다른 이름공간이라 서로를 막지 않습니다.
    """
    locked_keys: list[str] = []
    for suffix in sorted(set(target_keys)):
        lock_key = f"{CATALOG_APPROVAL_TARGET_ADVISORY_LOCK_PREFIX}{suffix}"
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:approval_lock_key, 0))"),
            {"approval_lock_key": lock_key},
        )
        locked_keys.append(lock_key)
    return tuple(locked_keys)


__all__ = [
    "CATALOG_APPROVAL_ADVISORY_LOCK_PREFIX",
    "CATALOG_APPROVAL_TARGET_ADVISORY_LOCK_PREFIX",
    "acquire_catalog_approval_target_locks",
    "CatalogApprovalLockKeyError",
    "acquire_catalog_approval_advisory_locks",
    "catalog_approval_advisory_lock_key",
]
