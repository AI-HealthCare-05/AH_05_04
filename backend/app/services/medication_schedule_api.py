from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from app.core.errors import ApiError, ErrorDetail
from app.dtos.medication_checkins import MedicationCheckinData
from app.dtos.medication_schedules import (
    CancelMedicationScheduleRequest,
    MedicationDayData,
    MedicationDayResponse,
    MedicationOccurrenceData,
    MedicationOccurrenceMedicationData,
    MedicationOccurrenceMedicationResponse,
    MedicationScheduleData,
    MedicationScheduleItem,
    MedicationScheduleResponse,
    PutMedicationScheduleRequest,
)
from app.models.medication_schedule_snapshots import ScheduleAuditSnapshot
from app.models.medication_schedules import MedicationScheduleSource, MedicationScheduleStatus
from app.repositories.medication_schedule_queries import MedicationScheduleQueries
from app.services.idempotency import SyncMutationIdempotencyService, SyncMutationResult
from app.services.medication_schedule_mutations import (
    MedicationScheduleMutationService,
    ScheduleOwnershipNotFoundError,
    ScheduleRevisionConflictError,
    ScheduleVersionConflictError,
)


class MedicationScheduleApiService:
    def __init__(
        self,
        queries: MedicationScheduleQueries,
        mutations: MedicationScheduleMutationService,
        idempotency: SyncMutationIdempotencyService,
    ) -> None:
        self.queries = queries
        self.mutations = mutations
        self.idempotency = idempotency

    async def occurrence_medication(
        self, *, user_id: UUID, occurrence_id: UUID
    ) -> MedicationOccurrenceMedicationResponse:
        medication = await self.queries.occurrence_medication_owned(occurrence_id, user_id)
        if medication is None:
            raise ApiError(
                status_code=404,
                code="MEDICATION_OCCURRENCE_NOT_FOUND",
                message="복약 일정을 찾을 수 없습니다.",
            )
        return MedicationOccurrenceMedicationResponse(
            data=MedicationOccurrenceMedicationData(
                occurrence_id=occurrence_id,
                prescription_version_id=medication.prescription_version_id,
                prescription_version_medication_id=medication.id,
                medication_name=medication.medication_name,
                strength_text=medication.strength_text,
                dose_value=float(medication.dose_value) if medication.dose_value is not None else None,
                dose_unit=medication.dose_unit,
            )
        )

    async def write(
        self,
        *,
        user_id: UUID,
        medication_id: UUID,
        request: PutMedicationScheduleRequest | CancelMedicationScheduleRequest,
        idempotency_key: str,
    ) -> SyncMutationResult:
        medication = await self.queries.medication_owned(medication_id, user_id)
        if medication is None:
            raise ApiError(
                status_code=404, code="PRESCRIPTION_MEDICATION_NOT_FOUND", message="처방 약제를 찾을 수 없습니다."
            )

        async def mutate() -> dict[str, Any]:
            effective_at = datetime.now(UTC)
            try:
                if isinstance(request, PutMedicationScheduleRequest):
                    if medication.frequency_per_day is not None and medication.frequency_per_day != len(
                        request.local_times
                    ):
                        raise ApiError(
                            status_code=422,
                            code="VALIDATION_FAILED",
                            message="입력값을 확인해 주세요.",
                            details=[ErrorDetail(field="local_times", reason="FREQUENCY_MISMATCH")],
                        )
                    settings = ScheduleAuditSnapshot(
                        **request.model_dump(exclude={"expected_revision"}),
                        status=MedicationScheduleStatus.ACTIVE,
                        source=MedicationScheduleSource.USER_CONFIRMED,
                    )
                    schedule = await self.mutations.put(
                        medication_id=medication_id,
                        user_id=user_id,
                        expected_revision=request.expected_revision,
                        settings=settings,
                        effective_at=effective_at,
                    )
                else:
                    schedule = await self.mutations.cancel(
                        medication_id=medication_id,
                        user_id=user_id,
                        expected_revision=request.expected_revision,
                        effective_at=effective_at,
                    )
            except ScheduleOwnershipNotFoundError:
                raise ApiError(
                    status_code=404, code="PRESCRIPTION_MEDICATION_NOT_FOUND", message="처방 약제를 찾을 수 없습니다."
                ) from None
            except ScheduleVersionConflictError:
                raise ApiError(
                    status_code=409, code="PRESCRIPTION_VERSION_CONFLICT", message="현재 처방 버전을 확인해 주세요."
                ) from None
            except ScheduleRevisionConflictError:
                raise ApiError(
                    status_code=409, code="SCHEDULE_REVISION_CONFLICT", message="최신 일정을 다시 확인해 주세요."
                ) from None
            snapshot = await self.mutations.repository.snapshot(schedule)
            return MedicationScheduleResponse(
                data=MedicationScheduleData(
                    schedule_id=schedule.id,
                    prescription_version_medication_id=medication_id,
                    revision=schedule.revision,
                    **snapshot.model_dump(exclude={"source"}),
                )
            ).model_dump(mode="json")

        return await self.idempotency.execute(
            user_id=user_id,
            operation_id="medication-schedule.put"
            if isinstance(request, PutMedicationScheduleRequest)
            else "medication-schedule.patch",
            parent_resource_id=medication_id,
            idempotency_key=idempotency_key,
            fingerprint=request.model_dump(mode="json"),
            success_status=200,
            mutate=mutate,
        )

    async def day(self, *, user_id: UUID, day: date) -> MedicationDayResponse:
        items = []
        for medication, schedule in await self.queries.active_items(user_id):
            # Immutable medication snapshots have no exact start/time/pattern fields.
            # PD-417 forbids deriving them from prescribed_date or timing_text.
            items.append(
                MedicationScheduleItem(
                    prescription_version_medication_id=medication.id,
                    schedule_item_status="SETUP_REQUIRED"
                    if schedule is None
                    else "READY"
                    if schedule.status == MedicationScheduleStatus.ACTIVE
                    else "INACTIVE",
                    schedule_id=schedule.id if schedule else None,
                    revision=schedule.revision if schedule else None,
                    setup_reason="MISSING_START_DATE" if schedule is None else None,
                )
            )
        statuses = {item.schedule_item_status for item in items}
        data = MedicationDayData(
            schedule_status="NO_ACTIVE_PRESCRIPTION"
            if not items
            else "PARTIAL"
            if {"READY", "SETUP_REQUIRED"} <= statuses
            else "SETUP_REQUIRED"
            if "SETUP_REQUIRED" in statuses
            else "READY"
            if "READY" in statuses
            else "INACTIVE",
            schedule_items=items,
            occurrences=[],
        )
        for occurrence, medication, checkin in await self.queries.occurrences(user_id, day):
            data.occurrences.append(
                MedicationOccurrenceData(
                    occurrence_id=occurrence.id,
                    prescription_version_id=medication.prescription_version_id,
                    prescription_version_medication_id=medication.id,
                    scheduled_local_date=occurrence.scheduled_local_date,
                    scheduled_at=occurrence.scheduled_at,
                    confirmation_deadline_at=occurrence.confirmation_deadline_at,
                    status=occurrence.status,
                    checkin=MedicationCheckinData(
                        checkin_id=checkin.id,
                        occurrence_id=occurrence.id,
                        status=checkin.status,
                        taken_at=checkin.taken_at,
                        revision=checkin.revision,
                        corrected=checkin.revision > 1,
                    )
                    if checkin
                    else None,
                )
            )
        return MedicationDayResponse(data=data)
