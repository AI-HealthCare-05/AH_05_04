from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_consents import ConsentPurpose, ConsentStatus, UserConsent


class UserConsentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_current(self, *, user_id: UUID, purpose: ConsentPurpose) -> UserConsent | None:
        return await self.session.scalar(
            select(UserConsent).where(
                UserConsent.user_id == user_id,
                UserConsent.purpose == purpose,
            )
        )

    async def is_granted(
        self,
        *,
        user_id: UUID,
        purpose: ConsentPurpose,
        policy_version: str,
    ) -> bool:
        row = await self.get_current(user_id=user_id, purpose=purpose)
        return (
            row is not None
            and row.status == ConsentStatus.GRANTED
            and row.policy_version == policy_version
            and row.granted_at is not None
            and row.withdrawn_at is None
        )

    async def set_status(
        self,
        *,
        user_id: UUID,
        purpose: ConsentPurpose,
        status: ConsentStatus,
        policy_version: str,
        changed_at: datetime,
    ) -> UserConsent:
        values = {
            "user_id": user_id,
            "purpose": purpose,
            "status": status,
            "policy_version": policy_version,
            "granted_at": changed_at if status == ConsentStatus.GRANTED else None,
            "withdrawn_at": changed_at if status == ConsentStatus.WITHDRAWN else None,
            "updated_at": changed_at,
        }
        row_id = await self.session.scalar(
            insert(UserConsent)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_user_consent_user_purpose",
                set_={
                    "status": status,
                    "policy_version": policy_version,
                    "granted_at": values["granted_at"],
                    "withdrawn_at": values["withdrawn_at"],
                    "updated_at": changed_at,
                },
            )
            .returning(UserConsent.id)
        )
        assert row_id is not None
        row = await self.session.scalar(
            select(UserConsent).where(UserConsent.id == row_id).execution_options(populate_existing=True)
        )
        assert row is not None
        return row
