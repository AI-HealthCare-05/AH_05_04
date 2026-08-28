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

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
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
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
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
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
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


@pytest.mark.parametrize(
    ("override_key", "override_value", "expected_field"),
    [
        ((1, "DOSE_VALUE"), "1e100", "medications[1].dose_value"),
        ((1, "MEDICATION_NAME"), "가" * 256, "medications[1].medication_name"),
        ((1, "FREQUENCY_PER_DAY"), "2147483648", "medications[1].frequency_per_day"),
        ((1, "DURATION_DAYS"), "2147483648", "medications[1].duration_days"),
    ],
)
@pytest.mark.asyncio
async def test_confirm_prescription_api_rejects_values_outside_db_limits(
    override_key: tuple[int, str],
    override_value: str,
    expected_field: str,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token = await _signup_and_login(client, label="db-limit")
        document_id, job_id = await _upload_and_run_ocr(client, access_token=access_token)
        await _confirm_fields(
            client,
            job_id=job_id,
            access_token=access_token,
            overrides={override_key: override_value},
        )

        response = await client.post(
            f"/api/v1/documents/{document_id}/prescription",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    body = response.json()
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert body["code"] == "VALIDATION_FAILED"
    assert body["trace_id"]
    assert any(detail["field"] == expected_field for detail in body["details"])


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


@pytest.mark.asyncio
async def test_optional_extracted_field_accepts_confirmed_null() -> None:
    ConfirmationTestOcrEngine.fields = [
        *DEFAULT_RECOGNIZED_FIELDS,
        RecognizedField(
            1,
            "MEDICATION_STRENGTH",
            "100mg",
            0.99,
        ),
    ]

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        access_token = await _signup_and_login(
            client,
            label="optional-null",
        )
        headers = {
            "Authorization": f"Bearer {access_token}",
        }

        document_id, job_id = await _upload_and_run_ocr(
            client,
            access_token=access_token,
        )

        job_response = await client.get(
            f"/api/v1/ocr-jobs/{job_id}",
            headers=headers,
        )
        assert job_response.status_code == status.HTTP_200_OK

        fields = job_response.json()["data"]["fields"]

        for field in fields:
            confirmed_value = (
                None
                if field["field_type"] == "MEDICATION_STRENGTH"
                else field["normalized_value"] or field["raw_value"]
            )

            patch_response = await client.patch(
                f"/api/v1/extracted-fields/{field['field_id']}",
                json={
                    "confirmed_value": confirmed_value,
                },
                headers=headers,
            )

            assert patch_response.status_code == status.HTTP_200_OK

            if field["field_type"] == "MEDICATION_STRENGTH":
                body = patch_response.json()["data"]
                assert body["raw_value"] == "100mg"
                assert body["confirmed_value"] is None
                assert body["confirmation_status"] == "CONFIRMED"

        confirm_response = await client.post(
            f"/api/v1/documents/{document_id}/prescription",
            headers=headers,
        )

    assert confirm_response.status_code == status.HTTP_201_CREATED
    medication = confirm_response.json()["data"]["medications"][0]
    assert medication["strength_text"] is None


@pytest.mark.asyncio
async def test_required_extracted_field_rejects_confirmed_null() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        access_token = await _signup_and_login(
            client,
            label="required-null",
        )
        headers = {
            "Authorization": f"Bearer {access_token}",
        }

        _, job_id = await _upload_and_run_ocr(
            client,
            access_token=access_token,
        )

        job_response = await client.get(
            f"/api/v1/ocr-jobs/{job_id}",
            headers=headers,
        )
        assert job_response.status_code == status.HTTP_200_OK

        medication_name = next(
            field for field in job_response.json()["data"]["fields"] if field["field_type"] == "MEDICATION_NAME"
        )

        patch_response = await client.patch(
            f"/api/v1/extracted-fields/{medication_name['field_id']}",
            json={
                "confirmed_value": None,
            },
            headers=headers,
        )

    body = patch_response.json()

    assert patch_response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert body["code"] == "VALIDATION_FAILED"
    assert body["message"] == "입력값을 확인해 주세요."
    assert body["details"] == [
        {
            "field": "confirmed_value",
            "reason": "REQUIRED",
            "rejected_value": None,
        }
    ]


@pytest.mark.asyncio
async def test_update_extracted_field_api_rejects_after_prescription_confirmed() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        access_token = await _signup_and_login(
            client,
            # SignUpRequest의 이메일 최대 길이 40자를 넘지 않는 짧은 테스트 식별자를 사용합니다.
            label="patch-lock",
        )
        headers = {
            "Authorization": f"Bearer {access_token}",
        }

        document_id, job_id = await _upload_and_run_ocr(
            client,
            access_token=access_token,
        )
        await _confirm_all_fields(
            client,
            job_id=job_id,
            access_token=access_token,
        )

        # 차단 테스트에 사용할 약물명 필드와 기존 확정값을 저장합니다.
        before_response = await client.get(
            f"/api/v1/ocr-jobs/{job_id}",
            headers=headers,
        )
        assert before_response.status_code == status.HTTP_200_OK

        target_field = next(
            field for field in before_response.json()["data"]["fields"] if field["field_type"] == "MEDICATION_NAME"
        )
        original_confirmed_value = target_field["confirmed_value"]

        # 처방을 최종 확정해 이후 extracted-field 수정을 금지합니다.
        confirm_response = await client.post(
            f"/api/v1/documents/{document_id}/prescription",
            headers=headers,
        )
        assert confirm_response.status_code == status.HTTP_201_CREATED

        patch_response = await client.patch(
            f"/api/v1/extracted-fields/{target_field['field_id']}",
            json={
                "confirmed_value": "확정 후 변경하면 안 되는 약물명",
            },
            headers=headers,
        )

        # 거부된 PATCH가 실제 DB 값을 변경하지 않았는지도 함께 확인합니다.
        after_response = await client.get(
            f"/api/v1/ocr-jobs/{job_id}",
            headers=headers,
        )
        assert after_response.status_code == status.HTTP_200_OK

    body = patch_response.json()

    assert patch_response.status_code == status.HTTP_409_CONFLICT
    assert body["code"] == "PRESCRIPTION_ALREADY_CONFIRMED"
    assert body["message"] == "이미 확정된 처방 정보입니다."
    assert body["trace_id"]
    assert body["details"][0]["field"] == "document_id"
    assert body["details"][0]["reason"] == "ALREADY_CONFIRMED"

    updated_field = next(
        field for field in after_response.json()["data"]["fields"] if field["field_id"] == target_field["field_id"]
    )

    # 409 응답만 반환하고 끝나는 것이 아니라, 기존 확정값이 보존되어야 합니다.
    assert updated_field["confirmed_value"] == original_confirmed_value
