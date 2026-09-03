from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core.db.databases import get_db_session
from app.dependencies.services import get_ocr_engine
from app.main import app, fastapi_app
from app.models.async_jobs import AiJob, AiJobStatus, AiJobType, DomainType
from app.models.guides import Guide, GuideGenerationStatus
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob, OcrStatus
from app.models.prescriptions import Prescription
from app.models.profiles import Profile, ProfileType
from app.models.users import User
from app.repositories.async_job_repository import AsyncJobRepository
from app.services.job_intake import DomainReference, JobIntakeService
from app.services.ocr_engine import OcrDeadline, OcrRecognitionResult, RecognizedField
from app.tests.conftest import test_engine

_JPEG_SIGNATURE = b"\xff\xd8\xff"


class _FakeOcrEngine:
    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: OcrDeadline,
    ) -> OcrRecognitionResult:
        _ = object_key, file_mime_type, deadline
        return OcrRecognitionResult(
            fields=[
                RecognizedField(
                    medication_index=1,
                    field_type="MEDICATION_NAME",
                    raw_value="테스트약",
                    normalized_value="테스트약",
                    normalization_version="rule-v1",
                    confidence_score=0.99,
                ),
            ],
        )


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """이 파일의 테스트는 `accept_job()`으로 seed한 데이터를 실제 HTTP 요청으로 읽어야 하므로,
    `conftest.isolate_database`가 감춰둔 세션 대신 직접 세션을 만들어 `get_db_session`을
    오버라이드합니다 — API 호출과 seed 코드가 같은 transaction(savepoint)을 공유해야
    seed한 row를 API가 볼 수 있습니다."""
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


async def _signup_and_login(client: AsyncClient, *, label: str) -> tuple[str, str]:
    suffix = uuid4().hex[:8]
    email = f"job-status-{label}-{suffix}@example.com"
    signup_response = await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "Password123!", "name": "Job상태테스터"},
    )
    assert signup_response.status_code == status.HTTP_201_CREATED, signup_response.text

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
    )
    assert login_response.status_code == status.HTTP_200_OK, login_response.text
    return email, login_response.json()["access_token"]


async def _get_user(session: AsyncSession, *, email: str) -> User:
    user = await session.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


async def _create_document(session: AsyncSession, *, user: User) -> MedicalDocument:
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
    return document


async def _accept_ocr_job(session: AsyncSession, *, user: User, document: MedicalDocument) -> UUID:
    async def create_domain_placeholder(_job_id: UUID) -> DomainReference:
        ocr_job = OcrJob(document_id=document.id, ocr_status=OcrStatus.PENDING)
        session.add(ocr_job)
        await session.flush()
        return DomainReference(domain_type=DomainType.OCR_JOB, domain_id=ocr_job.id)

    service = JobIntakeService(AsyncJobRepository(session))
    result = await service.accept_job(
        user_id=user.id,
        job_type=AiJobType.OCR,
        operation_id="ocr.create_job",
        idempotency_key=f"test-job-status-api-{uuid4().hex[:20]}",
        fingerprint={"job_type": "OCR", "document_id": str(document.id)},
        create_domain_placeholder=create_domain_placeholder,
        trace_id="a" * 32,
    )
    await session.flush()
    return result.job.id


async def _setup_owner_with_job(
    client: AsyncClient, db_session: AsyncSession, *, label: str = "owner"
) -> tuple[str, UUID, UUID]:
    """반환값은 (access_token, job_id, document_id)."""
    _email, access_token = await _signup_and_login(client, label=label)
    user = await _get_user(db_session, email=_email)
    document = await _create_document(db_session, user=user)
    job_id = await _accept_ocr_job(db_session, user=user, document=document)
    return access_token, job_id, document.id


