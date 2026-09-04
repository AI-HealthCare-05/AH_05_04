"""create ai job outbox idempotency tables

Revision ID: 146a1b2c3d4e
Revises: 9c1d7f2b6a4e
Create Date: 2026-09-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision: str = "146a1b2c3d4e"
down_revision: str | Sequence[str] | None = "9c1d7f2b6a4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FAILURE_CODES_SQL = (
    "'TIMEOUT', 'DEPENDENCY_UNAVAILABLE', 'INVALID_INPUT', 'UNSUPPORTED_SCHEMA', "
    "'SAFETY_VALIDATION_FAILED', 'RETRY_EXHAUSTED', 'INTERNAL_ERROR'"
)


def _ensure_downgrade_is_data_safe(connection: Connection) -> None:
    """outbox-stream-v1.md: 미발행 DLQ Outbox와 연결된 quarantine은 TTL로도 삭제하지 않습니다.

    격리된 poison message는 원본을 저장하지 않아 지워지면 재현할 수 없으므로, 아직 발행되지
    않은 DLQ row가 있으면 downgrade의 DDL 실행 전에 중단합니다.
    """

    dlq_outbox_event = sa.table("dlq_outbox_event", sa.column("status"))

    unpublished_dlq_count = connection.execute(
        sa.select(sa.func.count())
        .select_from(dlq_outbox_event)
        .where(dlq_outbox_event.c.status.in_(["PENDING", "CLAIMED"]))
    ).scalar_one()

    if unpublished_dlq_count:
        raise RuntimeError(
            "Cannot downgrade revision 146a1b2c3d4e while unpublished (PENDING|CLAIMED) "
            "dlq_outbox_event rows exist. Production must use a forward-fix. In a "
            "non-production environment, back up and remove or migrate the affected data "
            "through an approved rollback procedure first."
        )


def upgrade() -> None:
    op.create_table(
        "ai_job",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("user_id", sa.CHAR(length=36), nullable=False),
        sa.Column(
            "job_type", sa.Enum("OCR", "GUIDE", "CHAT", name="aijobtype", native_enum=False, length=20), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "PROCESSING",
                "RETRY_WAIT",
                "COMPLETED",
                "FAILED",
                "STALE",
                name="aijobstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("prescription_version_id", sa.CHAR(length=36), nullable=True),
        sa.Column("expected_event_id", sa.CHAR(length=36), nullable=True),
        sa.Column("last_consumed_event_id", sa.CHAR(length=36), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lease_token", sa.String(length=100), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("job_type IN ('OCR', 'GUIDE', 'CHAT')", name="chk_ai_job_type"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'RETRY_WAIT', 'COMPLETED', 'FAILED', 'STALE')",
            name="chk_ai_job_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="chk_ai_job_attempt_count"),
        sa.CheckConstraint("max_attempts > 0", name="chk_ai_job_max_attempts"),
        sa.CheckConstraint("attempt_count <= max_attempts", name="chk_ai_job_attempt_limit"),
        sa.CheckConstraint("status <> 'FAILED' OR failure_code IS NOT NULL", name="chk_ai_job_failed_code"),
        sa.CheckConstraint(
            f"failure_code IS NULL OR failure_code IN ({_FAILURE_CODES_SQL})",
            name="chk_ai_job_failure_code_values",
        ),
        sa.CheckConstraint(
            "status NOT IN ('COMPLETED', 'FAILED', 'STALE') OR completed_at IS NOT NULL",
            name="chk_ai_job_terminal_completed_at",
        ),
        sa.CheckConstraint(
            "status <> 'PROCESSING' OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="chk_ai_job_processing_lease",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ai_job_expected_event", "ai_job", ["expected_event_id"], unique=False)
    op.create_index("idx_ai_job_user_status_updated", "ai_job", ["user_id", "status", "updated_at", "id"], unique=False)
    op.create_index("idx_ai_job_status_available", "ai_job", ["status", "available_at", "id"], unique=False)
    op.create_index("idx_ai_job_lease_expires", "ai_job", ["status", "lease_expires_at", "id"], unique=False)

    op.create_table(
        "ai_job_attempt",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("ai_job_id", sa.CHAR(length=36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column(
            "attempt_status",
            sa.Enum(
                "PROCESSING", "COMPLETED", "BLOCKED", "FAILED", name="aijobattemptstatus", native_enum=False, length=30
            ),
            nullable=False,
        ),
        sa.Column("runtime_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("timed_out", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_no >= 1", name="chk_ai_job_attempt_attempt_no"),
        sa.CheckConstraint(
            "attempt_status IN ('PROCESSING', 'COMPLETED', 'BLOCKED', 'FAILED')",
            name="chk_ai_job_attempt_status",
        ),
        sa.CheckConstraint(
            "attempt_status NOT IN ('COMPLETED', 'BLOCKED', 'FAILED') OR completed_at IS NOT NULL",
            name="chk_ai_job_attempt_terminal_completed_at",
        ),
        sa.CheckConstraint(
            "attempt_status <> 'FAILED' OR error_code IS NOT NULL", name="chk_ai_job_attempt_failed_code"
        ),
        sa.CheckConstraint(
            f"error_code IS NULL OR error_code IN ({_FAILURE_CODES_SQL})",
            name="chk_ai_job_attempt_error_code_values",
        ),
        sa.ForeignKeyConstraint(["ai_job_id"], ["ai_job.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ai_job_id", "attempt_no", name="uq_ai_job_attempt_no"),
    )
    op.create_index(
        "idx_ai_job_attempt_status_started", "ai_job_attempt", ["attempt_status", "started_at"], unique=False
    )

    op.create_table(
        "outbox_event",
        sa.Column("event_id", sa.CHAR(length=36), nullable=False),
        sa.Column("job_id", sa.CHAR(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "event_kind",
            sa.Enum("JOB_EXECUTE", name="outboxeventkind", native_enum=False, length=30),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "CLAIMED", "PUBLISHED", "CANCELLED", name="outboxeventstatus", native_enum=False, length=20
            ),
            nullable=False,
        ),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("claim_token", sa.String(length=100), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("event_kind IN ('JOB_EXECUTE')", name="chk_outbox_event_kind"),
        sa.CheckConstraint("status IN ('PENDING', 'CLAIMED', 'PUBLISHED', 'CANCELLED')", name="chk_outbox_status"),
        sa.CheckConstraint("attempt > 0", name="chk_outbox_attempt"),
        sa.ForeignKeyConstraint(["job_id"], ["ai_job.id"]),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("job_id", "attempt", "event_kind", name="uq_outbox_event_job_attempt_kind"),
    )
    op.create_index("idx_outbox_claim_expires", "outbox_event", ["claim_expires_at", "event_id"], unique=False)
    op.create_index("idx_outbox_status_available", "outbox_event", ["status", "available_at", "event_id"], unique=False)

    # Outbox는 30일, Job은 90일 보존이라 Outbox가 먼저 삭제될 수 있으므로 ON DELETE SET NULL로
    # 둡니다 — 기본값(NO ACTION)이면 Outbox 정리 배치가 FK 위반으로 실패합니다.
    op.create_foreign_key(
        "fk_ai_job_expected_event",
        "ai_job",
        "outbox_event",
        ["expected_event_id"],
        ["event_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_ai_job_last_consumed_event",
        "ai_job",
        "outbox_event",
        ["last_consumed_event_id"],
        ["event_id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "idempotency_record",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("user_id", sa.CHAR(length=36), nullable=False),
        sa.Column("operation_id", sa.String(length=100), nullable=False),
        sa.Column("key_hmac_version", sa.String(length=20), nullable=False),
        sa.Column("key_hmac", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "record_type",
            sa.Enum("ASYNC_JOB", "SYNC_MUTATION", name="idempotencyrecordtype", native_enum=False, length=30),
            nullable=False,
        ),
        sa.Column("job_id", sa.CHAR(length=36), nullable=True),
        sa.Column("parent_resource_id", sa.CHAR(length=36), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body_snapshot", sa.LargeBinary(), nullable=True),
        sa.Column("encryption_key_version", sa.String(length=50), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("record_type IN ('ASYNC_JOB', 'SYNC_MUTATION')", name="chk_idempotency_record_type"),
        sa.CheckConstraint(
            "("
            "record_type = 'ASYNC_JOB' "
            "AND job_id IS NOT NULL "
            "AND parent_resource_id IS NULL "
            "AND response_status IS NULL "
            "AND response_body_snapshot IS NULL "
            "AND encryption_key_version IS NULL"
            ") OR ("
            "record_type = 'SYNC_MUTATION' "
            "AND job_id IS NULL "
            "AND parent_resource_id IS NOT NULL "
            "AND response_status IS NOT NULL "
            "AND response_body_snapshot IS NOT NULL "
            "AND encryption_key_version IS NOT NULL"
            ")",
            name="chk_idempotency_record_payload",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["ai_job.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_idempotency_job"),
    )
    # ASYNC_JOB의 parent_resource_id는 항상 NULL이라, parent_resource_id를 포함한 단일 UNIQUE
    # 제약으로는 PostgreSQL이 NULL끼리 다르다고 취급해 중복 접수를 막지 못합니다. record_type별
    # scope가 다른 두 partial unique index로 나눠서 겁니다.
    op.create_index(
        "uq_idempotency_async_scope",
        "idempotency_record",
        ["record_type", "user_id", "operation_id", "key_hmac"],
        unique=True,
        postgresql_where=sa.text("record_type = 'ASYNC_JOB'"),
    )
    op.create_index(
        "uq_idempotency_sync_scope",
        "idempotency_record",
        ["record_type", "user_id", "operation_id", "parent_resource_id", "key_hmac"],
        unique=True,
        postgresql_where=sa.text("record_type = 'SYNC_MUTATION'"),
    )
    op.create_index("idx_idempotency_expires", "idempotency_record", ["expires_at", "id"], unique=False)
    op.create_index(
        "idx_idempotency_user_operation", "idempotency_record", ["user_id", "operation_id", "created_at"], unique=False
    )

    op.create_table(
        "message_quarantine",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("stream_name", sa.String(length=100), nullable=False),
        sa.Column("stream_entry_id", sa.String(length=100), nullable=False),
        sa.Column("message_digest", sa.String(length=128), nullable=False),
        sa.Column("job_id", sa.CHAR(length=36), nullable=True),
        sa.Column("original_event_id", sa.CHAR(length=36), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=False),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("original_schema_version", sa.String(length=20), nullable=True),
        sa.Column("trace_id", sa.String(length=100), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stream_name", "stream_entry_id", name="uq_message_quarantine_stream_entry"),
    )
    op.create_index(
        "idx_message_quarantine_failure", "message_quarantine", ["failure_code", "received_at"], unique=False
    )
    op.create_index("idx_message_quarantine_received", "message_quarantine", ["received_at", "id"], unique=False)
    op.create_index("idx_message_quarantine_job", "message_quarantine", ["job_id"], unique=False)

    op.create_table(
        "dlq_outbox_event",
        sa.Column("event_id", sa.CHAR(length=36), nullable=False),
        sa.Column("quarantine_id", sa.CHAR(length=36), nullable=False),
        sa.Column(
            "event_kind",
            sa.Enum("QUARANTINE_RECORDED", name="dlqoutboxeventkind", native_enum=False, length=30),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("original_schema_version", sa.String(length=20), nullable=True),
        sa.Column(
            "status",
            sa.Enum("PENDING", "CLAIMED", "PUBLISHED", name="dlqoutboxeventstatus", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("claim_token", sa.String(length=100), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("event_kind IN ('QUARANTINE_RECORDED')", name="chk_dlq_outbox_event_kind"),
        sa.CheckConstraint("status IN ('PENDING', 'CLAIMED', 'PUBLISHED')", name="chk_dlq_outbox_status"),
        sa.CheckConstraint("attempt_count >= 0", name="chk_dlq_outbox_attempt_count"),
        sa.ForeignKeyConstraint(["quarantine_id"], ["message_quarantine.id"]),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("quarantine_id", name="uq_dlq_outbox_quarantine"),
    )
    op.create_index("idx_dlq_outbox_claim_expires", "dlq_outbox_event", ["claim_expires_at", "event_id"], unique=False)
    op.create_index(
        "idx_dlq_outbox_status_available", "dlq_outbox_event", ["status", "available_at", "event_id"], unique=False
    )


def downgrade() -> None:
    connection = op.get_bind()
    _ensure_downgrade_is_data_safe(connection)

    op.drop_index("idx_dlq_outbox_status_available", table_name="dlq_outbox_event")
    op.drop_index("idx_dlq_outbox_claim_expires", table_name="dlq_outbox_event")
    op.drop_table("dlq_outbox_event")

    op.execute("DROP INDEX IF EXISTS idx_message_quarantine_job")
    op.drop_index("idx_message_quarantine_received", table_name="message_quarantine")
    op.drop_index("idx_message_quarantine_failure", table_name="message_quarantine")
    op.drop_table("message_quarantine")

    op.drop_index("idx_idempotency_user_operation", table_name="idempotency_record")
    op.drop_index("idx_idempotency_expires", table_name="idempotency_record")
    op.drop_index("uq_idempotency_sync_scope", table_name="idempotency_record")
    op.drop_index("uq_idempotency_async_scope", table_name="idempotency_record")
    op.drop_table("idempotency_record")

    op.drop_constraint("fk_ai_job_last_consumed_event", "ai_job", type_="foreignkey")
    op.drop_constraint("fk_ai_job_expected_event", "ai_job", type_="foreignkey")

    op.drop_index("idx_outbox_status_available", table_name="outbox_event")
    op.drop_index("idx_outbox_claim_expires", table_name="outbox_event")
    op.drop_table("outbox_event")

    op.drop_index("idx_ai_job_attempt_status_started", table_name="ai_job_attempt")
    op.drop_table("ai_job_attempt")

    op.execute("DROP INDEX IF EXISTS idx_ai_job_lease_expires")
    op.drop_index("idx_ai_job_status_available", table_name="ai_job")
    op.execute("DROP INDEX IF EXISTS idx_ai_job_user_status_updated")
    op.drop_index("idx_ai_job_expected_event", table_name="ai_job")
    op.drop_table("ai_job")
