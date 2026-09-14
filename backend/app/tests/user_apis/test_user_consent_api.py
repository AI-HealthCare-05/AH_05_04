from datetime import datetime
from typing import cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.main import app
from app.models.user_consents import ConsentPurpose, ConsentStatus, UserConsent
from app.services.user_consent_policy import CURRENT_CONSENT_POLICY_VERSIONS
from app.services.users import _is_currently_granted


async def _signup_and_login(client: AsyncClient, *, email: str) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "Password123!", "name": "동의API"},
    )
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
    )
    return {"Authorization": f"Bearer {login_response.json()['access_token']}"}


def _email(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}@example.com"


@pytest.mark.parametrize(
    ("stored_status", "expected"),
    [
        (ConsentStatus.GRANTED, True),
        (ConsentStatus.WITHDRAWN, False),
        ("PENDING", False),
    ],
)
def test_is_currently_granted_allows_only_current_granted_status(
    stored_status: ConsentStatus | str,
    expected: bool,
) -> None:
    changed_at = datetime.now()
    row = UserConsent(
        user_id=uuid4(),
        purpose=ConsentPurpose.OCR,
        status=cast(ConsentStatus, stored_status),
        policy_version="ocr-consent.v1",
        granted_at=changed_at if stored_status == ConsentStatus.GRANTED else None,
        withdrawn_at=changed_at if stored_status == ConsentStatus.WITHDRAWN else None,
    )

    assert _is_currently_granted(row, "ocr-consent.v1") is expected


def test_is_currently_granted_rejects_stale_or_incomplete_grant() -> None:
    granted_at = datetime.now()
    stale = UserConsent(
        user_id=uuid4(),
        purpose=ConsentPurpose.OCR,
        status=ConsentStatus.GRANTED,
        policy_version="ocr-consent.v0",
        granted_at=granted_at,
        withdrawn_at=None,
    )
    incomplete = UserConsent(
        user_id=uuid4(),
        purpose=ConsentPurpose.OCR,
        status=ConsentStatus.GRANTED,
        policy_version="ocr-consent.v1",
        granted_at=None,
        withdrawn_at=None,
    )
    withdrawn_marker = UserConsent(
        user_id=uuid4(),
        purpose=ConsentPurpose.OCR,
        status=ConsentStatus.GRANTED,
        policy_version="ocr-consent.v1",
        granted_at=granted_at,
        withdrawn_at=granted_at,
    )

    assert not _is_currently_granted(stale, "ocr-consent.v1")
    assert not _is_currently_granted(incomplete, "ocr-consent.v1")
    assert not _is_currently_granted(withdrawn_marker, "ocr-consent.v1")


async def test_list_user_consents_returns_missing_rows_as_not_granted() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _signup_and_login(client, email=_email("missing-consent"))
        response = await client.get("/api/v1/users/me/consents", headers=headers)

    assert response.status_code == status.HTTP_200_OK
    assert response.headers.get_list("cache-control") == ["no-store"]
    data = response.json()["data"]
    assert [item["purpose"] for item in data] == [purpose.value for purpose in ConsentPurpose]
    assert all(item["status"] is None for item in data)
    assert all(item["policy_version"] is None for item in data)
    assert {item["purpose"]: item["current_policy_version"] for item in data} == {
        purpose.value: version for purpose, version in CURRENT_CONSENT_POLICY_VERSIONS.items()
    }
    assert all(item["is_granted"] is False for item in data)


