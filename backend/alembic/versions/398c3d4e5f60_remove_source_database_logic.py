"""Remove Source triggers after closing legacy non-owner write permissions.

Revision ID: 398c3d4e5f60
Revises: 398b2c3d4e5f
"""

import sqlalchemy as sa
from alembic import op

revision = "398c3d4e5f60"
down_revision = "398b2c3d4e5f"
branch_labels = None
depends_on = None

# Freeze this migration's inventory; do not import a mutable provisioning policy.
_TABLES = (
    "rag_source",
    "rag_source_endpoint",
    "rag_source_operation",
    "rag_source_snapshot",
    "rag_source_ingestion_run",
    "rag_source_ingestion_artifact",
    "rag_source_snapshot_verification",
)
_TRIGGERS = (
    ("rag_source_snapshot", "trg_rag_source_snapshot_prevent_update"),
    ("rag_source_snapshot", "trg_rag_source_snapshot_prevent_delete"),
    ("rag_source_snapshot", "trg_rag_snapshot_state_write"),
    ("rag_source_ingestion_artifact", "trg_rag_source_ingestion_artifact_prevent_update"),
    ("rag_source_ingestion_artifact", "trg_rag_source_ingestion_artifact_prevent_delete"),
    ("rag_source_snapshot_verification", "trg_rag_snapshot_verification_immutable"),
)
_FUNCTIONS = (
    "transition_rag_source_snapshot(text,text,text,timestamptz,timestamptz,text)",
    "guard_rag_snapshot_state_write()",
    "prevent_rag_source_snapshot_mutation()",
    "prevent_rag_source_ingestion_artifact_mutation()",
    "prevent_rag_snapshot_verification_mutation()",
)


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE " + ", ".join(f"public.{table}" for table in _TABLES) + " IN ACCESS EXCLUSIVE MODE")
    )
    # ACL revocation and trigger removal commit together. A failed later provisioning
    # leaves ordinary callers without Source write access, never with unguarded DML.
    for table in _TABLES:
        statements = list(
            connection.scalars(
                sa.text(
                    "SELECT DISTINCT format('REVOKE ALL ON TABLE %s FROM %s', c.oid::regclass, "
                    "CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE quote_ident(r.rolname) END) "
                    "FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) acl "
                    "LEFT JOIN pg_roles r ON r.oid=acl.grantee "
                    "WHERE c.oid=to_regclass(:target) AND acl.grantee<>c.relowner"
                ),
                {"target": f"public.{table}"},
            ).all()
        )
        statements += connection.scalars(
            sa.text(
                "SELECT DISTINCT format('REVOKE ALL PRIVILEGES (%I) ON TABLE %s FROM %s', "
                "a.attname, c.oid::regclass, "
                "CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE quote_ident(r.rolname) END) "
                "FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid "
                "CROSS JOIN LATERAL aclexplode(a.attacl) acl LEFT JOIN pg_roles r ON r.oid=acl.grantee "
                "WHERE c.oid=to_regclass(:target) AND a.attnum>0 AND NOT a.attisdropped "
                "AND acl.grantee<>c.relowner"
            ),
            {"target": f"public.{table}"},
        ).all()
        for statement in statements:
            connection.execute(sa.text(statement))
    for table, trigger in _TRIGGERS:
        connection.execute(sa.text(f"DROP TRIGGER {trigger} ON public.{table}"))
    for function in _FUNCTIONS:
        # No CASCADE: unexpected dependencies must roll back the entire cutover.
        connection.execute(sa.text(f"DROP FUNCTION public.{function}"))
    remaining = connection.scalar(
        sa.text(
            "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=ANY(:tables) AND NOT t.tgisinternal"
        ),
        {"tables": list(_TABLES)},
    )
    if remaining:
        raise RuntimeError("Source cutover refused: unexpected user-defined triggers remain")


def downgrade() -> None:
    raise RuntimeError("Source trigger removal cannot be downgraded; use a reviewed forward-fix or pre-cutover backup")
