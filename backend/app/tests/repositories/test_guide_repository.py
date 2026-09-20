from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.async_jobs import AiJobType
from app.models.guides import Guide, GuideGenerationStatus
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.profiles import Profile, ProfileType
from app.models.rag_source import (
    RagSource,
    RagSourceEndpoint,
    RagSourceOperation,
    RagSourceSnapshot,
    RagSourceSnapshotMember,
    RagSourceSnapshotMemberKind,
)
from app.models.users import Gender, User
from app.repositories.async_job_repository import AsyncJobRepository
from app.repositories.guide_repository import GuideRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.services.guides import _to_guide_data
from app.tests.conftest import test_engine
from app.tests.fixtures.prescription_fingerprint import fingerprint_values
from rag_runtime.guide_release_projection import (
    GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
    GuideRuntimeApprovedAnswer,
    GuideRuntimeApprovedFallback,
    GuideRuntimeCitationSourceType,
    GuideRuntimeFallbackCode,
    GuideRuntimeReleaseDecision,
    GuideRuntimeReleaseProjectionCarrier,
    GuideRuntimeVerifiedCitation,
)


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    # conftest.isolate_database와 동일한 savepoint 격리 방식을 사용해,
    # 리포지토리 메서드를 HTTP 계층 없이 직접 테스트하면서도 다른 테스트에 영향을 주지 않습니다.
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


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


async def _create_confirmed_prescription(session: AsyncSession, *, user: User) -> Prescription:
    profile = await session.scalar(
        select(Profile).where(Profile.user_id == user.id, Profile.profile_type == ProfileType.SELF)
    )
    assert profile is not None
    document = MedicalDocument(
        uploaded_by=user.id,
        profile_id=profile.id,
        original_file_name="prescription.jpg",
        object_key="prescription.jpg",
        file_mime_type="image/jpeg",
        file_size_bytes=100,
    )
    session.add(document)
    await session.flush()

    ocr_job = OcrJob(document_id=document.id)
    session.add(ocr_job)
    await session.flush()

    version_id = uuid4()
    prescription = Prescription(
        active_version_id=version_id,
        document_id=document.id,
        source_ocr_job_id=ocr_job.id,
        profile_id=profile.id,
        prescribed_date=date.today(),
        confirmed_at=datetime.now(UTC),
    )
    session.add(prescription)
    await session.flush()
    version = PrescriptionVersion(
        **fingerprint_values(prescription.prescribed_date, [{"medication_name": "타이레놀", "display_order": 1}]),
        id=version_id,
        prescription_id=prescription.id,
        version_number=1,
        prescribed_date=prescription.prescribed_date,
        confirmed_at=prescription.confirmed_at,
    )
    session.add(version)
    await session.flush()
    session.add(Medication(prescription_id=prescription.id, medication_name="타이레놀", display_order=1))
    session.add(
        PrescriptionVersionMedication(
            medication_count=1,
            prescription_version_id=version_id,
            medication_name="타이레놀",
            display_order=1,
        )
    )
    await session.flush()

    return prescription


async def _create_source_snapshot_members(
    session: AsyncSession,
) -> tuple[RagSourceSnapshot, tuple[RagSourceSnapshotMember, RagSourceSnapshotMember]]:
    suffix = uuid4().hex[:10]
    source = RagSource(source_code=f"guide-test-{suffix}", display_name="Guide 테스트 출처")
    session.add(source)
    await session.flush()
    endpoint = RagSourceEndpoint(
        source_id=source.id,
        endpoint_code=f"endpoint-{suffix}",
        display_name="Guide 테스트 endpoint",
    )
    session.add(endpoint)
    await session.flush()
    operation = RagSourceOperation(
        endpoint_id=endpoint.id,
        operation_code=f"operation-{suffix}",
        display_name="Guide 테스트 operation",
    )
    session.add(operation)
    await session.flush()
    snapshot = RagSourceSnapshot(
        operation_id=operation.id,
        source_version="2026-09-21",
        raw_manifest_checksum="a" * 64,
        canonical_checksum="b" * 64,
        schema_version="test-v1",
        parser_version="test-v1",
        normalization_version="test-v1",
        canonicalization_spec_version="test-v1",
        record_count=2,
        rejected_record_count=0,
        collected_at=datetime.now(UTC),
    )
    session.add(snapshot)
    await session.flush()
    members = (
        RagSourceSnapshotMember(
            source_snapshot_id=snapshot.id,
            member_kind=RagSourceSnapshotMemberKind.ENDPOINT_OPERATION,
            endpoint_id=endpoint.id,
            operation_id=operation.id,
            locator="guide-test:1",
            content_sha256="1" * 64,
        ),
        RagSourceSnapshotMember(
            source_snapshot_id=snapshot.id,
            member_kind=RagSourceSnapshotMemberKind.ENDPOINT_OPERATION,
            endpoint_id=endpoint.id,
            operation_id=operation.id,
            locator="guide-test:2",
            content_sha256="2" * 64,
        ),
    )
    session.add_all(members)
    await session.flush()
    return snapshot, members


