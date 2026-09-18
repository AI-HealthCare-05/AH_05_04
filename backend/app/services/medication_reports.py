from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.errors import ApiError
from app.dtos.medication_reports import (
    MedicationReportCheckin,
    MedicationReportCounts,
    MedicationReportData,
    MedicationReportRate,
    MedicationReportRecord,
    MedicationReportResponse,
    MedicationReportTimeSlot,
)
from app.repositories.medication_report_repository import MedicationReportRepository

SEOUL = ZoneInfo("Asia/Seoul")


def _report_time_slot(scheduled_at: datetime) -> MedicationReportTimeSlot:
    hour = scheduled_at.astimezone(SEOUL).hour
    if 5 <= hour < 11:
        return "BREAKFAST"
    if 11 <= hour < 15:
        return "LUNCH"
    if 15 <= hour < 21:
        return "DINNER"
    return "BEDTIME"


def _rate(numerator: int, denominator: int) -> MedicationReportRate:
    percentage = (
        float((Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
        if denominator
        else None
    )
    return MedicationReportRate(numerator=numerator, denominator=denominator, percentage=percentage)


class MedicationReportService:
    def __init__(self, repository: MedicationReportRepository) -> None:
        self.repository = repository

    async def report(
        self, *, user_id: UUID, period_days: int, end_date: date | None = None, now: datetime | None = None
    ) -> MedicationReportResponse:
        as_of = now if now is not None else datetime.now(UTC)
        today = as_of.astimezone(SEOUL).date()
        end_date = end_date if end_date is not None else today
        if period_days not in (7, 30) or end_date > today or end_date.toordinal() < period_days:
            raise ApiError(status_code=422, code="VALIDATION_FAILED", message="조회 기간을 확인해 주세요.")
        start_date = end_date - timedelta(days=period_days - 1)
        rows = await self.repository.list_owned(user_id=user_id, start_date=start_date, end_date=end_date)
        records = []
        counts = MedicationReportCounts()
        overdue_pending_count = 0
        for occurrence, medication, checkin in rows:
            records.append(
                MedicationReportRecord(
                    occurrence_id=occurrence.id,
                    prescription_version_id=medication.prescription_version_id,
                    prescription_version_medication_id=medication.id,
                    scheduled_local_date=occurrence.scheduled_local_date,
                    scheduled_at=occurrence.scheduled_at,
                    confirmation_deadline_at=occurrence.confirmation_deadline_at,
                    status=occurrence.status,
                    medication_name=medication.medication_name,
                    strength_text=medication.strength_text,
                    time_slot=_report_time_slot(occurrence.scheduled_at),
                    updated_at=max(occurrence.updated_at, checkin.updated_at) if checkin else occurrence.updated_at,
                    checkin=MedicationReportCheckin(
                        checkin_id=checkin.id,
                        occurrence_id=occurrence.id,
                        status=checkin.status,
                        taken_at=checkin.taken_at,
                        revision=checkin.revision,
                        corrected=checkin.revision > 1,
                        updated_at=checkin.updated_at,
                    )
                    if checkin
                    else None,
                )
            )
            if occurrence.status == "CANCELLED":
                counts.cancelled_count += 1
            elif checkin is not None:
                if checkin.status == "TAKEN":
                    counts.taken_count += 1
                elif checkin.status == "NOT_TAKEN":
                    counts.not_taken_count += 1
                else:
                    counts.unconfirmed_count += 1
            elif occurrence.status == "PENDING":
                counts.pending_count += 1
                overdue_pending_count += occurrence.confirmation_deadline_at <= as_of
        confirmed = counts.taken_count + counts.not_taken_count
        return MedicationReportResponse(
            data=MedicationReportData(
                period_days=7 if period_days == 7 else 30,
                start_date=start_date,
                end_date=end_date,
                as_of=as_of,
                counts=counts,
                overdue_pending_count=overdue_pending_count,
                adherence_rate=_rate(counts.taken_count, confirmed),
                confirmation_rate=_rate(confirmed, confirmed + counts.unconfirmed_count),
                records=records,
            )
        )
