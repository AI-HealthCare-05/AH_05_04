from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_jobs import AiJob, AiJobStatus, AiJobType
from app.models.chat import ChatMessage, ChatRole, ChatSession
from app.models.guides import Guide
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Prescription, PrescriptionVersionMedication
from app.models.profiles import Profile, ProfileType
from app.models.rag_candidate import (
    MedicationCandidateSearch,
    MedicationCandidateSearchResult,
    MedicationCandidateSearchStatus,
    MedicationIdentification,
    MedicationIdentificationSource,
    MedicationIdentificationStatus,
)
from app.models.rag_runtime import RagRuntimeBundleStatus, RagRuntimeEnvironmentStatus
from app.models.users import Gender, User
from app.repositories.prescription_repository import PrescriptionRepository
from app.repositories.rag_runtime_repository import (
    AiJobExecutionContextCreate,
    AiJobExecutionIdentificationCreate,
    AiJobIntakeContextCreate,
    RagRuntimeEnvironmentCreate,
    RagRuntimeExecutionManifestCreate,
    RagRuntimeReleaseBundleCreate,
    RagRuntimeRepository,
)


def _hash(char: str) -> str:
    return char * 64


async def _create_user(session: AsyncSession) -> tuple[User, Profile]:
    user = User(
        email=f"pfctx-{uuid4().hex[:10]}@ex.com",
        hashed_password="hashed-password",
        name="합성 사용자",
        gender=Gender.FEMALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    session.add(user)
    await session.flush()
    profile = Profile(user_id=user.id, profile_type=ProfileType.SELF, display_name=user.name)
    session.add(profile)
    await session.flush()
    return user, profile


async def _create_prescription(
    session: AsyncSession, *, user: User, profile: Profile, two_medications: bool = False
) -> Prescription:
    token = uuid4().hex
    document = MedicalDocument(
        uploaded_by=user.id,
        profile_id=profile.id,
        original_file_name="synthetic.jpg",
        object_key=f"synthetic/{token}.jpg",
        file_mime_type="image/jpeg",
        file_size_bytes=100,
    )
    session.add(document)
    await session.flush()
    ocr_job = OcrJob(document_id=document.id)
    session.add(ocr_job)
    await session.flush()
    return await PrescriptionRepository(session).create_with_medications(
        document=document,
        source_ocr_job=ocr_job,
        prescribed_date=date(2026, 9, 10),
        confirmed_at=datetime.now(UTC),
        medications=[{"medication_name": "합성 식별약", "display_order": 1}]
        + ([{"medication_name": "다른 합성약", "display_order": 2}] if two_medications else []),
    )


async def _create_runtime_graph(session: AsyncSession):
    repository = RagRuntimeRepository(session)
    suffix = uuid4().hex[:8]
    manifest = await repository.create_execution_manifest(
        RagRuntimeExecutionManifestCreate(
            manifest_key=f"preflight-runtime-{suffix}",
            manifest_version="1.0.0",
            manifest_hash=_hash("1"),
            schema_version="runtime-manifest-v1",
            git_commit_sha="abcdef1",
            guard_policy_ref="guard-policy:test",
        )
    )
    bundle = await repository.create_release_bundle(
        RagRuntimeReleaseBundleCreate(
            bundle_key=f"preflight-bundle-{suffix}",
            bundle_version="1.0.0",
            bundle_status=RagRuntimeBundleStatus.READY,
            execution_manifest_id=manifest.id,
            bundle_manifest_hash=_hash("2"),
            candidate_index_manifest_hash=_hash("3"),
        )
    )
    environment = await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code=f"preflight-{suffix}",
            environment_status=RagRuntimeEnvironmentStatus.ACTIVE,
            active_bundle_id=bundle.id,
            active_bundle_manifest_hash=bundle.bundle_manifest_hash,
            environment_revision=2,
        )
    )
    return manifest, bundle, environment


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
        query_digest=_hash("4"),
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
        canonical_code="SYNTHETIC-001",
        product_name="합성 식별약 10mg",
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
            confirmed_at=datetime.now(UTC),
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
            rejected_at=datetime.now(UTC),
        )
    session.add(identification)
    await session.flush()
    return identification


