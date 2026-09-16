from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_deletion_request import AccountDeletionRequest, AccountDeletionRequestStatus


class AccountDeletionRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_pending(self, *, user_id: UUID, requested_at: datetime) -> AccountDeletionRequest:
        request = AccountDeletionRequest(
            user_id=user_id,
            status=AccountDeletionRequestStatus.PENDING,
            requested_at=requested_at,
        )
        self.session.add(request)
        await self.session.flush()
        return request
