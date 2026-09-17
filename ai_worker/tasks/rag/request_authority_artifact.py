"""REQUEST Authority Artifact Identity Kernel (#713).

Pure, side-effect-free derivation of the `ImmutableArtifactRef` identity for the
three historical request-bound authority records persisted by #713:

```text
REQUEST Guard Authority
Source Decision Authority
Member Decision Authority
```

Scope & Authority Boundaries:
- Identity only: this module derives artifact identity from already-authoritative
  Decision facts. It never evaluates Source/Member eligibility, never computes a
  PASS outcome, and performs no I/O.
- Writer-owned identity: `artifact_code` and `version` are fixed contract constants
  and `content_sha256` is the canonical digest of the semantic projection, so a
  caller cannot choose an arbitrary authority identity.
- Reused contracts: canonical serialization uses the repository's RFC 8785 JCS
  helper (`ai_worker.tasks.evaluation.canonical`), identity uses
  `ImmutableArtifactRef`, outcome/stage vocabulary uses `ObservedDecisionOutcome`
  and `RequestDecisionStage`, and member identity uses `SourceMemberIdentity`.
  No new hash domain is introduced.
- Non-deterministic values (database primary keys, `created_at`, transaction
  timestamps, insertion order) are never part of a projection.
"""

from __future__ import annotations

from enum import StrEnum
from typing import cast
from uuid import UUID

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    is_valid_immutable_artifact_ref,
)
from ai_worker.tasks.rag.guide_evidence_handoff import (
    ObservedDecisionOutcome,
    RequestDecisionStage,
)
from ai_worker.tasks.rag.source_member_identity import (
    SourceMemberIdentity,
    is_valid_source_member_identity,
    source_member_identity_payload,
)

__all__ = [
    "REQUEST_AUTHORITY_ARTIFACT_VERSION",
    "REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE",
    "REQUEST_GUARD_AUTHORITY_PROJECTION_VERSION",
    "REQUEST_MEMBER_DECISION_AUTHORITY_ARTIFACT_CODE",
    "REQUEST_MEMBER_DECISION_AUTHORITY_PROJECTION_VERSION",
    "REQUEST_SOURCE_DECISION_AUTHORITY_ARTIFACT_CODE",
    "REQUEST_SOURCE_DECISION_AUTHORITY_PROJECTION_VERSION",
    "RequestAuthorityArtifactError",
    "RequestAuthorityArtifactReason",
    "compute_request_guard_authority_ref",
    "compute_request_member_decision_authority_ref",
    "compute_request_source_decision_authority_ref",
    "request_guard_authority_projection",
    "request_member_decision_authority_projection",
    "request_source_decision_authority_projection",
]

REQUEST_AUTHORITY_ARTIFACT_VERSION = "1.0"

REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE = "request_guard_authority"
REQUEST_SOURCE_DECISION_AUTHORITY_ARTIFACT_CODE = "request_source_decision_authority"
REQUEST_MEMBER_DECISION_AUTHORITY_ARTIFACT_CODE = "request_member_decision_authority"

REQUEST_GUARD_AUTHORITY_PROJECTION_VERSION = "request-guard-authority-v1"
REQUEST_SOURCE_DECISION_AUTHORITY_PROJECTION_VERSION = "request-source-decision-authority-v1"
REQUEST_MEMBER_DECISION_AUTHORITY_PROJECTION_VERSION = "request-member-decision-authority-v1"


class RequestAuthorityArtifactReason(StrEnum):
    USER_ID_INVALID = "USER_ID_INVALID"
    REQUEST_OPERATION_CODE_INVALID = "REQUEST_OPERATION_CODE_INVALID"
    DECISION_STAGE_INVALID = "DECISION_STAGE_INVALID"
    DECISION_OUTCOME_INVALID = "DECISION_OUTCOME_INVALID"
    REQUEST_GUARD_REF_INVALID = "REQUEST_GUARD_REF_INVALID"
    SOURCE_BINDING_INVALID = "SOURCE_BINDING_INVALID"
    MEMBER_BINDING_INVALID = "MEMBER_BINDING_INVALID"
    MEMBER_IDENTITY_INVALID = "MEMBER_IDENTITY_INVALID"


