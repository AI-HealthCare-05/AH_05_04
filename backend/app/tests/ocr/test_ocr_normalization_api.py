from httpx import ASGITransport, AsyncClient
from starlette import status

from app.dependencies.services import get_ocr_engine
from app.main import app, fastapi_app
from app.services.ocr_engine import OcrDeadline, OcrRecognitionResult, RecognizedField

JPEG_SIGNATURE = b"\xff\xd8\xff"


class NormalizationTestOcrEngine:
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
                    raw_value="복합정 500 mg / 5 mg",
                    normalized_value="복합정 500mg/5mg",
                    normalization_version="rule-v1",
                    confidence_score=0.99,
                ),
            ],
        )


async def _signup_and_login(
    client: AsyncClient,
    *,
    label: str = "normalization",
) -> str:
    signup_data = {
        "email": f"ocr-{label}-api@example.com",
        "password": "Password123!",
        "name": "OCR정규화테스터",
    }

    await client.post(
        "/api/v1/auth/signup",
        json=signup_data,
    )

    login_response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": signup_data["email"],
            "password": signup_data["password"],
        },
    )

    access_token: str = login_response.json()["access_token"]
    return access_token


async def test_normalization_fields_are_saved_and_returned() -> None:
    fastapi_app.dependency_overrides[get_ocr_engine] = lambda: NormalizationTestOcrEngine()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            access_token = await _signup_and_login(client)
            headers = {
                "Authorization": f"Bearer {access_token}",
            }

            upload_response = await client.post(
                "/api/v1/documents",
                files={
                    "file": (
                        "prescription.jpg",
                        JPEG_SIGNATURE + b"fake-jpeg",
                        "image/jpeg",
                    ),
                },
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

            job_id = execute_response.json()["data"]["job_id"]

            # OCR 실행 응답이 아니라 DB 저장 후 조회 API 결과를 검사합니다.
            get_response = await client.get(
                f"/api/v1/ocr-jobs/{job_id}",
                headers=headers,
            )

        assert get_response.status_code == status.HTTP_200_OK

        fields = get_response.json()["data"]["fields"]
        medication_name = next(field for field in fields if field["field_type"] == "MEDICATION_NAME")

        assert medication_name["raw_value"] == "복합정 500 mg / 5 mg"
        assert medication_name["normalized_value"] == "복합정 500mg/5mg"
        assert medication_name["normalization_version"] == "rule-v1"
        assert medication_name["confirmed_value"] is None
        assert medication_name["confirmation_status"] == "UNCONFIRMED"
    finally:
        fastapi_app.dependency_overrides.pop(
            get_ocr_engine,
            None,
        )


async def test_ocr_job_result_returns_404_for_another_user() -> None:
    fastapi_app.dependency_overrides[get_ocr_engine] = lambda: NormalizationTestOcrEngine()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            owner_token = await _signup_and_login(client, label="owner")
            other_token = await _signup_and_login(client, label="other")

            owner_headers = {
                "Authorization": f"Bearer {owner_token}",
            }

            upload_response = await client.post(
                "/api/v1/documents",
                files={
                    "file": (
                        "prescription.jpg",
                        JPEG_SIGNATURE + b"fake-jpeg",
                        "image/jpeg",
                    ),
                },
                headers=owner_headers,
            )

            assert upload_response.status_code == status.HTTP_201_CREATED

            document_id = upload_response.json()["data"]["document_id"]

            execute_response = await client.post(
                f"/api/v1/documents/{document_id}/ocr-jobs",
                json={"force_reprocess": False},
                headers=owner_headers,
            )

            assert execute_response.status_code == status.HTTP_202_ACCEPTED

            job_id = execute_response.json()["data"]["job_id"]

            other_response = await client.get(
                f"/api/v1/ocr-jobs/{job_id}",
                headers={"Authorization": f"Bearer {other_token}"},
            )

        assert other_response.status_code == status.HTTP_404_NOT_FOUND
        assert other_response.json()["code"] == "OCR_JOB_NOT_FOUND"
    finally:
        fastapi_app.dependency_overrides.pop(
            get_ocr_engine,
            None,
        )
