"""Seed the isolated real-stack database for the #420 Report browser closeout."""

import asyncio
import os
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.core.db.databases import AsyncSessionFactory, close_database
from app.core.utils.security import hash_password
from app.models.medical_documents import DocumentType, MedicalDocument, UploadStatus
from app.models.medication_schedules import (
    MedicationCheckin,
    MedicationCheckinStatus,
    MedicationOccurrence,
    MedicationOccurrenceStatus,
    MedicationSchedule,
    MedicationScheduleEndMode,
    MedicationScheduleSource,
    MedicationScheduleStatus,
    MedicationScheduleTime,
)
from app.models.ocr import OcrJob, OcrStatus
from app.models.profiles import Profile, ProfileType
from app.models.users import User
from app.repositories.prescription_repository import PrescriptionRepository

REPORT_E2E_EMAIL = "report-420@example.com"
REPORT_E2E_PASSWORD = "Synthetic1!"
KST = ZoneInfo("Asia/Seoul")


def _require_isolated_local_database() -> None:
    if os.environ.get("ENV") != "local" or os.environ.get("DB_NAME") != "dosey_e2e":
        raise RuntimeError("Report E2E fixture requires the isolated local dosey_e2e database")


async def seed_report_round_trip_fixture() -> None:
    _require_isolated_local_database()
    now = datetime.now(UTC)
    local_day = now.astimezone(KST).date()
    next_local_midnight = datetime.combine(local_day + timedelta(days=1), time.min, KST).astimezone(UTC)

    user = User(
        id=uuid4(),
        email=REPORT_E2E_EMAIL,
        hashed_password=hash_password(REPORT_E2E_PASSWORD),
        name="Report 합성 사용자",
        is_active=True,
        is_admin=False,
    )
    profile = Profile(
        id=uuid4(),
        user_id=user.id,
        profile_type=ProfileType.SELF,
        display_name=user.name,
    )
    document = MedicalDocument(
        id=uuid4(),
        uploaded_by=user.id,
        profile_id=profile.id,
        document_type=DocumentType.PRESCRIPTION,
        original_file_name="report-420-synthetic.png",
        object_key=f"report-420/{uuid4()}.png",
        file_mime_type="image/png",
        file_size_bytes=1,
        upload_status=UploadStatus.UPLOADED,
    )
    ocr_job = OcrJob(
        id=uuid4(),
        document_id=document.id,
        ocr_status=OcrStatus.COMPLETED,
        started_at=now,
        completed_at=now,
    )
    medication_values = [
        {
            "medication_name": "리포트 합성약",
            "dose_value": Decimal("1"),
            "dose_unit": "정",
            "frequency_per_day": 2,
            "display_order": 1,
        }
    ]

    async with AsyncSessionFactory() as session:
        for row in (user, profile, document, ocr_job):
            session.add(row)
            await session.flush()

        prescription_repository = PrescriptionRepository(session)
        prescription = await prescription_repository.create_with_medications(
            document=document,
            source_ocr_job=ocr_job,
            prescribed_date=local_day,
            confirmed_at=now,
            medications=medication_values,
        )
        medications = await prescription_repository.get_version_medications(
            prescription_version_id=prescription.active_version_id
        )
        medication = medications[0]

        schedule = MedicationSchedule(
            id=uuid4(),
            prescription_version_medication_id=medication.id,
            start_local_date=local_day,
            end_mode=MedicationScheduleEndMode.OPEN_ENDED,
            end_local_date=None,
            source=MedicationScheduleSource.USER_CONFIRMED,
            status=MedicationScheduleStatus.ACTIVE,
            revision=1,
        )
        session.add(schedule)
        await session.flush()
        schedule_times = [
            MedicationScheduleTime(
                id=uuid4(),
                medication_schedule_id=schedule.id,
                schedule_revision=1,
                local_time=time(hour),
            )
            for hour in (8, 20)
        ]
        session.add_all(schedule_times)
        await session.flush()
        scheduled_values = [
            datetime.combine(local_day, schedule_time.local_time, KST).astimezone(UTC)
            for schedule_time in schedule_times
        ]
        occurrences = [
            MedicationOccurrence(
                id=uuid4(),
                medication_schedule_id=schedule.id,
                medication_schedule_time_id=schedule_time.id,
                schedule_revision=1,
                scheduled_local_date=local_day,
                scheduled_at=scheduled_at,
                confirmation_deadline_at=max(scheduled_at + timedelta(hours=4), next_local_midnight),
                status=MedicationOccurrenceStatus.CLOSED,
            )
            for schedule_time, scheduled_at in zip(schedule_times, scheduled_values, strict=True)
        ]
        session.add_all(occurrences)
        await session.flush()
        checkins = [
            MedicationCheckin(
                id=uuid4(),
                occurrence_id=occurrences[0].id,
                status=MedicationCheckinStatus.TAKEN,
                taken_at=scheduled_values[0],
                revision=1,
            ),
            MedicationCheckin(
                id=uuid4(),
                occurrence_id=occurrences[1].id,
                status=MedicationCheckinStatus.UNCONFIRMED,
                taken_at=None,
                revision=1,
            ),
        ]
        session.add_all(checkins)
        await session.commit()


async def _main() -> None:
    try:
        await seed_report_round_trip_fixture()
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(_main())
