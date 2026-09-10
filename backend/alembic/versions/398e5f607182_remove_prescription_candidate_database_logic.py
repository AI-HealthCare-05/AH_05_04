"""Remove Prescription and Candidate triggers after validating stored graphs.

Revision ID: 398e5f607182
Revises: 398d4e5f6071
"""

import sqlalchemy as sa
from alembic import op

from provider_contracts.prescription_integrity import MEDICATION_CONTENT_FIELDS, verify_prescription_fingerprint

revision = "398e5f607182"
down_revision = "398d4e5f6071"
branch_labels = None
depends_on = None

# Frozen inventory; preserve ordinary FK/UNIQUE/CHECK constraints.
_TABLES = (
    "prescription",
    "prescription_version",
    "prescription_version_medication",
    "medication_candidate_search",
    "medication_candidate_search_result",
)
_TRIGGERS = (
    ("prescription", "trg_prescription_active_version_medication"),
    ("prescription_version", "trg_prescription_version_medication_required"),
    ("prescription_version", "trg_prescription_version_stamp_assembly_xid"),
    ("prescription_version_medication", "trg_prescription_version_medication_prevent_frozen_insert"),
    *(
        (table, f"trg_{table}_prevent_{operation}")
        for table in ("prescription_version", "prescription_version_medication")
        for operation in ("update", "delete")
    ),
    ("medication_candidate_search", "trg_candidate_search_displayed_count"),
    ("medication_candidate_search_result", "trg_candidate_search_result_displayed_count"),
)
_FUNCTIONS = (
    "prevent_prescription_version_mutation()",
    "stamp_prescription_version_assembly_xid()",
    "prevent_frozen_prescription_version_medication_insert()",
    "check_prescription_version_medications()",
    "check_prescription_active_version_medications()",
    "check_medication_candidate_search_displayed_count()",
)


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE " + ", ".join(f"public.{table}" for table in _TABLES) + " IN ACCESS EXCLUSIVE MODE")
    )
    _validate_existing_graphs(connection)
    # ACL revocation and trigger removal commit together. A failed later provisioning
    # leaves ordinary callers without Prescription/Candidate write access, never with unguarded DML.
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
    op.drop_column("prescription_version", "assembly_xid")
    remaining = connection.scalar(
        sa.text(
            "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=ANY(:tables) AND NOT t.tgisinternal"
        ),
        {"tables": list(_TABLES)},
    )
    if remaining:
        raise RuntimeError("Prescription/Candidate cutover refused: unexpected user-defined triggers remain")


def downgrade() -> None:
    raise RuntimeError(
        "Prescription/Candidate trigger removal cannot be downgraded; use a reviewed forward-fix or pre-cutover backup"
    )


def _validate_existing_graphs(connection) -> None:
    columns = ", ".join(MEDICATION_CONTENT_FIELDS)
    for version in connection.execute(
        sa.text("SELECT id,prescribed_date,medication_count,content_hash FROM prescription_version ORDER BY id")
    ).mappings():
        rows = (
            connection.execute(
                sa.text(
                    f"SELECT {columns},medication_count FROM prescription_version_medication WHERE prescription_version_id=:id"
                ),
                {"id": version["id"]},
            )
            .mappings()
            .all()
        )
        try:
            verify_prescription_fingerprint(
                version["prescribed_date"],
                [dict(row) for row in rows],
                medication_count=version["medication_count"],
                content_hash=version["content_hash"],
            )
        except ValueError:
            raise RuntimeError("Prescription trigger removal refused: invalid stored fingerprint") from None
    invalid = connection.scalar(
        sa.text(
            "SELECT count(*) FROM (SELECT s.id FROM medication_candidate_search s "
            "LEFT JOIN medication_candidate_search_result r ON r.search_id=s.id "
            "GROUP BY s.id HAVING s.candidate_count<>count(r.id) OR "
            "s.displayed_candidate_count<>count(r.id) FILTER (WHERE r.is_displayed)) invalid"
        )
    )
    if invalid:
        raise RuntimeError("Candidate trigger removal refused: invalid stored result counts")
