"""Pure contract for the per-request REQUEST Guard Runtime Binding authority (#806).

This contract is separate from the #713 ``request_authority`` projection.  #713 identifies
the shared semantic REQUEST authority; this module identifies one authoritative request
instance together with the runtime facts that were actually bound to that request.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from rag_runtime.request_authority import (
    RequestAuthorityArtifactRef,
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
    is_valid_request_authority_artifact_ref,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode

REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_CODE = "request_guard_runtime_binding"
REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_VERSION = "1.0"
REQUEST_GUARD_RUNTIME_BINDING_PROJECTION_VERSION = "request-guard-runtime-binding-v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RequestGuardRuntimeBindingValidationError(ValueError):
    """The request-bound runtime facts are not canonical or exact."""


@dataclass(frozen=True, slots=True)
class RequestGuardRuntimeBindingRef:
    """Content-addressed reference owned by the #806 projection."""

    artifact_code: str
    version: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class RequestGuardRuntimeBindingObservation:
    """Persisted semantic facts required to reconstruct Citation runtime bindings."""

    request_guard_decision_id: UUID
    actual_decision_outcome: RequestAuthorityDecisionOutcome
    user_id: UUID
    request_operation_code: str
    decision_stage: RequestAuthorityDecisionStage
    environment: RuntimeEnvironmentCode
    bundle_id: UUID
    bundle_manifest_hash: str
    request_scope_codes: tuple[str, ...]
    scope_manifest_hash: str
    legacy_request_authority_ref: RequestAuthorityArtifactRef

    def __post_init__(self) -> None:
        _require_uuid(self.request_guard_decision_id, "request_guard_decision_id")
        _require_uuid(self.user_id, "user_id")
        _require_uuid(self.bundle_id, "bundle_id")
        _require_text(self.request_operation_code, "request_operation_code")
        if type(self.actual_decision_outcome) is not RequestAuthorityDecisionOutcome:
            raise RequestGuardRuntimeBindingValidationError("actual_decision_outcome must be PASS or FAIL")
        if self.decision_stage is not RequestAuthorityDecisionStage.REQUEST:
            raise RequestGuardRuntimeBindingValidationError("decision_stage must be REQUEST")
        if type(self.environment) is not RuntimeEnvironmentCode:
            raise RequestGuardRuntimeBindingValidationError("environment must be RuntimeEnvironmentCode")
        _require_sha256(self.bundle_manifest_hash, "bundle_manifest_hash")
        _validate_scopes(self.request_scope_codes)
        expected_scope_hash = canonical_scope_manifest_hash(self.request_scope_codes)
        if self.scope_manifest_hash != expected_scope_hash:
            raise RequestGuardRuntimeBindingValidationError("scope_manifest_hash does not match request_scope_codes")
        if not is_valid_request_authority_artifact_ref(self.legacy_request_authority_ref):
            raise RequestGuardRuntimeBindingValidationError("legacy_request_authority_ref is invalid")
        if self.legacy_request_authority_ref.artifact_code != "request_guard_authority":
            raise RequestGuardRuntimeBindingValidationError("legacy_request_authority_ref is not a Guard ref")


def _require_uuid(value: object, field_name: str) -> None:
    if type(value) is not UUID:
        raise RequestGuardRuntimeBindingValidationError(f"{field_name} must be a UUID")


def _require_text(value: object, field_name: str) -> None:
    if type(value) is not str or not value or value != value.strip() or not unicodedata.is_normalized("NFC", value):
        raise RequestGuardRuntimeBindingValidationError(f"{field_name} must be canonical nonblank text")


def _require_sha256(value: object, field_name: str) -> None:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise RequestGuardRuntimeBindingValidationError(f"{field_name} must be a lowercase SHA-256")


def _validate_scopes(scope_codes: object) -> None:
    if type(scope_codes) is not tuple or not scope_codes:
        raise RequestGuardRuntimeBindingValidationError("request_scope_codes must be a non-empty tuple")
    for code in scope_codes:
        _require_text(code, "request_scope_codes entry")
    if len(scope_codes) != len(set(scope_codes)):
        raise RequestGuardRuntimeBindingValidationError("request_scope_codes must be unique")
    if scope_codes != tuple(sorted(scope_codes, key=lambda code: code.encode("utf-8"))):
        raise RequestGuardRuntimeBindingValidationError("request_scope_codes must be UTF-8 byte sorted")


def canonical_scope_manifest_hash(scope_codes: tuple[str, ...]) -> str:
    """Compute the exact scope hash used by the existing Citation kernel."""

    _validate_scopes(scope_codes)
    encoded = json.dumps(list(scope_codes), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(unicodedata.normalize("NFC", encoded).encode("utf-8")).hexdigest()


def request_guard_runtime_binding_projection(
    observation: RequestGuardRuntimeBindingObservation,
) -> dict[str, object]:
    """Return the deterministic semantic projection for one per-request authority."""

    if type(observation) is not RequestGuardRuntimeBindingObservation:
        raise RequestGuardRuntimeBindingValidationError("observation has an invalid type")
    return {
        "actual_decision_outcome": observation.actual_decision_outcome.value,
        "bundle_id": str(observation.bundle_id),
        "bundle_manifest_hash": observation.bundle_manifest_hash,
        "decision_stage": observation.decision_stage.value,
        "environment": observation.environment.value,
        "legacy_request_authority_ref": {
            "artifact_code": observation.legacy_request_authority_ref.artifact_code,
            "content_sha256": observation.legacy_request_authority_ref.content_sha256,
            "version": observation.legacy_request_authority_ref.version,
        },
        "projection_version": REQUEST_GUARD_RUNTIME_BINDING_PROJECTION_VERSION,
        "request_guard_decision_id": str(observation.request_guard_decision_id),
        "request_operation_code": observation.request_operation_code,
        "request_scope_codes": list(observation.request_scope_codes),
        "scope_manifest_hash": observation.scope_manifest_hash,
        "user_id": str(observation.user_id),
    }


def compute_request_guard_runtime_binding_ref(
    observation: RequestGuardRuntimeBindingObservation,
) -> RequestGuardRuntimeBindingRef:
    projection = request_guard_runtime_binding_projection(observation)
    encoded = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return RequestGuardRuntimeBindingRef(
        artifact_code=REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_CODE,
        version=REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_VERSION,
        content_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )


__all__ = [
    "REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_CODE",
    "REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_VERSION",
    "REQUEST_GUARD_RUNTIME_BINDING_PROJECTION_VERSION",
    "RequestGuardRuntimeBindingObservation",
    "RequestGuardRuntimeBindingRef",
    "RequestGuardRuntimeBindingValidationError",
    "canonical_scope_manifest_hash",
    "compute_request_guard_runtime_binding_ref",
    "request_guard_runtime_binding_projection",
]
