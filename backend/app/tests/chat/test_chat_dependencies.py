from collections.abc import Generator
from typing import cast

import pytest
from openai import AsyncOpenAI

from app.dependencies.services import get_chat_engine, get_chat_service
from app.services.chat_ai import ChatEngine, ChatProvider, ChatReplyInput, ChatReplyOutput


class StubEngine:
    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        raise AssertionError(f"not called: {chat_input}")


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

    def construct_provider(received_client: AsyncOpenAI) -> ChatProvider:
        captured["client"] = received_client
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

    engine = get_chat_engine(client)

    assert engine is expected_engine
    assert captured == {
        "client": client,
        "provider": provider,
        "model": "configured-model",
        "timeout_seconds": 12.5,
    }


def test_get_chat_service_requires_and_injects_chat_engine() -> None:
    prescription_repository = object()
    chat_repository = object()
    engine: ChatEngine = StubEngine()

    service = get_chat_service(prescription_repository, chat_repository, engine)  # type: ignore[arg-type]

    assert service._engine is engine
