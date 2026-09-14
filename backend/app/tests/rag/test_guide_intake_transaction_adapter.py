from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.errors import ApiError
from app.models.async_jobs import AiJob, AiJobStatus, AiJobType, DomainType, IdempotencyRecord, OutboxEvent
from app.models.guides import Guide, GuideGenerationStatus
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
from app.models.rag_runtime import (
    AiJobExecutionContext,
    AiJobExecutionIdentification,
    RagRuntimeBundleStatus,
    RagRuntimeEnvironmentStatus,
)
from app.models.users import Gender, User
from app.repositories.async_job_repository import AsyncJobRepository
from app.repositories.guide_repository import GuideRepository
from app.repositories.medication_candidate_repository import MedicationCandidateRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.repositories.rag_runtime_repository import (
    RagRuntimeEnvironmentCreate,
    RagRuntimeExecutionManifestCreate,
    RagRuntimeReleaseBundleCreate,
    RagRuntimeRepository,
)
from app.services.guide_intake import GuideJobIntakeTransactionAdapter, GuideRuntimeContextSnapshot
from app.services.job_intake import IdempotencyKeyConflictError, JobIntakeService
from app.services.medication_identification import MedicationIdentificationService
from app.services.rag_preflight import RagPreflightService


def _hash(char: str) -> str:
    return char * 64


async def _create_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        email=email,
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
    return user


async def _create_prescription(session: AsyncSession, *, user: User, medication_count: int = 2) -> Prescription:
    profile = await session.scalar(
        select(Profile).where(Profile.user_id == user.id, Profile.profile_type == ProfileType.SELF)
    )
    assert profile is not None
    document = MedicalDocument(
        uploaded_by=user.id,
        profile_id=profile.id,
        original_file_name="synthetic.jpg",
        object_key=f"synthetic/{uuid4()}.jpg",
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
        prescribed_date=date.today(),
        confirmed_at=datetime.now(UTC),
        medications=[
            {"medication_name": f"합성 식별약 {index}", "strength_text": "10mg", "display_order": index}
            for index in range(1, medication_count + 1)
        ],
    )


async def _active_medications(
    session: AsyncSession,
    prescription: Prescription,
) -> list[PrescriptionVersionMedication]:
    result = await session.execute(
        select(PrescriptionVersionMedication)
        .where(PrescriptionVersionMedication.prescription_version_id == prescription.active_version_id)
        .order_by(PrescriptionVersionMedication.display_order)
    )
    return list(result.scalars().all())


async def _create_identification(
    session: AsyncSession,
    *,
    medication: PrescriptionVersionMedication,
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
    session.add(identification)
    await session.flush()
    return identification


async def _create_matched_prescription(
    session: AsyncSession,
    *,
    user: User,
) -> Prescription:
    prescription = await _create_prescription(session, user=user)
    for medication in await _active_medications(session, prescription):
        await _create_identification(session, medication=medication)
    return prescription


async def _create_runtime_context(session: AsyncSession) -> GuideRuntimeContextSnapshot:
    repository = RagRuntimeRepository(session)
    suffix = uuid4().hex[:8]
    manifest = await repository.create_execution_manifest(
        RagRuntimeExecutionManifestCreate(
            manifest_key=f"guide-intake-{suffix}",
            manifest_version="1.0.0",
            manifest_hash=suffix.ljust(64, "1"),
            schema_version="runtime-manifest-v1",
            git_commit_sha="abcdef1",
            guard_policy_ref="guard-policy:test",
        )
    )
    bundle = await repository.create_release_bundle(
        RagRuntimeReleaseBundleCreate(
            bundle_key=f"guide-bundle-{suffix}",
            bundle_version="1.0.0",
            bundle_status=RagRuntimeBundleStatus.READY,
            execution_manifest_id=manifest.id,
            bundle_manifest_hash=suffix.ljust(64, "2"),
            environment_code="local",
            catalog_version="catalog-1.0.0",
            catalog_manifest_hash=suffix.ljust(64, "9"),
            candidate_index_manifest_hash=suffix.ljust(64, "3"),
        )
    )
    environment = await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code=f"guide-env-{suffix}",
            environment_status=RagRuntimeEnvironmentStatus.ACTIVE,
            active_bundle_id=bundle.id,
            active_bundle_manifest_hash=bundle.bundle_manifest_hash,
            environment_revision=2,
        )
    )
    return GuideRuntimeContextSnapshot(
        runtime_environment_id=environment.id,
        runtime_environment_revision=environment.environment_revision,
        runtime_release_bundle_id=bundle.id,
        runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
        runtime_execution_manifest_id=manifest.id,
        runtime_execution_manifest_hash=manifest.manifest_hash,
        runtime_guard_decision_ref="guard:guide-full-request",
        patient_context_digest=suffix.ljust(64, "5"),
        source_scope_manifest_hash=suffix.ljust(64, "6"),
    )


