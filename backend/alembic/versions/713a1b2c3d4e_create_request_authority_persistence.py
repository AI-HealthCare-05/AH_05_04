"""Create historical REQUEST authority persistence (#713).

#709 Production Reader가 소비할 REQUEST Guard·Source Decision·Member Decision의 historical
request-bound 증거를 만듭니다. Trigger·RLS·Stored Procedure·사용자 정의 DB 함수를 추가하지 않고
일반 FK·UNIQUE·CHECK만 사용합니다. 불변성은 Python Repository의 append-only writer가 관리합니다.
"""

import sqlalchemy as sa
from alembic import op

revision = "713a1b2c3d4e"
down_revision = "668a1b2c3d4e"
branch_labels = None
depends_on = None

_GUARD_REF_COLUMNS = (
    "request_guard_artifact_code",
    "request_guard_artifact_version",
    "request_guard_content_sha256",
)
_GUARD_TARGET_COLUMNS = ("artifact_code", "artifact_version", "artifact_content_sha256")

# downgrade 검사·drop 순서. 자식 Decision부터 본다.
_AUTHORITY_TABLES = (
    "rag_request_member_decision",
    "rag_request_source_decision",
    "rag_request_guard_authority",
)


def upgrade() -> None:
    op.create_table(
        "rag_request_guard_authority",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("artifact_code", sa.String(length=100), nullable=False),
        sa.Column("artifact_version", sa.String(length=50), nullable=False),
        sa.Column("artifact_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.CHAR(length=36), nullable=False),
        sa.Column("request_operation_code", sa.String(length=100), nullable=False),
        sa.Column("decision_stage", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rag_request_guard_authority"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_rag_request_guard_authority_user"),
        sa.UniqueConstraint(
            "artifact_code",
            "artifact_version",
            "artifact_content_sha256",
            name="uq_rag_request_guard_authority_artifact",
        ),
        sa.CheckConstraint(
            "artifact_code = 'request_guard_authority'",
            name="chk_rag_request_guard_authority_artifact_code",
        ),
        sa.CheckConstraint(
            "length(trim(artifact_version)) > 0",
            name="chk_rag_request_guard_authority_artifact_version_nonblank",
        ),
        sa.CheckConstraint(
            "artifact_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_guard_authority_content_sha256",
        ),
        sa.CheckConstraint(
            "length(trim(request_operation_code)) > 0",
            name="chk_rag_request_guard_authority_operation_nonblank",
        ),
        sa.CheckConstraint(
            "decision_stage = 'REQUEST'",
            name="chk_rag_request_guard_authority_decision_stage",
        ),
    )
    op.create_index("idx_rag_request_guard_authority_user", "rag_request_guard_authority", ["user_id"])

    op.create_table(
        "rag_request_source_decision",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("artifact_code", sa.String(length=100), nullable=False),
        sa.Column("artifact_version", sa.String(length=50), nullable=False),
        sa.Column("artifact_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_guard_artifact_code", sa.String(length=100), nullable=False),
        sa.Column("request_guard_artifact_version", sa.String(length=50), nullable=False),
        sa.Column("request_guard_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.CHAR(length=36), nullable=False),
        sa.Column("request_operation_code", sa.String(length=100), nullable=False),
        sa.Column("decision_stage", sa.String(length=20), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_code", sa.String(length=200), nullable=False),
        sa.Column("source_version", sa.String(length=200), nullable=False),
        sa.Column("actual_decision_outcome", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rag_request_source_decision"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_rag_request_source_decision_user"),
        sa.ForeignKeyConstraint(
            list(_GUARD_REF_COLUMNS),
            [f"rag_request_guard_authority.{column}" for column in _GUARD_TARGET_COLUMNS],
            name="fk_rag_request_source_decision_guard",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "artifact_code",
            "artifact_version",
            "artifact_content_sha256",
            name="uq_rag_request_source_decision_artifact",
        ),
        sa.CheckConstraint(
            "artifact_code = 'request_source_decision_authority'",
            name="chk_rag_request_source_decision_artifact_code",
        ),
        sa.CheckConstraint(
            "artifact_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_source_decision_content_sha256",
        ),
        sa.CheckConstraint(
            "request_guard_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_source_decision_guard_sha256",
        ),
        sa.CheckConstraint(
            "length(trim(request_operation_code)) > 0",
            name="chk_rag_request_source_decision_operation_nonblank",
        ),
        sa.CheckConstraint(
            "decision_stage = 'REQUEST'",
            name="chk_rag_request_source_decision_decision_stage",
        ),
        sa.CheckConstraint(
            "actual_decision_outcome IN ('PASS', 'FAIL')",
            name="chk_rag_request_source_decision_outcome",
        ),
        sa.CheckConstraint(
            "length(trim(source_code)) > 0",
            name="chk_rag_request_source_decision_source_code_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(source_version)) > 0",
            name="chk_rag_request_source_decision_source_version_nonblank",
        ),
    )
    op.create_index(
        "idx_rag_request_source_decision_guard",
        "rag_request_source_decision",
        list(_GUARD_REF_COLUMNS),
    )

    op.create_table(
        "rag_request_member_decision",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("artifact_code", sa.String(length=100), nullable=False),
        sa.Column("artifact_version", sa.String(length=50), nullable=False),
        sa.Column("artifact_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_guard_artifact_code", sa.String(length=100), nullable=False),
        sa.Column("request_guard_artifact_version", sa.String(length=50), nullable=False),
        sa.Column("request_guard_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.CHAR(length=36), nullable=False),
        sa.Column("request_operation_code", sa.String(length=100), nullable=False),
        sa.Column("decision_stage", sa.String(length=20), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_member_id", sa.CHAR(length=36), nullable=False),
        sa.Column("member_kind", sa.String(length=30), nullable=False),
        sa.Column("endpoint_code", sa.String(length=200), nullable=True),
        sa.Column("operation_code", sa.String(length=200), nullable=True),
        sa.Column("member_artifact_code", sa.String(length=200), nullable=True),
        sa.Column("member_artifact_version", sa.String(length=200), nullable=True),
        sa.Column("actual_decision_outcome", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rag_request_member_decision"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_rag_request_member_decision_user"),
        sa.ForeignKeyConstraint(
            list(_GUARD_REF_COLUMNS),
            [f"rag_request_guard_authority.{column}" for column in _GUARD_TARGET_COLUMNS],
            name="fk_rag_request_member_decision_guard",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "artifact_code",
            "artifact_version",
            "artifact_content_sha256",
            name="uq_rag_request_member_decision_artifact",
        ),
        sa.CheckConstraint(
            "artifact_code = 'request_member_decision_authority'",
            name="chk_rag_request_member_decision_artifact_code",
        ),
        sa.CheckConstraint(
            "artifact_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_member_decision_content_sha256",
        ),
        sa.CheckConstraint(
            "request_guard_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_request_member_decision_guard_sha256",
        ),
        sa.CheckConstraint(
            "length(trim(request_operation_code)) > 0",
            name="chk_rag_request_member_decision_operation_nonblank",
        ),
        sa.CheckConstraint(
            "decision_stage = 'REQUEST'",
            name="chk_rag_request_member_decision_decision_stage",
        ),
        sa.CheckConstraint(
            "actual_decision_outcome IN ('PASS', 'FAIL')",
            name="chk_rag_request_member_decision_outcome",
        ),
        sa.CheckConstraint(
            "member_kind IN ('ENDPOINT_OPERATION', 'ARTIFACT')",
            name="chk_rag_request_member_decision_member_kind",
        ),
        sa.CheckConstraint(
            "(member_kind = 'ENDPOINT_OPERATION' AND endpoint_code IS NOT NULL "
            "AND member_artifact_code IS NULL AND member_artifact_version IS NULL) OR "
            "(member_kind = 'ARTIFACT' AND member_artifact_code IS NOT NULL "
            "AND member_artifact_version IS NOT NULL "
            "AND endpoint_code IS NULL AND operation_code IS NULL)",
            name="chk_rag_request_member_decision_identity_shape",
        ),
    )
    op.create_index(
        "idx_rag_request_member_decision_guard",
        "rag_request_member_decision",
        list(_GUARD_REF_COLUMNS),
    )


def _has_rows(table_name: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT 1 FROM {table_name} LIMIT 1")).first())


def _raise_if_request_authority_data_exists() -> None:
    """실제 authority 증거가 남아 있으면 downgrade를 거부합니다.

    세 표는 append-only historical 증거이므로 drop은 조용한 데이터 손실입니다. 검사와 drop
    사이에 write가 끼어들지 않도록 먼저 ACCESS EXCLUSIVE lock을 잡습니다.
    """
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE " + ", ".join(_AUTHORITY_TABLES) + " IN ACCESS EXCLUSIVE MODE"))
    non_empty = [table for table in _AUTHORITY_TABLES if _has_rows(table)]
    if non_empty:
        raise RuntimeError("Refusing to downgrade request authority tables with existing data: " + ", ".join(non_empty))


def downgrade() -> None:
    _raise_if_request_authority_data_exists()
    op.drop_index("idx_rag_request_member_decision_guard", table_name="rag_request_member_decision")
    op.drop_table("rag_request_member_decision")
    op.drop_index("idx_rag_request_source_decision_guard", table_name="rag_request_source_decision")
    op.drop_table("rag_request_source_decision")
    op.drop_index("idx_rag_request_guard_authority_user", table_name="rag_request_guard_authority")
    op.drop_table("rag_request_guard_authority")
