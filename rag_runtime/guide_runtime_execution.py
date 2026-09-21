"""Shared Backend-to-Worker terminal contract for one Guide runtime execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from rag_runtime.guide_release_projection import (
    GuideRuntimeReleaseProjectionCarrier,
    GuideRuntimeReleaseProjectionOutcome,
)
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
    PROVIDER_PROVENANCE_UNAVAILABLE = "PROVIDER_PROVENANCE_UNAVAILABLE"


class GuideRuntimeRequestIdentificationPort(Protocol):
    @property
    def medication_identification_id(self) -> UUID: ...
    @property
    def prescription_version_medication_id(self) -> UUID: ...
    @property
    def medication_name_snapshot(self) -> str: ...
    @property
    def strength_text_snapshot(self) -> str | None: ...


class GuideRuntimeRequestBundleSourcePort(Protocol):
    @property
    def source_snapshot_id(self) -> UUID: ...
    @property
    def selected_for_operation(self) -> bool: ...


class GuideRuntimeRequestCarrierPort(Protocol):
    @property
    def job_id(self) -> UUID: ...
    @property
    def execution_context_id(self) -> UUID: ...
    @property
    def prescription_version_id(self) -> UUID: ...
    @property
    def runtime_release_bundle_id(self) -> UUID: ...
    @property
    def runtime_release_bundle_manifest_hash(self) -> str: ...
    @property
    def runtime_execution_manifest_id(self) -> UUID: ...
    @property
    def runtime_execution_manifest_hash(self) -> str: ...
    @property
    def runtime_guard_decision_ref(self) -> str: ...
    @property
    def request_guard_runtime_binding_ref(self) -> RequestGuardRuntimeBindingRef: ...
    @property
    def identifications(self) -> tuple[GuideRuntimeRequestIdentificationPort, ...]: ...
    @property
    def bundle_sources(self) -> tuple[GuideRuntimeRequestBundleSourcePort, ...]: ...
    @property
    def retrieval_binding(self) -> GuideRetrievalBindingManifest: ...


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

    def __post_init__(self) -> None:
        success = self.projection is not None
        if success == (self.failure is not None):
            raise ValueError("execution result must be exactly one of success or failure")
        if success != (self.provider_provenance is not None):
            raise ValueError("only success may carry non-null provider provenance")

    @classmethod
    def succeeded(
        cls, projection: GuideRuntimeReleaseProjectionCarrier, provenance: GuideRuntimeProviderProvenance
    ) -> GuideRuntimeExecutionResult:
        if type(projection) is not GuideRuntimeReleaseProjectionCarrier:
            raise ValueError("success requires an available release projection")
        return cls(projection, None, provenance)

    @classmethod
    def failed(cls, failure: GuideRuntimeExecutionFailure) -> GuideRuntimeExecutionResult:
        return cls(None, failure, None)


class GuideRuntimeExecutorPort(Protocol):
    async def execute(self, request: GuideRuntimeExecutionRequest) -> GuideRuntimeExecutionResult: ...


class GuideRuntimeExecutorFactoryPort(Protocol):
    def create(self) -> GuideRuntimeExecutorPort: ...
