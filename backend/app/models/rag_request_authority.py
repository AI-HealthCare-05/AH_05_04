"""#713 REQUEST 시점 authority의 historical 영속 저장소.

여기에 남는 행은 그 REQUEST 시점에 실제로 발행된 관측치입니다. Source의 현재 승인 상태나
Snapshot의 CURRENT 상태를 조회 시점에 해석해 만들지 않습니다. 따라서 `catalog_source_approval`,
`rag_source_snapshot`, `rag_source_snapshot_member`, `runtime_guard_decision_ref` 계열은
이 표를 대신할 수 없습니다.

세 표 모두 append-only 증거입니다. 발행 후 내용을 바꾸지 않으며 Repository도 update/delete API를
제공하지 않습니다. 불변성은 typed schema·NOT NULL·UNIQUE·FK·CHECK와 append-only writer로만
구성하고 Trigger·RLS·Stored Procedure·사용자 정의 DB 함수를 추가하지 않습니다.

artifact identity(`artifact_code`·`artifact_version`·`artifact_content_sha256`)는 caller가 고르는
값이 아니라 `ai_worker.tasks.rag.request_authority_artifact`의 canonical projection digest입니다.
조회는 그 3개 값의 exact equality로만 하며 latest/CURRENT fallback을 두지 않습니다.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE = "request_guard_authority"
REQUEST_SOURCE_DECISION_AUTHORITY_ARTIFACT_CODE = "request_source_decision_authority"
REQUEST_MEMBER_DECISION_AUTHORITY_ARTIFACT_CODE = "request_member_decision_authority"

REQUEST_DECISION_STAGE = "REQUEST"
DECISION_OUTCOME_VALUES = ("PASS", "FAIL")
PERSISTED_MEMBER_KIND_VALUES = ("ENDPOINT_OPERATION", "ARTIFACT")

_OUTCOME_IN_LIST = ", ".join(f"'{value}'" for value in DECISION_OUTCOME_VALUES)
_MEMBER_KIND_IN_LIST = ", ".join(f"'{value}'" for value in PERSISTED_MEMBER_KIND_VALUES)

_GUARD_REF_COLUMNS = (
    "request_guard_artifact_code",
    "request_guard_artifact_version",
    "request_guard_content_sha256",
)
_GUARD_TARGET_COLUMNS = (
    "rag_request_guard_authority.artifact_code",
    "rag_request_guard_authority.artifact_version",
    "rag_request_guard_authority.artifact_content_sha256",
)


class RagRequestGuardAuthority(Base):
    """REQUEST Guard가 그 요청에 대해 실제로 발행한 관측치."""

    __tablename__ = "rag_request_guard_authority"
    __table_args__ = (
        UniqueConstraint(
            "artifact_code",
            "artifact_version",
            "artifact_content_sha256",
            name="uq_rag_request_guard_authority_artifact",
        ),
        CheckConstraint(
            f"artifact_code = '{REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE}'",
            name="chk_rag_request_guard_authority_artifact_code",
        ),
        CheckConstraint(
            "length(trim(artifact_version)) > 0",
            name="chk_rag_request_guard_authority_artifact_version_nonblank",
        ),
        CheckConstraint(
            "artifact_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_guard_authority_content_sha256",
        ),
        CheckConstraint(
            "length(trim(request_operation_code)) > 0",
            name="chk_rag_request_guard_authority_operation_nonblank",
        ),
        CheckConstraint(
            f"decision_stage = '{REQUEST_DECISION_STAGE}'",
            name="chk_rag_request_guard_authority_decision_stage",
        ),
        Index("idx_rag_request_guard_authority_user", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_version: Mapped[str] = mapped_column(String(50), nullable=False)
    artifact_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id"), nullable=False)
    request_operation_code: Mapped[str] = mapped_column(String(100), nullable=False)
    decision_stage: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RagRequestSourceDecision(Base):
    """그 REQUEST에서 Source에 대해 실제로 발행된 PASS/FAIL Decision."""

    __tablename__ = "rag_request_source_decision"
    __table_args__ = (
        UniqueConstraint(
            "artifact_code",
            "artifact_version",
            "artifact_content_sha256",
            name="uq_rag_request_source_decision_artifact",
        ),
        # Guard 참조는 opaque 문자열이 아니라 Guard artifact identity 자체에 대한 실제 참조다.
        ForeignKeyConstraint(
            _GUARD_REF_COLUMNS,
            _GUARD_TARGET_COLUMNS,
            name="fk_rag_request_source_decision_guard",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"artifact_code = '{REQUEST_SOURCE_DECISION_AUTHORITY_ARTIFACT_CODE}'",
            name="chk_rag_request_source_decision_artifact_code",
        ),
        CheckConstraint(
            "artifact_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_source_decision_content_sha256",
        ),
        CheckConstraint(
            "request_guard_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_source_decision_guard_sha256",
        ),
        CheckConstraint(
            "length(trim(request_operation_code)) > 0",
            name="chk_rag_request_source_decision_operation_nonblank",
        ),
        CheckConstraint(
            f"decision_stage = '{REQUEST_DECISION_STAGE}'",
            name="chk_rag_request_source_decision_decision_stage",
        ),
        CheckConstraint(
            f"actual_decision_outcome IN ({_OUTCOME_IN_LIST})",
            name="chk_rag_request_source_decision_outcome",
        ),
        CheckConstraint("length(trim(source_code)) > 0", name="chk_rag_request_source_decision_source_code_nonblank"),
        CheckConstraint(
            "length(trim(source_version)) > 0",
            name="chk_rag_request_source_decision_source_version_nonblank",
        ),
        Index("idx_rag_request_source_decision_guard", *_GUARD_REF_COLUMNS),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_version: Mapped[str] = mapped_column(String(50), nullable=False)
    artifact_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_guard_artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    request_guard_artifact_version: Mapped[str] = mapped_column(String(50), nullable=False)
    request_guard_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id"), nullable=False)
    request_operation_code: Mapped[str] = mapped_column(String(100), nullable=False)
    decision_stage: Mapped[str] = mapped_column(String(20), nullable=False)
    # 교차 도메인 좌표는 기록 사실로 보존한다. Source 수명주기(cleanup·retention)와 결합하지 않도록
    # FK를 두지 않으며, Guard/Source/Member 정합성은 Repository writer가 명시적으로 검증한다.
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    source_code: Mapped[str] = mapped_column(String(200), nullable=False)
    source_version: Mapped[str] = mapped_column(String(200), nullable=False)
    actual_decision_outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RagRequestMemberDecision(Base):
    """그 REQUEST에서 Source Snapshot Member에 대해 실제로 발행된 PASS/FAIL Decision."""

    __tablename__ = "rag_request_member_decision"
    __table_args__ = (
        UniqueConstraint(
            "artifact_code",
            "artifact_version",
            "artifact_content_sha256",
            name="uq_rag_request_member_decision_artifact",
        ),
        ForeignKeyConstraint(
            _GUARD_REF_COLUMNS,
            _GUARD_TARGET_COLUMNS,
            name="fk_rag_request_member_decision_guard",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"artifact_code = '{REQUEST_MEMBER_DECISION_AUTHORITY_ARTIFACT_CODE}'",
            name="chk_rag_request_member_decision_artifact_code",
        ),
        CheckConstraint(
            "artifact_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_member_decision_content_sha256",
        ),
        CheckConstraint(
            "request_guard_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_member_decision_guard_sha256",
        ),
        CheckConstraint(
            "length(trim(request_operation_code)) > 0",
            name="chk_rag_request_member_decision_operation_nonblank",
        ),
        CheckConstraint(
            f"decision_stage = '{REQUEST_DECISION_STAGE}'",
            name="chk_rag_request_member_decision_decision_stage",
        ),
        CheckConstraint(
            f"actual_decision_outcome IN ({_OUTCOME_IN_LIST})",
            name="chk_rag_request_member_decision_outcome",
        ),
        CheckConstraint(
            f"member_kind IN ({_MEMBER_KIND_IN_LIST})",
            name="chk_rag_request_member_decision_member_kind",
        ),
        # source_member_identity 계약을 lossless하게 보존한다. operation_code는 계약대로 nullable이다.
        CheckConstraint(
            "(member_kind = 'ENDPOINT_OPERATION' AND endpoint_code IS NOT NULL "
            "AND member_artifact_code IS NULL AND member_artifact_version IS NULL) OR "
            "(member_kind = 'ARTIFACT' AND member_artifact_code IS NOT NULL "
            "AND member_artifact_version IS NOT NULL "
            "AND endpoint_code IS NULL AND operation_code IS NULL)",
            name="chk_rag_request_member_decision_identity_shape",
        ),
        Index("idx_rag_request_member_decision_guard", *_GUARD_REF_COLUMNS),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_version: Mapped[str] = mapped_column(String(50), nullable=False)
    artifact_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_guard_artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    request_guard_artifact_version: Mapped[str] = mapped_column(String(50), nullable=False)
    request_guard_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id"), nullable=False)
    request_operation_code: Mapped[str] = mapped_column(String(100), nullable=False)
    decision_stage: Mapped[str] = mapped_column(String(20), nullable=False)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    source_snapshot_member_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    member_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    endpoint_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    operation_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    member_artifact_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    member_artifact_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    actual_decision_outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
