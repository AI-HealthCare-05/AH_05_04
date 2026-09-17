import re
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core import config
from app.main import app
from app.models.account_deletion_request import AccountDeletionRequest, AccountDeletionRequestStatus
from app.models.medical_documents import DocumentType, MedicalDocument, UploadStatus
from app.models.ocr import ConfirmationStatus, ExtractedField, FieldType, OcrJob, OcrStatus
from app.models.prescriptions import (
    Prescription,
    PrescriptionStatus,
    PrescriptionVersion,
    PrescriptionVersionMedication,
)
from app.models.profiles import Profile
from app.models.push import PushSubscription
from app.models.rag_candidate import (
    MedicationCandidateSearch,
    MedicationCandidateSearchResult,
    MedicationCandidateSearchStatus,
    MedicationIdentification,
    MedicationIdentificationSource,
    MedicationIdentificationStatus,
)
from app.models.users import AccountStatus, User
from app.tests.helpers.auth import signup_verified_user

PASSWORD = "Password123!"


@pytest.fixture
def enable_account_withdrawal_request(monkeypatch):
    monkeypatch.setattr(config, "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED", True)


def extract_refresh_token(response) -> str:
    set_cookie = response.headers.get("set-cookie", "")
    match = re.search(r"refresh_token=([^;]+)", set_cookie)
    assert match is not None
    return match.group(1)


async def signup_and_login(client: AsyncClient, *, email: str) -> tuple[str, str]:
    await signup_verified_user(client, {"email": email, "password": PASSWORD, "name": "탈퇴테스터"})
    login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login_response.status_code == status.HTTP_200_OK, login_response.text
    return login_response.json()["access_token"], extract_refresh_token(login_response)


async def user_by_email(db_session: AsyncSession, email: str) -> User:
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


async def deletion_request_count(db_session: AsyncSession, user_id) -> int:
    return int(
        await db_session.scalar(
            select(func.count()).select_from(AccountDeletionRequest).where(AccountDeletionRequest.user_id == user_id)
        )
    )


async def seed_user_owned_ocr_data(db_session: AsyncSession, *, user_id) -> tuple:
    profile_id = await db_session.scalar(select(Profile.id).where(Profile.user_id == user_id))
    assert profile_id is not None
    document = MedicalDocument(
        uploaded_by=user_id,
        profile_id=profile_id,
        document_type=DocumentType.PRESCRIPTION,
        original_file_name="withdrawal-prescription.png",
        object_key="private/withdrawal-prescription.png",
        file_mime_type="image/png",
        file_size_bytes=1024,
        upload_status=UploadStatus.UPLOADED,
    )
    db_session.add(document)
    await db_session.flush()
    ocr_job = OcrJob(
        document_id=document.id,
        ocr_status=OcrStatus.COMPLETED,
        completed_at=datetime.now(UTC),
    )
    db_session.add(ocr_job)
    await db_session.flush()
    db_session.add(
        ExtractedField(
            ocr_job_id=ocr_job.id,
            medication_index=1,
            field_type=FieldType.MEDICATION_NAME,
            raw_value="개인 처방 약명",
            normalized_value="개인 처방 약명",
            confirmed_value="개인 처방 약명",
            confidence_score=Decimal("0.9500"),
            confirmation_status=ConfirmationStatus.CONFIRMED,
            confirmed_at=datetime.now(UTC),
        )
    )
    await db_session.flush()
    return document.id, ocr_job.id


async def seed_user_owned_prescription_data(db_session: AsyncSession, *, user_id, document_id, ocr_job_id):
    profile_id = await db_session.scalar(select(Profile.id).where(Profile.user_id == user_id))
    assert profile_id is not None
    prescription_id = uuid4()
    version_id = uuid4()
    version_medication_id = uuid4()
    confirmed_at = datetime.now(UTC)
    db_session.add(
        Prescription(
            id=prescription_id,
            active_version_id=version_id,
            document_id=document_id,
            source_ocr_job_id=ocr_job_id,
            profile_id=profile_id,
            prescribed_date=date(2026, 9, 15),
            prescription_status=PrescriptionStatus.CONFIRMED,
            confirmed_at=confirmed_at,
        )
    )
    db_session.add(
        PrescriptionVersion(
            id=version_id,
            prescription_id=prescription_id,
            medication_count=1,
            content_hash="a" * 64,
            version_number=1,
            prescribed_date=date(2026, 9, 15),
            confirmed_at=confirmed_at,
        )
    )
    db_session.add(
        PrescriptionVersionMedication(
            id=version_medication_id,
            prescription_version_id=version_id,
            medication_count=1,
            medication_name="개인 처방 약명",
            strength_text="10mg",
            display_order=1,
        )
    )
    await db_session.flush()
    return version_medication_id


