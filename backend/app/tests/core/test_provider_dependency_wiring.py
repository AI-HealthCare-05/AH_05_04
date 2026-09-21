from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.core.closed_demo_retrieval import ClosedDemoRetrievalService
from app.core.config import Env
from app.core.provider_observability import ProviderCallContext
from app.dependencies import services
from app.services.chat_ai import ChatEngine, ChatProvider
from app.services.guide_ai import GuideGenerator, GuideProvider
from app.services.ocr_ai import OcrStructureProvider, OcrStructurer
from app.services.ocr_engine import OcrEngine
from rag_runtime.closed_demo_chat_query_binding import (
    ClosedDemoChatQueryVerificationSuccess,
    ClosedDemoChatQueryVerifier,
)
from rag_runtime.guide_query_binding import (
    GuideQueryFingerprintDependencyError,
    ProductionQueryBindingVerifier,
    QueryBindingFailureReason,
    QueryBindingVerificationFailure,
    QueryBindingVerificationSuccess,
    QueryFingerprint,
    SensitiveText,
)


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


def test_provider_dependency_wiring_rejects_missing_context() -> None:
    with pytest.raises(ValueError, match="Provider call context is required"):
        services._provider_observability_kwargs(
            None,  # type: ignore[arg-type]
            provider=services.Provider.OPENAI,
            operation=services.ProviderOperation.CHAT_GENERATION,
            prompt_version="chat-prompt-v2",
        )


def test_guide_query_hmac_dependencies_assemble_the_same_typed_key_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services.config, "GUIDE_QUERY_HMAC_KEY", SecretStr("synthetic-guide-query-hmac-key"))
    monkeypatch.setattr(services.config, "GUIDE_QUERY_HMAC_KEY_VERSION", "guide-query-hmac-key@1")

    key_dependency = services.get_guide_query_hmac_key_dependency()
    producer = services.get_guide_query_fingerprint_producer(key_dependency)
    verifier = services.get_guide_query_binding_verifier(key_dependency)
    fingerprint = producer.produce(SensitiveText("합성 복약 정보"))

    result = verifier.verify(SensitiveText("합성 복약 정보"), fingerprint)

    assert isinstance(result, QueryBindingVerificationSuccess)
    assert result.query_fingerprint == fingerprint
    assert isinstance(verifier, ProductionQueryBindingVerifier)
    assert "synthetic-guide-query-hmac-key" not in repr(key_dependency)


def test_guide_query_hmac_dependency_maps_missing_secret_to_verifier_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(services.config, "GUIDE_QUERY_HMAC_KEY", None)

    dependency = services.get_guide_query_hmac_key_dependency()
    result = services.get_guide_query_binding_verifier(dependency).verify(
        SensitiveText("합성 복약 정보"),
        QueryFingerprint("HMAC-SHA-256", "guide-query-hmac-key@1", "0" * 64),
    )

    assert result == QueryBindingVerificationFailure(QueryBindingFailureReason.DEPENDENCY_ERROR)


def test_guide_query_hmac_dependency_rejects_blank_secret_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "   "
    monkeypatch.setattr(services.config, "GUIDE_QUERY_HMAC_KEY", SecretStr(secret))

    dependency = services.get_guide_query_hmac_key_dependency()

    with pytest.raises(GuideQueryFingerprintDependencyError) as raised:
        services.get_guide_query_fingerprint_producer(dependency).produce(SensitiveText("합성 복약 정보"))

    assert secret not in str(raised.value)


def test_closed_demo_chat_query_hmac_dependencies_use_the_separate_chat_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "synthetic-closed-demo-chat-query-hmac-key"
    monkeypatch.setattr(services.config, "CHAT_CLOSED_DEMO_QUERY_HMAC_KEY", SecretStr(secret))
    monkeypatch.setattr(
        services.config,
        "CHAT_CLOSED_DEMO_QUERY_HMAC_KEY_VERSION",
        "closed-demo-chat-query-hmac-key@1",
    )

    dependency = services.get_closed_demo_chat_query_hmac_key_dependency()
    producer = services.get_closed_demo_chat_query_fingerprint_producer(dependency)
    verifier = services.get_closed_demo_chat_query_binding_verifier(dependency)
    fingerprint = producer.produce(SensitiveText("복약 후 졸릴 수 있나요?"))

    result = verifier.verify(SensitiveText("복약 후 졸릴 수 있나요?"), fingerprint)

    assert isinstance(result, ClosedDemoChatQueryVerificationSuccess)
    assert isinstance(verifier, ClosedDemoChatQueryVerifier)
    assert fingerprint.key_version == "closed-demo-chat-query-hmac-key@1"
    assert secret not in repr(dependency)


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
    assert captured["openai"]["descriptor"].prompt_version == "ocr-structure-prompt-v3"
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
    assert captured["chat_provider"]["descriptor"].prompt_version == "chat-prompt-v6"


def test_closed_demo_chat_dependency_records_the_v7_prompt_version(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context()
    client = cast(AsyncOpenAI, object())
    retriever = cast(ClosedDemoRetrievalService, object())
    provider = cast(ChatProvider, object())
    engine = cast(ChatEngine, object())
    captured: dict[str, Any] = {}

    def construct_chat_provider(received_client: AsyncOpenAI, **kwargs: Any) -> ChatProvider:
        captured["client"] = received_client
        captured["descriptor"] = kwargs["descriptor"]
        return provider

    def construct_chat_engine(**kwargs: Any) -> ChatEngine:
        captured["retriever"] = kwargs["closed_demo_retriever"]
        return engine

    monkeypatch.setattr(services, "ChatOpenAIResponsesClient", construct_chat_provider)
    monkeypatch.setattr(services, "ChatGeneratorEngine", construct_chat_engine)

    assert services.get_chat_engine(client, context, retriever) is engine
    assert captured["client"] is client
    assert captured["retriever"] is retriever
    assert captured["descriptor"].prompt_version == "chat-prompt-v7-closed-demo-evidence"
