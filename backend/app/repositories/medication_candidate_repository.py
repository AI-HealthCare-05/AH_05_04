from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prescriptions import Medication, Prescription
from app.models.rag_candidate import (
    MedicationCandidateSearch,
    MedicationCandidateSearchResult,
    MedicationCandidateSearchStatus,
    MedicationIdentification,
    MedicationIdentificationSource,
    MedicationIdentificationStatus,
)
from app.repositories.profile_ownership import owned_by_self


@dataclass(frozen=True)
class MedicationCandidateResultCreate:
    product_id: UUID | None
    code_system: str | None
    canonical_code: str | None
    product_name: str | None
    strength_text: str | None
    dosage_form: str | None
    manufacturer_name: str | None
    product_status: str | None
    result_rank: int
    result_score: float
    result_method: str
    is_displayed: bool = False
    selection_eligible: bool = False


@dataclass(frozen=True)
class MedicationCandidateSelection:
    search: MedicationCandidateSearch
    result: MedicationCandidateSearchResult


class MedicationCandidateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_medication_for_candidate_search_owned(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
    ) -> Medication | None:
        """Candidate Search 입력으로 사용할 약품 row를 서버 소유권 경계에서 조회합니다."""
        result = await self.session.execute(
            select(Medication)
            .join(Prescription, Prescription.id == Medication.prescription_id)
            .where(
                Medication.id == prescription_version_medication_id,
                owned_by_self(Prescription.profile_id, user_id),
            )
            .with_for_update(of=Medication)
        )
        return result.scalar_one_or_none()

    async def get_active_search_for_update(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
    ) -> MedicationCandidateSearch | None:
        result = await self.session.execute(
            select(MedicationCandidateSearch)
            .join(Medication, Medication.id == MedicationCandidateSearch.prescription_version_medication_id)
            .join(Prescription, Prescription.id == Medication.prescription_id)
            .where(
                MedicationCandidateSearch.prescription_version_medication_id == prescription_version_medication_id,
                owned_by_self(Prescription.profile_id, user_id),
                MedicationCandidateSearch.status.in_(
                    (
                        MedicationCandidateSearchStatus.RUNNING,
                        MedicationCandidateSearchStatus.READY,
                    )
                ),
            )
            .with_for_update(of=MedicationCandidateSearch)
        )
        return result.scalar_one_or_none()

    async def get_search_for_update_owned(
        self,
        *,
        search_id: UUID,
        user_id: UUID,
    ) -> MedicationCandidateSearch | None:
        """다른 사용자의 Search를 조회·최종화하지 못하도록 소유권을 확인합니다.
        prescription_version_medication_id는 #169 이전까지 medication.id 값을 담는
        placeholder라 FK 제약은 없지만, 조회 시점에는 명시적 join 조건으로 같은
        경로(medication → prescription → profile)를 검증할 수 있습니다."""
        result = await self.session.execute(
            select(MedicationCandidateSearch)
            .join(Medication, Medication.id == MedicationCandidateSearch.prescription_version_medication_id)
            .join(Prescription, Prescription.id == Medication.prescription_id)
            .where(
                MedicationCandidateSearch.id == search_id,
                owned_by_self(Prescription.profile_id, user_id),
            )
            .with_for_update(of=MedicationCandidateSearch)
        )
        return result.scalar_one_or_none()

    async def get_result_selection_for_update_owned(
        self,
        *,
        candidate_search_result_id: UUID,
        user_id: UUID,
    ) -> MedicationCandidateSelection | None:
        search_result = await self.session.execute(
            select(MedicationCandidateSearch)
            .join(
                MedicationCandidateSearchResult,
                MedicationCandidateSearchResult.search_id == MedicationCandidateSearch.id,
            )
            .join(Medication, Medication.id == MedicationCandidateSearch.prescription_version_medication_id)
            .join(Prescription, Prescription.id == Medication.prescription_id)
            .where(
                MedicationCandidateSearchResult.id == candidate_search_result_id,
                owned_by_self(Prescription.profile_id, user_id),
            )
            .with_for_update(of=MedicationCandidateSearch)
        )
        search = search_result.scalar_one_or_none()
        if search is None:
            return None

        result = await self.session.execute(
            select(MedicationCandidateSearchResult)
            .where(
                MedicationCandidateSearchResult.id == candidate_search_result_id,
                MedicationCandidateSearchResult.search_id == search.id,
            )
            .with_for_update(of=MedicationCandidateSearchResult)
        )
        candidate_result = result.scalar_one_or_none()
        if candidate_result is None:
            return None
        return MedicationCandidateSelection(search=search, result=candidate_result)

    async def get_latest_matched_identification(
        self,
        *,
        prescription_version_medication_id: UUID,
    ) -> MedicationIdentification | None:
        result = await self.session.execute(
            select(MedicationIdentification)
            .where(
                MedicationIdentification.prescription_version_medication_id == prescription_version_medication_id,
                MedicationIdentification.status == MedicationIdentificationStatus.MATCHED,
            )
            .order_by(MedicationIdentification.created_at.desc(), MedicationIdentification.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_identification(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
    ) -> MedicationIdentification | None:
        result = await self.session.execute(
            select(MedicationIdentification)
            .join(Medication, Medication.id == MedicationIdentification.prescription_version_medication_id)
            .join(Prescription, Prescription.id == Medication.prescription_id)
            .where(
                MedicationIdentification.prescription_version_medication_id == prescription_version_medication_id,
                owned_by_self(Prescription.profile_id, user_id),
            )
            .order_by(MedicationIdentification.created_at.desc(), MedicationIdentification.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_matched_identifications_for_update(
        self,
        *,
        prescription_version_medication_ids: Sequence[UUID],
    ) -> list[MedicationIdentification]:
        if not prescription_version_medication_ids:
            return []

        result = await self.session.execute(
            select(MedicationIdentification)
            .where(
                MedicationIdentification.prescription_version_medication_id.in_(prescription_version_medication_ids),
                MedicationIdentification.status == MedicationIdentificationStatus.MATCHED,
            )
            .with_for_update(of=MedicationIdentification)
        )
        return list(result.scalars().all())

    async def create_search(
        self,
        *,
        prescription_version_medication_id: UUID,
        medication_name_snapshot: str,
        strength_text_snapshot: str | None,
        query_digest: str,
        runtime_release_bundle_id: UUID | None,
        candidate_index_version_id: UUID | None,
        expires_at: datetime | None,
    ) -> MedicationCandidateSearch:
        search = MedicationCandidateSearch(
            prescription_version_medication_id=prescription_version_medication_id,
            medication_name_snapshot=medication_name_snapshot,
            strength_text_snapshot=strength_text_snapshot,
            query_digest=query_digest,
            runtime_release_bundle_id=runtime_release_bundle_id,
            candidate_index_version_id=candidate_index_version_id,
            status=MedicationCandidateSearchStatus.RUNNING,
            candidate_count=0,
            displayed_candidate_count=0,
            expires_at=expires_at,
        )
        self.session.add(search)
        await self.session.flush()
        return search

    async def add_results(
        self,
        *,
        search: MedicationCandidateSearch,
        results: list[MedicationCandidateResultCreate],
    ) -> list[MedicationCandidateSearchResult]:
        created: list[MedicationCandidateSearchResult] = []
        for item in results:
            result = MedicationCandidateSearchResult(
                search_id=search.id,
                product_id=item.product_id,
                code_system=item.code_system,
                canonical_code=item.canonical_code,
                product_name=item.product_name,
                strength_text=item.strength_text,
                dosage_form=item.dosage_form,
                manufacturer_name=item.manufacturer_name,
                product_status=item.product_status,
                result_rank=item.result_rank,
                result_score=item.result_score,
                result_method=item.result_method,
                is_displayed=item.is_displayed,
                selection_eligible=item.selection_eligible,
            )
            self.session.add(result)
            created.append(result)
        await self.session.flush()
        return created

    async def finalize_search(
        self,
        *,
        search: MedicationCandidateSearch,
        status: MedicationCandidateSearchStatus,
        candidate_count: int,
        displayed_candidate_count: int,
        finalized_at: datetime,
        status_reason: str | None = None,
    ) -> MedicationCandidateSearch:
        search.status = status
        search.status_reason = status_reason
        search.candidate_count = candidate_count
        search.displayed_candidate_count = displayed_candidate_count
        search.finalized_at = finalized_at
        if status == MedicationCandidateSearchStatus.FAILED:
            search.failed_at = finalized_at
        await self.session.flush()
        return search

    async def invalidate_input_changed(
        self,
        *,
        search: MedicationCandidateSearch,
        invalidated_at: datetime,
    ) -> MedicationCandidateSearch:
        search.status = MedicationCandidateSearchStatus.INVALIDATED_INPUT_CHANGED
        search.invalidated_at = invalidated_at
        search.finalized_at = invalidated_at
        await self.session.flush()
        return search

    async def expire_search(
        self,
        *,
        search: MedicationCandidateSearch,
        expired_at: datetime,
    ) -> MedicationCandidateSearch:
        search.status = MedicationCandidateSearchStatus.EXPIRED
        search.finalized_at = expired_at
        await self.session.flush()
        return search

    async def consume_search(
        self,
        *,
        search: MedicationCandidateSearch,
        consumed_at: datetime,
    ) -> MedicationCandidateSearch:
        search.status = MedicationCandidateSearchStatus.CONSUMED
        search.consumed_at = consumed_at
        search.finalized_at = consumed_at
        await self.session.flush()
        return search

    async def invalidate_user_rejected(
        self,
        *,
        search: MedicationCandidateSearch,
        invalidated_at: datetime,
    ) -> MedicationCandidateSearch:
        search.status = MedicationCandidateSearchStatus.INVALIDATED_USER_REJECTED
        search.invalidated_at = invalidated_at
        search.finalized_at = invalidated_at
        await self.session.flush()
        return search

    async def create_matched_identification(
        self,
        *,
        prescription_version_medication_id: UUID,
        candidate_search: MedicationCandidateSearch,
        candidate_search_result: MedicationCandidateSearchResult,
        confirmed_at: datetime,
    ) -> MedicationIdentification:
        identification = MedicationIdentification(
            prescription_version_medication_id=prescription_version_medication_id,
            candidate_search_id=candidate_search.id,
            candidate_search_result_id=candidate_search_result.id,
            product_id=candidate_search_result.product_id,
            code_system=candidate_search_result.code_system,
            canonical_code=candidate_search_result.canonical_code,
            status=MedicationIdentificationStatus.MATCHED,
            source=MedicationIdentificationSource.USER_SELECTED,
            confirmed_at=confirmed_at,
        )
        self.session.add(identification)
        await self.session.flush()
        return identification

    async def create_unresolved_identification(
        self,
        *,
        prescription_version_medication_id: UUID,
        candidate_search: MedicationCandidateSearch,
        candidate_search_result: MedicationCandidateSearchResult,
        rejected_at: datetime,
    ) -> MedicationIdentification:
        identification = MedicationIdentification(
            prescription_version_medication_id=prescription_version_medication_id,
            candidate_search_id=candidate_search.id,
            candidate_search_result_id=candidate_search_result.id,
            product_id=None,
            code_system=None,
            canonical_code=None,
            status=MedicationIdentificationStatus.UNRESOLVED,
            source=MedicationIdentificationSource.USER_REJECTED,
            decision_reason="USER_REJECTED_DISPLAYED_CANDIDATE",
            rejected_at=rejected_at,
        )
        self.session.add(identification)
        await self.session.flush()
        return identification
