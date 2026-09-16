"""Static, single-offer Track C support and explicitly confirmed Plan creation."""

from asyncio import to_thread
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.errors import ApiError
from app.dtos.track_c_support import (
    ActionConfigSnapshot,
    ActionPlanFollowupData,
    ActionPlanFollowupEnvelope,
    ActionPlanFollowupReadEnvelope,
    CreateSupportActionPlanRequest,
    PatchSupportActionPlanRequest,
    SubmitActionPlanFollowupRequest,
    SupportActionPlanData,
    SupportActionPlanResponse,
    SupportCopyData,
    SupportOfferData,
    SupportOfferItem,
    SupportOfferResponse,
    TravelSituation,
)
from app.models.medication_schedules import MedicationCheckin, MedicationCheckinStatus
from app.models.track_c import (
    ActionPlanFollowup,
    BarrierCode,
    BarrierResponse,
    BarrierResponseStatus,
    SafetyAssessment,
    SafetyDisposition,
    SafetyResponseLevel,
    SupportActionPlan,
    SupportActionPlanStatus,
    SupportCode,
)
from app.repositories.track_c_storage_repository import TrackCStorageRepository
from app.services.idempotency import SyncMutationIdempotencyService, SyncMutationResult
from app.services.track_c_handler_config import (
    HandlerConfig,
    HandlerConfigError,
    SupportCopyCatalog,
    SupportRule,
    load_active_support_assets,
    save_action_plan_snapshot,
)

SUPPORT_OFFER_GET_OPERATION_ID = "barrier-response.supports"
SUPPORT_ACTION_PLAN_POST_OPERATION_ID = "support-action-plan.create"
SUPPORT_ACTION_PLAN_GET_OPERATION_ID = "support-action-plan.get"
SUPPORT_ACTION_PLAN_PATCH_OPERATION_ID = "support-action-plan.patch"
ACTION_PLAN_FOLLOWUP_POST_OPERATION_ID = "support-action-plan.followup.submit"
ACTION_PLAN_FOLLOWUP_GET_OPERATION_ID = "support-action-plan.followup.get"


