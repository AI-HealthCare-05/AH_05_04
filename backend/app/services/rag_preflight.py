from uuid import UUID

from app.services.medication_identification import (
    MedicationIdentificationGuidePreflightResult as RagPreflightResult,
)
from app.services.medication_identification import (
    MedicationIdentificationService,
)


class RagPreflightService:
    """Guide 접수 전 약품 식별 완료 여부를 검증하는 얇은 facade입니다.

    Chat은 Preflight 실패를 같은 Job의 제한 응답으로 저장해야 하므로, 이 facade는
    자동 Guide 접수처럼 HTTP 409로 중단하는 경로에서만 사용합니다.
    """

    def __init__(self, identification_service: MedicationIdentificationService) -> None:
        self._identification_service = identification_service

    async def ensure_all_active_medications_matched(
        self,
        *,
        prescription_id: UUID,
        user_id: UUID,
        expected_prescription_version_id: UUID,
    ) -> RagPreflightResult:
        return await self._identification_service.ensure_owned_active_matched_for_guide_preflight(
            prescription_id=prescription_id,
            user_id=user_id,
            expected_prescription_version_id=expected_prescription_version_id,
        )
