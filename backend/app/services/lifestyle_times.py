from typing import Any
from uuid import UUID

from app.core.errors import ApiError, ErrorDetail
from app.dtos.lifestyle_times import (
    LifestyleDay,
    LifestyleTimesData,
    LifestyleTimesResponse,
    PutLifestyleTimesRequest,
)
from app.models.lifestyle_times import LifestyleTimes
from app.repositories.lifestyle_times_repository import LifestyleTimesRepository
from app.services.idempotency import SyncMutationIdempotencyService, SyncMutationResult


class LifestyleTimesService:
    def __init__(
        self,
        repository: LifestyleTimesRepository,
        idempotency: SyncMutationIdempotencyService,
    ) -> None:
        self._repository = repository
        self._idempotency = idempotency

    @staticmethod
    def _not_found() -> ApiError:
        return ApiError(
            status_code=404,
            code="LIFESTYLE_TIMES_NOT_FOUND",
            message="본인 생활 시간 설정을 찾을 수 없습니다.",
        )

    @staticmethod
    def _revision_conflict() -> ApiError:
        return ApiError(
            status_code=409,
            code="LIFESTYLE_TIMES_REVISION_CONFLICT",
            message="최신 생활 시간 설정을 다시 확인해 주세요.",
            details=[ErrorDetail(field="expected_revision", reason="CURRENT_REVISION_MISMATCH")],
        )

    @staticmethod
    def _response(current: LifestyleTimes | None) -> LifestyleTimesResponse:
        return LifestyleTimesResponse(
            data=LifestyleTimesData(
                revision=current.revision if current is not None else 0,
                updated_at=current.updated_at if current is not None else None,
                days=[LifestyleDay.model_validate(day) for day in current.days] if current is not None else [],
            )
        )

    async def get(self, *, user_id: UUID) -> LifestyleTimesResponse:
        profile_id = await self._repository.get_self_profile_id(user_id=user_id)
        if profile_id is None:
            raise self._not_found()
        return self._response(await self._repository.get_current(profile_id=profile_id))

    async def put(
        self,
        *,
        user_id: UUID,
        request: PutLifestyleTimesRequest,
        idempotency_key: str,
    ) -> SyncMutationResult:
        profile_id = await self._repository.get_self_profile_id(user_id=user_id)
        if profile_id is None:
            raise self._not_found()

        canonical_days = [day.model_dump(mode="json") for day in request.days]

        async def mutate() -> dict[str, Any]:
            if not await self._repository.lock_self_profile(user_id=user_id, profile_id=profile_id):
                raise self._not_found()
            current = await self._repository.get_current(profile_id=profile_id)
            actual_revision = current.revision if current is not None else 0
            if request.expected_revision != actual_revision:
                raise self._revision_conflict()
            if current is None:
                current = await self._repository.create(profile_id=profile_id, days=canonical_days)
            else:
                current = await self._repository.replace(current=current, days=canonical_days)
            return self._response(current).model_dump(mode="json")

        return await self._idempotency.execute(
            user_id=user_id,
            operation_id="lifestyle-times.put",
            parent_resource_id=profile_id,
            idempotency_key=idempotency_key,
            fingerprint=request.model_dump(mode="json"),
            success_status=200,
            mutate=mutate,
        )
