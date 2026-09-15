"""OpenAI concrete adapter for text-embedding-3-large."""

from __future__ import annotations

import logging
from typing import Any

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.evidence_search import SensitiveVector
from ai_worker.tasks.rag.text_embedding import (
    TextEmbeddingFailure,
    TextEmbeddingFailureReason,
    TextEmbeddingPort,
    TextEmbeddingSuccess,
)

logger = logging.getLogger(__name__)

EXPECTED_MODEL_REF = "openai:text-embedding-3-large"
EXPECTED_MODEL_VERSION = "text-embedding-3-large"
EXPECTED_DIMENSION = 1536


class OpenAITextEmbeddingAdapter(TextEmbeddingPort):
    """Concrete adapter for OpenAI text-embedding-3-large generating 1536-dimensional vectors."""

    def __init__(
        self,
        client: Any,
        adapter_artifact_ref: ImmutableArtifactRef,
    ) -> None:
        self._client = client
        self._adapter_artifact_ref = adapter_artifact_ref

    async def embed(
        self,
        text: SensitiveText,
        *,
        model_ref: str,
        model_version: str,
        dimension: int,
    ) -> TextEmbeddingSuccess | TextEmbeddingFailure:
        if (
            model_ref != EXPECTED_MODEL_REF
            or model_version != EXPECTED_MODEL_VERSION
            or dimension != EXPECTED_DIMENSION
        ):
            return TextEmbeddingFailure(TextEmbeddingFailureReason.CONFIGURATION_MISMATCH)

        try:
            response = await self._client.embeddings.create(
                input=text.reveal(),
                model=EXPECTED_MODEL_VERSION,
                dimensions=EXPECTED_DIMENSION,
                encoding_format="float",
            )
        except Exception as exc:
            logger.error("OpenAI embedding request failed: %s", exc.__class__.__name__)
            return TextEmbeddingFailure(TextEmbeddingFailureReason.DEPENDENCY_ERROR)

        try:
            if not hasattr(response, "data") or len(response.data) != 1:
                return TextEmbeddingFailure(TextEmbeddingFailureReason.RESPONSE_INVALID)

            item = response.data[0]
            if getattr(item, "index", 0) != 0:
                return TextEmbeddingFailure(TextEmbeddingFailureReason.RESPONSE_INVALID)

            raw_embedding = item.embedding
            if len(raw_embedding) != EXPECTED_DIMENSION:
                return TextEmbeddingFailure(TextEmbeddingFailureReason.RESPONSE_INVALID)

            vector = SensitiveVector(raw_embedding)
        except Exception:
            return TextEmbeddingFailure(TextEmbeddingFailureReason.RESPONSE_INVALID)

        return TextEmbeddingSuccess(
            embedding=vector,
            adapter_artifact_ref=self._adapter_artifact_ref,
        )
