"""소유권 확인 뒤 모든 실제 DB 소비 경로가 변조된 버전을 거부합니다."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import ApiError
from app.models.prescriptions import PrescriptionVersion, PrescriptionVersionMedication
from app.repositories.guide_repository import GuideRepository
from app.repositories.medication_candidate_repository import MedicationCandidateRepository
from app.repositories.medication_schedule_repository import SqlAlchemyPrescriptionVersionMedicationOwnership
from app.repositories.prescription_repository import PrescriptionRepository
from app.tests.repositories.test_prescription_repository import _create_confirmed_prescription, _create_user


@pytest.mark.parametrize(
    "path", ["candidate_read", "candidate_write", "preflight", "schedule_read", "schedule_write", "chat", "guide"]
)
@pytest.mark.parametrize("corruption", ["content", "missing_medication"])
async def test_consumers_reject_corrupted_version(db_session, path, corruption):
    owner = await _create_user(db_session, email="consumer398@test.local")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    version = await PrescriptionRepository(db_session).create_version(
        prescription=prescription,
        prescribed_date=date.today(),
        confirmed_at=datetime.now(UTC),
        medications=[
            {"medication_name": "Synthetic A", "display_order": 1},
            {"medication_name": "Synthetic B", "display_order": 2},
        ],
    )
    medications = await PrescriptionRepository(db_session).get_version_medications(prescription_version_id=version.id)
    retained_id = medications[1].id
    if corruption == "content":
        await db_session.execute(
            update(PrescriptionVersionMedication)
            .where(PrescriptionVersionMedication.id == medications[0].id)
            .values(medication_name="Synthetic corruption")
        )
    else:
        await db_session.execute(
            delete(PrescriptionVersionMedication).where(PrescriptionVersionMedication.id == medications[0].id)
        )
    candidate = MedicationCandidateRepository(db_session)
    schedule = SqlAlchemyPrescriptionVersionMedicationOwnership(db_session)
    args = {"prescription_version_medication_id": retained_id, "user_id": owner.id}
    with pytest.raises(ApiError) as error:
        if path == "candidate_read":
            await candidate.get_medication_owned(**args)
        elif path == "candidate_write":
            await candidate.get_medication_for_candidate_search_owned(**args)
        elif path == "preflight":
            await candidate.get_active_version_medication_ids_for_update(prescription_version_id=version.id)
        elif path == "schedule_read":
            await schedule.is_owned(**args)
        elif path == "schedule_write":
            await schedule.lock_active_owned(**args)
        elif path == "chat":
            await PrescriptionRepository(db_session).get_version_medications(prescription_version_id=version.id)
        else:
            await GuideRepository(db_session).get_prescription_owned(prescription_id=prescription.id, user_id=owner.id)
    assert error.value.status_code == 409
    assert error.value.code == "PRESCRIPTION_VERSION_UNAVAILABLE"


@pytest.mark.parametrize("target", ["parent_count", "parent_hash", "child_count"])
async def test_null_integrity_metadata_is_rejected_by_database(db_session, target):
    owner = await _create_user(db_session, email="nonnull398@test.local")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    version_id = prescription.active_version_id
    with pytest.raises(IntegrityError) as error:
        async with db_session.begin_nested():
            if target == "child_count":
                await db_session.execute(
                    update(PrescriptionVersionMedication)
                    .where(PrescriptionVersionMedication.prescription_version_id == version_id)
                    .values(medication_count=None)
                )
            else:
                column = "medication_count" if target == "parent_count" else "content_hash"
                await db_session.execute(
                    update(PrescriptionVersion).where(PrescriptionVersion.id == version_id).values(**{column: None})
                )
    assert error.value.orig.sqlstate == "23502"
    assert await db_session.scalar(select(PrescriptionVersion.content_hash).where(PrescriptionVersion.id == version_id))
