from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.catalog.types import CatalogFreshnessStatus, CatalogVerificationStatus
from ai_worker.tasks.rag.runtime_bundle_builder import (
    MedicationCatalogBinding,
    RuntimeBundleArtifactKind,
    RuntimeBundleArtifactMemberInput,
    RuntimeBundleBuildRequest,
    RuntimeBundleCitationApprovalPinIdentity,
    RuntimeBundleMemberPurpose,
    RuntimeBundleSourceMemberInput,
    RuntimeExecutionManifestInput,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotVerificationStatus
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
from app.models.rag_runtime import (
    RagRuntimeBundleCitationApproval,
    RagRuntimeBundleSource,
    RagRuntimeBundleStatus,
    RagRuntimeEnvironment,
    RagRuntimeEnvironmentStatus,
    RagRuntimeExecutionManifest,
    RagRuntimeReleaseBundle,
)
from app.models.rag_source import RagSource, RagSourceSnapshot
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
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)
from app.repositories.rag_source_use_approval_repository import (
    RagSourceUseApprovalRepository,
    SourceUseApprovalCreate,
)
from app.services.guide_runtime_request import load_verified_guide_runtime_request_carrier
from app.services.rag_runtime_bundle_build import execute_runtime_bundle_build
from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import SourceUsePurpose


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
            environment_code="LOCAL",
            catalog_version="catalog-1.0.0",
            catalog_manifest_hash=_hash("9"),
            candidate_index_manifest_hash=_hash("3"),
        )
    )
    environment = await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code="LOCAL",
            environment_status=RagRuntimeEnvironmentStatus.ACTIVE,
            active_bundle_id=bundle.id,
            active_bundle_manifest_hash=bundle.bundle_manifest_hash,
            environment_revision=2,
        )
    )
    return manifest, bundle, environment


async def _create_runtime_source_snapshot(
    session: AsyncSession,
    label: str,
) -> tuple[RagSource, RagSourceSnapshot]:
    suffix = uuid4().hex[:10]
    repository = RagSourceCatalogRepository(session)
    source = await repository.create_source(
        RagSourceCreate(
            source_code=f"MFDS_CARRIER_{label}_{suffix}",
            display_name=f"MFDS Carrier {label}",
            owner_name="MFDS",
        )
    )
    endpoint = await repository.create_endpoint(
        RagSourceEndpointCreate(
            source_id=source.id,
            endpoint_code="PRODUCT_LIST",
            display_name="Product List",
        )
    )
    operation = await repository.create_operation(
        RagSourceOperationCreate(
            endpoint_id=endpoint.id,
            operation_code="LIST_PRODUCTS",
            display_name="List Products",
        )
    )
    snapshot = await repository.create_snapshot(
        RagSourceSnapshotCreate(
            operation_id=operation.id,
            source_version=f"api:2026-09-20:{suffix}",
            raw_manifest_checksum=_hash("a"),
            canonical_checksum=_hash("b"),
            schema_version="schema-v1",
            parser_version="parser-v1",
            normalization_version="normalization-v1",
            canonicalization_spec_version="canonical-v1",
            record_count=1,
            rejected_record_count=0,
            collected_at=datetime.now(UTC),
        )
    )
    return source, snapshot


def _runtime_source_member(
    snapshot: RagSourceSnapshot,
    purpose: RuntimeBundleMemberPurpose,
) -> RuntimeBundleSourceMemberInput:
    return RuntimeBundleSourceMemberInput(
        source_snapshot_id=str(snapshot.id),
        source_purpose=purpose,
        source_version=snapshot.source_version,
        canonical_checksum=snapshot.canonical_checksum,
        approval_version="approval-v1",
        scope_policy_hash=_hash("c"),
        freshness_policy_hash=_hash("d"),
        observed_environment="LOCAL",
        verification_status=SnapshotVerificationStatus.CURRENT,
        rejected_record_count=0,
        publication_approval_passed=True,
        freshness_eligible=True,
        provenance_valid=True,
        approval_expired=False,
        revocation_unresolved=False,
        scope_allowed=True,
    )


