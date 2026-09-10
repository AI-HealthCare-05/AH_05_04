from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication_schedules import MedicationOccurrence, MedicationOccurrenceStatus, MedicationSchedule
from app.models.notifications import NotificationKind, NotificationRecord, NotificationStatus
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.repositories.medication_schedule_repository import as_utc_instant
from app.repositories.profile_ownership import owned_by_self


def _owned_notifications(user_id: UUID):
    return (
        select(NotificationRecord, MedicationOccurrence.scheduled_local_date)
        .join(MedicationOccurrence, MedicationOccurrence.id == NotificationRecord.occurrence_id)
        .join(MedicationSchedule, MedicationSchedule.id == MedicationOccurrence.medication_schedule_id)
        .join(
            PrescriptionVersionMedication,
            PrescriptionVersionMedication.id == MedicationSchedule.prescription_version_medication_id,
        )
        .join(PrescriptionVersion, PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id)
        .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
        .where(owned_by_self(Prescription.profile_id, user_id))
    )


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_owned(self, *, user_id: UUID, limit: int, offset: int) -> list[tuple[NotificationRecord, date]]:
        rows = await self.session.execute(
            _owned_notifications(user_id)
            .where(NotificationRecord.status == NotificationStatus.DELIVERED)
            .order_by(NotificationRecord.scheduled_at.desc(), NotificationRecord.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [(record, local_date) for record, local_date in rows]

    async def get_owned(
        self, *, user_id: UUID, notification_id: UUID, lock: bool = False
    ) -> tuple[NotificationRecord, date] | None:
        query = _owned_notifications(user_id).where(NotificationRecord.id == notification_id)
        if lock:
            query = query.with_for_update(of=NotificationRecord).execution_options(populate_existing=True)
        row = (await self.session.execute(query)).first()
        return None if row is None else (row[0], row[1])

    async def find_reminder(self, *, occurrence_id: UUID) -> NotificationRecord | None:
        return await self.session.scalar(
            select(NotificationRecord).where(
                NotificationRecord.occurrence_id == occurrence_id,
                NotificationRecord.kind == NotificationKind.REMINDER,
            )
        )

    async def create_if_absent(
        self, *, occurrence_id: UUID, kind: NotificationKind, scheduled_at: datetime
    ) -> NotificationRecord | None:
        row_id = await self.session.scalar(
            insert(NotificationRecord)
            .values(
                id=uuid4(),
                occurrence_id=occurrence_id,
                kind=kind,
                scheduled_at=as_utc_instant(scheduled_at, field="scheduled_at"),
                status=NotificationStatus.PENDING,
                attempt=0,
            )
            .on_conflict_do_nothing(constraint="uq_notification_occurrence_kind")
            .returning(NotificationRecord.id)
        )
        return None if row_id is None else await self.session.get(NotificationRecord, row_id)

    async def cancel_undelivered_for_occurrences(
        self, *, occurrence_ids: Sequence[UUID], cancelled_at: datetime
    ) -> None:
        if not occurrence_ids:
            return
        rows = await self.session.scalars(
            select(NotificationRecord)
            .where(
                NotificationRecord.occurrence_id.in_(occurrence_ids),
                NotificationRecord.status == NotificationStatus.PENDING,
            )
            .order_by(NotificationRecord.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        for row in rows:
            row.status = NotificationStatus.CANCELLED
            row.cancelled_at = as_utc_instant(cancelled_at, field="cancelled_at")
        await self.session.flush()

    async def generation_targets(self, *, now: datetime, limit: int) -> Sequence[MedicationOccurrence]:
        return (
            await self.session.scalars(
                select(MedicationOccurrence)
                .where(
                    MedicationOccurrence.status == MedicationOccurrenceStatus.PENDING,
                    MedicationOccurrence.confirmation_deadline_at > now,
                    ~exists(
                        select(NotificationRecord.id).where(
                            NotificationRecord.occurrence_id == MedicationOccurrence.id,
                            NotificationRecord.kind == NotificationKind.SCHEDULED,
                        )
                    ),
                )
                .order_by(MedicationOccurrence.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).all()

    async def publication_targets(self, *, now: datetime, limit: int) -> Sequence[MedicationOccurrence]:
        # Lock the occurrence first, matching Check-in and prescription invalidation.
        return (
            await self.session.scalars(
                select(MedicationOccurrence)
                .where(
                    exists(
                        select(NotificationRecord.id).where(
                            NotificationRecord.occurrence_id == MedicationOccurrence.id,
                            NotificationRecord.status == NotificationStatus.PENDING,
                            or_(
                                NotificationRecord.scheduled_at <= now,
                                MedicationOccurrence.status != MedicationOccurrenceStatus.PENDING,
                                MedicationOccurrence.confirmation_deadline_at <= now,
                            ),
                        )
                    )
                )
                .order_by(MedicationOccurrence.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).all()

    async def pending_for_update(self, *, occurrence_id: UUID) -> Sequence[NotificationRecord]:
        return (
            await self.session.scalars(
                select(NotificationRecord)
                .where(
                    NotificationRecord.occurrence_id == occurrence_id,
                    NotificationRecord.status == NotificationStatus.PENDING,
                )
                .order_by(NotificationRecord.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
