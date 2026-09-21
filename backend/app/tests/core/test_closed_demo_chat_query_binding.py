"""Regression coverage for the CLOSED_DEMO Chat query-binding authority."""

from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace
from typing import cast

import pytest

from ai_worker.adapters.openai_text_embedding import OpenAITextEmbeddingAdapter
from app.core.closed_demo_retrieval import (
    ClosedDemoRetrievalConfigurationError,
    ClosedDemoRetrievalDependencies,
    ClosedDemoRetrievalService,
)
from app.dependencies import services
from rag_runtime.closed_demo_chat_query_binding import (
    ApprovedClosedDemoChatQueryHmacKey,
    ClosedDemoChatQueryFingerprintProducer,
    ClosedDemoChatQueryHmacKeyProvider,
    ClosedDemoChatQueryVerificationSuccess,
    ClosedDemoChatQueryVerifier,
    compute_closed_demo_chat_query_verifier_artifact_ref,
)
from rag_runtime.query_binding import SensitiveText


class _RetainedChatKeys(ClosedDemoChatQueryHmacKeyProvider):
    def active_key_version(self) -> str:
        return "closed-demo-chat-query-hmac-key@1"

    def key_for_version(self, key_version: str) -> ApprovedClosedDemoChatQueryHmacKey | None:
        if key_version == "closed-demo-chat-query-hmac-key@1":
            return ApprovedClosedDemoChatQueryHmacKey(b"closed-demo-chat-test-key")
        return None


def test_closed_demo_chat_authority_binds_the_exact_free_form_question_with_its_own_identity() -> None:
    """Catches substituting the Guide authority or preimage for a Chat question."""
    question = SensitiveText("이 약을 복용할 때 주의할 점은 무엇인가요?")
    producer = ClosedDemoChatQueryFingerprintProducer(_RetainedChatKeys())

    fingerprint = producer.produce(question)
    result = ClosedDemoChatQueryVerifier(_RetainedChatKeys()).verify(question, fingerprint)

    expected_digest = hmac.new(
        b"closed-demo-chat-test-key",
        b"closed-demo-chat-query-hmac@1\x00" + "이 약을 복용할 때 주의할 점은 무엇인가요?".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert fingerprint.algorithm == "HMAC-SHA-256"
    assert fingerprint.key_version == "closed-demo-chat-query-hmac-key@1"
    assert fingerprint.digest == expected_digest
    assert result == ClosedDemoChatQueryVerificationSuccess(
        fingerprint,
        compute_closed_demo_chat_query_verifier_artifact_ref(),
    )
    artifact = compute_closed_demo_chat_query_verifier_artifact_ref()
    assert artifact.artifact_code == "closed-demo-chat-query-binding-verifier"
    assert artifact.version == "1.0.0"


def test_closed_demo_retrieval_rejects_a_guide_query_authority() -> None:
    """Catches an accidental return to the Guide B2 producer/verifier at this boundary."""
    guide_keys = services.GuideQueryHmacKeyDependency(
        "guide-query-hmac-key@1",
        services.ApprovedGuideQueryHmacKey(b"synthetic-guide-key"),
    )

    with pytest.raises(ClosedDemoRetrievalConfigurationError, match="Chat query authority"):
        ClosedDemoRetrievalService(
            dependencies=cast(ClosedDemoRetrievalDependencies, SimpleNamespace(session_factory=object())),
            text_embedding_adapter=cast(OpenAITextEmbeddingAdapter, object()),
            fingerprint_producer=services.get_guide_query_fingerprint_producer(guide_keys),  # type: ignore[arg-type]
            binding_verifier=services.get_guide_query_binding_verifier(guide_keys),  # type: ignore[arg-type]
        )
