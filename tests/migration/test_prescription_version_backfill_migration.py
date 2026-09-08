"""Prescription Version legacy backfill tests against migrated PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.profiles import Profile, ProfileType
from app.models.users import User
from app.repositories.prescription_repository import PrescriptionRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKFILL_REVISION = "169b2c3d4e5f"
BACKFILL_BASE_REVISION = "169a1b2c3d4e"


def create_alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


async def _seed_legacy_prescription(*, include_medication: bool = True, mismatched_ocr: bool = False) -> dict[str, str]:
    token = uuid4().hex
    ids = {
        "user_id": str(uuid4()),
        "profile_id": str(uuid4()),
        "document_id": str(uuid4()),
        "other_document_id": str(uuid4()),
        "ocr_job_id": str(uuid4()),
        "prescription_id": str(uuid4()),
        "medication_id": str(uuid4()),
    }
    async with _connection() as connection, connection.begin():
        await connection.execute(
            text(
                """
                INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin)
                VALUES (:user_id, :email, 'synthetic-password-hash', 'backfill-test', true, false)
                """
            ),
            {**ids, "email": f"bf-{token[:12]}@example.com"},
        )
        await connection.execute(
            text(
                """
                INSERT INTO profile (id, user_id, profile_type, display_name)
                VALUES (:profile_id, :user_id, 'SELF', 'backfill-test')
                """
            ),
            ids,
        )
        await connection.execute(
            text(
                """
                INSERT INTO medical_document (
                    id, uploaded_by, profile_id, document_type, original_file_name,
                    object_key, file_mime_type, file_size_bytes, upload_status
                )
                VALUES (
                    :document_id, :user_id, :profile_id, 'PRESCRIPTION',
                    'legacy.png', :object_key, 'image/png', 1, 'UPLOADED'
                )
                """
            ),
            {**ids, "object_key": f"synthetic/{token}/legacy.png"},
        )
        if mismatched_ocr:
            await connection.execute(
                text(
                    """
                    INSERT INTO medical_document (
                        id, uploaded_by, profile_id, document_type, original_file_name,
                        object_key, file_mime_type, file_size_bytes, upload_status
                    )
                    VALUES (
                        :other_document_id, :user_id, :profile_id, 'PRESCRIPTION',
                        'other.png', :object_key, 'image/png', 1, 'UPLOADED'
                    )
                    """
                ),
                {**ids, "object_key": f"synthetic/{token}/other.png"},
            )
        await connection.execute(
            text(
                """
                INSERT INTO ocr_job (id, document_id, ocr_status, completed_at)
                VALUES (:ocr_job_id, :ocr_document_id, 'COMPLETED', now())
                """
            ),
            {
                **ids,
                "ocr_document_id": ids["other_document_id"] if mismatched_ocr else ids["document_id"],
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO prescription (
                    id, active_version_id, document_id, source_ocr_job_id,
                    profile_id, prescribed_date, prescription_status, confirmed_at, created_at
                )
                VALUES (
                    :prescription_id, NULL, :document_id, :ocr_job_id,
                    :profile_id, DATE '2026-09-08', 'CONFIRMED', :confirmed_at, :confirmed_at
                )
                """
            ),
            {**ids, "confirmed_at": datetime(2026, 9, 8, 1, 2, 3, tzinfo=UTC)},
        )
        if include_medication:
            await connection.execute(
                text(
                    """
                    INSERT INTO medication (
                        id, prescription_id, medication_name, strength_text,
                        dose_value, dose_unit, frequency_per_day, timing_text,
                        duration_days, display_order, created_at
                    )
                    VALUES (
                        :medication_id, :prescription_id, '합성백필정', '10mg',
                        1.500, '정', 2, '식후', 3, 1, :created_at
                    )
                    """
                ),
                {**ids, "created_at": datetime(2026, 9, 8, 1, 2, 4, tzinfo=UTC)},
            )
    return ids


async def _cleanup(ids: dict[str, str]) -> None:
    async with _connection() as connection, connection.begin():
        await connection.execute(
            text("DELETE FROM medication WHERE prescription_id = :prescription_id"),
            ids,
        )
        await connection.execute(
            text("DELETE FROM prescription WHERE id = :prescription_id"),
            ids,
        )
        await connection.execute(text("DELETE FROM ocr_job WHERE id = :ocr_job_id"), ids)
        await connection.execute(
            text("DELETE FROM medical_document WHERE id IN (:document_id, :other_document_id)"),
            ids,
        )
        await connection.execute(text("DELETE FROM profile WHERE id = :profile_id"), ids)
        await connection.execute(text('DELETE FROM "user" WHERE id = :user_id'), ids)


