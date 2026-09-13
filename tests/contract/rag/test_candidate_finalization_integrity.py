from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_candidate import MedicationCandidateSearch, MedicationCandidateSearchStatus
from app.repositories.medication_candidate_repository import MedicationCandidateRepository


@pytest.mark.asyncio
async def test_finalization_rejects_persisted_count_mismatch_before_state_change() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = MedicationCandidateSearchStatus.RUNNING
    counts = MagicMock()
    counts.one.return_value = (2, 1)
    session.execute.return_value = counts
    search = MedicationCandidateSearch(id=uuid4(), status=MedicationCandidateSearchStatus.RUNNING)
    repository = MedicationCandidateRepository(session)
    with pytest.raises(ValueError, match="persisted rows"):
        await repository.finalize_search(
            search=search,
            status=MedicationCandidateSearchStatus.READY,
            candidate_count=1,
            displayed_candidate_count=1,
            finalized_at=datetime.now(UTC),
        )
    assert search.status == MedicationCandidateSearchStatus.RUNNING
    assert "FOR UPDATE" in str(session.scalar.await_args.args[0])
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_search_rejects_further_result_assembly() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = MedicationCandidateSearchStatus.CONSUMED
    search = MedicationCandidateSearch(id=uuid4(), status=MedicationCandidateSearchStatus.RUNNING)
    with pytest.raises(ValueError, match="must be running"):
        await MedicationCandidateRepository(session).add_results(search=search, results=[])
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
