"""Unit tests for Track F Assessment·Eligibility Authority Issuer (#712).

Strict TDD suite verifying:
- Test 1: Validity policy deterministic projection and golden JCS hash (PD-722).
- Test 2: Validity window computation and interval constraints.
- Test 3: Retry does not extend validity window.
- Test 4: Selected hit required (fail closed).
- Test 5: Exact source/member/content binding verified (fail closed).
- Test 6: DB round-trip restores all triples and fields.
- Test 7: Idempotent retry returns existing authority without modification.
- Test 8: Authority identity conflict detection (fail closed).
- Test 9: Cross-run isolation for same chunk.
- Test 10: Corrupt or malformed artifact ref rejection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from rag_runtime.evidence_authority import (
    EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS,
    EVIDENCE_ASSESSMENT_VALIDITY_CANONICAL_SHA256,
    EVIDENCE_ASSESSMENT_VALIDITY_POLICY_CODE,
    EVIDENCE_ASSESSMENT_VALIDITY_VERSION,
    ChunkSourceBinding,
    EvidenceAssessmentValidityPolicy,
    EvidenceAuthorityConflictError,
    EvidenceAuthorityErrorCode,
    EvidenceAuthorityValidationError,
    ImmutableArtifactRef,
    IssueAssessmentAuthorityRequest,
    PersistedEvidenceAuthority,
    compute_assessment_artifact_ref,
    compute_assessment_validity_window,
    compute_eligibility_receipt_ref,
    compute_validity_policy_ref,
    compute_verifier_artifact_ref,
    is_valid_immutable_artifact_ref,
)

try:
    from ai_worker.tasks.rag.evidence_authority_issuer import (
        EvidenceAuthorityStorePort,
        issue_assessment_eligibility_authority,
    )
except ImportError:
    # Stubs before minimal GREEN
    EvidenceAuthorityStorePort = Any  # type: ignore[misc,assignment]
    issue_assessment_eligibility_authority = None  # type: ignore[assignment]


class InMemoryEvidenceAuthorityStore:
    """In-memory test fake implementing EvidenceAuthorityStorePort."""

    def __init__(self) -> None:
        self.selected_hits: dict[tuple[UUID, UUID], bool] = {}
        self.source_bindings: dict[tuple[UUID, UUID], ChunkSourceBinding] = {}
        self.authorities: dict[tuple[UUID, UUID], PersistedEvidenceAuthority] = {}
        self.persist_call_count = 0

    async def get_selected_hit(
        self,
        retrieval_run_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> bool | None:
        return self.selected_hits.get((retrieval_run_id, knowledge_chunk_id))

    async def get_chunk_source_binding(
        self,
        retrieval_run_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> ChunkSourceBinding | None:
        return self.source_bindings.get((retrieval_run_id, knowledge_chunk_id))

    async def get_authority_by_identity(
        self,
        retrieval_run_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> PersistedEvidenceAuthority | None:
        return self.authorities.get((retrieval_run_id, knowledge_chunk_id))

    async def persist_authority(
        self,
        record: PersistedEvidenceAuthority,
    ) -> PersistedEvidenceAuthority:
        self.persist_call_count += 1
        key = (record.retrieval_run_id, record.knowledge_chunk_id)
        if key in self.authorities:
            existing = self.authorities[key]
            # Exact check
            if (
                existing.source_snapshot_id == record.source_snapshot_id
                and existing.source_snapshot_member_id == record.source_snapshot_member_id
                and existing.source_code == record.source_code
                and existing.source_version == record.source_version
                and existing.content_sha256 == record.content_sha256
                and existing.assessment_artifact_ref == record.assessment_artifact_ref
            ):
                return existing
            raise EvidenceAuthorityConflictError(
                EvidenceAuthorityErrorCode.AUTHORITY_IDENTITY_CONFLICT,
                f"Conflict for identity {key}",
            )
        self.authorities[key] = record
        return record


def _make_sample_request(
    retrieval_run_id: UUID | None = None,
    knowledge_chunk_id: UUID | None = None,
    evaluated_at: datetime | None = None,
    content_sha256: str = "a" * 64,
) -> IssueAssessmentAuthorityRequest:
    return IssueAssessmentAuthorityRequest(
        retrieval_run_id=retrieval_run_id or uuid4(),
        knowledge_chunk_id=knowledge_chunk_id or uuid4(),
        source_snapshot_id=uuid4(),
        source_snapshot_member_id=uuid4(),
        source_code="mfds_drug_permission",
        source_version="2026-09-01",
        content_sha256=content_sha256,
        evaluated_at=evaluated_at or datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC),
    )


def test_validity_policy_deterministic_projection_matches_pd722_golden() -> None:
    """Test 1: Approved PD-722 validity policy projection matches canonical hash."""
    policy = EvidenceAssessmentValidityPolicy()
    assert policy.policy_code == EVIDENCE_ASSESSMENT_VALIDITY_POLICY_CODE == "evidence-assessment-validity"
    assert policy.version == EVIDENCE_ASSESSMENT_VALIDITY_VERSION == "v1"
    assert policy.max_validity_duration_seconds == EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS == 86400

    ref = compute_validity_policy_ref(policy)
    assert isinstance(ref, ImmutableArtifactRef)
    assert ref.artifact_code == "evidence-assessment-validity"
    assert ref.version == "v1"
    assert ref.content_sha256 == EVIDENCE_ASSESSMENT_VALIDITY_CANONICAL_SHA256
    assert ref.content_sha256 == "94cda93c44625cdfd978bca78a6748afd5f7011afc987d89ed6e86bbf4df043a"
    assert is_valid_immutable_artifact_ref(ref)


def test_validity_window_computation_half_open_and_bounds() -> None:
    """Test 2: Validity window computation enforces UTC, bounds, and half-open interval."""
    policy = EvidenceAssessmentValidityPolicy()
    t0 = datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC)

    valid_from, valid_until = compute_assessment_validity_window(
        evaluated_at=t0,
        policy=policy,
        applicable_upper_bounds=(),
    )
    assert valid_from == t0
    assert valid_until == t0 + timedelta(seconds=86400)
    assert valid_from < valid_until

    t_bound = t0 + timedelta(hours=6)
    valid_from, valid_until = compute_assessment_validity_window(
        evaluated_at=t0,
        policy=policy,
        applicable_upper_bounds=(t_bound,),
    )
    assert valid_from == t0
    assert valid_until == t_bound

    t_naive = datetime(2026, 9, 17, 10, 0, 0)
    with pytest.raises(EvidenceAuthorityValidationError) as exc_info:
        compute_assessment_validity_window(evaluated_at=t_naive, policy=policy, applicable_upper_bounds=())
    assert exc_info.value.code == EvidenceAuthorityErrorCode.DATETIME_NOT_UTC


@pytest.mark.asyncio
async def test_retry_does_not_extend_validity() -> None:
    """Test 3: Retry at T0 + 10m does not extend validity window (frozen to original T0)."""
    assert issue_assessment_eligibility_authority is not None
    store = InMemoryEvidenceAuthorityStore()
    t0 = datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC)
    req1 = _make_sample_request(evaluated_at=t0)

    # Setup hit and binding
    key = (req1.retrieval_run_id, req1.knowledge_chunk_id)
    store.selected_hits[key] = True
    store.source_bindings[key] = ChunkSourceBinding(
        retrieval_run_id=req1.retrieval_run_id,
        knowledge_chunk_id=req1.knowledge_chunk_id,
        source_snapshot_id=req1.source_snapshot_id,
        source_snapshot_member_id=req1.source_snapshot_member_id,
        source_code=req1.source_code,
        source_version=req1.source_version,
        content_sha256=req1.content_sha256,
    )

    # First issuance at T0
    auth1 = await issue_assessment_eligibility_authority(req1, store)
    assert auth1.assessment_valid_from == t0
    assert auth1.assessment_valid_until == t0 + timedelta(seconds=86400)

    # Retry at T0 + 10m
    t_retry = t0 + timedelta(minutes=10)
    req_retry = IssueAssessmentAuthorityRequest(
        retrieval_run_id=req1.retrieval_run_id,
        knowledge_chunk_id=req1.knowledge_chunk_id,
        source_snapshot_id=req1.source_snapshot_id,
        source_snapshot_member_id=req1.source_snapshot_member_id,
        source_code=req1.source_code,
        source_version=req1.source_version,
        content_sha256=req1.content_sha256,
        evaluated_at=t_retry,
    )

    auth2 = await issue_assessment_eligibility_authority(req_retry, store)
    # Must NOT extend to t_retry + 24h! Must return original frozen T0 authority
    assert auth2.assessment_valid_from == t0
    assert auth2.assessment_valid_until == t0 + timedelta(seconds=86400)
    assert auth2.id == auth1.id


@pytest.mark.asyncio
async def test_selected_hit_required_fail_closed() -> None:
    """Test 4: Non-existent or unselected hit fails closed."""
    assert issue_assessment_eligibility_authority is not None
    store = InMemoryEvidenceAuthorityStore()
    req = _make_sample_request()
    key = (req.retrieval_run_id, req.knowledge_chunk_id)

    # 1. Missing hit -> SELECTED_HIT_NOT_FOUND
    with pytest.raises(EvidenceAuthorityValidationError) as exc1:
        await issue_assessment_eligibility_authority(req, store)
    assert exc1.value.code == EvidenceAuthorityErrorCode.SELECTED_HIT_NOT_FOUND

    # 2. Hit exists but selected=False -> HIT_NOT_SELECTED
    store.selected_hits[key] = False
    with pytest.raises(EvidenceAuthorityValidationError) as exc2:
        await issue_assessment_eligibility_authority(req, store)
    assert exc2.value.code == EvidenceAuthorityErrorCode.HIT_NOT_SELECTED


@pytest.mark.asyncio
async def test_exact_source_binding_enforced_fail_closed() -> None:
    """Test 5: Source/member/content mismatch fails closed."""
    assert issue_assessment_eligibility_authority is not None
    store = InMemoryEvidenceAuthorityStore()
    req = _make_sample_request()
    key = (req.retrieval_run_id, req.knowledge_chunk_id)
    store.selected_hits[key] = True

    # 1. Missing source binding in DB
    with pytest.raises(EvidenceAuthorityValidationError) as exc1:
        await issue_assessment_eligibility_authority(req, store)
    assert exc1.value.code == EvidenceAuthorityErrorCode.SOURCE_BINDING_MISMATCH

    # 2. Source version mismatch
    store.source_bindings[key] = ChunkSourceBinding(
        retrieval_run_id=req.retrieval_run_id,
        knowledge_chunk_id=req.knowledge_chunk_id,
        source_snapshot_id=req.source_snapshot_id,
        source_snapshot_member_id=req.source_snapshot_member_id,
        source_code=req.source_code,
        source_version="DIFFERENT_VERSION",
        content_sha256=req.content_sha256,
    )
    with pytest.raises(EvidenceAuthorityValidationError) as exc2:
        await issue_assessment_eligibility_authority(req, store)
    assert exc2.value.code == EvidenceAuthorityErrorCode.SOURCE_BINDING_MISMATCH

    # 3. Content hash mismatch
    store.source_bindings[key] = ChunkSourceBinding(
        retrieval_run_id=req.retrieval_run_id,
        knowledge_chunk_id=req.knowledge_chunk_id,
        source_snapshot_id=req.source_snapshot_id,
        source_snapshot_member_id=req.source_snapshot_member_id,
        source_code=req.source_code,
        source_version=req.source_version,
        content_sha256="b" * 64,
    )
    with pytest.raises(EvidenceAuthorityValidationError) as exc3:
        await issue_assessment_eligibility_authority(req, store)
    assert exc3.value.code == EvidenceAuthorityErrorCode.CHUNK_CONTENT_MISMATCH


@pytest.mark.asyncio
async def test_idempotent_retry_returns_existing() -> None:
    """Test 7: Idempotent issuance returns identical record without duplicate store write."""
    assert issue_assessment_eligibility_authority is not None
    store = InMemoryEvidenceAuthorityStore()
    req = _make_sample_request()
    key = (req.retrieval_run_id, req.knowledge_chunk_id)
    store.selected_hits[key] = True
    store.source_bindings[key] = ChunkSourceBinding(
        retrieval_run_id=req.retrieval_run_id,
        knowledge_chunk_id=req.knowledge_chunk_id,
        source_snapshot_id=req.source_snapshot_id,
        source_snapshot_member_id=req.source_snapshot_member_id,
        source_code=req.source_code,
        source_version=req.source_version,
        content_sha256=req.content_sha256,
    )

    auth1 = await issue_assessment_eligibility_authority(req, store)
    initial_call_count = store.persist_call_count

    auth2 = await issue_assessment_eligibility_authority(req, store)
    assert auth1 == auth2
    assert len(store.authorities) == 1
    # 재시도는 writer를 다시 호출하지 않는다 (PD-722 §6.2 retry determinism).
    assert store.persist_call_count == initial_call_count


@pytest.mark.asyncio
async def test_authority_identity_conflict_fails_closed() -> None:
    """Test 8: Conflicting details for same identity raises EvidenceAuthorityConflictError."""
    assert issue_assessment_eligibility_authority is not None
    store = InMemoryEvidenceAuthorityStore()
    run_id = uuid4()
    chunk_id = uuid4()
    key = (run_id, chunk_id)
    store.selected_hits[key] = True

    # 1. Issue with snapshot A
    req1 = _make_sample_request(retrieval_run_id=run_id, knowledge_chunk_id=chunk_id)
    store.source_bindings[key] = ChunkSourceBinding(
        retrieval_run_id=run_id,
        knowledge_chunk_id=chunk_id,
        source_snapshot_id=req1.source_snapshot_id,
        source_snapshot_member_id=req1.source_snapshot_member_id,
        source_code=req1.source_code,
        source_version=req1.source_version,
        content_sha256=req1.content_sha256,
    )
    await issue_assessment_eligibility_authority(req1, store)

    # 2. Try issuing again for same run_id & chunk_id but with different snapshot B
    req2 = IssueAssessmentAuthorityRequest(
        retrieval_run_id=run_id,
        knowledge_chunk_id=chunk_id,
        source_snapshot_id=uuid4(),  # Different snapshot ID!
        source_snapshot_member_id=req1.source_snapshot_member_id,
        source_code=req1.source_code,
        source_version=req1.source_version,
        content_sha256=req1.content_sha256,
        evaluated_at=req1.evaluated_at,
    )
    store.source_bindings[key] = ChunkSourceBinding(
        retrieval_run_id=run_id,
        knowledge_chunk_id=chunk_id,
        source_snapshot_id=req2.source_snapshot_id,
        source_snapshot_member_id=req2.source_snapshot_member_id,
        source_code=req2.source_code,
        source_version=req2.source_version,
        content_sha256=req2.content_sha256,
    )

    with pytest.raises(EvidenceAuthorityConflictError) as exc:
        await issue_assessment_eligibility_authority(req2, store)
    assert exc.value.code == EvidenceAuthorityErrorCode.AUTHORITY_IDENTITY_CONFLICT


@pytest.mark.asyncio
async def test_cross_run_isolation_same_chunk() -> None:
    """Test 9: Same chunk under different retrieval runs yields separate authority identities."""
    assert issue_assessment_eligibility_authority is not None
    store = InMemoryEvidenceAuthorityStore()
    chunk_id = uuid4()
    run1 = uuid4()
    run2 = uuid4()

    req1 = _make_sample_request(retrieval_run_id=run1, knowledge_chunk_id=chunk_id)
    req2 = _make_sample_request(retrieval_run_id=run2, knowledge_chunk_id=chunk_id)

    store.selected_hits[(run1, chunk_id)] = True
    store.source_bindings[(run1, chunk_id)] = ChunkSourceBinding(
        retrieval_run_id=run1,
        knowledge_chunk_id=chunk_id,
        source_snapshot_id=req1.source_snapshot_id,
        source_snapshot_member_id=req1.source_snapshot_member_id,
        source_code=req1.source_code,
        source_version=req1.source_version,
        content_sha256=req1.content_sha256,
    )

    store.selected_hits[(run2, chunk_id)] = True
    store.source_bindings[(run2, chunk_id)] = ChunkSourceBinding(
        retrieval_run_id=run2,
        knowledge_chunk_id=chunk_id,
        source_snapshot_id=req2.source_snapshot_id,
        source_snapshot_member_id=req2.source_snapshot_member_id,
        source_code=req2.source_code,
        source_version=req2.source_version,
        content_sha256=req2.content_sha256,
    )

    auth1 = await issue_assessment_eligibility_authority(req1, store)
    auth2 = await issue_assessment_eligibility_authority(req2, store)

    assert auth1.id != auth2.id
    assert auth1.retrieval_run_id == run1
    assert auth2.retrieval_run_id == run2
    assert auth1.knowledge_chunk_id == auth2.knowledge_chunk_id == chunk_id
    assert len(store.authorities) == 2


def test_corrupt_or_malformed_artifact_ref_rejected() -> None:
    """Test 10: Non-64-hex SHA256 or malformed artifact ref rejected."""
    # Invalid length
    ref_short = ImmutableArtifactRef("code", "v1", "abc")
    assert not is_valid_immutable_artifact_ref(ref_short)

    # Non-hex characters
    ref_non_hex = ImmutableArtifactRef("code", "v1", "g" * 64)
    assert not is_valid_immutable_artifact_ref(ref_non_hex)

    # Empty code or version
    ref_empty_code = ImmutableArtifactRef("", "v1", "a" * 64)
    assert not is_valid_immutable_artifact_ref(ref_empty_code)

    # Valid
    ref_valid = ImmutableArtifactRef("code", "v1", "a" * 64)
    assert is_valid_immutable_artifact_ref(ref_valid)

    # Malformed verifier in compute_eligibility_receipt_ref raises
    with pytest.raises(EvidenceAuthorityValidationError) as exc:
        compute_eligibility_receipt_ref(
            retrieval_run_id=uuid4(),
            knowledge_chunk_id=uuid4(),
            source_snapshot_id=uuid4(),
            source_snapshot_member_id=uuid4(),
            source_code="source",
            source_version="v1",
            content_sha256="a" * 64,
            evaluated_at=datetime.now(UTC),
            verifier_artifact_ref=ref_short,
        )
    assert exc.value.code == EvidenceAuthorityErrorCode.MALFORMED_ARTIFACT_REF


@pytest.mark.asyncio
async def test_verifier_identity_is_writer_owned_and_artifact_ref_reproducible() -> None:
    """Test 11: caller는 verifier identity를 고를 수 없고, 발급된 ref는 계약으로 재현된다.

    Issue #712는 "caller가 전달한 verifier ref 신뢰"와 "Reader가 verifier ref 생성"을 모두
    금지한다. 따라서 `IssueAssessmentAuthorityRequest`에는 verifier 입력 자리가 없어야 하고,
    발급 결과의 verifier·assessment ref는 순수 계약 함수만으로 재계산되어야 한다.
    """
    assert issue_assessment_eligibility_authority is not None
    assert not hasattr(IssueAssessmentAuthorityRequest, "verifier_ref")

    store = InMemoryEvidenceAuthorityStore()
    req = _make_sample_request()
    key = (req.retrieval_run_id, req.knowledge_chunk_id)
    store.selected_hits[key] = True
    store.source_bindings[key] = ChunkSourceBinding(
        retrieval_run_id=req.retrieval_run_id,
        knowledge_chunk_id=req.knowledge_chunk_id,
        source_snapshot_id=req.source_snapshot_id,
        source_snapshot_member_id=req.source_snapshot_member_id,
        source_code=req.source_code,
        source_version=req.source_version,
        content_sha256=req.content_sha256,
    )

    authority = await issue_assessment_eligibility_authority(req, store)

    expected_verifier_ref = compute_verifier_artifact_ref()
    assert authority.verifier_artifact_ref == expected_verifier_ref

    expected_eligibility_ref = compute_eligibility_receipt_ref(
        retrieval_run_id=req.retrieval_run_id,
        knowledge_chunk_id=req.knowledge_chunk_id,
        source_snapshot_id=req.source_snapshot_id,
        source_snapshot_member_id=req.source_snapshot_member_id,
        source_code=req.source_code,
        source_version=req.source_version,
        content_sha256=req.content_sha256,
        evaluated_at=req.evaluated_at,
        verifier_artifact_ref=expected_verifier_ref,
    )
    assert authority.eligibility_receipt_ref == expected_eligibility_ref

    expected_assessment_ref = compute_assessment_artifact_ref(
        retrieval_run_id=req.retrieval_run_id,
        knowledge_chunk_id=req.knowledge_chunk_id,
        eligibility_receipt_ref=expected_eligibility_ref,
        validity_policy_ref=compute_validity_policy_ref(req.policy),
        assessment_valid_from=authority.assessment_valid_from,
        assessment_valid_until=authority.assessment_valid_until,
    )
    assert authority.assessment_artifact_ref == expected_assessment_ref
