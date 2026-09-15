from typing import Any
from uuid import UUID

from app.core.errors import ApiError
from app.dtos.track_c import (
    BarrierResponseData,
    BarrierResponseEnvelope,
    CreateSafetyAssessmentRequest,
    PutBarrierResponseRequest,
    SafetyAssessmentData,
    SafetyAssessmentResponse,
)
from app.models.track_c import BarrierResponseStatus
from app.repositories.track_c_storage_repository import TrackCStorageRepository
from app.services.idempotency import SyncMutationIdempotencyService, SyncMutationResult
from app.services.track_c_flow import TrackCFlowService

SAFETY_ASSESSMENT_POST_OPERATION_ID = "safety-assessment.create"
BARRIER_RESPONSE_PUT_OPERATION_ID = "barrier-response.put"


class TrackCApiService:
    def __init__(
        self,
        repository: TrackCStorageRepository,
        flow: TrackCFlowService,
        idempotency: SyncMutationIdempotencyService,
    ) -> None:
        self._repository = repository
        self._flow = flow
        self._idempotency = idempotency

    async def create_safety(
        self,
        *,
        user_id: UUID,
        request: CreateSafetyAssessmentRequest,
        idempotency_key: str,
    ) -> SyncMutationResult:
        # Do not allow another user's stored replay to become an ownership oracle.
        owned = await self._repository.get_checkin_owned(checkin_id=request.medication_checkin_id, user_id=user_id)
        if owned is None:
            raise self._not_found()

        async def mutate() -> dict[str, Any]:
            result = await self._flow.create_safety_owned(
                user_id=user_id,
                checkin_id=request.medication_checkin_id,
                checkin_revision=request.checkin_revision,
                symptom_codes=request.symptom_codes,
                expected_revision=request.expected_revision,
            )
            return SafetyAssessmentResponse(
                data=SafetyAssessmentData(
                    assessment_id=result.id,
                    medication_checkin_id=result.medication_checkin_id,
                    checkin_revision=result.checkin_revision,
                    response_level=result.response_level,
                    safety_disposition=result.safety_disposition,
                    message_code=result.message_code,
                    copy_version=result.copy_version,
                    source_version=result.source_version,
                    revision=result.revision,
                )
            ).model_dump(mode="json")

        return await self._idempotency.execute(
            user_id=user_id,
            operation_id=SAFETY_ASSESSMENT_POST_OPERATION_ID,
            parent_resource_id=request.medication_checkin_id,
            idempotency_key=idempotency_key,
            fingerprint=request.model_dump(mode="json"),
            success_status=200,
            mutate=mutate,
        )

    async def put_barrier(
        self,
        *,
        user_id: UUID,
        checkin_id: UUID,
        request: PutBarrierResponseRequest,
        idempotency_key: str,
    ) -> SyncMutationResult:
        owned = await self._repository.get_checkin_owned(checkin_id=checkin_id, user_id=user_id)
        if owned is None:
            raise self._not_found()

        async def mutate() -> dict[str, Any]:
            result = await self._flow.put_barrier_owned(
                user_id=user_id,
                checkin_id=checkin_id,
                checkin_revision=request.checkin_revision,
                response_status=BarrierResponseStatus(request.response_status),
                barrier_code=request.barrier_code,
                expected_revision=request.expected_revision,
            )
            return BarrierResponseEnvelope(
                data=BarrierResponseData(
                    barrier_response_id=result.id,
                    medication_checkin_id=result.medication_checkin_id,
                    checkin_revision=result.checkin_revision,
                    safety_assessment_id=result.safety_assessment_id,
                    response_status=result.response_status,
                    barrier_code=result.barrier_code,
                    revision=result.revision,
                )
            ).model_dump(mode="json")

        return await self._idempotency.execute(
            user_id=user_id,
            operation_id=BARRIER_RESPONSE_PUT_OPERATION_ID,
            parent_resource_id=checkin_id,
            idempotency_key=idempotency_key,
            fingerprint=request.model_dump(mode="json"),
            success_status=200,
            mutate=mutate,
        )

    @staticmethod
    def _not_found() -> ApiError:
        return ApiError(
            status_code=404,
            code="MEDICATION_CHECKIN_NOT_FOUND",
            message="복약 기록을 찾을 수 없습니다.",
        )
