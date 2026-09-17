"""Shared Track F Assessment·Eligibility Authority wire contract and artifact identity (#712).

Backend와 AI Worker가 함께 소비하는 순수 계약입니다. Backend Repository는 Evidence Authority를
저장·조회하고 AI Worker는 같은 의미를 Guide 실행 경계에서 사용하므로, 양쪽 중 한쪽 package
내부에 두고 반대쪽이 직접 import하는 구조를 쓰지 않습니다. `PD-175-20260910`이 고정한
`backend` → `ai_worker` import 경계를 우회하지 않기 위해, 두 이미지에 함께 복사되는 공유 순수
package인 `rag_runtime`이 이 계약을 소유합니다.

Scope & Authority Boundaries:
- 순수 계약: stdlib만 사용하며 DB I/O, network, clock, session이 없습니다.
- Identity only: 이미 authoritative한 평가 사실로부터 artifact identity를 도출합니다.
- Writer-owned identity: artifact_code와 version은 계약 상수이고 content_sha256은 semantic
  projection의 canonical digest이므로 caller가 임의의 authority identity를 고를 수 없습니다.
- Canonicalization: RFC 8785 JCS 규칙에 따라 key를 사전순 정렬한 JSON 바이트열을 SHA-256으로
  요약합니다. 모든 projection key는 ASCII이므로 `json.dumps(..., sort_keys=True, ensure_ascii=False,
  separators=(",", ":"))` 바이트열은 canonical JCS 바이트열과 100% 동일합니다.
- Timezone & Interval: 모든 datetime은 timezone-aware UTC여야 하며,
  `assessment_valid_from <= evaluated_at < assessment_valid_until` half-open interval을 준수합니다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

__all__ = [
    "ASSESSMENT_ARTIFACT_CODE",
    "ASSESSMENT_ARTIFACT_VERSION",
    "ChunkSourceBinding",
    "ELIGIBILITY_RECEIPT_ARTIFACT_CODE",
    "ELIGIBILITY_RECEIPT_VERSION",
    "EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS",
    "EVIDENCE_ASSESSMENT_VALIDITY_CANONICAL_SHA256",
    "EVIDENCE_ASSESSMENT_VALIDITY_POLICY_CODE",
    "EVIDENCE_ASSESSMENT_VALIDITY_SEMANTICS",
    "EVIDENCE_ASSESSMENT_VALIDITY_VERSION",
    "EvidenceAssessmentValidityPolicy",
    "EvidenceAuthorityConflictError",
    "EvidenceAuthorityCorruptError",
    "EvidenceAuthorityError",
    "EvidenceAuthorityErrorCode",
    "EvidenceAuthorityValidationError",
    "ImmutableArtifactRef",
    "IssueAssessmentAuthorityRequest",
    "POSTGRESQL_ELIGIBILITY_VERIFIER_ARTIFACT_CODE",
    "POSTGRESQL_ELIGIBILITY_VERIFIER_VERSION",
    "PersistedEvidenceAuthority",
    "canonical_datetime_str",
    "compute_assessment_artifact_ref",
    "compute_assessment_validity_window",
    "compute_eligibility_receipt_ref",
    "compute_validity_policy_ref",
    "compute_verifier_artifact_ref",
    "is_valid_immutable_artifact_ref",
]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# PD-722 Approved Validity Policy Baseline (PR #725)
EVIDENCE_ASSESSMENT_VALIDITY_POLICY_CODE = "evidence-assessment-validity"
EVIDENCE_ASSESSMENT_VALIDITY_VERSION = "v1"
EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS = 86400  # 24 hours
EVIDENCE_ASSESSMENT_VALIDITY_SEMANTICS = "Maximum operational lifetime for reusing a production evidence assessment in runtime handoff without re-evaluation."
EVIDENCE_ASSESSMENT_VALIDITY_CANONICAL_SHA256 = "94cda93c44625cdfd978bca78a6748afd5f7011afc987d89ed6e86bbf4df043a"

# Artifact Codes and Versions
ELIGIBILITY_RECEIPT_ARTIFACT_CODE = "production_evidence_eligibility_receipt"
ELIGIBILITY_RECEIPT_VERSION = "v1"
ASSESSMENT_ARTIFACT_CODE = "production_evidence_assessment"
ASSESSMENT_ARTIFACT_VERSION = "v1"
POSTGRESQL_ELIGIBILITY_VERIFIER_ARTIFACT_CODE = "postgresql_evidence_eligibility_verifier"
POSTGRESQL_ELIGIBILITY_VERIFIER_VERSION = "v1"


class EvidenceAuthorityErrorCode(StrEnum):
    DATETIME_NOT_UTC = "DATETIME_NOT_UTC"
    INVALID_VALIDITY_WINDOW = "INVALID_VALIDITY_WINDOW"
    SELECTED_HIT_NOT_FOUND = "SELECTED_HIT_NOT_FOUND"
    HIT_NOT_SELECTED = "HIT_NOT_SELECTED"
    SOURCE_BINDING_MISMATCH = "SOURCE_BINDING_MISMATCH"
    CHUNK_CONTENT_MISMATCH = "CHUNK_CONTENT_MISMATCH"
    AUTHORITY_IDENTITY_CONFLICT = "AUTHORITY_IDENTITY_CONFLICT"
    CORRUPT_AUTHORITY_RECORD = "CORRUPT_AUTHORITY_RECORD"
    MALFORMED_ARTIFACT_REF = "MALFORMED_ARTIFACT_REF"


class EvidenceAuthorityError(Exception):
    """Base exception for Evidence Authority errors."""

    def __init__(self, code: EvidenceAuthorityErrorCode, message: str) -> None:
        super().__init__(f"[{code.value}] {message}")
        self.code = code
        self.message = message


class EvidenceAuthorityValidationError(EvidenceAuthorityError):
    """Authority input or interval fails validation."""


class EvidenceAuthorityConflictError(EvidenceAuthorityError):
    """Authority identity already exists with conflicting details."""


class EvidenceAuthorityCorruptError(EvidenceAuthorityError):
    """Persisted authority record is corrupted or violates invariants."""


@dataclass(frozen=True, slots=True)
class ImmutableArtifactRef:
    artifact_code: str
    version: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceAssessmentValidityPolicy:
    policy_code: str = EVIDENCE_ASSESSMENT_VALIDITY_POLICY_CODE
    version: str = EVIDENCE_ASSESSMENT_VALIDITY_VERSION
    max_validity_duration_seconds: int = EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS
    semantics: str = EVIDENCE_ASSESSMENT_VALIDITY_SEMANTICS


@dataclass(frozen=True, slots=True)
class ChunkSourceBinding:
    retrieval_run_id: UUID
    knowledge_chunk_id: UUID
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class IssueAssessmentAuthorityRequest:
    """Issuer 입력. `verifier_artifact_ref`는 의도적으로 필드가 아니다.

    Issue #712는 "caller가 전달한 verifier ref 신뢰"를 명시적으로 금지하므로, verifier identity는
    caller 입력이 아니라 판단을 수행한 issuer가 `compute_verifier_artifact_ref()`로 직접 계산한다.
    """

    retrieval_run_id: UUID
    knowledge_chunk_id: UUID
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    content_sha256: str
    evaluated_at: datetime
    policy: EvidenceAssessmentValidityPolicy = EvidenceAssessmentValidityPolicy()
    applicable_upper_bounds: tuple[datetime, ...] = ()


@dataclass(frozen=True, slots=True)
class PersistedEvidenceAuthority:
    id: UUID
    retrieval_run_id: UUID
    knowledge_chunk_id: UUID
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    content_sha256: str
    eligibility_receipt_ref: ImmutableArtifactRef
    assessment_artifact_ref: ImmutableArtifactRef
    verifier_artifact_ref: ImmutableArtifactRef
    validity_policy_ref: ImmutableArtifactRef
    evaluated_at: datetime
    assessment_valid_from: datetime
    assessment_valid_until: datetime
    created_at: datetime | None = None


def is_valid_immutable_artifact_ref(ref: Any) -> bool:
    if not isinstance(ref, ImmutableArtifactRef):
        return False
    if not ref.artifact_code or not ref.artifact_code.strip():
        return False
    if not ref.version or not ref.version.strip():
        return False
    if not isinstance(ref.content_sha256, str) or not _SHA256_RE.match(ref.content_sha256):
        return False
    return True


def canonical_datetime_str(value: datetime) -> str:
    """Format timezone-aware UTC datetime into canonical RFC 3339 representation."""
    if value.tzinfo is None or value.tzinfo != UTC:
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.DATETIME_NOT_UTC,
            f"Datetime must be timezone-aware UTC: {value}",
        )
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_jcs_bytes(payload: dict[str, Any]) -> bytes:
    """RFC 8785 JCS canonical JSON bytes."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_validity_policy_ref(
    policy: EvidenceAssessmentValidityPolicy | None = None,
) -> ImmutableArtifactRef:
    """Compute deterministic ImmutableArtifactRef for EvidenceAssessmentValidityPolicy (PD-722)."""
    p = policy or EvidenceAssessmentValidityPolicy()
    payload = {
        "max_validity_duration_seconds": p.max_validity_duration_seconds,
        "policy_code": p.policy_code,
        "semantics": p.semantics,
        "version": p.version,
    }
    digest = _sha256_hex(_canonical_jcs_bytes(payload))
    return ImmutableArtifactRef(
        artifact_code=p.policy_code,
        version=p.version,
        content_sha256=digest,
    )