async def _create_canonical_runtime_graph(
    session: AsyncSession,
    *,
    actor: User,
) -> tuple[
    RagRuntimeExecutionManifest,
    RagRuntimeReleaseBundle,
    RagRuntimeEnvironment,
    tuple[RagRuntimeBundleSource, ...],
    RagRuntimeBundleCitationApproval,
]:
    _, catalog_snapshot = await _create_runtime_source_snapshot(session, "CATALOG")
    knowledge_source, knowledge_snapshot = await _create_runtime_source_snapshot(session, "KNOWLEDGE")
    approval = await RagSourceUseApprovalRepository(session).create_approval(
        SourceUseApprovalCreate(
            source_snapshot_id=knowledge_snapshot.id,
            source_code=knowledge_source.source_code,
            source_version=knowledge_snapshot.source_version,
            environment=RuntimeEnvironmentCode.LOCAL,
            purpose=SourceUsePurpose.PATIENT_CITATION,
            approval_version="citation-approval-v1",
            valid_from=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=1),
            actor_id=actor.id,
            evidence_ref="evidence://synthetic/guide-carrier",
        )
    )
    pin = RuntimeBundleCitationApprovalPinIdentity(
        source_snapshot_id=str(approval.identity.source_snapshot_id),
        source_use_approval_id=str(approval.id),
        source_code=approval.identity.source_code,
        source_version=approval.identity.source_version,
        approval_version=approval.identity.approval_version,
        environment=approval.identity.environment.value,
        purpose=approval.identity.purpose,
    )
    suffix = uuid4().hex[:8]
    request = RuntimeBundleBuildRequest(
        bundle_key=f"guide-carrier-{suffix}",
        bundle_version="1.0.0",
        environment_code="LOCAL",
        execution_manifest=RuntimeExecutionManifestInput(
            manifest_key=f"guide-carrier-manifest-{suffix}",
            manifest_version="1.0.0",
            schema_version="runtime-manifest-v1",
            git_commit_sha="abcdef1",
            worker_artifact_ref="worker:synthetic:guide-carrier",
        ),
        catalog=MedicationCatalogBinding(
            catalog_version="catalog-1.0.0",
            catalog_manifest_hash=_hash("9"),
            verification_status=CatalogVerificationStatus.APPROVED,
            freshness_status=CatalogFreshnessStatus.CURRENT,
            is_complete=True,
            source_snapshot_ids=(str(catalog_snapshot.id),),
        ),
        source_members=(
            _runtime_source_member(catalog_snapshot, RuntimeBundleMemberPurpose.CATALOG),
            _runtime_source_member(knowledge_snapshot, RuntimeBundleMemberPurpose.KNOWLEDGE),
        ),
        artifact_members=(
            RuntimeBundleArtifactMemberInput(
                artifact_kind=RuntimeBundleArtifactKind.CANDIDATE_INDEX,
                artifact_ref="candidate-index:synthetic",
                artifact_version="1.0.0",
                observed_environment="LOCAL",
                approval_effective=True,
                approval_expired=False,
                revocation_unresolved=False,
                manifest_hash=_hash("e"),
                catalog_version="catalog-1.0.0",
                catalog_manifest_hash=_hash("9"),
            ),
        ),
        citation_approval_pins=(pin,),
        created_by="backend-test",
    )
    execution = await execute_runtime_bundle_build(session, request)
    assert execution.persisted is not None
    persisted = execution.persisted
    environment = await RagRuntimeRepository(session).create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code="LOCAL",
            environment_status=RagRuntimeEnvironmentStatus.ACTIVE,
            active_bundle_id=persisted.bundle.id,
            active_bundle_manifest_hash=persisted.bundle.bundle_manifest_hash,
            environment_revision=2,
        )
    )
    return (
        persisted.execution_manifest,
        persisted.bundle,
        environment,
        persisted.bundle_sources,
        persisted.citation_approval_pins[0],
    )


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


