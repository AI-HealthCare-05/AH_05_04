"""Static, single-offer Track C support and explicitly confirmed Plan creation."""

from typing import Any
from uuid import UUID

from app.core.errors import ApiError
from app.dtos.track_c_support import (
    ActionConfigSnapshot,
    CreateSupportActionPlanRequest,
    SupportActionPlanData,
    SupportActionPlanResponse,
    SupportCopyData,
    SupportOfferData,
    SupportOfferItem,
    SupportOfferResponse,
)
from app.models.medication_schedules import MedicationCheckinStatus
from app.models.track_c import (
    BarrierResponse,
    BarrierResponseStatus,
    SafetyDisposition,
    SafetyResponseLevel,
    SupportCode,
)
from app.repositories.track_c_storage_repository import TrackCStorageRepository
from app.services.idempotency import SyncMutationIdempotencyService, SyncMutationResult
from app.services.track_c_handler_config import (
    HandlerConfig,
    HandlerConfigError,
    SupportCopyCatalog,
    SupportRule,
    load_active_handler_config,
    load_active_support_copy_catalog,
    save_action_plan_snapshot,
)

SUPPORT_OFFER_GET_OPERATION_ID = "barrier-response.supports"
SUPPORT_ACTION_PLAN_POST_OPERATION_ID = "support-action-plan.create"


def eligible_supports(config: HandlerConfig, barrier: BarrierResponse) -> list[SupportRule]:
    """All six approved handlers are static guidance; no provider routing is needed."""
    if barrier.response_status != BarrierResponseStatus.ANSWERED:
        return []
    return sorted(
        (rule for rule in config.supports.values() if barrier.barrier_code in rule.barrier_codes),
        key=lambda rule: (rule.priority, rule.support_code.value),
    )[:1]


