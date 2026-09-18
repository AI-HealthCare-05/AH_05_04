"""One-shot admin provisioning after migrations, before application startup."""

import asyncio
import os
import sys
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from infra.python.catalog_role_policy import CATALOG_WRITE_TABLES, apply_catalog_role_policy
from infra.python.knowledge_index_role_policy import (
    KNOWLEDGE_INDEX_RUNTIME_READ_TABLES,
    apply_knowledge_index_role_policy,
)
from infra.python.source_management_role_policy import apply_management_role_policy
from infra.python.source_role_policy import SOURCE_TABLES, apply_source_role_policy, quoted_identifier

# Explicit compatibility permissions for domains whose Writer cutover is still pending.
# New tables receive no access until their policy is reviewed and added here.
RUNTIME_MUTABLE_TABLES = frozenset(
    "user profile medical_document prescription medication guide guide_citation "
    "ai_job ai_job_attempt outbox_event idempotency_record message_quarantine dlq_outbox_event "
    "medication_candidate_search medication_identification "
    "ocr_job extracted_field chat_session chat_message chat_citation guide_feedback chat_message_feedback "
    "medication_schedule medication_schedule_time medication_occurrence medication_checkin "
    "push_subscription push_delivery "
    "eval_dataset eval_case eval_experiment eval_variant eval_run eval_case_result eval_metric eval_failure "
    "rag_runtime_execution_manifest rag_runtime_release_bundle rag_runtime_bundle_source "
    "rag_runtime_environment rag_release_evaluation_approval".split()
)
RUNTIME_APPEND_ONLY_TABLES = frozenset(
    "prescription_version prescription_version_medication checkin_audit medication_schedule_audit rag_citation "
    "rag_evidence_guideline rag_evidence_rule rag_evidence rag_evidence_knowledge "
    "rag_runtime_environment_transition medication_candidate_search_result "
    "ai_job_intake_context ai_job_execution_context ai_job_execution_identification "
    "retrieval_signal retrieval_hit "
    # #713/#731: REQUEST 단위 historical authority 증거. #713 writer의 발행과 #709 Production
    # Reader의 조회만 필요하므로 기존 append-only 권한(SELECT, INSERT)을 그대로 쓴다.
    "rag_request_guard_authority rag_request_source_decision rag_request_member_decision "
    # #712: selected hit 단위 Assessment·Eligibility authority. Issuer의 발급과 후속 Reader의
    # 조회만 필요하고 발급 뒤에는 고쳐 쓰지 않으므로 append-only 권한(SELECT, INSERT)을 그대로 쓴다.
    "rag_evidence_authority".split()
)
RUNTIME_CHECKIN_LOCK_TABLES = frozenset({"safety_assessment", "barrier_response"})

# Track C 안전 확인·장벽 응답·실천 계획은 revision 단위 append 뒤 고쳐 쓰지 않습니다.
# checkin_lock_marker 컬럼 UPDATE는 #668의 row lock 용도이므로 유지하고 INSERT만 더합니다.
# 삭제는 #748 탈퇴 cleanup 역할의 책임이므로 Runtime에 DELETE/TRUNCATE를 주지 않습니다.
RUNTIME_TRACK_C_APPEND_TABLES = frozenset({"safety_assessment", "barrier_response", "support_action_plan"})

# Follow-up은 현재 응답 1건을 갱신하고 정정 이력을 audit에 append합니다.
# 갱신 대상 컬럼만 열어 상태·소유권 컬럼의 직접 변경을 막습니다.
RUNTIME_TRACK_C_FOLLOWUP_TABLES = frozenset({"action_plan_followup", "action_plan_followup_audit"})
RUNTIME_TRACK_C_FOLLOWUP_UPDATE_COLUMNS = ("response", "revision", "updated_at")

RUNTIME_LIFESTYLE_TABLES = frozenset({"lifestyle_times"})

# #178/#689: retrieval_run tracks execution lifecycle (RUNNING -> COMPLETED/FAILED),
# requiring SELECT, INSERT, UPDATE. #748 withdrawal cleanup grants DELETE separately.
RUNTIME_RETRIEVAL_RUN_TABLES = frozenset({"retrieval_run"})

# #780: Candidate Index read authority for runtime search/hydration.
# Runtime requires SELECT on version and member, plus UPDATE on candidate_index_lock_marker for SELECT ... FOR SHARE.
# INSERT/DELETE/TRUNCATE and business column UPDATE are strictly prohibited.
CANDIDATE_INDEX_RUNTIME_READ_TABLES = frozenset({"rag_candidate_index_version", "rag_candidate_index_member"})

