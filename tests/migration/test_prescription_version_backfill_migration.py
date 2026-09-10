"""Prescription Version legacy backfill tests against migrated PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config
from app.core.errors import ApiError
from app.dtos.prescriptions import CorrectPrescriptionRequest, PrescriptionMedicationCorrectionRequest
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.profiles import Profile, ProfileType
from app.models.users import User
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.repositories.medication_schedule_repository import MedicationScheduleRepository
from app.repositories.ocr_repository import OcrRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.services.medication_occurrences import PrescriptionVersionMedicationInvalidationService
from app.services.prescriptions import PrescriptionService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKFILL_REVISION = "169b2c3d4e5f"
BACKFILL_BASE_REVISION = "165f90716263"
CUTOVER_REVISION = "169c3d4e5f6a"
HARDENING_BASE_REVISION = "164b6c7d8e9f"
HARDENING_REVISION = "169d4e5f6a7b"


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


async def _seed_legacy_prescription(
    *,
    include_medication: bool = True,
    medication_name: str = "합성백필정",
    mismatched_ocr: bool = False,
) -> dict[str, str]:
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
                        :medication_id, :prescription_id, :medication_name, '10mg',
                        1.500, '정', 2, '식후', 3, 1, :created_at
                    )
                    """
                ),
                {
                    **ids,
                    "medication_name": medication_name,
                    "created_at": datetime(2026, 9, 8, 1, 2, 4, tzinfo=UTC),
                },
            )
    return ids