async def seed_user_owned_identification_data(db_session: AsyncSession, *, prescription_version_medication_id) -> None:
    search = MedicationCandidateSearch(
        prescription_version_medication_id=prescription_version_medication_id,
        medication_name_snapshot="개인 처방 약명",
        strength_text_snapshot="10mg",
        query_digest="a" * 64,
        status=MedicationCandidateSearchStatus.READY,
        candidate_count=1,
        displayed_candidate_count=1,
    )
    db_session.add(search)
    await db_session.flush()
    result = MedicationCandidateSearchResult(
        search_id=search.id,
        product_id=uuid4(),
        code_system="MFDS_ITEM_SEQ",
        canonical_code="WITHDRAWAL-ITEM",
        product_name="개인 식별 후보",
        product_status="ACTIVE",
        result_rank=1,
        result_score=0.99,
        result_method="fixture",
        is_displayed=True,
        selection_eligible=True,
    )
    db_session.add(result)
    await db_session.flush()
    db_session.add(
        MedicationIdentification(
            prescription_version_medication_id=prescription_version_medication_id,
            candidate_search_id=search.id,
            candidate_search_result_id=result.id,
            product_id=result.product_id,
            code_system=result.code_system,
            canonical_code=result.canonical_code,
            status=MedicationIdentificationStatus.MATCHED,
            source=MedicationIdentificationSource.USER_SELECTED,
            confirmed_at=datetime.now(UTC),
        )
    )
    await db_session.flush()


async def seed_user_push_subscription(db_session: AsyncSession, *, user_id) -> None:
    profile_id = await db_session.scalar(select(Profile.id).where(Profile.user_id == user_id))
    assert profile_id is not None
    db_session.add(
        PushSubscription(
            profile_id=profile_id,
            token_version=0,
            generation=uuid4(),
            endpoint_hmac=uuid4().hex + uuid4().hex,
            ciphertext=b"encrypted-push-endpoint",
            key_id="demo-key",
            activated_at=datetime.now(UTC),
        )
    )
    await db_session.flush()


async def user_push_subscription_count(db_session: AsyncSession, *, user_id) -> int:
    profile_ids = select(Profile.id).where(Profile.user_id == user_id)
    count = await db_session.scalar(
        select(func.count()).select_from(PushSubscription).where(PushSubscription.profile_id.in_(profile_ids))
    )
    return int(count or 0)


async def user_owned_identification_row_count(db_session: AsyncSession, *, user_id) -> int:
    profile_ids = select(Profile.id).where(Profile.user_id == user_id)
    prescription_ids = select(Prescription.id).where(Prescription.profile_id.in_(profile_ids))
    version_ids = select(PrescriptionVersion.id).where(PrescriptionVersion.prescription_id.in_(prescription_ids))
    medication_ids = select(PrescriptionVersionMedication.id).where(
        PrescriptionVersionMedication.prescription_version_id.in_(version_ids)
    )
    search_ids = select(MedicationCandidateSearch.id).where(
        MedicationCandidateSearch.prescription_version_medication_id.in_(medication_ids)
    )
    identification_count = await db_session.scalar(
        select(func.count())
        .select_from(MedicationIdentification)
        .where(MedicationIdentification.prescription_version_medication_id.in_(medication_ids))
    )
    result_count = await db_session.scalar(
        select(func.count())
        .select_from(MedicationCandidateSearchResult)
        .where(MedicationCandidateSearchResult.search_id.in_(search_ids))
    )
    search_count = await db_session.scalar(
        select(func.count())
        .select_from(MedicationCandidateSearch)
        .where(MedicationCandidateSearch.prescription_version_medication_id.in_(medication_ids))
    )
    return int(identification_count or 0) + int(result_count or 0) + int(search_count or 0)


