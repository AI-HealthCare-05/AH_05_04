from collections.abc import Generator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from starlette import status

from app.dependencies.services import get_ocr_engine
from app.main import app, fastapi_app
from app.services.ocr_engine import OcrRecognitionResult, RecognizedField

JPEG_SIGNATURE = b"\xff\xd8\xff"

DEFAULT_RECOGNIZED_FIELDS = [
    RecognizedField(1, "MEDICATION_NAME", "혈압약정", 0.99),
    RecognizedField(1, "DOSE_VALUE", "1", 0.99),
    RecognizedField(1, "DOSE_UNIT", "정", 0.99),
    RecognizedField(1, "FREQUENCY_PER_DAY", "1", 0.99),
    RecognizedField(1, "TIMING", "아침 식후", 0.99),
    RecognizedField(1, "DURATION_DAYS", "7", 0.99),
    RecognizedField(0, "PRESCRIBED_DATE", "2026-08-01", 0.99),
]


class ConfirmationTestOcrEngine:
    fields: list[RecognizedField] = DEFAULT_RECOGNIZED_FIELDS

    async def recognize(self, *, object_key: str, file_mime_type: str) -> OcrRecognitionResult:
        _ = object_key, file_mime_type
        return OcrRecognitionResult(fields=list(self.fields))


@pytest.fixture(autouse=True)
def override_ocr_engine() -> Generator[None]:
    ConfirmationTestOcrEngine.fields = list(DEFAULT_RECOGNIZED_FIELDS)
    fastapi_app.dependency_overrides[get_ocr_engine] = lambda: ConfirmationTestOcrEngine()
    yield
    fastapi_app.dependency_overrides.pop(get_ocr_engine, None)


async def _signup_and_login(client: AsyncClient, *, label: str) -> str:
    suffix = uuid4().hex[:8]
    email = f"pc-{label}-{suffix}@example.com"
    signup_response = await client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": "Password123!",
            "name": "처방확정테스터",
        },
    )
    assert signup_response.status_code == status.HTTP_201_CREATED, signup_response.text

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
    )
    assert login_response.status_code == status.HTTP_200_OK, login_response.text
    return login_response.json()["access_token"]


async def _upload_and_run_ocr(client: AsyncClient, *, access_token: str) -> tuple[str, str]:
    headers = {"Authorization": f"Bearer {access_token}"}
    upload_response = await client.post(
        "/api/v1/documents",
        files={"file": ("prescription.jpg", JPEG_SIGNATURE + b"fake-jpeg", "image/jpeg")},
        headers=headers,
    )
    assert upload_response.status_code == status.HTTP_201_CREATED
    document_id = upload_response.json()["data"]["document_id"]

    ocr_response = await client.post(
        f"/api/v1/documents/{document_id}/ocr-jobs",
        json={"force_reprocess": True},
        headers=headers,
    )
    assert ocr_response.status_code == status.HTTP_202_ACCEPTED
    return document_id, ocr_response.json()["data"]["job_id"]


async def _confirm_all_fields(client: AsyncClient, *, job_id: str, access_token: str) -> None:
    await _confirm_fields(client, job_id=job_id, access_token=access_token)


