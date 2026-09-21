from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime

from app.services import guide_runtime_request
from app.services.guide_runtime_request import GuideRuntimeRequestCarrier
from rag_runtime.guide_release_projection import (
    GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
    GuideRuntimeApprovedFallback,
    GuideRuntimeFallbackCode,
    GuideRuntimeReleaseDecision,
    GuideRuntimeReleaseProjectionCarrier,
)
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
        self.requests: list[GuideRuntimeExecutionRequest] = []

    async def execute(self, request: GuideRuntimeExecutionRequest) -> GuideRuntimeExecutionResult:
        self.requests.append(request)
        return self.result


class _Factory:
    def __init__(self, result: GuideRuntimeExecutionResult) -> None:
        self.executor = _Executor(result)

    def create(self) -> GuideRuntimeExecutorPort:
        return self.executor


def test_backend_runtime_boundary_consumes_shared_success_failure_and_provenance_contract() -> None:
    success = GuideRuntimeExecutionResult.succeeded(
        GuideRuntimeReleaseProjectionCarrier(
            contract_version=GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
            release_decision=GuideRuntimeReleaseDecision.LIMITED,
            is_current=True,
            answer=None,
            fallback=GuideRuntimeApprovedFallback(GuideRuntimeFallbackCode.NO_APPROVED_EVIDENCE, "안전한 대체 안내"),
            citations=(),
        ),
        GuideRuntimeProviderProvenance("actual-model", "applied-prompt"),
    )
    factory: GuideRuntimeExecutorFactoryPort = _Factory(success)
    request = GuideRuntimeExecutionRequest(
        runtime_request=object.__new__(GuideRuntimeRequestCarrier),
        evaluation_time=datetime.now(UTC),
    )
    executor = factory.create()
    consumed = asyncio.run(executor.execute(request))

    assert consumed.provider_provenance == GuideRuntimeProviderProvenance("actual-model", "applied-prompt")
    assert isinstance(executor, _Executor)
    assert executor.requests == [request]
    failure = GuideRuntimeExecutionResult.failed(GuideRuntimeExecutionFailure.IDENTITY_UNRESOLVED)
    assert (
        asyncio.run(_Factory(failure).create().execute(request)).failure
        is GuideRuntimeExecutionFailure.IDENTITY_UNRESOLVED
    )
    assert "ai_worker" not in inspect.getsource(guide_runtime_request)
