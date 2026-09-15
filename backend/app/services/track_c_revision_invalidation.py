"""Same-transaction Track B to Track C check-in revision invalidation."""

from datetime import datetime
from uuid import UUID

from app.repositories.track_c_storage_repository import TrackCStorageRepository


class TrackCCheckinRevisionInvalidation:
    """Invalidate derived Track C state while retaining all historical rows."""

    def __init__(self, repository: TrackCStorageRepository) -> None:
        self._repository = repository

    async def invalidate_for_checkin_revision(
        self,
        *,
        checkin_id: UUID,
        invalidated_revision: int,
        invalidated_at: datetime,
    ) -> None:
        await self._repository.cancel_active_plans_for_checkin_revision(
            checkin_id=checkin_id,
            checkin_revision=invalidated_revision,
            cancelled_at=invalidated_at,
        )
