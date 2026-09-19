"""Catalog 승인 운영 권한·감사 저장소 (#526 Phase 2).

승인 발급·철회를 수행할 현재 운영자 권한(`catalog_approval_permission`)과 그 이력을 보존하는
append-only 감사(`catalog_approval_audit`)를 더한다. Phase 1의 승인 세 표는 재생성하거나
변경하지 않는다. 업무 판정은 Python 계층에서 하며 Trigger·RLS·업무용 DB 함수를 두지 않는다.
"""

import sqlalchemy as sa
from alembic import op

revision = "526c1d2e3f4a"
down_revision = "807a1b2c3d4e"
branch_labels = None
depends_on = None

PERMISSION = "catalog_approval_permission"
AUDIT = "catalog_approval_audit"
EVENT_KINDS = (
    "GRANT_PERMISSION",
    "ISSUE_SOURCE",
    "ISSUE_CATALOG",
    "REVOKE_PERMISSION",
    "REVOKE_SOURCE",
    "REVOKE_CATALOG",
)
_EVENT_KIND_LIST = ", ".join(f"'{kind}'" for kind in EVENT_KINDS)


def upgrade() -> None:
    op.create_table(
        PERMISSION,
        sa.Column("user_id", sa.CHAR(36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("evidence_ref", sa.String(500), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("user_id", name="pk_catalog_approval_permission"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name="fk_catalog_approval_permission_user", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("length(trim(evidence_ref)) > 0", name="chk_catalog_approval_permission_evidence_nonblank"),
        sa.CheckConstraint("revision >= 0", name="chk_catalog_approval_permission_revision"),
    )

    op.create_table(
        AUDIT,
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("request_id", sa.CHAR(36), nullable=False),
        sa.Column("event_kind", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.CHAR(36), nullable=False),
        sa.Column("subject_user_id", sa.CHAR(36), nullable=True),
        sa.Column("source_approval_id", sa.CHAR(36), nullable=True),
        sa.Column("build_approval_id", sa.CHAR(36), nullable=True),
        sa.Column("source_snapshot_id", sa.CHAR(36), nullable=True),
        sa.Column("source_version", sa.String(200), nullable=True),
        sa.Column("purpose", sa.String(60), nullable=True),
        sa.Column("catalog_version", sa.String(100), nullable=True),
        sa.Column("export_checksum", sa.String(64), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_catalog_approval_audit"),
        # 하나의 issue command가 같은 request_id로 ISSUE_SOURCE·ISSUE_CATALOG 두 event를 남긴다.
        sa.UniqueConstraint("actor_id", "request_id", "event_kind", name="uq_catalog_approval_audit_request"),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"], name="fk_catalog_approval_audit_actor", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["subject_user_id"], ["user.id"], name="fk_catalog_approval_audit_subject", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_approval_id"],
            ["catalog_source_approval.id"],
            name="fk_catalog_approval_audit_source_approval",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["build_approval_id"],
            ["catalog_build_approval.id"],
            name="fk_catalog_approval_audit_build_approval",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(f"event_kind IN ({_EVENT_KIND_LIST})", name="chk_catalog_approval_audit_event_kind"),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="chk_catalog_approval_audit_fingerprint"),
        sa.CheckConstraint(
            "export_checksum IS NULL OR export_checksum ~ '^[0-9a-f]{64}$'",
            name="chk_catalog_approval_audit_export_checksum",
        ),
        sa.CheckConstraint(
            "purpose IS NULL OR length(trim(purpose)) > 0", name="chk_catalog_approval_audit_purpose_nonblank"
        ),
    )
    op.create_index("idx_catalog_approval_audit_request", AUDIT, ["request_id"])
    op.create_index("idx_catalog_approval_audit_actor", AUDIT, ["actor_id"])


def downgrade() -> None:
    # 권한 이력과 승인 증빙이 있으면 되돌리지 않는다. #537의 fail-closed 규칙과 같다.
    bind = op.get_bind()
    for table in (AUDIT, PERMISSION):
        if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar():
            raise RuntimeError("Catalog approval permission/audit records exist; downgrade would drop evidence.")
    op.drop_index("idx_catalog_approval_audit_actor", table_name=AUDIT)
    op.drop_index("idx_catalog_approval_audit_request", table_name=AUDIT)
    op.drop_table(AUDIT)
    op.drop_table(PERMISSION)