def eligible_supports(
    config: HandlerConfig, barrier: BarrierResponse, travel_situation: TravelSituation | None = None
) -> list[SupportRule]:
    """Filter by explicit travel situation before the stable single-offer ordering."""
    if travel_situation is not None and (
        barrier.response_status != BarrierResponseStatus.ANSWERED
        or barrier.barrier_code != BarrierCode.SCHEDULE_OR_TRAVEL
    ):
        raise ApiError(
            status_code=422, code="VALIDATION_FAILED", message="일정 변경·외출 사유에서만 상황을 선택해 주세요."
        )
    selected = (
        {
            "SCHEDULE_CHANGED": SupportCode.REMINDER_SETUP,
            "MEDICATION_NOT_WITH_ME": SupportCode.ROUTINE_OR_TRAVEL_PLAN,
        }.get(travel_situation)
        if travel_situation is not None
        else None
    )
    if barrier.response_status != BarrierResponseStatus.ANSWERED:
        return []
    return sorted(
        (
            rule
            for rule in config.supports.values()
            if barrier.barrier_code in rule.barrier_codes and (selected is None or rule.support_code == selected)
        ),
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
        checkin = await self._repository.lock_checkin_owned(checkin_id=barrier.medication_checkin_id, user_id=user_id)
        if checkin is None:
            raise ApiError(
                status_code=404, code="BARRIER_RESPONSE_NOT_FOUND", message="지원 대상 응답을 찾을 수 없습니다."
            )
        safety = await self._repository.get_latest_safety_for_update(
            checkin_id=checkin.id, checkin_revision=checkin.revision
        )
        latest = await self._repository.get_latest_barrier_for_update(
            checkin_id=checkin.id, checkin_revision=checkin.revision
        )
        self._ensure_current_flow(barrier, checkin, safety, latest.id if latest else None)

    @staticmethod
    def _ensure_current_flow(
        barrier: BarrierResponse,
        checkin: MedicationCheckin,
        safety: SafetyAssessment | None,
        latest_barrier_id: UUID | None,
    ) -> None:
        if checkin.status != MedicationCheckinStatus.NOT_TAKEN or checkin.revision != barrier.checkin_revision:
            raise ApiError(status_code=409, code="CHECKIN_FLOW_STALE", message="현재 미복용 기록을 다시 확인해 주세요.")
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
        if latest_barrier_id != barrier.id or barrier.safety_assessment_id != safety.id:
            raise ApiError(
                status_code=409, code="BARRIER_FLOW_STALE", message="최신 안전 확인에 맞춰 어려움을 다시 확인해 주세요."
            )

    @staticmethod
    async def _load_config() -> tuple[HandlerConfig, SupportCopyCatalog]:
        try:
            return await to_thread(load_active_support_assets)
        except HandlerConfigError:
            # Missing/damaged assets are not an ordinary empty-offer outcome.
            raise ApiError(
                status_code=503,
                code="SUPPORT_CONFIG_UNAVAILABLE",
                message="지원 안내를 불러올 수 없습니다. 다시 시도해 주세요.",
            ) from None

    async def get_supports(
        self, *, user_id: UUID, barrier_id: UUID, travel_situation: TravelSituation | None = None
    ) -> SupportOfferResponse:
        flow = await self._repository.get_support_flow_owned(barrier_id=barrier_id, user_id=user_id)
        if flow is None:
            raise ApiError(
                status_code=404, code="BARRIER_RESPONSE_NOT_FOUND", message="지원 대상 응답을 찾을 수 없습니다."
            )
        barrier, medication_id, checkin, safety, latest_barrier_id = flow
        self._ensure_current_flow(barrier, checkin, safety, latest_barrier_id)
        config, catalog = await self._load_config()
        supports = []
        for rule in eligible_supports(config, barrier, travel_situation):
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
            config, _ = await self._load_config()
            await self._lock_current_flow(barrier=barrier, user_id=user_id)
            offered = eligible_supports(config, barrier, request.travel_situation)
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
            return self._plan_response(plan).model_dump(mode="json")

        return await self._idempotency.execute(
            user_id=user_id,
            operation_id=SUPPORT_ACTION_PLAN_POST_OPERATION_ID,
            parent_resource_id=request.barrier_response_id,
            idempotency_key=idempotency_key,
            fingerprint=request.model_dump(mode="json", exclude_none=True),
            success_status=200,
            mutate=mutate,
        )

    @staticmethod
    def _plan_response(plan: SupportActionPlan) -> SupportActionPlanResponse:
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
        )

    @staticmethod
    def _plan_not_found() -> ApiError:
        return ApiError(status_code=404, code="ACTION_PLAN_NOT_FOUND", message="지원 계획을 찾을 수 없습니다.")

    async def _owned_plan(self, *, user_id: UUID, plan_id: UUID) -> SupportActionPlan:
        plan = await self._repository.get_action_plan_owned(plan_id=plan_id, user_id=user_id)
        if plan is None:
            raise self._plan_not_found()
        return plan

    async def get_plan(self, *, user_id: UUID, plan_id: UUID) -> SupportActionPlanResponse:
        return self._plan_response(await self._owned_plan(user_id=user_id, plan_id=plan_id))

    @staticmethod
    def _followup_data(followup: ActionPlanFollowup) -> ActionPlanFollowupData:
        return ActionPlanFollowupData(
            followup_id=followup.id,
            support_action_plan_id=followup.support_action_plan_id,
            response=followup.response,
            revision=followup.revision,
            created_at=followup.created_at,
            updated_at=followup.updated_at,
        )

    async def get_followup(self, *, user_id: UUID, plan_id: UUID) -> ActionPlanFollowupReadEnvelope:
        owned = await self._repository.get_plan_followup_owned(plan_id=plan_id, user_id=user_id)
        if owned is None:
            raise self._plan_not_found()
        _, followup = owned
        return ActionPlanFollowupReadEnvelope(data=self._followup_data(followup) if followup else None)

    async def submit_followup(
        self, *, user_id: UUID, plan_id: UUID, request: SubmitActionPlanFollowupRequest, idempotency_key: str
    ) -> SyncMutationResult:
        await self._owned_plan(user_id=user_id, plan_id=plan_id)

        async def mutate() -> dict[str, Any]:
            owned = await self._owned_plan(user_id=user_id, plan_id=plan_id)
            barrier = await self._repository.get_barrier_owned(barrier_id=owned.barrier_response_id, user_id=user_id)
            if barrier is None:
                raise self._plan_not_found()
            checkin = await self._repository.lock_checkin_owned(
                checkin_id=barrier.medication_checkin_id, user_id=user_id
            )
            if checkin is None:
                raise self._plan_not_found()
            # Preserve the shared lock order without treating historical feedback as a new support action.
            await self._repository.get_latest_safety_for_update(
                checkin_id=checkin.id, checkin_revision=checkin.revision
            )
            await self._repository.get_latest_barrier_for_update(
                checkin_id=checkin.id, checkin_revision=checkin.revision
            )
            plan = await self._repository.get_action_plan_for_update(plan_id=plan_id)
            if plan is None:
                raise self._plan_not_found()
            if plan.status != SupportActionPlanStatus.COMPLETED:
                raise ApiError(
                    status_code=409,
                    code="ACTION_PLAN_STATE_CONFLICT",
                    message="완료한 지원 계획에만 평가를 남길 수 있습니다.",
                )
            current = await self._repository.get_plan_followup_for_update(plan_id=plan_id)
            if request.expected_revision != (current.revision if current else 0):
                raise ApiError(
                    status_code=409,
                    code="ACTION_PLAN_FOLLOWUP_REVISION_CONFLICT",
                    message="평가가 변경되었습니다. 최신 응답을 다시 확인해 주세요.",
                )
            followup = await self._repository.save_plan_followup(
                plan_id=plan_id,
                current=current,
                response=request.response,
                user_id=user_id,
                changed_at=datetime.now(UTC),
            )
            return ActionPlanFollowupEnvelope(data=self._followup_data(followup)).model_dump(mode="json")

        return await self._idempotency.execute(
            user_id=user_id,
            operation_id=ACTION_PLAN_FOLLOWUP_POST_OPERATION_ID,
            parent_resource_id=plan_id,
            idempotency_key=idempotency_key,
            fingerprint=request.model_dump(mode="json"),
            success_status=200,
            mutate=mutate,
        )

    async def patch_plan(
        self, *, user_id: UUID, plan_id: UUID, request: PatchSupportActionPlanRequest, idempotency_key: str
    ) -> SyncMutationResult:
        # A stored successful response never bypasses current SELF ownership.
        await self._owned_plan(user_id=user_id, plan_id=plan_id)

        async def mutate() -> dict[str, Any]:
            owned = await self._owned_plan(user_id=user_id, plan_id=plan_id)
            barrier = await self._repository.get_barrier_owned(barrier_id=owned.barrier_response_id, user_id=user_id)
            if barrier is None:
                raise self._plan_not_found()
            checkin = await self._repository.lock_checkin_owned(
                checkin_id=barrier.medication_checkin_id, user_id=user_id
            )
            if checkin is None:
                raise self._plan_not_found()
            safety = await self._repository.get_latest_safety_for_update(
                checkin_id=checkin.id, checkin_revision=checkin.revision
            )
            latest = await self._repository.get_latest_barrier_for_update(
                checkin_id=checkin.id, checkin_revision=checkin.revision
            )
            plan = await self._repository.get_action_plan_for_update(plan_id=plan_id)
            if plan is None:
                raise self._plan_not_found()
            if plan.status != SupportActionPlanStatus.ACTIVE:
                raise ApiError(
                    status_code=409, code="ACTION_PLAN_STATE_CONFLICT", message="이미 종료된 지원 계획입니다."
                )
            if request.status == "COMPLETED":
                self._ensure_current_flow(barrier, checkin, safety, latest.id if latest else None)
                plan.status = SupportActionPlanStatus.COMPLETED
                plan.completed_at = datetime.now(UTC)
            else:
                # Cancellation remains available when the original support flow is stale.
                plan.status = SupportActionPlanStatus.CANCELLED
                plan.cancelled_at = datetime.now(UTC)
            await self._repository.session.flush()
            return self._plan_response(plan).model_dump(mode="json")

        return await self._idempotency.execute(
            user_id=user_id,
            operation_id=SUPPORT_ACTION_PLAN_PATCH_OPERATION_ID,
            parent_resource_id=plan_id,
            idempotency_key=idempotency_key,
            fingerprint=request.model_dump(mode="json"),
            success_status=200,
            mutate=mutate,
        )
