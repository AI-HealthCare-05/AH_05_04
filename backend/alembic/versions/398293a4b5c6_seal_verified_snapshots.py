"""Protect verified Snapshot deletion through immutable history and ordinary constraints."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "398293a4b5c6"
down_revision = "39818293a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE rag_source_snapshot, rag_source_snapshot_verification IN ACCESS EXCLUSIVE MODE")
    )
    op.add_column("rag_source_snapshot", sa.Column("verification_seal_id", sa.CHAR(36), nullable=True))
    rows = connection.execute(
        sa.text(
            "SELECT id, COALESCE(verified_at, effective_at, collected_at) AS sealed_at FROM rag_source_snapshot "
            "WHERE verification_status <> 'PENDING' OR verified_at IS NOT NULL OR effective_at IS NOT NULL"
        )
    ).mappings()
    for row in rows:
        seal_id = str(uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO rag_source_snapshot_verification "
                "(id,snapshot_id,check_name,verification_result,details_summary,verified_by,verified_at) "
                "VALUES (:seal,:snapshot,'snapshot-state-seal','NO_CHANGE',"
                "'Historical state retained; this is not publication approval','migration-398293a4b5c6',:time)"
            ),
            {"seal": seal_id, "snapshot": row["id"], "time": row["sealed_at"]},
        )
        connection.execute(
            sa.text("UPDATE rag_source_snapshot SET verification_seal_id=:seal WHERE id=:snapshot"),
            {"seal": seal_id, "snapshot": row["id"]},
        )
    op.create_unique_constraint(
        "uq_rag_verification_snapshot_id", "rag_source_snapshot_verification", ["snapshot_id", "id"]
    )
    op.create_foreign_key(
        "fk_rag_snapshot_verification_seal",
        "rag_source_snapshot",
        "rag_source_snapshot_verification",
        ["id", "verification_seal_id"],
        ["snapshot_id", "id"],
    )
    op.create_check_constraint(
        "chk_rag_snapshot_verification_seal",
        "rag_source_snapshot",
        "(verification_status = 'PENDING' AND verified_at IS NULL AND effective_at IS NULL) "
        "OR verification_seal_id IS NOT NULL",
    )


def downgrade() -> None:
    raise RuntimeError("Snapshot verification seal cannot be downgraded; use a reviewed forward-fix")
