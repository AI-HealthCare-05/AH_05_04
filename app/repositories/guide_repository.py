from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.guides import Guide, GuideGenerationStatus
from app.models.prescriptions import Prescription


class GuideRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_prescription_with_medications(self, *, prescription_id: UUID) -> Prescription | None:
        result = await self.session.execute(
            select(Prescription)
            .options(selectinload(Prescription.medications))
            .where(Prescription.id == prescription_id)
        )
        return result.scalar_one_or_none()

    async def create(self, *, prescription_id: UUID) -> Guide:
        guide = Guide(prescription_id=prescription_id, generation_status=GuideGenerationStatus.GENERATING)
        self.session.add(guide)
        await self.session.flush()
        return guide

    async def get(self, *, guide_id: UUID) -> Guide | None:
        return await self.session.get(Guide, guide_id)

    async def mark_completed(
        self,
        guide: Guide,
        *,
        content: str,
        model_name: str,
        prompt_version: str,
        completed_at: datetime,
    ) -> Guide:
        guide.generation_status = GuideGenerationStatus.COMPLETED
        guide.content = content
        guide.model_name = model_name
        guide.prompt_version = prompt_version
        guide.completed_at = completed_at
        await self.session.flush()
        return guide

    async def mark_failed(
        self,
        guide: Guide,
        *,
        error_code: str,
        error_message: str,
        completed_at: datetime,
    ) -> Guide:
        guide.generation_status = GuideGenerationStatus.FAILED
        guide.error_code = error_code
        guide.error_message = error_message[:500]
        guide.completed_at = completed_at
        await self.session.flush()
        return guide
