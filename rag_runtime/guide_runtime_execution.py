"""Shared Backend-to-Worker terminal contract for one Guide runtime execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from rag_runtime.guide_release_projection import GuideRuntimeReleaseProjectionOutcome

__all__ = [
    "GuideRuntimeExecutionFailure",
    "GuideRuntimeExecutionRequest",
    "GuideRuntimeExecutionResult",
    "GuideRuntimeExecutorFactoryPort",
    "GuideRuntimeExecutorPort",
    "GuideRuntimeProviderProvenance",
]


class GuideRuntimeExecutionFailure(StrEnum):
    """Non-sensitive terminal categories for Backend persistence/API selection."""

    INVALID_REQUEST = "INVALID_REQUEST"
    RETRIEVAL_NOT_READY = "RETRIEVAL_NOT_READY"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
    CONTENT_UNAVAILABLE = "CONTENT_UNAVAILABLE"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
    HANDOFF_REJECTED = "HANDOFF_REJECTED"
    AGGREGATE_REJECTED = "AGGREGATE_REJECTED"
    RELEASE_UNAVAILABLE = "RELEASE_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class GuideRuntimeExecutionRequest:
    """One already-verified Backend runtime carrier; Worker internals stay hidden."""

    runtime_request: object
    evaluation_time: object


@dataclass(frozen=True, slots=True)
class GuideRuntimeProviderProvenance:
    """Actual provider execution metadata; absent when no provider call occurred."""

    model_name: str
    prompt_version: str


@dataclass(frozen=True, slots=True)
class GuideRuntimeExecutionResult:
    """Either persistence-ready release output or a typed fail-closed terminal stop."""

    projection: GuideRuntimeReleaseProjectionOutcome | None
    failure: GuideRuntimeExecutionFailure | None
    provider_provenance: GuideRuntimeProviderProvenance | None


class GuideRuntimeExecutorPort(Protocol):
    async def execute(self, request: GuideRuntimeExecutionRequest) -> GuideRuntimeExecutionResult: ...


class GuideRuntimeExecutorFactoryPort(Protocol):
    def create(self) -> GuideRuntimeExecutorPort: ...
