"""Pin the exact #806 request guard runtime binding on execution contexts."""

import sqlalchemy as sa
from alembic import op

revision = "180a1b2c3d4e"
down_revision = "921a1b2c3d4e"
branch_labels = None
depends_on = None

_TABLE = "ai_job_execution_context"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("request_guard_runtime_binding_artifact_code", sa.String(length=100)))
    op.add_column(_TABLE, sa.Column("request_guard_runtime_binding_artifact_version", sa.String(length=50)))
    op.add_column(_TABLE, sa.Column("request_guard_runtime_binding_content_sha256", sa.String(length=64)))
    op.create_check_constraint(
        "chk_ai_job_exec_ctx_request_guard_pin_all_or_none",
        _TABLE,
        "(request_guard_runtime_binding_artifact_code IS NULL "
        "AND request_guard_runtime_binding_artifact_version IS NULL "
        "AND request_guard_runtime_binding_content_sha256 IS NULL) "
        "OR (request_guard_runtime_binding_artifact_code IS NOT NULL "
        "AND request_guard_runtime_binding_artifact_version IS NOT NULL "
        "AND request_guard_runtime_binding_content_sha256 IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_ai_job_execution_context_request_guard_runtime_binding",
        _TABLE,
        "rag_request_guard_runtime_binding",
        [
            "request_guard_runtime_binding_artifact_code",
            "request_guard_runtime_binding_artifact_version",
            "request_guard_runtime_binding_content_sha256",
        ],
        ["artifact_code", "artifact_version", "artifact_content_sha256"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_ai_job_execution_context_request_guard_runtime_binding", _TABLE, type_="foreignkey")
    op.drop_constraint(
        "chk_ai_job_exec_ctx_request_guard_pin_all_or_none",
        _TABLE,
        type_="check",
    )
    op.drop_column(_TABLE, "request_guard_runtime_binding_content_sha256")
    op.drop_column(_TABLE, "request_guard_runtime_binding_artifact_version")
    op.drop_column(_TABLE, "request_guard_runtime_binding_artifact_code")
