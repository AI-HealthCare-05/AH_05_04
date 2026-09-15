"""Provider-neutral text embedding port and failure-closed result types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.evidence_search import SensitiveVector


class TextEmbeddingFailureReason(StrEnum):
    CONFIGURATION_MISMATCH = "CONFIGURATION_MISMATCH"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    RESPONSE_INVALID = "RESPONSE_INVALID"


@dataclass(frozen=True, slots=True)
class TextEmbeddingSuccess:
    embedding: SensitiveVector
    adapter_artifact_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class TextEmbeddingFailure:
    reason: TextEmbeddingFailureReason


class TextEmbeddingPort(Protocol):
    async def embed(
        self,
        text: SensitiveText,
        *,
        model_ref: str,
        model_version: str,
        dimension: int,
    ) -> TextEmbeddingSuccess | TextEmbeddingFailure:
        raise NotImplementedError