def _adapter(session: AsyncSession) -> GuideJobIntakeTransactionAdapter:
    return GuideJobIntakeTransactionAdapter(
        guide_repository=GuideRepository(session),
        preflight_service=RagPreflightService(MedicationIdentificationService(MedicationCandidateRepository(session))),
        runtime_repository=RagRuntimeRepository(session),
        job_intake_service=JobIntakeService(AsyncJobRepository(session)),
    )


async def _count(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_accept_guide_job_creates_job_guide_context_identifications_and_outbox(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email=f"gint-{uuid4().hex[:8]}@test.local")
    prescription = await _create_prescription(db_session, user=user)
    medications = await _active_medications(db_session, prescription)
    identifications = [await _create_identification(db_session, medication=medication) for medication in medications]
    runtime_context = await _create_runtime_context(db_session)

    result = await _adapter(db_session).accept_guide_job(
        user=user,
        prescription_id=prescription.id,
        idempotency_key="guide-intake-key-000000000001",
        trace_id="a" * 32,
        runtime_context=runtime_context,
    )

    assert result.is_duplicate is False
    assert result.job.job_type is AiJobType.GUIDE
    assert result.job.status is AiJobStatus.PENDING
    assert result.job.prescription_version_id == prescription.active_version_id
    assert result.guide.ai_job_id == result.job.id
    assert result.guide.generation_status is GuideGenerationStatus.PENDING

    outbox = await db_session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == result.job.id))
    assert outbox is not None
    assert outbox.domain_type is DomainType.GUIDE
    assert outbox.domain_id == result.guide.id
    assert result.job.expected_event_id == outbox.event_id

    context = await db_session.scalar(
        select(AiJobExecutionContext).where(AiJobExecutionContext.ai_job_id == result.job.id)
    )
    assert context is not None
    assert context.guide_id == result.guide.id
    assert context.prescription_version_id == prescription.active_version_id
    assert context.runtime_release_bundle_id == runtime_context.runtime_release_bundle_id
    assert context.runtime_execution_manifest_hash == runtime_context.runtime_execution_manifest_hash
    assert context.runtime_guard_decision_ref == "guard:guide-full-request"

    pinned = await db_session.execute(
        select(AiJobExecutionIdentification).where(AiJobExecutionIdentification.execution_context_id == context.id)
    )
    pinned_rows = list(pinned.scalars().all())
    assert {row.medication_identification_id for row in pinned_rows} == {item.id for item in identifications}
    assert {row.prescription_version_medication_id for row in pinned_rows} == {item.id for item in medications}


async def test_accept_guide_job_reuses_same_idempotency_key_without_duplicate_rows(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email=f"gint-dupe-{uuid4().hex[:8]}@test.local")
    prescription = await _create_matched_prescription(db_session, user=user)
    runtime_context = await _create_runtime_context(db_session)

    first = await _adapter(db_session).accept_guide_job(
        user=user,
        prescription_id=prescription.id,
        idempotency_key="guide-intake-key-000000000002",
        trace_id="b" * 32,
        runtime_context=runtime_context,
    )

    changed_runtime_context = await _create_runtime_context(db_session)

    second = await _adapter(db_session).accept_guide_job(
        user=user,
        prescription_id=prescription.id,
        idempotency_key="guide-intake-key-000000000002",
        trace_id="c" * 32,
        runtime_context=changed_runtime_context,
    )

    assert second.is_duplicate is True
    assert second.job.id == first.job.id
    assert second.guide.id == first.guide.id
    assert await _count(db_session, AiJob) == 1
    assert await _count(db_session, Guide) == 1
    assert await _count(db_session, OutboxEvent) == 1
    assert await _count(db_session, AiJobExecutionContext) == 1
    assert await _count(db_session, IdempotencyRecord) == 1

    context = await db_session.scalar(
        select(AiJobExecutionContext).where(AiJobExecutionContext.ai_job_id == first.job.id)
    )
    assert context is not None
    assert context.runtime_release_bundle_id == runtime_context.runtime_release_bundle_id
    assert context.runtime_release_bundle_id != changed_runtime_context.runtime_release_bundle_id