async def _create_guide_domain(
    session: AsyncSession,
    *,
    profile: Profile,
    prescription: Prescription,
) -> Guide:
    guide = Guide(
        prescription_id=prescription.id,
        prescription_version_id=prescription.active_version_id,
        profile_id=profile.id,
    )
    session.add(guide)
    await session.flush()
    return guide


@dataclass(frozen=True, slots=True)
class _GuideCarrierFixture:
    job: AiJob
    execution_context_id: UUID
    bundle: RagRuntimeReleaseBundle
    manifest: RagRuntimeExecutionManifest
    environment: RagRuntimeEnvironment
    bundle_sources: tuple[RagRuntimeBundleSource, ...]
    citation_pin: RagRuntimeBundleCitationApproval


async def _create_guide_carrier_fixture(session: AsyncSession) -> _GuideCarrierFixture:
    user, profile = await _create_user(session)
    prescription = await _create_prescription(session, user=user, profile=profile)
    medication = await session.scalar(
        select(PrescriptionVersionMedication).where(
            PrescriptionVersionMedication.prescription_version_id == prescription.active_version_id
        )
    )
    assert medication is not None
    guide = await _create_guide_domain(session, profile=profile, prescription=prescription)
    identification = await _create_identification(session, medication=medication)
    manifest, bundle, environment, bundle_sources, citation_pin = await _create_canonical_runtime_graph(
        session,
        actor=user,
    )
    job = AiJob(
        user_id=user.id,
        job_type=AiJobType.GUIDE,
        status=AiJobStatus.PENDING,
        prescription_version_id=prescription.active_version_id,
        max_attempts=3,
        available_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    repository = RagRuntimeRepository(session)
    execution_context = await repository.create_execution_context(
        AiJobExecutionContextCreate(
            ai_job_id=job.id,
            guide_id=guide.id,
            prescription_version_id=prescription.active_version_id,
            runtime_environment_id=environment.id,
            runtime_environment_revision=environment.environment_revision,
            runtime_release_bundle_id=bundle.id,
            runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
            runtime_execution_manifest_id=manifest.id,
            runtime_execution_manifest_hash=manifest.manifest_hash,
            runtime_guard_decision_ref="guard:guide-runtime-pass",
            patient_context_digest=_hash("6"),
            source_scope_manifest_hash=_hash("7"),
        )
    )
    await repository.create_execution_identification(
        AiJobExecutionIdentificationCreate(
            execution_context_id=execution_context.id,
            medication_identification_id=identification.id,
            prescription_version_medication_id=medication.id,
        )
    )
    return _GuideCarrierFixture(
        job=job,
        execution_context_id=execution_context.id,
        bundle=bundle,
        manifest=manifest,
        environment=environment,
        bundle_sources=bundle_sources,
        citation_pin=citation_pin,
    )


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


async def test_verified_guide_runtime_request_carrier_reads_exact_pinned_runtime_scope(
    db_session: AsyncSession,
) -> None:
    fixture = await _create_guide_carrier_fixture(db_session)
    fixture.environment.active_bundle_id = None
    fixture.environment.active_bundle_manifest_hash = None
    fixture.environment.environment_revision += 1
    await db_session.flush()

    carrier = await load_verified_guide_runtime_request_carrier(db_session, fixture.job.id)

    assert carrier is not None
    assert carrier.execution_context_id == fixture.execution_context_id
    assert carrier.runtime_release_bundle_id == fixture.bundle.id
    assert carrier.runtime_release_bundle_manifest_hash == fixture.bundle.bundle_manifest_hash
    assert carrier.runtime_execution_manifest_id == fixture.manifest.id
    assert carrier.runtime_execution_manifest_hash == fixture.manifest.manifest_hash
    assert carrier.runtime_environment_revision == 2
    assert {source.source_snapshot_id for source in carrier.bundle_sources} == {
        source.source_snapshot_id for source in fixture.bundle_sources
    }


async def test_verified_guide_runtime_request_carrier_rejects_bundle_source_drift(
    db_session: AsyncSession,
) -> None:
    fixture = await _create_guide_carrier_fixture(db_session)
    fixture.bundle_sources[0].approval_version = "tampered-approval"
    await db_session.flush()

    assert await load_verified_guide_runtime_request_carrier(db_session, fixture.job.id) is None


async def test_verified_guide_runtime_request_carrier_rejects_execution_manifest_drift(
    db_session: AsyncSession,
) -> None:
    fixture = await _create_guide_carrier_fixture(db_session)
    fixture.manifest.model_ref = "tampered:model"
    await db_session.flush()

    assert await load_verified_guide_runtime_request_carrier(db_session, fixture.job.id) is None


async def test_verified_guide_runtime_request_carrier_rejects_citation_approval_pin_drift(
    db_session: AsyncSession,
) -> None:
    fixture = await _create_guide_carrier_fixture(db_session)
    fixture.citation_pin.approval_version = "tampered-approval"
    await db_session.flush()

    assert await load_verified_guide_runtime_request_carrier(db_session, fixture.job.id) is None


async def test_verified_guide_runtime_request_carrier_ignores_chat_execution_context(
    db_session: AsyncSession,
) -> None:
    user, profile = await _create_user(db_session)
    prescription = await _create_prescription(db_session, user=user, profile=profile)
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
    await RagRuntimeRepository(db_session).create_execution_context(
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
            runtime_guard_decision_ref="guard:chat-runtime-pass",
        )
    )

    assert await load_verified_guide_runtime_request_carrier(db_session, chat_job.id) is None


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