async def _create_chat_domain(
    session: AsyncSession,
    *,
    profile: Profile,
    prescription: Prescription,
) -> ChatMessage:
    chat_session = ChatSession(
        prescription_id=prescription.id,
        prescription_version_id=prescription.active_version_id,
        profile_id=profile.id,
    )
    session.add(chat_session)
    await session.flush()
    message = ChatMessage(
        session_id=chat_session.id,
        message_seq=1,
        role=ChatRole.USER,
        content="합성 질문",
    )
    session.add(message)
    await session.flush()
    return message


async def persist_and_verify_chat_context(db_session: AsyncSession) -> None:
    user, profile = await _create_user(db_session)
    prescription = await _create_prescription(db_session, user=user, profile=profile)
    medication = await db_session.scalar(
        select(PrescriptionVersionMedication).where(
            PrescriptionVersionMedication.prescription_version_id == prescription.active_version_id
        )
    )
    assert medication is not None
    identification = await _create_identification(db_session, medication=medication)
    chat_message = await _create_chat_domain(db_session, profile=profile, prescription=prescription)
    manifest, bundle, environment = await _create_runtime_graph(db_session)
    chat_job = AiJob(
        user_id=user.id,
        job_type=AiJobType.CHAT,
        status=AiJobStatus.PENDING,
        prescription_version_id=prescription.active_version_id,
        max_attempts=2,
        available_at=datetime.now(UTC),
    )
    db_session.add(chat_job)
    await db_session.flush()

    repository = RagRuntimeRepository(db_session)
    intake = await repository.create_intake_context(
        AiJobIntakeContextCreate(
            ai_job_id=chat_job.id,
            chat_message_id=chat_message.id,
            prescription_version_id=prescription.active_version_id,
            runtime_environment_id=environment.id,
            runtime_environment_revision=environment.environment_revision,
            runtime_release_bundle_id=bundle.id,
            runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
            runtime_execution_manifest_id=manifest.id,
            runtime_execution_manifest_hash=manifest.manifest_hash,
            runtime_guard_decision_ref="guard:intake-pass",
            question_digest=_hash("5"),
            patient_context_digest=_hash("6"),
        )
    )
    execution = await repository.create_execution_context(
        AiJobExecutionContextCreate(
            ai_job_id=chat_job.id,
            intake_context_id=intake.id,
            chat_message_id=chat_message.id,
            prescription_version_id=prescription.active_version_id,
            runtime_environment_id=environment.id,
            runtime_environment_revision=environment.environment_revision,
            runtime_release_bundle_id=bundle.id,
            runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
            runtime_execution_manifest_id=manifest.id,
            runtime_execution_manifest_hash=manifest.manifest_hash,
            runtime_guard_decision_ref="guard:full-pass",
            patient_context_digest=_hash("6"),
            source_scope_manifest_hash=_hash("7"),
        )
    )
    pinned = await repository.create_execution_identification(
        AiJobExecutionIdentificationCreate(
            execution_context_id=execution.id,
            medication_identification_id=identification.id,
            prescription_version_medication_id=medication.id,
        )
    )

    assert await repository.get_intake_context_by_job(chat_job.id) == intake
    assert await repository.get_execution_context_by_job(chat_job.id) == execution
    assert await repository.list_execution_identifications(execution.id) == [pinned]


async def test_repository_persists_chat_intake_and_execution_context(db_session: AsyncSession) -> None:
    await persist_and_verify_chat_context(db_session)


