from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core.db.databases import get_db_session
from app.core.jwt.tokens import AccessToken
from app.main import app, fastapi_app
from app.models.async_jobs import AiJob, AiJobStatus, AiJobType, DomainType
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob, OcrStatus
from app.models.profiles import Profile, ProfileType
from app.models.users import User
from app.repositories.async_job_repository import AsyncJobRepository
from app.services.job_intake import DomainReference, JobIntakeService
from app.tests.conftest import test_engine


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


def _build_expired_access_token(user: User) -> str:
    """`AccessToken.set_exp()`는 `TIMEZONE`(Asia/Seoul, UTC+9) 벽시계 값을 `timegm()`으로
    UTC로 오인해 실제 만료 시각이 의도보다 9시간 늦게 계산되는 기존 버그가 있어, `from_time`을
    과거로 줘도 실제로는 만료되지 않습니다. `exp` claim을 지나간 실제 UTC epoch으로 직접
    덮어써 이 버그와 무관하게 만료 토큰을 재현합니다."""
    token = AccessToken.for_user(user)
    token.payload["exp"] = int(datetime.now(UTC).timestamp()) - 600
    return str(token)


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

    async def test_get_job_status_returns_401_expired_token_for_expired_access_token(
        self, db_session: AsyncSession
    ) -> None:
        """리뷰 지적: 실제 인증 경계(`get_request_user` → `JwtService.verify_jwt`)는 만료된
        access token에 `code=EXPIRED_TOKEN`을 반환하는데, `JOB_STATUS_OPENAPI_RESPONSES`와
        `job-status-v1.md` 401 표에는 `UNAUTHORIZED`\\|`INVALID_TOKEN`만 있어 실제 응답과
        문서가 어긋났습니다."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            email, _access_token = await _signup_and_login(client, label="expired")
            user = await _get_user(db_session, email=email)
            expired_token = _build_expired_access_token(user)

            response = await client.get(
                f"/api/v1/jobs/{uuid4()}",
                headers={"Authorization": f"Bearer {expired_token}"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "EXPIRED_TOKEN"

    async def test_get_job_status_returns_422_for_invalid_job_id(self) -> None:
        """#148 세 번째 리뷰: path parameter UUID 검증 실패는 FastAPI 기본
        `HTTPValidationError`가 아니라 전역 핸들러가 만드는 `ErrorResponse`(`VALIDATION_FAILED`)로
        나와야 합니다 — `JOB_STATUS_OPENAPI_RESPONSES`에 명시한 422 계약과 실제 응답이
        일치하는지 실제 라우트로 검증합니다."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _email, access_token = await _signup_and_login(client, label="badid")

            response = await client.get(
                "/api/v1/jobs/not-a-uuid",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        body = response.json()
        assert body["code"] == "VALIDATION_FAILED"
        assert "details" in body
