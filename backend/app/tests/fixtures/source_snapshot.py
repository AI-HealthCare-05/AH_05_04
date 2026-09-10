"""Privileged historical-state fixtures, not the production publication path."""

from dataclasses import replace
from uuid import uuid4

from app.models.rag_source import RagSnapshotVerificationStatus, RagSourceSnapshotVerification
from app.repositories.rag_source_catalog_repository import RagSourceCatalogRepository, RagSourceSnapshotCreate


async def seed_snapshot(repository: RagSourceCatalogRepository, item: RagSourceSnapshotCreate):
    snapshot = await repository.create_snapshot(
        replace(item, verification_status=RagSnapshotVerificationStatus.PENDING, verified_at=None, effective_at=None)
    )
    if item.verification_status != RagSnapshotVerificationStatus.PENDING or item.verified_at or item.effective_at:
        seal_id = uuid4()
        repository.session.add(
            RagSourceSnapshotVerification(
                id=seal_id,
                snapshot_id=snapshot.id,
                check_name="snapshot-state-seal",
                verification_result="NO_CHANGE",
                verified_at=item.verified_at or item.effective_at or item.collected_at,
                verified_by="synthetic-fixture",
            )
        )
        await repository.session.flush()
        snapshot.verification_seal_id = seal_id
    snapshot.verification_status = item.verification_status
    snapshot.verified_at = item.verified_at
    snapshot.effective_at = item.effective_at
    await repository.session.flush()
    return snapshot