class RequestAuthorityArtifactError(Exception):
    """Raised fail-closed when authority facts cannot form a canonical artifact identity."""

    def __init__(self, reason: RequestAuthorityArtifactReason) -> None:
        self.reason = reason
        super().__init__(str(reason))


def _is_canonical_code(value: object) -> bool:
    return type(value) is str and len(value) > 0 and value == value.strip()


def _require_user_id(user_id: object) -> UUID:
    if type(user_id) is not UUID:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.USER_ID_INVALID)
    return user_id


def _require_operation_code(request_operation_code: object) -> str:
    if not _is_canonical_code(request_operation_code):
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.REQUEST_OPERATION_CODE_INVALID)
    return cast(str, request_operation_code)


def _require_request_stage(decision_stage: object) -> RequestDecisionStage:
    if decision_stage is not RequestDecisionStage.REQUEST:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.DECISION_STAGE_INVALID)
    return RequestDecisionStage.REQUEST


def _require_outcome(actual_decision_outcome: object) -> ObservedDecisionOutcome:
    if type(actual_decision_outcome) is not ObservedDecisionOutcome:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.DECISION_OUTCOME_INVALID)
    return actual_decision_outcome


def _require_guard_ref(request_guard_ref: object) -> ImmutableArtifactRef:
    if not is_valid_immutable_artifact_ref(request_guard_ref):
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.REQUEST_GUARD_REF_INVALID)
    guard_ref = cast(ImmutableArtifactRef, request_guard_ref)
    if guard_ref.artifact_code != REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.REQUEST_GUARD_REF_INVALID)
    return guard_ref


def _artifact_projection(ref: ImmutableArtifactRef) -> dict[str, JsonValue]:
    return {
        "artifact_code": ref.artifact_code,
        "content_sha256": ref.content_sha256,
        "version": ref.version,
    }


def _digest_ref(*, artifact_code: str, projection: JsonValue) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(
        artifact_code=artifact_code,
        version=REQUEST_AUTHORITY_ARTIFACT_VERSION,
        content_sha256=canonical_sha256(projection),
    )


# ---------------------------------------------------------------------------
# REQUEST Guard
# ---------------------------------------------------------------------------


def request_guard_authority_projection(
    *,
    user_id: UUID,
    request_operation_code: str,
    decision_stage: RequestDecisionStage,
) -> JsonValue:
    """Canonical semantic projection of a REQUEST Guard authority observation."""
    return {
        "decision_stage": _require_request_stage(decision_stage).value,
        "projection_version": REQUEST_GUARD_AUTHORITY_PROJECTION_VERSION,
        "request_operation_code": _require_operation_code(request_operation_code),
        "user_id": str(_require_user_id(user_id)),
    }


def compute_request_guard_authority_ref(
    *,
    user_id: UUID,
    request_operation_code: str,
    decision_stage: RequestDecisionStage,
) -> ImmutableArtifactRef:
    projection = request_guard_authority_projection(
        user_id=user_id,
        request_operation_code=request_operation_code,
        decision_stage=decision_stage,
    )
    return _digest_ref(artifact_code=REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE, projection=projection)


# ---------------------------------------------------------------------------
# Source Decision
# ---------------------------------------------------------------------------


