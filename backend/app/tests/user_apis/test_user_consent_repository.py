from datetime import datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.errors import ApiError
from app.main import app
from app.models.user_consents import ConsentPurpose, ConsentStatus
from app.repositories.user_consent_repository import UserConsentRepository
from app.repositories.user_repository import UserRepository
from app.services.user_consents import ConsentGateService, OcrConsentService


async def _create_user(session: AsyncSession):
    repository = UserRepository(session)
    return await repository.create_user(
        email=f"consent-{uuid4().hex[:10]}@example.com",
        hashed_password="synthetic-password-hash",
        name="동의테스터",
    )


async def test_user_consent_repository_treats_missing_row_as_not_granted(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    repository = UserConsentRepository(db_session)

    assert not await repository.is_granted(
        user_id=user.id,
        purpose=ConsentPurpose.OCR,
        policy_version="ocr-consent.v1",
    )


async def test_user_consent_repository_upserts_current_status_per_purpose(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    repository = UserConsentRepository(db_session)
    now = datetime.now(config.TIMEZONE)

    granted = await repository.set_status(
        user_id=user.id,
        purpose=ConsentPurpose.GUIDE,
        status=ConsentStatus.GRANTED,
        policy_version="guide-consent.v1",
        changed_at=now,
    )

    assert granted.status == ConsentStatus.GRANTED
    assert granted.granted_at == now
    assert granted.withdrawn_at is None
    assert await repository.is_granted(
        user_id=user.id,
        purpose=ConsentPurpose.GUIDE,
        policy_version="guide-consent.v1",
    )
    assert not await repository.is_granted(
        user_id=user.id,
        purpose=ConsentPurpose.GUIDE,
        policy_version="guide-consent.v2",
    )

    withdrawn_at = datetime.now(config.TIMEZONE)
    withdrawn = await repository.set_status(
        user_id=user.id,
        purpose=ConsentPurpose.GUIDE,
        status=ConsentStatus.WITHDRAWN,
        policy_version="guide-consent.v1",
        changed_at=withdrawn_at,
    )

    assert withdrawn.id == granted.id
    assert withdrawn.status == ConsentStatus.WITHDRAWN
    assert withdrawn.granted_at is None
    assert withdrawn.withdrawn_at == withdrawn_at
    assert not await repository.is_granted(
        user_id=user.id,
        purpose=ConsentPurpose.GUIDE,
        policy_version="guide-consent.v1",
    )


async def test_user_consent_repository_keeps_purposes_independent(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    repository = UserConsentRepository(db_session)
    now = datetime.now(config.TIMEZONE)

    await repository.set_status(
        user_id=user.id,
        purpose=ConsentPurpose.OCR,
        status=ConsentStatus.GRANTED,
        policy_version="ocr-consent.v1",
        changed_at=now,
    )

    assert await repository.is_granted(
        user_id=user.id,
        purpose=ConsentPurpose.OCR,
        policy_version="ocr-consent.v1",
    )
    assert not await repository.is_granted(
        user_id=user.id,
        purpose=ConsentPurpose.CHAT,
        policy_version="chat-consent.v1",
    )


async def test_user_consent_repository_does_not_use_other_users_consent(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session)
    requester = await _create_user(db_session)
    repository = UserConsentRepository(db_session)

    await repository.set_status(
        user_id=owner.id,
        purpose=ConsentPurpose.OCR,
        status=ConsentStatus.GRANTED,
        policy_version="ocr-consent.v1",
        changed_at=datetime.now(config.TIMEZONE),
    )

    assert await repository.is_granted(
        user_id=owner.id,
        purpose=ConsentPurpose.OCR,
        policy_version="ocr-consent.v1",
    )
    assert not await repository.is_granted(
        user_id=requester.id,
        purpose=ConsentPurpose.OCR,
        policy_version="ocr-consent.v1",
    )


@pytest.mark.parametrize("purpose", list(ConsentPurpose))
async def test_user_consent_repository_grants_each_supported_purpose(
    db_session: AsyncSession,
    purpose: ConsentPurpose,
) -> None:
    user = await _create_user(db_session)
    repository = UserConsentRepository(db_session)
    policy_version = f"{purpose.value.lower()}-consent.v1"

    await repository.set_status(
        user_id=user.id,
        purpose=purpose,
        status=ConsentStatus.GRANTED,
        policy_version=policy_version,
        changed_at=datetime.now(config.TIMEZONE),
    )

    assert await repository.is_granted(
        user_id=user.id,
        purpose=purpose,
        policy_version=policy_version,
    )


@pytest.mark.parametrize("status", list(ConsentStatus))
async def test_user_consent_repository_only_granted_status_allows(
    db_session: AsyncSession,
    status: ConsentStatus,
) -> None:
    user = await _create_user(db_session)
    repository = UserConsentRepository(db_session)

    await repository.set_status(
        user_id=user.id,
        purpose=ConsentPurpose.CHAT,
        status=status,
        policy_version="chat-consent.v1",
        changed_at=datetime.now(config.TIMEZONE),
    )

    assert await repository.is_granted(
        user_id=user.id,
        purpose=ConsentPurpose.CHAT,
        policy_version="chat-consent.v1",
    ) is (status == ConsentStatus.GRANTED)


async def test_consent_gate_requires_exact_guide_purpose(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    repository = UserConsentRepository(db_session)
    gate = ConsentGateService(repository)

    await repository.set_status(
        user_id=user.id,
        purpose=ConsentPurpose.OCR,
        status=ConsentStatus.GRANTED,
        policy_version="ocr-consent.v1",
        changed_at=datetime.now(config.TIMEZONE),
    )

    with pytest.raises(ApiError) as missing:
        await gate.require_for_intake(user=user, purpose=ConsentPurpose.GUIDE)
    assert missing.value.code == "CONSENT_REQUIRED"

    await repository.set_status(
        user_id=user.id,
        purpose=ConsentPurpose.GUIDE,
        status=ConsentStatus.GRANTED,
        policy_version="guide-consent.v1",
        changed_at=datetime.now(config.TIMEZONE),
    )

    await gate.require_for_intake(user=user, purpose=ConsentPurpose.GUIDE)


async def test_consent_gate_rejects_withdrawn_guide_consent(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    repository = UserConsentRepository(db_session)
    await repository.set_status(
        user_id=user.id,
        purpose=ConsentPurpose.GUIDE,
        status=ConsentStatus.WITHDRAWN,
        policy_version="guide-consent.v1",
        changed_at=datetime.now(config.TIMEZONE),
    )

    with pytest.raises(ApiError) as withdrawn:
        await ConsentGateService(repository).require_for_intake(user=user, purpose=ConsentPurpose.GUIDE)
    assert withdrawn.value.code == "CONSENT_REQUIRED"


async def test_ocr_consent_service_grant_withdraw_and_version_gate(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    service = OcrConsentService(UserConsentRepository(db_session), current_policy_version="ocr-consent.v2")

    assert (await service.get_state(user=user)).reason == "MISSING_CONSENT"
    with pytest.raises(ApiError) as missing:
        await service.require_for_intake(user=user)
    assert missing.value.code == "CONSENT_REQUIRED"

    with pytest.raises(ApiError) as mismatch:
        await service.grant(user=user, policy_version="ocr-consent.v1")
    assert mismatch.value.code == "CONSENT_POLICY_MISMATCH"

    granted = await service.grant(user=user, policy_version="ocr-consent.v2")
    assert granted.effective
    await service.require_for_intake(user=user)

    withdrawn = await service.withdraw(user=user)
    assert withdrawn.status == "WITHDRAWN"
    assert withdrawn.reason == "WITHDRAWN"
    assert withdrawn.accepted_policy_version == "ocr-consent.v2"
    assert not withdrawn.effective

    await service.grant(user=user, policy_version="ocr-consent.v2")
    changed_version = OcrConsentService(UserConsentRepository(db_session), current_policy_version="ocr-consent.v3")
    assert (await changed_version.get_state(user=user)).reason == "POLICY_VERSION_MISMATCH"


async def test_ocr_consent_service_unconfigured_policy_fails_closed(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    service = OcrConsentService(UserConsentRepository(db_session), current_policy_version="")
    with pytest.raises(ApiError) as unavailable:
        await service.require_for_intake(user=user)
    assert unavailable.value.code == "CONSENT_POLICY_UNAVAILABLE"


async def test_ocr_consent_withdrawal_still_works_without_current_policy_version(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    repository = UserConsentRepository(db_session)
    await OcrConsentService(repository, current_policy_version="ocr-consent.v2").grant(
        user=user, policy_version="ocr-consent.v2"
    )

    withdrawn = await OcrConsentService(repository, current_policy_version="").withdraw(user=user)

    assert withdrawn.status == "WITHDRAWN"
    assert withdrawn.reason == "WITHDRAWN"
    assert withdrawn.accepted_policy_version == "ocr-consent.v2"
    assert withdrawn.current_policy_version == ""
    assert not withdrawn.effective


async def test_ocr_consent_api_reports_distinct_states_and_refuses_old_version(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = db_session
    monkeypatch.setattr(config, "OCR_CONSENT_POLICY_VERSION", "ocr-consent.v2")
    email = f"ocr-api-{uuid4().hex[:10]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        signup = await client.post(
            "/api/v1/auth/signup", json={"email": email, "password": "Password123!", "name": "동의테스터"}
        )
        assert signup.status_code in (200, 201)
        login = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        missing = await client.get("/api/v1/users/me/consents/OCR", headers=headers)
        assert missing.status_code == 200
        assert missing.json()["data"]["reason"] == "MISSING_CONSENT"

        old = await client.post(
            "/api/v1/users/me/consents/OCR", headers=headers, json={"policy_version": "ocr-consent.v1"}
        )
        assert old.status_code == 409
        assert old.json()["code"] == "CONSENT_POLICY_MISMATCH"

        granted = await client.post(
            "/api/v1/users/me/consents/OCR", headers=headers, json={"policy_version": "ocr-consent.v2"}
        )
        assert granted.status_code == 200
        assert granted.json()["data"]["effective"] is True

        withdrawn = await client.delete("/api/v1/users/me/consents/OCR", headers=headers)
        assert withdrawn.status_code == 200
        assert withdrawn.json()["data"]["reason"] == "WITHDRAWN"
        assert withdrawn.headers["cache-control"] == "no-store"
