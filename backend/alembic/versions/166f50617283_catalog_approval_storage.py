"""Catalog 사용 승인 저장소 (#166, #526).

승인 payload·기간·출처 목록은 발급 후 수정하지 않고 철회 metadata만 갱신한다. 만료 상태를
바꾸는 scheduler 없이 기간 비교로 판정한다. 업무 판정은 Python Service/Repository에서 하며
Trigger·RLS·업무용 DB 함수를 추가하지 않는다.
"""

import sqlalchemy as sa
from alembic import op

revision = "166f50617283"
down_revision = "166f40516273"
branch_labels = None
depends_on = None

SOURCE_APPROVAL = "catalog_source_approval"
BUILD_APPROVAL = "catalog_build_approval"
BUILD_APPROVAL_SOURCE = "catalog_build_approval_source"
_REVOCATION_SHAPE = (
    "(revoked_at IS NULL AND revoked_by IS NULL AND revoked_reason IS NULL) OR "
    "(revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_reason IS NOT NULL)"
)


def upgrade() -> None:
    op.create_table(
        SOURCE_APPROVAL,
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(36), nullable=False),
        sa.Column("source_version", sa.String(200), nullable=False),
        sa.Column("purpose", sa.String(60), nullable=False),
        sa.Column("actor_id", sa.CHAR(36), nullable=False),
        sa.Column("evidence_ref", sa.String(500), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.CHAR(36), nullable=True),
        sa.Column("revoked_reason", sa.String(200), nullable=True),
        sa.Column("issued_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_catalog_source_approval"),
        sa.UniqueConstraint(
            "source_snapshot_id", "purpose", "issued_revision", name="uq_catalog_source_approval_target"
        ),
        sa.UniqueConstraint("id", "source_snapshot_id", "source_version", name="uq_catalog_source_approval_id_target"),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id", "source_version"],
            ["rag_source_snapshot.id", "rag_source_snapshot.source_version"],
            name="fk_catalog_source_approval_snapshot_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["user.id"], name="fk_catalog_source_approval_actor", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"], ["user.id"], name="fk_catalog_source_approval_revoked_by", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("length(trim(purpose)) > 0", name="chk_catalog_source_approval_purpose_nonblank"),
        sa.CheckConstraint("length(trim(evidence_ref)) > 0", name="chk_catalog_source_approval_evidence_nonblank"),
        sa.CheckConstraint("expires_at > valid_from", name="chk_catalog_source_approval_validity_window"),
        sa.CheckConstraint(_REVOCATION_SHAPE, name="chk_catalog_source_approval_revocation_shape"),
    )
    op.create_index("idx_catalog_source_approval_snapshot", SOURCE_APPROVAL, ["source_snapshot_id"])

    op.create_table(
        BUILD_APPROVAL,
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("catalog_version", sa.String(100), nullable=False),
        sa.Column("export_checksum", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(100), nullable=False),
        sa.Column("manifest_spec_version", sa.String(100), nullable=False),
        sa.Column("approved_export_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("is_complete", sa.Boolean(), nullable=False),
        sa.Column("actor_id", sa.CHAR(36), nullable=False),
        sa.Column("evidence_ref", sa.String(500), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.CHAR(36), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column("issued_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_catalog_build_approval"),
        sa.UniqueConstraint(
            "catalog_version", "export_checksum", "issued_revision", name="uq_catalog_build_approval_target"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"], name="fk_catalog_build_approval_actor", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["revoked_by"], ["user.id"], name="fk_catalog_build_approval_revoked_by", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("length(trim(catalog_version)) > 0", name="chk_catalog_build_approval_version_nonblank"),
        sa.CheckConstraint("export_checksum ~ '^[0-9a-f]{64}$'", name="chk_catalog_build_approval_checksum"),
        sa.CheckConstraint("length(trim(schema_version)) > 0", name="chk_catalog_build_approval_schema_nonblank"),
        sa.CheckConstraint(
            "length(trim(manifest_spec_version)) > 0", name="chk_catalog_build_approval_manifest_spec_nonblank"
        ),
        sa.CheckConstraint("length(trim(evidence_ref)) > 0", name="chk_catalog_build_approval_evidence_nonblank"),
        sa.CheckConstraint("expires_at > valid_from", name="chk_catalog_build_approval_validity_window"),
        sa.CheckConstraint(_REVOCATION_SHAPE, name="chk_catalog_build_approval_revocation_shape"),
    )
    op.create_index("idx_catalog_build_approval_checksum", BUILD_APPROVAL, ["export_checksum"])

    op.create_table(
        BUILD_APPROVAL_SOURCE,
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("build_approval_id", sa.CHAR(36), nullable=False),
        sa.Column("source_approval_id", sa.CHAR(36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(36), nullable=False),
        sa.Column("source_version", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_catalog_build_approval_source"),
        sa.UniqueConstraint(
            "build_approval_id", "source_snapshot_id", name="uq_catalog_build_approval_source_snapshot"
        ),
        sa.UniqueConstraint(
            "build_approval_id", "source_approval_id", name="uq_catalog_build_approval_source_approval"
        ),
        sa.ForeignKeyConstraint(
            ["build_approval_id"],
            [f"{BUILD_APPROVAL}.id"],
            name="fk_catalog_build_approval_source_build",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_approval_id", "source_snapshot_id", "source_version"],
            [
                f"{SOURCE_APPROVAL}.id",
                f"{SOURCE_APPROVAL}.source_snapshot_id",
                f"{SOURCE_APPROVAL}.source_version",
            ],
            name="fk_catalog_build_approval_source_target",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("idx_catalog_build_approval_source_build", BUILD_APPROVAL_SOURCE, ["build_approval_id"])


def downgrade() -> None:
    # 발급된 승인 사실이 있으면 되돌리지 않는다. 증빙이 사라지면 과거 Set의 근거를 복원할 수 없다.
    bind = op.get_bind()
    for table in (BUILD_APPROVAL_SOURCE, BUILD_APPROVAL, SOURCE_APPROVAL):
        if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar():
            raise RuntimeError("Catalog approval records exist; downgrade would drop approval evidence.")
    op.drop_index("idx_catalog_build_approval_source_build", table_name=BUILD_APPROVAL_SOURCE)
    op.drop_table(BUILD_APPROVAL_SOURCE)
    op.drop_index("idx_catalog_build_approval_checksum", table_name=BUILD_APPROVAL)
    op.drop_table(BUILD_APPROVAL)
    op.drop_index("idx_catalog_source_approval_snapshot", table_name=SOURCE_APPROVAL)
    op.drop_table(SOURCE_APPROVAL)