# #748/#206: demo withdrawal deletes user-owned runtime data from otherwise
# restricted lifecycle/history tables. Grant only SELECT for scoped predicates
# and DELETE for cleanup; keep INSERT/UPDATE policy in each domain section.
ACCOUNT_WITHDRAWAL_CLEANUP_DELETE_TABLES = frozenset(
    {
        "action_plan_followup",
        "action_plan_followup_audit",
        "ai_job",
        "ai_job_attempt",
        "barrier_response",
        "chat_citation",
        "chat_message",
        "chat_message_feedback",
        "chat_session",
        "dlq_outbox_event",
        "extracted_field",
        "guide",
        "guide_citation",
        "guide_feedback",
        "idempotency_record",
        "medical_document",
        "medication",
        "medication_candidate_search",
        "medication_candidate_search_result",
        "medication_checkin",
        "medication_identification",
        "medication_occurrence",
        "medication_schedule",
        "medication_schedule_time",
        "message_quarantine",
        "notification_record",
        "ocr_job",
        "outbox_event",
        "password_reset_token",
        "prescription",
        "push_delivery",
        "push_subscription",
        "refresh_session",
        "retrieval_run",
        "safety_assessment",
        "support_action_plan",
        "user_consent",
    }
)

ACCOUNT_WITHDRAWAL_RUNTIME_PROTECTED_DELETE_TABLES = frozenset(
    {
        "action_plan_followup",
        "action_plan_followup_audit",
        "barrier_response",
        "medication_candidate_search_result",
        "notification_record",
        "password_reset_token",
        "refresh_session",
        "retrieval_run",
        "safety_assessment",
        "support_action_plan",
        "user_consent",
    }
)

ACCOUNT_WITHDRAWAL_CLEANUP_READ_TABLES = ACCOUNT_WITHDRAWAL_CLEANUP_DELETE_TABLES | frozenset(
    {
        "account_deletion_request",
        "prescription_version",
        "prescription_version_medication",
        "profile",
        "user",
    }
)

ACCOUNT_WITHDRAWAL_CLEANUP_INSERT_TABLES = frozenset({"account_deletion_request"})

ACCOUNT_WITHDRAWAL_CLEANUP_UPDATE_COLUMNS = {
    "account_deletion_request": (
        "status",
        "started_at",
        "completed_at",
        "failed_at",
        "retry_count",
        "last_error_code",
        "updated_at",
    ),
    "ai_job": ("expected_event_id", "last_consumed_event_id"),
    "profile": ("display_name", "updated_at"),
    "user": (
        "email",
        "hashed_password",
        "name",
        "phone_number",
        "gender",
        "birthday",
        "is_active",
        "account_status",
        "withdrawal_requested_at",
        "withdrawn_at",
        "token_version",
        "updated_at",
    ),
}


# #404: token identity and history are immutable after issuance. Runtime only rotates/consumes.
RUNTIME_AUTH_UPDATE_COLUMNS = {
    "refresh_session": ("active_jti", "updated_at"),
    "password_reset_token": ("used_at",),
    "email_verification_token": ("verified_at",),
}

# #748/#206: account withdrawal is inserted once, then Runtime advances only the
# deletion request lifecycle. Identity and request provenance remain immutable.
RUNTIME_ACCOUNT_DELETION_REQUEST_UPDATE_COLUMNS = (
    "status",
    "started_at",
    "completed_at",
    "failed_at",
    "retry_count",
    "last_error_code",
    "updated_at",
)


def validate_distinct_role_names(*names: str | None) -> None:
    """Reject credential sharing across configured database responsibility boundaries."""
    configured = [name for name in names if name]
    if len(configured) != len(set(configured)):
        raise ValueError("Database roles must be distinct")


def _configured_role_names(*names: str | None) -> list[str]:
    return [name for name in names if name]


def _revoke_recipients(runtime_sql: str, writer_sql: str, cleanup_sql: str | None) -> str:
    recipients = ["PUBLIC", runtime_sql, writer_sql, *([cleanup_sql] if cleanup_sql else [])]
    return ", ".join(recipients)


