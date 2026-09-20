"""Catalog 승인 발급·철회 전용 최소권한 role (#526 Phase 2).

Catalog Writer role은 건드리지 않습니다. 이 role은 승인 표만 쓰고 Catalog·Candidate·Knowledge·
Runtime·Source 적재 표에는 쓰기 권한을 갖지 않습니다. audit은 append-only이고, 승인 payload는
발급 후 revoke 컬럼만 갱신할 수 있습니다.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from infra.python.source_role_policy import _revoke_column_grants, _validate_role_boundary, quoted_identifier

APPROVAL_AUDIT_TABLE = "catalog_approval_audit"
APPROVAL_PERMISSION_TABLE = "catalog_approval_permission"
#: 발급한 승인 자체는 지우지 않습니다. INSERT와 revoke 컬럼 UPDATE만 필요합니다.
APPROVAL_PAYLOAD_TABLES = frozenset(
    {
        "catalog_source_approval",
        "catalog_build_approval",
        "catalog_build_approval_source",
    }
)
APPROVAL_INSERT_TABLES = APPROVAL_PAYLOAD_TABLES | {APPROVAL_AUDIT_TABLE}
#: Permission current state는 payload/audit append set과 분리해 bootstrap INSERT 범위를 명시합니다.
APPROVAL_STATE_INSERT_TABLES = frozenset({APPROVAL_PERMISSION_TABLE})
#: 권한 현재 상태의 최초 bootstrap에 필요한 정확한 INSERT/UPDATE 컬럼입니다.
APPROVAL_PERMISSION_INSERT_COLUMNS = ("user_id", "enabled", "evidence_ref", "revision", "updated_at")
APPROVAL_PERMISSION_UPDATE_COLUMNS = ("enabled", "evidence_ref", "revision", "updated_at")
#: 철회는 이 세 컬럼만 바꿉니다. 기간·대상·승인 payload는 발급 후 불변입니다.
APPROVAL_REVOKE_COLUMNS = ("revoked_at", "revoked_by", "revoked_reason")
REVOCABLE_TABLES = frozenset({"catalog_source_approval", "catalog_build_approval"})
#: 발급 전 Product Source authority를 정확히 재검증하는 데 필요한 읽기 범위.
APPROVAL_SOURCE_READ_TABLES = frozenset(
    "rag_source rag_source_endpoint rag_source_operation rag_source_snapshot "
    "rag_source_snapshot_verification rag_source_ingestion_run rag_source_ingestion_artifact".split()
)
APPROVAL_READ_TABLES = APPROVAL_PAYLOAD_TABLES | {APPROVAL_AUDIT_TABLE, APPROVAL_PERMISSION_TABLE}
#: 운영자 식별에 필요한 최소 컬럼만 봅니다. 자격 증명·PII 컬럼은 읽지 않습니다.
APPROVAL_USER_COLUMNS = ("id", "is_active", "account_status")


async def apply_catalog_approval_role_policy(
    connection: AsyncConnection,
    *,
    owner: str,
    runtime: str,
    approval: str,
    catalog_writer: str | None = None,
) -> None:
    roles = [owner, runtime, approval] + ([catalog_writer] if catalog_writer else [])
    if len(set(roles)) != len(roles):
        raise ValueError("Catalog approval issuance requires a separate database role")
    await _validate_role_boundary(connection, schema="public", runtime=runtime, writer=approval)
    role_sql, owner_sql = quoted_identifier(approval), quoted_identifier(owner)
    present = set(await connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
    if not (APPROVAL_READ_TABLES | APPROVAL_SOURCE_READ_TABLES) <= present:
        raise ValueError("Apply Catalog approval migrations before approval role provisioning")

    await connection.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role_sql}"))
    await connection.execute(text(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role_sql}"))
    await _revoke_column_grants(connection, schema="public", runtime=runtime, writer=approval)
    await connection.execute(text(f"REVOKE CREATE ON SCHEMA public FROM {role_sql}"))
    await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role_sql}"))
    for object_type in ("TABLES", "SEQUENCES"):
        for scope in ("", "IN SCHEMA public"):
            await connection.execute(
                text(
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_sql} {scope} REVOKE ALL ON {object_type} FROM {role_sql}"
                )
            )

    for table in sorted(APPROVAL_READ_TABLES | APPROVAL_SOURCE_READ_TABLES):
        await connection.execute(text(f"GRANT SELECT ON TABLE public.{quoted_identifier(table)} TO {role_sql}"))
    for table in sorted(APPROVAL_INSERT_TABLES):
        await connection.execute(text(f"GRANT INSERT ON TABLE public.{quoted_identifier(table)} TO {role_sql}"))
    # 승인 payload는 철회 컬럼만 바꿉니다. 기간 연장·대상 변경은 새 발급으로만 합니다.
    revoke_columns = ", ".join(quoted_identifier(name) for name in APPROVAL_REVOKE_COLUMNS)
    for table in sorted(REVOCABLE_TABLES):
        await connection.execute(
            text(f"GRANT UPDATE ({revoke_columns}) ON TABLE public.{quoted_identifier(table)} TO {role_sql}")
        )
    # 권한 현재 상태는 이 role이 직접 관리합니다. audit이 이력을 보존합니다.
    permission_sql = quoted_identifier(APPROVAL_PERMISSION_TABLE)
    permission_insert_columns = ", ".join(quoted_identifier(name) for name in APPROVAL_PERMISSION_INSERT_COLUMNS)
    permission_update_columns = ", ".join(quoted_identifier(name) for name in APPROVAL_PERMISSION_UPDATE_COLUMNS)
    await connection.execute(
        text(f"GRANT INSERT ({permission_insert_columns}) ON TABLE public.{permission_sql} TO {role_sql}")
    )
    await connection.execute(
        text(f"GRANT UPDATE ({permission_update_columns}) ON TABLE public.{permission_sql} TO {role_sql}")
    )
    user_columns = ", ".join(quoted_identifier(name) for name in APPROVAL_USER_COLUMNS)
    await connection.execute(text(f'GRANT SELECT ({user_columns}) ON public."user" TO {role_sql}'))


async def validate_catalog_approval_connection(connection: AsyncConnection) -> None:
    """실행 시점에 role이 승인 범위를 넘지 않는지 확인합니다. 통과 실패는 fail-closed입니다."""
    forbidden = await connection.scalar(
        text(
            "SELECT has_table_privilege(current_user,'catalog_approval_audit',"
            "'UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') "
            "OR has_table_privilege(current_user,'catalog_source_approval',"
            "'UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') "
            "OR has_table_privilege(current_user,'catalog_build_approval',"
            "'UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') "
            "OR has_table_privilege(current_user,'catalog_build_approval_source',"
            "'UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') "
            "OR has_table_privilege(current_user,'catalog_approval_permission',"
            "'UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') "
            "OR has_column_privilege(current_user,'user','hashed_password','SELECT') "
            "OR has_schema_privilege(current_user,'public','CREATE')"
        )
    )
    required = await connection.scalar(
        text(
            "SELECT has_table_privilege(current_user,'catalog_approval_audit','SELECT,INSERT') "
            "AND has_table_privilege(current_user,'catalog_approval_permission','SELECT') "
            "AND has_column_privilege(current_user,'catalog_approval_permission','user_id','INSERT') "
            "AND has_column_privilege(current_user,'catalog_approval_permission','enabled','INSERT') "
            "AND has_column_privilege(current_user,'catalog_approval_permission','evidence_ref','INSERT') "
            "AND has_column_privilege(current_user,'catalog_approval_permission','revision','INSERT') "
            "AND has_column_privilege(current_user,'catalog_approval_permission','updated_at','INSERT') "
            "AND has_table_privilege(current_user,'catalog_source_approval','SELECT,INSERT') "
            "AND has_table_privilege(current_user,'catalog_build_approval','SELECT,INSERT') "
            "AND has_table_privilege(current_user,'catalog_build_approval_source','SELECT,INSERT') "
            "AND has_column_privilege(current_user,'catalog_approval_permission','enabled','UPDATE') "
            "AND has_column_privilege(current_user,'catalog_approval_permission','evidence_ref','UPDATE') "
            "AND has_column_privilege(current_user,'catalog_approval_permission','revision','UPDATE') "
            "AND has_column_privilege(current_user,'catalog_approval_permission','updated_at','UPDATE') "
            "AND has_column_privilege(current_user,'catalog_source_approval','revoked_at','UPDATE') "
            "AND has_column_privilege(current_user,'catalog_source_approval','revoked_by','UPDATE') "
            "AND has_column_privilege(current_user,'catalog_source_approval','revoked_reason','UPDATE') "
            "AND has_column_privilege(current_user,'catalog_build_approval','revoked_at','UPDATE') "
            "AND has_column_privilege(current_user,'catalog_build_approval','revoked_by','UPDATE') "
            "AND has_column_privilege(current_user,'catalog_build_approval','revoked_reason','UPDATE')"
        )
    )
    if forbidden is not False or required is not True:
        raise ValueError("Catalog approval role policy is missing or grants forbidden permissions")
    if await connection.scalar(
        text("SELECT has_column_privilege(current_user,'catalog_approval_permission','user_id','UPDATE')")
    ):
        raise ValueError("Catalog approval role policy grants immutable permission identity updates")
    # 승인 role이 Catalog 업무 표를 쓰면 승인과 적재의 분리가 깨집니다.
    business = await connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
    for table in business:
        if table in APPROVAL_INSERT_TABLES or table == APPROVAL_PERMISSION_TABLE:
            continue
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "TRIGGER", "REFERENCES"):
            if await connection.scalar(
                text("SELECT has_table_privilege(current_user, :table, :privilege)"),
                {"table": f'public."{table}"', "privilege": privilege},
            ):
                raise ValueError("Catalog approval role has unexpected table privileges")
