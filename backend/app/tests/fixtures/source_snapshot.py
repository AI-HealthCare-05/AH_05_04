"""Privileged historical-state fixtures, not the production publication path."""

from dataclasses import replace

from app.models.rag_source import RagSnapshotVerificationStatus
from app.repositories.rag_source_catalog_repository import RagSourceCatalogRepository, RagSourceSnapshotCreate


async def seed_snapshot(repository: RagSourceCatalogRepository, item: RagSourceSnapshotCreate):
    snapshot = await repository.create_snapshot(
        replace(item, verification_status=RagSnapshotVerificationStatus.PENDING, verified_at=None, effective_at=None)
    )
    snapshot.verification_status = item.verification_status
    snapshot.verified_at = item.verified_at
    snapshot.effective_at = item.effective_at
    await repository.session.flush()
    return snapshot
