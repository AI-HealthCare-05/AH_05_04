"""#712 Track F Evidence Assessment·Eligibility Authority Repository.

이 Repository는 이미 권위 있는 평가 사실을 바탕으로 발급된 불변 Evidence Authority를
영속화하고, exact identity 및 artifact reference로 되돌려 주는 책임을 담당합니다.

불변성 및 거버넌스 원칙:
- `backend` -> `ai_worker` 직접 import 금지 (AGENTS.md 및 PD-175 준수, `rag_runtime` 계약만 소비)
- 불변 identity: UNIQUE(retrieval_run_id, knowledge_chunk_id)
- 고유 artifact ref: UNIQUE(assessment_artifact_code, assessment_artifact_version, assessment_artifact_sha256)
- 멱등성: 동일한 입력으로 재시도 시 기존 레코드를 변경 없이 반환 (validity window 연장 금지)
- 충돌 처리: 동일 identity에 다른 내용 존재 시 덮어쓰지 않고 fail-closed (EvidenceAuthorityConflictError)
- 트랜잭션: caller가 session/transaction 경계를 제어할 수 있도록 스스로 commit하지 않고 flush 수행
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeChunk, RagKnowledgeIndexMember
from app.models.rag_evidence_authority import RagEvidenceAuthority
from app.models.rag_retrieval import RetrievalHit, RetrievalRun
from rag_runtime.evidence_authority import (
    ChunkSourceBinding,
    EvidenceAuthorityConflictError,
    EvidenceAuthorityCorruptError,
    EvidenceAuthorityErrorCode,
    ImmutableArtifactRef,
    PersistedEvidenceAuthority,
    is_valid_immutable_artifact_ref,
)

__all__ = ["RagEvidenceAuthorityRepository"]


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _to_persisted_authority(row: RagEvidenceAuthority) -> PersistedEvidenceAuthority:
    eligibility_ref = ImmutableArtifactRef(
        artifact_code=row.eligibility_receipt_artifact_code,
        version=row.eligibility_receipt_version,
        content_sha256=row.eligibility_receipt_sha256,
    )
    assessment_ref = ImmutableArtifactRef(
        artifact_code=row.assessment_artifact_code,
        version=row.assessment_artifact_version,
        content_sha256=row.assessment_artifact_sha256,
    )
    verifier_ref = ImmutableArtifactRef(
        artifact_code=row.verifier_artifact_code,
        version=row.verifier_artifact_version,
        content_sha256=row.verifier_artifact_sha256,
    )
    validity_policy_ref = ImmutableArtifactRef(
        artifact_code=row.validity_policy_artifact_code,
        version=row.validity_policy_version,
        content_sha256=row.validity_policy_sha256,
    )

    for ref in (eligibility_ref, assessment_ref, verifier_ref, validity_policy_ref):
        if not is_valid_immutable_artifact_ref(ref):
            raise EvidenceAuthorityCorruptError(
                EvidenceAuthorityErrorCode.CORRUPT_AUTHORITY_RECORD,
                f"Corrupt artifact ref in persisted row {row.id}: {ref}",
            )

    return PersistedEvidenceAuthority(
        id=row.id,
        retrieval_run_id=row.retrieval_run_id,
        knowledge_chunk_id=row.knowledge_chunk_id,
        source_snapshot_id=row.source_snapshot_id,
        source_snapshot_member_id=row.source_snapshot_member_id,
        source_code=row.source_code,
        source_version=row.source_version,
        content_sha256=row.content_sha256,
        eligibility_receipt_ref=eligibility_ref,
        assessment_artifact_ref=assessment_ref,
        verifier_artifact_ref=verifier_ref,
        validity_policy_ref=validity_policy_ref,
        evaluated_at=_ensure_utc(row.evaluated_at),
        assessment_valid_from=_ensure_utc(row.assessment_valid_from),
        assessment_valid_until=_ensure_utc(row.assessment_valid_until),
        created_at=_ensure_utc(row.created_at) if row.created_at is not None else None,
    )


class RagEvidenceAuthorityRepository:
    """Repository for managing immutable RagEvidenceAuthority persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_selected_hit(
        self,
        retrieval_run_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> bool | None:
        """Returns True if hit exists and selected==True, False if selected==False, None if hit not found."""
        stmt = select(RetrievalHit.selected).where(
            RetrievalHit.retrieval_run_id == retrieval_run_id,
            RetrievalHit.knowledge_chunk_id == knowledge_chunk_id,
        )
        result = await self._session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        return bool(row[0])

    async def get_chunk_source_binding(
        self,
        retrieval_run_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> ChunkSourceBinding | None:
        """Returns DB-persisted chunk source binding or None if not found.

        `content_sha256`은 index member가 색인 시점에 기록한 `content_hash`를 정본으로 쓴다.
        `knowledge_chunk.content_hash`는 nullable이고 hydration 이후에 채워지므로 단독으로는
        과거 selection의 불변 binding 정본이 될 수 없다. 대신 이미 authoritative한
        `PostgreSqlEvidenceEligibilityVerifier._is_provenance_matching`과 같은 규칙으로 두 값이
        일치할 때만 binding을 인정하고, 어긋나면 authority를 발급하지 않도록 None을 돌려준다.
        """
        stmt = (
            select(
                RagKnowledgeIndexMember.source_snapshot_id,
                RagKnowledgeIndexMember.source_snapshot_member_id,
                RagKnowledgeIndexMember.source_code,
                RagKnowledgeIndexMember.source_version,
                RagKnowledgeIndexMember.content_hash,
                KnowledgeChunk.content_hash.label("chunk_content_hash"),
            )
            .select_from(RetrievalRun)
            .join(
                RagKnowledgeIndexMember,
                RagKnowledgeIndexMember.knowledge_index_id == RetrievalRun.knowledge_index_id,
            )
            .join(KnowledgeChunk, KnowledgeChunk.id == RagKnowledgeIndexMember.knowledge_chunk_id)
            .where(
                RetrievalRun.id == retrieval_run_id,
                RagKnowledgeIndexMember.knowledge_chunk_id == knowledge_chunk_id,
            )
        )
        result = await self._session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        if row.chunk_content_hash != row.content_hash:
            return None
        return ChunkSourceBinding(
            retrieval_run_id=retrieval_run_id,
            knowledge_chunk_id=knowledge_chunk_id,
            source_snapshot_id=row.source_snapshot_id,
            source_snapshot_member_id=row.source_snapshot_member_id,
            source_code=row.source_code,
            source_version=row.source_version,
            content_sha256=row.content_hash,
        )

    async def get_authority_by_identity(
        self,
        retrieval_run_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> PersistedEvidenceAuthority | None:
        """Returns persisted authority for (retrieval_run_id, knowledge_chunk_id) or None."""
        stmt = select(RagEvidenceAuthority).where(
            RagEvidenceAuthority.retrieval_run_id == retrieval_run_id,
            RagEvidenceAuthority.knowledge_chunk_id == knowledge_chunk_id,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return _to_persisted_authority(row)

    async def get_authority_by_assessment_ref(
        self,
        assessment_ref: ImmutableArtifactRef,
    ) -> PersistedEvidenceAuthority | None:
        """Returns persisted authority matching exact assessment artifact ref or None."""
        stmt = select(RagEvidenceAuthority).where(
            RagEvidenceAuthority.assessment_artifact_code == assessment_ref.artifact_code,
            RagEvidenceAuthority.assessment_artifact_version == assessment_ref.version,
            RagEvidenceAuthority.assessment_artifact_sha256 == assessment_ref.content_sha256,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return _to_persisted_authority(row)

    async def persist_authority(
        self,
        record: PersistedEvidenceAuthority,
    ) -> PersistedEvidenceAuthority:
        """Persists authority record idempotently.

        If row exists for (retrieval_run_id, knowledge_chunk_id):
        - If all semantic fields match: returns existing record (retry determinism).
        - If any semantic field differs: raises EvidenceAuthorityConflictError (fail-closed).
        """
        existing = await self.get_authority_by_identity(record.retrieval_run_id, record.knowledge_chunk_id)
        if existing is not None:
            if (
                existing.source_snapshot_id == record.source_snapshot_id
                and existing.source_snapshot_member_id == record.source_snapshot_member_id
                and existing.source_code == record.source_code
                and existing.source_version == record.source_version
                and existing.content_sha256 == record.content_sha256
                and existing.eligibility_receipt_ref == record.eligibility_receipt_ref
                and existing.assessment_artifact_ref == record.assessment_artifact_ref
                and existing.verifier_artifact_ref == record.verifier_artifact_ref
                and existing.validity_policy_ref == record.validity_policy_ref
            ):
                return existing
            raise EvidenceAuthorityConflictError(
                EvidenceAuthorityErrorCode.AUTHORITY_IDENTITY_CONFLICT,
                f"Conflicting authority details for run={record.retrieval_run_id}, chunk={record.knowledge_chunk_id}",
            )

        row = RagEvidenceAuthority(
            id=record.id,
            retrieval_run_id=record.retrieval_run_id,
            knowledge_chunk_id=record.knowledge_chunk_id,
            source_snapshot_id=record.source_snapshot_id,
            source_snapshot_member_id=record.source_snapshot_member_id,
            source_code=record.source_code,
            source_version=record.source_version,
            content_sha256=record.content_sha256,
            eligibility_receipt_artifact_code=record.eligibility_receipt_ref.artifact_code,
            eligibility_receipt_version=record.eligibility_receipt_ref.version,
            eligibility_receipt_sha256=record.eligibility_receipt_ref.content_sha256,
            assessment_artifact_code=record.assessment_artifact_ref.artifact_code,
            assessment_artifact_version=record.assessment_artifact_ref.version,
            assessment_artifact_sha256=record.assessment_artifact_ref.content_sha256,
            verifier_artifact_code=record.verifier_artifact_ref.artifact_code,
            verifier_artifact_version=record.verifier_artifact_ref.version,
            verifier_artifact_sha256=record.verifier_artifact_ref.content_sha256,
            validity_policy_artifact_code=record.validity_policy_ref.artifact_code,
            validity_policy_version=record.validity_policy_ref.version,
            validity_policy_sha256=record.validity_policy_ref.content_sha256,
            evaluated_at=record.evaluated_at,
            assessment_valid_from=record.assessment_valid_from,
            assessment_valid_until=record.assessment_valid_until,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_persisted_authority(row)