async def test_update_user_consent_grants_and_lists_current_status() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _signup_and_login(client, email=_email("grant-consent"))
        grant_response = await client.put(
            "/api/v1/users/me/consents/OCR",
            json={"status": "GRANTED", "policy_version": "ocr-consent.v1"},
            headers=headers,
        )
        list_response = await client.get("/api/v1/users/me/consents", headers=headers)

    assert grant_response.status_code == status.HTTP_200_OK
    assert grant_response.headers.get_list("cache-control") == ["no-store"]
    granted = grant_response.json()["data"]
    assert granted["purpose"] == ConsentPurpose.OCR.value
    assert granted["status"] == ConsentStatus.GRANTED.value
    assert granted["policy_version"] == "ocr-consent.v1"
    assert granted["current_policy_version"] == "ocr-consent.v1"
    assert granted["is_granted"] is True
    assert granted["granted_at"] is not None
    assert granted["withdrawn_at"] is None

    ocr_item = next(item for item in list_response.json()["data"] if item["purpose"] == ConsentPurpose.OCR.value)
    assert ocr_item["status"] == ConsentStatus.GRANTED.value
    assert ocr_item["current_policy_version"] == "ocr-consent.v1"
    assert ocr_item["is_granted"] is True


async def test_list_user_consents_marks_stale_policy_version_as_not_granted(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _signup_and_login(client, email=_email("stale-consent"))
        await client.put(
            "/api/v1/users/me/consents/OCR",
            json={"status": "GRANTED", "policy_version": "ocr-consent.v1"},
            headers=headers,
        )
        await db_session.execute(
            update(UserConsent).where(UserConsent.purpose == ConsentPurpose.OCR).values(policy_version="ocr-consent.v0")
        )
        await db_session.flush()
        response = await client.get("/api/v1/users/me/consents", headers=headers)

    ocr_item = next(item for item in response.json()["data"] if item["purpose"] == ConsentPurpose.OCR.value)
    assert ocr_item["status"] == ConsentStatus.GRANTED.value
    assert ocr_item["policy_version"] == "ocr-consent.v0"
    assert ocr_item["current_policy_version"] == "ocr-consent.v1"
    assert ocr_item["is_granted"] is False


async def test_update_user_consent_regrants_same_purpose_without_changing_other_purposes(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _signup_and_login(client, email=_email("regrant-consent"))
        await client.put(
            "/api/v1/users/me/consents/GUIDE",
            json={"status": "GRANTED", "policy_version": "guide-consent.v1"},
            headers=headers,
        )
        await client.put(
            "/api/v1/users/me/consents/CHAT",
            json={"status": "GRANTED", "policy_version": "chat-consent.v1"},
            headers=headers,
        )
        withdrawn = await client.put(
            "/api/v1/users/me/consents/GUIDE",
            json={"status": "WITHDRAWN", "policy_version": "guide-consent.v1"},
            headers=headers,
        )
        regranted = await client.put(
            "/api/v1/users/me/consents/GUIDE",
            json={"status": "GRANTED", "policy_version": "guide-consent.v1"},
            headers=headers,
        )
        list_response = await client.get("/api/v1/users/me/consents", headers=headers)

    assert withdrawn.status_code == status.HTTP_200_OK
    assert withdrawn.json()["data"]["withdrawn_at"] is not None

    assert regranted.status_code == status.HTTP_200_OK
    data = regranted.json()["data"]
    assert data["purpose"] == ConsentPurpose.GUIDE.value
    assert data["status"] == ConsentStatus.GRANTED.value
    assert data["policy_version"] == "guide-consent.v1"
    assert data["is_granted"] is True
    assert data["granted_at"] is not None
    assert data["withdrawn_at"] is None

    guide_row_count = await db_session.scalar(
        select(func.count()).select_from(UserConsent).where(UserConsent.purpose == ConsentPurpose.GUIDE)
    )
    assert guide_row_count == 1

    items = {item["purpose"]: item for item in list_response.json()["data"]}
    assert items[ConsentPurpose.GUIDE.value]["is_granted"] is True
    assert items[ConsentPurpose.GUIDE.value]["withdrawn_at"] is None
    assert items[ConsentPurpose.CHAT.value]["is_granted"] is True
    assert items[ConsentPurpose.CHAT.value]["status"] == ConsentStatus.GRANTED.value


async def test_update_user_consent_withdraws_current_status(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _signup_and_login(client, email=_email("withdraw-consent"))
        await client.put(
            "/api/v1/users/me/consents/GUIDE",
            json={"status": "GRANTED", "policy_version": "guide-consent.v1"},
            headers=headers,
        )
        response = await client.put(
            "/api/v1/users/me/consents/GUIDE",
            json={"status": "WITHDRAWN", "policy_version": "guide-consent.v1"},
            headers=headers,
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    assert data["purpose"] == ConsentPurpose.GUIDE.value
    assert data["status"] == ConsentStatus.WITHDRAWN.value
    assert data["current_policy_version"] == "guide-consent.v1"
    assert data["is_granted"] is False
    assert data["granted_at"] is None
    assert data["withdrawn_at"] is not None

    row_count = await db_session.scalar(
        select(func.count()).select_from(UserConsent).where(UserConsent.purpose == ConsentPurpose.GUIDE)
    )
    assert row_count == 1


async def test_user_consents_do_not_expose_other_users_rows() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        owner_headers = await _signup_and_login(client, email=_email("owner-consent"))
        requester_headers = await _signup_and_login(client, email=_email("requester-consent"))
        await client.put(
            "/api/v1/users/me/consents/CHAT",
            json={"status": "GRANTED", "policy_version": "chat-consent.v1"},
            headers=owner_headers,
        )
        response = await client.get("/api/v1/users/me/consents", headers=requester_headers)

    chat_item = next(item for item in response.json()["data"] if item["purpose"] == ConsentPurpose.CHAT.value)
    assert chat_item["status"] is None
    assert chat_item["policy_version"] is None
    assert chat_item["current_policy_version"] == "chat-consent.v1"
    assert chat_item["is_granted"] is False


async def test_user_consents_require_authentication() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/users/me/consents")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.headers.get_list("cache-control") == ["no-store"]


async def test_update_user_consent_rejects_unsupported_purpose_and_invalid_body() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _signup_and_login(client, email=_email("invalid-consent"))
        unsupported_purpose = await client.put(
            "/api/v1/users/me/consents/LOCATION",
            json={"status": "GRANTED", "policy_version": "location-consent.v1"},
            headers=headers,
        )
        invalid_status = await client.put(
            "/api/v1/users/me/consents/OCR",
            json={"status": "PENDING", "policy_version": "ocr-consent.v1"},
            headers=headers,
        )
        empty_policy = await client.put(
            "/api/v1/users/me/consents/OCR",
            json={"status": "GRANTED", "policy_version": ""},
            headers=headers,
        )

    assert unsupported_purpose.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert invalid_status.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert empty_policy.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_update_user_consent_rejects_non_current_policy_versions() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _signup_and_login(client, email=_email("policy-mismatch"))
        stale_policy = await client.put(
            "/api/v1/users/me/consents/OCR",
            json={"status": "GRANTED", "policy_version": "ocr-consent.v0"},
            headers=headers,
        )
        other_purpose_policy = await client.put(
            "/api/v1/users/me/consents/OCR",
            json={"status": "GRANTED", "policy_version": "chat-consent.v1"},
            headers=headers,
        )
        arbitrary_policy = await client.put(
            "/api/v1/users/me/consents/OCR",
            json={"status": "GRANTED", "policy_version": "synthetic-consent.v1"},
            headers=headers,
        )
        withdraw_wrong_policy = await client.put(
            "/api/v1/users/me/consents/GUIDE",
            json={"status": "WITHDRAWN", "policy_version": "guide-consent.v0"},
            headers=headers,
        )

    for response in (stale_policy, other_purpose_policy, arbitrary_policy, withdraw_wrong_policy):
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert response.json()["code"] == "VALIDATION_FAILED"
        assert response.json()["details"] == [
            {"field": "policy_version", "reason": "POLICY_VERSION_MISMATCH", "rejected_value": None}
        ]
