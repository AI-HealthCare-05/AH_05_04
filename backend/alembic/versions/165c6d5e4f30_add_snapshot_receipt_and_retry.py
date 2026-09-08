"""add snapshot receipt provenance and failed retry boundary

Revision ID: 165c6d5e4f30
Revises: 165b5c4d3e2f
Create Date: 2026-09-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "165c6d5e4f30"
down_revision: str | Sequence[str] | None = "165b5c4d3e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASE_IMMUTABLE_CHECKS = """
                OLD.operation_id IS DISTINCT FROM NEW.operation_id OR
                OLD.source_version IS DISTINCT FROM NEW.source_version OR
                OLD.raw_manifest_checksum IS DISTINCT FROM NEW.raw_manifest_checksum OR
                OLD.canonical_checksum IS DISTINCT FROM NEW.canonical_checksum OR
                OLD.schema_version IS DISTINCT FROM NEW.schema_version OR
                OLD.parser_version IS DISTINCT FROM NEW.parser_version OR
                OLD.normalization_version IS DISTINCT FROM NEW.normalization_version OR
                OLD.canonicalization_spec_version IS DISTINCT FROM NEW.canonicalization_spec_version OR
                OLD.record_count IS DISTINCT FROM NEW.record_count OR
                OLD.rejected_record_count IS DISTINCT FROM NEW.rejected_record_count OR
                OLD.collected_at IS DISTINCT FROM NEW.collected_at OR
                OLD.supersedes_snapshot_id IS DISTINCT FROM NEW.supersedes_snapshot_id OR
                OLD.created_at IS DISTINCT FROM NEW.created_at
""".strip()


def _replace_snapshot_guard(*, include_endpoint_receipt_hash: bool) -> None:
    immutable_checks = _BASE_IMMUTABLE_CHECKS
    if include_endpoint_receipt_hash:
        immutable_checks += " OR\n                OLD.endpoint_receipt_hash IS DISTINCT FROM NEW.endpoint_receipt_hash"
    op.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION prevent_rag_source_snapshot_mutation()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'rag_source_snapshot rows are append-only; use a forward-fix snapshot instead';
                END IF;

                IF {immutable_checks} THEN
                    RAISE EXCEPTION 'rag_source_snapshot immutable fields cannot be updated; append verification rows or create a new snapshot';
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )


def upgrade() -> None:
    op.add_column(
        "rag_source_snapshot",
        sa.Column("endpoint_receipt_hash", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "chk_rag_source_snapshot_endpoint_receipt_hash",
        "rag_source_snapshot",
        "endpoint_receipt_hash IS NULL OR endpoint_receipt_hash ~ '^[0-9a-f]{64}$'",
    )
    op.drop_constraint(
        "uq_rag_source_snapshot_operation_version",
        "rag_source_snapshot",
        type_="unique",
    )
    op.create_index(
        "uq_rag_source_snapshot_active_version",
        "rag_source_snapshot",
        ["operation_id", "source_version"],
        unique=True,
        postgresql_where=sa.text("verification_status <> 'FAILED'"),
    )
    _replace_snapshot_guard(include_endpoint_receipt_hash=True)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE rag_source_snapshot IN ACCESS EXCLUSIVE MODE"))
    receipt_count = connection.execute(
        sa.text("SELECT count(*) FROM rag_source_snapshot WHERE endpoint_receipt_hash IS NOT NULL")
    ).scalar_one()
    duplicate_version_count = connection.execute(
        sa.text(
            """
            SELECT count(*)
            FROM (
                SELECT operation_id, source_version
                FROM rag_source_snapshot
                GROUP BY operation_id, source_version
                HAVING count(*) > 1
            ) AS duplicate_versions
            """
        )
    ).scalar_one()
    if receipt_count or duplicate_version_count:
        raise RuntimeError(
            "Cannot downgrade revision 165c6d5e4f30 while receipt provenance or retried Source snapshots exist. "
            "Use a forward-fix migration or an approved retention procedure instead."
        )

    op.drop_index("uq_rag_source_snapshot_active_version", table_name="rag_source_snapshot")
    op.create_unique_constraint(
        "uq_rag_source_snapshot_operation_version",
        "rag_source_snapshot",
        ["operation_id", "source_version"],
    )
    op.drop_constraint(
        "chk_rag_source_snapshot_endpoint_receipt_hash",
        "rag_source_snapshot",
        type_="check",
    )
    _replace_snapshot_guard(include_endpoint_receipt_hash=False)
    op.drop_column("rag_source_snapshot", "endpoint_receipt_hash")
