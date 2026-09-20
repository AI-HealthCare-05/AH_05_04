from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.guides import Guide, GuideCitation, GuideGenerationStatus
from app.models.prescriptions import Prescription, PrescriptionVersion
from app.repositories.prescription_integrity import require_verified_version
from app.repositories.profile_ownership import owned_by_self
from rag_runtime.guide_release_projection import (
    GuideRuntimeApprovedAnswer,
    GuideRuntimeReleaseProjectionCarrier,
)


def _render_approved_answer(answer: GuideRuntimeApprovedAnswer) -> str:
    return "\n\n".join((*answer.claim_action_texts, answer.uncertainty_text, answer.consultation_text))


class GuideRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_prescription_owned(self, *, prescription_id: UUID, user_id: UUID) -> Prescription | None:
        result = await self.session.execute(
            select(Prescription)
            .options(
                selectinload(Prescription.document),
                selectinload(Prescription.active_version).selectinload(PrescriptionVersion.medications),
            )
            .where(
                Prescription.id == prescription_id,
                owned_by_self(Prescription.profile_id, user_id),
            )
        )
        prescription = result.scalar_one_or_none()
        if prescription is not None:
            await require_verified_version(self.session, prescription.active_version_id)
        return prescription

    async def create(self, *, prescription: Prescription) -> Guide:
        guide = Guide(
            prescription_id=prescription.id,
            prescription_version_id=prescription.active_version_id,
            profile_id=prescription.profile_id,
            generation_status=GuideGenerationStatus.GENERATING,
        )
        self.session.add(guide)
        await self.session.flush()
        return guide

    async def create_async_placeholder(self, *, prescription: Prescription, ai_job_id: UUID) -> Guide:
        guide = Guide(
            prescription_id=prescription.id,
            prescription_version_id=prescription.active_version_id,
            profile_id=prescription.profile_id,
            ai_job_id=ai_job_id,
            generation_status=GuideGenerationStatus.PENDING,
        )
        self.session.add(guide)
        await self.session.flush()
        return guide

    async def get_owned(self, *, guide_id: UUID, user_id: UUID) -> Guide | None:
        result = await self.session.execute(
            select(Guide)
            .options(
                selectinload(Guide.prescription).selectinload(Prescription.document),
                selectinload(Guide.citations),
            )
            .where(
                Guide.id == guide_id,
                owned_by_self(Guide.profile_id, user_id),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_ai_job_id(self, *, ai_job_id: UUID) -> Guide | None:
        """`guide.ai_job_id`(unique) 영속 매핑으로 조회합니다. Outbox 기반 임시 조회
        (`AsyncJobRepository.get_interim_domain_reference`)와 달리 Outbox 30일 보존과
        무관하게 Job 90일 보존 동안 유지됩니다 — rediscovery·`GET /jobs/{job_id}`가 이 값이
        채워진 뒤에는 이 경로를 우선 사용해야 합니다(OCR의 #212와 같은 목적)."""
        result = await self.session.execute(
            select(Guide).options(selectinload(Guide.citations)).where(Guide.ai_job_id == ai_job_id)
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
            .join(Prescription, Prescription.id == Guide.prescription_id)
            .options(
                selectinload(Guide.prescription).selectinload(Prescription.document),
                selectinload(Guide.citations),
            )
            .where(
                Guide.prescription_id == prescription_id,
                Guide.prescription_version_id == Prescription.active_version_id,
                owned_by_self(Guide.profile_id, user_id),
            )
            .order_by(Guide.requested_at.desc(), Guide.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def lock_if_current_version(self, *, guide: Guide) -> bool:
        current = await self.session.scalar(
            select(Prescription.id)
            .where(
                Prescription.id == guide.prescription_id,
                Prescription.active_version_id == guide.prescription_version_id,
            )
            .with_for_update(of=Prescription)
        )
        return current is not None

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

    async def mark_release_completed(
        self,
        guide: Guide,
        *,
        projection: GuideRuntimeReleaseProjectionCarrier,
        model_name: str,
        prompt_version: str,
        completed_at: datetime,
    ) -> Guide:
        guide.generation_status = GuideGenerationStatus.COMPLETED
        guide.content = _render_approved_answer(projection.answer) if projection.answer is not None else None
        guide.model_name = model_name
        guide.prompt_version = prompt_version
        guide.completed_at = completed_at
        guide.error_code = None
        guide.error_message = None
        guide.release_projection_version = projection.contract_version
        guide.release_decision = projection.release_decision.value
        guide.release_is_current = projection.is_current
        guide.answer_claim_action_texts = (
            list(projection.answer.claim_action_texts) if projection.answer is not None else None
        )
        guide.answer_uncertainty_text = projection.answer.uncertainty_text if projection.answer is not None else None
        guide.answer_consultation_text = projection.answer.consultation_text if projection.answer is not None else None
        guide.fallback_code = projection.fallback.code.value if projection.fallback is not None else None
        guide.fallback_text = projection.fallback.text if projection.fallback is not None else None

        await self.session.execute(delete(GuideCitation).where(GuideCitation.guide_id == guide.id))
        self.session.add_all(
            [
                GuideCitation(
                    guide_id=guide.id,
                    card_target_ref=citation.card_target_ref,
                    claim_key=citation.claim_key,
                    evidence_key=citation.evidence_key,
                    source_type=citation.source_type.value,
                    source_snapshot_id=citation.source_snapshot_id,
                    source_snapshot_member_id=citation.source_snapshot_member_id,
                    source_code=citation.source_code,
                    source_version=citation.source_version,
                    locator=citation.locator,
                    content_sha256=citation.content_sha256,
                    display_order=citation.display_order,
                )
                for citation in projection.citations
            ]
        )
        await self.session.flush()
        await self.session.refresh(guide, attribute_names=["citations"])
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
