from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.guides import Guide, GuideGenerationStatus
from app.models.prescriptions import Prescription
from app.repositories.profile_ownership import owned_by_self


class GuideRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_prescription_owned(self, *, prescription_id: UUID, user_id: UUID) -> Prescription | None:
        result = await self.session.execute(
            select(Prescription)
            .options(selectinload(Prescription.medications), selectinload(Prescription.document))
            .where(
                Prescription.id == prescription_id,
                owned_by_self(Prescription.profile_id, user_id),
            )
        )
        return result.scalar_one_or_none()

    async def create(self, *, prescription: Prescription) -> Guide:
        guide = Guide(
            prescription_id=prescription.id,
            profile_id=prescription.profile_id,
            generation_status=GuideGenerationStatus.GENERATING,
        )
        self.session.add(guide)
        await self.session.flush()
        return guide

    async def get_owned(self, *, guide_id: UUID, user_id: UUID) -> Guide | None:
        result = await self.session.execute(
            select(Guide)
            .options(selectinload(Guide.prescription).selectinload(Prescription.document))
            .where(
                Guide.id == guide_id,
                owned_by_self(Guide.profile_id, user_id),
            )
        )
        return result.scalar_one_or_none()

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
        # 이후 서비스 계층에서 ApiError를 다시 발생시키면 get_db_session의 예외 처리가
        # 세션 전체를 rollback합니다. flush만으로는 실패 상태가 사라지므로 즉시 commit합니다.
        await self.session.commit()
        return guide