async def _create_confirmed_prescription(session: AsyncSession, *, user: User) -> Prescription:
    profile = await session.scalar(
        select(Profile).where(Profile.user_id == user.id, Profile.profile_type == ProfileType.SELF)
    )
    assert profile is not None
    document = await _create_document(session, user=user)
    ocr_job = OcrJob(document_id=document.id, ocr_status=OcrStatus.COMPLETED, completed_at=datetime.now(UTC))
    session.add(ocr_job)
    await session.flush()
    prescription = Prescription(
        document_id=document.id,
        source_ocr_job_id=ocr_job.id,
        profile_id=profile.id,
        prescribed_date=datetime.now(UTC).date(),
        confirmed_at=datetime.now(UTC),
    )
    session.add(prescription)
    await session.flush()
    return prescription


async def _accept_guide_job(
    session: AsyncSession, *, user: User, prescription: Prescription, requested_at: datetime | None = None
) -> tuple[UUID, UUID]:
    """Guide 접수는 아직 `accept_job()`에 연결되지 않아(팀 결정, #148) repository를 직접
    조합합니다. 반환값은 (job_id, guide_id).

    `requested_at`을 명시하지 않으면 DB `now()` 기본값을 씁니다 — 같은 transaction에서
    Guide를 두 번 이상 만들면 `now()`가 같아서
    `GuideRepository.get_latest_for_prescription_owned()`의 tie-break(`Guide.id`, 무작위
    UUID)가 순서를 보장하지 않으므로, "여러 개 중 최신 선택"을 테스트할 때는 이전 Guide에
    과거 `requested_at`을 명시해 순서를 고정해야 합니다."""
    guide = Guide(
        prescription_id=prescription.id,
        profile_id=prescription.profile_id,
        generation_status=GuideGenerationStatus.GENERATING,
        **({"requested_at": requested_at} if requested_at is not None else {}),
    )
    session.add(guide)
    await session.flush()

    repo = AsyncJobRepository(session)
    job = await repo.create_job(user_id=user.id, job_type=AiJobType.GUIDE, prescription_version_id=None)
    await repo.create_outbox_event(job=job, trace_id="a" * 32, domain_type=DomainType.GUIDE, domain_id=guide.id)
    return job.id, guide.id


async def _setup_owner_with_guide_job(
    client: AsyncClient, db_session: AsyncSession, *, label: str = "owner"
) -> tuple[str, UUID, UUID]:
    """반환값은 (access_token, job_id, prescription_id)."""
    _email, access_token = await _signup_and_login(client, label=label)
    user = await _get_user(db_session, email=_email)
    prescription = await _create_confirmed_prescription(db_session, user=user)
    job_id, _guide_id = await _accept_guide_job(db_session, user=user, prescription=prescription)
    return access_token, job_id, prescription.id