def compute_verifier_artifact_ref(
    artifact_code: str = POSTGRESQL_ELIGIBILITY_VERIFIER_ARTIFACT_CODE,
    version: str = POSTGRESQL_ELIGIBILITY_VERIFIER_VERSION,
    configuration: dict[str, Any] | None = None,
) -> ImmutableArtifactRef:
    """Compute deterministic ImmutableArtifactRef for the evidence eligibility verifier."""
    payload: dict[str, Any] = {
        "artifact_code": artifact_code,
        "contract": "ProductionEvidenceEligibilityVerifierPort",
        "semantics": "PostgreSQL-backed exact binding and lifecycle status verification for knowledge evidence chunks.",
        "version": version,
    }
    if configuration:
        payload["configuration"] = configuration
    digest = _sha256_hex(_canonical_jcs_bytes(payload))
    return ImmutableArtifactRef(
        artifact_code=artifact_code,
        version=version,
        content_sha256=digest,
    )


def compute_eligibility_receipt_ref(
    retrieval_run_id: UUID,
    knowledge_chunk_id: UUID,
    source_snapshot_id: UUID,
    source_snapshot_member_id: UUID,
    source_code: str,
    source_version: str,
    content_sha256: str,
    evaluated_at: datetime,
    verifier_artifact_ref: ImmutableArtifactRef,
) -> ImmutableArtifactRef:
    """Compute deterministic ImmutableArtifactRef for the production eligibility receipt."""
    if not _SHA256_RE.match(content_sha256):
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.MALFORMED_ARTIFACT_REF,
            f"Invalid content_sha256 format: {content_sha256}",
        )
    if not is_valid_immutable_artifact_ref(verifier_artifact_ref):
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.MALFORMED_ARTIFACT_REF,
            f"Invalid verifier_artifact_ref: {verifier_artifact_ref}",
        )

    canonical_eval_at = canonical_datetime_str(evaluated_at)
    payload = {
        "content_sha256": content_sha256,
        "eligibility_decision": "ELIGIBLE",
        "evaluated_at": canonical_eval_at,
        "knowledge_chunk_id": str(knowledge_chunk_id),
        "projection_version": "production-evidence-eligibility-receipt-v1",
        "retrieval_run_id": str(retrieval_run_id),
        "source_code": source_code,
        "source_snapshot_id": str(source_snapshot_id),
        "source_snapshot_member_id": str(source_snapshot_member_id),
        "source_version": source_version,
        "verifier_artifact_ref": {
            "artifact_code": verifier_artifact_ref.artifact_code,
            "content_sha256": verifier_artifact_ref.content_sha256,
            "version": verifier_artifact_ref.version,
        },
    }
    digest = _sha256_hex(_canonical_jcs_bytes(payload))
    return ImmutableArtifactRef(
        artifact_code=ELIGIBILITY_RECEIPT_ARTIFACT_CODE,
        version=ELIGIBILITY_RECEIPT_VERSION,
        content_sha256=digest,
    )


