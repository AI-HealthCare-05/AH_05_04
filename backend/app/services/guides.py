from datetime import UTC, datetime
from uuid import UUID

from app.core.errors import ApiError, ErrorDetail
from app.dtos.guides import CreateGuideRequest, GuideData, GuideStatus
from app.models.guides import Guide
from app.models.users import User
from app.repositories.guide_repository import GuideRepository
from app.services.guide_ai import GuideGenerationInput, GuideGenerator, MedicationInput
from app.services.guide_ai.exceptions import GuideGenerationTimeoutError, GuideGenerationUnavailableError

# OpenAI SDK/도메인 예외 메시지를 그대로 저장하면 요청 payload(약물 정보 등)가 노출될 수 있어
# 고정된 문구만 DB에 저장합니다.
_TIMEOUT_ERROR_MESSAGE = "OpenAI 호출이 제한 시간 내에 완료되지 않았습니다."
_UNAVAILABLE_ERROR_MESSAGE = "OpenAI 서비스 호출에 실패했습니다."
_GENERATION_FAILED_ERROR_MESSAGE = "가이드 생성 처리 중 오류가 발생했습니다."


def _to_guide_data(guide: Guide) -> GuideData:
    return GuideData(
        guide_id=guide.id,
        prescription_id=guide.prescription_id,
        generation_status=GuideStatus(guide.generation_status),
        content=guide.content,
        model_name=guide.model_name,
        prompt_version=guide.prompt_version,
        requested_at=guide.requested_at,
        completed_at=guide.completed_at,
    )


class GuideService:
    def __init__(
        self,
        repository: GuideRepository,
        generator: GuideGenerator,
    ) -> None:
        self._repo = repository
        self._generator = generator

    async def create_guide(
        self,
        *,
        user: User,
        request: CreateGuideRequest,
    ) -> GuideData:
        # 복약 가이드 생성 Backend 계약(one-cycle, 동기):
        # 확정 처방과 소속 약물을 조회해 같은 요청 안에서 OpenAI 가이드 생성을 완료하고 GUIDE에 저장합니다.
        prescription = await self._repo.get_prescription_owned(
            prescription_id=request.prescription_id,
            user_id=user.id,
        )
        if prescription is None:
            raise ApiError(
                status_code=404,
                code="PRESCRIPTION_NOT_FOUND",
                message="처방 정보를 찾을 수 없습니다.",
                details=[
                    ErrorDetail(
                        field="prescription_id",
                        reason="NOT_FOUND",
                        rejected_value=str(request.prescription_id),
                    )
                ],
            )

        guide = await self._repo.create(prescription_id=prescription.id)

        try:
            generation_input = GuideGenerationInput(
                medications=[
                    MedicationInput(
                        medication_name=medication.medication_name,
                        dose_value=medication.dose_value,
                        dose_unit=medication.dose_unit,
                        frequency_per_day=medication.frequency_per_day,
                        timing_text=medication.timing_text,
                        duration_days=medication.duration_days,
                    )
                    for medication in prescription.medications
                ]
            )
            result = await self._generator.generate(generation_input)
        except GuideGenerationTimeoutError as err:
            await self._repo.mark_failed(
                guide,
                error_code="OPENAI_API_TIMEOUT",
                error_message=_TIMEOUT_ERROR_MESSAGE,
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=504,
                code="GATEWAY_TIMEOUT",
                message="외부 처리 시간이 초과되었습니다. 다시 시도해 주세요.",
                details=[ErrorDetail(field="openai_api", reason="OPENAI_API_TIMEOUT")],
            ) from err
        except GuideGenerationUnavailableError as err:
            await self._repo.mark_failed(
                guide,
                error_code="OPENAI_API_ERROR",
                error_message=_UNAVAILABLE_ERROR_MESSAGE,
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=503,
                code="SERVICE_UNAVAILABLE",
                message="현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                details=[ErrorDetail(field="openai_api", reason="OPENAI_API_ERROR")],
            ) from err
        except Exception as err:
            await self._repo.mark_failed(
                guide,
                error_code="GENERATION_REQUEST_FAILED",
                error_message=_GENERATION_FAILED_ERROR_MESSAGE,
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=500,
                code="GUIDE_GENERATION_FAILED",
                message="복약 가이드 생성에 실패했습니다. 다시 시도해 주세요.",
                details=[ErrorDetail(field="guide", reason="GENERATION_REQUEST_FAILED")],
            ) from err

        guide = await self._repo.mark_completed(
            guide,
            content=result.content,
            model_name=result.model_name,
            prompt_version=result.prompt_version,
            completed_at=datetime.now(UTC),
        )

        return _to_guide_data(guide)

    async def get_guide_detail(self, *, user: User, guide_id: UUID) -> GuideData:
        # 지원 API: 새로고침·재조회용. one-cycle 최초 생성 흐름에는 필요하지 않습니다.
        guide = await self._repo.get_owned(guide_id=guide_id, user_id=user.id)
        if guide is None:
            raise ApiError(
                status_code=404,
                code="GUIDE_NOT_FOUND",
                message="가이드를 찾을 수 없습니다.",
                details=[ErrorDetail(field="guide_id", reason="NOT_FOUND", rejected_value=str(guide_id))],
            )
        return _to_guide_data(guide)
