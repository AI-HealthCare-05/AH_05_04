"""Persist Citation Authorization Decisions and Receipts (#869).

Revision ID: 869a1b2c3d4e
Revises: 853a1b2c3d4e
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "869a1b2c3d4e"
down_revision = "853a1b2c3d4e"
branch_labels = None
depends_on = None

SOURCE = "rag_citation_authorization_source_decision"
MEMBER = "rag_citation_authorization_member_decision"
RECEIPT = "rag_citation_authorization_receipt"
SELECTION = "rag_citation_authorization_receipt_selection"
TABLES = (SOURCE, MEMBER, RECEIPT, SELECTION)


def upgrade() -> None:
    op.create_table(
        SOURCE,
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("artifact_code", sa.String(100), nullable=False),
        sa.Column("artifact_version", sa.String(50), nullable=False),
        sa.Column("artifact_content_sha256", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("request_guard_decision_id", sa.CHAR(36), nullable=False),
        sa.Column("origin_guard_artifact_code", sa.String(100), nullable=False),
        sa.Column("origin_guard_artifact_version", sa.String(50), nullable=False),
        sa.Column("origin_guard_content_sha256", sa.String(64), nullable=False),
        sa.Column("user_id", sa.CHAR(36), nullable=False),
        sa.Column("request_operation_code", sa.String(100), nullable=False),
        sa.Column("environment", sa.String(20), nullable=False),
        sa.Column("bundle_id", sa.CHAR(36), nullable=False),
        sa.Column("bundle_manifest_hash", sa.String(64), nullable=False),
        sa.Column("scope_manifest_hash", sa.String(64), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(36), nullable=False),
        sa.Column("source_use_approval_id", sa.CHAR(36), nullable=False),
        sa.Column("source_code", sa.String(100), nullable=False),
        sa.Column("source_version", sa.String(200), nullable=False),
        sa.Column("approval_version", sa.String(120), nullable=False),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("evaluation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_lifecycle_status", sa.String(20), nullable=False),
        sa.Column("snapshot_verification_status", sa.String(20), nullable=False),
        sa.Column("actual_decision_outcome", sa.String(10), nullable=False),
        sa.Column("reason_code", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rag_citation_authorization_source_decision"),
        sa.UniqueConstraint(
            "artifact_code", "artifact_version", "artifact_content_sha256", name="uq_rag_citation_auth_source_artifact"
        ),
        sa.UniqueConstraint("id", "request_sha256", "artifact_content_sha256", name="uq_rag_cit_auth_source_binding"),
        sa.ForeignKeyConstraint(
            ["request_guard_decision_id"],
            ["rag_request_guard_runtime_binding.request_guard_decision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["bundle_id", "bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["rag_source_snapshot.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_use_approval_id"], ["rag_source_use_approval.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "artifact_code = 'citation_authorization_source_decision'", name="chk_rag_citation_auth_source_code"
        ),
        sa.CheckConstraint("artifact_content_sha256 ~ '^[0-9a-f]{64}$'", name="chk_rag_citation_auth_source_hash"),
        sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'", name="chk_rag_citation_auth_source_request_hash"),
        sa.CheckConstraint("purpose = 'PATIENT_CITATION'", name="chk_rag_citation_auth_source_purpose"),
        sa.CheckConstraint("actual_decision_outcome IN ('PASS', 'FAIL')", name="chk_rag_citation_auth_source_outcome"),
    )
    op.create_index("idx_rag_citation_auth_source_request", SOURCE, ["request_sha256"])

    op.create_table(
        MEMBER,
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("source_decision_id", sa.CHAR(36), nullable=False),
        sa.Column("artifact_code", sa.String(100), nullable=False),
        sa.Column("artifact_version", sa.String(50), nullable=False),
        sa.Column("artifact_content_sha256", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("source_decision_content_sha256", sa.String(64), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(36), nullable=False),
        sa.Column("source_snapshot_member_id", sa.CHAR(36), nullable=False),
        sa.Column("source_code", sa.String(100), nullable=False),
        sa.Column("source_version", sa.String(200), nullable=False),
        sa.Column("member_kind", sa.String(30), nullable=False),
        sa.Column("endpoint_code", sa.String(100), nullable=True),
        sa.Column("operation_code", sa.String(100), nullable=True),
        sa.Column("artifact_member_code", sa.String(200), nullable=True),
        sa.Column("artifact_member_version", sa.String(200), nullable=True),
        sa.Column("evaluation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("endpoint_lifecycle_status", sa.String(20), nullable=True),
        sa.Column("endpoint_runtime_status", sa.String(20), nullable=True),
        sa.Column("endpoint_acquisition_status", sa.String(20), nullable=True),
        sa.Column("operation_runtime_status", sa.String(20), nullable=True),
        sa.Column("operation_acquisition_status", sa.String(20), nullable=True),
        sa.Column("current_member_kind", sa.String(30), nullable=True),
        sa.Column("snapshot_endpoint_id", sa.CHAR(36), nullable=True),
        sa.Column("snapshot_operation_id", sa.CHAR(36), nullable=True),
        sa.Column("current_endpoint_id", sa.CHAR(36), nullable=True),
        sa.Column("current_operation_id", sa.CHAR(36), nullable=True),
        sa.Column("current_ingestion_artifact_id", sa.CHAR(36), nullable=True),
        sa.Column("artifact_fk_exists", sa.Boolean(), nullable=True),
        sa.Column("actual_decision_outcome", sa.String(10), nullable=False),
        sa.Column("reason_code", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rag_citation_authorization_member_decision"),
        sa.UniqueConstraint(
            "artifact_code", "artifact_version", "artifact_content_sha256", name="uq_rag_citation_auth_member_artifact"
        ),
        sa.UniqueConstraint(
            "id",
            "request_sha256",
            "artifact_content_sha256",
            "source_decision_id",
            name="uq_rag_cit_auth_member_binding",
        ),
        sa.ForeignKeyConstraint(
            ["source_decision_id", "request_sha256", "source_decision_content_sha256"],
            [f"{SOURCE}.id", f"{SOURCE}.request_sha256", f"{SOURCE}.artifact_content_sha256"],
            name="fk_rag_cit_auth_member_source_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["source_snapshot_member_id"], ["rag_source_snapshot_member.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "artifact_code = 'citation_authorization_member_decision'", name="chk_rag_citation_auth_member_code"
        ),
        sa.CheckConstraint("artifact_content_sha256 ~ '^[0-9a-f]{64}$'", name="chk_rag_citation_auth_member_hash"),
        sa.CheckConstraint("actual_decision_outcome IN ('PASS', 'FAIL')", name="chk_rag_citation_auth_member_outcome"),
    )
    op.create_index("idx_rag_citation_auth_member_request", MEMBER, ["request_sha256"])

    op.create_table(
        RECEIPT,
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("artifact_code", sa.String(100), nullable=False),
        sa.Column("artifact_version", sa.String(50), nullable=False),
        sa.Column("artifact_content_sha256", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("origin_guard_artifact_code", sa.String(100), nullable=False),
        sa.Column("origin_guard_artifact_version", sa.String(50), nullable=False),
        sa.Column("origin_guard_content_sha256", sa.String(64), nullable=False),
        sa.Column("origin_decision", sa.String(10), nullable=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("environment", sa.String(20), nullable=False),
        sa.Column("bundle_id", sa.String(36), nullable=False),
        sa.Column("bundle_manifest_hash", sa.String(64), nullable=False),
        sa.Column("request_scope_codes", postgresql.JSONB(), nullable=False),
        sa.Column("scope_manifest_hash", sa.String(64), nullable=False),
        sa.Column("validated_selection_sha256", sa.String(64), nullable=False),
        sa.Column("selection_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rag_citation_authorization_receipt"),
        sa.UniqueConstraint("request_sha256", name="uq_rag_citation_auth_receipt_request"),
        sa.UniqueConstraint("id", "request_sha256", name="uq_rag_cit_auth_receipt_binding"),
        sa.UniqueConstraint(
            "artifact_code", "artifact_version", "artifact_content_sha256", name="uq_rag_citation_auth_receipt_artifact"
        ),
        sa.CheckConstraint(
            "artifact_code = 'citation_authorization_receipt'", name="chk_rag_citation_auth_receipt_code"
        ),
        sa.CheckConstraint("origin_decision = 'PASS'", name="chk_rag_citation_auth_receipt_origin"),
        sa.CheckConstraint("operation = 'CITATION_AUTHORIZATION'", name="chk_rag_citation_auth_receipt_operation"),
    )

    op.create_table(
        SELECTION,
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("receipt_id", sa.CHAR(36), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("selection_order", sa.Integer(), nullable=False),
        sa.Column("source_decision_id", sa.CHAR(36), nullable=False),
        sa.Column("source_decision_content_sha256", sa.String(64), nullable=False),
        sa.Column("member_decision_id", sa.CHAR(36), nullable=False),
        sa.Column("member_decision_content_sha256", sa.String(64), nullable=False),
        sa.Column("source_code", sa.String(100), nullable=False),
        sa.Column("source_version", sa.String(200), nullable=False),
        sa.Column("member_kind", sa.String(30), nullable=False),
        sa.Column("endpoint_code", sa.String(100), nullable=True),
        sa.Column("operation_code", sa.String(100), nullable=True),
        sa.Column("artifact_member_code", sa.String(200), nullable=True),
        sa.Column("artifact_member_version", sa.String(200), nullable=True),
        sa.Column("selected_for_operation", sa.Boolean(), nullable=False),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("source_decision", sa.String(10), nullable=False),
        sa.Column("member_decision", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rag_citation_authorization_receipt_selection"),
        sa.UniqueConstraint("receipt_id", "selection_order", name="uq_rag_citation_auth_receipt_selection_order"),
        sa.ForeignKeyConstraint(
            ["receipt_id", "request_sha256"],
            [f"{RECEIPT}.id", f"{RECEIPT}.request_sha256"],
            name="fk_rag_cit_auth_selection_receipt_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_decision_id", "request_sha256", "source_decision_content_sha256"],
            [f"{SOURCE}.id", f"{SOURCE}.request_sha256", f"{SOURCE}.artifact_content_sha256"],
            name="fk_rag_cit_auth_selection_source_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["member_decision_id", "request_sha256", "member_decision_content_sha256", "source_decision_id"],
            [
                f"{MEMBER}.id",
                f"{MEMBER}.request_sha256",
                f"{MEMBER}.artifact_content_sha256",
                f"{MEMBER}.source_decision_id",
            ],
            name="fk_rag_cit_auth_selection_member_binding",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("selection_order >= 0", name="chk_rag_citation_auth_selection_order"),
        sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'", name="chk_rag_cit_auth_selection_request_hash"),
        sa.CheckConstraint(
            "source_decision_content_sha256 ~ '^[0-9a-f]{64}$'", name="chk_rag_cit_auth_selection_source_hash"
        ),
        sa.CheckConstraint(
            "member_decision_content_sha256 ~ '^[0-9a-f]{64}$'", name="chk_rag_cit_auth_selection_member_hash"
        ),
        sa.CheckConstraint("selected_for_operation", name="chk_rag_citation_auth_selection_selected"),
        sa.CheckConstraint("purpose = 'PATIENT_CITATION'", name="chk_rag_citation_auth_selection_purpose"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(TABLES):
        bind.execute(sa.text(f"LOCK TABLE {table_name} IN ACCESS EXCLUSIVE MODE"))
    if any(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table_name})")).scalar_one() for table_name in TABLES):
        raise RuntimeError("refusing destructive downgrade: Citation Authorization authority rows exist")
    op.drop_table(SELECTION)
    op.drop_table(RECEIPT)
    op.drop_index("idx_rag_citation_auth_member_request", table_name=MEMBER)
    op.drop_table(MEMBER)
    op.drop_index("idx_rag_citation_auth_source_request", table_name=SOURCE)
    op.drop_table(SOURCE)
