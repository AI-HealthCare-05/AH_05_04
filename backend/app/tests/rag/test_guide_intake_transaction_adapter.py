from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.errors import ApiError
from app.core.utils.idempotency import compute_request_hash
from app.models.async_jobs import AiJob, AiJobStatus, AiJobType, DomainType, IdempotencyRecord, OutboxEvent
from app.models.guides import Guide, GuideGenerationStatus
from app.models.knowledge import RagKnowledgeDistanceMetric, RagKnowledgeIndex
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
from app.models.rag_request_authority import RagRequestGuardAuthority
from app.models.rag_runtime import (
    AiJobExecutionContext,
    AiJobExecutionIdentification,
    GuideRetrievalBindingManifest,
    RagRuntimeBundleStatus,
    RagRuntimeEnvironmentStatus,
)
from app.models.users import Gender, User
from app.repositories.async_job_repository import AsyncJobRepository
from app.repositories.guide_repository import GuideRepository
from app.repositories.medication_candidate_repository import MedicationCandidateRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.repositories.rag_request_guard_runtime_binding_repository import RagRequestGuardRuntimeBindingRepository
from app.repositories.rag_runtime_repository import (
    RagRuntimeEnvironmentCreate,
    RagRuntimeExecutionManifestCreate,
    RagRuntimeReleaseBundleCreate,
    RagRuntimeRepository,
)
from app.services.guide_intake import (
    GUIDE_JOB_INTAKE_METHOD,
    GUIDE_JOB_INTAKE_ROUTE_TEMPLATE,
    GuideJobIntakeTransactionAdapter,
    GuideRuntimeContextBindingError,
    GuideRuntimeContextSnapshot,
)
from app.services.job_intake import IdempotencyKeyConflictError, JobIntakeService
from app.services.medication_identification import MedicationIdentificationService
from app.services.rag_preflight import RagPreflightService
from rag_runtime.request_authority import (
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
    compute_request_guard_authority_ref,
)
from rag_runtime.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingObservation,
    RequestGuardRuntimeBindingRef,
    canonical_scope_manifest_hash,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode


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


async def _create_runtime_context(
    session: AsyncSession,
    *,
    user: User,
    environment_code: str = "LOCAL",
) -> GuideRuntimeContextSnapshot:
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
            environment_code=environment_code,
            catalog_version="catalog-1.0.0",
            catalog_manifest_hash=suffix.ljust(64, "9"),
            candidate_index_manifest_hash=suffix.ljust(64, "3"),
        )
    )
    environment = await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code=environment_code,
            environment_status=RagRuntimeEnvironmentStatus.ACTIVE,
            active_bundle_id=bundle.id,
            active_bundle_manifest_hash=bundle.bundle_manifest_hash,
            environment_revision=2,
        )
    )
    knowledge_index = RagKnowledgeIndex(
        index_code=f"guide-intake-index-{suffix}",
        index_version="1.0.0",
        corpus_manifest_hash=_hash("a"),
        embedding_manifest_hash=_hash("b"),
        index_configuration_hash=_hash("c"),
        embedding_model_ref="synthetic-embedding",
        embedding_model_version="1.0.0",
        embedding_dimension=3,
        distance_metric=RagKnowledgeDistanceMetric.COSINE,
        member_count=0,
        knowledge_index_lock_marker=0,
    )
    session.add(knowledge_index)
    await session.flush()
    retrieval_binding = GuideRetrievalBindingManifest(
        manifest_version="guide-retrieval-binding@1",
        manifest_hash=suffix.ljust(64, "d"),
        runtime_release_bundle_id=bundle.id,
        runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
        runtime_execution_manifest_id=manifest.id,
        runtime_execution_manifest_hash=manifest.manifest_hash,
        knowledge_index_id=knowledge_index.id,
        evidence_index_code=knowledge_index.index_code,
        evidence_index_version=knowledge_index.index_version,
        evidence_index_configuration_hash=knowledge_index.index_configuration_hash,
        member_bindings_json=[],
        retrieval_configuration_json={},
        retrieval_configuration_hash=_hash("e"),
        filter_snapshot_code="guide-intake-filter",
        filter_snapshot_version="1.0.0",
        filter_snapshot_hash=_hash("f"),
        source_manifest_hash=_hash("0"),
    )
    session.add(retrieval_binding)
    await session.flush()
    request_guard_runtime_binding_ref = await _record_request_guard_runtime_binding(
        session,
        user=user,
        bundle_id=bundle.id,
        bundle_manifest_hash=bundle.bundle_manifest_hash,
        environment_code=environment_code,
    )
    return GuideRuntimeContextSnapshot(
        runtime_environment_id=environment.id,
        runtime_environment_revision=environment.environment_revision,
        runtime_release_bundle_id=bundle.id,
        runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
        runtime_execution_manifest_id=manifest.id,
        runtime_execution_manifest_hash=manifest.manifest_hash,
        guide_retrieval_binding_manifest_id=retrieval_binding.id,
        guide_retrieval_binding_manifest_hash=retrieval_binding.manifest_hash,
        runtime_guard_decision_ref="guard:guide-full-request",
        request_guard_runtime_binding_ref=request_guard_runtime_binding_ref,
        patient_context_digest=suffix.ljust(64, "5"),
        source_scope_manifest_hash=suffix.ljust(64, "6"),
    )


