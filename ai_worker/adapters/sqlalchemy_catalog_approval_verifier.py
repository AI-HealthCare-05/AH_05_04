"""승인 저장소를 직접 조회하는 Catalog 승인 검증기 (#166, #526).

호출자가 주장한 checksum·완전성·APPROVED를 믿지 않고 저장된 승인 사실과 대조합니다.
Snapshot이 CURRENT라는 이유로 사용 승인을 대신하지 않으며, 철회·만료된 승인은 거부합니다.

업무 판정은 여기 Python 계층에서 하고 DB는 일반 제약만 씁니다. 이 검증기는 조회 전용이며
승인 발급·철회 권한을 갖지 않습니다.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String, column, select, table
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_worker.adapters.catalog_approval_advisory_lock import (
    CatalogApprovalLockKeyError,
    acquire_catalog_approval_advisory_locks,
)
from ai_worker.tasks.rag.catalog.approval import (
    CatalogApprovalBinding,
    CatalogApprovalReceipt,
    CatalogSourceApproval,
)
from ai_worker.tasks.rag.catalog.types import (
    CandidateCatalogSourceRef,
    CatalogFreshnessStatus,
    CatalogVerificationStatus,
)

_BUILD_APPROVAL = table(
    "catalog_build_approval",
    column("id", String(36)),
    column("catalog_version", String(100)),
    column("export_checksum", String(64)),
    column("is_complete", Boolean()),
    column("valid_from", DateTime(timezone=True)),
    column("expires_at", DateTime(timezone=True)),
    column("revoked_at", DateTime(timezone=True)),
)
_BUILD_APPROVAL_SOURCE = table(
    "catalog_build_approval_source",
    column("build_approval_id", String(36)),
    column("source_approval_id", String(36)),
    column("source_snapshot_id", String(36)),
    column("source_version", String(200)),
)
_SOURCE_APPROVAL = table(
    "catalog_source_approval",
    column("id", String(36)),
    column("source_snapshot_id", String(36)),
    column("source_version", String(200)),
    column("purpose", String(60)),
    column("valid_from", DateTime(timezone=True)),
    column("expires_at", DateTime(timezone=True)),
    column("revoked_at", DateTime(timezone=True)),
)

#: 이 Catalog 도메인의 Source 사용 목적은 #526에서 PRODUCT_IDENTIFICATION으로 확정되었습니다.
#: 다른 목적으로 발급된 승인은 Catalog 사용 승인으로 인정하지 않습니다.
CATALOG_SOURCE_USE_PURPOSE = "PRODUCT_IDENTIFICATION"


class CatalogApprovalStorageError(RuntimeError):
    """승인 저장소를 신뢰할 수 없어 판정을 내릴 수 없습니다. 통과로 해석하지 않습니다."""


class CatalogApprovalAmbiguityError(CatalogApprovalStorageError):
    """같은 export 구성에 유효한 승인이 둘 이상입니다. 임의로 하나를 고르지 않습니다."""


class SqlAlchemyCatalogApprovalVerifier:
    """`CatalogApprovalVerifier` 포트의 실제 저장소 구현입니다."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        # 사용 확정 직전 실제 시각으로 만료를 재검사합니다. transaction 시작 시각 고정값을 쓰지 않습니다.
        self._now = now or (lambda: datetime.now(UTC))

    async def verify(
        self,
        *,
        catalog_version: str,
        export_checksum: str,
        source_refs: tuple[CandidateCatalogSourceRef, ...],
    ) -> CatalogApprovalReceipt | None:
        """정확히 이 export 구성에 발급된 유효 승인을 찾습니다. 없으면 None입니다."""
        checked_at = self._now()
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(
                            _BUILD_APPROVAL.c.id,
                            _BUILD_APPROVAL.c.is_complete,
                        ).where(
                            _BUILD_APPROVAL.c.catalog_version == catalog_version,
                            _BUILD_APPROVAL.c.export_checksum == export_checksum,
                            _BUILD_APPROVAL.c.revoked_at.is_(None),
                            _BUILD_APPROVAL.c.valid_from <= checked_at,
                            _BUILD_APPROVAL.c.expires_at > checked_at,
                        )
                    )
                ).all()
                if not rows:
                    return None
                if len(rows) > 1:
                    # 재승인으로 여러 건이 살아 있으면 어느 receipt가 envelope에 들어갈지 결정할 수
                    # 없습니다. 임의 선택은 과거 manifest의 exact bytes 비교를 깨뜨립니다.
                    raise CatalogApprovalAmbiguityError()
                approval_id, is_complete = rows[0]
                sources = await self._verified_sources(
                    session, approval_id=approval_id, source_refs=source_refs, checked_at=checked_at
                )
        except SQLAlchemyError:
            raise CatalogApprovalStorageError() from None

        if sources is None:
            return None
        return CatalogApprovalReceipt(
            receipt_id=str(approval_id),
            catalog_version=catalog_version,
            export_checksum=export_checksum,
            verification_status=CatalogVerificationStatus.APPROVED,
            is_complete=bool(is_complete),
            sources=sources,
        )

    async def _verified_sources(
        self,
        session: AsyncSession,
        *,
        approval_id: str,
        source_refs: tuple[CandidateCatalogSourceRef, ...],
        checked_at: datetime,
    ) -> tuple[CatalogSourceApproval, ...] | None:
        """결속된 Source 승인 집합이 요청 범위와 정확히 같고 모두 유효한지 확인합니다."""
        joined = (
            select(
                _BUILD_APPROVAL_SOURCE.c.source_snapshot_id,
                _BUILD_APPROVAL_SOURCE.c.source_version,
                _SOURCE_APPROVAL.c.id,
                _SOURCE_APPROVAL.c.revoked_at,
                _SOURCE_APPROVAL.c.valid_from,
                _SOURCE_APPROVAL.c.expires_at,
                _SOURCE_APPROVAL.c.purpose,
            )
            .select_from(
                _BUILD_APPROVAL_SOURCE.join(
                    _SOURCE_APPROVAL,
                    _BUILD_APPROVAL_SOURCE.c.source_approval_id == _SOURCE_APPROVAL.c.id,
                )
            )
            .where(_BUILD_APPROVAL_SOURCE.c.build_approval_id == approval_id)
        )
        linked = (await session.execute(joined)).all()

        expected = {(str(ref.snapshot_id), ref.source_version) for ref in source_refs}
        observed = {(str(row[0]), row[1]) for row in linked}
        # 누락도 초과도 거부합니다. 승인되지 않은 Source가 섞이거나 승인 범위 일부만 쓰지 못합니다.
        if expected != observed or len(linked) != len(expected):
            return None

        approvals: list[CatalogSourceApproval] = []
        for snapshot_id, source_version, source_approval_id, revoked_at, valid_from, expires_at, purpose in linked:
            # 다른 목적·NULL·공백 목적으로 발급된 승인은 Catalog 사용 승인이 아닙니다.
            if purpose is None or purpose.strip() != CATALOG_SOURCE_USE_PURPOSE:
                return None
            if revoked_at is not None or not (_aware(valid_from) <= checked_at < _aware(expires_at)):
                return None
            approvals.append(
                CatalogSourceApproval(
                    source_ref=CandidateCatalogSourceRef(snapshot_id=str(snapshot_id), source_version=source_version),
                    receipt_id=str(source_approval_id),
                    verification_status=CatalogVerificationStatus.APPROVED,
                    freshness_status=CatalogFreshnessStatus.CURRENT,
                )
            )
        ordered = {str(ref.snapshot_id): index for index, ref in enumerate(source_refs)}
        approvals.sort(key=lambda item: ordered[str(item.source_ref.snapshot_id)])
        return tuple(approvals)


