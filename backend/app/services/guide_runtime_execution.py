"""Backend application boundary for the canonical Guide runtime executor."""

from __future__ import annotations

from datetime import datetime

from app.services.guide_runtime_projection import (
    GuideRuntimeProjectionKind,
    project_guide_runtime_release,
)
from rag_runtime.guide_release_projection import GuideRuntimeReleaseProjectionCarrier
from rag_runtime.guide_runtime_execution import (
    GuideRuntimeExecutionFailure,
    GuideRuntimeExecutionRequest,
    GuideRuntimeExecutorFactoryPort,
    GuideRuntimeProviderProvenance,
    GuideRuntimeRequestCarrierPort,
)


class GuideRuntimeExecutionUnavailableError(RuntimeError):
    """The shared runtime stopped before producing a persistence-safe release."""

    def __init__(self, failure: GuideRuntimeExecutionFailure) -> None:
        super().__init__("Guide runtime execution did not produce a public release")
        self.failure = failure


async def execute_verified_guide_runtime(
    *,
    factory: GuideRuntimeExecutorFactoryPort,
    runtime_request: GuideRuntimeRequestCarrierPort,
    evaluation_time: datetime,
) -> tuple[GuideRuntimeReleaseProjectionCarrier, GuideRuntimeProviderProvenance]:
    """Execute one verified carrier and return only persistence-safe output."""

    result = await factory.create().execute(
        GuideRuntimeExecutionRequest(
            runtime_request=runtime_request,
            evaluation_time=evaluation_time,
        )
    )
    if result.failure is not None:
        raise GuideRuntimeExecutionUnavailableError(result.failure)

    projection = result.projection
    provenance = result.provider_provenance
    if type(projection) is not GuideRuntimeReleaseProjectionCarrier or provenance is None:
        raise GuideRuntimeExecutionUnavailableError(GuideRuntimeExecutionFailure.RELEASE_UNAVAILABLE)

    public_candidate = project_guide_runtime_release(projection)
    if public_candidate.kind is GuideRuntimeProjectionKind.FAIL_CLOSED_REJECTION:
        raise GuideRuntimeExecutionUnavailableError(GuideRuntimeExecutionFailure.RELEASE_UNAVAILABLE)

    return projection, provenance
