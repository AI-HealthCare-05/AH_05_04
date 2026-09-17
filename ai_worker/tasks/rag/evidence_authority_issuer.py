"""Production Assessment·Eligibility Authority Issuer (#712).

Issuer pipeline:
1. input validation
2. selected-hit persistence verification (retrieval_hit.selected == True)
3. source/member/content exact binding verification
4. eligibility/verifier/policy artifact projection creation
5. validity computation (assessment_valid_from = evaluated_at, assessment_valid_until <= 24h)
6. assessment artifact projection creation
7. idempotent persistence (frozen retry window, fail-closed conflict detection)
8. return persisted authority record
"""

from __future__ import annotations

import re
from typing import Protocol
from uuid import UUID, uuid4

from rag_runtime.evidence_authority import (
    ChunkSourceBinding,
    EvidenceAuthorityConflictError,
    EvidenceAuthorityErrorCode,
    EvidenceAuthorityValidationError,
    IssueAssessmentAuthorityRequest,
    PersistedEvidenceAuthority,
    compute_assessment_artifact_ref,
    compute_assessment_validity_window,
    compute_eligibility_receipt_ref,
    compute_validity_policy_ref,
    compute_verifier_artifact_ref,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EvidenceAuthorityStorePort(Protocol):
    """Port defining the persistence and verification contract for Evidence Authority."""

    async def get_selected_hit(
        self,
        retrieval_run_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> bool | None:
        """Returns True if hit exists and selected==True, False if selected==False, None if hit not found."""
        ...

    async def get_chunk_source_binding(
        self,
        retrieval_run_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> ChunkSourceBinding | None:
        """Returns DB-persisted chunk source binding or None if not found."""
        ...

    async def get_authority_by_identity(
        self,
        retrieval_run_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> PersistedEvidenceAuthority | None:
        """Returns persisted authority for (retrieval_run_id, knowledge_chunk_id) or None."""
        ...

    async def persist_authority(
        self,
        record: PersistedEvidenceAuthority,
    ) -> PersistedEvidenceAuthority:
        """Persists authority record. Raises EvidenceAuthorityConflictError if identity conflict."""
        ...


def _validate_request_shape(request: IssueAssessmentAuthorityRequest) -> None:
    """1. Input validation. Fail-closed before any store access."""
    if not isinstance(request.retrieval_run_id, UUID) or not isinstance(request.knowledge_chunk_id, UUID):
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.MALFORMED_ARTIFACT_REF,
            "retrieval_run_id and knowledge_chunk_id must be valid UUIDs",
        )
    if not request.source_code or not request.source_code.strip():
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.MALFORMED_ARTIFACT_REF,
            "source_code must not be empty",
        )
    if not request.source_version or not request.source_version.strip():
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.MALFORMED_ARTIFACT_REF,
            "source_version must not be empty",
        )
    if not isinstance(request.content_sha256, str) or not _SHA256_RE.match(request.content_sha256):
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.MALFORMED_ARTIFACT_REF,
            f"Invalid content_sha256 format: {request.content_sha256}",
        )


