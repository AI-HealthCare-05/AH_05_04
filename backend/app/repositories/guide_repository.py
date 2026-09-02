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

    async def get_latest_for_prescription_owned(self, *, prescription_id: UUID, user_id: UUID) -> Guide | None:
        """async-job-v1.md "공통 화면 재접속 복구": 화면 재진입 시 새 Job을 만들지 않고 기존 Job의
        polling을 재개하기 위해, 이 처방의 가장 최근 Guide 하나만 돌려줍니다(`idx_guide_prescription_requested`
        활용). `Guide`에는 `ocr_job.created_sequence` 같은 단조 증가 컬럼이 없어 `id`(무작위 UUID)로
        타이브레이크합니다 — 같은 transaction 안에서 같은 `prescription_id`에 Guide가 두 번 이상
        생성되는 경우(현재 실사용 경로에서는 발생하지 않음)에만 이론상 모호할 수 있습니다."""
        result = await self.session.execute(
            select(Guide)
            .where(
                Guide.prescription_id == prescription_id,
                owned_by_self(Guide.profile_id, user_id),
            )
            .order_by(Guide.requested_at.desc(), Guide.id.desc())
            .limit(1)
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
