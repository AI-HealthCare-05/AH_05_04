"""Reusable deterministic shared-contract fakes for Backend/Worker Guide tests."""

from rag_runtime.guide_release_projection import GuideRuntimeReleaseProjectionOutcome
from rag_runtime.guide_runtime_execution import (
    GuideRuntimeExecutionFailure,
    GuideRuntimeExecutionRequest,
    GuideRuntimeExecutionResult,
    GuideRuntimeExecutorFactoryPort,
    GuideRuntimeExecutorPort,
    GuideRuntimeProviderProvenance,
)


class DeterministicGuideRuntimeExecutor(GuideRuntimeExecutorPort):
    def __init__(self, result: GuideRuntimeExecutionResult) -> None:
        self.result = result
        self.calls: list[GuideRuntimeExecutionRequest] = []

    async def execute(self, request: GuideRuntimeExecutionRequest) -> GuideRuntimeExecutionResult:
        self.calls.append(request)
        return self.result


class DeterministicGuideRuntimeExecutorFactory(GuideRuntimeExecutorFactoryPort):
    def __init__(self, result: GuideRuntimeExecutionResult) -> None:
        self.executor = DeterministicGuideRuntimeExecutor(result)

    def create(self) -> GuideRuntimeExecutorPort:
        return self.executor

    @classmethod
    def pass_result(cls, projection: GuideRuntimeReleaseProjectionOutcome):
        return cls(
            GuideRuntimeExecutionResult.succeeded(
                projection, GuideRuntimeProviderProvenance("fake-model", "fake-prompt")
            )
        )

    @classmethod
    def typed_failure(cls, failure: GuideRuntimeExecutionFailure):
        return cls(GuideRuntimeExecutionResult.failed(failure))
