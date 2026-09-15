from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lifestyle_times import LifestyleTimes
from app.models.profiles import Profile, ProfileType


class LifestyleTimesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_self_profile_id(self, *, user_id: UUID) -> UUID | None:
        return await self.session.scalar(
            select(Profile.id).where(
                Profile.user_id == user_id,
                Profile.profile_type == ProfileType.SELF,
            )
        )

    async def lock_self_profile(self, *, user_id: UUID, profile_id: UUID) -> bool:
        locked_id = await self.session.scalar(
            select(Profile.id)
            .where(
                Profile.id == profile_id,
                Profile.user_id == user_id,
                Profile.profile_type == ProfileType.SELF,
            )
            .with_for_update(of=Profile)
        )
        return locked_id is not None

    async def get_current(self, *, profile_id: UUID) -> LifestyleTimes | None:
        return await self.session.get(LifestyleTimes, profile_id)

    async def create(self, *, profile_id: UUID, days: list[dict[str, Any]]) -> LifestyleTimes:
        lifestyle_times = LifestyleTimes(profile_id=profile_id, revision=1, days=days)
        self.session.add(lifestyle_times)
        await self.session.flush()
        await self.session.refresh(lifestyle_times)
        return lifestyle_times

    async def replace(self, *, current: LifestyleTimes, days: list[dict[str, Any]]) -> LifestyleTimes:
        current.revision += 1
        current.days = days
        await self.session.flush()
        await self.session.refresh(current)
        return current
