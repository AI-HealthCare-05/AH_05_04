"""Shared Backend-to-Worker terminal contract for one Guide runtime execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from rag_runtime.guide_release_projection import GuideRuntimeReleaseProjectionOutcome
from rag_runtime.guide_retrieval_binding import GuideRetrievalBindingManifest
from rag_runtime.request_guard_runtime_binding import RequestGuardRuntimeBindingRef

__all__ = [
    "GuideRuntimeExecutionFailure",
    "GuideRuntimeExecutionRequest",
    "GuideRuntimeExecutionResult",
    "GuideRuntimeRequestBundleSourcePort",
    "GuideRuntimeRequestCarrierPort",
    "GuideRuntimeRequestIdentificationPort",
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


class GuideRuntimeRequestIdentificationPort(Protocol):
    medication_identification_id: UUID
    prescription_version_medication_id: UUID
    medication_name_snapshot: str
    strength_text_snapshot: str | None


class GuideRuntimeRequestBundleSourcePort(Protocol):
    source_snapshot_id: UUID
    selected_for_operation: bool


class GuideRuntimeRequestCarrierPort(Protocol):
    job_id: UUID
    execution_context_id: UUID
    prescription_version_id: UUID
    runtime_release_bundle_id: UUID
    runtime_release_bundle_manifest_hash: str
    runtime_execution_manifest_id: UUID
    runtime_execution_manifest_hash: str
    runtime_guard_decision_ref: str
    request_guard_runtime_binding_ref: RequestGuardRuntimeBindingRef
    identifications: tuple[GuideRuntimeRequestIdentificationPort, ...]
    bundle_sources: tuple[GuideRuntimeRequestBundleSourcePort, ...]
    retrieval_binding: GuideRetrievalBindingManifest


@dataclass(frozen=True, slots=True)
class GuideRuntimeExecutionRequest:
    """One already-verified Backend runtime carrier; Worker internals stay hidden."""

    runtime_request: GuideRuntimeRequestCarrierPort
    evaluation_time: datetime


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