def request_source_decision_authority_projection(
    *,
    request_guard_ref: ImmutableArtifactRef,
    user_id: UUID,
    request_operation_code: str,
    decision_stage: RequestDecisionStage,
    source_snapshot_id: UUID,
    source_code: str,
    source_version: str,
    actual_decision_outcome: ObservedDecisionOutcome,
) -> JsonValue:
    """Canonical semantic projection of a request-bound Source Decision observation."""
    if (
        type(source_snapshot_id) is not UUID
        or not _is_canonical_code(source_code)
        or not _is_canonical_code(source_version)
    ):
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.SOURCE_BINDING_INVALID)

    return {
        "actual_decision_outcome": _require_outcome(actual_decision_outcome).value,
        "decision_stage": _require_request_stage(decision_stage).value,
        "projection_version": REQUEST_SOURCE_DECISION_AUTHORITY_PROJECTION_VERSION,
        "request_guard_ref": _artifact_projection(_require_guard_ref(request_guard_ref)),
        "request_operation_code": _require_operation_code(request_operation_code),
        "source_code": source_code,
        "source_snapshot_id": str(source_snapshot_id),
        "source_version": source_version,
        "user_id": str(_require_user_id(user_id)),
    }


def compute_request_source_decision_authority_ref(
    *,
    request_guard_ref: ImmutableArtifactRef,
    user_id: UUID,
    request_operation_code: str,
    decision_stage: RequestDecisionStage,
    source_snapshot_id: UUID,
    source_code: str,
    source_version: str,
    actual_decision_outcome: ObservedDecisionOutcome,
) -> ImmutableArtifactRef:
    projection = request_source_decision_authority_projection(
        request_guard_ref=request_guard_ref,
        user_id=user_id,
        request_operation_code=request_operation_code,
        decision_stage=decision_stage,
        source_snapshot_id=source_snapshot_id,
        source_code=source_code,
        source_version=source_version,
        actual_decision_outcome=actual_decision_outcome,
    )
    return _digest_ref(artifact_code=REQUEST_SOURCE_DECISION_AUTHORITY_ARTIFACT_CODE, projection=projection)


# ---------------------------------------------------------------------------
# Member Decision
# ---------------------------------------------------------------------------


def request_member_decision_authority_projection(
    *,
    request_guard_ref: ImmutableArtifactRef,
    user_id: UUID,
    request_operation_code: str,
    decision_stage: RequestDecisionStage,
    source_snapshot_id: UUID,
    source_snapshot_member_id: UUID,
    member_identity: SourceMemberIdentity,
    actual_decision_outcome: ObservedDecisionOutcome,
) -> JsonValue:
    """Canonical semantic projection of a request-bound Member Decision observation."""
    if type(source_snapshot_id) is not UUID or type(source_snapshot_member_id) is not UUID:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.MEMBER_BINDING_INVALID)
    if not is_valid_source_member_identity(member_identity):
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.MEMBER_IDENTITY_INVALID)

    return {
        "actual_decision_outcome": _require_outcome(actual_decision_outcome).value,
        "decision_stage": _require_request_stage(decision_stage).value,
        "member_identity": cast(JsonValue, source_member_identity_payload(member_identity)),
        "projection_version": REQUEST_MEMBER_DECISION_AUTHORITY_PROJECTION_VERSION,
        "request_guard_ref": _artifact_projection(_require_guard_ref(request_guard_ref)),
        "request_operation_code": _require_operation_code(request_operation_code),
        "source_snapshot_id": str(source_snapshot_id),
        "source_snapshot_member_id": str(source_snapshot_member_id),
        "user_id": str(_require_user_id(user_id)),
    }


def compute_request_member_decision_authority_ref(
    *,
    request_guard_ref: ImmutableArtifactRef,
    user_id: UUID,
    request_operation_code: str,
    decision_stage: RequestDecisionStage,
    source_snapshot_id: UUID,
    source_snapshot_member_id: UUID,
    member_identity: SourceMemberIdentity,
    actual_decision_outcome: ObservedDecisionOutcome,
) -> ImmutableArtifactRef:
    projection = request_member_decision_authority_projection(
        request_guard_ref=request_guard_ref,
        user_id=user_id,
        request_operation_code=request_operation_code,
        decision_stage=decision_stage,
        source_snapshot_id=source_snapshot_id,
        source_snapshot_member_id=source_snapshot_member_id,
        member_identity=member_identity,
        actual_decision_outcome=actual_decision_outcome,
    )
    return _digest_ref(artifact_code=REQUEST_MEMBER_DECISION_AUTHORITY_ARTIFACT_CODE, projection=projection)
