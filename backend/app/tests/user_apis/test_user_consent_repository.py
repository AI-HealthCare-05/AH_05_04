from datetime import datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.user_consents import ConsentPurpose, ConsentStatus
from app.repositories.user_consent_repository import UserConsentRepository
from app.repositories.user_repository import UserRepository


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
