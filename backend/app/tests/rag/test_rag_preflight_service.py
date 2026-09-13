from datetime import date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.errors import ApiError
from app.models.async_jobs import AiJob, OutboxEvent
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.profiles import Profile, ProfileType
from app.models.rag_candidate import (
    MedicationCandidateSearch,
    MedicationCandidateSearchResult,
    MedicationCandidateSearchStatus,
    MedicationIdentification,
    MedicationIdentificationSource,
    MedicationIdentificationStatus,
)
from app.models.rag_runtime import AiJobExecutionContext, AiJobExecutionIdentification, AiJobIntakeContext
from app.models.users import Gender, User
from app.repositories.medication_candidate_repository import MedicationCandidateRepository
from app.services.medication_identification import MedicationIdentificationService
from app.services.rag_preflight import RagPreflightService
from app.tests.fixtures.prescription_fingerprint import fingerprint_values


def _service(session: AsyncSession) -> RagPreflightService:
    return RagPreflightService(MedicationIdentificationService(MedicationCandidateRepository(session)))


def _hash(char: str) -> str:
    return char * 64


async def _create_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        email=email,
        hashed_password="hashed-password",
        name="테스트 사용자",
        gender=Gender.MALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    session.add(user)
    await session.flush()
    profile = Profile(user_id=user.id, profile_type=ProfileType.SELF, display_name=user.name)
    session.add(profile)
    await session.flush()
    return user


async def _create_prescription(
    session: AsyncSession,
    *,
    user: User,
    medication_count: int = 2,
) -> tuple[Prescription, list[PrescriptionVersionMedication]]:
    profile = await session.scalar(
        select(Profile).where(Profile.user_id == user.id, Profile.profile_type == ProfileType.SELF)
    )
    assert profile is not None
    document = MedicalDocument(
        uploaded_by=user.id,
        profile_id=profile.id,
        original_file_name="prescription.jpg",
        object_key=f"synthetic/{uuid4()}.jpg",
        file_mime_type="image/jpeg",
        file_size_bytes=100,
    )
    session.add(document)
    await session.flush()
    ocr_job = OcrJob(document_id=document.id)
    session.add(ocr_job)
    await session.flush()

    medication_payloads = [
        {"medication_name": f"테스트약{i}", "strength_text": "500mg", "display_order": i}
        for i in range(1, medication_count + 1)
    ]
    version_id = uuid4()
    prescribed_date = date.today()
    prescription = Prescription(
        active_version_id=version_id,
        document_id=document.id,
        source_ocr_job_id=ocr_job.id,
        profile_id=profile.id,
        prescribed_date=prescribed_date,
        confirmed_at=datetime.now(config.TIMEZONE),
    )
    session.add(prescription)
    await session.flush()
    version = PrescriptionVersion(
        **fingerprint_values(prescribed_date, medication_payloads),
        id=version_id,
        prescription_id=prescription.id,
        version_number=1,
        prescribed_date=prescribed_date,
        confirmed_at=prescription.confirmed_at,
    )
    session.add(version)
    await session.flush()

    medications = [
        PrescriptionVersionMedication(
            medication_count=medication_count,
            prescription_version_id=version_id,
            medication_name=payload["medication_name"],
            strength_text=payload["strength_text"],
            display_order=payload["display_order"],
        )
        for payload in medication_payloads
    ]
    session.add_all(medications)
    await session.flush()
    return prescription, medications


async def _create_identification(
    session: AsyncSession,
    *,
    medication: PrescriptionVersionMedication,
    status: MedicationIdentificationStatus = MedicationIdentificationStatus.MATCHED,
) -> MedicationIdentification:
    search = MedicationCandidateSearch(
        prescription_version_medication_id=medication.id,
        medication_name_snapshot=medication.medication_name,
        strength_text_snapshot=medication.strength_text,
        query_digest=_hash("a"),
        status=MedicationCandidateSearchStatus.READY,
        candidate_count=1,
        displayed_candidate_count=1,
    )
    session.add(search)
    await session.flush()
    result = MedicationCandidateSearchResult(
        search_id=search.id,
        product_id=uuid4(),
        code_system="MFDS_ITEM_SEQ",
        canonical_code=f"SYNTHETIC-{medication.display_order}",
        product_name=f"{medication.medication_name} 후보",
        product_status="ACTIVE",
        result_rank=1,
        result_score=1.0,
        result_method="fixture-exact",
        is_displayed=True,
        selection_eligible=True,
    )
    session.add(result)
    await session.flush()

    if status == MedicationIdentificationStatus.MATCHED:
        identification = MedicationIdentification(
            prescription_version_medication_id=medication.id,
            candidate_search_id=search.id,
            candidate_search_result_id=result.id,
            product_id=result.product_id,
            code_system=result.code_system,
            canonical_code=result.canonical_code,
            status=MedicationIdentificationStatus.MATCHED,
            source=MedicationIdentificationSource.USER_SELECTED,
            confirmed_at=datetime.now(config.TIMEZONE),
        )
    else:
        identification = MedicationIdentification(
            prescription_version_medication_id=medication.id,
            candidate_search_id=search.id,
            candidate_search_result_id=result.id,
            product_id=None,
            code_system=None,
            canonical_code=None,
            status=MedicationIdentificationStatus.UNRESOLVED,
            source=MedicationIdentificationSource.USER_REJECTED,
            decision_reason="USER_REJECTED_DISPLAYED_CANDIDATE",
            rejected_at=datetime.now(config.TIMEZONE),
        )
    session.add(identification)
    await session.flush()
    return identification


