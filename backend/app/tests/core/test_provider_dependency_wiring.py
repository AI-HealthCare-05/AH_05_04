from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from openai import AsyncOpenAI

from app.core.config import Env
from app.core.provider_observability import ProviderCallContext
from app.dependencies import services
from app.services.chat_ai import ChatEngine, ChatProvider
from app.services.guide_ai import GuideGenerator, GuideProvider
from app.services.ocr_ai import OcrStructureProvider, OcrStructurer
from app.services.ocr_engine import OcrEngine


def _context() -> ProviderCallContext:
    return ProviderCallContext(
        trace_id="c" * 32,
        validation_run_id=UUID("61a10000-0000-4000-8000-000000000003"),
        environment=Env.LOCAL,
        validation_enabled=True,
    )


def test_provider_context_dependency_reads_only_server_state() -> None:
    context = _context()
    request = SimpleNamespace(state=SimpleNamespace(provider_call_context=context))

    assert services.get_provider_call_context(request) is context  # type: ignore[arg-type]


def test_ocr_dependencies_inject_distinct_clova_and_openai_descriptors(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context()
    client = cast(AsyncOpenAI, object())
    structurer = cast(OcrStructurer, object())
    provider = cast(OcrStructureProvider, object())
    engine = cast(OcrEngine, object())
    captured: dict[str, dict[str, Any]] = {}

    def construct_openai(received_client: AsyncOpenAI, **kwargs: Any) -> OcrStructureProvider:
        captured["openai"] = {"client": received_client, **kwargs}
        return provider

    def construct_structurer(**_kwargs: Any) -> OcrStructurer:
        return structurer

    def construct_clova(**kwargs: Any) -> OcrEngine:
        captured["clova"] = kwargs
        return engine

    monkeypatch.setattr(services.config, "OCR_STRUCTURE_LLM_ENABLED", True)
    monkeypatch.setattr(services, "OpenAIOcrStructureClient", construct_openai)
    monkeypatch.setattr(services, "LlmPrescriptionStructurer", construct_structurer)
    monkeypatch.setattr(services, "ClovaOcrEngine", construct_clova)

    assert services.get_ocr_structurer(client, context) is structurer
    assert services.get_ocr_engine(structurer, context) is engine
    assert captured["openai"]["context"] is context
    assert captured["openai"]["descriptor"].operation == "OCR_STRUCTURING"
    assert captured["openai"]["descriptor"].prompt_version == "ocr-structure-prompt-v2"
    assert captured["clova"]["context"] is context
    assert captured["clova"]["descriptor"].operation == "PRESCRIPTION_RECOGNITION"
    assert captured["clova"]["descriptor"].prompt_version is None


def test_guide_and_chat_dependencies_inject_operation_descriptors(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context()
    client = cast(AsyncOpenAI, object())
    guide_provider = cast(GuideProvider, object())
    chat_provider = cast(ChatProvider, object())
    guide_generator = cast(GuideGenerator, object())
    chat_engine = cast(ChatEngine, object())
    captured: dict[str, dict[str, Any]] = {}

    def construct_guide_provider(received_client: AsyncOpenAI, **kwargs: Any) -> GuideProvider:
        captured["guide_provider"] = {"client": received_client, **kwargs}
        return guide_provider

    def construct_chat_provider(received_client: AsyncOpenAI, **kwargs: Any) -> ChatProvider:
        captured["chat_provider"] = {"client": received_client, **kwargs}
        return chat_provider

    monkeypatch.setattr(services, "GuideOpenAIResponsesClient", construct_guide_provider)
    monkeypatch.setattr(services, "ChatOpenAIResponsesClient", construct_chat_provider)
    monkeypatch.setattr(services, "GuideGenerator", lambda **_kwargs: guide_generator)
    monkeypatch.setattr(services, "ChatGeneratorEngine", lambda **_kwargs: chat_engine)

    assert services.get_guide_generator(client, context) is guide_generator
    assert services.get_chat_engine(client, context) is chat_engine
    assert captured["guide_provider"]["context"] is context
    assert captured["guide_provider"]["descriptor"].operation == "GUIDE_GENERATION"
    assert captured["guide_provider"]["descriptor"].prompt_version == "guide-prompt-v3"
    assert captured["chat_provider"]["context"] is context
    assert captured["chat_provider"]["descriptor"].operation == "CHAT_GENERATION"
    assert captured["chat_provider"]["descriptor"].prompt_version == "chat-prompt-v2"
