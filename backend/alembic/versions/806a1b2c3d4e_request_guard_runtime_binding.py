"""Persist per-request REQUEST Guard Runtime Binding authority (#806)."""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "806a1b2c3d4e"
down_revision = "810a1b2c3d4e"
branch_labels = None
depends_on = None

_TABLE = "rag_request_guard_runtime_binding"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("request_guard_decision_id", sa.CHAR(length=36), nullable=False),
        sa.Column("artifact_code", sa.String(length=100), nullable=False),
        sa.Column("artifact_version", sa.String(length=50), nullable=False),
        sa.Column("artifact_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("actual_decision_outcome", sa.String(length=10), nullable=False),
        sa.Column("user_id", sa.CHAR(length=36), nullable=False),
        sa.Column("request_operation_code", sa.String(length=100), nullable=False),
        sa.Column("decision_stage", sa.String(length=20), nullable=False),
        sa.Column("environment_code", sa.String(length=50), nullable=False),
        sa.Column("bundle_id", sa.CHAR(length=36), nullable=False),
        sa.Column("bundle_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("request_scope_codes", postgresql.JSONB(), nullable=False),
        sa.Column("scope_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("legacy_request_guard_artifact_code", sa.String(length=100), nullable=False),
        sa.Column("legacy_request_guard_artifact_version", sa.String(length=50), nullable=False),
        sa.Column("legacy_request_guard_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("request_guard_decision_id", name="pk_rag_request_guard_runtime_binding"),
        sa.UniqueConstraint(
            "artifact_code",
            "artifact_version",
            "artifact_content_sha256",
            name="uq_rag_request_guard_runtime_binding_artifact",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_rag_request_guard_runtime_binding_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bundle_id", "bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_rag_request_guard_runtime_binding_bundle",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "legacy_request_guard_artifact_code",
                "legacy_request_guard_artifact_version",
                "legacy_request_guard_content_sha256",
            ],
            [
                "rag_request_guard_authority.artifact_code",
                "rag_request_guard_authority.artifact_version",
                "rag_request_guard_authority.artifact_content_sha256",
            ],
            name="fk_rag_request_guard_runtime_binding_legacy_guard",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "artifact_code = 'request_guard_runtime_binding'",
            name="chk_rag_request_guard_runtime_binding_artifact_code",
        ),
        sa.CheckConstraint(
            "length(trim(artifact_version)) > 0",
            name="chk_rag_request_guard_runtime_binding_artifact_version_nonblank",
        ),
        sa.CheckConstraint(
            "artifact_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_guard_runtime_binding_content_sha256",
        ),
        sa.CheckConstraint(
            "actual_decision_outcome IN ('PASS', 'FAIL')",
            name="chk_rag_request_guard_runtime_binding_outcome",
        ),
        sa.CheckConstraint(
            "decision_stage = 'REQUEST'",
            name="chk_rag_request_guard_runtime_binding_stage",
        ),
        sa.CheckConstraint(
            "environment_code IN ('LOCAL', 'TEST', 'CLOSED_DEMO', 'PRODUCTION')",
            name="chk_rag_request_guard_runtime_binding_environment",
        ),
        sa.CheckConstraint(
            "bundle_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_guard_runtime_binding_bundle_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(request_scope_codes) = 'array' AND jsonb_array_length(request_scope_codes) > 0",
            name="chk_rag_request_guard_runtime_binding_scopes",
        ),
        sa.CheckConstraint(
            "scope_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_guard_runtime_binding_scope_hash",
        ),
        sa.CheckConstraint(
            "legacy_request_guard_artifact_code = 'request_guard_authority'",
            name="chk_rag_request_guard_runtime_binding_legacy_code",
        ),
        sa.CheckConstraint(
            "legacy_request_guard_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_guard_runtime_binding_legacy_hash",
        ),
    )
    op.create_index("idx_rag_request_guard_runtime_binding_user", _TABLE, ["user_id"])
    op.create_index("idx_rag_request_guard_runtime_binding_bundle", _TABLE, ["bundle_id", "bundle_manifest_hash"])


def _has_rows(connection: Any) -> bool:
    return bool(connection.execute(sa.text(f"SELECT 1 FROM {_TABLE} LIMIT 1")).first())


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(f"LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE"))
    if _has_rows(bind):
        raise RuntimeError("Refusing to downgrade request guard runtime bindings with existing data")
    op.drop_index("idx_rag_request_guard_runtime_binding_bundle", table_name=_TABLE)
    op.drop_index("idx_rag_request_guard_runtime_binding_user", table_name=_TABLE)
    op.drop_table(_TABLE)
