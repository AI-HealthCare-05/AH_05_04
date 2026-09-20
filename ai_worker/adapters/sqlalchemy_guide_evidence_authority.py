"""Read-only SQLAlchemy adapter for the production `GuideEvidenceAuthorityReaderPort` (#709).

#713이 남긴 historical REQUEST authority 증거를 exact `ImmutableArtifactRef`로 읽어 기존
`Authoritative*Observation`으로 투영합니다. 그 이상은 하지 않습니다.

Boundaries:
- Read-Only: transaction을 REPEATABLE READ / READ ONLY로 선언합니다. INSERT·UPDATE·DELETE·
  `SELECT ... FOR UPDATE`·advisory lock을 쓰지 않고 schema/migration에도 관여하지 않습니다.
- Package Boundary: `PD-175-20260910` 경계를 지키기 위해 backend ORM을 import하지 않고,
  SQLAlchemy Core `table()`/`column()`로 read-only persistence shape만 선언합니다. 공유 의미의
  정본은 `rag_runtime.request_authority`입니다.
- Exact Lookup: `artifact_code`·`artifact_version`·`artifact_content_sha256` 3열 equality만
  사용합니다. latest·CURRENT·newest·`ORDER BY created_at`·PK fallback·부분 일치가 없습니다.
- No Decision Re-Judgement: PASS/FAIL을 계산하거나 추론하지 않습니다. 저장된 실제 결과만
  투영하며, 호출자가 넘긴 값을 관측치 필드로 되돌려 쓰지 않습니다. Source Decision과 Member
  Decision의 `request_guard_ref`도 저장된 값에서 복원합니다.
- Persisted Identity Re-Verification: row를 찾았다는 사실만으로 반환하지 않고, 저장된 semantic
  facts로 `rag_runtime`의 canonical identity를 다시 계산해 요청한 ref와 대조합니다. 새 hash나
  canonicalization을 정의하지 않습니다.
- Ambiguity: UNIQUE 제약이 이미 이 조회를 최대 한 행으로 만들지만, 방어적으로 두 행 이상을
  데이터 무결성 실패로 거부합니다. `LIMIT`·정렬·최신 행 선택을 쓰지 않습니다.
- Errors: 정상 no-row만 `None`입니다. 예상 가능한 persistence/데이터 실패만
  `GuideEvidenceAuthorityReaderError`로 바꾸고, programming bug는 그대로 전파합니다. 로그에는
  예외 클래스 이름만 남기고 행·SQL·연결 정보를 남기지 않습니다.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import String, and_, column, select, table, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, is_valid_immutable_artifact_ref
from ai_worker.tasks.rag.guide_evidence_authority import (
    AuthoritativeMemberDecisionObservation,
    AuthoritativeRequestGuardObservation,
    AuthoritativeSourceDecisionObservation,
    GuideEvidenceAuthorityReaderError,
    GuideRequestAuthorityDecisionRefs,
    GuideRequestAuthorityLookupCoordinate,
)
from ai_worker.tasks.rag.guide_evidence_handoff import ObservedDecisionOutcome, RequestDecisionStage
from ai_worker.tasks.rag.request_authority_artifact import (
    shared_artifact_ref,
    shared_member_identity,
    worker_artifact_ref,
    worker_member_identity,
)
from ai_worker.tasks.rag.source_member_identity import persisted_member_kind_value
from rag_runtime.request_authority import (
    RequestAuthorityArtifactError,
    RequestAuthorityArtifactRef,
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
    RequestAuthorityMemberIdentity,
    compute_request_guard_authority_ref,
    compute_request_member_decision_authority_ref,
    compute_request_source_decision_authority_ref,
    member_kind_from_persisted,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]


def _artifact_columns():
    """SQLAlchemy Core column은 표마다 새 객체여야 하므로 매번 새로 만든다."""
    return (
        column("artifact_code", String(100)),
        column("artifact_version", String(50)),
        column("artifact_content_sha256", String(64)),
    )


def _guard_ref_columns():
    return (
        column("request_guard_artifact_code", String(100)),
        column("request_guard_artifact_version", String(50)),
        column("request_guard_content_sha256", String(64)),
    )


def _request_columns():
    return (
        column("user_id", String(36)),
        column("request_operation_code", String(100)),
        column("decision_stage", String(20)),
    )


_GUARD = table("rag_request_guard_authority", *_artifact_columns(), *_request_columns())
_SOURCE_DECISION = table(
    "rag_request_source_decision",
    *_artifact_columns(),
    *_guard_ref_columns(),
    *_request_columns(),
    column("source_snapshot_id", String(36)),
    column("source_code", String(200)),
    column("source_version", String(200)),
    column("actual_decision_outcome", String(10)),
)
_MEMBER_DECISION = table(
    "rag_request_member_decision",
    *_artifact_columns(),
    *_guard_ref_columns(),
    *_request_columns(),
    column("source_snapshot_id", String(36)),
    column("source_snapshot_member_id", String(36)),
    column("member_kind", String(30)),
    column("endpoint_code", String(200)),
    column("operation_code", String(200)),
    column("member_artifact_code", String(200)),
    column("member_artifact_version", String(200)),
    column("actual_decision_outcome", String(10)),
)

_OUTCOME_TO_KERNEL = {
    RequestAuthorityDecisionOutcome.PASS: ObservedDecisionOutcome.PASS,
    RequestAuthorityDecisionOutcome.FAIL: ObservedDecisionOutcome.FAIL,
}


class _CorruptAuthorityRowError(Exception):
    """저장된 authority가 계약을 만족하지 못할 때 내부적으로만 쓰는 신호."""


def _exact_where(source, artifact_ref: RequestAuthorityArtifactRef):
    """Exact artifact identity 3열 equality. 다른 조회 조건을 쓰지 않는다."""
    return and_(
        source.c.artifact_code == artifact_ref.artifact_code,
        source.c.artifact_version == artifact_ref.version,
        source.c.artifact_content_sha256 == artifact_ref.content_sha256,
    )


def _guard_statement(request_guard_ref: ImmutableArtifactRef | RequestAuthorityArtifactRef):
    shared = _shared_ref(request_guard_ref)
    return select(*_GUARD.c).select_from(_GUARD).where(_exact_where(_GUARD, shared))


def _source_decision_statement(request_source_decision_ref: ImmutableArtifactRef | RequestAuthorityArtifactRef):
    shared = _shared_ref(request_source_decision_ref)
    return select(*_SOURCE_DECISION.c).select_from(_SOURCE_DECISION).where(_exact_where(_SOURCE_DECISION, shared))


def _member_decision_statement(request_member_decision_ref: ImmutableArtifactRef | RequestAuthorityArtifactRef):
    shared = _shared_ref(request_member_decision_ref)
    return select(*_MEMBER_DECISION.c).select_from(_MEMBER_DECISION).where(_exact_where(_MEMBER_DECISION, shared))


def _guard_ref_where(source, request_guard_ref: RequestAuthorityArtifactRef):
    return and_(
        source.c.request_guard_artifact_code == request_guard_ref.artifact_code,
        source.c.request_guard_artifact_version == request_guard_ref.version,
        source.c.request_guard_content_sha256 == request_guard_ref.content_sha256,
    )


def _nullable_exact(column_, value: str | None):
    return column_.is_(None) if value is None else column_ == value


def _source_coordinate_statement(
    coordinate: GuideRequestAuthorityLookupCoordinate,
    request_guard_ref: ImmutableArtifactRef | RequestAuthorityArtifactRef,
):
    shared_request_guard_ref = _shared_ref(request_guard_ref)
    return (
        select(*_SOURCE_DECISION.c)
        .select_from(_SOURCE_DECISION)
        .where(
            _guard_ref_where(_SOURCE_DECISION, shared_request_guard_ref),
            _SOURCE_DECISION.c.user_id == str(coordinate.user_id),
            _SOURCE_DECISION.c.request_operation_code == coordinate.request_operation_code,
            _SOURCE_DECISION.c.decision_stage == coordinate.decision_stage.value,
            _SOURCE_DECISION.c.source_snapshot_id == str(coordinate.source_snapshot_id),
            _SOURCE_DECISION.c.source_code == coordinate.source_code,
            _SOURCE_DECISION.c.source_version == coordinate.source_version,
            _SOURCE_DECISION.c.actual_decision_outcome == coordinate.expected_source_decision_outcome.value,
        )
    )


def _member_coordinate_statement(
    coordinate: GuideRequestAuthorityLookupCoordinate,
    request_guard_ref: ImmutableArtifactRef | RequestAuthorityArtifactRef,
):
    shared_request_guard_ref = _shared_ref(request_guard_ref)
    identity = shared_member_identity(coordinate.member_identity)
    persisted_kind = persisted_member_kind_value(coordinate.member_identity.member_kind)
    return (
        select(*_MEMBER_DECISION.c)
        .select_from(_MEMBER_DECISION)
        .where(
            _guard_ref_where(_MEMBER_DECISION, shared_request_guard_ref),
            _MEMBER_DECISION.c.user_id == str(coordinate.user_id),
            _MEMBER_DECISION.c.request_operation_code == coordinate.request_operation_code,
            _MEMBER_DECISION.c.decision_stage == coordinate.decision_stage.value,
            _MEMBER_DECISION.c.source_snapshot_id == str(coordinate.source_snapshot_id),
            _MEMBER_DECISION.c.source_snapshot_member_id == str(coordinate.source_snapshot_member_id),
            _MEMBER_DECISION.c.member_kind == persisted_kind,
            _nullable_exact(_MEMBER_DECISION.c.endpoint_code, identity.endpoint_code),
            _nullable_exact(_MEMBER_DECISION.c.operation_code, identity.operation_code),
            _nullable_exact(_MEMBER_DECISION.c.member_artifact_code, identity.artifact_code),
            _nullable_exact(_MEMBER_DECISION.c.member_artifact_version, identity.artifact_version),
            _MEMBER_DECISION.c.actual_decision_outcome == coordinate.expected_member_decision_outcome.value,
        )
    )


def _shared_ref(value: ImmutableArtifactRef | RequestAuthorityArtifactRef) -> RequestAuthorityArtifactRef:
    if type(value) is RequestAuthorityArtifactRef:
        return value
    return shared_artifact_ref(value)


def _persisted_uuid(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _CorruptAuthorityRowError("persisted UUID is malformed") from error


def _persisted_stage(value: object) -> RequestAuthorityDecisionStage:
    try:
        return RequestAuthorityDecisionStage(str(value))
    except ValueError as error:
        raise _CorruptAuthorityRowError("persisted decision stage is unsupported") from error


def _persisted_outcome(value: object) -> RequestAuthorityDecisionOutcome:
    try:
        return RequestAuthorityDecisionOutcome(str(value))
    except ValueError as error:
        raise _CorruptAuthorityRowError("persisted decision outcome is unsupported") from error


def _persisted_artifact_ref(row: RowMapping) -> RequestAuthorityArtifactRef:
    return RequestAuthorityArtifactRef(
        artifact_code=str(row["artifact_code"]),
        version=str(row["artifact_version"]),
        content_sha256=str(row["artifact_content_sha256"]),
    )


def _persisted_guard_ref(row: RowMapping) -> RequestAuthorityArtifactRef:
    """Guard 참조는 저장된 값에서 복원한다. 호출자가 넘긴 guard ref를 복사하지 않는다."""
    return RequestAuthorityArtifactRef(
        artifact_code=str(row["request_guard_artifact_code"]),
        version=str(row["request_guard_artifact_version"]),
        content_sha256=str(row["request_guard_content_sha256"]),
    )


def _persisted_member_identity(row: RowMapping) -> RequestAuthorityMemberIdentity:
    try:
        member_kind = member_kind_from_persisted(str(row["member_kind"]))
    except RequestAuthorityArtifactError as error:
        raise _CorruptAuthorityRowError("persisted member kind is unsupported") from error

    def _optional(name: str) -> str | None:
        value = row[name]
        return None if value is None else str(value)

    return RequestAuthorityMemberIdentity(
        member_kind=member_kind,
        endpoint_code=_optional("endpoint_code"),
        operation_code=_optional("operation_code"),
        artifact_code=_optional("member_artifact_code"),
        artifact_version=_optional("member_artifact_version"),
    )


def _verify_identity(requested: RequestAuthorityArtifactRef, recomputed: RequestAuthorityArtifactRef) -> None:
    if requested != recomputed:
        raise _CorruptAuthorityRowError("persisted authority does not match the requested artifact identity")


def _to_guard_observation(
    row: RowMapping,
    requested: RequestAuthorityArtifactRef,
) -> AuthoritativeRequestGuardObservation:
    user_id = _persisted_uuid(row["user_id"])
    operation_code = str(row["request_operation_code"])
    stage = _persisted_stage(row["decision_stage"])

    try:
        recomputed = compute_request_guard_authority_ref(
            user_id=user_id,
            request_operation_code=operation_code,
            decision_stage=stage,
        )
    except RequestAuthorityArtifactError as error:
        raise _CorruptAuthorityRowError("persisted guard authority is not canonical") from error

    _verify_identity(requested, recomputed)
    _verify_identity(requested, _persisted_artifact_ref(row))

    return AuthoritativeRequestGuardObservation(
        artifact_ref=worker_artifact_ref(recomputed),
        user_id=user_id,
        request_operation_code=operation_code,
        decision_stage=stage.value,
    )


def _to_source_observation(
    row: RowMapping,
    requested: RequestAuthorityArtifactRef,
) -> AuthoritativeSourceDecisionObservation:
    guard_ref = _persisted_guard_ref(row)
    user_id = _persisted_uuid(row["user_id"])
    operation_code = str(row["request_operation_code"])
    stage = _persisted_stage(row["decision_stage"])
    snapshot_id = _persisted_uuid(row["source_snapshot_id"])
    source_code = str(row["source_code"])
    source_version = str(row["source_version"])
    outcome = _persisted_outcome(row["actual_decision_outcome"])

    try:
        recomputed = compute_request_source_decision_authority_ref(
            request_guard_ref=guard_ref,
            user_id=user_id,
            request_operation_code=operation_code,
            decision_stage=stage,
            source_snapshot_id=snapshot_id,
            source_code=source_code,
            source_version=source_version,
            actual_decision_outcome=outcome,
        )
    except RequestAuthorityArtifactError as error:
        raise _CorruptAuthorityRowError("persisted source decision is not canonical") from error

    _verify_identity(requested, recomputed)
    _verify_identity(requested, _persisted_artifact_ref(row))

    return AuthoritativeSourceDecisionObservation(
        artifact_ref=worker_artifact_ref(recomputed),
        request_guard_ref=worker_artifact_ref(guard_ref),
        user_id=user_id,
        request_operation_code=operation_code,
        decision_stage=stage.value,
        source_snapshot_id=snapshot_id,
        source_code=source_code,
        source_version=source_version,
        actual_decision_outcome=_OUTCOME_TO_KERNEL[outcome],
    )


def _to_member_observation(
    row: RowMapping,
    requested: RequestAuthorityArtifactRef,
) -> AuthoritativeMemberDecisionObservation:
    guard_ref = _persisted_guard_ref(row)
    user_id = _persisted_uuid(row["user_id"])
    operation_code = str(row["request_operation_code"])
    stage = _persisted_stage(row["decision_stage"])
    snapshot_id = _persisted_uuid(row["source_snapshot_id"])
    member_id = _persisted_uuid(row["source_snapshot_member_id"])
    member_identity = _persisted_member_identity(row)
    outcome = _persisted_outcome(row["actual_decision_outcome"])

    try:
        recomputed = compute_request_member_decision_authority_ref(
            request_guard_ref=guard_ref,
            user_id=user_id,
            request_operation_code=operation_code,
            decision_stage=stage,
            source_snapshot_id=snapshot_id,
            source_snapshot_member_id=member_id,
            member_identity=member_identity,
            actual_decision_outcome=outcome,
        )
        kernel_identity = worker_member_identity(member_identity)
    except RequestAuthorityArtifactError as error:
        raise _CorruptAuthorityRowError("persisted member decision is not canonical") from error

    _verify_identity(requested, recomputed)
    _verify_identity(requested, _persisted_artifact_ref(row))

    return AuthoritativeMemberDecisionObservation(
        artifact_ref=worker_artifact_ref(recomputed),
        request_guard_ref=worker_artifact_ref(guard_ref),
        user_id=user_id,
        request_operation_code=operation_code,
        decision_stage=stage.value,
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=member_id,
        member_identity=kernel_identity,
        actual_decision_outcome=_OUTCOME_TO_KERNEL[outcome],
    )


class SqlAlchemyGuideEvidenceAuthorityReader:
    """Production `GuideEvidenceAuthorityReaderPort` over the persisted #713 authority."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def read_request_guard(
        self,
        *,
        request_guard_ref: ImmutableArtifactRef,
    ) -> AuthoritativeRequestGuardObservation | None:
        return await self._read(
            requested_ref=request_guard_ref,
            statement_factory=_guard_statement,
            projection=_to_guard_observation,
            kind="REQUEST guard",
        )

    async def read_source_decision(
        self,
        *,
        request_source_decision_ref: ImmutableArtifactRef,
    ) -> AuthoritativeSourceDecisionObservation | None:
        return await self._read(
            requested_ref=request_source_decision_ref,
            statement_factory=_source_decision_statement,
            projection=_to_source_observation,
            kind="Source decision",
        )

    async def read_member_decision(
        self,
        *,
        request_member_decision_ref: ImmutableArtifactRef,
    ) -> AuthoritativeMemberDecisionObservation | None:
        return await self._read(
            requested_ref=request_member_decision_ref,
            statement_factory=_member_decision_statement,
            projection=_to_member_observation,
            kind="Member decision",
        )

    async def lookup_request_decision_refs(
        self,
        *,
        coordinate: GuideRequestAuthorityLookupCoordinate,
    ) -> GuideRequestAuthorityDecisionRefs | None:
        request_guard_ref = _validate_lookup_coordinate(coordinate)
        try:
            async with self._session_factory() as session, session.begin():
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                guard_rows = list((await session.execute(_guard_statement(request_guard_ref))).mappings().all())
                source_rows = list(
                    (await session.execute(_source_coordinate_statement(coordinate, request_guard_ref)))
                    .mappings()
                    .all()
                )
                member_rows = list(
                    (await session.execute(_member_coordinate_statement(coordinate, request_guard_ref)))
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as exc:
            logger.error(
                "REQUEST authority coordinate lookup failed with database exception: %s", exc.__class__.__name__
            )
            raise GuideEvidenceAuthorityReaderError("REQUEST authority coordinate lookup failed") from None

        guard_row = _single_coordinate_row(guard_rows, kind="REQUEST guard")
        source_row = _single_coordinate_row(source_rows, kind="Source decision")
        member_row = _single_coordinate_row(member_rows, kind="Member decision")
        if guard_row is None or source_row is None or member_row is None:
            return None

        try:
            guard = _to_guard_observation(guard_row, request_guard_ref)
            source_ref = _persisted_artifact_ref(source_row)
            member_ref = _persisted_artifact_ref(member_row)
            source = _to_source_observation(source_row, source_ref)
            member = _to_member_observation(member_row, member_ref)
            _verify_lookup_observations(coordinate, guard, source, member)
        except _CorruptAuthorityRowError as error:
            logger.error("REQUEST authority coordinate row is corrupt: %s", error)
            raise GuideEvidenceAuthorityReaderError("REQUEST authority coordinate row is corrupt") from None

        return GuideRequestAuthorityDecisionRefs(
            request_source_decision_ref=worker_artifact_ref(source_ref),
            request_member_decision_ref=worker_artifact_ref(member_ref),
        )

    async def _read(self, *, requested_ref, statement_factory, projection, kind: str):
        try:
            shared = _shared_ref(requested_ref)
        except RequestAuthorityArtifactError as error:
            logger.error("%s authority ref is not a valid artifact identity: %s", kind, error.reason)
            raise GuideEvidenceAuthorityReaderError(f"{kind} authority ref is invalid") from None

        try:
            rows = await self._fetch_rows(statement_factory(shared))
        except SQLAlchemyError as exc:
            logger.error("%s authority read failed with database exception: %s", kind, exc.__class__.__name__)
            raise GuideEvidenceAuthorityReaderError(f"{kind} authority read failed") from None

        if not rows:
            return None
        if len(rows) > 1:
            logger.error("%s authority lookup is ambiguous: %d rows", kind, len(rows))
            raise GuideEvidenceAuthorityReaderError(f"{kind} authority lookup is ambiguous")

        try:
            return projection(rows[0], shared)
        except _CorruptAuthorityRowError as error:
            logger.error("%s authority row is corrupt: %s", kind, error)
            raise GuideEvidenceAuthorityReaderError(f"{kind} authority row is corrupt") from None

    async def _fetch_rows(self, statement) -> list[RowMapping]:
        async with self._session_factory() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            result = await session.execute(statement)
            return list(result.mappings().all())


def _is_nonblank_nfc(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip() and unicodedata.is_normalized("NFC", value)


def _validate_lookup_coordinate(
    coordinate: GuideRequestAuthorityLookupCoordinate,
) -> RequestAuthorityArtifactRef:
    if (
        type(coordinate) is not GuideRequestAuthorityLookupCoordinate
        or not is_valid_immutable_artifact_ref(coordinate.request_guard_ref)
        or type(coordinate.user_id) is not UUID
        or coordinate.decision_stage is not RequestDecisionStage.REQUEST
        or type(coordinate.source_snapshot_id) is not UUID
        or type(coordinate.source_snapshot_member_id) is not UUID
        or not _is_nonblank_nfc(coordinate.request_operation_code)
        or not _is_nonblank_nfc(coordinate.source_code)
        or not _is_nonblank_nfc(coordinate.source_version)
        or type(coordinate.expected_source_decision_outcome) is not ObservedDecisionOutcome
        or type(coordinate.expected_member_decision_outcome) is not ObservedDecisionOutcome
    ):
        raise GuideEvidenceAuthorityReaderError("REQUEST authority lookup coordinate is invalid")
    try:
        shared_member_identity(coordinate.member_identity)
        return shared_artifact_ref(coordinate.request_guard_ref)
    except RequestAuthorityArtifactError:
        raise GuideEvidenceAuthorityReaderError("REQUEST authority lookup coordinate is invalid") from None


def _single_coordinate_row(rows: list[RowMapping], *, kind: str) -> RowMapping | None:
    if not rows:
        return None
    if len(rows) > 1:
        logger.error("%s coordinate lookup is ambiguous: %d rows", kind, len(rows))
        raise GuideEvidenceAuthorityReaderError(f"{kind} coordinate lookup is ambiguous")
    return rows[0]


def _verify_lookup_observations(
    coordinate: GuideRequestAuthorityLookupCoordinate,
    guard: AuthoritativeRequestGuardObservation,
    source: AuthoritativeSourceDecisionObservation,
    member: AuthoritativeMemberDecisionObservation,
) -> None:
    if (
        guard.user_id != coordinate.user_id
        or guard.request_operation_code != coordinate.request_operation_code
        or guard.decision_stage != coordinate.decision_stage.value
        or source.request_guard_ref != coordinate.request_guard_ref
        or source.user_id != coordinate.user_id
        or source.request_operation_code != coordinate.request_operation_code
        or source.decision_stage != coordinate.decision_stage.value
        or source.source_snapshot_id != coordinate.source_snapshot_id
        or source.source_code != coordinate.source_code
        or source.source_version != coordinate.source_version
        or source.actual_decision_outcome is not coordinate.expected_source_decision_outcome
        or member.request_guard_ref != coordinate.request_guard_ref
        or member.user_id != coordinate.user_id
        or member.request_operation_code != coordinate.request_operation_code
        or member.decision_stage != coordinate.decision_stage.value
        or member.source_snapshot_id != coordinate.source_snapshot_id
        or member.source_snapshot_member_id != coordinate.source_snapshot_member_id
        or member.member_identity != coordinate.member_identity
        or member.actual_decision_outcome is not coordinate.expected_member_decision_outcome
    ):
        raise _CorruptAuthorityRowError("persisted authority does not match the exact lookup coordinate")
