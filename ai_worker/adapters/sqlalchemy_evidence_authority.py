"""Read-only SQLAlchemy adapter for the production Assessment·Eligibility Authority Reader (#746).

#712 issuer가 `rag_evidence_authority`에 남긴 immutable historical authority를 exact lookup으로
읽고, 저장된 semantic facts로 artifact identity를 재계산해 자기무결성을 확인한 뒤 shared
`PersistedEvidenceAuthority`로 반환합니다. 그 이상은 하지 않습니다.

Boundaries:
- Read-Only: transaction을 REPEATABLE READ / READ ONLY로 선언합니다. INSERT·UPDATE·DELETE·
  `SELECT ... FOR UPDATE`·advisory lock을 쓰지 않고 schema/migration/permission에도 관여하지
  않습니다.
- Package Boundary: `PD-175-20260910` 경계를 지키기 위해 backend ORM(`backend.app.models.*`)을
  import하지 않고, SQLAlchemy Core `table()`/`column()`로 read-only persistence shape만
  선언합니다. 공유 의미의 정본은 `rag_runtime.evidence_authority`입니다.
- Exact Lookup: #712가 확정한 두 조회만 씁니다. selection identity
  (`retrieval_run_id`, `knowledge_chunk_id`)와 assessment artifact identity
  (`assessment_artifact_code`, `assessment_artifact_version`, `assessment_artifact_sha256`)의
  equality뿐이며, latest·CURRENT·newest·`ORDER BY created_at DESC`·PK fallback·부분 일치가
  없습니다.
- Persisted Self-Integrity: row가 존재한다는 사실만으로 authority를 신뢰하지 않습니다. 저장된
  semantic facts로 `rag_runtime.evidence_authority`의 canonical function을 다시 돌려 네 개의
  `ImmutableArtifactRef`를 대조합니다. 새 hash domain이나 canonicalization을 정의하지 않습니다.
- No Currentness Judgement: 현재 Snapshot CURRENT 여부, Source ACTIVE 여부, approval revoke
  여부, verifier deployment, 새 validity window를 재판정하지 않습니다. historical authority를
  현재 DB 상태에서 재구성하지 않습니다.
- Strict UTC: backend Repository는 naive datetime을 UTC로 보정하지만, 이 Reader는 읽기 경계에서
  fail closed해야 하므로 보정하지 않고 non-UTC를 corrupt으로 거부합니다.
- Ambiguity: UNIQUE 제약이 이미 두 조회를 최대 한 행으로 만들지만, 방어적으로 두 행 이상을
  데이터 무결성 실패로 거부합니다. `LIMIT`·정렬·최신 행 선택을 쓰지 않습니다.
- Errors: 정상 no-row만 `None`입니다. 예상 가능한 persistence/데이터 실패만
  `AssessmentEligibilityAuthorityReaderError`로 바꾸고, programming bug는 그대로 전파합니다.
  로그에는 예외 클래스 이름과 무해한 설명만 남기고 raw row·SQL·DB URL·credential을 남기지
  않습니다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import CHAR, DateTime, String, and_, column, select, table, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.assessment_eligibility_authority import (
    AssessmentEligibilityAuthorityReaderError,
)
from rag_runtime.evidence_authority import (
    EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS,
    EvidenceAuthorityError,
    ImmutableArtifactRef,
    PersistedEvidenceAuthority,
    compute_assessment_artifact_ref,
    compute_eligibility_receipt_ref,
    compute_validity_policy_ref,
    compute_verifier_artifact_ref,
    is_valid_immutable_artifact_ref,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]

_AUTHORITY = table(
    "rag_evidence_authority",
    column("id", CHAR(36)),
    column("retrieval_run_id", CHAR(36)),
    column("knowledge_chunk_id", CHAR(36)),
    column("source_snapshot_id", CHAR(36)),
    column("source_snapshot_member_id", CHAR(36)),
    column("source_code", String(100)),
    column("source_version", String(200)),
    column("content_sha256", String(64)),
    column("eligibility_receipt_artifact_code", String(100)),
    column("eligibility_receipt_version", String(50)),
    column("eligibility_receipt_sha256", String(64)),
    column("assessment_artifact_code", String(100)),
    column("assessment_artifact_version", String(50)),
    column("assessment_artifact_sha256", String(64)),
    column("verifier_artifact_code", String(100)),
    column("verifier_artifact_version", String(50)),
    column("verifier_artifact_sha256", String(64)),
    column("validity_policy_artifact_code", String(100)),
    column("validity_policy_version", String(50)),
    column("validity_policy_sha256", String(64)),
    column("evaluated_at", DateTime(timezone=True)),
    column("assessment_valid_from", DateTime(timezone=True)),
    column("assessment_valid_until", DateTime(timezone=True)),
    column("created_at", DateTime(timezone=True)),
)

_MAX_VALIDITY = timedelta(seconds=EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS)


class _CorruptAuthorityRowError(Exception):
    """저장된 authority가 계약을 만족하지 못할 때 내부적으로만 쓰는 신호."""


def _selection_statement(retrieval_run_id: UUID, knowledge_chunk_id: UUID):
    """Selection identity exact equality. 다른 조회 조건을 쓰지 않는다."""
    return (
        select(*_AUTHORITY.c)
        .select_from(_AUTHORITY)
        .where(
            and_(
                _AUTHORITY.c.retrieval_run_id == str(retrieval_run_id),
                _AUTHORITY.c.knowledge_chunk_id == str(knowledge_chunk_id),
            )
        )
    )


def _assessment_ref_statement(assessment_artifact_ref: ImmutableArtifactRef):
    """Assessment artifact identity 3열 exact equality. 부분 일치를 쓰지 않는다."""
    return (
        select(*_AUTHORITY.c)
        .select_from(_AUTHORITY)
        .where(
            and_(
                _AUTHORITY.c.assessment_artifact_code == assessment_artifact_ref.artifact_code,
                _AUTHORITY.c.assessment_artifact_version == assessment_artifact_ref.version,
                _AUTHORITY.c.assessment_artifact_sha256 == assessment_artifact_ref.content_sha256,
            )
        )
    )


def _persisted_uuid(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _CorruptAuthorityRowError("persisted UUID is malformed") from error


def _persisted_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _CorruptAuthorityRowError(f"persisted {field} is missing or blank")
    return value


def _persisted_utc(value: object, field: str) -> datetime:
    """저장된 datetime을 보정 없이 검증한다. naive/non-UTC는 corrupt으로 거부한다."""
    if not isinstance(value, datetime):
        raise _CorruptAuthorityRowError(f"persisted {field} is not a datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise _CorruptAuthorityRowError(f"persisted {field} is not timezone-aware UTC")
    return value.astimezone(UTC)


def _persisted_ref(
    row: RowMapping, code_field: str, version_field: str, sha_field: str, label: str
) -> ImmutableArtifactRef:
    ref = ImmutableArtifactRef(
        artifact_code=str(row[code_field]),
        version=str(row[version_field]),
        content_sha256=str(row[sha_field]),
    )
    if not is_valid_immutable_artifact_ref(ref):
        raise _CorruptAuthorityRowError(f"persisted {label} is not a canonical ImmutableArtifactRef")
    return ref


def _verify_identity(persisted: ImmutableArtifactRef, recomputed: ImmutableArtifactRef, label: str) -> None:
    if persisted != recomputed:
        raise _CorruptAuthorityRowError(f"persisted {label} does not match the recomputed canonical identity")


def _verify_validity_interval(
    *,
    evaluated_at: datetime,
    valid_from: datetime,
    valid_until: datetime,
) -> None:
    """PD-722 의미를 확인만 한다. 새 window를 계산하지 않고 24h를 새 magic number로 두지 않는다.

    더 이른 authoritative upper bound가 왜 존재했는지는 현재 DB 상태에서 재구성하지 않으므로,
    ceiling 이하이기만 하면 받아들인다.
    """
    if valid_from != evaluated_at:
        raise _CorruptAuthorityRowError("persisted assessment_valid_from does not equal evaluated_at")
    if valid_from >= valid_until:
        raise _CorruptAuthorityRowError("persisted validity interval is not strictly increasing")
    if valid_until > valid_from + _MAX_VALIDITY:
        raise _CorruptAuthorityRowError("persisted validity interval exceeds the PD-722 maximum duration")


def _to_persisted_authority(row: RowMapping) -> PersistedEvidenceAuthority:
    """저장된 semantic facts만으로 authority를 복원하고 self-integrity를 재검증한다."""
    authority_id = _persisted_uuid(row["id"])
    retrieval_run_id = _persisted_uuid(row["retrieval_run_id"])
    knowledge_chunk_id = _persisted_uuid(row["knowledge_chunk_id"])
    source_snapshot_id = _persisted_uuid(row["source_snapshot_id"])
    source_snapshot_member_id = _persisted_uuid(row["source_snapshot_member_id"])
    source_code = _persisted_text(row["source_code"], "source_code")
    source_version = _persisted_text(row["source_version"], "source_version")
    content_sha256 = _persisted_text(row["content_sha256"], "content_sha256")

    eligibility_receipt_ref = _persisted_ref(
        row,
        "eligibility_receipt_artifact_code",
        "eligibility_receipt_version",
        "eligibility_receipt_sha256",
        "eligibility_receipt_ref",
    )
    assessment_artifact_ref = _persisted_ref(
        row,
        "assessment_artifact_code",
        "assessment_artifact_version",
        "assessment_artifact_sha256",
        "assessment_artifact_ref",
    )
    verifier_artifact_ref = _persisted_ref(
        row,
        "verifier_artifact_code",
        "verifier_artifact_version",
        "verifier_artifact_sha256",
        "verifier_artifact_ref",
    )
    validity_policy_ref = _persisted_ref(
        row,
        "validity_policy_artifact_code",
        "validity_policy_version",
        "validity_policy_sha256",
        "validity_policy_ref",
    )

    evaluated_at = _persisted_utc(row["evaluated_at"], "evaluated_at")
    assessment_valid_from = _persisted_utc(row["assessment_valid_from"], "assessment_valid_from")
    assessment_valid_until = _persisted_utc(row["assessment_valid_until"], "assessment_valid_until")
    created_at = None if row["created_at"] is None else _persisted_utc(row["created_at"], "created_at")

    _verify_validity_interval(
        evaluated_at=evaluated_at,
        valid_from=assessment_valid_from,
        valid_until=assessment_valid_until,
    )

    # Validity policy와 verifier identity는 caller 입력이나 저장 값이 아니라 canonical function이
    # 정본이다. 저장 값은 그 결과와 exact match해야만 신뢰된다.
    _verify_identity(validity_policy_ref, compute_validity_policy_ref(), "validity_policy_ref")
    _verify_identity(verifier_artifact_ref, compute_verifier_artifact_ref(), "verifier_artifact_ref")

    try:
        recomputed_receipt = compute_eligibility_receipt_ref(
            retrieval_run_id=retrieval_run_id,
            knowledge_chunk_id=knowledge_chunk_id,
            source_snapshot_id=source_snapshot_id,
            source_snapshot_member_id=source_snapshot_member_id,
            source_code=source_code,
            source_version=source_version,
            content_sha256=content_sha256,
            evaluated_at=evaluated_at,
            verifier_artifact_ref=verifier_artifact_ref,
        )
    except EvidenceAuthorityError as error:
        raise _CorruptAuthorityRowError("persisted eligibility receipt facts are not canonical") from error
    _verify_identity(eligibility_receipt_ref, recomputed_receipt, "eligibility_receipt_ref")

    try:
        recomputed_assessment = compute_assessment_artifact_ref(
            retrieval_run_id=retrieval_run_id,
            knowledge_chunk_id=knowledge_chunk_id,
            eligibility_receipt_ref=eligibility_receipt_ref,
            validity_policy_ref=validity_policy_ref,
            assessment_valid_from=assessment_valid_from,
            assessment_valid_until=assessment_valid_until,
        )
    except EvidenceAuthorityError as error:
        raise _CorruptAuthorityRowError("persisted assessment artifact facts are not canonical") from error
    _verify_identity(assessment_artifact_ref, recomputed_assessment, "assessment_artifact_ref")

    return PersistedEvidenceAuthority(
        id=authority_id,
        retrieval_run_id=retrieval_run_id,
        knowledge_chunk_id=knowledge_chunk_id,
        source_snapshot_id=source_snapshot_id,
        source_snapshot_member_id=source_snapshot_member_id,
        source_code=source_code,
        source_version=source_version,
        content_sha256=content_sha256,
        eligibility_receipt_ref=eligibility_receipt_ref,
        assessment_artifact_ref=assessment_artifact_ref,
        verifier_artifact_ref=verifier_artifact_ref,
        validity_policy_ref=validity_policy_ref,
        evaluated_at=evaluated_at,
        assessment_valid_from=assessment_valid_from,
        assessment_valid_until=assessment_valid_until,
        created_at=created_at,
    )


class SqlAlchemyAssessmentEligibilityAuthorityReader:
    """Production `AssessmentEligibilityAuthorityReaderPort` over the persisted #712 authority."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def read_by_selection(
        self,
        *,
        retrieval_run_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> PersistedEvidenceAuthority | None:
        if not isinstance(retrieval_run_id, UUID) or not isinstance(knowledge_chunk_id, UUID):
            logger.error("Selection lookup rejected: selection identity is not a pair of UUIDs")
            raise AssessmentEligibilityAuthorityReaderError("selection identity is invalid")

        authority = await self._read(
            _selection_statement(retrieval_run_id, knowledge_chunk_id),
            kind="selection",
        )
        if authority is None:
            return None

        # 저장된 행이 요청한 selection identity와 같은 사실을 가리키는지 방어적으로 확인한다.
        if authority.retrieval_run_id != retrieval_run_id or authority.knowledge_chunk_id != knowledge_chunk_id:
            logger.error("Assessment authority row is corrupt: selection identity does not match the request")
            raise AssessmentEligibilityAuthorityReaderError("assessment authority row is corrupt")
        return authority

    async def read_by_assessment_ref(
        self,
        *,
        assessment_artifact_ref: ImmutableArtifactRef,
    ) -> PersistedEvidenceAuthority | None:
        if not is_valid_immutable_artifact_ref(assessment_artifact_ref):
            logger.error("Assessment ref lookup rejected: ref is not a canonical ImmutableArtifactRef")
            raise AssessmentEligibilityAuthorityReaderError("assessment artifact ref is invalid")

        authority = await self._read(
            _assessment_ref_statement(assessment_artifact_ref),
            kind="assessment ref",
        )
        if authority is None:
            return None

        if authority.assessment_artifact_ref != assessment_artifact_ref:
            logger.error("Assessment authority row is corrupt: assessment ref does not match the request")
            raise AssessmentEligibilityAuthorityReaderError("assessment authority row is corrupt")
        return authority

    async def _read(self, statement, *, kind: str) -> PersistedEvidenceAuthority | None:
        try:
            rows = await self._fetch_rows(statement)
        except SQLAlchemyError as exc:
            logger.error(
                "Assessment authority %s read failed with database exception: %s", kind, exc.__class__.__name__
            )
            raise AssessmentEligibilityAuthorityReaderError(f"assessment authority {kind} read failed") from None

        if not rows:
            return None
        if len(rows) > 1:
            logger.error("Assessment authority %s lookup is ambiguous: %d rows", kind, len(rows))
            raise AssessmentEligibilityAuthorityReaderError(f"assessment authority {kind} lookup is ambiguous")

        try:
            return _to_persisted_authority(rows[0])
        except _CorruptAuthorityRowError as error:
            logger.error("Assessment authority %s row is corrupt: %s", kind, error)
            raise AssessmentEligibilityAuthorityReaderError("assessment authority row is corrupt") from None

    async def _fetch_rows(self, statement) -> list[RowMapping]:
        async with self._session_factory() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            result = await session.execute(statement)
            return list(result.mappings().all())
