from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.core.errors import ApiError, ErrorDetail
from app.models.medication_schedules import (
    MedicationCheckin,
    MedicationCheckinStatus,
    MedicationOccurrenceStatus,
)
from app.repositories.medication_checkin_repository import MedicationCheckinRepository
from app.repositories.medication_schedule_repository import as_utc_instant

DEFAULT_UNCONFIRMED_BATCH_SIZE = 500


class CheckinRevisionInvalidationPort(Protocol):
    """Track C가 이전 NOT_TAKEN revision의 파생 결과를 동기 무효화하는 경계."""

    async def invalidate_for_checkin_revision(
        self,
        *,
        checkin_id: UUID,
        invalidated_revision: int,
        invalidated_at: datetime,
    ) -> None: ...


class NoopCheckinRevisionInvalidation:
    """Track C 저장 모델이 연결되기 전 사용하는 명시적 no-op adapter."""

    async def invalidate_for_checkin_revision(
        self,
        *,
        checkin_id: UUID,
        invalidated_revision: int,
        invalidated_at: datetime,
    ) -> None:
        return None


@dataclass(frozen=True)
class MedicationCheckinResult:
    checkin_id: UUID
    occurrence_id: UUID
    status: MedicationCheckinStatus
    taken_at: datetime | None
    revision: int
    corrected: bool


@dataclass(frozen=True)
class UnconfirmedGenerationResult:
    processed_count: int
    duplicate_count: int


def _result(checkin: MedicationCheckin) -> MedicationCheckinResult:
    return MedicationCheckinResult(
        checkin_id=checkin.id,
        occurrence_id=checkin.occurrence_id,
        status=checkin.status,
        taken_at=checkin.taken_at,
        revision=checkin.revision,
        corrected=checkin.revision > 1,
    )


def _validate_user_input(
    *,
    status: MedicationCheckinStatus,
    taken_at: datetime | None,
) -> datetime | None:
    if status == MedicationCheckinStatus.UNCONFIRMED:
        raise ApiError(
            status_code=422,
            code="CHECKIN_STATUS_NOT_USER_SETTABLE",
            message="미확인 상태는 응답 기한이 지난 경우에만 자동 생성됩니다.",
            details=[ErrorDetail(field="status", reason="NOT_USER_SETTABLE")],
        )
    if taken_at is not None and status != MedicationCheckinStatus.TAKEN:
        raise ApiError(
            status_code=422,
            code="VALIDATION_FAILED",
            message="복용 시각은 복용 완료 상태에서만 입력할 수 있습니다.",
            details=[ErrorDetail(field="taken_at", reason="NOT_ALLOWED_FOR_STATUS")],
        )
    if taken_at is None:
        return None
    try:
        return as_utc_instant(taken_at, field="taken_at")
    except ValueError:
        raise ApiError(
            status_code=422,
            code="VALIDATION_FAILED",
            message="복용 시각에 시간대 정보가 필요합니다.",
            details=[ErrorDetail(field="taken_at", reason="TIMEZONE_REQUIRED")],
        ) from None


class MedicationCheckinService:
    def __init__(
        self,
        repository: MedicationCheckinRepository,
        *,
        revision_invalidation: CheckinRevisionInvalidationPort,
    ) -> None:
        self._repository = repository
        self._revision_invalidation = revision_invalidation

    async def put_owned(
        self,
        *,
        occurrence_id: UUID,
        user_id: UUID,
        status: MedicationCheckinStatus,
        taken_at: datetime | None,
        expected_revision: int,
        changed_at: datetime | None = None,
    ) -> MedicationCheckinResult:
        taken_at_utc = _validate_user_input(status=status, taken_at=taken_at)
        occurrence = await self._repository.lock_occurrence_owned(occurrence_id=occurrence_id, user_id=user_id)
        if occurrence is None:
            raise ApiError(
                status_code=404,
                code="MEDICATION_OCCURRENCE_NOT_FOUND",
                message="복약 일정을 찾을 수 없습니다.",
            )
        if occurrence.status == MedicationOccurrenceStatus.CANCELLED:
            raise ApiError(
                status_code=409,
                code="OCCURRENCE_CANCELLED",
                message="취소된 복약 일정에는 기록을 남길 수 없습니다.",
            )

        current = await self._repository.get_current_for_update(occurrence_id=occurrence.id)
        if current is None:
            if expected_revision != 0:
                raise self._revision_conflict()
            created = await self._repository.create_if_absent(
                occurrence_id=occurrence.id,
                status=status,
                taken_at=taken_at_utc,
            )
            if created is None:
                raise self._revision_conflict()
            await self._repository.close_occurrence(occurrence=occurrence)
            return _result(created)

        if expected_revision != current.revision:
            raise self._revision_conflict()

        now_utc = as_utc_instant(changed_at or datetime.now(UTC), field="changed_at")
        invalidated_revision = current.revision if current.status == MedicationCheckinStatus.NOT_TAKEN else None
        await self._repository.correct(
            checkin=current,
            status=status,
            taken_at=taken_at_utc,
            changed_by=user_id,
            changed_at=now_utc,
        )
        if invalidated_revision is not None:
            await self._revision_invalidation.invalidate_for_checkin_revision(
                checkin_id=current.id,
                invalidated_revision=invalidated_revision,
                invalidated_at=now_utc,
            )
        return _result(current)

    async def list_unconfirmed_owned(self, *, user_id: UUID, limit: int = 100) -> Sequence[MedicationCheckinResult]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        rows = await self._repository.list_unconfirmed_owned(user_id=user_id, limit=limit)
        return tuple(_result(row) for row in rows)

    @staticmethod
    def _revision_conflict() -> ApiError:
        return ApiError(
            status_code=409,
            code="CHECKIN_REVISION_CONFLICT",
            message="복약 기록이 이미 변경되었습니다. 최신 기록을 다시 확인해 주세요.",
            details=[ErrorDetail(field="expected_revision", reason="CURRENT_REVISION_MISMATCH")],
        )


class MedicationCheckinDeadlineScheduler:
    """응답 기한이 지난 결과 없는 occurrence를 UNCONFIRMED로 닫는다."""

    def __init__(self, repository: MedicationCheckinRepository) -> None:
        self._repository = repository

    async def generate_unconfirmed(
        self,
        *,
        now: datetime,
        batch_size: int = DEFAULT_UNCONFIRMED_BATCH_SIZE,
    ) -> UnconfirmedGenerationResult:
        if batch_size < 1 or batch_size > DEFAULT_UNCONFIRMED_BATCH_SIZE:
            raise ValueError(f"batch_size must be between 1 and {DEFAULT_UNCONFIRMED_BATCH_SIZE}")
        now_utc = as_utc_instant(now, field="now")
        occurrences = await self._repository.list_due_occurrences_for_update(
            deadline_at=now_utc,
            batch_size=batch_size,
        )
        processed_count = 0
        duplicate_count = 0
        for occurrence in occurrences:
            created = await self._repository.create_if_absent(
                occurrence_id=occurrence.id,
                status=MedicationCheckinStatus.UNCONFIRMED,
                taken_at=None,
            )
            if created is None:
                duplicate_count += 1
                continue
            await self._repository.close_occurrence(occurrence=occurrence)
            processed_count += 1
        return UnconfirmedGenerationResult(
            processed_count=processed_count,
            duplicate_count=duplicate_count,
        )