async def _confirm_fields(
    client: AsyncClient,
    *,
    job_id: str,
    access_token: str,
    medication_indexes: set[int] | None = None,
    overrides: dict[tuple[int, str], str] | None = None,
) -> None:
    headers = {"Authorization": f"Bearer {access_token}"}
    result_response = await client.get(f"/api/v1/ocr-jobs/{job_id}", headers=headers)
    assert result_response.status_code == status.HTTP_200_OK

    for field in result_response.json()["data"]["fields"]:
        if medication_indexes is not None and field["medication_index"] not in medication_indexes:
            continue
        value = field["normalized_value"] or field["raw_value"]
        if overrides is not None:
            value = overrides.get((field["medication_index"], field["field_type"]), value)
        response = await client.patch(
            f"/api/v1/extracted-fields/{field['field_id']}",
            json={"confirmed_value": value},
            headers=headers,
        )
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_confirm_prescription_api_uses_confirmed_fields() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token = await _signup_and_login(client, label="success")
        document_id, job_id = await _upload_and_run_ocr(client, access_token=access_token)
        await _confirm_all_fields(client, job_id=job_id, access_token=access_token)

        response = await client.post(
            f"/api/v1/documents/{document_id}/prescription",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["data"]["medications"][0]["medication_name"] == "혈압약정"


@pytest.mark.asyncio
async def test_confirm_prescription_api_rejects_unreviewed_fields() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token = await _signup_and_login(client, label="unreviewed")
        document_id, _ = await _upload_and_run_ocr(client, access_token=access_token)

        response = await client.post(
            f"/api/v1/documents/{document_id}/prescription",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["code"] == "PRESCRIPTION_REQUIRED_FIELD_MISSING"
    assert response.json()["message"] == "처방 확정에 필요한 항목이 누락되었습니다."
    assert response.json()["details"]
    assert response.json()["trace_id"]


@pytest.mark.asyncio
async def test_confirm_prescription_api_rejects_when_only_one_of_two_medications_is_reviewed() -> None:
    ConfirmationTestOcrEngine.fields = [
        *DEFAULT_RECOGNIZED_FIELDS,
        RecognizedField(2, "MEDICATION_NAME", "당뇨약정", 0.99),
        RecognizedField(2, "DOSE_VALUE", "1", 0.99),
        RecognizedField(2, "DOSE_UNIT", "정", 0.99),
        RecognizedField(2, "FREQUENCY_PER_DAY", "2", 0.99),
        RecognizedField(2, "TIMING", "아침 저녁 식후", 0.99),
        RecognizedField(2, "DURATION_DAYS", "14", 0.99),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token = await _signup_and_login(client, label="partial-review")
        document_id, job_id = await _upload_and_run_ocr(client, access_token=access_token)
        await _confirm_fields(client, job_id=job_id, access_token=access_token, medication_indexes={0, 1})

        response = await client.post(
            f"/api/v1/documents/{document_id}/prescription",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    body = response.json()
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert body["code"] == "PRESCRIPTION_REQUIRED_FIELD_MISSING"
    assert body["message"] == "처방 확정에 필요한 항목이 누락되었습니다."
    assert body["trace_id"]
    assert {detail["field"] for detail in body["details"]} >= {
        "medications[2].medication_name",
        "medications[2].dose_value",
        "medications[2].frequency_per_day",
        "medications[2].duration_days",
    }


@pytest.mark.asyncio
async def test_confirm_prescription_api_rejects_invalid_numeric_value() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token = await _signup_and_login(client, label="invalid-number")
        document_id, job_id = await _upload_and_run_ocr(client, access_token=access_token)
        await _confirm_fields(
            client,
            job_id=job_id,
            access_token=access_token,
            overrides={(1, "DOSE_VALUE"): "약 반 알"},
        )

        response = await client.post(
            f"/api/v1/documents/{document_id}/prescription",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    body = response.json()
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert body["code"] == "VALIDATION_FAILED"
    assert body["message"] == "입력값을 확인해 주세요."
    assert body["trace_id"]
    assert body["details"] == [
        {
            "field": "medications[1].dose_value",
            "reason": "INVALID_FORMAT",
            "rejected_value": "약 반 알",
        }
    ]


@pytest.mark.asyncio
async def test_confirm_prescription_api_rejects_another_users_document() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        owner_token = await _signup_and_login(client, label="owner")
        document_id, _ = await _upload_and_run_ocr(client, access_token=owner_token)
        other_user_token = await _signup_and_login(client, label="other-user")

        response = await client.post(
            f"/api/v1/documents/{document_id}/prescription",
            headers={"Authorization": f"Bearer {other_user_token}"},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["code"] == "MEDICAL_DOCUMENT_NOT_FOUND"
