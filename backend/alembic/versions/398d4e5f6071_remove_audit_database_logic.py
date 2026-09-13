"""Remove audit immutability triggers after closing non-owner permissions.

Revision ID: 398d4e5f6071
Revises: 398c3d4e5f60
"""

import sqlalchemy as sa
from alembic import op

revision = "398d4e5f6071"
down_revision = "398c3d4e5f60"
branch_labels = None
depends_on = None

# Frozen inventory: audit history remains insert-only for application roles.
_TABLES = (
    "rag_runtime_environment_transition",
    "checkin_audit",
    "rag_citation",
    "rag_evidence_guideline",
    "rag_evidence_rule",
    "rag_evidence",
    "rag_evidence_knowledge",
)
_TRIGGERS = (
    ("rag_runtime_environment_transition", "trg_rag_runtime_transition_append_only"),
    ("checkin_audit", "trg_checkin_audit_append_only"),
    *((table, f"trg_{table}_append_only_{operation}") for table in _TABLES[2:] for operation in ("update", "delete")),
)
_FUNCTIONS = (
    "prevent_rag_runtime_transition_mutation()",
    "prevent_checkin_audit_mutation()",
    "prevent_rag_evidence_citation_mutation()",
)


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE " + ", ".join(f"public.{table}" for table in _TABLES) + " IN ACCESS EXCLUSIVE MODE")
    )
    # ACL revocation and trigger removal commit together. A failed later provisioning
    # leaves ordinary callers without audit write access, never with unguarded DML.
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
        raise RuntimeError("Audit cutover refused: unexpected user-defined triggers remain")


def downgrade() -> None:
    raise RuntimeError("Audit trigger removal cannot be downgraded; use a reviewed forward-fix or pre-cutover backup")
