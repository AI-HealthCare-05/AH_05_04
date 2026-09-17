"""Shared REQUEST authority wire contract and artifact identity (#713).

Backend와 AI Worker가 함께 소비하는 순수 계약입니다. Backend Repository는 REQUEST authority를
저장·조회하고 AI Worker는 같은 의미를 Guide 실행 경계에서 사용하므로, 양쪽 중 한쪽 package
내부에 두고 반대쪽이 직접 import하는 구조를 쓰지 않습니다. `PD-175-20260910`이 고정한
`backend` → `ai_worker` import 경계를 우회하지 않기 위해, 두 이미지에 함께 복사되는 공유 순수
package인 `rag_runtime`이 이 계약을 소유합니다.

Scope & Authority Boundaries:
- 순수 계약: stdlib만 사용하며 DB I/O, network, clock, session이 없습니다.
- Identity only: 이미 authoritative한 Decision 사실로부터 artifact identity를 도출할 뿐,
  Source/Member eligibility를 평가하거나 PASS 결과를 계산하지 않습니다.
- Writer-owned identity: `artifact_code`와 `version`은 계약 상수이고 `content_sha256`은 semantic
  projection의 canonical digest이므로 caller가 임의의 authority identity를 고를 수 없습니다.
- 단일 정본: #713 authority identity 계산은 이 모듈에만 존재합니다. AI Worker 쪽
  `ai_worker.tasks.rag.request_authority_artifact`는 기존 kernel 타입과의 얇은 projection일 뿐
  별도의 hash 구현을 두지 않습니다.
- 비결정적 값(DB primary key, `created_at`, transaction timestamp, 임의 UUID, 행 삽입 순서)은
  projection에 넣지 않습니다.

Canonicalization: `rag_runtime.identification_preflight.canonical_preflight_manifest_hash`와 같은
방식으로 `json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":"))` 결과를
SHA-256으로 요약합니다. projection key가 모두 ASCII이므로 이 정렬은 저장소의 RFC 8785 JCS
helper와 동일한 바이트열을 만들며, 그 동등성은 계약 테스트로 고정합니다.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

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
    "RequestAuthorityArtifactRef",
    "RequestAuthorityDecisionOutcome",
    "RequestAuthorityDecisionStage",
    "RequestAuthorityMemberIdentity",
    "RequestAuthorityMemberKind",
    "compute_request_guard_authority_ref",
    "compute_request_member_decision_authority_ref",
    "compute_request_source_decision_authority_ref",
    "is_valid_request_authority_artifact_ref",
    "is_valid_request_authority_member_identity",
    "member_kind_from_persisted",
    "persisted_member_kind_value",
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

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class RequestAuthorityDecisionStage(StrEnum):
    REQUEST = "REQUEST"


class RequestAuthorityDecisionOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class RequestAuthorityMemberKind(StrEnum):
    """Source Snapshot Member 종류.

    값은 authority projection에 그대로 들어가는 계약 어휘입니다. 데이터베이스
    `member_kind` 열이 쓰는 저장 값("ARTIFACT")은 다르므로
    :func:`persisted_member_kind_value` / :func:`member_kind_from_persisted`로 변환합니다.
    이는 기존 `source_member_identity` 계약의 bridge 규칙과 같습니다.
    """

    ENDPOINT_OPERATION = "ENDPOINT_OPERATION"
    ARTIFACT_MEMBER = "ARTIFACT_MEMBER"


_KIND_TO_PERSISTED: dict[RequestAuthorityMemberKind, str] = {
    RequestAuthorityMemberKind.ENDPOINT_OPERATION: "ENDPOINT_OPERATION",
    RequestAuthorityMemberKind.ARTIFACT_MEMBER: "ARTIFACT",
}
_PERSISTED_TO_KIND: dict[str, RequestAuthorityMemberKind] = {
    persisted: kind for kind, persisted in _KIND_TO_PERSISTED.items()
}


@dataclass(frozen=True, slots=True)
class RequestAuthorityArtifactRef:
    artifact_code: str
    version: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class RequestAuthorityMemberIdentity:
    member_kind: RequestAuthorityMemberKind
    endpoint_code: str | None = None
    operation_code: str | None = None
    artifact_code: str | None = None
    artifact_version: str | None = None


class RequestAuthorityArtifactReason(StrEnum):
    USER_ID_INVALID = "USER_ID_INVALID"
    REQUEST_OPERATION_CODE_INVALID = "REQUEST_OPERATION_CODE_INVALID"
    DECISION_STAGE_INVALID = "DECISION_STAGE_INVALID"
    DECISION_OUTCOME_INVALID = "DECISION_OUTCOME_INVALID"
    REQUEST_GUARD_REF_INVALID = "REQUEST_GUARD_REF_INVALID"
    SOURCE_BINDING_INVALID = "SOURCE_BINDING_INVALID"
    MEMBER_BINDING_INVALID = "MEMBER_BINDING_INVALID"
    MEMBER_IDENTITY_INVALID = "MEMBER_IDENTITY_INVALID"
    MEMBER_KIND_INVALID = "MEMBER_KIND_INVALID"


class RequestAuthorityArtifactError(Exception):
    """Authority 사실로 canonical artifact identity를 만들 수 없을 때 fail closed."""

    def __init__(self, reason: RequestAuthorityArtifactReason) -> None:
        self.reason = reason
        super().__init__(str(reason))


def persisted_member_kind_value(kind: RequestAuthorityMemberKind) -> str:
    """계약 enum을 데이터베이스 저장 값으로 변환합니다."""
    if type(kind) is not RequestAuthorityMemberKind:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.MEMBER_KIND_INVALID)
    return _KIND_TO_PERSISTED[kind]


def member_kind_from_persisted(value: str) -> RequestAuthorityMemberKind:
    """데이터베이스 저장 값을 계약 enum으로 변환합니다."""
    kind = _PERSISTED_TO_KIND.get(value)
    if kind is None:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.MEMBER_KIND_INVALID)
    return kind


def _is_canonical_text(value: object) -> bool:
    return type(value) is str and len(value) > 0 and value == value.strip() and unicodedata.is_normalized("NFC", value)


def is_valid_request_authority_artifact_ref(value: object) -> bool:
    if type(value) is not RequestAuthorityArtifactRef:
        return False
    return (
        _is_canonical_text(value.artifact_code)
        and _is_canonical_text(value.version)
        and type(value.content_sha256) is str
        and bool(_SHA256_RE.fullmatch(value.content_sha256))
    )


def is_valid_request_authority_member_identity(value: object) -> bool:
    """`source_member_identity` 계약과 같은 규칙으로 member identity를 검증합니다.

    ENDPOINT_OPERATION은 `endpoint_code`가 필수이고 `operation_code`는 계약대로 nullable이며
    artifact 필드를 가질 수 없습니다. ARTIFACT_MEMBER는 artifact 두 필드가 필수이고 endpoint
    필드를 가질 수 없습니다.
    """
    if type(value) is not RequestAuthorityMemberIdentity:
        return False
    if type(value.member_kind) is not RequestAuthorityMemberKind:
        return False

    if value.member_kind is RequestAuthorityMemberKind.ENDPOINT_OPERATION:
        if not _is_canonical_text(value.endpoint_code):
            return False
        if value.operation_code is not None and not _is_canonical_text(value.operation_code):
            return False
        return value.artifact_code is None and value.artifact_version is None

    if not _is_canonical_text(value.artifact_code) or not _is_canonical_text(value.artifact_version):
        return False
    return value.endpoint_code is None and value.operation_code is None


def _require_user_id(user_id: object) -> str:
    """사용자 식별자를 canonical 문자열로 고정합니다.

    공유 계약이므로 `uuid.UUID` 인스턴스와 그 정규 문자열 표현을 모두 받아들이되, 두 경우가
    항상 같은 digest를 만들도록 소문자 canonical 형식만 허용합니다.
    """
    if isinstance(user_id, str):
        text = user_id
    else:
        text = str(user_id)
        if type(user_id).__name__ != "UUID":
            raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.USER_ID_INVALID)
    if not _UUID_RE.fullmatch(text):
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.USER_ID_INVALID)
    return text


def _require_uuid_text(value: object, reason: RequestAuthorityArtifactReason) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = str(value)
        if type(value).__name__ != "UUID":
            raise RequestAuthorityArtifactError(reason)
    if not _UUID_RE.fullmatch(text):
        raise RequestAuthorityArtifactError(reason)
    return text


def _require_operation_code(request_operation_code: object) -> str:
    if not _is_canonical_text(request_operation_code):
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.REQUEST_OPERATION_CODE_INVALID)
    assert isinstance(request_operation_code, str)
    return request_operation_code


def _require_request_stage(decision_stage: object) -> RequestAuthorityDecisionStage:
    if decision_stage is not RequestAuthorityDecisionStage.REQUEST:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.DECISION_STAGE_INVALID)
    return RequestAuthorityDecisionStage.REQUEST


def _require_outcome(actual_decision_outcome: object) -> RequestAuthorityDecisionOutcome:
    if type(actual_decision_outcome) is not RequestAuthorityDecisionOutcome:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.DECISION_OUTCOME_INVALID)
    return actual_decision_outcome


def _require_guard_ref(request_guard_ref: object) -> RequestAuthorityArtifactRef:
    if not is_valid_request_authority_artifact_ref(request_guard_ref):
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.REQUEST_GUARD_REF_INVALID)
    assert isinstance(request_guard_ref, RequestAuthorityArtifactRef)
    if request_guard_ref.artifact_code != REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE:
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.REQUEST_GUARD_REF_INVALID)
    return request_guard_ref


def _artifact_projection(ref: RequestAuthorityArtifactRef) -> dict[str, object]:
    return {
        "artifact_code": ref.artifact_code,
        "content_sha256": ref.content_sha256,
        "version": ref.version,
    }


def _member_identity_projection(identity: RequestAuthorityMemberIdentity) -> dict[str, object]:
    return {
        "member_kind": identity.member_kind.value,
        "endpoint_code": identity.endpoint_code,
        "operation_code": identity.operation_code,
        "artifact_code": identity.artifact_code,
        "artifact_version": identity.artifact_version,
    }


def canonical_request_authority_sha256(projection: dict[str, object]) -> str:
    encoded = json.dumps(projection, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _digest_ref(*, artifact_code: str, projection: dict[str, object]) -> RequestAuthorityArtifactRef:
    return RequestAuthorityArtifactRef(
        artifact_code=artifact_code,
        version=REQUEST_AUTHORITY_ARTIFACT_VERSION,
        content_sha256=canonical_request_authority_sha256(projection),
    )


# ---------------------------------------------------------------------------
# REQUEST Guard
# ---------------------------------------------------------------------------


def request_guard_authority_projection(
    *,
    user_id: object,
    request_operation_code: str,
    decision_stage: RequestAuthorityDecisionStage,
) -> dict[str, object]:
    """REQUEST Guard authority 관측치의 canonical semantic projection."""
    return {
        "decision_stage": _require_request_stage(decision_stage).value,
        "projection_version": REQUEST_GUARD_AUTHORITY_PROJECTION_VERSION,
        "request_operation_code": _require_operation_code(request_operation_code),
        "user_id": _require_user_id(user_id),
    }


def compute_request_guard_authority_ref(
    *,
    user_id: object,
    request_operation_code: str,
    decision_stage: RequestAuthorityDecisionStage,
) -> RequestAuthorityArtifactRef:
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
    request_guard_ref: RequestAuthorityArtifactRef,
    user_id: object,
    request_operation_code: str,
    decision_stage: RequestAuthorityDecisionStage,
    source_snapshot_id: object,
    source_code: str,
    source_version: str,
    actual_decision_outcome: RequestAuthorityDecisionOutcome,
) -> dict[str, object]:
    """Request-bound Source Decision 관측치의 canonical semantic projection."""
    if not _is_canonical_text(source_code) or not _is_canonical_text(source_version):
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.SOURCE_BINDING_INVALID)
    snapshot_id = _require_uuid_text(source_snapshot_id, RequestAuthorityArtifactReason.SOURCE_BINDING_INVALID)

    return {
        "actual_decision_outcome": _require_outcome(actual_decision_outcome).value,
        "decision_stage": _require_request_stage(decision_stage).value,
        "projection_version": REQUEST_SOURCE_DECISION_AUTHORITY_PROJECTION_VERSION,
        "request_guard_ref": _artifact_projection(_require_guard_ref(request_guard_ref)),
        "request_operation_code": _require_operation_code(request_operation_code),
        "source_code": source_code,
        "source_snapshot_id": snapshot_id,
        "source_version": source_version,
        "user_id": _require_user_id(user_id),
    }


def compute_request_source_decision_authority_ref(
    *,
    request_guard_ref: RequestAuthorityArtifactRef,
    user_id: object,
    request_operation_code: str,
    decision_stage: RequestAuthorityDecisionStage,
    source_snapshot_id: object,
    source_code: str,
    source_version: str,
    actual_decision_outcome: RequestAuthorityDecisionOutcome,
) -> RequestAuthorityArtifactRef:
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
    request_guard_ref: RequestAuthorityArtifactRef,
    user_id: object,
    request_operation_code: str,
    decision_stage: RequestAuthorityDecisionStage,
    source_snapshot_id: object,
    source_snapshot_member_id: object,
    member_identity: RequestAuthorityMemberIdentity,
    actual_decision_outcome: RequestAuthorityDecisionOutcome,
) -> dict[str, object]:
    """Request-bound Member Decision 관측치의 canonical semantic projection."""
    snapshot_id = _require_uuid_text(source_snapshot_id, RequestAuthorityArtifactReason.MEMBER_BINDING_INVALID)
    member_id = _require_uuid_text(source_snapshot_member_id, RequestAuthorityArtifactReason.MEMBER_BINDING_INVALID)
    if not is_valid_request_authority_member_identity(member_identity):
        raise RequestAuthorityArtifactError(RequestAuthorityArtifactReason.MEMBER_IDENTITY_INVALID)

    return {
        "actual_decision_outcome": _require_outcome(actual_decision_outcome).value,
        "decision_stage": _require_request_stage(decision_stage).value,
        "member_identity": _member_identity_projection(member_identity),
        "projection_version": REQUEST_MEMBER_DECISION_AUTHORITY_PROJECTION_VERSION,
        "request_guard_ref": _artifact_projection(_require_guard_ref(request_guard_ref)),
        "request_operation_code": _require_operation_code(request_operation_code),
        "source_snapshot_id": snapshot_id,
        "source_snapshot_member_id": member_id,
        "user_id": _require_user_id(user_id),
    }


def compute_request_member_decision_authority_ref(
    *,
    request_guard_ref: RequestAuthorityArtifactRef,
    user_id: object,
    request_operation_code: str,
    decision_stage: RequestAuthorityDecisionStage,
    source_snapshot_id: object,
    source_snapshot_member_id: object,
    member_identity: RequestAuthorityMemberIdentity,
    actual_decision_outcome: RequestAuthorityDecisionOutcome,
) -> RequestAuthorityArtifactRef:
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
