from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from app.core.errors import ApiError
from app.dtos.notifications import (
    CreateReminderRequest,
    NotificationData,
    NotificationListData,
    NotificationListResponse,
    NotificationResponse,
    ReminderData,
    ReminderResponse,
)
from app.models.medication_schedules import MedicationOccurrenceStatus
from app.models.notifications import NotificationKind, NotificationRecord, NotificationStatus
from app.repositories.medication_checkin_repository import MedicationCheckinRepository
from app.repositories.medication_schedule_repository import MedicationScheduleRepository, as_utc_instant
from app.repositories.notification_repository import NotificationRepository
from app.services.idempotency import SyncMutationIdempotencyService, SyncMutationResult

NOTIFICATION_READ_OPERATION_ID = "notification.read"
REMINDER_CREATE_OPERATION_ID = "medication-reminder.create"


def _now() -> datetime:
    return datetime.now(UTC)


def _not_found() -> ApiError:
    return ApiError(status_code=404, code="NOTIFICATION_NOT_FOUND", message="알림을 찾을 수 없습니다.")


def _data(record: NotificationRecord, local_date: date) -> NotificationData:
    assert record.delivered_at is not None
    return NotificationData(
        id=record.id,
        occurrence_id=record.occurrence_id,
        occurrence_local_date=local_date,
        kind=record.kind.value,
        scheduled_at=record.scheduled_at,
        status="DELIVERED",
        delivered_at=record.delivered_at,
        read_at=record.read_at,
    )