async def _count_rows(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def _side_effect_counts(session: AsyncSession) -> dict[str, int]:
    return {
        "ai_job": await _count_rows(session, AiJob),
        "outbox_event": await _count_rows(session, OutboxEvent),
        "intake_context": await _count_rows(session, AiJobIntakeContext),
        "execution_context": await _count_rows(session, AiJobExecutionContext),
        "execution_identification": await _count_rows(session, AiJobExecutionIdentification),
    }


async def test_preflight_passes_when_all_active_medications_are_matched(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="preflight-owner@example.com")
    prescription, medications = await _create_prescription(db_session, user=owner)
    identifications = [await _create_identification(db_session, medication=medication) for medication in medications]

    result = await _service(db_session).ensure_all_active_medications_matched(
        prescription_id=prescription.id,
        user_id=owner.id,
        expected_prescription_version_id=prescription.active_version_id,
    )

    assert result.prescription_id == prescription.id
    assert result.prescription_version_id == prescription.active_version_id
    assert [item.prescription_version_medication_id for item in result.matched_medications] == [
        medication.id for medication in medications
    ]
    assert {item.medication_identification_id for item in result.matched_medications} == {
        identification.id for identification in identifications
    }


async def test_preflight_rejects_missing_identification_without_side_effects(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="preflight-missing@example.com")
    prescription, medications = await _create_prescription(db_session, user=owner)
    await _create_identification(db_session, medication=medications[0])
    before = await _side_effect_counts(db_session)

    with pytest.raises(ApiError) as exc_info:
        await _service(db_session).ensure_all_active_medications_matched(
            prescription_id=prescription.id,
            user_id=owner.id,
            expected_prescription_version_id=prescription.active_version_id,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "PRESCRIPTION_MEDICATION_IDENTIFICATION_INCOMPLETE"
    assert exc_info.value.details[0].reason == "MATCHED_IDENTIFICATION_REQUIRED"
    assert await _side_effect_counts(db_session) == before


async def test_preflight_rejects_non_matched_identification(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="preflight-unresolved@example.com")
    prescription, medications = await _create_prescription(db_session, user=owner)
    await _create_identification(db_session, medication=medications[0])
    await _create_identification(
        db_session,
        medication=medications[1],
        status=MedicationIdentificationStatus.UNRESOLVED,
    )

    with pytest.raises(ApiError) as exc_info:
        await _service(db_session).ensure_all_active_medications_matched(
            prescription_id=prescription.id,
            user_id=owner.id,
            expected_prescription_version_id=prescription.active_version_id,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "PRESCRIPTION_MEDICATION_IDENTIFICATION_INCOMPLETE"


async def test_preflight_hides_other_users_prescription(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="preflight-owner-404@example.com")
    intruder = await _create_user(db_session, email="preflight-intruder-404@example.com")
    prescription, medications = await _create_prescription(db_session, user=owner)
    for medication in medications:
        await _create_identification(db_session, medication=medication)

    with pytest.raises(ApiError) as exc_info:
        await _service(db_session).ensure_all_active_medications_matched(
            prescription_id=prescription.id,
            user_id=intruder.id,
            expected_prescription_version_id=prescription.active_version_id,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "PRESCRIPTION_NOT_FOUND"


async def test_preflight_rejects_stale_expected_prescription_version(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="preflight-stale@example.com")
    prescription, medications = await _create_prescription(db_session, user=owner)
    for medication in medications:
        await _create_identification(db_session, medication=medication)

    with pytest.raises(ApiError) as exc_info:
        await _service(db_session).ensure_all_active_medications_matched(
            prescription_id=prescription.id,
            user_id=owner.id,
            expected_prescription_version_id=uuid4(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "PRESCRIPTION_VERSION_CONFLICT"
    assert exc_info.value.details[0].reason == "STALE"


async def test_preflight_returns_matches_in_medication_display_order(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="preflight-order@example.com")
    prescription, medications = await _create_prescription(db_session, user=owner, medication_count=3)
    for medication in reversed(medications):
        await _create_identification(db_session, medication=medication)

    result = await _service(db_session).ensure_all_active_medications_matched(
        prescription_id=prescription.id,
        user_id=owner.id,
        expected_prescription_version_id=prescription.active_version_id,
    )

    assert [item.prescription_version_medication_id for item in result.matched_medications] == [
        medication.id for medication in medications
    ]


async def test_preflight_does_not_include_sensitive_medication_text_in_result(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="preflight-shape@example.com")
    prescription, medications = await _create_prescription(db_session, user=owner)
    for medication in medications:
        await _create_identification(db_session, medication=medication)

    result = await _service(db_session).ensure_all_active_medications_matched(
        prescription_id=prescription.id,
        user_id=owner.id,
        expected_prescription_version_id=prescription.active_version_id,
    )

    assert all(not hasattr(item, "medication_name") for item in result.matched_medications)
    assert all(not hasattr(item, "strength_text") for item in result.matched_medications)
