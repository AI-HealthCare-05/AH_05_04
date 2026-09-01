"""처방 확정과 extracted-field PATCH의 동시 요청 직렬화를 검증합니다.

기본 isolate_database fixture는 모든 요청을 단일 connection과 savepoint에 묶기 때문에
row lock이 없어도 요청이 직렬화되어 회귀를 잡지 못합니다.
이 모듈은 요청마다 독립 connection을 쓰는 real_connection_app fixture를 사용하고,
격리를 포기하는 대신 테스트가 직접 정리합니다.
"""

import asyncio
from collections.abc import AsyncIterator, Generator
from datetime import UTC, date, datetime
from time import monotonic
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core.db.databases import get_db_session
from app.dependencies.services import get_ocr_engine
from app.main import app, fastapi_app
from app.models.prescriptions import Prescription, PrescriptionStatus
from app.services.ocr_engine import OcrRecognitionResult, RecognizedField
from app.tests.conftest import test_engine

JPEG_SIGNATURE = b"\xff\xd8\xff"

# 구현의 SET LOCAL lock_timeout 값과 맞춥니다.
LOCK_TIMEOUT_SECONDS = 3

# 잠금 보유자가 경쟁 요청을 blocking 상태로 만든 뒤 작업을 마칠 때까지의 여유입니다.
HOLD_SECONDS = 0.5

DEFAULT_RECOGNIZED_FIELDS = [
    RecognizedField(1, "MEDICATION_NAME", "혈압약정", 0.99),
    RecognizedField(1, "DOSE_VALUE", "1", 0.99),
    RecognizedField(1, "DOSE_UNIT", "정", 0.99),
    RecognizedField(1, "FREQUENCY_PER_DAY", "1", 0.99),
    RecognizedField(1, "TIMING", "아침 식후", 0.99),
    RecognizedField(1, "DURATION_DAYS", "7", 0.99),
    RecognizedField(0, "PRESCRIBED_DATE", "2026-08-01", 0.99),
]

TRUNCATE_TARGETS = (
    "extracted_field",
    "medication",
    "prescription",
    "ocr_job",
    "medical_document",
    "profile",
    '"user"',
)


class ConcurrencyTestOcrEngine:
    async def recognize(self, *, object_key: str, file_mime_type: str) -> OcrRecognitionResult:
        _ = object_key, file_mime_type
        return OcrRecognitionResult(fields=list(DEFAULT_RECOGNIZED_FIELDS))


@pytest.fixture(autouse=True)
def override_ocr_engine() -> Generator[None]:
    fastapi_app.dependency_overrides[get_ocr_engine] = lambda: ConcurrencyTestOcrEngine()
    yield
    fastapi_app.dependency_overrides.pop(get_ocr_engine, None)


@pytest_asyncio.fixture
async def real_connection_app() -> AsyncIterator[None]:
    """요청마다 독립 connection을 사용하도록 get_db_session을 교체합니다.

    autouse인 isolate_database가 먼저 override를 설정하므로 여기서 덮어씁니다.
    실제 commit이 발생하므로 teardown에서 관련 테이블을 직접 정리합니다.
    """

    async def override_get_db_session() -> AsyncIterator[AsyncSession]:
        session = AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    fastapi_app.dependency_overrides[get_db_session] = override_get_db_session

    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)
        async with test_engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE TABLE {', '.join(TRUNCATE_TARGETS)} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def lock_holder() -> AsyncIterator[AsyncSession]:
    """경쟁 상황을 만들기 위해 문서 row lock을 직접 선점하는 세션입니다."""
    session = AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False)
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()


async def _lock_document(session: AsyncSession, document_id: str) -> UUID:
    """구현이 잠그는 것과 같은 MEDICAL_DOCUMENT row를 선점하고 소유 profile_id를 돌려줍니다."""
    result = await session.execute(
        text("SELECT profile_id FROM medical_document WHERE id = :document_id FOR UPDATE"),
        {"document_id": document_id},
    )
    return UUID(result.scalar_one())


async def _signup_and_login(client: AsyncClient, *, label: str) -> str:
    email = f"cc-{label}-{uuid4().hex[:8]}@example.com"
    signup = await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "Password123!", "name": "동시성테스터"},
    )
    assert signup.status_code == status.HTTP_201_CREATED, signup.text

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
    )
    assert login.status_code == status.HTTP_200_OK, login.text
    return login.json()["access_token"]


async def _upload_and_run_ocr(client: AsyncClient, *, access_token: str) -> tuple[str, str]:
    headers = {"Authorization": f"Bearer {access_token}"}
    upload = await client.post(
        "/api/v1/documents",
        files={"file": ("prescription.jpg", JPEG_SIGNATURE + b"fake-jpeg", "image/jpeg")},
        headers=headers,
    )
    assert upload.status_code == status.HTTP_201_CREATED, upload.text
    document_id = upload.json()["data"]["document_id"]

    ocr = await client.post(
        f"/api/v1/documents/{document_id}/ocr-jobs",
        json={"force_reprocess": True},
        headers=headers,
    )
    assert ocr.status_code == status.HTTP_202_ACCEPTED, ocr.text
    return document_id, ocr.json()["data"]["job_id"]


