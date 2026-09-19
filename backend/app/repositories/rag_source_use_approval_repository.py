"""Backend writer and exact reader for historical Source Use Approval facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_source import RagSource, RagSourceEndpoint, RagSourceOperation, RagSourceSnapshot
from app.models.rag_source_use_approval import RagSourceUseApproval
from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import (
    SourceUseApprovalIdentity,
    SourceUseApprovalObservation,
    SourceUsePurpose,
)
from rag_runtime.source_use_approval import (
    SourceUseApprovalValidationError as PureSourceUseApprovalValidationError,
)


class SourceUseApprovalRepositoryError(Exception):
    """Base error for Source Use Approval persistence."""


class SourceUseApprovalValidationError(SourceUseApprovalRepositoryError):
    """The requested approval is not canonical or is not bound to the source snapshot."""


class SourceUseApprovalConflictError(SourceUseApprovalRepositoryError):
    """An immutable identity already contains different semantic content."""


class SourceUseApprovalNotFoundError(SourceUseApprovalRepositoryError):
    """The requested approval row does not exist."""


class SourceUseApprovalCorruptError(SourceUseApprovalRepositoryError):
    """A persisted row cannot be represented by the pure Source Use Approval contract."""


@dataclass(frozen=True, slots=True)
class SourceUseApprovalCreate:
    source_snapshot_id: UUID
    source_code: str
    source_version: str
    environment: RuntimeEnvironmentCode
    purpose: SourceUsePurpose
    approval_version: str
    valid_from: datetime
    expires_at: datetime
    actor_id: UUID
    evidence_ref: str

    def identity(self) -> SourceUseApprovalIdentity:
        return SourceUseApprovalIdentity(
            source_snapshot_id=self.source_snapshot_id,
            source_code=self.source_code,
            source_version=self.source_version,
            environment=self.environment,
            purpose=self.purpose,
            approval_version=self.approval_version,
        )

    def as_observation(self, *, approval_id: UUID) -> SourceUseApprovalObservation:
        return SourceUseApprovalObservation(
            id=approval_id,
            identity=self.identity(),
            valid_from=self.valid_from,
            expires_at=self.expires_at,
            revoked_at=None,
            revoked_by=None,
            revoked_reason=None,
            actor_id=self.actor_id,
            evidence_ref=self.evidence_ref,
        )

    def __post_init__(self) -> None:
        try:
            self.as_observation(approval_id=uuid4())
        except PureSourceUseApprovalValidationError as error:
            raise SourceUseApprovalValidationError(str(error)) from error


@dataclass(frozen=True, slots=True)
class SourceUseApprovalRevoke:
    approval_id: UUID
    revoked_at: datetime
    revoked_by: UUID
    revoked_reason: str

    def __post_init__(self) -> None:
        if type(self.approval_id) is not UUID or type(self.revoked_by) is not UUID:
            raise SourceUseApprovalValidationError("approval_id and revoked_by must be UUID values")
        if (
            type(self.revoked_at) is not datetime
            or self.revoked_at.tzinfo is None
            or self.revoked_at.utcoffset() is None
        ):
            raise SourceUseApprovalValidationError("revoked_at must be timezone-aware")
        if (
            type(self.revoked_reason) is not str
            or not self.revoked_reason
            or not self.revoked_reason.strip()
            or self.revoked_reason != self.revoked_reason.strip()
        ):
            raise SourceUseApprovalValidationError("revoked_reason must be a nonblank canonical string")


class RagSourceUseApprovalRepository:
    """Append-only approval writer plus exact identity read and one-way revoke.

    The repository owns neither commit nor rollback.  The caller owns the surrounding
    transaction and can persist an approval together with its source governance transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_approval(self, request: SourceUseApprovalCreate) -> SourceUseApprovalObservation:
        if type(request) is not SourceUseApprovalCreate:
            raise SourceUseApprovalValidationError("SourceUseApprovalCreate 형식이 아닙니다")

        await self._assert_source_snapshot_binding(request)
        requested = request.as_observation(approval_id=uuid4())
        existing = await self._select_by_identity(requested.identity)
        if existing is not None:
            observed = self._observation_from_row(existing)
            if observed.semantic_payload() == requested.semantic_payload():
                return observed
            raise SourceUseApprovalConflictError(
                "같은 Source Use Approval identity에 다른 semantic payload가 이미 저장되어 있습니다"
            )

        statement = (
            pg_insert(RagSourceUseApproval)
            .values(
                id=requested.id,
                source_snapshot_id=request.source_snapshot_id,
                source_code=request.source_code,
                source_version=request.source_version,
                environment=request.environment.value,
                purpose=request.purpose.value,
                approval_version=request.approval_version,
                valid_from=request.valid_from,
                expires_at=request.expires_at,
                actor_id=request.actor_id,
                evidence_ref=request.evidence_ref,
            )
            .on_conflict_do_nothing(
                index_elements=["source_snapshot_id", "environment", "purpose", "approval_version"],
            )
            .returning(RagSourceUseApproval.id)
        )
        inserted_id = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted_id is None:
            concurrent = await self._select_by_identity(requested.identity)
            if concurrent is None:
                raise SourceUseApprovalConflictError("Source Use Approval 기록에 실패했습니다")
            observed = self._observation_from_row(concurrent)
            if observed.semantic_payload() != requested.semantic_payload():
                raise SourceUseApprovalConflictError(
                    "동시 retry가 같은 identity에 다른 semantic payload를 제출했습니다"
                )
            return observed

        row = await self._select_by_id(inserted_id)
        if row is None:
            raise SourceUseApprovalCorruptError("삽입한 Source Use Approval을 다시 읽지 못했습니다")
        return self._observation_from_row(row)

    async def get_exact(self, identity: SourceUseApprovalIdentity) -> SourceUseApprovalObservation | None:
        if type(identity) is not SourceUseApprovalIdentity:
            raise SourceUseApprovalValidationError("SourceUseApprovalIdentity 형식이 아닙니다")
        row = await self._select_by_identity(identity)
        return None if row is None else self._observation_from_row(row)

    async def revoke(self, request: SourceUseApprovalRevoke) -> SourceUseApprovalObservation:
        if type(request) is not SourceUseApprovalRevoke:
            raise SourceUseApprovalValidationError("SourceUseApprovalRevoke 형식이 아닙니다")

        result = await self._session.execute(
            select(
                RagSourceUseApproval.revoked_at,
                RagSourceUseApproval.revoked_by,
                RagSourceUseApproval.revoked_reason,
            )
            .where(RagSourceUseApproval.id == request.approval_id)
            .with_for_update()
        )
        revocation = result.mappings().one_or_none()
        if revocation is None:
            raise SourceUseApprovalNotFoundError("Source Use Approval을 찾을 수 없습니다")

        if revocation["revoked_at"] is not None:
            if (
                revocation["revoked_at"] == request.revoked_at
                and revocation["revoked_by"] == request.revoked_by
                and revocation["revoked_reason"] == request.revoked_reason
            ):
                row = await self._select_by_id(request.approval_id)
                if row is None:
                    raise SourceUseApprovalCorruptError("철회된 Source Use Approval을 다시 읽지 못했습니다")
                return self._observation_from_row(row)
            raise SourceUseApprovalConflictError("이미 철회된 approval을 다른 payload로 덮어쓸 수 없습니다")

        await self._session.execute(
            update(RagSourceUseApproval)
            .where(RagSourceUseApproval.id == request.approval_id)
            .values(
                revoked_at=request.revoked_at,
                revoked_by=request.revoked_by,
                revoked_reason=request.revoked_reason,
            )
        )
        await self._session.flush()
        row = await self._select_by_id(request.approval_id)
        if row is None:
            raise SourceUseApprovalCorruptError("철회한 Source Use Approval을 다시 읽지 못했습니다")
        return self._observation_from_row(row)

    async def _assert_source_snapshot_binding(self, request: SourceUseApprovalCreate) -> None:
        result = await self._session.execute(
            select(RagSource.source_code, RagSourceSnapshot.source_version)
            .select_from(RagSourceSnapshot)
            .join(RagSourceOperation, RagSourceSnapshot.operation_id == RagSourceOperation.id)
            .join(RagSourceEndpoint, RagSourceOperation.endpoint_id == RagSourceEndpoint.id)
            .join(RagSource, RagSourceEndpoint.source_id == RagSource.id)
            .where(RagSourceSnapshot.id == request.source_snapshot_id)
        )
        binding = result.one_or_none()
        if binding is None:
            raise SourceUseApprovalValidationError("존재하지 않는 source_snapshot_id입니다")
        source_code, source_version = binding
        if source_code != request.source_code or source_version != request.source_version:
            raise SourceUseApprovalValidationError(
                "source_snapshot_id와 source_code/source_version이 exact-match하지 않습니다"
            )

    async def _select_by_identity(self, identity: SourceUseApprovalIdentity) -> RagSourceUseApproval | None:
        result = await self._session.execute(
            select(RagSourceUseApproval).where(
                RagSourceUseApproval.source_snapshot_id == identity.source_snapshot_id,
                RagSourceUseApproval.source_code == identity.source_code,
                RagSourceUseApproval.source_version == identity.source_version,
                RagSourceUseApproval.environment == identity.environment.value,
                RagSourceUseApproval.purpose == identity.purpose.value,
                RagSourceUseApproval.approval_version == identity.approval_version,
            )
        )
        return result.scalar_one_or_none()

    async def _select_by_id(self, approval_id: UUID) -> RagSourceUseApproval | None:
        return await self._session.scalar(select(RagSourceUseApproval).where(RagSourceUseApproval.id == approval_id))

    @staticmethod
    def _observation_from_row(row: RagSourceUseApproval) -> SourceUseApprovalObservation:
        try:
            identity = SourceUseApprovalIdentity(
                source_snapshot_id=row.source_snapshot_id,
                source_code=row.source_code,
                source_version=row.source_version,
                environment=RuntimeEnvironmentCode(row.environment),
                purpose=SourceUsePurpose(row.purpose),
                approval_version=row.approval_version,
            )
            return SourceUseApprovalObservation(
                id=row.id,
                identity=identity,
                valid_from=row.valid_from,
                expires_at=row.expires_at,
                revoked_at=row.revoked_at,
                revoked_by=row.revoked_by,
                revoked_reason=row.revoked_reason,
                actor_id=row.actor_id,
                evidence_ref=row.evidence_ref,
            )
        except (PureSourceUseApprovalValidationError, ValueError, TypeError) as error:
            raise SourceUseApprovalCorruptError("저장된 Source Use Approval이 pure contract와 어긋납니다") from error


__all__ = [
    "RagSourceUseApprovalRepository",
    "SourceUseApprovalConflictError",
    "SourceUseApprovalCorruptError",
    "SourceUseApprovalCreate",
    "SourceUseApprovalNotFoundError",
    "SourceUseApprovalRevoke",
    "SourceUseApprovalRepositoryError",
    "SourceUseApprovalValidationError",
]
