"""#526 실제 승인 저장소로 Catalog 사용 승인을 판정하는 경계 검증."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from ai_worker.adapters.sqlalchemy_catalog_approval_verifier import (
    CatalogApprovalAmbiguityError,
    SqlAlchemyCatalogApprovalVerifier,
)
from ai_worker.tasks.rag.catalog.types import CandidateCatalogSourceRef, CatalogVerificationStatus
from app.models import CatalogBuildApproval, CatalogBuildApprovalSource, CatalogSourceApproval, User
from tests.integration.rag.test_catalog_storage_roundtrip import (
    ALIAS_SNAPSHOT,
    PRODUCT_SNAPSHOT,
)
from tests.integration.rag.test_catalog_storage_roundtrip import database as _database

database = _database

pytestmark = pytest.mark.asyncio

CATALOG_VERSION = "synthetic-approval-v1"
EXPORT_CHECKSUM = "a" * 64
REFS = (
    CandidateCatalogSourceRef(PRODUCT_SNAPSHOT, "external:v1"),
    CandidateCatalogSourceRef(ALIAS_SNAPSHOT, "external:v2"),
)


async def _actor(factory) -> UUID:
    async with factory.begin() as session:
        actor = User(
            email=f"approver-{uuid4().hex[:8]}@example.test",
            hashed_password="synthetic-only",
            name="합성 승인자",
        )
        session.add(actor)
        await session.flush()
        return actor.id


async def issue(
    factory,
    *,
    actor_id: UUID,
    refs=REFS,
    checksum: str = EXPORT_CHECKSUM,
    catalog_version: str = CATALOG_VERSION,
    valid_from: datetime | None = None,
    expires_at: datetime | None = None,
    revision: int = 1,
) -> UUID:
    """검증 대상 승인을 저장합니다. 운영 발급 명령은 이번 범위 밖입니다."""
    now = datetime.now(UTC)
    build_id = uuid4()
    async with factory.begin() as session:
        session.add(
            CatalogBuildApproval(
                id=build_id,
                catalog_version=catalog_version,
                export_checksum=checksum,
                schema_version="medication-catalog-v2",
                manifest_spec_version="catalog-manifest-envelope-v2",
                approved_export_bytes=b"synthetic-export",
                is_complete=True,
                actor_id=actor_id,
                evidence_ref="synthetic://approval-evidence",
                valid_from=valid_from or now - timedelta(days=1),
                expires_at=expires_at or now + timedelta(days=1),
                issued_revision=revision,
            )
        )
        await session.flush()
        for index, ref in enumerate(refs):
            source_id = uuid4()
            session.add(
                CatalogSourceApproval(
                    id=source_id,
                    source_snapshot_id=UUID(ref.snapshot_id),
                    source_version=ref.source_version,
                    purpose=f"CATALOG_BUILD_{revision}_{index}",
                    actor_id=actor_id,
                    evidence_ref="synthetic://source-evidence",
                    valid_from=valid_from or now - timedelta(days=1),
                    expires_at=expires_at or now + timedelta(days=1),
                    issued_revision=revision,
                )
            )
            await session.flush()
            session.add(
                CatalogBuildApprovalSource(
                    build_approval_id=build_id,
                    source_approval_id=source_id,
                    source_snapshot_id=UUID(ref.snapshot_id),
                    source_version=ref.source_version,
                )
            )
    return build_id


async def test_stored_approval_is_returned_with_every_bound_source(database):
    _, factory = database
    actor_id = await _actor(factory)
    build_id = await issue(factory, actor_id=actor_id)

    receipt = await SqlAlchemyCatalogApprovalVerifier(factory).verify(
        catalog_version=CATALOG_VERSION, export_checksum=EXPORT_CHECKSUM, source_refs=REFS
    )

    assert receipt is not None
    assert receipt.receipt_id == str(build_id)
    assert receipt.verification_status is CatalogVerificationStatus.APPROVED
    assert receipt.is_complete is True
    # 요청한 순서대로 돌려줍니다. envelope의 exact bytes 비교가 순서에 의존합니다.
    assert tuple(item.source_ref.snapshot_id for item in receipt.sources) == tuple(ref.snapshot_id for ref in REFS)


@pytest.mark.parametrize(
    ("catalog_version", "checksum"),
    (
        ("other-catalog-version", EXPORT_CHECKSUM),
        (CATALOG_VERSION, "b" * 64),
    ),
)
async def test_claimed_target_that_was_never_approved_is_refused(database, catalog_version, checksum):
    _, factory = database
    actor_id = await _actor(factory)
    await issue(factory, actor_id=actor_id)

    receipt = await SqlAlchemyCatalogApprovalVerifier(factory).verify(
        catalog_version=catalog_version, export_checksum=checksum, source_refs=REFS
    )

    assert receipt is None


@pytest.mark.parametrize("mutation", ["missing", "extra"])
async def test_source_set_must_match_the_approval_exactly(database, mutation):
    _, factory = database
    actor_id = await _actor(factory)
    if mutation == "missing":
        await issue(factory, actor_id=actor_id, refs=REFS)
        requested = (REFS[0],)
    else:
        await issue(factory, actor_id=actor_id, refs=(REFS[0],))
        requested = REFS

    receipt = await SqlAlchemyCatalogApprovalVerifier(factory).verify(
        catalog_version=CATALOG_VERSION, export_checksum=EXPORT_CHECKSUM, source_refs=requested
    )

    assert receipt is None


async def test_revoked_catalog_approval_stops_consumption(database):
    _, factory = database
    actor_id = await _actor(factory)
    build_id = await issue(factory, actor_id=actor_id)
    verifier = SqlAlchemyCatalogApprovalVerifier(factory)
    assert await verifier.verify(catalog_version=CATALOG_VERSION, export_checksum=EXPORT_CHECKSUM, source_refs=REFS)

    async with factory.begin() as session:
        await session.execute(
            text(
                "UPDATE catalog_build_approval SET revoked_at = now(), revoked_by = :actor, "
                "revoked_reason = 'synthetic revocation' WHERE id = :id"
            ),
            {"actor": str(actor_id), "id": str(build_id)},
        )

    assert (
        await verifier.verify(catalog_version=CATALOG_VERSION, export_checksum=EXPORT_CHECKSUM, source_refs=REFS)
        is None
    )


async def test_revoked_source_approval_stops_consumption(database):
    _, factory = database
    actor_id = await _actor(factory)
    await issue(factory, actor_id=actor_id)

    async with factory.begin() as session:
        await session.execute(
            text(
                "UPDATE catalog_source_approval SET revoked_at = now(), revoked_by = :actor, "
                "revoked_reason = 'synthetic revocation' WHERE source_snapshot_id = :snapshot"
            ),
            {"actor": str(actor_id), "snapshot": PRODUCT_SNAPSHOT},
        )

    assert (
        await SqlAlchemyCatalogApprovalVerifier(factory).verify(
            catalog_version=CATALOG_VERSION, export_checksum=EXPORT_CHECKSUM, source_refs=REFS
        )
        is None
    )


@pytest.mark.parametrize("boundary", ["before_valid_from", "at_expires_at"])
async def test_validity_window_boundaries_are_refused(database, boundary):
    _, factory = database
    actor_id = await _actor(factory)
    now = datetime.now(UTC)
    if boundary == "before_valid_from":
        await issue(factory, actor_id=actor_id, valid_from=now + timedelta(hours=1))
    else:
        await issue(factory, actor_id=actor_id, valid_from=now - timedelta(hours=2), expires_at=now)

    assert (
        await SqlAlchemyCatalogApprovalVerifier(factory).verify(
            catalog_version=CATALOG_VERSION, export_checksum=EXPORT_CHECKSUM, source_refs=REFS
        )
        is None
    )


async def test_expiry_is_rechecked_at_consumption_time(database):
    """transaction 시작 시각이 아니라 사용 확정 직전 시각으로 판정합니다."""
    _, factory = database
    actor_id = await _actor(factory)
    now = datetime.now(UTC)
    await issue(factory, actor_id=actor_id, valid_from=now - timedelta(hours=2), expires_at=now + timedelta(minutes=5))

    later = SqlAlchemyCatalogApprovalVerifier(factory, now=lambda: now + timedelta(hours=1))

    assert (
        await later.verify(catalog_version=CATALOG_VERSION, export_checksum=EXPORT_CHECKSUM, source_refs=REFS) is None
    )


async def test_two_live_approvals_for_one_export_are_not_silently_picked(database):
    """재승인으로 유효 승인이 둘이면 임의 선택 대신 실패시킵니다."""
    _, factory = database
    actor_id = await _actor(factory)
    await issue(factory, actor_id=actor_id, revision=1)
    await issue(factory, actor_id=actor_id, revision=2)

    with pytest.raises(CatalogApprovalAmbiguityError):
        await SqlAlchemyCatalogApprovalVerifier(factory).verify(
            catalog_version=CATALOG_VERSION, export_checksum=EXPORT_CHECKSUM, source_refs=REFS
        )
