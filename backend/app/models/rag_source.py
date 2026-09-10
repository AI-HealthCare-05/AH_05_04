from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from app.core.db.databases import Base
from app.core.db.types import UUIDChar


def _sql_in_list(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class RagSourceLifecycleStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    REVOKED = "REVOKED"


class RagSourceEndpointLifecycleStatus(StrEnum):
    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    RETIRED = "RETIRED"
    REVOKED = "REVOKED"


class RagSourceUsageStatus(StrEnum):
    DISABLED = "DISABLED"
    ENABLED = "ENABLED"


class RagSourceApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REVOKED = "REVOKED"


class RagSnapshotVerificationStatus(StrEnum):
    PENDING = "PENDING"
    CURRENT = "CURRENT"
    STALE = "STALE"
    FAILED = "FAILED"


class RagIngestionRunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    SUCCEEDED_WITH_REJECTIONS = "SUCCEEDED_WITH_REJECTIONS"
    NO_CHANGE = "NO_CHANGE"
    FAILED = "FAILED"


class RagSourceIngestionArtifactKind(StrEnum):
    RAW_RESPONSE = "RAW_RESPONSE"
    REJECTS = "REJECTS"


class RagVerificationResultStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NO_CHANGE = "NO_CHANGE"


class RagSource(Base):
    __tablename__ = "rag_source"
    __table_args__ = (
        UniqueConstraint("source_code", name="uq_rag_source_code"),
        CheckConstraint("max_rejected_records >= 0", name="chk_rag_source_max_rejected_records"),
        CheckConstraint(
            "max_rejection_rate >= 0 AND max_rejection_rate <= 1", name="chk_rag_source_max_rejection_rate"
        ),
        CheckConstraint("empty_result_policy = 'REJECT'", name="chk_rag_source_empty_result_policy"),
        CheckConstraint("length(trim(source_code)) > 0", name="chk_rag_source_code_nonblank"),
        CheckConstraint("length(trim(display_name)) > 0", name="chk_rag_source_display_name_nonblank"),
        CheckConstraint(
            f"lifecycle_status IN ({_sql_in_list(RagSourceLifecycleStatus)})",
            name="chk_rag_source_lifecycle_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_code: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    license_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attribution_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lifecycle_status: Mapped[RagSourceLifecycleStatus] = mapped_column(
        Enum(RagSourceLifecycleStatus, native_enum=False, length=20),
        nullable=False,
        default=RagSourceLifecycleStatus.DRAFT,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    max_rejected_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_rejection_rate: Mapped[Decimal] = mapped_column(
        Numeric(), nullable=False, default=Decimal("0"), server_default="0"
    )
    empty_result_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="REJECT", server_default="REJECT"
    )

    endpoints: Mapped[list["RagSourceEndpoint"]] = relationship(back_populates="source")


class RagSourceEndpoint(Base):
    __tablename__ = "rag_source_endpoint"
    __table_args__ = (
        UniqueConstraint("source_id", "endpoint_code", name="uq_rag_source_endpoint_code"),
        Index("idx_rag_source_endpoint_source", "source_id"),
        CheckConstraint("length(trim(endpoint_code)) > 0", name="chk_rag_source_endpoint_code_nonblank"),
        CheckConstraint("length(trim(display_name)) > 0", name="chk_rag_source_endpoint_display_name_nonblank"),
        CheckConstraint(
            f"lifecycle_status IN ({_sql_in_list(RagSourceEndpointLifecycleStatus)})",
            name="chk_rag_source_endpoint_lifecycle_status",
        ),
        CheckConstraint(
            f"runtime_status IN ({_sql_in_list(RagSourceUsageStatus)})",
            name="chk_rag_source_endpoint_runtime_status",
        ),
        CheckConstraint(
            f"acquisition_status IN ({_sql_in_list(RagSourceApprovalStatus)})",
            name="chk_rag_source_endpoint_acquisition_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source.id"), nullable=False)
    endpoint_code: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    official_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    lifecycle_status: Mapped[RagSourceEndpointLifecycleStatus] = mapped_column(
        Enum(RagSourceEndpointLifecycleStatus, native_enum=False, length=20),
        nullable=False,
        default=RagSourceEndpointLifecycleStatus.DRAFT,
    )
    runtime_status: Mapped[RagSourceUsageStatus] = mapped_column(
        Enum(RagSourceUsageStatus, native_enum=False, length=20),
        nullable=False,
        default=RagSourceUsageStatus.DISABLED,
    )
    acquisition_status: Mapped[RagSourceApprovalStatus] = mapped_column(
        Enum(RagSourceApprovalStatus, native_enum=False, length=20),
        nullable=False,
        default=RagSourceApprovalStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    source: Mapped[RagSource] = relationship(back_populates="endpoints")
    operations: Mapped[list["RagSourceOperation"]] = relationship(back_populates="endpoint")


class RagSourceOperation(Base):
    __tablename__ = "rag_source_operation"
    __table_args__ = (
        UniqueConstraint("endpoint_id", "operation_code", name="uq_rag_source_operation_code"),
        Index("idx_rag_source_operation_endpoint", "endpoint_id"),
        CheckConstraint("length(trim(operation_code)) > 0", name="chk_rag_source_operation_code_nonblank"),
        CheckConstraint("length(trim(display_name)) > 0", name="chk_rag_source_operation_display_name_nonblank"),
        CheckConstraint(
            f"runtime_status IN ({_sql_in_list(RagSourceUsageStatus)})",
            name="chk_rag_source_operation_runtime_status",
        ),
        CheckConstraint(
            f"acquisition_status IN ({_sql_in_list(RagSourceApprovalStatus)})",
            name="chk_rag_source_operation_acquisition_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    endpoint_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source_endpoint.id"), nullable=False)
    operation_code: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    runtime_status: Mapped[RagSourceUsageStatus] = mapped_column(
        Enum(RagSourceUsageStatus, native_enum=False, length=20),
        nullable=False,
        default=RagSourceUsageStatus.DISABLED,
    )
    acquisition_status: Mapped[RagSourceApprovalStatus] = mapped_column(
        Enum(RagSourceApprovalStatus, native_enum=False, length=20),
        nullable=False,
        default=RagSourceApprovalStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    endpoint: Mapped[RagSourceEndpoint] = relationship(back_populates="operations")
    snapshots: Mapped[list["RagSourceSnapshot"]] = relationship(back_populates="operation")
    ingestion_runs: Mapped[list["RagSourceIngestionRun"]] = relationship(back_populates="operation")


class RagSourceSnapshot(Base):
    """Source verification snapshot. CURRENT is latest verified, not runtime-selected."""

    __tablename__ = "rag_source_snapshot"
    __table_args__ = (
        Index(
            "uq_rag_source_snapshot_active_version",
            "operation_id",
            "source_version",
            unique=True,
            postgresql_where=text("verification_status <> 'FAILED'"),
        ),
        UniqueConstraint("id", "source_version", name="uq_rag_source_snapshot_id_version"),
        ForeignKeyConstraint(
            ["id", "verification_seal_id"],
            ["rag_source_snapshot_verification.snapshot_id", "rag_source_snapshot_verification.id"],
            name="fk_rag_snapshot_verification_seal",
            use_alter=True,
        ),
        CheckConstraint(
            "(verification_status = 'PENDING' AND verified_at IS NULL AND effective_at IS NULL) "
            "OR verification_seal_id IS NOT NULL",
            name="chk_rag_snapshot_verification_seal",
        ),
        CheckConstraint("management_lock_marker = 0", name="chk_rag_source_snapshot_management_lock_marker"),
        Index("idx_rag_source_snapshot_operation_status", "operation_id", "verification_status"),
        Index(
            "uq_rag_source_snapshot_current",
            "operation_id",
            unique=True,
            postgresql_where=text("verification_status = 'CURRENT'"),
        ),
        CheckConstraint("length(source_version) <= 200", name="chk_rag_source_snapshot_version_length"),
        CheckConstraint("length(trim(source_version)) > 0", name="chk_rag_source_snapshot_version_nonblank"),
        CheckConstraint("length(raw_manifest_checksum) = 64", name="chk_rag_source_snapshot_raw_manifest_checksum"),
        CheckConstraint("length(canonical_checksum) = 64", name="chk_rag_source_snapshot_canonical_checksum"),
        CheckConstraint(
            "endpoint_receipt_hash IS NULL OR endpoint_receipt_hash ~ '^[0-9a-f]{64}$'",
            name="chk_rag_source_snapshot_endpoint_receipt_hash",
        ),
        CheckConstraint("record_count >= 0", name="chk_rag_source_snapshot_record_count"),
        CheckConstraint("rejected_record_count >= 0", name="chk_rag_source_snapshot_rejected_record_count"),
        CheckConstraint(
            "rejected_record_count <= record_count",
            name="chk_rag_source_snapshot_rejected_record_count_lte_record_count",
        ),
        CheckConstraint(
            f"verification_status IN ({_sql_in_list(RagSnapshotVerificationStatus)})",
            name="chk_rag_source_snapshot_verification_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source_operation.id"), nullable=False)
    source_version: Mapped[str] = mapped_column(String(200), nullable=False)
    external_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw_manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(100), nullable=False)
    canonicalization_spec_version: Mapped[str] = mapped_column(String(100), nullable=False)
    endpoint_receipt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verification_status: Mapped[RagSnapshotVerificationStatus] = mapped_column(
        Enum(RagSnapshotVerificationStatus, native_enum=False, length=20),
        nullable=False,
        default=RagSnapshotVerificationStatus.PENDING,
    )
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_seal_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    # Fixed, non-provenance column providing management SELECT FOR UPDATE permission.
    management_lock_marker: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    supersedes_snapshot_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey("rag_source_snapshot.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    operation: Mapped[RagSourceOperation] = relationship(back_populates="snapshots")
    supersedes_snapshot: Mapped["RagSourceSnapshot | None"] = relationship(remote_side=[id])
    ingestion_runs: Mapped[list["RagSourceIngestionRun"]] = relationship(back_populates="snapshot")
    verification_runs: Mapped[list["RagSourceSnapshotVerification"]] = relationship(
        back_populates="snapshot", foreign_keys="RagSourceSnapshotVerification.snapshot_id"
    )


class RagSourceIngestionRun(Base):
    __tablename__ = "rag_source_ingestion_run"
    __table_args__ = (
        UniqueConstraint("operation_id", "run_group_key", "attempt_number", name="uq_rag_source_ingestion_run_attempt"),
        Index("idx_rag_source_ingestion_run_operation_status", "operation_id", "run_status", "started_at"),
        CheckConstraint(
            "(run_status <> 'FAILED' OR snapshot_id IS NULL) AND (run_status <> 'NO_CHANGE' OR snapshot_id IS NOT NULL)",
            name="chk_rag_ingestion_run_snapshot_status",
        ),
        CheckConstraint("length(trim(run_group_key)) > 0", name="chk_rag_source_ingestion_run_group_key_nonblank"),
        CheckConstraint("attempt_number > 0", name="chk_rag_source_ingestion_run_attempt_positive"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="chk_rag_source_ingestion_run_duration"),
        CheckConstraint(
            f"run_status IN ({_sql_in_list(RagIngestionRunStatus)})",
            name="chk_rag_source_ingestion_run_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source_operation.id"), nullable=False)
    run_group_key: Mapped[str] = mapped_column(String(100), nullable=False)
    snapshot_id: Mapped[UUID | None] = mapped_column(UUIDChar(), ForeignKey("rag_source_snapshot.id"), nullable=True)
    run_status: Mapped[RagIngestionRunStatus] = mapped_column(
        Enum(RagIngestionRunStatus, native_enum=False, length=40),
        nullable=False,
        default=RagIngestionRunStatus.RUNNING,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    operation: Mapped[RagSourceOperation] = relationship(back_populates="ingestion_runs")
    snapshot: Mapped[RagSourceSnapshot | None] = relationship(back_populates="ingestion_runs")
    artifacts: Mapped[list["RagSourceIngestionArtifact"]] = relationship(back_populates="ingestion_run")


class RagSourceIngestionArtifact(Base):
    """접근 통제 저장소에 보존한 수집 원본의 불변 참조입니다."""

    __tablename__ = "rag_source_ingestion_artifact"
    __table_args__ = (
        UniqueConstraint("ingestion_run_id", "page_number", name="uq_rag_source_artifact_run_page"),
        UniqueConstraint("ingestion_run_id", "artifact_key", name="uq_rag_source_artifact_run_key"),
        Index("idx_rag_source_artifact_run", "ingestion_run_id"),
        Index("idx_rag_source_artifact_object", "storage_backend", "object_key"),
        CheckConstraint("page_number > 0", name="chk_rag_source_artifact_page_positive"),
        CheckConstraint(
            "(artifact_kind = 'RAW_RESPONSE' AND page_number IS NOT NULL "
            "AND reject_code IS NULL AND parser_location IS NULL) OR "
            "(artifact_kind = 'REJECTS' AND page_number IS NULL "
            "AND reject_code ~ '^[A-Z][A-Z0-9_]{0,99}$' "
            "AND length(trim(parser_location)) > 0 "
            "AND parser_location !~ '[[:cntrl:]]')",
            name="chk_rag_source_artifact_kind_metadata",
        ),
        CheckConstraint("length(trim(artifact_key)) > 0", name="chk_rag_source_artifact_key_nonblank"),
        CheckConstraint("length(trim(storage_backend)) > 0", name="chk_rag_source_artifact_backend_nonblank"),
        CheckConstraint("length(trim(object_key)) > 0", name="chk_rag_source_artifact_object_key_nonblank"),
        CheckConstraint(
            "raw_checksum ~ '^[0-9a-f]{64}$'",
            name="chk_rag_source_artifact_checksum",
        ),
        CheckConstraint("byte_size >= 0", name="chk_rag_source_artifact_byte_size"),
        CheckConstraint("length(trim(content_type)) > 0", name="chk_rag_source_artifact_content_type_nonblank"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    ingestion_run_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("rag_source_ingestion_run.id", ondelete="RESTRICT"),
        nullable=False,
    )
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artifact_kind: Mapped[RagSourceIngestionArtifactKind] = mapped_column(
        Enum(RagSourceIngestionArtifactKind, native_enum=False, length=20),
        nullable=False,
        default=RagSourceIngestionArtifactKind.RAW_RESPONSE,
    )
    artifact_key: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(50), nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    reject_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parser_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    ingestion_run: Mapped[RagSourceIngestionRun] = relationship(back_populates="artifacts")


class RagSourceSnapshotVerification(Base):
    __tablename__ = "rag_source_snapshot_verification"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "id", name="uq_rag_verification_snapshot_id"),
        Index("idx_rag_source_snapshot_verification_snapshot", "snapshot_id", "verified_at"),
        CheckConstraint(
            "check_name <> 'snapshot-publication-approval' OR verification_result <> 'PASSED' OR "
            "(verified_by IS NOT NULL AND length(trim(verified_by)) > 0)",
            name="chk_rag_snapshot_publication_approver",
        ),
        CheckConstraint("length(trim(check_name)) > 0", name="chk_rag_source_snapshot_verification_check_nonblank"),
        CheckConstraint(
            f"verification_result IN ({_sql_in_list(RagVerificationResultStatus)})",
            name="chk_rag_source_snapshot_verification_result",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source_snapshot.id"), nullable=False)
    check_name: Mapped[str] = mapped_column(String(100), nullable=False)
    verification_result: Mapped[RagVerificationResultStatus] = mapped_column(
        Enum(RagVerificationResultStatus, native_enum=False, length=20),
        nullable=False,
    )
    details_summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    snapshot: Mapped[RagSourceSnapshot] = relationship(back_populates="verification_runs", foreign_keys=[snapshot_id])
