"""Protect immutable verification history and named publication approvals.

Revision ID: 165d7e6f5041
Revises: 165c6d5e4f30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "165d7e6f5041"
down_revision: str | Sequence[str] | None = "165c6d5e4f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing anonymous approvals must be reviewed, never silently rewritten.
    op.create_check_constraint(
        "chk_rag_snapshot_publication_approver",
        "rag_source_snapshot_verification",
        "check_name <> 'snapshot-publication-approval' OR verification_result <> 'PASSED' OR "
        "(verified_by IS NOT NULL AND length(trim(verified_by)) > 0)",
    )
    op.execute(
        sa.text("""
        CREATE FUNCTION prevent_rag_snapshot_verification_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'rag_source_snapshot_verification rows are append-only';
        END;
        $$ LANGUAGE plpgsql;
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER trg_rag_snapshot_verification_immutable
        BEFORE UPDATE OR DELETE ON rag_source_snapshot_verification
        FOR EACH ROW EXECUTE FUNCTION prevent_rag_snapshot_verification_mutation();
    """)
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE rag_source_snapshot_verification IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM rag_source_snapshot_verification)")).scalar_one():
        raise RuntimeError(
            "Cannot downgrade revision 165d7e6f5041 while verification history exists. Use a forward-fix migration."
        )
    op.execute(sa.text("DROP TRIGGER trg_rag_snapshot_verification_immutable ON rag_source_snapshot_verification"))
    op.execute(sa.text("DROP FUNCTION prevent_rag_snapshot_verification_mutation()"))
    op.drop_constraint("chk_rag_snapshot_publication_approver", "rag_source_snapshot_verification", type_="check")
