from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core import config
from app.core.db.databases import get_db_session
from app.dependencies.security import get_request_user
from app.main import app, fastapi_app
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.profiles import Profile, ProfileType
from app.models.rag_candidate import MedicationCandidateSearchStatus
from app.models.users import Gender, User
from app.repositories.medication_candidate_repository import (
    MedicationCandidateRepository,
    MedicationCandidateResultCreate,
)
from app.services.medication_identification import MedicationIdentificationService
from app.tests.conftest import test_engine

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as test_client:
            yield test_client
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)


@pytest.fixture
def public_track_f_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET/confirm/reject는 기본값(false)에서 공개 게이트에 fail-closed되므로, 게이트 뒤
    실제 동작을 검증하는 테스트는 이 fixture로 명시적으로 활성화합니다."""
    monkeypatch.setattr(config, "PUBLIC_TRACK_F_ENABLED", True)


async def test_candidate_search_is_fail_closed_before_ownership_chain(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/medication-candidate-searches",
        json={"prescription_version_medication_id": str(uuid4())},
    )

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert response.json()["details"] == [
        {
            "field": "medication_candidate",
            "reason": "PRESCRIPTION_VERSION_MEDICATION_OWNERSHIP_NOT_CONNECTED",
            "rejected_value": None,
        }
    ]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/api/v1/medication-candidates/confirm",
            {
                "prescription_version_medication_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
        (
            "/api/v1/medication-candidates/reject",
            {
                "search_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
    ],
)
async def test_confirm_and_reject_require_idempotency_key(
    client: AsyncClient,
    public_track_f_enabled: None,
    path: str,
    body: dict[str, str],
) -> None:
    response = await client.post(path, json=body)

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert response.json()["details"] == [
        {
            "field": "Idempotency-Key",
            "reason": "IDEMPOTENCY_KEY_REQUIRED",
            "rejected_value": None,
        }
    ]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/api/v1/medication-candidates/confirm",
            {
                "prescription_version_medication_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
        (
            "/api/v1/medication-candidates/reject",
            {
                "search_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
    ],
)
async def test_confirm_and_reject_validate_idempotency_key_format(
    client: AsyncClient,
    public_track_f_enabled: None,
    path: str,
    body: dict[str, str],
) -> None:
    response = await client.post(path, headers={"Idempotency-Key": "short"}, json=body)

    assert response.status_code == 400
    assert response.json()["code"] == "IDEMPOTENCY_KEY_INVALID"


@pytest.mark.parametrize(
    ("path", "body_factory"),
    [
        (
            "/api/v1/medication-candidates/confirm",
            lambda: {
                "prescription_version_medication_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
        (
            "/api/v1/medication-candidates/reject",
            lambda: {
                "search_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
    ],
)
async def test_confirm_and_reject_are_not_found_for_unknown_result_after_idempotency_validation(
    client: AsyncClient,
    public_track_f_enabled: None,
    path: str,
    body_factory,
) -> None:
    """Idempotency-Key 형식만 유효하고 실제 candidate_search_result_id가 없으면(다른 사용자
    소유이거나 존재하지 않는 경우와 동일 shape) 404 CANDIDATE_SEARCH_NOT_FOUND로 끝나야 합니다.
    #172 이전에는 이 경로가 기능 자체가 없어 항상 503이었습니다."""
    response = await client.post(
        path,
        headers={"Idempotency-Key": "candidate-key-20260907-001"},
        json=body_factory(),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CANDIDATE_SEARCH_NOT_FOUND"


async def test_get_candidate_search_is_not_found_for_unknown_medication(
    client: AsyncClient, public_track_f_enabled: None
) -> None:
    response = await client.get(f"/api/v1/medication-candidate-searches/{uuid4()}")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == "PRESCRIPTION_MEDICATION_NOT_FOUND"


async def test_get_candidate_search_is_fail_closed_when_public_track_f_disabled(client: AsyncClient) -> None:
    """PUBLIC_TRACK_F_ENABLED 기본값(false)에서는 조회조차 fail-closed되어야 합니다(#312 리뷰 지적)."""
    response = await client.get(f"/api/v1/medication-candidate-searches/{uuid4()}")

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert response.json()["details"] == [
        {
            "field": "medication_candidate",
            "reason": "PUBLIC_TRACK_F_DISABLED",
            "rejected_value": None,
        }
    ]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/api/v1/medication-candidates/confirm",
            {
                "prescription_version_medication_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
        (
            "/api/v1/medication-candidates/reject",
            {
                "search_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
    ],
)
async def test_confirm_and_reject_are_fail_closed_when_public_track_f_disabled(
    client: AsyncClient,
    path: str,
    body: dict[str, str],
) -> None:
    """PUBLIC_TRACK_F_ENABLED 기본값(false)에서는 유효한 Idempotency-Key를 보내도
    확인·거절 자체가 fail-closed되어야 합니다(#312 리뷰 지적)."""
    response = await client.post(
        path,
        headers={"Idempotency-Key": "candidate-key-gate-disabled-001"},
        json=body,
    )

    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert response.json()["details"][0]["reason"] == "PUBLIC_TRACK_F_DISABLED"


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """이 파일의 happy-path 테스트는 seed한 Search/Result를 실제 HTTP 요청으로 읽어야 하므로,
    `conftest.isolate_database`가 감춰둔 세션 대신 직접 세션을 만들어 `get_db_session`을
    오버라이드합니다(test_job_status_api.py와 동일한 이유)."""
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )

        async def override_get_db_session() -> AsyncIterator[AsyncSession]:
            yield session

        fastapi_app.dependency_overrides[get_db_session] = override_get_db_session
        try:
            yield session
        finally:
            fastapi_app.dependency_overrides.pop(get_db_session, None)
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


async def _create_owner(session: AsyncSession) -> User:
    user = User(
        email=f"candidate-api-{uuid4().hex[:8]}@example.com",
        hashed_password="hashed-password",
        name="후보API테스터",
        gender=Gender.MALE,
        birthday=datetime(1990, 1, 1, tzinfo=UTC).date(),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    session.add(user)
    await session.flush()
    profile = Profile(user_id=user.id, profile_type=ProfileType.SELF, display_name=user.name)
    session.add(profile)
    await session.flush()
    return user


async def _create_medication(
    session: AsyncSession, *, user: User, display_order: int = 1
) -> PrescriptionVersionMedication:
    profile = await session.scalar(
        select(Profile).where(Profile.user_id == user.id, Profile.profile_type == ProfileType.SELF)
    )
    assert profile is not None
    document = MedicalDocument(
        uploaded_by=user.id,
        profile_id=profile.id,
        original_file_name="prescription.jpg",
        object_key=f"{uuid4()}.jpg",
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
        prescribed_date=datetime.now(config.TIMEZONE).date(),
        confirmed_at=datetime.now(config.TIMEZONE),
    )
    session.add(prescription)
    await session.flush()
    session.add(
        PrescriptionVersion(
            id=version_id,
            prescription_id=prescription.id,
            version_number=1,
            prescribed_date=prescription.prescribed_date,
            confirmed_at=prescription.confirmed_at,
        )
    )
    await session.flush()

    medication = PrescriptionVersionMedication(
        prescription_version_id=version_id,
        medication_name="테스트약",
        strength_text="500mg",
        display_order=display_order,
    )
    session.add(medication)
    await session.flush()
    return medication


async def _create_ready_search(
    session: AsyncSession, *, medication: PrescriptionVersionMedication, user: User
) -> tuple[UUID, UUID]:
    """RAG-09 service를 그대로 사용해 READY 상태의 Search·표시 Result 1건을 만듭니다.
    반환값은 (search_id, candidate_search_result_id)."""
    service = MedicationIdentificationService(MedicationCandidateRepository(session))
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=user.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        user_id=user.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[
            MedicationCandidateResultCreate(
                product_id=uuid4(),
                code_system="MFDS_ITEM_SEQ",
                canonical_code="200012345",
                product_name="테스트정",
                strength_text="500mg",
                dosage_form="정제",
                manufacturer_name="테스트제약",
                product_status="ACTIVE",
                result_rank=1,
                result_score=0.95,
                result_method="PRODUCT_NAME",
                is_displayed=True,
                selection_eligible=True,
            )
        ],
    )
    return search.id, finalized.results[0].id


class TestGetMedicationCandidateSearch:
    async def test_returns_ready_snapshot_with_no_store(
        self, db_session: AsyncSession, public_track_f_enabled: None
    ) -> None:
        owner = await _create_owner(db_session)
        medication = await _create_medication(db_session, user=owner, display_order=2)
        search_id, result_id = await _create_ready_search(db_session, medication=medication, user=owner)
        fastapi_app.dependency_overrides[get_request_user] = lambda: owner
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(f"/api/v1/medication-candidate-searches/{medication.id}")
        finally:
            fastapi_app.dependency_overrides.pop(get_request_user, None)

        assert response.status_code == status.HTTP_200_OK
        assert response.headers.get_list("cache-control") == ["no-store"]
        data = response.json()["data"]
        assert data["search_id"] == str(search_id)
        assert data["prescription_version_medication_id"] == str(medication.id)
        assert data["medication_index"] == 2
        assert data["status"] == "READY"
        assert data["candidate_search_result_id"] == str(result_id)
        assert data["candidate"] == {
            "product_name": "테스트정",
            "strength_text": "500mg",
            "dosage_form": "정제",
            "manufacturer_name": "테스트제약",
            "product_status": "ACTIVE",
        }

    async def test_rejects_other_users_medication(self, db_session: AsyncSession, public_track_f_enabled: None) -> None:
        owner = await _create_owner(db_session)
        intruder = await _create_owner(db_session)
        medication = await _create_medication(db_session, user=owner)
        await _create_ready_search(db_session, medication=medication, user=owner)
        fastapi_app.dependency_overrides[get_request_user] = lambda: intruder
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(f"/api/v1/medication-candidate-searches/{medication.id}")
        finally:
            fastapi_app.dependency_overrides.pop(get_request_user, None)

        assert response.status_code == 404
        assert response.json()["code"] == "PRESCRIPTION_MEDICATION_NOT_FOUND"

    async def test_returns_candidate_search_not_found_when_no_search_yet(
        self, db_session: AsyncSession, public_track_f_enabled: None
    ) -> None:
        owner = await _create_owner(db_session)
        medication = await _create_medication(db_session, user=owner)
        fastapi_app.dependency_overrides[get_request_user] = lambda: owner
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(f"/api/v1/medication-candidate-searches/{medication.id}")
        finally:
            fastapi_app.dependency_overrides.pop(get_request_user, None)

        assert response.status_code == 404
        assert response.json()["code"] == "CANDIDATE_SEARCH_NOT_FOUND"


class TestConfirmAndRejectMedicationCandidate:
    async def test_confirm_maps_identification_fields(
        self, db_session: AsyncSession, public_track_f_enabled: None
    ) -> None:
        owner = await _create_owner(db_session)
        medication = await _create_medication(db_session, user=owner)
        _search_id, result_id = await _create_ready_search(db_session, medication=medication, user=owner)
        fastapi_app.dependency_overrides[get_request_user] = lambda: owner
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/medication-candidates/confirm",
                    headers={"Idempotency-Key": "candidate-confirm-20260907-001"},
                    json={
                        "prescription_version_medication_id": str(medication.id),
                        "candidate_search_result_id": str(result_id),
                    },
                )
        finally:
            fastapi_app.dependency_overrides.pop(get_request_user, None)

        assert response.status_code == status.HTTP_200_OK
        assert response.headers.get_list("cache-control") == ["no-store"]
        data = response.json()["data"]
        assert data["prescription_version_medication_id"] == str(medication.id)
        assert data["status"] == "MATCHED"
        assert data["source"] == "USER_SELECTED"
        assert data["confirmed_at"] is not None

    async def test_reject_maps_identification_event_fields(
        self, db_session: AsyncSession, public_track_f_enabled: None
    ) -> None:
        owner = await _create_owner(db_session)
        medication = await _create_medication(db_session, user=owner)
        search_id, result_id = await _create_ready_search(db_session, medication=medication, user=owner)
        fastapi_app.dependency_overrides[get_request_user] = lambda: owner
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/medication-candidates/reject",
                    headers={"Idempotency-Key": "candidate-reject-20260907-001"},
                    json={
                        "search_id": str(search_id),
                        "candidate_search_result_id": str(result_id),
                    },
                )
        finally:
            fastapi_app.dependency_overrides.pop(get_request_user, None)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert "identification_event_id" in data
        assert data["prescription_version_medication_id"] == str(medication.id)
        assert data["status"] == "UNRESOLVED"
        assert data["search_status"] == "INVALIDATED_USER_REJECTED"
        assert data["rejected_at"] is not None

    async def test_confirm_replays_stored_response_for_repeated_request(
        self, db_session: AsyncSession, public_track_f_enabled: None
    ) -> None:
        owner = await _create_owner(db_session)
        medication = await _create_medication(db_session, user=owner)
        _search_id, result_id = await _create_ready_search(db_session, medication=medication, user=owner)
        fastapi_app.dependency_overrides[get_request_user] = lambda: owner
        body = {
            "prescription_version_medication_id": str(medication.id),
            "candidate_search_result_id": str(result_id),
        }
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                first = await client.post(
                    "/api/v1/medication-candidates/confirm",
                    headers={"Idempotency-Key": "candidate-confirm-replay-001"},
                    json=body,
                )
                second = await client.post(
                    "/api/v1/medication-candidates/confirm",
                    headers={"Idempotency-Key": "candidate-confirm-replay-001"},
                    json=body,
                )
        finally:
            fastapi_app.dependency_overrides.pop(get_request_user, None)

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        assert second.json()["data"] == first.json()["data"]

    async def test_confirm_rejects_same_key_with_different_body_as_conflict(
        self, db_session: AsyncSession, public_track_f_enabled: None
    ) -> None:
        owner = await _create_owner(db_session)
        medication = await _create_medication(db_session, user=owner)
        _search_id, result_id = await _create_ready_search(db_session, medication=medication, user=owner)
        fastapi_app.dependency_overrides[get_request_user] = lambda: owner
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                first = await client.post(
                    "/api/v1/medication-candidates/confirm",
                    headers={"Idempotency-Key": "candidate-confirm-conflict-001"},
                    json={
                        "prescription_version_medication_id": str(medication.id),
                        "candidate_search_result_id": str(result_id),
                    },
                )
                second = await client.post(
                    "/api/v1/medication-candidates/confirm",
                    headers={"Idempotency-Key": "candidate-confirm-conflict-001"},
                    json={
                        "prescription_version_medication_id": str(medication.id),
                        "candidate_search_result_id": str(uuid4()),
                    },
                )
        finally:
            fastapi_app.dependency_overrides.pop(get_request_user, None)

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == 409
        assert second.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"
