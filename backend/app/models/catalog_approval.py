"""#166 Catalog 사용 승인 저장소.

승인 payload·기간·출처 목록은 발급 후 수정하지 않는다. 철회는 `revoked_at` 계열만 갱신하는
단방향 변경이며, 기간 연장·재승인은 새 ID를 발급한다. 만료 상태를 바꾸는 scheduler를 두지 않고
`valid_from <= 검사 시각 < expires_at` 비교로 판정한다.

업무 판정과 접근 검증은 Python Service/Repository에서 하며 Trigger·RLS·업무용 DB 함수를
추가하지 않는다. 여기서는 일반 FK·UNIQUE·CHECK만 사용한다.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar


class CatalogSourceApproval(Base):
    """하나의 Source Snapshot·version을 Catalog 용도로 쓰는 것에 대한 승인 사실.

    수집 자체의 승인이 아니다. Snapshot이 CURRENT라는 이유로 사용 승인을 대신하지 않는다.
    """

    __tablename__ = "catalog_source_approval"
    __table_args__ = (
        UniqueConstraint("source_snapshot_id", "purpose", "issued_revision", name="uq_catalog_source_approval_target"),
        # 아래 link 표의 복합 FK가 승인과 대상 Snapshot·version을 함께 고정하기 위해 필요하다.
        UniqueConstraint("id", "source_snapshot_id", "source_version", name="uq_catalog_source_approval_id_target"),
        Index("idx_catalog_source_approval_snapshot", "source_snapshot_id"),
        ForeignKeyConstraint(
            ["source_snapshot_id", "source_version"],
            ["rag_source_snapshot.id", "rag_source_snapshot.source_version"],
            name="fk_catalog_source_approval_snapshot_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(trim(purpose)) > 0", name="chk_catalog_source_approval_purpose_nonblank"),
        CheckConstraint("length(trim(evidence_ref)) > 0", name="chk_catalog_source_approval_evidence_nonblank"),
        CheckConstraint("expires_at > valid_from", name="chk_catalog_source_approval_validity_window"),
        CheckConstraint(
            "(revoked_at IS NULL AND revoked_by IS NULL AND revoked_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_reason IS NOT NULL)",
            name="chk_catalog_source_approval_revocation_shape",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    source_version: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose: Mapped[str] = mapped_column(String(60), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[UUID | None] = mapped_column(
        UUIDChar(), ForeignKey("user.id", ondelete="RESTRICT"), nullable=True
    )
    revoked_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    issued_revision: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CatalogBuildApproval(Base):
    """Set 생성 전 정확한 export 구성에 대한 승인.

    Set ID와 최종 envelope hash는 승인 이후에 만들어지므로 요구하지 않는다.
    """

    __tablename__ = "catalog_build_approval"
    __table_args__ = (
        UniqueConstraint(
            "catalog_version", "export_checksum", "issued_revision", name="uq_catalog_build_approval_target"
        ),
        Index("idx_catalog_build_approval_checksum", "export_checksum"),
        CheckConstraint("length(trim(catalog_version)) > 0", name="chk_catalog_build_approval_version_nonblank"),
        CheckConstraint("export_checksum ~ '^[0-9a-f]{64}$'", name="chk_catalog_build_approval_checksum"),
        CheckConstraint("length(trim(schema_version)) > 0", name="chk_catalog_build_approval_schema_nonblank"),
        CheckConstraint(
            "length(trim(manifest_spec_version)) > 0", name="chk_catalog_build_approval_manifest_spec_nonblank"
        ),
        CheckConstraint("length(trim(evidence_ref)) > 0", name="chk_catalog_build_approval_evidence_nonblank"),
        CheckConstraint("expires_at > valid_from", name="chk_catalog_build_approval_validity_window"),
        CheckConstraint(
            "(revoked_at IS NULL AND revoked_by IS NULL AND revoked_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_reason IS NOT NULL)",
            name="chk_catalog_build_approval_revocation_shape",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    catalog_version: Mapped[str] = mapped_column(String(100), nullable=False)
    export_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    manifest_spec_version: Mapped[str] = mapped_column(String(100), nullable=False)
    # 승인 시점의 검증 대상 bytes. 재승인 시 과거 승인의 bytes를 바꾸지 않는다.
    approved_export_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    is_complete: Mapped[bool] = mapped_column(nullable=False)
    actor_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[UUID | None] = mapped_column(
        UUIDChar(), ForeignKey("user.id", ondelete="RESTRICT"), nullable=True
    )
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_revision: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CatalogBuildApprovalSource(Base):
    """Catalog 승인과 전체 Source 승인 집합의 결속. 같은 Snapshot을 두 번 담지 않는다."""

    __tablename__ = "catalog_build_approval_source"
    __table_args__ = (
        UniqueConstraint("build_approval_id", "source_snapshot_id", name="uq_catalog_build_approval_source_snapshot"),
        UniqueConstraint("build_approval_id", "source_approval_id", name="uq_catalog_build_approval_source_approval"),
        Index("idx_catalog_build_approval_source_build", "build_approval_id"),
        ForeignKeyConstraint(
            ["build_approval_id"],
            ["catalog_build_approval.id"],
            name="fk_catalog_build_approval_source_build",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_approval_id", "source_snapshot_id", "source_version"],
            [
                "catalog_source_approval.id",
                "catalog_source_approval.source_snapshot_id",
                "catalog_source_approval.source_version",
            ],
            name="fk_catalog_build_approval_source_target",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    build_approval_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    source_approval_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    source_version: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


#: Phase 2에서 승인된 audit event 전체 목록. 새 종류를 늘리지 않는다.
CATALOG_APPROVAL_AUDIT_EVENT_KINDS = (
    "GRANT_PERMISSION",
    "ISSUE_SOURCE",
    "ISSUE_CATALOG",
    "REVOKE_PERMISSION",
    "REVOKE_SOURCE",
    "REVOKE_CATALOG",
)
_AUDIT_EVENT_KIND_LIST = ", ".join(f"'{kind}'" for kind in CATALOG_APPROVAL_AUDIT_EVENT_KINDS)


class CatalogApprovalPermission(Base):
    """승인 발급·철회를 수행할 수 있는 현재 운영자 권한 상태.

    현재 상태만 보관하고 이력은 `catalog_approval_audit`이 보존한다. Catalog Writer는 이 표에
    접근하지 않으며, 변경은 승인 전용 one-shot command만 수행한다.
    """

    __tablename__ = "catalog_approval_permission"
    __table_args__ = (
        CheckConstraint("length(trim(evidence_ref)) > 0", name="chk_catalog_approval_permission_evidence_nonblank"),
        CheckConstraint("revision >= 0", name="chk_catalog_approval_permission_revision"),
    )

    user_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id", ondelete="RESTRICT"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CatalogApprovalAudit(Base):
    """승인 권한 부여·발급·철회의 append-only 증빙.

    발급 후 UPDATE/DELETE하지 않는다. secret·credential·private artifact 경로·provider 원문은
    담지 않고 식별자와 이미 공개된 검증 값만 남긴다. 하나의 issue command가 같은 request_id로
    ISSUE_SOURCE와 ISSUE_CATALOG 두 event를 남기므로 event_kind까지 포함해 유일성을 잡는다.
    """

    __tablename__ = "catalog_approval_audit"
    __table_args__ = (
        UniqueConstraint("actor_id", "request_id", "event_kind", name="uq_catalog_approval_audit_request"),
        Index("idx_catalog_approval_audit_request", "request_id"),
        Index("idx_catalog_approval_audit_actor", "actor_id"),
        CheckConstraint(f"event_kind IN ({_AUDIT_EVENT_KIND_LIST})", name="chk_catalog_approval_audit_event_kind"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="chk_catalog_approval_audit_fingerprint"),
        CheckConstraint(
            "export_checksum IS NULL OR export_checksum ~ '^[0-9a-f]{64}$'",
            name="chk_catalog_approval_audit_export_checksum",
        ),
        CheckConstraint(
            "purpose IS NULL OR length(trim(purpose)) > 0", name="chk_catalog_approval_audit_purpose_nonblank"
        ),
        ForeignKeyConstraint(
            ["source_approval_id"],
            ["catalog_source_approval.id"],
            name="fk_catalog_approval_audit_source_approval",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["build_approval_id"],
            ["catalog_build_approval.id"],
            name="fk_catalog_approval_audit_build_approval",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    subject_user_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(), ForeignKey("user.id", ondelete="RESTRICT"), nullable=True
    )
    source_approval_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    build_approval_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    source_snapshot_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(60), nullable=True)
    catalog_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    export_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
