from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.errors import ApiError
from app.models.rag_candidate import (
    MedicationCandidateSearchStatus,
    MedicationIdentificationSource,
    MedicationIdentificationStatus,
)
from app.repositories.medication_candidate_repository import (
    MedicationCandidateRepository,
    MedicationCandidateResultCreate,
)
from app.services.medication_identification import MedicationIdentificationService
from app.tests.conftest import test_engine


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


def _service(session: AsyncSession) -> MedicationIdentificationService:
    return MedicationIdentificationService(MedicationCandidateRepository(session))


def _ready_result(*, product_id=None, result_rank: int = 1) -> MedicationCandidateResultCreate:
    return MedicationCandidateResultCreate(
        product_id=product_id or uuid4(),
        code_system="MFDS_ITEM_SEQ",
        canonical_code="200012345",
        product_name="테스트정",
        strength_text="500mg",
        dosage_form="정제",
        manufacturer_name="테스트제약",
        product_status="ACTIVE",
        result_rank=result_rank,
        result_score=0.95,
        result_method="PRODUCT_NAME",
        is_displayed=True,
        selection_eligible=True,
    )


async def test_record_candidate_search_reuses_same_context(db_session: AsyncSession) -> None:
    service = _service(db_session)
    prescription_version_medication_id = uuid4()
    bundle_id = uuid4()
    index_id = uuid4()
    expires_at = datetime.now(config.TIMEZONE) + timedelta(minutes=10)

    first = await service.record_candidate_search(
        prescription_version_medication_id=prescription_version_medication_id,
        query_digest="query-digest-1",
        runtime_release_bundle_id=bundle_id,
        candidate_index_version_id=index_id,
        expires_at=expires_at,
    )
    second = await service.record_candidate_search(
        prescription_version_medication_id=prescription_version_medication_id,
        query_digest="query-digest-1",
        runtime_release_bundle_id=bundle_id,
        candidate_index_version_id=index_id,
        expires_at=expires_at,
    )

    assert first.is_reused is False
    assert second.is_reused is True
    assert second.search.id == first.search.id


async def test_record_candidate_search_invalidates_changed_context(db_session: AsyncSession) -> None:
    service = _service(db_session)
    prescription_version_medication_id = uuid4()

    first = await service.record_candidate_search(
        prescription_version_medication_id=prescription_version_medication_id,
        query_digest="query-digest-1",
        runtime_release_bundle_id=None,
        candidate_index_version_id=None,
        expires_at=None,
    )
    second = await service.record_candidate_search(
        prescription_version_medication_id=prescription_version_medication_id,
        query_digest="query-digest-2",
        runtime_release_bundle_id=None,
        candidate_index_version_id=None,
        expires_at=None,
    )

    assert second.is_reused is False
    assert second.search.id != first.search.id
    assert first.search.status == MedicationCandidateSearchStatus.INVALIDATED_INPUT_CHANGED
    assert first.search.invalidated_at is not None


async def test_confirm_identification_consumes_ready_search(db_session: AsyncSession) -> None:
    service = _service(db_session)
    prescription_version_medication_id = uuid4()
    product_id = uuid4()
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=prescription_version_medication_id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result(product_id=product_id)],
    )

    identification = await service.confirm_identification(
        prescription_version_medication_id=prescription_version_medication_id,
        candidate_search_result_id=finalized.results[0].id,
    )

    assert identification.status == MedicationIdentificationStatus.MATCHED
    assert identification.source == MedicationIdentificationSource.USER_SELECTED
    assert identification.product_id == product_id
    assert identification.confirmed_at is not None
    assert identification.decision_reason is None
    assert finalized.search.status == MedicationCandidateSearchStatus.CONSUMED
    assert finalized.search.consumed_at is not None


async def test_record_candidate_search_rejects_existing_identification(db_session: AsyncSession) -> None:
    service = _service(db_session)
    prescription_version_medication_id = uuid4()
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=prescription_version_medication_id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result()],
    )
    await service.confirm_identification(
        prescription_version_medication_id=prescription_version_medication_id,
        candidate_search_result_id=finalized.results[0].id,
    )

    with pytest.raises(ApiError) as exc_info:
        await service.record_candidate_search(
            prescription_version_medication_id=prescription_version_medication_id,
            query_digest="query-digest-2",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=None,
        )

    assert exc_info.value.code == "IDENTIFICATION_CONTEXT_STALE"
    assert exc_info.value.details[0].reason == "IDENTIFICATION_ALREADY_EXISTS"