async def user_owned_ocr_row_count(db_session: AsyncSession, *, user_id) -> int:
    document_ids = select(MedicalDocument.id).where(MedicalDocument.uploaded_by == user_id)
    ocr_job_ids = select(OcrJob.id).where(OcrJob.document_id.in_(document_ids))
    document_count = await db_session.scalar(
        select(func.count()).select_from(MedicalDocument).where(MedicalDocument.uploaded_by == user_id)
    )
    ocr_count = await db_session.scalar(
        select(func.count()).select_from(OcrJob).where(OcrJob.document_id.in_(document_ids))
    )
    field_count = await db_session.scalar(
        select(func.count()).select_from(ExtractedField).where(ExtractedField.ocr_job_id.in_(ocr_job_ids))
    )
    return int(document_count or 0) + int(ocr_count or 0) + int(field_count or 0)


async def user_owned_prescription_row_count(db_session: AsyncSession, *, user_id) -> int:
    profile_ids = select(Profile.id).where(Profile.user_id == user_id)
    prescription_ids = select(Prescription.id).where(Prescription.profile_id.in_(profile_ids))
    version_ids = select(PrescriptionVersion.id).where(PrescriptionVersion.prescription_id.in_(prescription_ids))
    prescription_count = await db_session.scalar(
        select(func.count()).select_from(Prescription).where(Prescription.profile_id.in_(profile_ids))
    )
    version_count = await db_session.scalar(
        select(func.count()).select_from(PrescriptionVersion).where(PrescriptionVersion.id.in_(version_ids))
    )
    version_medication_count = await db_session.scalar(
        select(func.count())
        .select_from(PrescriptionVersionMedication)
        .where(PrescriptionVersionMedication.prescription_version_id.in_(version_ids))
    )
    return int(prescription_count or 0) + int(version_count or 0) + int(version_medication_count or 0)


async def test_account_withdrawal_is_closed_when_public_gate_disabled(db_session: AsyncSession):
    email = f"withdrawal-gate-{uuid4().hex[:10]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, _ = await signup_and_login(client, email=email)
        response = await client.post(
            "/api/v1/auth/account/withdrawal",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"password": PASSWORD, "confirmed": True},
        )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    body = response.json()
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["details"] == [
        {
            "field": "account_withdrawal",
            "reason": "ACCOUNT_WITHDRAWAL_REQUEST_DISABLED",
            "rejected_value": None,
        }
    ]
    user = await user_by_email(db_session, email)
    assert user.account_status == AccountStatus.ACTIVE
    assert user.is_active is True
    assert user.token_version == 0
    assert await deletion_request_count(db_session, user.id) == 0


