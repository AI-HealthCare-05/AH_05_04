"""Application orchestration for one synchronous Guide runtime execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from app.models.async_jobs import AiJobStatus
from app.models.guides import Guide
from app.models.users import User
from app.repositories.guide_repository import GuideRepository
from app.services.guide_runtime_execution import (
    GuideRuntimeExecutionUnavailableError,
    execute_verified_guide_runtime,
)
from app.services.guide_sync_runtime_lifecycle import (
    GuideSyncRuntimeAuthority,
    GuideSyncRuntimeLifecycleProducer,
)
from rag_runtime.guide_runtime_execution import GuideRuntimeExecutorFactoryPort


class GuideSyncRuntimeAuthorityProvider(Protocol):
    async def resolve(self, *, user: User, guide: Guide) -> GuideSyncRuntimeAuthority: ...


class GuideSyncRuntimeVersionConflictError(RuntimeError):
    """The prescription version changed while the runtime was executing."""


class GuideSyncRuntimeExecution:
    def __init__(
        self,
        *,
        repository: GuideRepository,
        lifecycle: GuideSyncRuntimeLifecycleProducer,
        executor_factory: GuideRuntimeExecutorFactoryPort,
        authority_provider: GuideSyncRuntimeAuthorityProvider,
    ) -> None:
        self._repository = repository
        self._lifecycle = lifecycle
        self._executor_factory = executor_factory
        self._authority_provider = authority_provider

    async def execute(self, *, user: User, guide: Guide) -> Guide:
        authority = await self._authority_provider.resolve(user=user, guide=guide)
        preparation = await self._lifecycle.prepare(user=user, guide=guide, authority=authority)
        completed_at = datetime.now(UTC)
        try:
            projection, provenance = await execute_verified_guide_runtime(
                factory=self._executor_factory,
                runtime_request=preparation.carrier,
                evaluation_time=completed_at,
            )
        except GuideRuntimeExecutionUnavailableError as error:
            preparation.job.status = AiJobStatus.FAILED
            preparation.job.failure_code = "DEPENDENCY_UNAVAILABLE"
            preparation.job.failure_detail = error.failure.value
            preparation.job.completed_at = completed_at
            await self._repository.session.flush()
            raise

        if not await self._repository.lock_if_current_version(guide=guide):
            preparation.job.status = AiJobStatus.STALE
            preparation.job.completed_at = completed_at
            await self._repository.session.flush()
            raise GuideSyncRuntimeVersionConflictError

        completed = await self._repository.mark_release_completed(
            guide,
            projection=projection,
            model_name=provenance.model_name,
            prompt_version=provenance.prompt_version,
            completed_at=completed_at,
        )
        preparation.job.status = AiJobStatus.COMPLETED
        preparation.job.completed_at = completed_at
        await self._repository.session.flush()
        return completed
