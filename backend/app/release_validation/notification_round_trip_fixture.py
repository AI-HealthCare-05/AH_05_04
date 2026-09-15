"""Seed an isolated synthetic Notification round-trip for browser validation."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select

from app.core import config
from app.core.db.databases import AsyncSessionFactory, close_database
from app.core.utils.security import hash_password
from app.models.medical_documents import DocumentType, MedicalDocument, UploadStatus
from app.models.medication_schedules import MedicationScheduleEndMode, MedicationScheduleSource
from app.models.ocr import OcrJob, OcrStatus
from app.models.prescriptions import PrescriptionVersionMedication
from app.models.profiles import Profile, ProfileType
from app.repositories.medication_schedule_repository import MedicationScheduleRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.repositories.user_repository import UserRepository
from app.services.notifications import NotificationScheduler
from provider_contracts.observability import DeploymentEnvironment

NOTIFICATION_E2E_EMAIL = "notification-421@example.com"
NOTIFICATION_E2E_PASSWORD = "Synthetic1!"
HISTORICAL_MEDICATION_NAME = "합성 과거 처방약"
CURRENT_MEDICATION_NAME = "합성 현재 처방약"
OCCURRENCE_LOCAL_DATE = date(2026, 9, 10)


def _require_isolated_local_database() -> None:
    if config.ENV != DeploymentEnvironment.LOCAL or config.DB_NAME != "dosey_e2e":
        raise RuntimeError("Notification E2E seed is restricted to the isolated local dosey_e2e database")


async def seed_notification_round_trip() -> None:
    _require_isolated_local_database()
    async with AsyncSessionFactory() as session:
        existing = await UserRepository(session).get_user_by_email(NOTIFICATION_E2E_EMAIL)
        if existing is not None:
            raise RuntimeError("Notification E2E synthetic user already exists; recreate the isolated stack")

        user = await UserRepository(session).create_user(
            email=NOTIFICATION_E2E_EMAIL,
            hashed_password=hash_password(NOTIFICATION_E2E_PASSWORD),
            name="알림합성검증",
        )
        profile = await session.scalar(
            select(Profile).where(Profile.user_id == user.id, Profile.profile_type == ProfileType.SELF)
        )
        assert profile is not None

        confirmed_at = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
        document = MedicalDocument(
            uploaded_by=user.id,
            profile_id=profile.id,
            document_type=DocumentType.PRESCRIPTION,
            original_file_name="synthetic-notification-421.png",
            object_key="synthetic/notification-421.png",
            file_mime_type="image/png",
            file_size_bytes=1,
            upload_status=UploadStatus.UPLOADED,
        )
        session.add(document)
        await session.flush()
        ocr_job = OcrJob(
            document_id=document.id,
            ocr_status=OcrStatus.COMPLETED,
            started_at=confirmed_at,
            completed_at=confirmed_at,
        )
        session.add(ocr_job)
        await session.flush()

        prescriptions = PrescriptionRepository(session)
        prescription = await prescriptions.create_with_medications(
            document=document,
            source_ocr_job=ocr_job,
            prescribed_date=date(2026, 9, 9),
            confirmed_at=confirmed_at,
            medications=[
                {
                    "medication_name": HISTORICAL_MEDICATION_NAME,
                    "strength_text": "10mg",
                    "dose_value": 1,
                    "dose_unit": "정",
                    "frequency_per_day": 1,
                    "display_order": 1,
                }
            ],
        )
        historical_medication = await session.scalar(
            select(PrescriptionVersionMedication).where(
                PrescriptionVersionMedication.prescription_version_id == prescription.active_version_id
            )
        )
        assert historical_medication is not None

        schedules = MedicationScheduleRepository(session)
        schedule = await schedules.create_schedule_owned(
            prescription_version_medication_id=historical_medication.id,
            user_id=user.id,
            start_local_date=OCCURRENCE_LOCAL_DATE,
            end_mode=MedicationScheduleEndMode.OPEN_ENDED,
            end_local_date=None,
            source=MedicationScheduleSource.USER_CONFIRMED,
        )
        assert schedule is not None
        schedule_time = (
            await schedules.add_schedule_times(
                schedule=schedule,
                schedule_revision=schedule.revision,
                local_times=[time(23, 0)],
            )
        )[0]
        scheduled_at = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
        await schedules.create_occurrence(
            schedule=schedule,
            schedule_time=schedule_time,
            scheduled_local_date=OCCURRENCE_LOCAL_DATE,
            scheduled_at=scheduled_at,
            confirmation_deadline_at=scheduled_at + timedelta(hours=4),
        )

        notifications = NotificationRepository(session)
        scheduler = NotificationScheduler(notifications)
        generated = await scheduler.generate_once(now=scheduled_at)
        published = await scheduler.publish_once(now=scheduled_at)
        if generated.created_count != 1 or published.delivered_count != 1:
            raise RuntimeError("Notification E2E seed did not create and deliver exactly one notification")

        replacement = await prescriptions.create_version(
            prescription=prescription,
            prescribed_date=date(2026, 9, 10),
            confirmed_at=scheduled_at + timedelta(minutes=1),
            medications=[
                {
                    "medication_name": CURRENT_MEDICATION_NAME,
                    "strength_text": "20mg",
                    "dose_value": 2,
                    "dose_unit": "정",
                    "frequency_per_day": 1,
                    "display_order": 1,
                }
            ],
        )
        if replacement.id == historical_medication.prescription_version_id:
            raise RuntimeError("Notification E2E seed did not create a distinct current prescription version")

        await session.commit()


async def _main() -> None:
    try:
        await seed_notification_round_trip()
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(_main())
