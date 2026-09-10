"""One-shot admin provisioning after migrations, before application startup."""

import asyncio
import os
import sys
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from infra.python.source_role_policy import SOURCE_TABLES, apply_source_role_policy, quoted_identifier

# Explicit compatibility permissions for domains whose Writer cutover is still pending.
# New tables receive no access until their policy is reviewed and added here.
RUNTIME_MUTABLE_TABLES = frozenset(
    "user profile medical_document prescription medication guide guide_citation "
    "ai_job ai_job_attempt outbox_event idempotency_record message_quarantine dlq_outbox_event "
    "medication_candidate_search medication_candidate_search_result medication_identification "
    "ocr_job extracted_field chat_session chat_message chat_citation "
    "medication_schedule medication_schedule_time medication_occurrence medication_checkin "
    "knowledge_document knowledge_chunk "
    "rag_medication_product rag_medication_ingredient rag_medication_alias rag_medication_product_component "
    "eval_dataset eval_case eval_experiment eval_variant eval_run eval_case_result eval_metric eval_failure "
    "rag_runtime_execution_manifest rag_runtime_release_bundle rag_runtime_bundle_source "
    "rag_runtime_environment rag_runtime_environment_transition rag_release_evaluation_approval".split()
)
RUNTIME_APPEND_ONLY_TABLES = frozenset(
    "prescription_version prescription_version_medication checkin_audit rag_citation "
    "rag_evidence_guideline rag_evidence_rule rag_evidence rag_evidence_knowledge".split()
)


async def provision_roles(connection: AsyncConnection, *, owner: str, runtime: str, writer: str) -> None:
    """Caller must use a single admin transaction; failure must roll it back."""
    owner_sql, runtime_sql, writer_sql = (quoted_identifier(value) for value in (owner, runtime, writer))
    # Validates real role boundaries and rejects the legacy transition function before granting anything.
    await apply_source_role_policy(connection, schema="public", owner=owner, runtime=runtime, writer=writer)
    recipients = f"PUBLIC, {runtime_sql}, {writer_sql}"
    await connection.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {recipients}"))
    await connection.execute(text(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {recipients}"))
    statements = await connection.scalars(
        text(
            "SELECT DISTINCT format('REVOKE ALL PRIVILEGES (%I) ON TABLE %I.%I FROM %s', "
            "a.attname, n.nspname, c.relname, "
            "CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE quote_ident(r.rolname) END) "
            "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "CROSS JOIN LATERAL aclexplode(a.attacl) acl LEFT JOIN pg_roles r ON r.oid=acl.grantee "
            "WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped "
            "AND (acl.grantee=0 OR r.rolname IN (:runtime, :writer))"
        ),
        {"runtime": runtime, "writer": writer},
    )
    for statement in statements:
        await connection.execute(text(statement))
    for object_type in ("TABLES", "SEQUENCES"):
        await connection.execute(
            text(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_sql} IN SCHEMA public REVOKE ALL ON {object_type} FROM {recipients}"
            )
        )
    present = set(await connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
    required = RUNTIME_MUTABLE_TABLES | RUNTIME_APPEND_ONLY_TABLES | set(SOURCE_TABLES)
    if not required.issubset(present):
        raise ValueError("Required application tables are missing; apply migrations before provisioning")
    for tables, privileges in (
        (RUNTIME_MUTABLE_TABLES, "SELECT, INSERT, UPDATE, DELETE"),
        (RUNTIME_APPEND_ONLY_TABLES, "SELECT, INSERT"),
    ):
        for table in sorted(tables):
            await connection.execute(
                text(f"GRANT {privileges} ON TABLE public.{quoted_identifier(table)} TO {runtime_sql}")
            )
    # Only sequences owned by explicitly supported Runtime columns are available.
    sequences = await connection.scalars(
        text(
            "SELECT DISTINCT format('%I.%I', n.nspname, s.relname) "
            "FROM pg_class s JOIN pg_namespace n ON n.oid=s.relnamespace "
            "JOIN pg_depend d ON d.classid='pg_class'::regclass AND d.objid=s.oid "
            "JOIN pg_class t ON d.refclassid='pg_class'::regclass AND t.oid=d.refobjid "
            "WHERE n.nspname='public' AND s.relkind='S' AND d.deptype IN ('a','i') "
            "AND t.relname=ANY(:tables) AND t.relnamespace=n.oid"
        ),
        {"tables": sorted(RUNTIME_MUTABLE_TABLES | RUNTIME_APPEND_ONLY_TABLES)},
    )
    for sequence in sequences:
        await connection.execute(text(f"GRANT USAGE, SELECT ON SEQUENCE {sequence} TO {runtime_sql}"))
    await apply_source_role_policy(connection, schema="public", owner=owner, runtime=runtime, writer=writer)


async def run_provisioning(environment: Mapping[str, str]) -> None:
    names = (
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_ADMIN_USER",
        "DB_ADMIN_PASSWORD",
        "DB_MIGRATION_USER",
        "DB_APP_USER",
        "SOURCE_WRITER_USER",
    )
    if any(not environment.get(name, "").strip() for name in names):
        raise ValueError("Missing database provisioning configuration")
    if (
        len({environment[name] for name in ("DB_ADMIN_USER", "DB_MIGRATION_USER", "DB_APP_USER", "SOURCE_WRITER_USER")})
        != 4
    ):
        raise ValueError("Database roles must be distinct")
    engine = create_async_engine(
        URL.create(
            "postgresql+asyncpg",
            username=environment["DB_ADMIN_USER"],
            password=environment["DB_ADMIN_PASSWORD"],
            host=environment["DB_HOST"],
            port=int(environment["DB_PORT"]),
            database=environment["DB_NAME"],
        ),
        hide_parameters=True,
    )
    try:
        async with engine.begin() as connection:
            await provision_roles(
                connection,
                owner=environment["DB_MIGRATION_USER"],
                runtime=environment["DB_APP_USER"],
                writer=environment["SOURCE_WRITER_USER"],
            )
    finally:
        await engine.dispose()


def main() -> int:
    try:
        asyncio.run(run_provisioning(os.environ))
    except Exception:
        print(
            "Database role provisioning failed; transaction rolled back. Keep application services stopped.",
            file=sys.stderr,
        )
        return 1
    print("Database role provisioning committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
