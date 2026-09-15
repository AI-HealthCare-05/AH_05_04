from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from app.repositories.track_c_storage_repository import TrackCStorageRepository
from app.services.track_c_revision_invalidation import TrackCCheckinRevisionInvalidation


async def test_adapter_delegates_exact_historical_revision_and_timestamp() -> None:
    repository = AsyncMock(spec=TrackCStorageRepository)
    adapter = TrackCCheckinRevisionInvalidation(repository)
    checkin_id = uuid4()
    invalidated_at = datetime(2026, 9, 15, 3, tzinfo=UTC)

    await adapter.invalidate_for_checkin_revision(
        checkin_id=checkin_id,
        invalidated_revision=3,
        invalidated_at=invalidated_at,
    )

    repository.cancel_active_plans_for_checkin_revision.assert_awaited_once_with(
        checkin_id=checkin_id,
        checkin_revision=3,
        cancelled_at=invalidated_at,
    )