def compute_assessment_artifact_ref(
    retrieval_run_id: UUID,
    knowledge_chunk_id: UUID,
    eligibility_receipt_ref: ImmutableArtifactRef,
    validity_policy_ref: ImmutableArtifactRef,
    assessment_valid_from: datetime,
    assessment_valid_until: datetime,
) -> ImmutableArtifactRef:
    """Compute deterministic ImmutableArtifactRef for the evidence assessment artifact."""
    if not is_valid_immutable_artifact_ref(eligibility_receipt_ref):
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.MALFORMED_ARTIFACT_REF,
            f"Invalid eligibility_receipt_ref: {eligibility_receipt_ref}",
        )
    if not is_valid_immutable_artifact_ref(validity_policy_ref):
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.MALFORMED_ARTIFACT_REF,
            f"Invalid validity_policy_ref: {validity_policy_ref}",
        )

    valid_from_str = canonical_datetime_str(assessment_valid_from)
    valid_until_str = canonical_datetime_str(assessment_valid_until)

    payload = {
        "assessment_valid_from": valid_from_str,
        "assessment_valid_until": valid_until_str,
        "eligibility_receipt_ref": {
            "artifact_code": eligibility_receipt_ref.artifact_code,
            "content_sha256": eligibility_receipt_ref.content_sha256,
            "version": eligibility_receipt_ref.version,
        },
        "evidence_gate_status": "SUCCEEDED",
        "knowledge_chunk_id": str(knowledge_chunk_id),
        "projection_version": "production-evidence-assessment-v1",
        "retrieval_run_id": str(retrieval_run_id),
        "validity_policy_ref": {
            "artifact_code": validity_policy_ref.artifact_code,
            "content_sha256": validity_policy_ref.content_sha256,
            "version": validity_policy_ref.version,
        },
    }
    digest = _sha256_hex(_canonical_jcs_bytes(payload))
    return ImmutableArtifactRef(
        artifact_code=ASSESSMENT_ARTIFACT_CODE,
        version=ASSESSMENT_ARTIFACT_VERSION,
        content_sha256=digest,
    )