async def test_accept_guide_job_rolls_back_when_preflight_fails(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email=f"gint-fail-{uuid4().hex[:8]}@test.local")
    prescription = await _create_prescription(db_session, user=user)
    runtime_context = await _create_runtime_context(db_session)

    with pytest.raises(ApiError) as exc_info:
        await _adapter(db_session).accept_guide_job(
            user=user,
            prescription_id=prescription.id,
            idempotency_key="guide-intake-key-000000000003",
            trace_id="d" * 32,
            runtime_context=runtime_context,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "PRESCRIPTION_MEDICATION_IDENTIFICATION_INCOMPLETE"
    assert await _count(db_session, AiJob) == 0
    assert await _count(db_session, Guide) == 0
    assert await _count(db_session, OutboxEvent) == 0
    assert await _count(db_session, AiJobExecutionContext) == 0
    assert await _count(db_session, AiJobExecutionIdentification) == 0
    assert await _count(db_session, IdempotencyRecord) == 0


async def test_accept_guide_job_rejects_idempotency_conflict_without_duplicate_rows(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email=f"gint-conflict-{uuid4().hex[:8]}@test.local")
    prescription = await _create_matched_prescription(db_session, user=user)
    runtime_context = await _create_runtime_context(db_session)

    first = await _adapter(db_session).accept_guide_job(
        user=user,
        prescription_id=prescription.id,
        idempotency_key="guide-intake-key-000000000004",
        trace_id="e" * 32,
        runtime_context=runtime_context,
    )
    other_prescription = await _create_matched_prescription(db_session, user=user)

    with pytest.raises(IdempotencyKeyConflictError):
        await _adapter(db_session).accept_guide_job(
            user=user,
            prescription_id=other_prescription.id,
            idempotency_key="guide-intake-key-000000000004",
            trace_id="f" * 32,
            runtime_context=runtime_context,
        )

    assert await _count(db_session, AiJob) == 1
    assert await _count(db_session, Guide) == 1
    assert await _count(db_session, OutboxEvent) == 1
    assert await _count(db_session, AiJobExecutionContext) == 1
    assert await _count(db_session, IdempotencyRecord) == 1
    stored_guide = await GuideRepository(db_session).get_by_ai_job_id(ai_job_id=first.job.id)
    assert stored_guide is not None
    assert stored_guide.id == first.guide.id


async def test_accept_guide_job_hides_other_users_prescription_without_side_effects(
    db_session: AsyncSession,
) -> None:
    owner = await _create_user(db_session, email=f"gint-owner-{uuid4().hex[:8]}@test.local")
    intruder = await _create_user(db_session, email=f"gint-intruder-{uuid4().hex[:8]}@test.local")
    prescription = await _create_matched_prescription(db_session, user=owner)
    runtime_context = await _create_runtime_context(db_session)

    with pytest.raises(ApiError) as exc_info:
        await _adapter(db_session).accept_guide_job(
            user=intruder,
            prescription_id=prescription.id,
            idempotency_key="guide-intake-key-000000000005",
            trace_id="7" * 32,
            runtime_context=runtime_context,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "PRESCRIPTION_NOT_FOUND"
    assert await _count(db_session, AiJob) == 0
    assert await _count(db_session, Guide) == 0
    assert await _count(db_session, OutboxEvent) == 0
    assert await _count(db_session, AiJobExecutionContext) == 0
    assert await _count(db_session, AiJobExecutionIdentification) == 0
    assert await _count(db_session, IdempotencyRecord) == 0


async def test_accept_guide_job_rolls_back_when_runtime_snapshot_mismatches(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email=f"gint-runtime-{uuid4().hex[:8]}@test.local")
    prescription = await _create_matched_prescription(db_session, user=user)
    runtime_context = await _create_runtime_context(db_session)
    mismatched_context = replace(runtime_context, runtime_release_bundle_manifest_hash="8" * 64)

    with pytest.raises(IntegrityError):
        await _adapter(db_session).accept_guide_job(
            user=user,
            prescription_id=prescription.id,
            idempotency_key="guide-intake-key-000000000006",
            trace_id="8" * 32,
            runtime_context=mismatched_context,
        )

    assert await _count(db_session, AiJob) == 0
    assert await _count(db_session, Guide) == 0
    assert await _count(db_session, OutboxEvent) == 0
    assert await _count(db_session, AiJobExecutionContext) == 0
    assert await _count(db_session, AiJobExecutionIdentification) == 0
    assert await _count(db_session, IdempotencyRecord) == 0


async def test_accept_guide_job_outbox_reference_contains_no_sensitive_payload(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email=f"gint-outbox-{uuid4().hex[:8]}@test.local")
    prescription = await _create_matched_prescription(db_session, user=user)
    runtime_context = await _create_runtime_context(db_session)

    result = await _adapter(db_session).accept_guide_job(
        user=user,
        prescription_id=prescription.id,
        idempotency_key="guide-intake-key-000000000007",
        trace_id="9" * 32,
        runtime_context=runtime_context,
    )

    outbox = await db_session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == result.job.id))
    assert outbox is not None
    assert outbox.domain_type is DomainType.GUIDE
    assert outbox.domain_id == result.guide.id
    assert not hasattr(outbox, "payload")
    assert result.guide.content is None
    assert result.guide.error_message is None
