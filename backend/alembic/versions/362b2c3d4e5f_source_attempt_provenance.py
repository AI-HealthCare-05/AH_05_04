"""Preserve Source attempt provenance separately from Snapshot identity (PD-362)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "362b2c3d4e5f"
down_revision = "362a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, type_ in (
        ("attempted_source_version", sa.String(200)),
        ("attempted_external_version", sa.String(200)),
        ("attempted_canonical_contract", JSONB()),
        ("invalid_source_version_sha256", sa.String(64)),
        ("invalid_source_version_byte_length", sa.Integer()),
        ("validation_reason_code", sa.String(100)),
    ):
        op.add_column("rag_source_ingestion_run", sa.Column(name, type_, nullable=True))
    op.create_check_constraint(
        "chk_rag_ingestion_invalid_version_audit",
        "rag_source_ingestion_run",
        "(invalid_source_version_sha256 IS NULL AND invalid_source_version_byte_length IS NULL) OR "
        "(invalid_source_version_sha256 IS NOT NULL AND invalid_source_version_sha256 ~ '^[0-9a-f]{64}$' "
        "AND invalid_source_version_byte_length IS NOT NULL AND invalid_source_version_byte_length >= 0 "
        "AND attempted_source_version IS NULL AND attempted_external_version IS NULL "
        "AND run_status = 'FAILED' AND validation_reason_code IS NOT NULL)",
    )
    op.create_index(
        "idx_rag_ingestion_attempt_version", "rag_source_ingestion_run", ["operation_id", "attempted_source_version"]
    )

    # Preserve existing lifecycle UPDATE access without granting provenance edits.
    connection = op.get_bind()
    grants = connection.execute(
        sa.text(
            "SELECT DISTINCT a.grantee, r.rolname FROM pg_class c "
            "CROSS JOIN LATERAL aclexplode(c.relacl) a LEFT JOIN pg_roles r ON r.oid=a.grantee "
            "WHERE c.oid='rag_source_ingestion_run'::regclass AND a.privilege_type='UPDATE' AND a.grantee<>c.relowner"
        )
    ).all()
    for grantee, name in grants:
        quoted = "PUBLIC" if grantee == 0 else connection.dialect.identifier_preparer.quote_identifier(name)
        connection.execute(sa.text(f"REVOKE UPDATE ON rag_source_ingestion_run FROM {quoted}"))
        if grantee != 0:
            connection.execute(
                sa.text(
                    "GRANT UPDATE (snapshot_id, run_status, failure_code, failure_message, duration_ms, finished_at) "
                    f"ON rag_source_ingestion_run TO {quoted}"
                )
            )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE rag_source_ingestion_run IN ACCESS EXCLUSIVE MODE"))
    columns = (
        "attempted_source_version",
        "attempted_external_version",
        "attempted_canonical_contract",
        "invalid_source_version_sha256",
        "invalid_source_version_byte_length",
        "validation_reason_code",
    )
    populated = " OR ".join(f"{name} IS NOT NULL" for name in columns)
    if connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM rag_source_ingestion_run WHERE {populated})")):
        raise RuntimeError("Source attempt provenance would be lost; downgrade refused")

    op.drop_index("idx_rag_ingestion_attempt_version", table_name="rag_source_ingestion_run")
    op.drop_constraint("chk_rag_ingestion_invalid_version_audit", "rag_source_ingestion_run", type_="check")
    for name in columns:
        op.drop_column("rag_source_ingestion_run", name)
    # Keep lifecycle-only UPDATE grants; do not restore broad table/PUBLIC privileges.