def compute_assessment_validity_window(
    evaluated_at: datetime,
    policy: EvidenceAssessmentValidityPolicy,
    applicable_upper_bounds: tuple[datetime, ...],
) -> tuple[datetime, datetime]:
    """Compute (assessment_valid_from, assessment_valid_until) adhering to PD-722.

    Rules:
    - Timezone-aware UTC required for evaluated_at and all upper bounds.
    - assessment_valid_from = evaluated_at
    - assessment_valid_until = min(evaluated_at + policy.max_validity_duration, *applicable_upper_bounds)
    - assessment_valid_from < assessment_valid_until strictly required (fail closed).
    """
    if evaluated_at.tzinfo is None or evaluated_at.tzinfo != UTC:
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.DATETIME_NOT_UTC,
            f"evaluated_at must be timezone-aware UTC: {evaluated_at}",
        )

    for bound in applicable_upper_bounds:
        if bound.tzinfo is None or bound.tzinfo != UTC:
            raise EvidenceAuthorityValidationError(
                EvidenceAuthorityErrorCode.DATETIME_NOT_UTC,
                f"applicable_upper_bound must be timezone-aware UTC: {bound}",
            )

    valid_from = evaluated_at
    ceiling = evaluated_at + timedelta(seconds=policy.max_validity_duration_seconds)
    candidates = (ceiling, *applicable_upper_bounds)
    valid_until = min(candidates)

    if valid_from >= valid_until:
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.INVALID_VALIDITY_WINDOW,
            f"Invalid validity window: valid_from ({valid_from}) must be strictly less than valid_until ({valid_until})",
        )

    return valid_from, valid_until