async def provision_roles(
    connection: AsyncConnection,
    *,
    owner: str,
    runtime: str,
    writer: str,
    management: str | None = None,
    catalog_writer: str | None = None,
    knowledge_index_builder: str | None = None,
    account_withdrawal_cleanup: str | None = None,
) -> None:
    """Caller must use a single admin transaction; failure must roll it back."""
    validate_distinct_role_names(
        owner,
        runtime,
        writer,
        management,
        catalog_writer,
        knowledge_index_builder,
        account_withdrawal_cleanup,
    )
    owner_sql, runtime_sql, writer_sql = (quoted_identifier(value) for value in (owner, runtime, writer))
    cleanup_sql = quoted_identifier(account_withdrawal_cleanup) if account_withdrawal_cleanup else None
    # Validates real role boundaries and rejects the legacy transition function before granting anything.
    await apply_source_role_policy(connection, schema="public", owner=owner, runtime=runtime, writer=writer)
    recipients = _revoke_recipients(runtime_sql, writer_sql, cleanup_sql)
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
            "AND (acl.grantee=0 OR r.rolname = ANY(:roles))"
        ),
        {"roles": _configured_role_names(runtime, writer, account_withdrawal_cleanup)},
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
    required = (
        RUNTIME_MUTABLE_TABLES
        | RUNTIME_APPEND_ONLY_TABLES
        | CATALOG_WRITE_TABLES
        | KNOWLEDGE_INDEX_RUNTIME_READ_TABLES
        | CANDIDATE_INDEX_RUNTIME_READ_TABLES
        | set(SOURCE_TABLES)
        | set(RUNTIME_AUTH_UPDATE_COLUMNS)
        | {"account_deletion_request"}
        | ACCOUNT_WITHDRAWAL_CLEANUP_DELETE_TABLES
        | RUNTIME_LIFESTYLE_TABLES
        | RUNTIME_CHECKIN_LOCK_TABLES
        | RUNTIME_TRACK_C_APPEND_TABLES
        | RUNTIME_TRACK_C_FOLLOWUP_TABLES
        | {"support_action_plan"}
        | RUNTIME_RETRIEVAL_RUN_TABLES
        | {"notification_record", "user_consent"}
    )
    if not required.issubset(present):
        raise ValueError("Required application tables are missing; apply migrations before provisioning")
    for tables, privileges in (
        (RUNTIME_MUTABLE_TABLES, "SELECT, INSERT, UPDATE, DELETE"),
        (RUNTIME_APPEND_ONLY_TABLES, "SELECT, INSERT"),
        (
            CATALOG_WRITE_TABLES | KNOWLEDGE_INDEX_RUNTIME_READ_TABLES | CANDIDATE_INDEX_RUNTIME_READ_TABLES,
            "SELECT",
        ),
    ):
        for table in sorted(tables):
            await connection.execute(
                text(f"GRANT {privileges} ON TABLE public.{quoted_identifier(table)} TO {runtime_sql}")
            )
    # #668: Check-in correction locks historical Safety/Barrier rows without editing them.
    for table in sorted(RUNTIME_CHECKIN_LOCK_TABLES):
        target = f"public.{quoted_identifier(table)}"
        await connection.execute(text(f"GRANT SELECT ON TABLE {target} TO {runtime_sql}"))
        await connection.execute(text(f"GRANT UPDATE (checkin_lock_marker) ON TABLE {target} TO {runtime_sql}"))
    await _grant_track_c_runtime_permissions(connection, runtime_sql)
    # #780: Candidate Index Version row lock without payload mutation.
    await connection.execute(
        text(f"GRANT UPDATE (candidate_index_lock_marker) ON TABLE public.rag_candidate_index_version TO {runtime_sql}")
    )
    await connection.execute(text(f"GRANT SELECT ON TABLE public.support_action_plan TO {runtime_sql}"))
    await connection.execute(
        text(f"GRANT UPDATE (status, cancelled_at) ON TABLE public.support_action_plan TO {runtime_sql}")
    )
    # #434: notification creation/publication/read require DML, never history deletion.
    await connection.execute(text(f"GRANT SELECT, INSERT, UPDATE ON TABLE public.notification_record TO {runtime_sql}"))
    # #556: reset keeps the current row and replaces days with [], so Runtime never deletes lifestyle data directly.
    await connection.execute(text(f"GRANT SELECT, INSERT, UPDATE ON TABLE public.lifestyle_times TO {runtime_sql}"))
    # #207/#621: set_status() only upserts the per-purpose current row (ON CONFLICT DO UPDATE),
    # never deletes it, so Runtime needs SELECT/INSERT/UPDATE and nothing more.
    await connection.execute(text(f"GRANT SELECT, INSERT, UPDATE ON TABLE public.user_consent TO {runtime_sql}"))
    # #178/#689: retrieval_run tracks execution lifecycle (RUNNING -> COMPLETED/FAILED),
    # requiring SELECT, INSERT, UPDATE. #748 withdrawal cleanup grants DELETE separately.
    await connection.execute(text(f"GRANT SELECT, INSERT, UPDATE ON TABLE public.retrieval_run TO {runtime_sql}"))
    names = ", ".join(quoted_identifier(column) for column in RUNTIME_ACCOUNT_DELETION_REQUEST_UPDATE_COLUMNS)
    await connection.execute(text(f"GRANT SELECT, INSERT ON TABLE public.account_deletion_request TO {runtime_sql}"))
    await connection.execute(text(f"GRANT UPDATE ({names}) ON TABLE public.account_deletion_request TO {runtime_sql}"))
    for table, columns in RUNTIME_AUTH_UPDATE_COLUMNS.items():
        target = f"public.{quoted_identifier(table)}"
        update_columns = ", ".join(quoted_identifier(column) for column in columns)
        await connection.execute(text(f"GRANT SELECT, INSERT ON TABLE {target} TO {runtime_sql}"))
        await connection.execute(text(f"GRANT UPDATE ({update_columns}) ON TABLE {target} TO {runtime_sql}"))
    await _grant_account_withdrawal_cleanup_permissions(connection, cleanup_sql)
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
    if management:
        await apply_management_role_policy(
            connection, owner=owner, runtime=runtime, writer=writer, management=management
        )

    await _apply_optional_role_policies(
        connection,
        owner=owner,
        runtime=runtime,
        source_writer=writer,
        management=management,
        catalog_writer=catalog_writer,
        knowledge_index_builder=knowledge_index_builder,
    )


