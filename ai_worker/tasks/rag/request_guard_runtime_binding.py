"""Production read projection for the #806 per-request REQUEST authority."""

from __future__ import annotations

from ai_worker.tasks.rag.citation_authorization import (
    GuardDecision,
    GuardOperation,
    OriginRequestGuardBinding,
    RuntimeAuthorizationBinding,
    RuntimeEnvironment,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from rag_runtime.request_authority import RequestAuthorityDecisionOutcome
from rag_runtime.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingObservation,
    compute_request_guard_runtime_binding_ref,
)

__all__ = [
    "RequestGuardRuntimeBindingAssemblyError",
    "build_origin_request_guard_binding",
    "build_runtime_authorization_binding",
]


class RequestGuardRuntimeBindingAssemblyError(ValueError):
    """The persisted observation cannot authorize a REQUEST binding."""


def _require_pass(observation: RequestGuardRuntimeBindingObservation) -> None:
    if observation.actual_decision_outcome is not RequestAuthorityDecisionOutcome.PASS:
        raise RequestGuardRuntimeBindingAssemblyError("REQUEST authority outcome is not PASS")


def build_runtime_authorization_binding(
    observation: RequestGuardRuntimeBindingObservation,
) -> RuntimeAuthorizationBinding:
    """Build the existing Citation runtime binding without caller-supplied facts."""

    _require_pass(observation)
    return RuntimeAuthorizationBinding(
        environment=RuntimeEnvironment(observation.environment.value),
        bundle_id=str(observation.bundle_id),
        bundle_manifest_hash=observation.bundle_manifest_hash,
        request_scope_codes=observation.request_scope_codes,
        scope_manifest_hash=observation.scope_manifest_hash,
    )


def build_origin_request_guard_binding(
    observation: RequestGuardRuntimeBindingObservation,
) -> OriginRequestGuardBinding:
    """Build the existing Citation Origin binding from exact persisted authority."""

    _require_pass(observation)
    reference = compute_request_guard_runtime_binding_ref(observation)
    return OriginRequestGuardBinding(
        guard_ref=ImmutableArtifactRef(
            artifact_code=reference.artifact_code,
            version=reference.version,
            content_sha256=reference.content_sha256,
        ),
        decision=GuardDecision.PASS,
        operation=GuardOperation.REQUEST,
        environment=RuntimeEnvironment(observation.environment.value),
        bundle_id=str(observation.bundle_id),
        bundle_manifest_hash=observation.bundle_manifest_hash,
        request_scope_codes=observation.request_scope_codes,
        scope_manifest_hash=observation.scope_manifest_hash,
    )