class NotificationService:
    def __init__(
        self,
        repository: NotificationRepository,
        idempotency: SyncMutationIdempotencyService,
        *,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._repository = repository
        self._idempotency = idempotency
        self._clock = clock

    async def list_owned(self, *, user_id: UUID, limit: int, offset: int) -> NotificationListResponse:
        rows = await self._repository.list_owned(user_id=user_id, limit=limit + 1, offset=offset)
        return NotificationListResponse(
            data=NotificationListData(
                items=[_data(record, local_date) for record, local_date in rows[:limit]],
                next_offset=offset + limit if len(rows) > limit else None,
            )
        )

    async def read(self, *, user_id: UUID, notification_id: UUID, idempotency_key: str) -> SyncMutationResult:
        row = await self._repository.get_owned(user_id=user_id, notification_id=notification_id)
        if row is None or row[0].status != NotificationStatus.DELIVERED:
            raise _not_found()

        async def mutate() -> dict[str, Any]:
            row = await self._repository.get_owned(user_id=user_id, notification_id=notification_id, lock=True)
            if row is None or row[0].status != NotificationStatus.DELIVERED:
                raise _not_found()
            record, local_date = row
            assert record.delivered_at is not None
            if record.read_at is None:
                record.read_at = max(as_utc_instant(self._clock(), field="now"), record.delivered_at)
                await self._repository.session.flush()
            return NotificationResponse(data=_data(record, local_date)).model_dump(mode="json")

        return await self._idempotency.execute(
            user_id=user_id,
            operation_id=NOTIFICATION_READ_OPERATION_ID,
            parent_resource_id=notification_id,
            idempotency_key=idempotency_key,
            fingerprint={},
            success_status=200,
            mutate=mutate,
        )

    async def create_reminder(
        self, *, user_id: UUID, occurrence_id: UUID, request: CreateReminderRequest, idempotency_key: str
    ) -> SyncMutationResult:
        session = self._repository.session
        occurrence = await MedicationScheduleRepository(session).get_occurrence_owned(
            occurrence_id=occurrence_id,
            user_id=user_id,
        )
        if occurrence is None:
            raise ApiError(
                status_code=404, code="MEDICATION_OCCURRENCE_NOT_FOUND", message="복약 일정을 찾을 수 없습니다."
            )

        async def mutate() -> dict[str, Any]:
            occurrence = await MedicationCheckinRepository(session).lock_occurrence_owned(
                occurrence_id=occurrence_id,
                user_id=user_id,
            )
            if occurrence is None:
                raise ApiError(
                    status_code=404, code="MEDICATION_OCCURRENCE_NOT_FOUND", message="복약 일정을 찾을 수 없습니다."
                )
            now = as_utc_instant(self._clock(), field="now")
            if occurrence.status == MedicationOccurrenceStatus.CANCELLED:
                raise ApiError(status_code=409, code="OCCURRENCE_CANCELLED", message="취소된 복약 일정입니다.")
            if occurrence.status != MedicationOccurrenceStatus.PENDING or now >= occurrence.confirmation_deadline_at:
                raise ApiError(status_code=409, code="REMINDER_NOT_ALLOWED", message="재알림을 설정할 수 없습니다.")
            existing = await self._repository.find_reminder(occurrence_id=occurrence_id)
            if existing is not None:
                code = (
                    "REMINDER_ALREADY_SCHEDULED"
                    if existing.status == NotificationStatus.PENDING
                    else "REMINDER_LIMIT_REACHED"
                )
                raise ApiError(status_code=409, code=code, message="이미 재알림을 요청한 복약 일정입니다.")
            scheduled_at = as_utc_instant(request.scheduled_at, field="scheduled_at")
            if not max(now, occurrence.scheduled_at) < scheduled_at < occurrence.confirmation_deadline_at:
                raise ApiError(status_code=422, code="VALIDATION_FAILED", message="재알림 시간을 확인해 주세요.")
            record = await self._repository.create_if_absent(
                occurrence_id=occurrence_id,
                kind=NotificationKind.REMINDER,
                scheduled_at=scheduled_at,
            )
            assert record is not None  # Every reminder creator holds the occurrence lock.
            return ReminderResponse(
                data=ReminderData(
                    id=record.id,
                    occurrence_id=occurrence_id,
                    scheduled_at=record.scheduled_at,
                )
            ).model_dump(mode="json")

        return await self._idempotency.execute(
            user_id=user_id,
            operation_id=REMINDER_CREATE_OPERATION_ID,
            parent_resource_id=occurrence_id,
            idempotency_key=idempotency_key,
            fingerprint=request.model_dump(mode="json"),
            success_status=201,
            mutate=mutate,
        )


@dataclass(frozen=True)
class NotificationBatchResult:
    created_count: int
    delivered_count: int
    cancelled_count: int


class NotificationScheduler:
    def __init__(self, repository: NotificationRepository) -> None:
        self._repository = repository

    async def generate_once(self, *, now: datetime, limit: int = 500) -> NotificationBatchResult:
        if limit < 1:
            raise ValueError("limit must be positive")
        now = as_utc_instant(now, field="now")
        created = delivered = cancelled = 0
        # Both phases visit occurrence IDs in order. The command executes each phase
        # in its own transaction so it never acquires a lower occurrence lock later.
        for occurrence in await self._repository.generation_targets(now=now, limit=limit):
            record = await self._repository.create_if_absent(
                occurrence_id=occurrence.id,
                kind=NotificationKind.SCHEDULED,
                scheduled_at=occurrence.scheduled_at,
            )
            created += int(record is not None)
        return NotificationBatchResult(created, delivered, cancelled)

    async def publish_once(self, *, now: datetime, limit: int = 500) -> NotificationBatchResult:
        if limit < 1:
            raise ValueError("limit must be positive")
        now = as_utc_instant(now, field="now")
        delivered = cancelled = 0
        for occurrence in await self._repository.publication_targets(now=now, limit=limit):
            for record in await self._repository.pending_for_update(occurrence_id=occurrence.id):
                if (
                    occurrence.status != MedicationOccurrenceStatus.PENDING
                    or now >= occurrence.confirmation_deadline_at
                ):
                    record.status = NotificationStatus.CANCELLED
                    record.cancelled_at = now
                    cancelled += 1
                elif record.scheduled_at <= now:
                    record.status = NotificationStatus.DELIVERED
                    record.attempt = 1
                    record.delivered_at = now
                    delivered += 1
        await self._repository.session.flush()
        return NotificationBatchResult(0, delivered, cancelled)