def _verified_citation(
    *,
    snapshot: RagSourceSnapshot,
    member: RagSourceSnapshotMember,
    display_order: int,
) -> GuideRuntimeVerifiedCitation:
    return GuideRuntimeVerifiedCitation(
        card_target_ref=f"card-{display_order}",
        claim_key=f"claim-{display_order}",
        evidence_key=f"evidence-{display_order}",
        source_type=GuideRuntimeCitationSourceType.LIFESTYLE_GUIDELINE,
        source_snapshot_id=snapshot.id,
        source_snapshot_member_id=member.id,
        source_code="MFDS_GUIDE",
        source_version=snapshot.source_version,
        locator=member.locator,
        content_sha256=member.content_sha256,
        display_order=display_order,
    )


async def test_get_prescription_owned_rejects_other_users_prescription(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="owner@example.com")
    intruder = await _create_user(db_session, email="intruder@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)

    repo = GuideRepository(db_session)

    owned = await repo.get_prescription_owned(prescription_id=prescription.id, user_id=owner.id)
    assert owned is not None

    stolen = await repo.get_prescription_owned(prescription_id=prescription.id, user_id=intruder.id)
    assert stolen is None


async def test_get_prescription_owned_orders_medications_by_display_order(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="ordered-medications@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    # 완성된 새 버전을 1, 3, 2 삽입 순서로 구성해 읽기 정렬을 검증합니다.
    await PrescriptionRepository(db_session).create_version(
        prescription=prescription,
        prescribed_date=date.today(),
        confirmed_at=datetime.now(UTC),
        medications=[
            {"medication_name": "첫번째 약", "display_order": 1},
            {"medication_name": "세번째 약", "display_order": 3},
            {"medication_name": "두번째 약", "display_order": 2},
        ],
    )

    repo = GuideRepository(db_session)
    loaded = await repo.get_prescription_owned(prescription_id=prescription.id, user_id=owner.id)

    assert loaded is not None
    assert loaded.active_version is not None
    assert [medication.display_order for medication in loaded.active_version.medications] == [1, 2, 3]


async def test_get_owned_guide_rejects_other_users_guide(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="owner2@example.com")
    intruder = await _create_user(db_session, email="intruder2@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)

    repo = GuideRepository(db_session)
    guide = await repo.create(prescription=prescription)

    owned = await repo.get_owned(guide_id=guide.id, user_id=owner.id)
    assert owned is not None

    stolen = await repo.get_owned(guide_id=guide.id, user_id=intruder.id)
    assert stolen is None


async def test_get_by_ai_job_id_returns_matching_guide(db_session: AsyncSession) -> None:
    """Guide 영속 매핑: `guide.ai_job_id`로 직접 조회할 수 있어야 rediscovery·
    `GET /jobs/{job_id}`가 Outbox 30일 보존과 무관하게 Job 90일 보존 동안 값을 찾을 수
    있습니다(OCR의 #212와 같은 목적)."""
    owner = await _create_user(db_session, email=f"guide-persist-{uuid4().hex[:10]}@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    guide = await GuideRepository(db_session).create(prescription=prescription)
    ai_job = await AsyncJobRepository(db_session).create_job(
        user_id=owner.id,
        job_type=AiJobType.GUIDE,
        prescription_version_id=prescription.active_version_id,
    )
    guide.ai_job_id = ai_job.id
    await db_session.flush()

    found = await GuideRepository(db_session).get_by_ai_job_id(ai_job_id=ai_job.id)

    assert found is not None
    assert found.id == guide.id


async def test_get_by_ai_job_id_returns_none_when_unset(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email=f"guide-unset-{uuid4().hex[:10]}@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    await GuideRepository(db_session).create(prescription=prescription)

    found = await GuideRepository(db_session).get_by_ai_job_id(ai_job_id=uuid4())

    assert found is None


async def test_mark_release_completed_round_trips_pass_projection_and_ordered_citations(
    db_session: AsyncSession,
) -> None:
    owner = await _create_user(db_session, email=f"g-pass-{uuid4().hex[:8]}@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    snapshot, members = await _create_source_snapshot_members(db_session)
    guide = await GuideRepository(db_session).create(prescription=prescription)
    projection = GuideRuntimeReleaseProjectionCarrier(
        contract_version=GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
        release_decision=GuideRuntimeReleaseDecision.PASS,
        is_current=True,
        answer=GuideRuntimeApprovedAnswer(
            claim_action_texts=("첫 번째 행동", "두 번째 행동"),
            uncertainty_text="개인 상태에 따라 다를 수 있습니다.",
            consultation_text="의료진과 상의하세요.",
        ),
        fallback=None,
        citations=(
            _verified_citation(snapshot=snapshot, member=members[1], display_order=2),
            _verified_citation(snapshot=snapshot, member=members[0], display_order=1),
        ),
    )

    completed = await GuideRepository(db_session).mark_release_completed(
        guide,
        projection=projection,
        model_name="synthetic-model",
        prompt_version="synthetic-prompt",
        completed_at=datetime.now(UTC),
    )
    assert [citation.display_order for citation in completed.citations] == [1, 2]
    post_projection = _to_guide_data(completed)
    guide_id = guide.id
    owner_id = owner.id
    member_ids = [members[0].id, members[1].id]
    db_session.expire_all()

    reloaded = await GuideRepository(db_session).get_owned(guide_id=guide_id, user_id=owner_id)

    assert reloaded is not None
    assert reloaded.release_projection_version == GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION
    assert reloaded.release_decision == GuideRuntimeReleaseDecision.PASS.value
    assert reloaded.release_is_current is True
    assert reloaded.content == (
        "첫 번째 행동\n\n두 번째 행동\n\n개인 상태에 따라 다를 수 있습니다.\n\n의료진과 상의하세요."
    )
    assert reloaded.answer_claim_action_texts == ["첫 번째 행동", "두 번째 행동"]
    assert reloaded.answer_uncertainty_text == "개인 상태에 따라 다를 수 있습니다."
    assert reloaded.answer_consultation_text == "의료진과 상의하세요."
    assert reloaded.fallback_code is None
    assert [citation.display_order for citation in reloaded.citations] == [1, 2]
    assert [citation.source_snapshot_member_id for citation in reloaded.citations] == member_ids
    assert _to_guide_data(reloaded) == post_projection


async def test_mark_release_completed_round_trips_stale_fallback_without_content(
    db_session: AsyncSession,
) -> None:
    owner = await _create_user(db_session, email=f"g-stale-{uuid4().hex[:8]}@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    guide = await GuideRepository(db_session).create(prescription=prescription)
    projection = GuideRuntimeReleaseProjectionCarrier(
        contract_version=GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
        release_decision=GuideRuntimeReleaseDecision.STALE,
        is_current=False,
        answer=None,
        fallback=GuideRuntimeApprovedFallback(
            code=GuideRuntimeFallbackCode.PRESCRIPTION_STALE,
            text="처방 정보가 변경되어 다시 확인이 필요합니다.",
        ),
        citations=(),
    )

    await GuideRepository(db_session).mark_release_completed(
        guide,
        projection=projection,
        model_name="synthetic-model",
        prompt_version="synthetic-prompt",
        completed_at=datetime.now(UTC),
    )
    guide_id = guide.id
    owner_id = owner.id
    db_session.expire_all()

    reloaded = await GuideRepository(db_session).get_owned(guide_id=guide_id, user_id=owner_id)

    assert reloaded is not None
    assert reloaded.release_decision == GuideRuntimeReleaseDecision.STALE.value
    assert reloaded.release_is_current is False
    assert reloaded.content is None
    assert reloaded.answer_claim_action_texts is None
    assert reloaded.fallback_code == GuideRuntimeFallbackCode.PRESCRIPTION_STALE.value
    assert reloaded.fallback_text == "처방 정보가 변경되어 다시 확인이 필요합니다."
    assert reloaded.citations == []


async def test_mark_failed_persists_after_writer_session_closes_and_new_session_reloads() -> None:
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)
    synthetic_suffix = uuid4().hex[:12]

    async with session_factory() as writer_session:
        user = await _create_user(writer_session, email=f"guide-failure-{synthetic_suffix}@example.com")
        prescription = await _create_confirmed_prescription(writer_session, user=user)
        guide = await GuideRepository(writer_session).create(prescription=prescription)

        await GuideRepository(writer_session).mark_failed(
            guide,
            error_code="OPENAI_API_ERROR",
            error_message="고정된 안전 문구",
            completed_at=datetime.now(UTC),
        )

        # 실제 요청 흐름에서는 commit 뒤 ApiError가 전파되어 dependency가 rollback을 호출합니다.
        # 실패 상태가 commit 경계를 넘었는지 검증하기 위해 writer session은 여기서 완전히 닫습니다.
        await writer_session.rollback()
        guide_id = guide.id
        prescription_id = prescription.id
        ocr_job_id = prescription.source_ocr_job_id
        document_id = prescription.document_id
        user_id = user.id

    try:
        async with session_factory() as verification_session:
            reloaded = await GuideRepository(verification_session).get_owned(
                guide_id=guide_id,
                user_id=user_id,
            )
            assert reloaded is not None
            assert reloaded.generation_status == GuideGenerationStatus.FAILED
            assert reloaded.error_code == "OPENAI_API_ERROR"
            assert reloaded.error_message == "고정된 안전 문구"
            assert reloaded.completed_at is not None
            assert (reloaded.content, reloaded.model_name, reloaded.prompt_version) == (None, None, None)
    finally:
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(delete(Guide).where(Guide.id == guide_id))
            await cleanup_session.execute(delete(Medication).where(Medication.prescription_id == prescription_id))
            await cleanup_session.execute(delete(Prescription).where(Prescription.id == prescription_id))
            await cleanup_session.execute(delete(OcrJob).where(OcrJob.id == ocr_job_id))
            await cleanup_session.execute(delete(MedicalDocument).where(MedicalDocument.id == document_id))
            await cleanup_session.execute(delete(Profile).where(Profile.user_id == user_id))
            await cleanup_session.execute(delete(User).where(User.id == user_id))
            await cleanup_session.commit()
