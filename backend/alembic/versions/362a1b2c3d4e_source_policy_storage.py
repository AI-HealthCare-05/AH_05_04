"""Persist Source policy and external version (PD-362).

Revision ID: 362a1b2c3d4e
Revises: 3984b5c6d7e8
"""

import sqlalchemy as sa
from alembic import op

revision = "362a1b2c3d4e"
down_revision = "3984b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE rag_source_snapshot, rag_citation IN ACCESS EXCLUSIVE MODE"))
    for table_name in ("rag_source_snapshot", "rag_citation"):
        if connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table_name} WHERE length(source_version) > 200)")):
            raise RuntimeError("Source version exceeds PD-362 length; review existing data before migration")

    op.add_column("rag_source", sa.Column("max_rejected_records", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("rag_source", sa.Column("max_rejection_rate", sa.Numeric(), nullable=False, server_default="0"))
    op.add_column(
        "rag_source", sa.Column("empty_result_policy", sa.String(20), nullable=False, server_default="REJECT")
    )
    op.create_check_constraint("chk_rag_source_max_rejected_records", "rag_source", "max_rejected_records >= 0")
    op.create_check_constraint(
        "chk_rag_source_max_rejection_rate", "rag_source", "max_rejection_rate >= 0 AND max_rejection_rate <= 1"
    )
    op.create_check_constraint("chk_rag_source_empty_result_policy", "rag_source", "empty_result_policy = 'REJECT'")
    op.add_column("rag_source_snapshot", sa.Column("external_version", sa.String(200), nullable=True))
    op.drop_constraint("fk_rag_citation_snapshot_version", "rag_citation", type_="foreignkey")
    for table_name, constraint in (
        ("rag_source_snapshot", "chk_rag_source_snapshot_version_length"),
        ("rag_citation", "chk_rag_citation_source_version_length"),
    ):
        op.alter_column(table_name, "source_version", type_=sa.String(200), existing_type=sa.String(255))
        op.create_check_constraint(constraint, table_name, "length(source_version) <= 200")
    op.create_foreign_key(
        "fk_rag_citation_snapshot_version",
        "rag_citation",
        "rag_source_snapshot",
        ["source_snapshot_id", "source_version"],
        ["id", "source_version"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE rag_source, rag_source_snapshot, rag_citation IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM rag_source WHERE max_rejected_records <> 0 "
            "OR max_rejection_rate <> 0 OR empty_result_policy <> 'REJECT') "
            "OR EXISTS (SELECT 1 FROM rag_source_snapshot WHERE external_version IS NOT NULL)"
        )
    ):
        raise RuntimeError("Source policy or external version would be lost; downgrade refused")

    op.drop_constraint("fk_rag_citation_snapshot_version", "rag_citation", type_="foreignkey")
    for table_name, constraint in (
        ("rag_source_snapshot", "chk_rag_source_snapshot_version_length"),
        ("rag_citation", "chk_rag_citation_source_version_length"),
    ):
        op.drop_constraint(constraint, table_name, type_="check")
        op.alter_column(table_name, "source_version", type_=sa.String(255), existing_type=sa.String(200))
    op.create_foreign_key(
        "fk_rag_citation_snapshot_version",
        "rag_citation",
        "rag_source_snapshot",
        ["source_snapshot_id", "source_version"],
        ["id", "source_version"],
    )
    op.drop_column("rag_source_snapshot", "external_version")
    for column_name in ("max_rejected_records", "max_rejection_rate", "empty_result_policy"):
        op.drop_constraint(f"chk_rag_source_{column_name}", "rag_source", type_="check")
        op.drop_column("rag_source", column_name)