async def _confirm_all_fields(client: AsyncClient, *, job_id: str, access_token: str) -> list[dict]:
    """모든 필드를 검수 완료 상태로 만들고 필드 목록을 돌려줍니다."""
    headers = {"Authorization": f"Bearer {access_token}"}
    result = await client.get(f"/api/v1/ocr-jobs/{job_id}", headers=headers)
    assert result.status_code == status.HTTP_200_OK, result.text

    fields = result.json()["data"]["fields"]
    for field in fields:
        value = field["normalized_value"] or field["raw_value"]
        response = await client.patch(
            f"/api/v1/extracted-fields/{field['field_id']}",
            json={"confirmed_value": value},
            headers=headers,
        )
        assert response.status_code == status.HTTP_200_OK, response.text
    return fields


async def _prepare_reviewed_document(client: AsyncClient, *, label: str) -> tuple[str, str, str, list[dict]]:
    access_token = await _signup_and_login(client, label=label)
    document_id, job_id = await _upload_and_run_ocr(client, access_token=access_token)
    fields = await _confirm_all_fields(client, job_id=job_id, access_token=access_token)
    return access_token, document_id, job_id, fields


def _field_id(fields: list[dict], *, field_type: str) -> str:
    return next(field["field_id"] for field in fields if field["field_type"] == field_type)


@pytest.mark.asyncio
async def test_confirm_waits_and_times_out_when_document_row_is_locked(
    real_connection_app: None,
    lock_holder: AsyncSession,
) -> None:
    """5. lock_timeout 초과 시 409 CONCURRENT_UPDATE_IN_PROGRESS."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, document_id, _, _ = await _prepare_reviewed_document(client, label="timeout")

        await _lock_document(lock_holder, document_id)

        started = monotonic()
        response = await client.post(
            f"/api/v1/documents/{document_id}/prescription",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        elapsed = monotonic() - started

    assert response.status_code == status.HTTP_409_CONFLICT, response.text
    assert response.json()["code"] == "CONCURRENT_UPDATE_IN_PROGRESS"

    # 즉시 실패하면 lock을 아예 잡지 않았다는 뜻입니다.
    assert elapsed >= LOCK_TIMEOUT_SECONDS * 0.8


@pytest.mark.asyncio
async def test_patch_waits_for_confirmation_and_then_rejects(
    real_connection_app: None,
    lock_holder: AsyncSession,
) -> None:
    """1. 확정 진행 중 진입한 PATCH는 대기 후 409 PRESCRIPTION_ALREADY_CONFIRMED."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, document_id, job_id, fields = await _prepare_reviewed_document(client, label="patch-wait")
        headers = {"Authorization": f"Bearer {access_token}"}
        target_field_id = _field_id(fields, field_type="MEDICATION_NAME")

        # 확정 transaction이 lock을 잡고 있는 상태를 재현합니다.
        profile_id = await _lock_document(lock_holder, document_id)

        patch_task = asyncio.create_task(
            client.patch(
                f"/api/v1/extracted-fields/{target_field_id}",
                json={"confirmed_value": "뒤늦은수정"},
                headers=headers,
            )
        )
        await asyncio.sleep(HOLD_SECONDS)
        assert not patch_task.done(), "PATCH가 lock을 기다리지 않고 통과했습니다."

        # 잠금 보유자가 확정을 완료하고 lock을 놓습니다.
        lock_holder.add(
            Prescription(
                document_id=UUID(document_id),
                source_ocr_job_id=UUID(job_id),
                profile_id=profile_id,
                prescribed_date=date(2026, 8, 1),
                prescription_status=PrescriptionStatus.CONFIRMED,
                confirmed_at=datetime.now(UTC),
            )
        )
        await lock_holder.commit()

        response = await asyncio.wait_for(patch_task, timeout=LOCK_TIMEOUT_SECONDS + 5)

    assert response.status_code == status.HTTP_409_CONFLICT, response.text
    assert response.json()["code"] == "PRESCRIPTION_ALREADY_CONFIRMED"