async def _apply_optional_role_policies(
    connection: AsyncConnection,
    *,
    owner: str,
    runtime: str,
    source_writer: str,
    management: str | None,
    catalog_writer: str | None,
    knowledge_index_builder: str | None,
) -> None:
    if catalog_writer:
        await apply_catalog_role_policy(
            connection,
            owner=owner,
            runtime=runtime,
            writer=catalog_writer,
            source_writer=source_writer,
            management=management,
        )
    if knowledge_index_builder:
        await apply_knowledge_index_role_policy(
            connection,
            owner=owner,
            runtime=runtime,
            builder=knowledge_index_builder,
        )


async def _grant_track_c_runtime_permissions(connection: AsyncConnection, runtime_sql: str) -> None:
    """Track C 쓰기 경로. #668 lock marker 권한과 별개로 새 revision row 생성이 필요합니다."""
    for table in sorted(RUNTIME_TRACK_C_APPEND_TABLES):
        await connection.execute(text(f"GRANT INSERT ON TABLE public.{quoted_identifier(table)} TO {runtime_sql}"))
    # Follow-up 현재 응답 upsert와 정정 이력 append. 갱신 컬럼만 명시적으로 엽니다.
    columns = ", ".join(quoted_identifier(column) for column in RUNTIME_TRACK_C_FOLLOWUP_UPDATE_COLUMNS)
    for table in sorted(RUNTIME_TRACK_C_FOLLOWUP_TABLES):
        await connection.execute(
            text(f"GRANT SELECT, INSERT ON TABLE public.{quoted_identifier(table)} TO {runtime_sql}")
        )
    await connection.execute(text(f"GRANT UPDATE ({columns}) ON TABLE public.action_plan_followup TO {runtime_sql}"))


async def _grant_account_withdrawal_cleanup_permissions(
    connection: AsyncConnection,
    cleanup_sql: str | None,
) -> None:
    if cleanup_sql is None:
        return
    for table in sorted(ACCOUNT_WITHDRAWAL_CLEANUP_READ_TABLES - ACCOUNT_WITHDRAWAL_CLEANUP_DELETE_TABLES):
        await connection.execute(text(f"GRANT SELECT ON TABLE public.{quoted_identifier(table)} TO {cleanup_sql}"))
    for table in sorted(ACCOUNT_WITHDRAWAL_CLEANUP_DELETE_TABLES):
        await connection.execute(
            text(f"GRANT SELECT, DELETE ON TABLE public.{quoted_identifier(table)} TO {cleanup_sql}")
        )
    for table in sorted(ACCOUNT_WITHDRAWAL_CLEANUP_INSERT_TABLES):
        await connection.execute(text(f"GRANT INSERT ON TABLE public.{quoted_identifier(table)} TO {cleanup_sql}"))
    for table, names in ACCOUNT_WITHDRAWAL_CLEANUP_UPDATE_COLUMNS.items():
        columns = ", ".join(quoted_identifier(name) for name in names)
        await connection.execute(
            text(f"GRANT UPDATE ({columns}) ON TABLE public.{quoted_identifier(table)} TO {cleanup_sql}")
        )


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
    validate_distinct_role_names(
        environment["DB_ADMIN_USER"],
        environment["DB_MIGRATION_USER"],
        environment["DB_APP_USER"],
        environment["SOURCE_WRITER_USER"],
        environment.get("SOURCE_MANAGEMENT_USER") or None,
        environment.get("CATALOG_WRITER_USER") or None,
        environment.get("KNOWLEDGE_INDEX_BUILDER_USER") or None,
        environment.get("ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE") or None,
    )
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
                management=environment.get("SOURCE_MANAGEMENT_USER") or None,
                catalog_writer=environment.get("CATALOG_WRITER_USER") or None,
                knowledge_index_builder=environment.get("KNOWLEDGE_INDEX_BUILDER_USER") or None,
                account_withdrawal_cleanup=environment.get("ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE") or None,
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