async def _snapshot(ids: dict[str, str]) -> dict[str, object] | None:
    async with _connection() as connection:
        result = await connection.execute(
            text(
                """
                SELECT
                    p.active_version_id,
                    pv.version_number,
                    pv.prescribed_date,
                    pv.confirmed_at,
                    pvm.medication_name,
                    pvm.strength_text,
                    pvm.dose_value,
                    pvm.dose_unit,
                    pvm.frequency_per_day,
                    pvm.timing_text,
                    pvm.duration_days,
                    pvm.display_order
                FROM prescription AS p
                JOIN prescription_version AS pv
                  ON pv.id = p.active_version_id
                 AND pv.prescription_id = p.id
                JOIN prescription_version_medication AS pvm
                  ON pvm.prescription_version_id = pv.id
                WHERE p.id = :prescription_id
                """
            ),
            ids,
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None


async def _version_counts(ids: dict[str, str]) -> tuple[int, int]:
    async with _connection() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM prescription_version WHERE prescription_id = :prescription_id),
                        (
                            SELECT count(*)
                            FROM prescription_version_medication AS pvm
                            JOIN prescription_version AS pv ON pv.id = pvm.prescription_version_id
                            WHERE pv.prescription_id = :prescription_id
                        )
                    """
                ),
                ids,
            )
        ).one()
        return int(row[0]), int(row[1])


async def _create_via_repository() -> dict[str, str]:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            token = uuid4().hex[:12]
            user = User(
                email=f"dw-{token}@example.com",
                hashed_password="synthetic-password-hash",
                name="dual-write-test",
            )
            session.add(user)
            await session.flush()
            profile = Profile(
                user_id=user.id,
                profile_type=ProfileType.SELF,
                display_name="dual-write-test",
            )
            session.add(profile)
            await session.flush()
            document = MedicalDocument(
                uploaded_by=user.id,
                profile_id=profile.id,
                original_file_name="dual-write.png",
                object_key=f"synthetic/{token}/dual-write.png",
                file_mime_type="image/png",
                file_size_bytes=1,
            )
            session.add(document)
            await session.flush()
            ocr_job = OcrJob(document_id=document.id)
            session.add(ocr_job)
            await session.flush()
            prescription = await PrescriptionRepository(session).create_with_medications(
                document=document,
                source_ocr_job=ocr_job,
                prescribed_date=date(2026, 9, 8),
                confirmed_at=datetime(2026, 9, 8, 2, 3, 4, tzinfo=UTC),
                medications=[
                    {
                        "medication_name": "합성듀얼정",
                        "strength_text": "5mg",
                        "dose_value": Decimal("0.500"),
                        "dose_unit": "정",
                        "frequency_per_day": 1,
                        "timing_text": "취침 전",
                        "duration_days": 7,
                        "display_order": 1,
                    }
                ],
            )
            return {
                "user_id": str(user.id),
                "profile_id": str(profile.id),
                "document_id": str(document.id),
                "other_document_id": str(uuid4()),
                "ocr_job_id": str(ocr_job.id),
                "prescription_id": str(prescription.id),
                "medication_id": "",
            }
    finally:
        await engine.dispose()


def test_backfill_creates_exact_version_one_snapshot_and_is_rerunnable() -> None:
    alembic_config = create_alembic_config()
    command.downgrade(alembic_config, BACKFILL_BASE_REVISION)
    ids = asyncio.run(_seed_legacy_prescription())
    try:
        command.upgrade(alembic_config, BACKFILL_REVISION)

        snapshot = asyncio.run(_snapshot(ids))
        assert snapshot is not None
        assert snapshot["active_version_id"] is not None
        assert snapshot["version_number"] == 1
        assert str(snapshot["prescribed_date"]) == "2026-09-08"
        assert snapshot["confirmed_at"] == datetime(2026, 9, 8, 1, 2, 3, tzinfo=UTC)
        assert snapshot["medication_name"] == "합성백필정"
        assert snapshot["strength_text"] == "10mg"
        assert str(snapshot["dose_value"]) == "1.500"
        assert snapshot["dose_unit"] == "정"
        assert snapshot["frequency_per_day"] == 2
        assert snapshot["timing_text"] == "식후"
        assert snapshot["duration_days"] == 3
        assert snapshot["display_order"] == 1

        original_counts = asyncio.run(_version_counts(ids))
        command.downgrade(alembic_config, BACKFILL_BASE_REVISION)
        command.upgrade(alembic_config, BACKFILL_REVISION)
        assert asyncio.run(_version_counts(ids)) == original_counts == (1, 1)
    finally:
        asyncio.run(_cleanup(ids))
        command.upgrade(alembic_config, "head")


def test_repository_dual_write_commits_against_migrated_postgresql() -> None:
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_create_via_repository())
    try:
        snapshot = asyncio.run(_snapshot(ids))
        assert snapshot is not None
        assert snapshot["version_number"] == 1
        assert snapshot["medication_name"] == "합성듀얼정"
        assert snapshot["strength_text"] == "5mg"
        assert str(snapshot["dose_value"]) == "0.500"
        assert snapshot["duration_days"] == 7
        assert asyncio.run(_version_counts(ids)) == (1, 1)
    finally:
        asyncio.run(_cleanup(ids))


@pytest.mark.parametrize(
    ("include_medication", "mismatched_ocr", "message"),
    [
        (False, False, "prescriptions have no medications"),
        (True, True, "ownership or OCR provenance chains are invalid"),
    ],
)
def test_backfill_rejects_invalid_legacy_graph_without_partial_snapshot(
    include_medication: bool,
    mismatched_ocr: bool,
    message: str,
) -> None:
    alembic_config = create_alembic_config()
    command.downgrade(alembic_config, BACKFILL_BASE_REVISION)
    ids = asyncio.run(
        _seed_legacy_prescription(
            include_medication=include_medication,
            mismatched_ocr=mismatched_ocr,
        )
    )
    try:
        with pytest.raises(RuntimeError, match=message):
            command.upgrade(alembic_config, BACKFILL_REVISION)

        assert asyncio.run(_version_counts(ids)) == (0, 0)
    finally:
        asyncio.run(_cleanup(ids))
        command.upgrade(alembic_config, "head")
