from __future__ import annotations

import inspect

from app.services import guide_runtime_request
from rag_runtime.guide_runtime_execution import (
    GuideRuntimeExecutionRequest,
    GuideRuntimeExecutionResult,
    GuideRuntimeExecutorFactoryPort,
    GuideRuntimeExecutorPort,
)


class _Executor:
    async def execute(self, request: GuideRuntimeExecutionRequest) -> GuideRuntimeExecutionResult:
        raise AssertionError("deterministic contract fake is not invoked in this import-boundary test")


class _Factory:
    def create(self) -> GuideRuntimeExecutorPort:
        return _Executor()


def test_backend_runtime_boundary_consumes_only_shared_execution_contract() -> None:
    factory: GuideRuntimeExecutorFactoryPort = _Factory()

    assert isinstance(factory.create(), _Executor)
    assert "ai_worker" not in inspect.getsource(guide_runtime_request)
