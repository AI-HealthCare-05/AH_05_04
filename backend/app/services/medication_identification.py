import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core import config
from app.core.errors import ApiError, ErrorDetail
from app.models.rag_candidate import (
    MedicationCandidateSearch,
    MedicationCandidateSearchResult,
    MedicationCandidateSearchStatus,
    MedicationIdentification,
)
from app.repositories.medication_candidate_repository import (
    MedicationCandidateRepository,
    MedicationCandidateResultCreate,
)

_FINALIZABLE_SEARCH_STATUSES = frozenset(
    {
        MedicationCandidateSearchStatus.READY,
        MedicationCandidateSearchStatus.AMBIGUOUS,
        MedicationCandidateSearchStatus.NO_CANDIDATE,
        MedicationCandidateSearchStatus.INGREDIENT_ONLY,
        MedicationCandidateSearchStatus.INVALID_INPUT,
        MedicationCandidateSearchStatus.FAILED,
    }
)


@dataclass(frozen=True)
class CandidateSearchRecordResult:
    search: MedicationCandidateSearch
    is_reused: bool


@dataclass(frozen=True)
class CandidateSearchFinalizeResult:
    search: MedicationCandidateSearch
    results: list[MedicationCandidateSearchResult]


@dataclass(frozen=True)
class MedicationIdentificationPreflightResult:
    prescription_version_medication_count: int
    matched_identification_count: int


