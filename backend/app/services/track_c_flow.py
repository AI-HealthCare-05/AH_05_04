"""Track C C2 Safety → Barrier state transitions.

The approved contract does not yet contain a medically reviewed non-empty symptom
code table. The default policy therefore handles the approved empty-list path and
fails closed to UNKNOWN for every non-empty list. InternalDemoSafetyPolicy is a
separate opt-in Local synthetic demo policy, not a medically reviewed replacement
or a public release.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.core.errors import ApiError, ErrorDetail
from app.models.medication_schedules import MedicationCheckinStatus
from app.models.track_c import (
    BarrierCode,
    BarrierResponse,
    BarrierResponseStatus,
    SafetyAssessment,
    SafetyDisposition,
    SafetyResponseLevel,
)
from app.repositories.track_c_storage_repository import TrackCStorageRepository


@dataclass(frozen=True)
class SafetyPolicyResult:
    response_level: SafetyResponseLevel
    safety_disposition: SafetyDisposition
    message_code: str
    copy_version: str
    source_version: str


class SafetyPolicy(Protocol):
    def evaluate(self, symptom_codes: tuple[str, ...], *, user_id: UUID | None = None) -> SafetyPolicyResult: ...


class ContractFoundationSafetyPolicy:
    """Non-production C2 foundation until the reviewed symptom artifact is supplied."""

    _COPY_VERSION = "track-c-safety-foundation-v1"
    _SOURCE_VERSION = "contract-freeze-v4"

    def evaluate(self, symptom_codes: tuple[str, ...], *, user_id: UUID | None = None) -> SafetyPolicyResult:
        if not symptom_codes:
            return SafetyPolicyResult(
                response_level=SafetyResponseLevel.ROUTINE,
                safety_disposition=SafetyDisposition.NORMAL,
                message_code="NO_SYMPTOMS_CONFIRMED",
                copy_version=self._COPY_VERSION,
                source_version=self._SOURCE_VERSION,
            )
        return SafetyPolicyResult(
            response_level=SafetyResponseLevel.UNKNOWN,
            safety_disposition=SafetyDisposition.UNKNOWN_RISK,
            message_code="SYMPTOM_POLICY_APPROVAL_REQUIRED",
            copy_version=self._COPY_VERSION,
            source_version=self._SOURCE_VERSION,
        )


class TrackCFlowService:
    def __init__(self, repository: TrackCStorageRepository, safety_policy: SafetyPolicy) -> None:
        self._repository = repository
        self._safety_policy = safety_policy

    async def create_safety_owned(
        self,
        *,
        user_id: UUID,
        checkin_id: UUID,
        checkin_revision: int,
        symptom_codes: list[str],
        expected_revision: int,
    ) -> SafetyAssessment:
        checkin = await self._repository.lock_checkin_owned(checkin_id=checkin_id, user_id=user_id)
        if checkin is None:
            raise self._not_found()
        self._ensure_current_not_taken(checkin.status, checkin.revision, checkin_revision)

        latest = await self._repository.get_latest_safety_for_update(
            checkin_id=checkin.id,
            checkin_revision=checkin_revision,
        )
        current_revision = latest.revision if latest is not None else 0
        if expected_revision != current_revision:
            raise ApiError(
                status_code=409,
                code="SAFETY_ASSESSMENT_REVISION_CONFLICT",
                message="안전 확인 결과가 이미 변경되었습니다. 최신 결과를 다시 확인해 주세요.",
                details=[ErrorDetail(field="expected_revision", reason="CURRENT_REVISION_MISMATCH")],
            )

        policy_result = self._safety_policy.evaluate(tuple(symptom_codes), user_id=user_id)
        assessment = await self._repository.create_safety(
            checkin_id=checkin.id,
            checkin_revision=checkin_revision,
            revision=current_revision + 1,
            symptom_codes=list(symptom_codes),
            response_level=policy_result.response_level,
            safety_disposition=policy_result.safety_disposition,
            message_code=policy_result.message_code,
            copy_version=policy_result.copy_version,
            source_version=policy_result.source_version,
        )
        if policy_result.response_level != SafetyResponseLevel.ROUTINE:
            await self._repository.cancel_active_plans_for_checkin_revision(
                checkin_id=checkin.id,
                checkin_revision=checkin_revision,
                cancelled_at=datetime.now(UTC),
            )
        return assessment

    async def put_barrier_owned(
        self,
        *,
        user_id: UUID,
        checkin_id: UUID,
        checkin_revision: int,
        response_status: BarrierResponseStatus,
        barrier_code: BarrierCode | None,
        expected_revision: int,
    ) -> BarrierResponse:
        checkin = await self._repository.lock_checkin_owned(checkin_id=checkin_id, user_id=user_id)
        if checkin is None:
            raise self._not_found()
        self._ensure_current_not_taken(checkin.status, checkin.revision, checkin_revision)

        latest_safety = await self._repository.get_latest_safety_for_update(
            checkin_id=checkin.id,
            checkin_revision=checkin_revision,
        )
        if latest_safety is None or latest_safety.response_level != SafetyResponseLevel.ROUTINE:
            raise ApiError(
                status_code=409,
                code="SAFETY_FLOW_PRECEDES_BARRIER",
                message="일상 지원을 선택하기 전에 현재 복약 기록의 안전 확인을 완료해 주세요.",
            )

        latest_barrier = await self._repository.get_latest_barrier_for_update(
            checkin_id=checkin.id,
            checkin_revision=checkin_revision,
        )
        current_revision = latest_barrier.revision if latest_barrier is not None else 0
        if expected_revision != current_revision:
            raise ApiError(
                status_code=409,
                code="BARRIER_RESPONSE_REVISION_CONFLICT",
                message="현재 미복용 지원 흐름과 요청한 Barrier revision이 일치하지 않습니다.",
                details=[ErrorDetail(field="expected_revision", reason="CURRENT_REVISION_MISMATCH")],
            )
        return await self._repository.create_barrier(
            checkin_id=checkin.id,
            checkin_revision=checkin_revision,
            safety_assessment_id=latest_safety.id,
            revision=current_revision + 1,
            response_status=response_status,
            barrier_code=barrier_code,
        )

    @staticmethod
    def _ensure_current_not_taken(
        status: MedicationCheckinStatus,
        current_revision: int,
        requested_revision: int,
    ) -> None:
        if status != MedicationCheckinStatus.NOT_TAKEN or current_revision != requested_revision:
            raise ApiError(
                status_code=409,
                code="CHECKIN_FLOW_STALE",
                message="현재 미복용 기록과 요청한 revision이 일치하지 않습니다.",
            )

    @staticmethod
    def _not_found() -> ApiError:
        return ApiError(
            status_code=404,
            code="MEDICATION_CHECKIN_NOT_FOUND",
            message="복약 기록을 찾을 수 없습니다.",
        )