async def test_reject_identification_invalidates_ready_search(db_session: AsyncSession) -> None:
    service = _service(db_session)
    prescription_version_medication_id = uuid4()
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=prescription_version_medication_id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result()],
    )

    identification = await service.reject_identification(
        search_id=search.id,
        candidate_search_result_id=finalized.results[0].id,
    )

    assert identification.status == MedicationIdentificationStatus.UNRESOLVED
    assert identification.source == MedicationIdentificationSource.USER_REJECTED
    assert identification.product_id is None
    assert identification.confirmed_at is None
    assert identification.rejected_at is not None
    assert identification.decision_reason == "USER_REJECTED_DISPLAYED_CANDIDATE"
    assert finalized.search.status == MedicationCandidateSearchStatus.INVALIDATED_USER_REJECTED
    assert finalized.search.invalidated_at is not None


async def test_expired_active_search_does_not_block_new_search(db_session: AsyncSession) -> None:
    service = _service(db_session)
    prescription_version_medication_id = uuid4()
    first = await service.record_candidate_search(
        prescription_version_medication_id=prescription_version_medication_id,
        query_digest="query-digest-1",
        runtime_release_bundle_id=None,
        candidate_index_version_id=None,
        expires_at=datetime.now(config.TIMEZONE) - timedelta(seconds=1),
    )

    second = await service.record_candidate_search(
        prescription_version_medication_id=prescription_version_medication_id,
        query_digest="query-digest-1",
        runtime_release_bundle_id=None,
        candidate_index_version_id=None,
        expires_at=None,
    )

    assert first.search.status == MedicationCandidateSearchStatus.EXPIRED
    assert second.is_reused is False
    assert second.search.id != first.search.id


async def test_non_ready_search_must_not_expose_selectable_result(db_session: AsyncSession) -> None:
    service = _service(db_session)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=uuid4(),
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=None,
        )
    ).search

    with pytest.raises(ApiError) as exc_info:
        await service.finalize_candidate_search(
            search_id=search.id,
            status=MedicationCandidateSearchStatus.AMBIGUOUS,
            results=[_ready_result()],
        )

    assert exc_info.value.code == "VALIDATION_FAILED"
    assert exc_info.value.details[0].reason == "NON_READY_RESULT_MUST_NOT_BE_DISPLAYED"


async def test_preflight_passes_when_all_medications_are_matched(db_session: AsyncSession) -> None:
    service = _service(db_session)
    first_medication_id = uuid4()
    second_medication_id = uuid4()

    for medication_id in (first_medication_id, second_medication_id):
        search = (
            await service.record_candidate_search(
                prescription_version_medication_id=medication_id,
                query_digest=f"query-digest-{medication_id}",
                runtime_release_bundle_id=None,
                candidate_index_version_id=None,
                expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
            )
        ).search
        finalized = await service.finalize_candidate_search(
            search_id=search.id,
            status=MedicationCandidateSearchStatus.READY,
            results=[_ready_result()],
        )
        await service.confirm_identification(
            prescription_version_medication_id=medication_id,
            candidate_search_result_id=finalized.results[0].id,
        )

    result = await service.ensure_matched_for_preflight(
        prescription_version_medication_ids=[first_medication_id, second_medication_id, first_medication_id]
    )

    assert result.prescription_version_medication_count == 2
    assert result.matched_identification_count == 2


async def test_preflight_rejects_when_any_medication_is_not_matched(db_session: AsyncSession) -> None:
    service = _service(db_session)
    matched_medication_id = uuid4()
    unmatched_medication_id = uuid4()
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=matched_medication_id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result()],
    )
    await service.confirm_identification(
        prescription_version_medication_id=matched_medication_id,
        candidate_search_result_id=finalized.results[0].id,
    )

    with pytest.raises(ApiError) as exc_info:
        await service.ensure_matched_for_preflight(
            prescription_version_medication_ids=[matched_medication_id, unmatched_medication_id]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "PRESCRIPTION_MEDICATION_IDENTIFICATION_INCOMPLETE"
    assert exc_info.value.details[0].field == "prescription_version_medication_ids"
    assert exc_info.value.details[0].reason == "MATCHED_IDENTIFICATION_REQUIRED"
    assert exc_info.value.details[0].rejected_value is None


async def test_search_cannot_have_multiple_displayed_or_selectable_results(db_session: AsyncSession) -> None:
    service = _service(db_session)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=uuid4(),
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=None,
        )
    ).search

    with pytest.raises(IntegrityError):
        await MedicationCandidateRepository(db_session).add_results(
            search=search,
            results=[
                _ready_result(result_rank=1),
                _ready_result(result_rank=2),
            ],
        )