async def _seed_partial_version_graph(ids: dict[str, str]) -> None:
    async with _connection() as connection, connection.begin():
        version_id = str(uuid4())
        await connection.execute(
            text(
                """
                INSERT INTO prescription_version (
                    id, prescription_id, version_number, prescribed_date, confirmed_at
                )
                VALUES (
                    :version_id, :prescription_id, 1, DATE '2026-09-08', :confirmed_at
                )
                """
            ),
            {
                **ids,
                "version_id": version_id,
                "confirmed_at": datetime(2026, 9, 8, 1, 2, 3, tzinfo=UTC),
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO prescription_version_medication (
                    id, prescription_version_id, medication_name, strength_text,
                    dose_value, dose_unit, frequency_per_day, timing_text,
                    duration_days, display_order
                )
                VALUES (
                    :version_medication_id, :version_id, '부분그래프정', '10mg',
                    1.500, '정', 2, '식후', 3, 1
                )
                """
            ),
            {
                "version_medication_id": str(uuid4()),
                "version_id": version_id,
            },
        )


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


async def _seed_cutover_dependents(
    ids: dict[str, str],
    *,
    search_strength_text: str = "10mg",
) -> dict[str, str]:
    dependent_ids = {
        "search_id": str(uuid4()),
        "guide_id": str(uuid4()),
        "chat_session_id": str(uuid4()),
    }
    async with _connection() as connection, connection.begin():
        await connection.execute(
            text(
                """
                INSERT INTO medication_candidate_search (
                    id, prescription_version_medication_id, medication_name_snapshot,
                    strength_text_snapshot, query_digest, status, candidate_count,
                    displayed_candidate_count
                ) VALUES (
                    :search_id, :medication_id, '합성백필정', :search_strength_text,
                    'synthetic-cutover-digest', 'NO_CANDIDATE', 0, 0
                )
                """
            ),
            {**ids, **dependent_ids, "search_strength_text": search_strength_text},
        )
        await connection.execute(
            text(
                """
                INSERT INTO guide (id, prescription_id, profile_id, generation_status)
                VALUES (:guide_id, :prescription_id, :profile_id, 'PENDING')
                """
            ),
            {**ids, **dependent_ids},
        )
        await connection.execute(
            text(
                """
                INSERT INTO chat_session (id, prescription_id, profile_id, session_status)
                VALUES (:chat_session_id, :prescription_id, :profile_id, 'ACTIVE')
                """
            ),
            {**ids, **dependent_ids},
        )
    return dependent_ids


async def _cutover_snapshot(ids: dict[str, str]) -> dict[str, object]:
    async with _connection() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """
                    SELECT p.active_version_id, pvm.id AS version_medication_id,
                           mcs.prescription_version_medication_id AS search_medication_id,
                           g.prescription_version_id AS guide_version_id,
                           cs.prescription_version_id AS chat_version_id,
                           (
                               SELECT count(*) FROM pg_constraint
                               WHERE conname IN (
                                   'fk_medication_candidate_search_version_medication',
                                   'fk_medication_identification_version_medication',
                                   'fk_guide_prescription_version_prescription',
                                   'fk_chat_session_prescription_version_prescription',
                                   'fk_ai_job_prescription_version'
                               )
                           ) AS cutover_fk_count
                    FROM prescription p
                    JOIN prescription_version_medication pvm
                      ON pvm.prescription_version_id = p.active_version_id
                    JOIN medication_candidate_search mcs ON mcs.id = :search_id
                    JOIN guide g ON g.id = :guide_id
                    JOIN chat_session cs ON cs.id = :chat_session_id
                    WHERE p.id = :prescription_id
                    """
                    ),
                    ids,
                )
            )
            .mappings()
            .one()
        )
        return dict(row)


async def _cleanup_cutover(ids: dict[str, str]) -> None:
    async with _connection() as connection, connection.begin():
        await connection.execute(text("DELETE FROM chat_session WHERE id = :chat_session_id"), ids)
        await connection.execute(text("DELETE FROM guide WHERE id = :guide_id"), ids)
        await connection.execute(text("DELETE FROM medication_candidate_search WHERE id = :search_id"), ids)
    await _cleanup(ids)


async def _create_via_repository(*, legacy_schema: bool = False) -> dict[str, str]:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            token = uuid4().hex[:12]
            user = User(
                email=f"dw-{token}@example.com",
                hashed_password="synthetic-password-hash",
                name="version-write-test",
            )
            session.add(user)
            await session.flush()
            profile = Profile(
                user_id=user.id,
                profile_type=ProfileType.SELF,
                display_name="version-write-test",
            )
            session.add(profile)
            await session.flush()
            document = MedicalDocument(
                uploaded_by=user.id,
                profile_id=profile.id,
                original_file_name="version-write.png",
                object_key=f"synthetic/{token}/version-write.png",
                file_mime_type="image/png",
                file_size_bytes=1,
            )
            session.add(document)
            await session.flush()
            ocr_job = OcrJob(document_id=document.id)
            session.add(ocr_job)
            await session.flush()
            if legacy_schema:
                # Hardening migration tests run against the pre-398 schema.
                from types import SimpleNamespace

                prescription_id, version_id = str(uuid4()), str(uuid4())
                params = {
                    "id": prescription_id,
                    "version_id": version_id,
                    "document_id": str(document.id),
                    "ocr_id": str(ocr_job.id),
                    "profile_id": str(profile.id),
                }
                await session.execute(
                    text(
                        "INSERT INTO prescription (id, active_version_id, document_id, source_ocr_job_id, profile_id, "
                        "prescribed_date, prescription_status, confirmed_at) VALUES "
                        "(:id, :version_id, :document_id, :ocr_id, :profile_id, DATE '2026-09-08', 'CONFIRMED', now())"
                    ),
                    params,
                )
                await session.execute(
                    text(
                        "INSERT INTO prescription_version (id, prescription_id, version_number, prescribed_date, confirmed_at) "
                        "VALUES (:version_id, :id, 1, DATE '2026-09-08', now())"
                    ),
                    params,
                )
                await session.execute(
                    text(
                        "INSERT INTO prescription_version_medication (id, prescription_version_id, medication_name, display_order) "
                        "VALUES (:med_id, :version_id, '합성듀얼정', 1)"
                    ),
                    {**params, "med_id": str(uuid4())},
                )
                prescription = SimpleNamespace(id=prescription_id)
            else:
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


async def _correct_via_repository(ids: dict[str, str]) -> str:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            repository = PrescriptionRepository(session)
            prescription = await repository.get_owned_for_version_update(
                prescription_id=UUID(ids["prescription_id"]),
                user_id=UUID(ids["user_id"]),
            )
            assert prescription is not None
            version = await repository.create_version(
                prescription=prescription,
                prescribed_date=date(2026, 9, 9),
                confirmed_at=datetime(2026, 9, 9, 2, 3, 4, tzinfo=UTC),
                medications=[
                    {
                        "medication_name": "합성정정정",
                        "strength_text": "2.5mg",
                        "dose_value": Decimal("0.250"),
                        "dose_unit": "정",
                        "frequency_per_day": 2,
                        "timing_text": "식후",
                        "duration_days": 5,
                        "display_order": 1,
                    }
                ],
            )
            return str(version.id)
    finally:
        await engine.dispose()


async def _correct_once(ids: dict[str, str], *, base_version_id: UUID) -> tuple[str, str | None]:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            user = await session.get(User, UUID(ids["user_id"]))
            assert user is not None
            service = PrescriptionService(
                MedicalDocumentRepository(session),
                OcrRepository(session),
                PrescriptionRepository(session),
                PrescriptionVersionMedicationInvalidationService(MedicationScheduleRepository(session)),
            )
            try:
                result = await service.correct_prescription(
                    user=user,
                    prescription_id=UUID(ids["prescription_id"]),
                    request=CorrectPrescriptionRequest(
                        base_version_id=base_version_id,
                        expected_revision=1,
                        prescribed_date=date(2026, 9, 9),
                        medications=[
                            PrescriptionMedicationCorrectionRequest(
                                medication_name="동시정정합성약",
                                strength_text="1mg",
                                display_order=1,
                            )
                        ],
                    ),
                )
                await session.commit()
                return "ok", str(result.prescription_version_id)
            except ApiError as exc:
                await session.rollback()
                return exc.code, None
    finally:
        await engine.dispose()


async def _correct_concurrently(ids: dict[str, str], *, base_version_id: UUID) -> list[tuple[str, str | None]]:
    return list(
        await asyncio.gather(
            _correct_once(ids, base_version_id=base_version_id),
            _correct_once(ids, base_version_id=base_version_id),
        )
    )


async def _seed_guide_chat_provenance(ids: dict[str, str]) -> dict[str, str]:
    provenance_ids = {
        "guide_id": str(uuid4()),
        "chat_session_id": str(uuid4()),
    }
    async with _connection() as connection, connection.begin():
        active_version_id = await connection.scalar(
            text("SELECT active_version_id FROM prescription WHERE id = :prescription_id"),
            ids,
        )
        assert active_version_id is not None
        values = {**ids, **provenance_ids, "active_version_id": str(active_version_id)}
        await connection.execute(
            text(
                """
                INSERT INTO guide (
                    id, prescription_id, prescription_version_id, profile_id, generation_status
                ) VALUES (
                    :guide_id, :prescription_id, :active_version_id, :profile_id, 'PENDING'
                )
                """
            ),
            values,
        )
        await connection.execute(
            text(
                """
                INSERT INTO chat_session (
                    id, prescription_id, prescription_version_id, profile_id, session_status
                ) VALUES (
                    :chat_session_id, :prescription_id, :active_version_id, :profile_id, 'ACTIVE'
                )
                """
            ),
            values,
        )
    return provenance_ids


async def _cleanup_guide_chat_provenance(ids: dict[str, str]) -> None:
    async with _connection() as connection, connection.begin():
        await connection.execute(text("DELETE FROM chat_session WHERE id = :chat_session_id"), ids)
        await connection.execute(text("DELETE FROM guide WHERE id = :guide_id"), ids)


async def _seed_null_version_guide(ids: dict[str, str]) -> str:
    guide_id = str(uuid4())
    async with _connection() as connection, connection.begin():
        await connection.execute(
            text(
                """
                INSERT INTO guide (id, prescription_id, prescription_version_id, profile_id, generation_status)
                VALUES (:guide_id, :prescription_id, NULL, :profile_id, 'PENDING')
                """
            ),
            {**ids, "guide_id": guide_id},
        )
    return guide_id


async def _delete_guide(guide_id: str) -> None:
    async with _connection() as connection, connection.begin():
        await connection.execute(text("DELETE FROM guide WHERE id = :guide_id"), {"guide_id": guide_id})


async def _seed_invalid_ai_job(
    ids: dict[str, str],
    *,
    job_type: str,
    prescription_version_id: str | None,
) -> str:
    job_id = str(uuid4())
    async with _connection() as connection, connection.begin():
        await connection.execute(
            text(
                """
                INSERT INTO ai_job (
                    id, user_id, job_type, status, prescription_version_id,
                    attempt_count, max_attempts
                ) VALUES (
                    :job_id, :user_id, :job_type, 'PENDING', :prescription_version_id,
                    0, 3
                )
                """
            ),
            {
                **ids,
                "job_id": job_id,
                "job_type": job_type,
                "prescription_version_id": prescription_version_id,
            },
        )
    return job_id


async def _delete_ai_job(job_id: str) -> None:
    async with _connection() as connection, connection.begin():
        await connection.execute(text("DELETE FROM ai_job WHERE id = :job_id"), {"job_id": job_id})


async def _active_version_id(ids: dict[str, str]) -> str:
    async with _connection() as connection:
        active_version_id = await connection.scalar(
            text("SELECT active_version_id FROM prescription WHERE id = :prescription_id"),
            ids,
        )
        assert active_version_id is not None
        return str(active_version_id)


async def _insert_invalid_ai_job_after_hardening(
    ids: dict[str, str],
    *,
    job_type: str,
    prescription_version_id: str | None,
) -> None:
    async with _connection() as connection, connection.begin():
        await connection.execute(
            text(
                """
                INSERT INTO ai_job (
                    id, user_id, job_type, status, prescription_version_id,
                    attempt_count, max_attempts
                ) VALUES (
                    :job_id, :user_id, :job_type, 'PENDING', :prescription_version_id,
                    0, 3
                )
                """
            ),
            {
                **ids,
                "job_id": str(uuid4()),
                "job_type": job_type,
                "prescription_version_id": prescription_version_id,
            },
        )


async def _seed_active_candidate(ids: dict[str, str]) -> str:
    search_id = str(uuid4())
    async with _connection() as connection, connection.begin():
        version_medication_id = await connection.scalar(
            text(
                """
                SELECT pvm.id
                FROM prescription p
                JOIN prescription_version_medication pvm
                  ON pvm.prescription_version_id = p.active_version_id
                WHERE p.id = :prescription_id
                """
            ),
            ids,
        )
        assert version_medication_id is not None
        await connection.execute(
            text(
                """
                INSERT INTO medication_candidate_search (
                    id, prescription_version_medication_id, medication_name_snapshot,
                    strength_text_snapshot, query_digest, status, candidate_count,
                    displayed_candidate_count
                )
                SELECT :search_id, pvm.id, pvm.medication_name, pvm.strength_text,
                       'synthetic-audit-retention', 'NO_CANDIDATE', 0, 0
                FROM prescription_version_medication pvm
                WHERE pvm.id = :version_medication_id
                """
            ),
            {
                "search_id": search_id,
                "version_medication_id": str(version_medication_id),
            },
        )
    return search_id


async def _delete_prescription(ids: dict[str, str]) -> None:
    async with _connection() as connection, connection.begin():
        await connection.execute(text("DELETE FROM prescription WHERE id = :prescription_id"), ids)


async def _delete_candidate(search_id: str) -> None:
    async with _connection() as connection, connection.begin():
        await connection.execute(
            text("DELETE FROM medication_candidate_search WHERE id = :search_id"),
            {"search_id": search_id},
        )


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
        command.upgrade(alembic_config, "398b2c3d4e5f")


def test_repository_version_only_write_commits_against_migrated_postgresql() -> None:
    command.upgrade(create_alembic_config(), "398b2c3d4e5f")
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


def test_repository_correction_commits_complete_version_against_migrated_postgresql() -> None:
    command.upgrade(create_alembic_config(), "398b2c3d4e5f")
    ids = asyncio.run(_create_via_repository())
    try:
        corrected_version_id = asyncio.run(_correct_via_repository(ids))
        snapshot = asyncio.run(_snapshot(ids))
        assert snapshot is not None
        assert str(snapshot["active_version_id"]) == corrected_version_id
        assert snapshot["version_number"] == 2
        assert snapshot["medication_name"] == "합성정정정"
        assert asyncio.run(_version_counts(ids)) == (2, 2)
    finally:
        asyncio.run(_cleanup(ids))


def test_concurrent_corrections_allow_only_one_new_active_version_on_migrated_postgresql() -> None:
    command.upgrade(create_alembic_config(), "398b2c3d4e5f")
    ids = asyncio.run(_create_via_repository())
    try:
        snapshot = asyncio.run(_snapshot(ids))
        assert snapshot is not None
        base_version_id = UUID(str(snapshot["active_version_id"]))

        results = asyncio.run(_correct_concurrently(ids, base_version_id=base_version_id))

        assert [code for code, _ in results].count("ok") == 1
        assert [code for code, _ in results].count("PRESCRIPTION_VERSION_CONFLICT") == 1
        assert asyncio.run(_version_counts(ids)) == (2, 2)
        active = asyncio.run(_snapshot(ids))
        assert active is not None
        assert active["version_number"] == 2
        assert str(active["active_version_id"]) in {version_id for _, version_id in results if version_id}
    finally:
        asyncio.run(_cleanup(ids))


def test_read_cutover_rebackfills_and_remaps_dependents_on_migrated_postgresql() -> None:
    alembic_config = create_alembic_config()
    command.downgrade(alembic_config, BACKFILL_REVISION)
    ids = asyncio.run(_seed_legacy_prescription())
    ids.update(asyncio.run(_seed_cutover_dependents(ids)))
    try:
        command.upgrade(alembic_config, CUTOVER_REVISION)
        snapshot = asyncio.run(_cutover_snapshot(ids))
        assert snapshot["active_version_id"] is not None
        assert snapshot["search_medication_id"] == snapshot["version_medication_id"]
        assert snapshot["guide_version_id"] == snapshot["active_version_id"]
        assert snapshot["chat_version_id"] == snapshot["active_version_id"]
        assert snapshot["cutover_fk_count"] == 5
    finally:
        asyncio.run(_cleanup_cutover(ids))
        command.upgrade(alembic_config, "398b2c3d4e5f")


def test_read_cutover_rejects_candidate_snapshot_that_disagrees_with_pvm() -> None:
    alembic_config = create_alembic_config()
    command.downgrade(alembic_config, BACKFILL_REVISION)
    ids = asyncio.run(_seed_legacy_prescription())
    ids.update(asyncio.run(_seed_cutover_dependents(ids, search_strength_text="999mg")))
    try:
        with pytest.raises(RuntimeError, match="Candidate Search snapshots disagree"):
            command.upgrade(alembic_config, CUTOVER_REVISION)
    finally:
        asyncio.run(_cleanup_cutover(ids))
        command.upgrade(alembic_config, "398b2c3d4e5f")


def test_read_cutover_downgrade_rejects_guide_and_chat_provenance() -> None:
    alembic_config = create_alembic_config()
    command.upgrade(alembic_config, "398b2c3d4e5f")
    ids = asyncio.run(_create_via_repository())
    ids.update(asyncio.run(_seed_guide_chat_provenance(ids)))
    try:
        with pytest.raises(RuntimeError, match="Guide/Chat provenance exist"):
            command.downgrade(alembic_config, BACKFILL_REVISION)
    finally:
        asyncio.run(_cleanup_guide_chat_provenance(ids))
        asyncio.run(_cleanup(ids))
        command.upgrade(alembic_config, "398b2c3d4e5f")


def test_hardening_refuses_remaining_null_runtime_version_link() -> None:
    alembic_config = create_alembic_config()
    command.downgrade(alembic_config, HARDENING_BASE_REVISION)
    ids = asyncio.run(_create_via_repository(legacy_schema=True))
    guide_id = asyncio.run(_seed_null_version_guide(ids))
    try:
        with pytest.raises(RuntimeError, match="invalid provenance remains"):
            command.upgrade(alembic_config, HARDENING_REVISION)
    finally:
        asyncio.run(_delete_guide(guide_id))
        asyncio.run(_cleanup(ids))
        command.upgrade(alembic_config, "398b2c3d4e5f")


@pytest.mark.parametrize("job_type", ["GUIDE", "CHAT"])
def test_hardening_refuses_job_without_required_version(job_type: str) -> None:
    alembic_config = create_alembic_config()
    command.downgrade(alembic_config, HARDENING_BASE_REVISION)
    ids = asyncio.run(_create_via_repository(legacy_schema=True))
    job_id = asyncio.run(_seed_invalid_ai_job(ids, job_type=job_type, prescription_version_id=None))
    try:
        with pytest.raises(RuntimeError, match="invalid provenance remains"):
            command.upgrade(alembic_config, HARDENING_REVISION)
    finally:
        asyncio.run(_delete_ai_job(job_id))
        asyncio.run(_cleanup(ids))
        command.upgrade(alembic_config, "398b2c3d4e5f")


def test_hardening_refuses_ocr_job_with_version() -> None:
    alembic_config = create_alembic_config()
    command.downgrade(alembic_config, HARDENING_BASE_REVISION)
    ids = asyncio.run(_create_via_repository(legacy_schema=True))
    version_id = asyncio.run(_active_version_id(ids))
    job_id = asyncio.run(_seed_invalid_ai_job(ids, job_type="OCR", prescription_version_id=version_id))
    try:
        with pytest.raises(RuntimeError, match="invalid provenance remains"):
            command.upgrade(alembic_config, HARDENING_REVISION)
    finally:
        asyncio.run(_delete_ai_job(job_id))
        asyncio.run(_cleanup(ids))
        command.upgrade(alembic_config, "398b2c3d4e5f")


@pytest.mark.parametrize(
    ("job_type", "use_active_version"),
    [("OCR", True), ("GUIDE", False), ("CHAT", False)],
)
def test_hardening_constraint_prevents_invalid_job_from_bypassing_version_fencing(
    job_type: str,
    use_active_version: bool,
) -> None:
    command.upgrade(create_alembic_config(), "398b2c3d4e5f")
    ids = asyncio.run(_create_via_repository())
    version_id = asyncio.run(_active_version_id(ids)) if use_active_version else None
    try:
        with pytest.raises(IntegrityError, match="chk_ai_job_prescription_version_by_type"):
            asyncio.run(
                _insert_invalid_ai_job_after_hardening(
                    ids,
                    job_type=job_type,
                    prescription_version_id=version_id,
                )
            )
    finally:
        asyncio.run(_cleanup(ids))


def test_prescription_delete_cannot_cascade_candidate_audit_history() -> None:
    command.upgrade(create_alembic_config(), "398b2c3d4e5f")
    ids = asyncio.run(_create_via_repository())
    search_id = asyncio.run(_seed_active_candidate(ids))
    try:
        with pytest.raises(IntegrityError):
            asyncio.run(_delete_prescription(ids))
    finally:
        asyncio.run(_delete_candidate(search_id))
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
        command.upgrade(alembic_config, "398b2c3d4e5f")


def test_backfill_rejects_blank_legacy_medication_name_before_copy() -> None:
    alembic_config = create_alembic_config()
    command.downgrade(alembic_config, BACKFILL_BASE_REVISION)
    ids = asyncio.run(_seed_legacy_prescription(medication_name="   "))
    try:
        with pytest.raises(RuntimeError, match="medications have blank medication names"):
            command.upgrade(alembic_config, BACKFILL_REVISION)

        assert asyncio.run(_version_counts(ids)) == (0, 0)
    finally:
        asyncio.run(_cleanup(ids))
        command.upgrade(alembic_config, "398b2c3d4e5f")


def test_backfill_rejects_partial_version_graph() -> None:
    alembic_config = create_alembic_config()
    command.downgrade(alembic_config, BACKFILL_BASE_REVISION)
    ids = asyncio.run(_seed_legacy_prescription())
    asyncio.run(_seed_partial_version_graph(ids))
    try:
        with pytest.raises(RuntimeError, match="prescriptions have a partial Version graph"):
            command.upgrade(alembic_config, BACKFILL_REVISION)

        assert asyncio.run(_version_counts(ids)) == (1, 1)
    finally:
        asyncio.run(_cleanup(ids))
        command.upgrade(alembic_config, "398b2c3d4e5f")
