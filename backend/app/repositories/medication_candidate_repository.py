from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.rag_candidate import (
    MedicationCandidateSearch,
    MedicationCandidateSearchResult,
    MedicationCandidateSearchStatus,
    MedicationIdentification,
    MedicationIdentificationSource,
    MedicationIdentificationStatus,
)
from app.repositories.prescription_integrity import require_verified_version
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

    async def _lock_active_prescription_for_medication_owned(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
    ) -> Prescription | None:
        prescription = await self.session.scalar(
            select(Prescription)
            .join(PrescriptionVersion, PrescriptionVersion.prescription_id == Prescription.id)
            .join(
                PrescriptionVersionMedication,
                PrescriptionVersionMedication.prescription_version_id == PrescriptionVersion.id,
            )
            .where(
                PrescriptionVersionMedication.id == prescription_version_medication_id,
                Prescription.active_version_id == PrescriptionVersion.id,
                owned_by_self(Prescription.profile_id, user_id),
            )
            .with_for_update(of=Prescription)
        )
        if prescription is not None:
            await require_verified_version(self.session, prescription.active_version_id)
        return prescription

    async def get_medication_for_candidate_search_owned(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
    ) -> PrescriptionVersionMedication | None:
        """Candidate Search 입력으로 사용할 약품 row를 서버 소유권 경계에서 조회합니다."""
        prescription = await self._lock_active_prescription_for_medication_owned(
            prescription_version_medication_id=prescription_version_medication_id,
            user_id=user_id,
        )
        if prescription is None:
            return None
        result = await self.session.execute(
            select(PrescriptionVersionMedication)
            .where(
                PrescriptionVersionMedication.id == prescription_version_medication_id,
                PrescriptionVersionMedication.prescription_version_id == prescription.active_version_id,
            )
            .with_for_update(of=PrescriptionVersionMedication)
        )
        return result.scalar_one_or_none()

    async def get_medication_owned(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
    ) -> PrescriptionVersionMedication | None:
        """조회(GET) 전용 읽기 경로입니다. 쓰기 경로(get_medication_for_candidate_search_owned)와
        달리 행을 잠그지 않습니다."""
        result = await self.session.execute(
            select(PrescriptionVersionMedication)
            .join(
                PrescriptionVersion,
                PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id,
            )
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .where(
                PrescriptionVersionMedication.id == prescription_version_medication_id,
                Prescription.active_version_id == PrescriptionVersion.id,
                owned_by_self(Prescription.profile_id, user_id),
            )
        )
        medication = result.scalar_one_or_none()
        if medication is not None:
            await require_verified_version(self.session, medication.prescription_version_id)
        return medication

    async def get_latest_search_for_medication(
        self,
        *,
        prescription_version_medication_id: UUID,
    ) -> MedicationCandidateSearch | None:
        """조회(GET) 전용 읽기 경로입니다. 소유권은 호출자가 get_medication_owned로 먼저 확인합니다."""
        result = await self.session.execute(
            select(MedicationCandidateSearch)
            .where(MedicationCandidateSearch.prescription_version_medication_id == prescription_version_medication_id)
            .order_by(MedicationCandidateSearch.created_at.desc(), MedicationCandidateSearch.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_displayed_result_for_search(
        self,
        *,
        search_id: UUID,
    ) -> MedicationCandidateSearchResult | None:
        """READY Search에서 표시 중인 단일 Result를 조회합니다(uq_medication_candidate_result_displayed로
        최대 1건이 보장됩니다)."""
        result = await self.session.execute(
            select(MedicationCandidateSearchResult).where(
                MedicationCandidateSearchResult.search_id == search_id,
                MedicationCandidateSearchResult.is_displayed.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_search_for_update(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
    ) -> MedicationCandidateSearch | None:
        prescription = await self._lock_active_prescription_for_medication_owned(
            prescription_version_medication_id=prescription_version_medication_id,
            user_id=user_id,
        )
        if prescription is None:
            return None
        result = await self.session.execute(
            select(MedicationCandidateSearch)
            .where(
                MedicationCandidateSearch.prescription_version_medication_id == prescription_version_medication_id,
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
        실제 PVM FK 경로와 활성 version을 함께 검증합니다."""
        medication_id = await self.session.scalar(
            select(MedicationCandidateSearch.prescription_version_medication_id).where(
                MedicationCandidateSearch.id == search_id
            )
        )
        if medication_id is None:
            return None
        prescription = await self._lock_active_prescription_for_medication_owned(
            prescription_version_medication_id=medication_id,
            user_id=user_id,
        )
        if prescription is None:
            return None
        result = await self.session.execute(
            select(MedicationCandidateSearch)
            .where(
                MedicationCandidateSearch.id == search_id,
                MedicationCandidateSearch.prescription_version_medication_id == medication_id,
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
        medication_id = await self.session.scalar(
            select(MedicationCandidateSearch.prescription_version_medication_id)
            .join(
                MedicationCandidateSearchResult,
                MedicationCandidateSearchResult.search_id == MedicationCandidateSearch.id,
            )
            .where(MedicationCandidateSearchResult.id == candidate_search_result_id)
        )
        if medication_id is None:
            return None
        prescription = await self._lock_active_prescription_for_medication_owned(
            prescription_version_medication_id=medication_id,
            user_id=user_id,
        )
        if prescription is None:
            return None
        search_result = await self.session.execute(
            select(MedicationCandidateSearch)
            .join(
                MedicationCandidateSearchResult,
                MedicationCandidateSearchResult.search_id == MedicationCandidateSearch.id,
            )
            .where(
                MedicationCandidateSearchResult.id == candidate_search_result_id,
                MedicationCandidateSearch.prescription_version_medication_id == medication_id,
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
            .join(
                PrescriptionVersionMedication,
                PrescriptionVersionMedication.id == MedicationIdentification.prescription_version_medication_id,
            )
            .join(
                PrescriptionVersion,
                PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id,
            )
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .where(
                MedicationIdentification.prescription_version_medication_id == prescription_version_medication_id,
                Prescription.active_version_id == PrescriptionVersion.id,
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

    async def get_active_version_medication_ids_for_update(
        self,
        *,
        prescription_version_id: UUID,
    ) -> list[UUID] | None:
        prescription = await self.session.scalar(
            select(Prescription)
            .join(PrescriptionVersion, PrescriptionVersion.prescription_id == Prescription.id)
            .where(
                PrescriptionVersion.id == prescription_version_id,
                Prescription.active_version_id == prescription_version_id,
            )
            .with_for_update(of=Prescription)
        )
        if prescription is None:
            return None
        await require_verified_version(self.session, prescription_version_id)
        result = await self.session.execute(
            select(PrescriptionVersionMedication.id)
            .where(PrescriptionVersionMedication.prescription_version_id == prescription_version_id)
            .order_by(PrescriptionVersionMedication.display_order)
            .with_for_update(of=PrescriptionVersionMedication)
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

    async def assemble_and_finalize_search(
        self,
        *,
        search: MedicationCandidateSearch,
        results: list[MedicationCandidateResultCreate],
        status: MedicationCandidateSearchStatus,
        finalized_at: datetime,
        status_reason: str | None = None,
    ) -> tuple[MedicationCandidateSearch, list[MedicationCandidateSearchResult]]:
        """결과 저장과 실제 집계 최종화를 묶어 실패 시 부분 결과를 남기지 않습니다."""
        async with self.session.begin_nested():
            created = await self.add_results(search=search, results=results)
            finalized = await self.finalize_search(
                search=search,
                status=status,
                candidate_count=len(results),
                displayed_candidate_count=sum(item.is_displayed for item in results),
                finalized_at=finalized_at,
                status_reason=status_reason,
            )
            return finalized, created

    async def add_results(
        self,
        *,
        search: MedicationCandidateSearch,
        results: list[MedicationCandidateResultCreate],
    ) -> list[MedicationCandidateSearchResult]:
        await self._lock_running_search(search_id=search.id)
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
        await self._lock_running_search(search_id=search.id)
        # 입력 목록이 아니라 같은 transaction에 실제 저장된 전체 결과를 검사합니다.
        await self.session.flush()
        counts = await self.session.execute(
            select(
                func.count(MedicationCandidateSearchResult.id),
                func.count(MedicationCandidateSearchResult.id).filter(
                    MedicationCandidateSearchResult.is_displayed.is_(True)
                ),
            ).where(MedicationCandidateSearchResult.search_id == search.id)
        )
        actual_count, actual_displayed = counts.one()
        if (candidate_count, displayed_candidate_count) != (actual_count, actual_displayed):
            raise ValueError("Candidate result counts do not match persisted rows")
        search.status = status
        search.status_reason = status_reason
        search.candidate_count = candidate_count
        search.displayed_candidate_count = displayed_candidate_count
        search.finalized_at = finalized_at
        if status == MedicationCandidateSearchStatus.FAILED:
            search.failed_at = finalized_at
        await self.session.flush()
        return search

    async def _lock_running_search(self, *, search_id: UUID) -> None:
        # Service가 상위 Prescription/PVM 잠금을 획득한 뒤 호출합니다.
        # 직접 Repository를 쓰는 경로도 완료된 Search에 결과를 추가할 수 없습니다.
        current_status = await self.session.scalar(
            select(MedicationCandidateSearch.status).where(MedicationCandidateSearch.id == search_id).with_for_update()
        )
        if current_status != MedicationCandidateSearchStatus.RUNNING:
            raise ValueError("Candidate search must be running before result assembly")

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
