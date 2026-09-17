"""#713 REQUEST authority append-only writer and exact artifact-ref read primitive.

이 Repository는 이미 authoritative한 Decision 결과를 받아 historical 증거로 남기고, 그 증거를
exact `RequestAuthorityArtifactRef`로 되돌려 주는 것까지만 담당합니다. Source·Member eligibility 재평가,
Retrieval, Evidence Gate, Guideline 생성, Citation 권한 판정은 하지 않습니다.

경계:
- artifact identity는 caller가 고르지 않습니다. `request_authority_artifact` kernel의 canonical
  projection digest로 writer가 확정합니다.
- 조회는 `artifact_code`·`version`·`content_sha256` exact equality뿐입니다. latest/CURRENT/newest
  fallback을 제공하지 않습니다.
- 정상적인 no-row만 `None`입니다. 저장된 사실이 artifact identity와 어긋나거나 지원하지 않는 값이면
  `RequestAuthorityCorruptError`로 드러냅니다.
- update/delete API를 제공하지 않습니다. 같은 identity에 다른 내용이 이미 있으면 덮어쓰지 않고
  `RequestAuthorityConflictError`로 실패합니다.
- 스스로 commit하지 않습니다. transaction 경계는 caller가 소유하므로 한 REQUEST의 Guard·Source·
  Member를 하나의 transaction으로 기록할 수 있습니다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_request_authority import (
    RagRequestGuardAuthority,
    RagRequestMemberDecision,
    RagRequestSourceDecision,
)
from rag_runtime.request_authority import (
    RequestAuthorityArtifactError,
    RequestAuthorityArtifactRef,
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
    RequestAuthorityMemberIdentity,
    compute_request_guard_authority_ref,
    compute_request_member_decision_authority_ref,
    compute_request_source_decision_authority_ref,
    is_valid_request_authority_artifact_ref,
    member_kind_from_persisted,
    persisted_member_kind_value,
)

__all__ = [
    "RagRequestAuthorityRepository",
    "RequestAuthorityConflictError",
    "RequestAuthorityCorruptError",
    "RequestAuthorityValidationError",
    "RequestGuardAuthorityRecord",
    "RequestMemberDecisionRecord",
    "RequestSourceDecisionRecord",
]


class RequestAuthorityValidationError(Exception):
    """입력 authority 사실이 구조·결속 검증을 통과하지 못했습니다."""


class RequestAuthorityConflictError(Exception):
    """같은 immutable identity에 다른 semantic content가 이미 저장되어 있습니다."""


class RequestAuthorityCorruptError(Exception):
    """저장된 authority가 artifact identity와 어긋나거나 지원하지 않는 값을 담고 있습니다."""


@dataclass(frozen=True, slots=True)
class RequestGuardAuthorityRecord:
    user_id: UUID
    request_operation_code: str
    decision_stage: RequestAuthorityDecisionStage


@dataclass(frozen=True, slots=True)
class RequestSourceDecisionRecord:
    request_guard_ref: RequestAuthorityArtifactRef
    user_id: UUID
    request_operation_code: str
    decision_stage: RequestAuthorityDecisionStage
    source_snapshot_id: UUID
    source_code: str
    source_version: str
    actual_decision_outcome: RequestAuthorityDecisionOutcome


@dataclass(frozen=True, slots=True)
class RequestMemberDecisionRecord:
    request_guard_ref: RequestAuthorityArtifactRef
    user_id: UUID
    request_operation_code: str
    decision_stage: RequestAuthorityDecisionStage
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    member_identity: RequestAuthorityMemberIdentity
    actual_decision_outcome: RequestAuthorityDecisionOutcome


def _outcome_from_persisted(value: str) -> RequestAuthorityDecisionOutcome:
    try:
        return RequestAuthorityDecisionOutcome(value)
    except ValueError as error:
        raise RequestAuthorityCorruptError(f"지원하지 않는 decision outcome 값입니다: {value!r}") from error


def _stage_from_persisted(value: str) -> RequestAuthorityDecisionStage:
    try:
        return RequestAuthorityDecisionStage(value)
    except ValueError as error:
        raise RequestAuthorityCorruptError(f"지원하지 않는 decision stage 값입니다: {value!r}") from error


def _guard_ref_from_row(row: RagRequestSourceDecision | RagRequestMemberDecision) -> RequestAuthorityArtifactRef:
    return RequestAuthorityArtifactRef(
        artifact_code=row.request_guard_artifact_code,
        version=row.request_guard_artifact_version,
        content_sha256=row.request_guard_content_sha256,
    )


def _member_identity_from_row(row: RagRequestMemberDecision) -> RequestAuthorityMemberIdentity:
    try:
        member_kind = member_kind_from_persisted(row.member_kind)
    except RequestAuthorityArtifactError as error:
        raise RequestAuthorityCorruptError(f"지원하지 않는 member kind 값입니다: {row.member_kind!r}") from error
    return RequestAuthorityMemberIdentity(
        member_kind=member_kind,
        endpoint_code=row.endpoint_code,
        operation_code=row.operation_code,
        artifact_code=row.member_artifact_code,
        artifact_version=row.member_artifact_version,
    )


class RagRequestAuthorityRepository:
    """REQUEST Guard·Source Decision·Member Decision authority의 append-only 저장·조회 경계."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # REQUEST Guard
    # ------------------------------------------------------------------

    async def record_request_guard_authority(self, record: RequestGuardAuthorityRecord) -> RequestAuthorityArtifactRef:
        """REQUEST Guard 관측치를 append-only로 기록하고 확정된 artifact ref를 돌려줍니다."""
        if type(record) is not RequestGuardAuthorityRecord:
            raise RequestAuthorityValidationError("RequestGuardAuthorityRecord 형식이 아닙니다")

        artifact_ref = self._compute_guard_ref(record)

        existing = await self._select_guard_row(artifact_ref)
        if existing is not None:
            self._assert_guard_matches(existing, record)
            return artifact_ref

        statement = (
            pg_insert(RagRequestGuardAuthority)
            .values(
                id=uuid4(),
                artifact_code=artifact_ref.artifact_code,
                artifact_version=artifact_ref.version,
                artifact_content_sha256=artifact_ref.content_sha256,
                user_id=record.user_id,
                request_operation_code=record.request_operation_code,
                decision_stage=record.decision_stage.value,
            )
            .on_conflict_do_nothing(
                index_elements=["artifact_code", "artifact_version", "artifact_content_sha256"],
            )
            .returning(RagRequestGuardAuthority.id)
        )
        inserted = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted is None:
            # 동시 요청이 같은 authority를 먼저 기록했습니다. 덮어쓰지 않고 내용 일치만 확인합니다.
            concurrent = await self._select_guard_row(artifact_ref)
            if concurrent is None:
                raise RequestAuthorityConflictError("REQUEST Guard authority 기록에 실패했습니다")
            self._assert_guard_matches(concurrent, record)
        return artifact_ref

    async def get_request_guard_authority_by_artifact_ref(
        self,
        artifact_ref: RequestAuthorityArtifactRef,
    ) -> RequestGuardAuthorityRecord | None:
        """exact artifact ref로만 조회합니다. 정상 no-row는 None입니다."""
        row = await self._select_guard_row(self._validated_ref(artifact_ref))
        if row is None:
            return None

        record = RequestGuardAuthorityRecord(
            user_id=row.user_id,
            request_operation_code=row.request_operation_code,
            decision_stage=_stage_from_persisted(row.decision_stage),
        )
        self._assert_persisted_identity(artifact_ref, lambda: self._compute_guard_ref(record))
        return record

    # ------------------------------------------------------------------
    # Source Decision
    # ------------------------------------------------------------------

    async def record_request_source_decision(self, record: RequestSourceDecisionRecord) -> RequestAuthorityArtifactRef:
        """Source Decision 관측치를 append-only로 기록하고 확정된 artifact ref를 돌려줍니다."""
        if type(record) is not RequestSourceDecisionRecord:
            raise RequestAuthorityValidationError("RequestSourceDecisionRecord 형식이 아닙니다")

        artifact_ref = self._compute_source_ref(record)
        await self._assert_guard_binding(
            request_guard_ref=record.request_guard_ref,
            user_id=record.user_id,
            request_operation_code=record.request_operation_code,
            decision_stage=record.decision_stage,
        )

        existing = await self._select_source_row(artifact_ref)
        if existing is not None:
            self._assert_source_matches(existing, record)
            return artifact_ref

        statement = (
            pg_insert(RagRequestSourceDecision)
            .values(
                id=uuid4(),
                artifact_code=artifact_ref.artifact_code,
                artifact_version=artifact_ref.version,
                artifact_content_sha256=artifact_ref.content_sha256,
                request_guard_artifact_code=record.request_guard_ref.artifact_code,
                request_guard_artifact_version=record.request_guard_ref.version,
                request_guard_content_sha256=record.request_guard_ref.content_sha256,
                user_id=record.user_id,
                request_operation_code=record.request_operation_code,
                decision_stage=record.decision_stage.value,
                source_snapshot_id=record.source_snapshot_id,
                source_code=record.source_code,
                source_version=record.source_version,
                actual_decision_outcome=record.actual_decision_outcome.value,
            )
            .on_conflict_do_nothing(
                index_elements=["artifact_code", "artifact_version", "artifact_content_sha256"],
            )
            .returning(RagRequestSourceDecision.id)
        )
        inserted = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted is None:
            concurrent = await self._select_source_row(artifact_ref)
            if concurrent is None:
                raise RequestAuthorityConflictError("Source Decision authority 기록에 실패했습니다")
            self._assert_source_matches(concurrent, record)
        return artifact_ref

    async def get_request_source_decision_by_artifact_ref(
        self,
        artifact_ref: RequestAuthorityArtifactRef,
    ) -> RequestSourceDecisionRecord | None:
        """exact artifact ref로만 조회합니다. 정상 no-row는 None입니다."""
        row = await self._select_source_row(self._validated_ref(artifact_ref))
        if row is None:
            return None

        record = RequestSourceDecisionRecord(
            request_guard_ref=_guard_ref_from_row(row),
            user_id=row.user_id,
            request_operation_code=row.request_operation_code,
            decision_stage=_stage_from_persisted(row.decision_stage),
            source_snapshot_id=row.source_snapshot_id,
            source_code=row.source_code,
            source_version=row.source_version,
            actual_decision_outcome=_outcome_from_persisted(row.actual_decision_outcome),
        )
        self._assert_persisted_identity(artifact_ref, lambda: self._compute_source_ref(record))
        return record

    # ------------------------------------------------------------------
    # Member Decision
    # ------------------------------------------------------------------

    async def record_request_member_decision(self, record: RequestMemberDecisionRecord) -> RequestAuthorityArtifactRef:
        """Member Decision 관측치를 append-only로 기록하고 확정된 artifact ref를 돌려줍니다."""
        if type(record) is not RequestMemberDecisionRecord:
            raise RequestAuthorityValidationError("RequestMemberDecisionRecord 형식이 아닙니다")

        artifact_ref = self._compute_member_ref(record)
        await self._assert_guard_binding(
            request_guard_ref=record.request_guard_ref,
            user_id=record.user_id,
            request_operation_code=record.request_operation_code,
            decision_stage=record.decision_stage,
        )

        try:
            member_kind = persisted_member_kind_value(record.member_identity.member_kind)
        except RequestAuthorityArtifactError as error:
            raise RequestAuthorityValidationError("지원하지 않는 member kind입니다") from error

        existing = await self._select_member_row(artifact_ref)
        if existing is not None:
            self._assert_member_matches(existing, record)
            return artifact_ref

        identity = record.member_identity
        statement = (
            pg_insert(RagRequestMemberDecision)
            .values(
                id=uuid4(),
                artifact_code=artifact_ref.artifact_code,
                artifact_version=artifact_ref.version,
                artifact_content_sha256=artifact_ref.content_sha256,
                request_guard_artifact_code=record.request_guard_ref.artifact_code,
                request_guard_artifact_version=record.request_guard_ref.version,
                request_guard_content_sha256=record.request_guard_ref.content_sha256,
                user_id=record.user_id,
                request_operation_code=record.request_operation_code,
                decision_stage=record.decision_stage.value,
                source_snapshot_id=record.source_snapshot_id,
                source_snapshot_member_id=record.source_snapshot_member_id,
                member_kind=member_kind,
                endpoint_code=identity.endpoint_code,
                operation_code=identity.operation_code,
                member_artifact_code=identity.artifact_code,
                member_artifact_version=identity.artifact_version,
                actual_decision_outcome=record.actual_decision_outcome.value,
            )
            .on_conflict_do_nothing(
                index_elements=["artifact_code", "artifact_version", "artifact_content_sha256"],
            )
            .returning(RagRequestMemberDecision.id)
        )
        inserted = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted is None:
            concurrent = await self._select_member_row(artifact_ref)
            if concurrent is None:
                raise RequestAuthorityConflictError("Member Decision authority 기록에 실패했습니다")
            self._assert_member_matches(concurrent, record)
        return artifact_ref

    async def get_request_member_decision_by_artifact_ref(
        self,
        artifact_ref: RequestAuthorityArtifactRef,
    ) -> RequestMemberDecisionRecord | None:
        """exact artifact ref로만 조회합니다. 정상 no-row는 None입니다."""
        row = await self._select_member_row(self._validated_ref(artifact_ref))
        if row is None:
            return None

        record = RequestMemberDecisionRecord(
            request_guard_ref=_guard_ref_from_row(row),
            user_id=row.user_id,
            request_operation_code=row.request_operation_code,
            decision_stage=_stage_from_persisted(row.decision_stage),
            source_snapshot_id=row.source_snapshot_id,
            source_snapshot_member_id=row.source_snapshot_member_id,
            member_identity=_member_identity_from_row(row),
            actual_decision_outcome=_outcome_from_persisted(row.actual_decision_outcome),
        )
        self._assert_persisted_identity(artifact_ref, lambda: self._compute_member_ref(record))
        return record

    # ------------------------------------------------------------------
    # Identity derivation (writer owns identity; caller cannot choose it)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_guard_ref(record: RequestGuardAuthorityRecord) -> RequestAuthorityArtifactRef:
        try:
            return compute_request_guard_authority_ref(
                user_id=record.user_id,
                request_operation_code=record.request_operation_code,
                decision_stage=record.decision_stage,
            )
        except RequestAuthorityArtifactError as error:
            raise RequestAuthorityValidationError(f"REQUEST Guard authority 검증 실패: {error.reason}") from error

    @staticmethod
    def _compute_source_ref(record: RequestSourceDecisionRecord) -> RequestAuthorityArtifactRef:
        try:
            return compute_request_source_decision_authority_ref(
                request_guard_ref=record.request_guard_ref,
                user_id=record.user_id,
                request_operation_code=record.request_operation_code,
                decision_stage=record.decision_stage,
                source_snapshot_id=record.source_snapshot_id,
                source_code=record.source_code,
                source_version=record.source_version,
                actual_decision_outcome=record.actual_decision_outcome,
            )
        except RequestAuthorityArtifactError as error:
            raise RequestAuthorityValidationError(f"Source Decision authority 검증 실패: {error.reason}") from error

    @staticmethod
    def _compute_member_ref(record: RequestMemberDecisionRecord) -> RequestAuthorityArtifactRef:
        try:
            return compute_request_member_decision_authority_ref(
                request_guard_ref=record.request_guard_ref,
                user_id=record.user_id,
                request_operation_code=record.request_operation_code,
                decision_stage=record.decision_stage,
                source_snapshot_id=record.source_snapshot_id,
                source_snapshot_member_id=record.source_snapshot_member_id,
                member_identity=record.member_identity,
                actual_decision_outcome=record.actual_decision_outcome,
            )
        except RequestAuthorityArtifactError as error:
            raise RequestAuthorityValidationError(f"Member Decision authority 검증 실패: {error.reason}") from error

    @staticmethod
    def _validated_ref(artifact_ref: RequestAuthorityArtifactRef) -> RequestAuthorityArtifactRef:
        if not is_valid_request_authority_artifact_ref(artifact_ref):
            raise RequestAuthorityValidationError("RequestAuthorityArtifactRef 형식이 아닙니다")
        return artifact_ref

    @staticmethod
    def _assert_persisted_identity(
        requested: RequestAuthorityArtifactRef,
        recompute: Callable[[], RequestAuthorityArtifactRef],
    ) -> None:
        """저장된 사실로 identity를 다시 계산해 대조합니다.

        저장된 값이 계약을 만족하지 못해 identity를 계산할 수 없는 경우도 손상으로 봅니다.
        읽기 경로에서는 어느 쪽이든 정상 not-found로 숨기지 않습니다.
        """
        try:
            recomputed = recompute()
        except RequestAuthorityValidationError as error:
            raise RequestAuthorityCorruptError(
                "저장된 authority 사실로 artifact identity를 계산할 수 없습니다"
            ) from error
        if requested != recomputed:
            raise RequestAuthorityCorruptError("저장된 authority 사실이 요청한 artifact identity와 일치하지 않습니다")

    # ------------------------------------------------------------------
    # Exact lookups (no latest / CURRENT / newest fallback)
    # ------------------------------------------------------------------

    async def _select_guard_row(self, artifact_ref: RequestAuthorityArtifactRef) -> RagRequestGuardAuthority | None:
        statement = select(RagRequestGuardAuthority).where(
            RagRequestGuardAuthority.artifact_code == artifact_ref.artifact_code,
            RagRequestGuardAuthority.artifact_version == artifact_ref.version,
            RagRequestGuardAuthority.artifact_content_sha256 == artifact_ref.content_sha256,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def _select_source_row(self, artifact_ref: RequestAuthorityArtifactRef) -> RagRequestSourceDecision | None:
        statement = select(RagRequestSourceDecision).where(
            RagRequestSourceDecision.artifact_code == artifact_ref.artifact_code,
            RagRequestSourceDecision.artifact_version == artifact_ref.version,
            RagRequestSourceDecision.artifact_content_sha256 == artifact_ref.content_sha256,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def _select_member_row(self, artifact_ref: RequestAuthorityArtifactRef) -> RagRequestMemberDecision | None:
        statement = select(RagRequestMemberDecision).where(
            RagRequestMemberDecision.artifact_code == artifact_ref.artifact_code,
            RagRequestMemberDecision.artifact_version == artifact_ref.version,
            RagRequestMemberDecision.artifact_content_sha256 == artifact_ref.content_sha256,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    # ------------------------------------------------------------------
    # Binding and conflict checks
    # ------------------------------------------------------------------

    async def _assert_guard_binding(
        self,
        *,
        request_guard_ref: RequestAuthorityArtifactRef,
        user_id: UUID,
        request_operation_code: str,
        decision_stage: RequestAuthorityDecisionStage,
    ) -> None:
        if not is_valid_request_authority_artifact_ref(request_guard_ref):
            raise RequestAuthorityValidationError("request_guard_ref가 RequestAuthorityArtifactRef 형식이 아닙니다")

        guard = await self._select_guard_row(request_guard_ref)
        if guard is None:
            raise RequestAuthorityValidationError("참조한 REQUEST Guard authority가 저장되어 있지 않습니다")
        if guard.user_id != user_id:
            raise RequestAuthorityValidationError("REQUEST Guard의 소유자와 일치하지 않습니다")
        if guard.request_operation_code != request_operation_code:
            raise RequestAuthorityValidationError("REQUEST Guard의 request_operation_code와 일치하지 않습니다")
        if guard.decision_stage != decision_stage.value:
            raise RequestAuthorityValidationError("REQUEST Guard의 decision_stage와 일치하지 않습니다")

    @staticmethod
    def _assert_guard_matches(row: RagRequestGuardAuthority, record: RequestGuardAuthorityRecord) -> None:
        if (
            row.user_id != record.user_id
            or row.request_operation_code != record.request_operation_code
            or row.decision_stage != record.decision_stage.value
        ):
            raise RequestAuthorityConflictError(
                "같은 artifact identity에 다른 REQUEST Guard 내용이 이미 저장되어 있습니다"
            )

    @staticmethod
    def _assert_source_matches(row: RagRequestSourceDecision, record: RequestSourceDecisionRecord) -> None:
        if (
            _guard_ref_from_row(row) != record.request_guard_ref
            or row.user_id != record.user_id
            or row.request_operation_code != record.request_operation_code
            or row.decision_stage != record.decision_stage.value
            or row.source_snapshot_id != record.source_snapshot_id
            or row.source_code != record.source_code
            or row.source_version != record.source_version
            or row.actual_decision_outcome != record.actual_decision_outcome.value
        ):
            raise RequestAuthorityConflictError(
                "같은 artifact identity에 다른 Source Decision 내용이 이미 저장되어 있습니다"
            )

    @staticmethod
    def _assert_member_matches(row: RagRequestMemberDecision, record: RequestMemberDecisionRecord) -> None:
        if (
            _guard_ref_from_row(row) != record.request_guard_ref
            or row.user_id != record.user_id
            or row.request_operation_code != record.request_operation_code
            or row.decision_stage != record.decision_stage.value
            or row.source_snapshot_id != record.source_snapshot_id
            or row.source_snapshot_member_id != record.source_snapshot_member_id
            or _member_identity_from_row(row) != record.member_identity
            or row.actual_decision_outcome != record.actual_decision_outcome.value
        ):
            raise RequestAuthorityConflictError(
                "같은 artifact identity에 다른 Member Decision 내용이 이미 저장되어 있습니다"
            )
