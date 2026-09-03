from collections.abc import Generator
from typing import cast
from uuid import UUID

import pytest
from openai import AsyncOpenAI

from app.core.config import Env
from app.core.provider_observability import ProviderCallContext, ProviderCallDescriptor
from app.dependencies.services import get_chat_engine, get_chat_service
from app.services.chat_ai import ChatEngine, ChatProvider, ChatReplyInput, ChatReplyOutput


class StubEngine:
    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        raise AssertionError(f"not called: {chat_input}")


def _context() -> ProviderCallContext:
    return ProviderCallContext(
        trace_id="d" * 32,
        validation_run_id=UUID("61a10000-0000-4000-8000-000000000003"),
        environment=Env.LOCAL,
        validation_enabled=True,
    )


@pytest.fixture(scope="session", autouse=True)
def initialize_database() -> Generator[None]:
    yield


@pytest.fixture(autouse=True)
def isolate_database() -> Generator[None]:
    yield


def test_get_chat_engine_wires_process_client_and_existing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.dependencies import services

    client = cast(AsyncOpenAI, object())
    provider = cast(ChatProvider, object())
    expected_engine: ChatEngine = StubEngine()
    captured: dict[str, object] = {}

    def construct_provider(received_client: AsyncOpenAI, **kwargs: object) -> ChatProvider:
        captured["client"] = received_client
        captured.update(kwargs)
        return provider

    def construct_engine(*, provider: ChatProvider, model: str, timeout_seconds: float) -> ChatEngine:
        captured["provider"] = provider
        captured["model"] = model
        captured["timeout_seconds"] = timeout_seconds
        return expected_engine

    monkeypatch.setattr(services, "ChatOpenAIResponsesClient", construct_provider)
    monkeypatch.setattr(services, "ChatGeneratorEngine", construct_engine)
    monkeypatch.setattr(services.config, "OPENAI_MODEL", "configured-model")
    monkeypatch.setattr(services.config, "OPENAI_TIMEOUT_SECONDS", 12.5)

    context = _context()
    engine = get_chat_engine(client, context)
    descriptor = cast(ProviderCallDescriptor, captured["descriptor"])

    assert engine is expected_engine
    assert captured == {
        "client": client,
        "context": context,
        "descriptor": descriptor,
        "provider": provider,
        "model": "configured-model",
        "timeout_seconds": 12.5,
    }
    assert descriptor.operation == "CHAT_GENERATION"


def test_get_chat_service_requires_and_injects_chat_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.dependencies import services

    prescription_repository = object()
    chat_repository = object()
    engine: ChatEngine = StubEngine()
    monkeypatch.setattr(services.config, "CHAT_HISTORY_CONTEXT_ENABLED", True)

    service = get_chat_service(prescription_repository, chat_repository, engine)  # type: ignore[arg-type]

    assert service._engine is engine
    assert service._history_context_enabled is True
