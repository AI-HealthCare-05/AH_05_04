from typing import cast

import pytest
from openai import AsyncOpenAI

from app.dependencies import services
from app.services.ocr_ai import (
    OcrStructureProvider,
    OcrStructurer,
)


def test_get_ocr_structurer_uses_rule_based_path_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = cast(AsyncOpenAI, object())
    expected_structurer = cast(OcrStructurer, object())

    monkeypatch.setattr(
        services.config,
        "OCR_STRUCTURE_LLM_ENABLED",
        False,
    )
    monkeypatch.setattr(
        services,
        "RuleBasedPrescriptionStructurer",
        lambda: expected_structurer,
    )

    def fail_if_provider_is_created(
        _client: AsyncOpenAI,
    ) -> OcrStructureProvider:
        raise AssertionError("LLM이 비활성화되면 OpenAI provider를 생성하면 안 됩니다.")

    monkeypatch.setattr(
        services,
        "OpenAIOcrStructureClient",
        fail_if_provider_is_created,
    )

    structurer = services.get_ocr_structurer(client)

    assert structurer is expected_structurer


def test_get_ocr_structurer_uses_llm_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = cast(AsyncOpenAI, object())
    provider = cast(OcrStructureProvider, object())
    expected_structurer = cast(OcrStructurer, object())
    captured: dict[str, object] = {}

    def construct_provider(
        received_client: AsyncOpenAI,
    ) -> OcrStructureProvider:
        captured["client"] = received_client
        return provider

    def construct_structurer(
        *,
        provider: OcrStructureProvider,
        model: str,
        timeout_seconds: float,
    ) -> OcrStructurer:
        captured["provider"] = provider
        captured["model"] = model
        captured["timeout_seconds"] = timeout_seconds
        return expected_structurer

    monkeypatch.setattr(
        services.config,
        "OCR_STRUCTURE_LLM_ENABLED",
        True,
    )
    monkeypatch.setattr(
        services.config,
        "OCR_STRUCTURE_MODEL",
        "configured-ocr-model",
    )
    monkeypatch.setattr(
        services.config,
        "OCR_STRUCTURE_TIMEOUT_SECONDS",
        12.5,
    )
    monkeypatch.setattr(
        services,
        "OpenAIOcrStructureClient",
        construct_provider,
    )
    monkeypatch.setattr(
        services,
        "LlmPrescriptionStructurer",
        construct_structurer,
    )

    structurer = services.get_ocr_structurer(client)

    assert structurer is expected_structurer
    assert captured == {
        "client": client,
        "provider": provider,
        "model": "configured-ocr-model",
        "timeout_seconds": 12.5,
    }