async def verify_exact_catalog_approval(
    session: AsyncSession,
    *,
    binding: CatalogApprovalBinding,
    expected_purpose: str = CATALOG_SOURCE_USE_PURPOSE,
    lock: bool = True,
) -> bool:
    """호출한 session·transaction 안에서 정확히 그 승인 ID들이 지금도 유효한지 확인합니다.

    "가장 최근 유효 승인"을 고르지 않습니다. manifest가 지목한 build/Source 승인 ID가 아니면
    통과시키지 않으므로, 재승인이 생겨도 저장된 manifest의 근거가 바뀌지 않습니다.

    `lock=True`면 승인 advisory lock을 정해진 순서로 먼저 잡습니다. 이 lock은 호출자의
    transaction이 끝날 때까지 유지되므로, 검증과 저장/소비 사이에 철회가 끼어들 수 없습니다.
    Catalog Writer에는 승인 UPDATE 권한이 없어 row lock 대신 이 방식을 씁니다.
    """
    try:
        if lock:
            await acquire_catalog_approval_advisory_locks(session, binding.approval_ids)
    except CatalogApprovalLockKeyError:
        return False

    # 잠금 대기 중 만료될 수 있으므로 caller나 transaction 시작 시각을 사용하지 않습니다.
    checked_at = datetime.now(UTC)

    build = (
        await session.execute(
            select(_BUILD_APPROVAL.c.is_complete, _BUILD_APPROVAL.c.valid_from, _BUILD_APPROVAL.c.expires_at).where(
                _BUILD_APPROVAL.c.id == binding.build_approval_id,
                _BUILD_APPROVAL.c.catalog_version == binding.catalog_version,
                _BUILD_APPROVAL.c.export_checksum == binding.export_checksum,
                _BUILD_APPROVAL.c.revoked_at.is_(None),
            )
        )
    ).all()
    if len(build) != 1:
        return False
    is_complete, valid_from, expires_at = build[0]
    if is_complete is not True or not (_aware(valid_from) <= checked_at < _aware(expires_at)):
        return False

    linked = (
        await session.execute(
            select(
                _BUILD_APPROVAL_SOURCE.c.source_approval_id,
                _BUILD_APPROVAL_SOURCE.c.source_snapshot_id,
                _BUILD_APPROVAL_SOURCE.c.source_version,
                _SOURCE_APPROVAL.c.purpose,
                _SOURCE_APPROVAL.c.revoked_at,
                _SOURCE_APPROVAL.c.valid_from,
                _SOURCE_APPROVAL.c.expires_at,
            )
            .select_from(
                _BUILD_APPROVAL_SOURCE.join(
                    _SOURCE_APPROVAL,
                    _BUILD_APPROVAL_SOURCE.c.source_approval_id == _SOURCE_APPROVAL.c.id,
                )
            )
            .where(_BUILD_APPROVAL_SOURCE.c.build_approval_id == binding.build_approval_id)
        )
    ).all()

    expected_ids = set(binding.source_approval_ids)
    expected_refs = {(ref.snapshot_id, ref.source_version) for ref in binding.source_refs}
    observed_ids = {str(row[0]) for row in linked}
    observed_refs = {(str(row[1]), row[2]) for row in linked}
    # link 집합이 정확히 같아야 합니다. 누락도 초과도 승인 범위의 변경입니다.
    if len(linked) != len(expected_ids) or observed_ids != expected_ids or observed_refs != expected_refs:
        return False

    for _, _, _, purpose, revoked_at, source_valid_from, source_expires_at in linked:
        if purpose is None or purpose.strip() != expected_purpose:
            return False
        if revoked_at is not None:
            return False
        if not (_aware(source_valid_from) <= checked_at < _aware(source_expires_at)):
            return False
    return True


def _aware(value: datetime) -> datetime:
    """저장 드라이버가 naive datetime을 돌려줘도 UTC 기준으로 비교합니다."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = [
    "CATALOG_SOURCE_USE_PURPOSE",
    "CatalogApprovalAmbiguityError",
    "CatalogApprovalStorageError",
    "SqlAlchemyCatalogApprovalVerifier",
    "verify_exact_catalog_approval",
]
