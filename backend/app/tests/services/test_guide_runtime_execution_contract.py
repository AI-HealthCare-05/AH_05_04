from __future__ import annotations

import asyncio
import inspect

from app.services import guide_runtime_request
from rag_runtime.guide_release_projection import GuideRuntimeReleaseProjectionUnavailable
from rag_runtime.guide_runtime_execution import (
    GuideRuntimeExecutionFailure,
    GuideRuntimeExecutionRequest,
    GuideRuntimeExecutionResult,
    GuideRuntimeExecutorFactoryPort,
    GuideRuntimeExecutorPort,
    GuideRuntimeProviderProvenance,
)


class _Executor(GuideRuntimeExecutorPort):
    def __init__(self, result: GuideRuntimeExecutionResult) -> None:
        self.result = result

    async def execute(self, request: GuideRuntimeExecutionRequest) -> GuideRuntimeExecutionResult:
        return self.result


class _Factory:
    def __init__(self, result: GuideRuntimeExecutionResult) -> None:
        self.executor = _Executor(result)

    def create(self) -> GuideRuntimeExecutorPort:
        return self.executor


def test_backend_runtime_boundary_consumes_shared_success_failure_and_provenance_contract() -> None:
    success = GuideRuntimeExecutionResult.succeeded(
        GuideRuntimeReleaseProjectionUnavailable(), GuideRuntimeProviderProvenance("actual-model", "applied-prompt")
    )
    factory: GuideRuntimeExecutorFactoryPort = _Factory(success)
    consumed = asyncio.run(factory.create().execute(None))  # type: ignore[arg-type]

    assert consumed.provider_provenance == GuideRuntimeProviderProvenance("actual-model", "applied-prompt")
    failure = GuideRuntimeExecutionResult.failed(GuideRuntimeExecutionFailure.IDENTITY_UNRESOLVED)
    assert (
        asyncio.run(_Factory(failure).create().execute(None)).failure
        is GuideRuntimeExecutionFailure.IDENTITY_UNRESOLVED
    )  # type: ignore[arg-type]
    assert "ai_worker" not in inspect.getsource(guide_runtime_request)
