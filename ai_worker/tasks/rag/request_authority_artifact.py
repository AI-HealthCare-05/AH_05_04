"""REQUEST Authority Artifact Identity projection for AI Worker kernels (#713).

계약 정본은 `rag_runtime.request_authority`입니다. Backend Repository와 AI Worker가 같은 의미를
소비하므로 wire contract와 canonical identity 계산은 두 이미지에 함께 복사되는 공유 순수
package가 소유하고(`PD-175-20260910` 경계 유지), 이 모듈은 기존 AI Worker kernel 타입과 그
공유 계약 사이의 얇은 projection만 담당합니다.

즉 여기에는 hash 구현이 없습니다. `ImmutableArtifactRef` / `ObservedDecisionOutcome` /
`RequestDecisionStage` / `SourceMemberIdentity`를 공유 wire 타입으로 옮긴 뒤 계산은 전부
`rag_runtime`에 위임하므로, artifact identity는 Backend와 AI Worker에서 항상 동일합니다.

`ImmutableArtifactRef`, `ObservedDecisionOutcome`, `RequestDecisionStage`, `SourceMemberIdentity`,
`SourceMemberKind`의 의미는 바꾸지 않습니다.
"""

from __future__ import annotations

from uuid import UUID

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
    SourceMemberKind,
)
from rag_runtime.request_authority import (
    REQUEST_AUTHORITY_ARTIFACT_VERSION,
    REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE,
    REQUEST_GUARD_AUTHORITY_PROJECTION_VERSION,
    REQUEST_MEMBER_DECISION_AUTHORITY_ARTIFACT_CODE,
    REQUEST_MEMBER_DECISION_AUTHORITY_PROJECTION_VERSION,
    REQUEST_SOURCE_DECISION_AUTHORITY_ARTIFACT_CODE,
    REQUEST_SOURCE_DECISION_AUTHORITY_PROJECTION_VERSION,
    RequestAuthorityArtifactError,
    RequestAuthorityArtifactReason,
    RequestAuthorityArtifactRef,
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
    RequestAuthorityMemberIdentity,
    RequestAuthorityMemberKind,
)
from rag_runtime.request_authority import (
    compute_request_guard_authority_ref as _shared_guard_ref,
)
from rag_runtime.request_authority import (
    compute_request_member_decision_authority_ref as _shared_member_ref,
)
from rag_runtime.request_authority import (
    compute_request_source_decision_authority_ref as _shared_source_ref,
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
    "shared_artifact_ref",
    "shared_member_identity",
    "worker_artifact_ref",
    "worker_member_identity",
]

_KIND_TO_SHARED: dict[SourceMemberKind, RequestAuthorityMemberKind] = {
    SourceMemberKind.ENDPOINT_OPERATION: RequestAuthorityMemberKind.ENDPOINT_OPERATION,
    SourceMemberKind.ARTIFACT_MEMBER: RequestAuthorityMemberKind.ARTIFACT_MEMBER,
}
_KIND_FROM_SHARED: dict[RequestAuthorityMemberKind, SourceMemberKind] = {
    shared: worker for worker, shared in _KIND_TO_SHARED.items()
}

_OUTCOME_TO_SHARED: dict[ObservedDecisionOutcome, RequestAuthorityDecisionOutcome] = {
    ObservedDecisionOutcome.PASS: RequestAuthorityDecisionOutcome.PASS,
    ObservedDecisionOutcome.FAIL: RequestAuthorityDecisionOutcome.FAIL,
}


def shared_artifact_ref(value: object) -> RequestAuthorityArtifactRef:
    """AI Worker `ImmutableArtifactRef`를 공유 wire ref로 옮깁니다."""
    if not is_valid_immutable_artifact_ref(value):
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.REQUEST_GUARD_REF_INVALID)
    assert isinstance(value, ImmutableArtifactRef)
    return RequestAuthorityArtifactRef(
        artifact_code=value.artifact_code,
        version=value.version,
        content_sha256=value.content_sha256,
    )


def worker_artifact_ref(value: RequestAuthorityArtifactRef) -> ImmutableArtifactRef:
    """공유 wire ref를 AI Worker `ImmutableArtifactRef`로 되돌립니다."""
    return ImmutableArtifactRef(
        artifact_code=value.artifact_code,
        version=value.version,
        content_sha256=value.content_sha256,
    )


def shared_member_identity(value: object) -> RequestAuthorityMemberIdentity:
    """`SourceMemberIdentity`를 공유 wire identity로 옮깁니다 (lossless)."""
    if type(value) is not SourceMemberIdentity or type(value.member_kind) is not SourceMemberKind:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.MEMBER_IDENTITY_INVALID)
    shared_kind = _KIND_TO_SHARED.get(value.member_kind)
    if shared_kind is None:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.MEMBER_IDENTITY_INVALID)
    return RequestAuthorityMemberIdentity(
        member_kind=shared_kind,
        endpoint_code=value.endpoint_code,
        operation_code=value.operation_code,
        artifact_code=value.artifact_code,
        artifact_version=value.artifact_version,
    )


def worker_member_identity(value: RequestAuthorityMemberIdentity) -> SourceMemberIdentity:
    """공유 wire identity를 `SourceMemberIdentity`로 되돌립니다 (lossless)."""
    worker_kind = _KIND_FROM_SHARED.get(value.member_kind)
    if worker_kind is None:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.MEMBER_IDENTITY_INVALID)
    return SourceMemberIdentity(
        member_kind=worker_kind,
        endpoint_code=value.endpoint_code,
        operation_code=value.operation_code,
        artifact_code=value.artifact_code,
        artifact_version=value.artifact_version,
    )


def _shared_stage(decision_stage: object) -> RequestAuthorityDecisionStage:
    if decision_stage is not RequestDecisionStage.REQUEST:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.DECISION_STAGE_INVALID)
    return RequestAuthorityDecisionStage.REQUEST


def _shared_outcome(actual_decision_outcome: object) -> RequestAuthorityDecisionOutcome:
    if type(actual_decision_outcome) is not ObservedDecisionOutcome:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.DECISION_OUTCOME_INVALID)
    return _OUTCOME_TO_SHARED[actual_decision_outcome]


def compute_request_guard_authority_ref(
    *,
    user_id: UUID,
    request_operation_code: str,
    decision_stage: RequestDecisionStage,
) -> ImmutableArtifactRef:
    return worker_artifact_ref(
        _shared_guard_ref(
            user_id=user_id,
            request_operation_code=request_operation_code,
            decision_stage=_shared_stage(decision_stage),
        )
    )


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
    return worker_artifact_ref(
        _shared_source_ref(
            request_guard_ref=shared_artifact_ref(request_guard_ref),
            user_id=user_id,
            request_operation_code=request_operation_code,
            decision_stage=_shared_stage(decision_stage),
            source_snapshot_id=source_snapshot_id,
            source_code=source_code,
            source_version=source_version,
            actual_decision_outcome=_shared_outcome(actual_decision_outcome),
        )
    )


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
    return worker_artifact_ref(
        _shared_member_ref(
            request_guard_ref=shared_artifact_ref(request_guard_ref),
            user_id=user_id,
            request_operation_code=request_operation_code,
            decision_stage=_shared_stage(decision_stage),
            source_snapshot_id=source_snapshot_id,
            source_snapshot_member_id=source_snapshot_member_id,
            member_identity=shared_member_identity(member_identity),
            actual_decision_outcome=_shared_outcome(actual_decision_outcome),
        )
    )