class MedicationIdentificationService:
    def __init__(self, repository: MedicationCandidateRepository) -> None:
        self._repository = repository

    async def record_candidate_search(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
        query_digest: str,
        runtime_release_bundle_id: UUID | None,
        candidate_index_version_id: UUID | None,
        expires_at: datetime | None,
    ) -> CandidateSearchRecordResult:
        medication = await self._repository.get_medication_for_candidate_search_owned(
            prescription_version_medication_id=prescription_version_medication_id,
            user_id=user_id,
        )
        if medication is None:
            raise self._not_found_error(field="prescription_version_medication_id")

        active_search = await self._repository.get_active_search_for_update(
            prescription_version_medication_id=prescription_version_medication_id,
            user_id=user_id,
        )
        now = datetime.now(config.TIMEZONE)
        if active_search is not None and self._is_expired(active_search, now=now):
            await self._repository.expire_search(search=active_search, expired_at=now)
            active_search = None

        existing_identification = await self._repository.get_latest_identification(
            prescription_version_medication_id=prescription_version_medication_id,
            user_id=user_id,
        )
        if existing_identification is not None:
            raise self._context_stale_error(
                field="prescription_version_medication_id",
                reason="IDENTIFICATION_ALREADY_EXISTS",
            )

        if active_search is not None and self._has_same_search_context(
            active_search,
            query_digest=query_digest,
            runtime_release_bundle_id=runtime_release_bundle_id,
            candidate_index_version_id=candidate_index_version_id,
        ):
            return CandidateSearchRecordResult(search=active_search, is_reused=True)

        if active_search is not None:
            await self._repository.invalidate_input_changed(search=active_search, invalidated_at=now)

        search = await self._repository.create_search(
            prescription_version_medication_id=prescription_version_medication_id,
            medication_name_snapshot=medication.medication_name,
            strength_text_snapshot=medication.strength_text,
            query_digest=query_digest,
            runtime_release_bundle_id=runtime_release_bundle_id,
            candidate_index_version_id=candidate_index_version_id,
            expires_at=expires_at,
        )
        return CandidateSearchRecordResult(search=search, is_reused=False)

    async def finalize_candidate_search(
        self,
        *,
        search_id: UUID,
        user_id: UUID,
        status: MedicationCandidateSearchStatus,
        results: list[MedicationCandidateResultCreate],
        status_reason: str | None = None,
        finalized_at: datetime | None = None,
    ) -> CandidateSearchFinalizeResult:
        search = await self._repository.get_search_for_update_owned(search_id=search_id, user_id=user_id)
        if search is None:
            raise self._not_found_error(field="search_id")
        if search.status != MedicationCandidateSearchStatus.RUNNING:
            raise self._stale_error(field="search_id", reason="NOT_RUNNING")

        self._validate_finalize_payload(status=status, results=results, status_reason=status_reason)
        created_results = await self._repository.add_results(search=search, results=results)
        displayed_candidate_count = sum(1 for result in results if result.is_displayed)

        finalized = await self._repository.finalize_search(
            search=search,
            status=status,
            candidate_count=len(results),
            displayed_candidate_count=displayed_candidate_count,
            finalized_at=finalized_at or datetime.now(config.TIMEZONE),
            status_reason=status_reason,
        )
        return CandidateSearchFinalizeResult(search=finalized, results=created_results)

    async def confirm_identification(
        self,
        *,
        prescription_version_medication_id: UUID,
        candidate_search_result_id: UUID,
        user_id: UUID,
        confirmed_at: datetime | None = None,
    ) -> MedicationIdentification:
        selection = await self._repository.get_result_selection_for_update_owned(
            candidate_search_result_id=candidate_search_result_id, user_id=user_id
        )
        if selection is None:
            raise self._not_found_error(field="candidate_search_result_id")

        self._validate_selectable_result(
            search=selection.search,
            result=selection.result,
            prescription_version_medication_id=prescription_version_medication_id,
        )
        if (
            selection.result.product_id is None
            or selection.result.code_system is None
            or selection.result.canonical_code is None
        ):
            raise self._stale_error(field="candidate_search_result_id", reason="PRODUCT_ID_MISSING")

        existing = await self._repository.get_latest_matched_identification(
            prescription_version_medication_id=prescription_version_medication_id
        )
        if existing is not None:
            raise self._stale_error(field="prescription_version_medication_id", reason="ALREADY_MATCHED")

        now = confirmed_at or datetime.now(config.TIMEZONE)
        try:
            async with self._repository.session.begin_nested():
                identification = await self._repository.create_matched_identification(
                    prescription_version_medication_id=prescription_version_medication_id,
                    candidate_search=selection.search,
                    candidate_search_result=selection.result,
                    confirmed_at=now,
                )
                await self._repository.consume_search(search=selection.search, consumed_at=now)
        except IntegrityError as exc:
            raise self._stale_error(
                field="prescription_version_medication_id",
                reason="ALREADY_MATCHED",
            ) from exc
        return identification

    async def reject_identification(
        self,
        *,
        search_id: UUID,
        candidate_search_result_id: UUID,
        user_id: UUID,
        rejected_at: datetime | None = None,
    ) -> MedicationIdentification:
        selection = await self._repository.get_result_selection_for_update_owned(
            candidate_search_result_id=candidate_search_result_id, user_id=user_id
        )
        if selection is None or selection.search.id != search_id:
            raise self._not_found_error(field="candidate_search_result_id")

        self._validate_selectable_result(
            search=selection.search,
            result=selection.result,
            prescription_version_medication_id=selection.search.prescription_version_medication_id,
        )

        now = rejected_at or datetime.now(config.TIMEZONE)
        try:
            async with self._repository.session.begin_nested():
                identification = await self._repository.create_unresolved_identification(
                    prescription_version_medication_id=selection.search.prescription_version_medication_id,
                    candidate_search=selection.search,
                    candidate_search_result=selection.result,
                    rejected_at=now,
                )
                await self._repository.invalidate_user_rejected(search=selection.search, invalidated_at=now)
        except IntegrityError as exc:
            raise self._stale_error(field="search_id", reason="ALREADY_REJECTED") from exc
        return identification

    async def ensure_matched_for_preflight(
        self,
        *,
        prescription_version_medication_ids: Sequence[UUID],
    ) -> MedicationIdentificationPreflightResult:
        unique_medication_ids = list(dict.fromkeys(prescription_version_medication_ids))
        if not unique_medication_ids:
            raise self._invalid_state_error(
                field="prescription_version_medication_ids",
                reason="AT_LEAST_ONE_MEDICATION_REQUIRED",
            )

        identifications = await self._repository.get_matched_identifications_for_update(
            prescription_version_medication_ids=unique_medication_ids
        )
        matched_medication_ids = {item.prescription_version_medication_id for item in identifications}
        if len(matched_medication_ids) != len(unique_medication_ids):
            raise ApiError(
                status_code=409,
                code="PRESCRIPTION_MEDICATION_IDENTIFICATION_INCOMPLETE",
                message="약품 확인이 완료되지 않아 다음 단계를 진행할 수 없습니다.",
                details=[
                    ErrorDetail(
                        field="prescription_version_medication_ids",
                        reason="MATCHED_IDENTIFICATION_REQUIRED",
                    )
                ],
            )

        return MedicationIdentificationPreflightResult(
            prescription_version_medication_count=len(unique_medication_ids),
            matched_identification_count=len(matched_medication_ids),
        )

    @staticmethod
    def _has_same_search_context(
        search: MedicationCandidateSearch,
        *,
        query_digest: str,
        runtime_release_bundle_id: UUID | None,
        candidate_index_version_id: UUID | None,
    ) -> bool:
        return (
            search.query_digest == query_digest
            and search.runtime_release_bundle_id == runtime_release_bundle_id
            and search.candidate_index_version_id == candidate_index_version_id
        )

    @staticmethod
    def _validate_finalize_payload(
        *,
        status: MedicationCandidateSearchStatus,
        results: list[MedicationCandidateResultCreate],
        status_reason: str | None,
    ) -> None:
        displayed_count = sum(1 for result in results if result.is_displayed)
        selectable_count = sum(1 for result in results if result.selection_eligible)

        MedicationIdentificationService._validate_result_metadata(results)

        if status not in _FINALIZABLE_SEARCH_STATUSES:
            raise MedicationIdentificationService._invalid_state_error(
                field="status",
                reason="STATUS_NOT_FINALIZABLE",
            )

        if status == MedicationCandidateSearchStatus.READY:
            if status_reason is not None:
                raise MedicationIdentificationService._invalid_state_error(
                    field="status_reason",
                    reason="READY_STATUS_REASON_MUST_BE_NULL",
                )
            if displayed_count != 1 or selectable_count != 1:
                raise MedicationIdentificationService._invalid_state_error(
                    field="status",
                    reason="READY_REQUIRES_SINGLE_DISPLAYED_SELECTABLE_RESULT",
                )
            displayed = next(result for result in results if result.is_displayed)
            if (
                displayed.product_id is None
                or displayed.code_system is None
                or displayed.canonical_code is None
                or displayed.product_name is None
                or displayed.product_status is None
            ):
                raise MedicationIdentificationService._invalid_state_error(
                    field="candidate",
                    reason="READY_REQUIRES_PRODUCT_SNAPSHOT",
                )
            return

        if displayed_count > 0 or selectable_count > 0:
            raise MedicationIdentificationService._invalid_state_error(
                field="candidate",
                reason="NON_READY_RESULT_MUST_NOT_BE_DISPLAYED",
            )
        if status == MedicationCandidateSearchStatus.INGREDIENT_ONLY and status_reason != "PRODUCT_NAME_REQUIRED":
            raise MedicationIdentificationService._invalid_state_error(
                field="status_reason",
                reason="PRODUCT_NAME_REQUIRED",
            )
        if status == MedicationCandidateSearchStatus.INVALID_INPUT and status_reason != "INVALID_INPUT":
            raise MedicationIdentificationService._invalid_state_error(
                field="status_reason",
                reason="INVALID_INPUT",
            )

    @staticmethod
    def _validate_result_metadata(results: list[MedicationCandidateResultCreate]) -> None:
        for result in results:
            if not math.isfinite(result.result_score):
                raise MedicationIdentificationService._invalid_state_error(
                    field="result_score",
                    reason="FINITE_NUMBER_REQUIRED",
                )
            if not result.result_method.strip():
                raise MedicationIdentificationService._invalid_state_error(
                    field="result_method",
                    reason="NONBLANK_TEXT_REQUIRED",
                )

    @staticmethod
    def _is_expired(search: MedicationCandidateSearch, *, now: datetime) -> bool:
        return search.expires_at is not None and search.expires_at <= now

    @staticmethod
    def _validate_selectable_result(
        *,
        search: MedicationCandidateSearch,
        result: MedicationCandidateSearchResult,
        prescription_version_medication_id: UUID,
    ) -> None:
        if search.prescription_version_medication_id != prescription_version_medication_id:
            raise MedicationIdentificationService._stale_error(
                field="prescription_version_medication_id",
                reason="SEARCH_MEDICATION_MISMATCH",
            )
        if search.status != MedicationCandidateSearchStatus.READY:
            raise MedicationIdentificationService._stale_error(field="search_id", reason="SEARCH_NOT_READY")
        now = datetime.now(config.TIMEZONE)
        if search.expires_at is not None and search.expires_at <= now:
            raise MedicationIdentificationService._stale_error(field="search_id", reason="SEARCH_EXPIRED")
        if not result.is_displayed or not result.selection_eligible:
            raise MedicationIdentificationService._stale_error(
                field="candidate_search_result_id",
                reason="RESULT_NOT_SELECTABLE",
            )

    @staticmethod
    def _not_found_error(*, field: str) -> ApiError:
        return ApiError(
            status_code=404,
            code="CANDIDATE_SEARCH_NOT_FOUND",
            message="약품 후보 검색 결과를 찾을 수 없습니다.",
            details=[ErrorDetail(field=field, reason="NOT_FOUND")],
        )

    @staticmethod
    def _stale_error(*, field: str, reason: str) -> ApiError:
        return ApiError(
            status_code=409,
            code="CANDIDATE_SEARCH_STALE",
            message="현재 사용할 수 없는 약품 후보 검색 결과입니다. 최신 상태를 다시 확인해 주세요.",
            details=[ErrorDetail(field=field, reason=reason)],
        )

    @staticmethod
    def _context_stale_error(*, field: str, reason: str) -> ApiError:
        return ApiError(
            status_code=409,
            code="IDENTIFICATION_CONTEXT_STALE",
            message="약품 식별 기준이 최신 상태가 아닙니다. 최신 처방 상태를 다시 확인해 주세요.",
            details=[ErrorDetail(field=field, reason=reason)],
        )

    @staticmethod
    def _invalid_state_error(*, field: str, reason: str) -> ApiError:
        return ApiError(
            status_code=422,
            code="VALIDATION_FAILED",
            message="입력값을 확인해 주세요.",
            details=[ErrorDetail(field=field, reason=reason)],
        )