async def issue_assessment_eligibility_authority(
    request: IssueAssessmentAuthorityRequest,
    store: EvidenceAuthorityStorePort,
) -> PersistedEvidenceAuthority:
    """Issue and persist an immutable Assessment·Eligibility Authority record.

    Adheres strictly to PD-722 (24h ceiling, half-open interval, retry determinism).
    Fail-closed on any validation error, missing hit, binding mismatch, or conflict.
    """
    _validate_request_shape(request)

    # 2. Selected-hit check
    hit_selected = await store.get_selected_hit(request.retrieval_run_id, request.knowledge_chunk_id)
    if hit_selected is None:
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.SELECTED_HIT_NOT_FOUND,
            f"Selected hit not found for run={request.retrieval_run_id}, chunk={request.knowledge_chunk_id}",
        )
    if not hit_selected:
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.HIT_NOT_SELECTED,
            f"Hit for run={request.retrieval_run_id}, chunk={request.knowledge_chunk_id} exists but selected is False",
        )

    # 3. Exact source binding check
    binding = await store.get_chunk_source_binding(request.retrieval_run_id, request.knowledge_chunk_id)
    if binding is None:
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.SOURCE_BINDING_MISMATCH,
            f"No source binding found for run={request.retrieval_run_id}, chunk={request.knowledge_chunk_id}",
        )
    if (
        binding.source_snapshot_id != request.source_snapshot_id
        or binding.source_snapshot_member_id != request.source_snapshot_member_id
        or binding.source_code != request.source_code
        or binding.source_version != request.source_version
    ):
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.SOURCE_BINDING_MISMATCH,
            f"Source binding mismatch for run={request.retrieval_run_id}, chunk={request.knowledge_chunk_id}",
        )
    if binding.content_sha256 != request.content_sha256:
        raise EvidenceAuthorityValidationError(
            EvidenceAuthorityErrorCode.CHUNK_CONTENT_MISMATCH,
            f"Chunk content hash mismatch for run={request.retrieval_run_id}, chunk={request.knowledge_chunk_id}",
        )

    # 4. Idempotent check: return existing if already persisted with identical semantics
    policy_ref = compute_validity_policy_ref(request.policy)
    existing = await store.get_authority_by_identity(request.retrieval_run_id, request.knowledge_chunk_id)
    if existing is not None:
        if (
            existing.source_snapshot_id == request.source_snapshot_id
            and existing.source_snapshot_member_id == request.source_snapshot_member_id
            and existing.source_code == request.source_code
            and existing.source_version == request.source_version
            and existing.content_sha256 == request.content_sha256
            and existing.validity_policy_ref == policy_ref
        ):
            # Idempotent retry determinism: do not extend validity window, return existing
            return existing
        raise EvidenceAuthorityConflictError(
            EvidenceAuthorityErrorCode.AUTHORITY_IDENTITY_CONFLICT,
            f"Authority for run={request.retrieval_run_id}, chunk={request.knowledge_chunk_id} exists with conflicting details",
        )

    # 5. Validity window & Projections
    valid_from, valid_until = compute_assessment_validity_window(
        evaluated_at=request.evaluated_at,
        policy=request.policy,
        applicable_upper_bounds=request.applicable_upper_bounds,
    )
    # #712: verifier identity는 caller 입력이 아니라 판단을 수행한 issuer가 직접 계산한다.
    verifier_ref = compute_verifier_artifact_ref()
    eligibility_receipt_ref = compute_eligibility_receipt_ref(
        retrieval_run_id=request.retrieval_run_id,
        knowledge_chunk_id=request.knowledge_chunk_id,
        source_snapshot_id=request.source_snapshot_id,
        source_snapshot_member_id=request.source_snapshot_member_id,
        source_code=request.source_code,
        source_version=request.source_version,
        content_sha256=request.content_sha256,
        evaluated_at=request.evaluated_at,
        verifier_artifact_ref=verifier_ref,
    )
    assessment_artifact_ref = compute_assessment_artifact_ref(
        retrieval_run_id=request.retrieval_run_id,
        knowledge_chunk_id=request.knowledge_chunk_id,
        eligibility_receipt_ref=eligibility_receipt_ref,
        validity_policy_ref=policy_ref,
        assessment_valid_from=valid_from,
        assessment_valid_until=valid_until,
    )

    # 6. Record assembly
    record = PersistedEvidenceAuthority(
        id=uuid4(),
        retrieval_run_id=request.retrieval_run_id,
        knowledge_chunk_id=request.knowledge_chunk_id,
        source_snapshot_id=request.source_snapshot_id,
        source_snapshot_member_id=request.source_snapshot_member_id,
        source_code=request.source_code,
        source_version=request.source_version,
        content_sha256=request.content_sha256,
        eligibility_receipt_ref=eligibility_receipt_ref,
        assessment_artifact_ref=assessment_artifact_ref,
        verifier_artifact_ref=verifier_ref,
        validity_policy_ref=policy_ref,
        evaluated_at=request.evaluated_at,
        assessment_valid_from=valid_from,
        assessment_valid_until=valid_until,
    )

    # 7. Idempotent persistence
    return await store.persist_authority(record)