@pytest.mark.asyncio
async def test_confirmation_reflects_patch_committed_while_waiting(
    real_connection_app: None,
    lock_holder: AsyncSession,
) -> None:
    """2. PATCH 진행 중 진입한 확정은 PATCH 값을 반영해야 합니다. 이 이슈의 본체입니다."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, document_id, job_id, _ = await _prepare_reviewed_document(client, label="patch-first")

        # PATCH transaction이 lock을 잡고 있는 상태를 재현합니다.
        await _lock_document(lock_holder, document_id)

        confirm_task = asyncio.create_task(
            client.post(
                f"/api/v1/documents/{document_id}/prescription",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        )
        await asyncio.sleep(HOLD_SECONDS)
        assert not confirm_task.done(), "확정이 lock을 기다리지 않고 통과했습니다."

        # 잠금 보유자가 검수값을 바꾸고 commit합니다.
        await lock_holder.execute(
            text(
                "UPDATE extracted_field SET confirmed_value = :value "
                "WHERE ocr_job_id = :job_id AND field_type = 'MEDICATION_NAME'"
            ),
            {"value": "수정된약이름", "job_id": job_id},
        )
        await lock_holder.commit()

        response = await asyncio.wait_for(confirm_task, timeout=LOCK_TIMEOUT_SECONDS + 5)

    assert response.status_code == status.HTTP_201_CREATED, response.text

    # lock 이전에 읽은 값을 쓰면 여기서 "혈압약정"이 나옵니다.
    assert response.json()["data"]["medications"][0]["medication_name"] == "수정된약이름"


@pytest.mark.asyncio
async def test_two_concurrent_confirmations_produce_one_success_and_one_conflict(
    real_connection_app: None,
) -> None:
    """3. 동시 확정 2건 중 하나만 성공하고, 나머지는 500이 아닌 409여야 합니다."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, document_id, _, _ = await _prepare_reviewed_document(client, label="double")
        headers = {"Authorization": f"Bearer {access_token}"}

        first, second = await asyncio.gather(
            client.post(f"/api/v1/documents/{document_id}/prescription", headers=headers),
            client.post(f"/api/v1/documents/{document_id}/prescription", headers=headers),
        )

    codes = sorted([first.status_code, second.status_code])
    assert codes == [status.HTTP_201_CREATED, status.HTTP_409_CONFLICT], (first.text, second.text)

    conflict = first if first.status_code == status.HTTP_409_CONFLICT else second
    # document_id unique 제약 때문에 lock이 없으면 IntegrityError 500이 됩니다.
    assert conflict.json()["code"] == "PRESCRIPTION_ALREADY_CONFIRMED"


@pytest.mark.asyncio
async def test_lock_scope_is_per_document(
    real_connection_app: None,
    lock_holder: AsyncSession,
) -> None:
    """6. 다른 문서의 요청은 서로 대기하지 않아야 합니다."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _, locked_document_id, _, _ = await _prepare_reviewed_document(client, label="other-a")
        access_token, free_document_id, _, _ = await _prepare_reviewed_document(client, label="other-b")

        await _lock_document(lock_holder, locked_document_id)

        started = monotonic()
        response = await client.post(
            f"/api/v1/documents/{free_document_id}/prescription",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        elapsed = monotonic() - started

    assert response.status_code == status.HTTP_201_CREATED, response.text
    # 잠금 대기가 발생하지 않았음을 보는 것이므로 lock_timeout보다 짧으면 충분합니다.
    # CI 러너 지연을 고려해 여유를 둡니다.
    assert elapsed < LOCK_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_patch_times_out_and_preserves_value_when_document_row_is_locked(
    real_connection_app: None,
    lock_holder: AsyncSession,
) -> None:
    """PATCH의 공개 lock timeout 계약입니다.

    잠금이 유지된 상태에서 409 CONCURRENT_UPDATE_IN_PROGRESS와 공통 error envelope를
    반환하고 기존 confirmed_value를 변경하지 않아야 합니다.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, document_id, job_id, fields = await _prepare_reviewed_document(client, label="patch-timeout")
        headers = {"Authorization": f"Bearer {access_token}"}

        target = next(field for field in fields if field["field_type"] == "MEDICATION_NAME")
        # _confirm_all_fields가 저장한 값과 같은 기준으로 원래 값을 계산합니다.
        expected_value = target["normalized_value"] or target["raw_value"]

        # 확정 transaction이 잠금을 계속 보유한 상태를 재현합니다.
        await _lock_document(lock_holder, document_id)

        started = monotonic()
        response = await client.patch(
            f"/api/v1/extracted-fields/{target['field_id']}",
            json={"confirmed_value": "잠금중수정시도"},
            headers=headers,
        )
        elapsed = monotonic() - started

        assert response.status_code == status.HTTP_409_CONFLICT, response.text

        body = response.json()
        assert body["code"] == "CONCURRENT_UPDATE_IN_PROGRESS"
        assert body["message"] == "같은 문서에 대한 다른 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요."
        assert body["details"] == [
            {
                "field": "field_id",
                "reason": "CONCURRENT_UPDATE_IN_PROGRESS",
                "rejected_value": None,
            }
        ]
        assert body["trace_id"]

        # 즉시 실패하면 잠금을 기다리지 않았다는 뜻입니다.
        assert elapsed >= LOCK_TIMEOUT_SECONDS * 0.8

        # 잠금을 해제한 뒤 기존 값이 그대로인지 확인합니다.
        await lock_holder.rollback()

        after = await client.get(f"/api/v1/ocr-jobs/{job_id}", headers=headers)
        assert after.status_code == status.HTTP_200_OK, after.text

        current = next(field for field in after.json()["data"]["fields"] if field["field_id"] == target["field_id"])

    assert current["confirmed_value"] == expected_value
    assert current["confirmation_status"] == "CONFIRMED"