class TestJobStatusApi:
    async def test_get_job_status_returns_envelope_with_no_store(self, db_session: AsyncSession) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            access_token, job_id, _document_id = await _setup_owner_with_job(client, db_session)

            response = await client.get(
                f"/api/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        data = body["data"]
        assert data["job_id"] == str(job_id)
        assert data["job_type"] == "OCR"
        assert data["status"] == "PENDING"
        assert data["result_url"] is None
        assert data["error"] is None
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_get_job_status_returns_processing_status(self, db_session: AsyncSession) -> None:
        """PR #250/이슈 #148의 "6상태 전부 route-level 검증" 체크에 `PROCESSING`이 실제로는
        빠져 있어(리뷰 지적) 채웁니다. `chk_ai_job_processing_lease` CHECK 제약대로
        `lease_token`/`lease_expires_at`을 함께 채운 상태를 재현합니다."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            access_token, job_id, _document_id = await _setup_owner_with_job(client, db_session)

            job = await db_session.get(AiJob, job_id)
            assert job is not None
            job.status = AiJobStatus.PROCESSING
            job.started_at = datetime.now(UTC)
            job.lease_token = "test-lease-token"
            job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60)
            await db_session.flush()

            response = await client.get(
                f"/api/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["status"] == "PROCESSING"
        assert data["result_url"] is None
        assert data["error"] is None
        assert data["retry_after_seconds"] is None

    async def test_get_job_status_sets_retry_after_header_for_retry_wait(self, db_session: AsyncSession) -> None:
        """outbox-stream-v1.md §소비와 fencing: `RETRY_WAIT` 구간에는 `job.expected_event_id`가
        `NULL`입니다(Reconciler가 다음 attempt Outbox를 만들기 전까지). 그 상태를 재현해
        실제 HTTP 응답이 `404`가 되지 않는지 검증합니다."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            access_token, job_id, _document_id = await _setup_owner_with_job(client, db_session)

            job = await db_session.get(AiJob, job_id)
            assert job is not None
            job.status = AiJobStatus.RETRY_WAIT
            job.available_at = datetime.now(UTC) + timedelta(seconds=30)
            # 실제 전이처럼 last_consumed_event_id를 채운 뒤 expected_event_id를 비웁니다.
            job.last_consumed_event_id = job.expected_event_id
            job.expected_event_id = None
            await db_session.flush()

            response = await client.get(
                f"/api/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()["data"]
        assert body["status"] == "RETRY_WAIT"
        assert body["retry_after_seconds"] is not None
        assert response.headers["retry-after"] == str(body["retry_after_seconds"])

    async def test_get_job_status_exposes_retry_after_header_for_cross_origin_frontend(
        self, db_session: AsyncSession
    ) -> None:
        """`Retry-After`가 CORS `Access-Control-Expose-Headers`에 없으면 cross-origin
        Frontend가 응답은 받아도 fetch()로 그 값을 읽지 못합니다(브라우저가 스크립트에
        노출하는 헤더를 그 목록으로 제한). `Origin` 헤더를 보내 실제로 노출되는지 검증합니다."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            access_token, job_id, _document_id = await _setup_owner_with_job(client, db_session)

            job = await db_session.get(AiJob, job_id)
            assert job is not None
            job.status = AiJobStatus.RETRY_WAIT
            job.available_at = datetime.now(UTC) + timedelta(seconds=30)
            # 실제 전이처럼 last_consumed_event_id를 채운 뒤 expected_event_id를 비웁니다.
            job.last_consumed_event_id = job.expected_event_id
            job.expected_event_id = None
            await db_session.flush()

            response = await client.get(
                f"/api/v1/jobs/{job_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Origin": "http://localhost:5173",
                },
            )

        assert response.status_code == status.HTTP_200_OK
        exposed = {value.strip().lower() for value in response.headers["Access-Control-Expose-Headers"].split(",")}
        assert "retry-after" in exposed

    async def test_get_job_status_returns_result_url_when_completed(self, db_session: AsyncSession) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            access_token, job_id, _document_id = await _setup_owner_with_job(client, db_session)

            job = await db_session.get(AiJob, job_id)
            assert job is not None
            job.status = AiJobStatus.COMPLETED
            job.completed_at = datetime.now(UTC)
            await db_session.flush()

            response = await client.get(
                f"/api/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["status"] == "COMPLETED"
        assert data["result_url"] == f"/api/v1/ocr-jobs/{data['domain_id']}"

    async def test_get_job_status_hides_result_url_when_stale(self, db_session: AsyncSession) -> None:
        """`STALE` Job은 결과가 준비돼 있어 보여도 현재 결과처럼 노출되면 안 됩니다."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            access_token, job_id, _document_id = await _setup_owner_with_job(client, db_session)

            job = await db_session.get(AiJob, job_id)
            assert job is not None
            job.status = AiJobStatus.STALE
            job.completed_at = datetime.now(UTC)
            await db_session.flush()

            response = await client.get(
                f"/api/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["status"] == "STALE"
        assert data["result_url"] is None

    async def test_get_job_status_returns_error_for_failed_job(self, db_session: AsyncSession) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            access_token, job_id, _document_id = await _setup_owner_with_job(client, db_session)

            job = await db_session.get(AiJob, job_id)
            assert job is not None
            job.status = AiJobStatus.FAILED
            job.failure_code = "TIMEOUT"
            job.completed_at = datetime.now(UTC)
            await db_session.flush()

            response = await client.get(
                f"/api/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["status"] == "FAILED"
        assert data["result_url"] is None
        assert data["error"] == {"code": "TIMEOUT", "message": "처리 시간이 초과되어 작업이 실패했습니다."}
        assert data["retry_after_seconds"] is None

    async def test_get_job_status_returns_404_for_other_users_job(self, db_session: AsyncSession) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _owner_token, job_id, _document_id = await _setup_owner_with_job(client, db_session, label="owner")
            _other_email, other_token = await _signup_and_login(client, label="other")

            response = await client.get(
                f"/api/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {other_token}"},
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["code"] == "AI_JOB_NOT_FOUND"

    async def test_get_job_status_returns_404_for_missing_job(self) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _email, access_token = await _signup_and_login(client, label="lonely")

            response = await client.get(
                f"/api/v1/jobs/{uuid4()}",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["code"] == "AI_JOB_NOT_FOUND"

    async def test_get_job_status_requires_authentication(self) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/jobs/{uuid4()}")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


async def test_rediscover_ocr_job_returns_404_for_real_execute_ocr_flow() -> None:
    """PR #250 리뷰 지적: `execute_ocr`(실제 POST)는 아직 `accept_job()`에 연결되지 않아
    OcrJob은 생성돼도 대응하는 AiJob/OutboxEvent가 없습니다. 그래서 실제 업로드 → OCR 실행 →
    rediscovery GET 흐름에서는 `404`가 현재 기대되는 동작입니다(재개할 진행 중인 Job이 실제로
    없음) — `job_status.py`의 다른 rediscovery 테스트들처럼 `accept_job()`을 직접 seed하지
    않고, 실제 라우트만으로 이 현재 동작을 증명합니다. `execute_ocr`가 `accept_job()`에
    연결되면(#148 후속) 이 테스트는 200으로 바뀌어야 합니다."""
    fastapi_app.dependency_overrides[get_ocr_engine] = lambda: _FakeOcrEngine()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _email, access_token = await _signup_and_login(client, label="redi-gap")
            headers = {"Authorization": f"Bearer {access_token}"}

            upload_response = await client.post(
                "/api/v1/documents",
                files={"file": ("prescription.jpg", _JPEG_SIGNATURE + b"fake-jpeg", "image/jpeg")},
                headers=headers,
            )
            assert upload_response.status_code == status.HTTP_201_CREATED
            document_id = upload_response.json()["data"]["document_id"]

            execute_response = await client.post(
                f"/api/v1/documents/{document_id}/ocr-jobs",
                json={"force_reprocess": False},
                headers=headers,
            )
            assert execute_response.status_code == status.HTTP_202_ACCEPTED

            rediscover_response = await client.get(
                f"/api/v1/documents/{document_id}/ocr-jobs",
                headers=headers,
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_ocr_engine, None)

    assert rediscover_response.status_code == status.HTTP_404_NOT_FOUND
    assert rediscover_response.json()["code"] == "AI_JOB_NOT_FOUND"


async def test_rediscover_ocr_job_prefers_latest_async_row_over_older_sync_row(
    db_session: AsyncSession,
) -> None:
    """한 문서에 예전 동기 방식(실제 POST로 만들어졌지만 Job 없는 OcrJob)과 `accept_job()`
    기반 비동기 OcrJob이 섞여 있을 수 있습니다(접수가 연결되기 전후에 걸쳐 재실행한 경우 등).
    최신 row가 비동기 쪽이면 실제 POST/GET 라우트로도 그 Job 상태를 반환해야 합니다."""
    fastapi_app.dependency_overrides[get_ocr_engine] = lambda: _FakeOcrEngine()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            email, access_token = await _signup_and_login(client, label="redi-mx1")
            headers = {"Authorization": f"Bearer {access_token}"}

            upload_response = await client.post(
                "/api/v1/documents",
                files={"file": ("prescription.jpg", _JPEG_SIGNATURE + b"fake-jpeg", "image/jpeg")},
                headers=headers,
            )
            assert upload_response.status_code == status.HTTP_201_CREATED
            document_id = upload_response.json()["data"]["document_id"]

            sync_execute_response = await client.post(
                f"/api/v1/documents/{document_id}/ocr-jobs",
                json={"force_reprocess": False},
                headers=headers,
            )
            assert sync_execute_response.status_code == status.HTTP_202_ACCEPTED

            user = await _get_user(db_session, email=email)
            document = await db_session.get(MedicalDocument, UUID(document_id))
            assert document is not None
            latest_job_id = await _accept_ocr_job(db_session, user=user, document=document)

            rediscover_response = await client.get(
                f"/api/v1/documents/{document_id}/ocr-jobs",
                headers=headers,
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_ocr_engine, None)

    assert rediscover_response.status_code == status.HTTP_200_OK
    assert rediscover_response.json()["data"]["job_id"] == str(latest_job_id)


async def test_rediscover_ocr_job_returns_404_when_latest_row_is_sync_despite_older_async_job(
    db_session: AsyncSession,
) -> None:
    """반대 순서 — 더 이전에 비동기 Job이 있어도, 그 뒤 동기 방식으로 재실행해 최신 row가
    Job 없는 상태가 되면 오래된 Job을 잘못 반환하지 않고 현재 기대되는 `404`를 유지해야
    합니다."""
    fastapi_app.dependency_overrides[get_ocr_engine] = lambda: _FakeOcrEngine()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            email, access_token = await _signup_and_login(client, label="redi-mx2")
            headers = {"Authorization": f"Bearer {access_token}"}

            upload_response = await client.post(
                "/api/v1/documents",
                files={"file": ("prescription.jpg", _JPEG_SIGNATURE + b"fake-jpeg", "image/jpeg")},
                headers=headers,
            )
            assert upload_response.status_code == status.HTTP_201_CREATED
            document_id = upload_response.json()["data"]["document_id"]

            user = await _get_user(db_session, email=email)
            document = await db_session.get(MedicalDocument, UUID(document_id))
            assert document is not None
            await _accept_ocr_job(db_session, user=user, document=document)

            # PENDING인 비동기 OcrJob이 이미 있어 force_reprocess 없이는 409입니다.
            sync_execute_response = await client.post(
                f"/api/v1/documents/{document_id}/ocr-jobs",
                json={"force_reprocess": True},
                headers=headers,
            )
            assert sync_execute_response.status_code == status.HTTP_202_ACCEPTED

            rediscover_response = await client.get(
                f"/api/v1/documents/{document_id}/ocr-jobs",
                headers=headers,
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_ocr_engine, None)

    assert rediscover_response.status_code == status.HTTP_404_NOT_FOUND
    assert rediscover_response.json()["code"] == "AI_JOB_NOT_FOUND"


async def test_rediscover_ocr_job_requires_authentication() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/documents/{uuid4()}/ocr-jobs")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


async def test_rediscover_ocr_job_returns_404_for_other_users_document(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _owner_token, _job_id, document_id = await _setup_owner_with_job(client, db_session, label="owner")
        _other_email, other_token = await _signup_and_login(client, label="other")

        response = await client.get(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            headers={"Authorization": f"Bearer {other_token}"},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["code"] == "AI_JOB_NOT_FOUND"


async def test_rediscover_ocr_job_returns_latest_job_when_multiple_exist(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        email, access_token = await _signup_and_login(client, label="redi-mul")
        user = await _get_user(db_session, email=email)
        document = await _create_document(db_session, user=user)
        await _accept_ocr_job(db_session, user=user, document=document)
        latest_job_id = await _accept_ocr_job(db_session, user=user, document=document)

        response = await client.get(
            f"/api/v1/documents/{document.id}/ocr-jobs",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["job_id"] == str(latest_job_id)


async def test_rediscover_ocr_job_returns_terminal_status_and_result_url(db_session: AsyncSession) -> None:
    """rediscovery도 `get_job_status`와 동일하게 종결 상태(`COMPLETED`)와 `result_url`을
    그대로 반영해야 합니다 — PENDING만 확인하면 이 경로가 놓칠 수 있습니다."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, job_id, document_id = await _setup_owner_with_job(client, db_session)

        job = await db_session.get(AiJob, job_id)
        assert job is not None
        job.status = AiJobStatus.COMPLETED
        job.completed_at = datetime.now(UTC)
        await db_session.flush()

        response = await client.get(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    assert data["status"] == "COMPLETED"
    assert data["result_url"] == f"/api/v1/ocr-jobs/{data['domain_id']}"


async def test_rediscover_ocr_job_sets_retry_after_header_for_retry_wait(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, job_id, document_id = await _setup_owner_with_job(client, db_session)

        job = await db_session.get(AiJob, job_id)
        assert job is not None
        job.status = AiJobStatus.RETRY_WAIT
        job.available_at = datetime.now(UTC) + timedelta(seconds=30)
        # 실제 전이처럼 last_consumed_event_id를 채운 뒤 expected_event_id를 비웁니다.
        job.last_consumed_event_id = job.expected_event_id
        job.expected_event_id = None
        await db_session.flush()

        response = await client.get(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()["data"]
    assert body["status"] == "RETRY_WAIT"
    assert response.headers["retry-after"] == str(body["retry_after_seconds"])


async def test_rediscover_ocr_job_response_has_no_store(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, _job_id, document_id = await _setup_owner_with_job(client, db_session)

        response = await client.get(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.headers.get_list("cache-control") == ["no-store"]


async def test_rediscover_ocr_job_does_not_create_new_rows(db_session: AsyncSession) -> None:
    """rediscovery는 순수 조회입니다 — 화면 재접속 때마다 새 Job이나 OcrJob이 만들어지면 안
    됩니다."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, job_id, document_id = await _setup_owner_with_job(client, db_session)

        job = await db_session.get(AiJob, job_id)
        assert job is not None
        ai_job_count_before = await db_session.scalar(
            select(func.count()).select_from(AiJob).where(AiJob.user_id == job.user_id)
        )
        ocr_job_count_before = await db_session.scalar(
            select(func.count()).select_from(OcrJob).where(OcrJob.document_id == document_id)
        )

        response = await client.get(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == status.HTTP_200_OK

        ai_job_count_after = await db_session.scalar(
            select(func.count()).select_from(AiJob).where(AiJob.user_id == job.user_id)
        )
        ocr_job_count_after = await db_session.scalar(
            select(func.count()).select_from(OcrJob).where(OcrJob.document_id == document_id)
        )

    assert ai_job_count_after == ai_job_count_before
    assert ocr_job_count_after == ocr_job_count_before


async def test_rediscover_ocr_job_uses_persistent_mapping_after_outbox_event_deleted(
    db_session: AsyncSession,
) -> None:
    """#212: `ocr_job.ai_job_id`가 채워져 있으면, 그 값을 만든 `outbox_event`가 이미 삭제된
    상태(30일 보존 경과 재현 — 여기서는 애초에 만들지 않음)에서도 실제 HTTP 요청으로 값을
    찾아야 합니다. 서비스 계층 테스트(test_job_status.py)와 달리 실제 라우트로 증명합니다."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        email, access_token = await _signup_and_login(client, label="redi-per")
        user = await _get_user(db_session, email=email)
        document = await _create_document(db_session, user=user)

        repo = AsyncJobRepository(db_session)
        job = await repo.create_job(user_id=user.id, job_type=AiJobType.OCR, prescription_version_id=None)
        ocr_job = OcrJob(document_id=document.id, ocr_status=OcrStatus.PENDING, ai_job_id=job.id)
        db_session.add(ocr_job)
        await db_session.flush()

        response = await client.get(
            f"/api/v1/documents/{document.id}/ocr-jobs",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["job_id"] == str(job.id)


async def test_rediscover_guide_job_requires_authentication() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/prescriptions/{uuid4()}/guides")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


async def test_rediscover_guide_job_returns_404_for_other_users_prescription(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _owner_token, _job_id, prescription_id = await _setup_owner_with_guide_job(client, db_session, label="owner")
        _other_email, other_token = await _signup_and_login(client, label="other")

        response = await client.get(
            f"/api/v1/prescriptions/{prescription_id}/guides",
            headers={"Authorization": f"Bearer {other_token}"},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["code"] == "AI_JOB_NOT_FOUND"


async def test_rediscover_guide_job_returns_latest_job_when_multiple_exist(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        email, access_token = await _signup_and_login(client, label="redi-gmu")
        user = await _get_user(db_session, email=email)
        prescription = await _create_confirmed_prescription(db_session, user=user)
        await _accept_guide_job(
            db_session,
            user=user,
            prescription=prescription,
            requested_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        latest_job_id, _latest_guide_id = await _accept_guide_job(db_session, user=user, prescription=prescription)

        response = await client.get(
            f"/api/v1/prescriptions/{prescription.id}/guides",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["job_id"] == str(latest_job_id)


async def test_rediscover_guide_job_returns_terminal_status_and_result_url(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, job_id, prescription_id = await _setup_owner_with_guide_job(client, db_session)

        job = await db_session.get(AiJob, job_id)
        assert job is not None
        job.status = AiJobStatus.COMPLETED
        job.completed_at = datetime.now(UTC)
        await db_session.flush()

        response = await client.get(
            f"/api/v1/prescriptions/{prescription_id}/guides",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    assert data["status"] == "COMPLETED"
    assert data["result_url"] == f"/api/v1/guides/{data['domain_id']}"


async def test_rediscover_guide_job_sets_retry_after_header_for_retry_wait(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, job_id, prescription_id = await _setup_owner_with_guide_job(client, db_session)

        job = await db_session.get(AiJob, job_id)
        assert job is not None
        job.status = AiJobStatus.RETRY_WAIT
        job.available_at = datetime.now(UTC) + timedelta(seconds=30)
        # 실제 전이처럼 last_consumed_event_id를 채운 뒤 expected_event_id를 비웁니다.
        job.last_consumed_event_id = job.expected_event_id
        job.expected_event_id = None
        await db_session.flush()

        response = await client.get(
            f"/api/v1/prescriptions/{prescription_id}/guides",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()["data"]
    assert body["status"] == "RETRY_WAIT"
    assert response.headers["retry-after"] == str(body["retry_after_seconds"])


async def test_rediscover_guide_job_response_has_no_store(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, _job_id, prescription_id = await _setup_owner_with_guide_job(client, db_session)

        response = await client.get(
            f"/api/v1/prescriptions/{prescription_id}/guides",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.headers.get_list("cache-control") == ["no-store"]


async def test_rediscover_guide_job_does_not_create_new_rows(db_session: AsyncSession) -> None:
    """rediscovery는 순수 조회입니다 — 화면 재접속 때마다 새 Job이나 Guide가 만들어지면 안
    됩니다."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, job_id, prescription_id = await _setup_owner_with_guide_job(client, db_session)

        job = await db_session.get(AiJob, job_id)
        assert job is not None
        ai_job_count_before = await db_session.scalar(
            select(func.count()).select_from(AiJob).where(AiJob.user_id == job.user_id)
        )
        guide_count_before = await db_session.scalar(
            select(func.count()).select_from(Guide).where(Guide.prescription_id == prescription_id)
        )

        response = await client.get(
            f"/api/v1/prescriptions/{prescription_id}/guides",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == status.HTTP_200_OK

        ai_job_count_after = await db_session.scalar(
            select(func.count()).select_from(AiJob).where(AiJob.user_id == job.user_id)
        )
        guide_count_after = await db_session.scalar(
            select(func.count()).select_from(Guide).where(Guide.prescription_id == prescription_id)
        )

    assert ai_job_count_after == ai_job_count_before
    assert guide_count_after == guide_count_before