async def _record_request_guard_runtime_binding(
    session: AsyncSession,
    *,
    user: User,
    bundle_id,
    bundle_manifest_hash: str,
    environment_code: str,
) -> RequestGuardRuntimeBindingRef:
    legacy_ref = compute_request_guard_authority_ref(
        user_id=user.id,
        request_operation_code="GUIDE_SYNC_ANSWER",
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
    )
    existing_legacy = await session.scalar(
        select(RagRequestGuardAuthority).where(
            RagRequestGuardAuthority.artifact_code == legacy_ref.artifact_code,
            RagRequestGuardAuthority.artifact_version == legacy_ref.version,
            RagRequestGuardAuthority.artifact_content_sha256 == legacy_ref.content_sha256,
        )
    )
    if existing_legacy is None:
        session.add(
            RagRequestGuardAuthority(
                id=uuid4(),
                artifact_code=legacy_ref.artifact_code,
                artifact_version=legacy_ref.version,
                artifact_content_sha256=legacy_ref.content_sha256,
                user_id=user.id,
                request_operation_code="GUIDE_SYNC_ANSWER",
                decision_stage=RequestAuthorityDecisionStage.REQUEST.value,
            )
        )
        await session.flush()
    scopes = ("GUIDE",)
    return await RagRequestGuardRuntimeBindingRepository(session).record(
        RequestGuardRuntimeBindingObservation(
            request_guard_decision_id=uuid4(),
            actual_decision_outcome=RequestAuthorityDecisionOutcome.PASS,
            user_id=user.id,
            request_operation_code="GUIDE_SYNC_ANSWER",
            decision_stage=RequestAuthorityDecisionStage.REQUEST,
            environment=RuntimeEnvironmentCode(environment_code),
            bundle_id=bundle_id,
            bundle_manifest_hash=bundle_manifest_hash,
            request_scope_codes=scopes,
            scope_manifest_hash=canonical_scope_manifest_hash(scopes),
            legacy_request_authority_ref=legacy_ref,
        )
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
    runtime_context = await _create_runtime_context(db_session, user=user)

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
    assert context.guide_retrieval_binding_manifest_id == runtime_context.guide_retrieval_binding_manifest_id
    assert context.guide_retrieval_binding_manifest_hash == runtime_context.guide_retrieval_binding_manifest_hash
    assert context.runtime_guard_decision_ref == "guard:guide-full-request"
    assert (
        context.request_guard_runtime_binding_artifact_code
        == runtime_context.request_guard_runtime_binding_ref.artifact_code
    )
    assert (
        context.request_guard_runtime_binding_artifact_version
        == runtime_context.request_guard_runtime_binding_ref.version
    )
    assert (
        context.request_guard_runtime_binding_content_sha256
        == runtime_context.request_guard_runtime_binding_ref.content_sha256
    )

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
    runtime_context = await _create_runtime_context(db_session, user=user)

    first = await _adapter(db_session).accept_guide_job(
        user=user,
        prescription_id=prescription.id,
        idempotency_key="guide-intake-key-000000000002",
        trace_id="b" * 32,
        runtime_context=runtime_context,
    )

    changed_runtime_context = await _create_runtime_context(db_session, user=user, environment_code="TEST")

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

    idempotency_record = await db_session.scalar(
        select(IdempotencyRecord).where(IdempotencyRecord.job_id == first.job.id)
    )
    assert idempotency_record is not None
    assert idempotency_record.request_hash == compute_request_hash(
        {
            "method": GUIDE_JOB_INTAKE_METHOD,
            "route_template": GUIDE_JOB_INTAKE_ROUTE_TEMPLATE,
            "job_type": AiJobType.GUIDE.value,
            "prescription_id": str(prescription.id),
            "prescription_version_id": str(prescription.active_version_id),
        }
    )


async def test_accept_guide_job_rolls_back_when_preflight_fails(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email=f"gint-fail-{uuid4().hex[:8]}@test.local")
    prescription = await _create_prescription(db_session, user=user)
    runtime_context = await _create_runtime_context(db_session, user=user)

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
    runtime_context = await _create_runtime_context(db_session, user=user)

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
    runtime_context = await _create_runtime_context(db_session, user=owner)

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
    runtime_context = await _create_runtime_context(db_session, user=user)
    mismatched_context = replace(runtime_context, runtime_release_bundle_manifest_hash="8" * 64)

    with pytest.raises(GuideRuntimeContextBindingError):
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


async def test_accept_guide_job_rejects_typed_request_guard_runtime_binding_for_a_different_user(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email=f"gint-guard-owner-{uuid4().hex[:8]}@test.local")
    other_user = await _create_user(db_session, email=f"gint-guard-other-{uuid4().hex[:8]}@test.local")
    prescription = await _create_matched_prescription(db_session, user=user)
    runtime_context = await _create_runtime_context(db_session, user=user)
    bundle = await RagRuntimeRepository(db_session).get_release_bundle_by_id(runtime_context.runtime_release_bundle_id)
    assert bundle is not None
    foreign_ref = await _record_request_guard_runtime_binding(
        db_session,
        user=other_user,
        bundle_id=bundle.id,
        bundle_manifest_hash=bundle.bundle_manifest_hash,
        environment_code=bundle.environment_code,
    )

    with pytest.raises(GuideRuntimeContextBindingError, match="request guard runtime binding"):
        await _adapter(db_session).accept_guide_job(
            user=user,
            prescription_id=prescription.id,
            idempotency_key="guide-intake-key-000000000008",
            trace_id="0" * 32,
            runtime_context=replace(runtime_context, request_guard_runtime_binding_ref=foreign_ref),
        )

    assert await _count(db_session, AiJob) == 0
    assert await _count(db_session, Guide) == 0
    assert await _count(db_session, AiJobExecutionContext) == 0


async def test_accept_guide_job_outbox_reference_contains_no_sensitive_payload(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email=f"gint-outbox-{uuid4().hex[:8]}@test.local")
    prescription = await _create_matched_prescription(db_session, user=user)
    runtime_context = await _create_runtime_context(db_session, user=user)

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