class TrackCSupportService:
    def __init__(self, repository: TrackCStorageRepository, idempotency: SyncMutationIdempotencyService) -> None:
        self._repository = repository
        self._idempotency = idempotency

    async def _owned_parent(self, *, barrier_id: UUID, user_id: UUID) -> tuple[BarrierResponse, UUID]:
        parent = await self._repository.get_barrier_medication_owned(barrier_id=barrier_id, user_id=user_id)
        if parent is None:
            raise ApiError(
                status_code=404, code="BARRIER_RESPONSE_NOT_FOUND", message="지원 대상 응답을 찾을 수 없습니다."
            )
        return parent

    async def _lock_current_flow(self, *, barrier: BarrierResponse, user_id: UUID) -> None:
        # GET also holds these locks until its response is assembled. It writes no rows.
        checkin = await self._repository.lock_checkin_owned(checkin_id=barrier.medication_checkin_id, user_id=user_id)
        if checkin is None:
            raise ApiError(
                status_code=404, code="BARRIER_RESPONSE_NOT_FOUND", message="지원 대상 응답을 찾을 수 없습니다."
            )
        if checkin.status != MedicationCheckinStatus.NOT_TAKEN or checkin.revision != barrier.checkin_revision:
            raise ApiError(status_code=409, code="CHECKIN_FLOW_STALE", message="현재 미복용 기록을 다시 확인해 주세요.")
        safety = await self._repository.get_latest_safety_for_update(
            checkin_id=checkin.id, checkin_revision=checkin.revision
        )
        if (
            safety is None
            or safety.response_level != SafetyResponseLevel.ROUTINE
            or safety.safety_disposition != SafetyDisposition.NORMAL
        ):
            raise ApiError(
                status_code=409,
                code="SAFETY_FLOW_PRECEDES_SUPPORT",
                message="일상 지원 전에 안전 확인을 완료해 주세요.",
            )
        latest = await self._repository.get_latest_barrier_for_update(
            checkin_id=checkin.id, checkin_revision=checkin.revision
        )
        if latest is None or latest.id != barrier.id or latest.safety_assessment_id != safety.id:
            raise ApiError(
                status_code=409, code="BARRIER_FLOW_STALE", message="최신 안전 확인에 맞춰 어려움을 다시 확인해 주세요."
            )

    @staticmethod
    def _load_config() -> tuple[HandlerConfig, SupportCopyCatalog]:
        try:
            config = load_active_handler_config()
            catalog = load_active_support_copy_catalog()
            if {rule.copy_version for rule in config.supports.values()} != {catalog.copy_version}:
                raise HandlerConfigError("active copy changed during load")
            return config, catalog
        except HandlerConfigError:
            # Missing/damaged assets are not an ordinary empty-offer outcome.
            raise ApiError(
                status_code=503,
                code="SUPPORT_CONFIG_UNAVAILABLE",
                message="지원 안내를 불러올 수 없습니다. 다시 시도해 주세요.",
            ) from None

    async def get_supports(self, *, user_id: UUID, barrier_id: UUID) -> SupportOfferResponse:
        barrier, medication_id = await self._owned_parent(barrier_id=barrier_id, user_id=user_id)
        await self._lock_current_flow(barrier=barrier, user_id=user_id)
        config, catalog = self._load_config()
        supports = []
        for rule in eligible_supports(config, barrier):
            copy = catalog.supports[rule.support_code]
            supports.append(
                SupportOfferItem(
                    support_code=rule.support_code,
                    rule_version=config.rule_version,
                    copy_version=rule.copy_version,
                    priority=rule.priority,
                    rationale_code=rule.rationale_code,
                    action_config=ActionConfigSnapshot.model_validate(
                        config.snapshot(
                            rule.support_code,
                            medication_id=medication_id if rule.support_code == SupportCode.REMINDER_SETUP else None,
                        )
                    ),
                    support_copy=SupportCopyData(
                        title=copy.title,
                        body=copy.body,
                        confirmation_prompt=copy.confirmation_prompt,
                        primary_label=copy.primary_label,
                        secondary_label=copy.secondary_label,
                    ),
                )
            )
        return SupportOfferResponse(
            data=SupportOfferData(
                barrier_response_id=barrier.id,
                medication_checkin_id=barrier.medication_checkin_id,
                checkin_revision=barrier.checkin_revision,
                safety_assessment_id=barrier.safety_assessment_id,
                supports=supports,
                reason_code=None if supports else "NO_ELIGIBLE_SUPPORT",
            )
        )

    async def create_plan(
        self, *, user_id: UUID, request: CreateSupportActionPlanRequest, idempotency_key: str
    ) -> SyncMutationResult:
        # Ownership precedes replay; currentness is checked only for a new mutation.
        await self._owned_parent(barrier_id=request.barrier_response_id, user_id=user_id)

        async def mutate() -> dict[str, Any]:
            barrier, _ = await self._owned_parent(barrier_id=request.barrier_response_id, user_id=user_id)
            await self._lock_current_flow(barrier=barrier, user_id=user_id)
            config, _ = self._load_config()
            offered = eligible_supports(config, barrier)
            if request.rule_version != config.rule_version or (
                offered and request.copy_version != offered[0].copy_version
            ):
                raise ApiError(
                    status_code=409,
                    code="SUPPORT_VERSION_CONFLICT",
                    message="지원 안내가 변경되었습니다. 다시 확인해 주세요.",
                )
            if not offered or request.support_code != offered[0].support_code:
                raise ApiError(
                    status_code=409, code="SUPPORT_NOT_OFFERED", message="현재 제안된 지원만 선택할 수 있습니다."
                )
            active = await self._repository.get_active_plan_for_update(barrier_id=barrier.id)
            if active is not None:
                raise ApiError(
                    status_code=409, code="ACTION_PLAN_ALREADY_ACTIVE", message="이미 진행 중인 지원 계획이 있습니다."
                )
            plan = await save_action_plan_snapshot(
                self._repository.session,
                user_id=user_id,
                barrier_id=barrier.id,
                support_code=request.support_code,
                config=config,
            )
            return SupportActionPlanResponse(
                data=SupportActionPlanData(
                    support_action_plan_id=plan.id,
                    barrier_response_id=plan.barrier_response_id,
                    support_code=plan.support_code,
                    rule_version=plan.rule_version,
                    copy_version=plan.copy_version,
                    action_config_snapshot=ActionConfigSnapshot.model_validate(plan.action_config_snapshot),
                    status=plan.status,
                    created_at=plan.created_at,
                    completed_at=plan.completed_at,
                    cancelled_at=plan.cancelled_at,
                )
            ).model_dump(mode="json")

        return await self._idempotency.execute(
            user_id=user_id,
            operation_id=SUPPORT_ACTION_PLAN_POST_OPERATION_ID,
            parent_resource_id=request.barrier_response_id,
            idempotency_key=idempotency_key,
            fingerprint=request.model_dump(mode="json"),
            success_status=200,
            mutate=mutate,
        )