async def test_execution_identification_requires_matching_medication_and_matched_status(
    db_session: AsyncSession,
) -> None:
    user, profile = await _create_user(db_session)
    prescription = await _create_prescription(db_session, user=user, profile=profile, two_medications=True)
    medication = await db_session.scalar(
        select(PrescriptionVersionMedication).where(
            PrescriptionVersionMedication.prescription_version_id == prescription.active_version_id,
            PrescriptionVersionMedication.display_order == 1,
        )
    )
    assert medication is not None
    other_medication = await db_session.scalar(
        select(PrescriptionVersionMedication).where(
            PrescriptionVersionMedication.prescription_version_id == prescription.active_version_id,
            PrescriptionVersionMedication.display_order == 2,
        )
    )
    assert other_medication is not None
    matched_identification = await _create_identification(db_session, medication=medication)
    unresolved_identification = await _create_identification(
        db_session,
        medication=other_medication,
        status=MedicationIdentificationStatus.UNRESOLVED,
    )
    chat_message = await _create_chat_domain(db_session, profile=profile, prescription=prescription)
    manifest, bundle, environment = await _create_runtime_graph(db_session)
    chat_job = AiJob(
        user_id=user.id,
        job_type=AiJobType.CHAT,
        status=AiJobStatus.PENDING,
        prescription_version_id=prescription.active_version_id,
        max_attempts=2,
        available_at=datetime.now(UTC),
    )
    db_session.add(chat_job)
    await db_session.flush()

    repository = RagRuntimeRepository(db_session)
    execution = await repository.create_execution_context(
        AiJobExecutionContextCreate(
            ai_job_id=chat_job.id,
            chat_message_id=chat_message.id,
            prescription_version_id=prescription.active_version_id,
            runtime_environment_id=environment.id,
            runtime_environment_revision=environment.environment_revision,
            runtime_release_bundle_id=bundle.id,
            runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
            runtime_execution_manifest_id=manifest.id,
            runtime_execution_manifest_hash=manifest.manifest_hash,
            runtime_guard_decision_ref="guard:full-pass",
        )
    )

    with pytest.raises(ValueError, match="MATCHED identification"):
        await repository.create_execution_identification(
            AiJobExecutionIdentificationCreate(
                execution_context_id=execution.id,
                medication_identification_id=matched_identification.id,
                prescription_version_medication_id=other_medication.id,
            )
        )

    with pytest.raises(ValueError, match="MATCHED identification"):
        await repository.create_execution_identification(
            AiJobExecutionIdentificationCreate(
                execution_context_id=execution.id,
                medication_identification_id=unresolved_identification.id,
                prescription_version_medication_id=other_medication.id,
            )
        )


async def test_execution_context_requires_exactly_one_domain_reference(db_session: AsyncSession) -> None:
    user, profile = await _create_user(db_session)
    prescription = await _create_prescription(db_session, user=user, profile=profile)
    guide = Guide(
        prescription_id=prescription.id,
        prescription_version_id=prescription.active_version_id,
        profile_id=profile.id,
    )
    chat_message = await _create_chat_domain(db_session, profile=profile, prescription=prescription)
    manifest, bundle, environment = await _create_runtime_graph(db_session)
    job = AiJob(
        user_id=user.id,
        job_type=AiJobType.GUIDE,
        status=AiJobStatus.PENDING,
        prescription_version_id=prescription.active_version_id,
        max_attempts=3,
        available_at=datetime.now(UTC),
    )
    db_session.add_all([guide, job])
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await RagRuntimeRepository(db_session).create_execution_context(
            AiJobExecutionContextCreate(
                ai_job_id=job.id,
                guide_id=guide.id,
                chat_message_id=chat_message.id,
                prescription_version_id=prescription.active_version_id,
                runtime_environment_id=environment.id,
                runtime_environment_revision=environment.environment_revision,
                runtime_release_bundle_id=bundle.id,
                runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
                runtime_execution_manifest_id=manifest.id,
                runtime_execution_manifest_hash=manifest.manifest_hash,
                runtime_guard_decision_ref="guard:bad-domain",
            )
        )
