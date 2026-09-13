from typing import Any
from uuid import UUID

from app.core.errors import ApiError
from app.dtos.medication_checkins import (
    MedicationCheckinData,
    MedicationCheckinResponse,
    PutMedicationCheckinRequest,
)
from app.models.medication_schedules import MedicationCheckinStatus
from app.repositories.medication_schedule_repository import MedicationScheduleRepository
from app.services.idempotency import SyncMutationIdempotencyService, SyncMutationResult
from app.services.medication_checkins import MedicationCheckinService

MEDICATION_CHECKIN_PUT_OPERATION_ID = "medication-checkin.put"


class MedicationCheckinApiService:
    def __init__(
        self,
        schedules: MedicationScheduleRepository,
        checkins: MedicationCheckinService,
        idempotency: SyncMutationIdempotencyService,
    ) -> None:
        self._schedules = schedules
        self._checkins = checkins
        self._idempotency = idempotency

    async def put(
        self,
        *,
        user_id: UUID,
        occurrence_id: UUID,
        request: PutMedicationCheckinRequest,
        idempotency_key: str,
    ) -> SyncMutationResult:
        # A stored medical response remains subject to the SELF ownership check.
        # Revision and occurrence-state checks stay inside mutate, after replay.
        occurrence = await self._schedules.get_occurrence_owned(occurrence_id=occurrence_id, user_id=user_id)
        if occurrence is None:
            raise ApiError(
                status_code=404,
                code="MEDICATION_OCCURRENCE_NOT_FOUND",
                message="복약 일정을 찾을 수 없습니다.",
            )

        async def mutate() -> dict[str, Any]:
            result = await self._checkins.put_owned(
                user_id=user_id,
                occurrence_id=occurrence_id,
                status=MedicationCheckinStatus(request.status),
                taken_at=request.taken_at,
                expected_revision=request.expected_revision,
            )
            return MedicationCheckinResponse(data=MedicationCheckinData.model_validate(result)).model_dump(mode="json")

        return await self._idempotency.execute(
            user_id=user_id,
            operation_id=MEDICATION_CHECKIN_PUT_OPERATION_ID,
            parent_resource_id=occurrence_id,
            idempotency_key=idempotency_key,
            fingerprint=request.model_dump(mode="json"),
            success_status=200,
            mutate=mutate,
        )