async def test_account_withdrawal_success_completes_demo_deletion_and_allows_resignup(
    db_session: AsyncSession, enable_account_withdrawal_request
):
    email = f"withdrawal-{uuid4().hex[:10]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, refresh_token = await signup_and_login(client, email=email)
        original_user = await user_by_email(db_session, email)
        original_user_id = original_user.id
        document_id, ocr_job_id = await seed_user_owned_ocr_data(db_session, user_id=original_user_id)
        version_medication_id = await seed_user_owned_prescription_data(
            db_session,
            user_id=original_user_id,
            document_id=document_id,
            ocr_job_id=ocr_job_id,
        )
        await seed_user_owned_identification_data(db_session, prescription_version_medication_id=version_medication_id)
        await seed_user_push_subscription(db_session, user_id=original_user_id)
        other_email = f"withdrawal-other-{uuid4().hex[:10]}@example.com"
        await signup_verified_user(client, {"email": other_email, "password": PASSWORD, "name": "다른사용자"})
        other_user = await user_by_email(db_session, other_email)
        other_user_id = other_user.id
        other_document_id, other_ocr_job_id = await seed_user_owned_ocr_data(db_session, user_id=other_user_id)
        await seed_user_owned_prescription_data(
            db_session,
            user_id=other_user_id,
            document_id=other_document_id,
            ocr_job_id=other_ocr_job_id,
        )
        assert await user_owned_ocr_row_count(db_session, user_id=original_user_id) == 3
        assert await user_owned_prescription_row_count(db_session, user_id=original_user_id) == 3
        assert await user_owned_identification_row_count(db_session, user_id=original_user_id) == 3
        assert await user_push_subscription_count(db_session, user_id=original_user_id) == 1
        assert await user_owned_ocr_row_count(db_session, user_id=other_user_id) == 3
        assert await user_owned_prescription_row_count(db_session, user_id=other_user_id) == 3
        response = await client.post(
            "/api/v1/auth/account/withdrawal",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"password": PASSWORD, "confirmed": True},
        )
        user_me_response = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {access_token}"})
        client.cookies["refresh_token"] = refresh_token
        refresh_response = await client.get("/api/v1/auth/token/refresh")
        resignup_response = await client.post(
            "/api/v1/auth/signup",
            json={"email": email, "password": PASSWORD, "name": "재가입테스터"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"detail": "회원탈퇴가 완료되었습니다."}
    assert "refresh_token=" in response.headers.get("set-cookie", "")

    db_session.expire_all()
    resigned_user = await db_session.scalar(select(User).where(User.email == email))
    assert resigned_user is not None
    assert resigned_user.id != original_user_id
    user = await db_session.get(User, original_user_id)
    assert user is not None
    assert user.account_status == AccountStatus.WITHDRAWN
    assert user.is_active is False
    assert user.email.startswith("wd-")
    assert user.email.endswith("@deleted.local")
    assert user.name == "withdrawn"
    assert user.phone_number is None
    assert user.gender is None
    assert user.birthday is None
    assert user.withdrawal_requested_at is not None
    assert user.withdrawn_at is not None
    assert user.token_version == 1
    assert await user_owned_ocr_row_count(db_session, user_id=original_user_id) == 0
    assert await user_owned_prescription_row_count(db_session, user_id=original_user_id) == 0
    assert await user_owned_identification_row_count(db_session, user_id=original_user_id) == 0
    assert await user_push_subscription_count(db_session, user_id=original_user_id) == 0
    assert await user_owned_ocr_row_count(db_session, user_id=other_user_id) == 3
    assert await user_owned_prescription_row_count(db_session, user_id=other_user_id) == 3

    request = await db_session.scalar(select(AccountDeletionRequest).where(AccountDeletionRequest.user_id == user.id))
    assert request is not None
    assert request.status == AccountDeletionRequestStatus.COMPLETED
    assert request.requested_at is not None
    assert request.started_at is not None
    assert request.completed_at is not None
    assert request.failed_at is None
    assert request.last_error_code is None

    assert user_me_response.status_code == status.HTTP_401_UNAUTHORIZED
    assert user_me_response.json()["code"] == "INVALID_TOKEN"
    assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED
    assert refresh_response.json()["code"] == "INVALID_TOKEN"
    assert resignup_response.status_code == status.HTTP_201_CREATED


async def test_account_withdrawal_rejects_wrong_password_without_side_effect(
    db_session: AsyncSession, enable_account_withdrawal_request
):
    email = f"withdrawal-wrong-{uuid4().hex[:10]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, _ = await signup_and_login(client, email=email)
        response = await client.post(
            "/api/v1/auth/account/withdrawal",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"password": "WrongPassword123!", "confirmed": True},
        )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["code"] == "UNAUTHORIZED"
    user = await user_by_email(db_session, email)
    assert user.account_status == AccountStatus.ACTIVE
    assert user.is_active is True
    assert user.withdrawal_requested_at is None
    assert user.token_version == 0
    assert await deletion_request_count(db_session, user.id) == 0


async def test_account_withdrawal_requires_confirmation_without_side_effect(
    db_session: AsyncSession, enable_account_withdrawal_request
):
    email = f"wd-confirm-{uuid4().hex[:10]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, _ = await signup_and_login(client, email=email)
        response = await client.post(
            "/api/v1/auth/account/withdrawal",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"password": PASSWORD, "confirmed": False},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert body["details"] == [{"field": "confirmed", "reason": "CONFIRMATION_REQUIRED", "rejected_value": None}]
    user = await user_by_email(db_session, email)
    assert user.account_status == AccountStatus.ACTIVE
    assert user.is_active is True
    assert user.withdrawal_requested_at is None
    assert user.token_version == 0
    assert await deletion_request_count(db_session, user.id) == 0


async def test_account_withdrawal_rejects_user_id_in_body_without_side_effect(
    db_session: AsyncSession, enable_account_withdrawal_request
):
    email = f"withdrawal-extra-{uuid4().hex[:10]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, _ = await signup_and_login(client, email=email)
        response = await client.post(
            "/api/v1/auth/account/withdrawal",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"password": PASSWORD, "confirmed": True, "user_id": str(uuid4())},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    user = await user_by_email(db_session, email)
    assert user.account_status == AccountStatus.ACTIVE
    assert user.is_active is True
    assert await deletion_request_count(db_session, user.id) == 0