async def test_execution_identification_requires_same_prescription_version(
    db_session: AsyncSession,
) -> None:
    user, profile = await _create_user(db_session)
    prescription = await _create_prescription(db_session, user=user, profile=profile)
    other_prescription = await _create_prescription(db_session, user=user, profile=profile)
    other_medication = await db_session.scalar(
        select(PrescriptionVersionMedication).where(
            PrescriptionVersionMedication.prescription_version_id == other_prescription.active_version_id
        )
    )
    assert other_medication is not None
    other_identification = await _create_identification(db_session, medication=other_medication)
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

    with pytest.raises(ValueError, match="prescription version"):
        await repository.create_execution_identification(
            AiJobExecutionIdentificationCreate(
                execution_context_id=execution.id,
                medication_identification_id=other_identification.id,
                prescription_version_medication_id=other_medication.id,
            )
        )


async def test_execution_context_rejects_bundle_manifest_mismatch(
    db_session: AsyncSession,
) -> None:
    user, profile = await _create_user(db_session)
    prescription = await _create_prescription(db_session, user=user, profile=profile)
    chat_message = await _create_chat_domain(db_session, profile=profile, prescription=prescription)
    manifest, bundle, environment = await _create_runtime_graph(db_session)
    other_manifest = await RagRuntimeRepository(db_session).create_execution_manifest(
        RagRuntimeExecutionManifestCreate(
            manifest_key=f"preflight-runtime-other-{uuid4().hex[:8]}",
            manifest_version="1.0.0",
            manifest_hash=_hash("8"),
            schema_version="runtime-manifest-v1",
            git_commit_sha="abcdef2",
            guard_policy_ref="guard-policy:test",
        )
    )
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

    with pytest.raises(IntegrityError):
        await RagRuntimeRepository(db_session).create_execution_context(
            AiJobExecutionContextCreate(
                ai_job_id=chat_job.id,
                chat_message_id=chat_message.id,
                prescription_version_id=prescription.active_version_id,
                runtime_environment_id=environment.id,
                runtime_environment_revision=environment.environment_revision,
                runtime_release_bundle_id=bundle.id,
                runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
                runtime_execution_manifest_id=other_manifest.id,
                runtime_execution_manifest_hash=other_manifest.manifest_hash,
                runtime_guard_decision_ref="guard:full-pass",
            )
        )
