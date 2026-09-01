from uuid import UUID

from sqlalchemy import ColumnElement, Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models.profiles import Profile, ProfileType


def self_profile_id_subquery(user_id: UUID) -> Select[tuple[UUID]]:
    return select(Profile.id).where(
        Profile.user_id == user_id,
        Profile.profile_type == ProfileType.SELF,
    )


async def get_self_profile_id(session: AsyncSession, *, user_id: UUID) -> UUID | None:
    return await session.scalar(self_profile_id_subquery(user_id))


def owned_by_self(
    profile_id_column: ColumnElement[UUID] | InstrumentedAttribute[UUID],
    user_id: UUID,
) -> ColumnElement[bool]:
    """모든 도메인 리소스가 공유하는 소유권 조건: 컬럼의 profile_id가 user_id의 SELF profile과 같아야 합니다."""
    return profile_id_column == self_profile_id_subquery(user_id).scalar_subquery()
