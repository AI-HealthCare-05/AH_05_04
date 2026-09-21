from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app import main
from app.dependencies import services
from rag_runtime.guide_runtime_execution import GuideRuntimeExecutorFactoryPort, GuideRuntimeExecutorPort


class _Factory:
    def create(self) -> GuideRuntimeExecutorPort:
        raise AssertionError("the wiring test must not create an executor")


def test_lifespan_initializer_constructs_and_reuses_one_process_scoped_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    opaque_dependencies = object()
    app.state.guide_runtime_provider_dependencies = opaque_dependencies
    app.state.openai_client = object()
    factory = _Factory()
    calls: list[tuple[object, object, str, float]] = []

    def build(
        dependencies: object,
        *,
        openai_client: object,
        openai_model: str,
        openai_timeout_seconds: float,
    ) -> GuideRuntimeExecutorFactoryPort:
        calls.append((dependencies, openai_client, openai_model, openai_timeout_seconds))
        return factory

    monkeypatch.setattr(main, "build_production_guide_runtime_executor_factory", build)
    monkeypatch.setattr(main.config, "OPENAI_MODEL", "gpt-4o")
    monkeypatch.setattr(main.config, "OPENAI_TIMEOUT_SECONDS", 20.0)

    main.initialize_guide_runtime_executor_factory(app)
    request = SimpleNamespace(app=app)

    assert services.get_guide_runtime_executor_factory(request) is factory  # type: ignore[arg-type]
    assert services.get_guide_runtime_executor_factory(request) is factory  # type: ignore[arg-type]
    assert calls == [(opaque_dependencies, app.state.openai_client, "gpt-4o", 20.0)]


def test_backend_provider_fails_closed_when_factory_was_not_initialized() -> None:
    request = SimpleNamespace(app=FastAPI())

    with pytest.raises(RuntimeError, match="Guide runtime executor factory is not initialized"):
        services.get_guide_runtime_executor_factory(request)  # type: ignore[arg-type]
