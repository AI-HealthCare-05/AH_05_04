"""Source Writer 전용 권한 적용. 관리자 연결의 외부 transaction에서 실행합니다.

Runtime 프로세스에 관리자/Writer credential을 전달하지 않습니다.
이 모듈은 역할을 생성하거나 비밀번호를 취급하지 않습니다.
"""

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

SOURCE_TABLES = (
    "rag_source",
    "rag_source_endpoint",
    "rag_source_operation",
    "rag_source_snapshot",
    "rag_source_ingestion_run",
    "rag_source_ingestion_artifact",
    "rag_source_snapshot_verification",
)
# 승인 이력·원본은 append-only입니다. 관리용 수정/삭제는 별도 권한 경로로 연결합니다.
WRITER_UPDATE_TABLES = {"rag_source_operation", "rag_source_ingestion_run"}


def quoted_identifier(value: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value) is None:
        raise ValueError("Invalid database role or schema identifier")
    return f'"{value}"'


async def apply_source_role_policy(
    connection: AsyncConnection, *, schema: str, owner: str, runtime: str, writer: str
) -> None:
    """기존 역할의 안전성을 확인하고 Source 권한을 명시적으로 적용합니다."""
    names = [quoted_identifier(value) for value in (schema, owner, runtime, writer)]
    if len({owner, runtime, writer}) != 3:
        raise ValueError("Migration, Runtime and Writer roles must differ")
    schema_sql, owner_sql, runtime_sql, writer_sql = names
    await _validate_role_boundary(connection, schema=schema, runtime=runtime, writer=writer)
    global_defaults = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a "
            "WHERE d.defaclnamespace=0 AND d.defaclobjtype='r' "
            "AND d.defaclrole=(SELECT oid FROM pg_roles WHERE rolname=:owner) "
            "AND (a.grantee=0 OR a.grantee IN (SELECT oid FROM pg_roles WHERE rolname IN (:runtime, :writer)))"
        ),
        {"owner": owner, "runtime": runtime, "writer": writer},
    )
    if global_defaults:
        raise ValueError("Remove global table default grants before Source role cutover")
    tables = await connection.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname=:schema"), {"schema": schema}
    )
    present = set(tables.scalars())
    if not set(SOURCE_TABLES).issubset(present):
        raise ValueError("Apply Source migrations before provisioning Writer privileges")
    await connection.execute(text(f"REVOKE CREATE ON SCHEMA {schema_sql} FROM PUBLIC"))
    await connection.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema_sql} FROM {writer_sql}"))
    await _revoke_column_grants(connection, schema=schema, runtime=runtime, writer=writer)
    for role_sql in (runtime_sql, writer_sql):
        await connection.execute(text(f"REVOKE CREATE ON SCHEMA {schema_sql} FROM {role_sql}"))
        await connection.execute(text(f"GRANT USAGE ON SCHEMA {schema_sql} TO {role_sql}"))
        await connection.execute(
            text(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_sql} IN SCHEMA {schema_sql} "
                f"REVOKE ALL ON TABLES FROM {role_sql}"
            )
        )
    # PUBLIC 경유 권한도 차단하고 신규 테이블에 암묵적인 쓰기 권한을 남기지 않습니다.
    await connection.execute(
        text(f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_sql} IN SCHEMA {schema_sql} REVOKE ALL ON TABLES FROM PUBLIC")
    )
    for table in SOURCE_TABLES:
        target = f"{schema_sql}.{quoted_identifier(table)}"
        await connection.execute(text(f"REVOKE ALL ON TABLE {target} FROM PUBLIC, {runtime_sql}, {writer_sql}"))
        await connection.execute(text(f"GRANT SELECT ON TABLE {target} TO {runtime_sql}, {writer_sql}"))
        await connection.execute(text(f"GRANT INSERT ON TABLE {target} TO {writer_sql}"))
        if table == "rag_source_snapshot":
            await connection.execute(
                text(
                    f"GRANT UPDATE (verification_status, verified_at, effective_at, verification_seal_id) ON TABLE {target} TO {writer_sql}"
                )
            )
        if table in WRITER_UPDATE_TABLES:
            await connection.execute(text(f"GRANT UPDATE ON TABLE {target} TO {writer_sql}"))


async def _validate_role_boundary(connection: AsyncConnection, *, schema: str, runtime: str, writer: str) -> None:
    roles = await connection.execute(
        text(
            "SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication FROM pg_roles WHERE rolname IN (:runtime, :writer)"
        ),
        {"runtime": runtime, "writer": writer},
    )
    rows = roles.mappings().all()
    if len(rows) != 2 or any(
        any(row[key] for key in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolbypassrls", "rolreplication"))
        for row in rows
    ):
        raise ValueError("Runtime and Writer must exist without administrative privileges")
    memberships = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname IN (:runtime, :writer)"
        ),
        {"runtime": runtime, "writer": writer},
    )
    if memberships:
        raise ValueError("Runtime and Writer must not inherit or SET ROLE to another role")
    owned = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND r.rolname IN (:runtime, :writer)"
        ),
        {"schema": schema, "runtime": runtime, "writer": writer},
    )
    if owned:
        raise ValueError("Runtime and Writer must not own schema objects")
    schema_owned = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname=:schema AND r.rolname IN (:runtime, :writer)"
        ),
        {"schema": schema, "runtime": runtime, "writer": writer},
    )
    database_owned = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba WHERE d.datname=current_database() AND r.rolname IN (:runtime, :writer)"
        ),
        {"runtime": runtime, "writer": writer},
    )
    if schema_owned or database_owned:
        raise ValueError("Runtime and Writer must not own database or schema")
    legacy_function = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=:schema AND p.proname='transition_rag_source_snapshot'"
        ),
        {"schema": schema},
    )
    if legacy_function:
        raise ValueError("Remove the legacy Source transition function before role cutover")


async def _revoke_column_grants(connection: AsyncConnection, *, schema: str, runtime: str, writer: str) -> None:
    # Table REVOKE does not remove independently granted column privileges.
    # Writer loses column access across the schema; Source also closes PUBLIC/Runtime access.
    statements = await connection.scalars(
        text(
            "SELECT DISTINCT format('REVOKE ALL PRIVILEGES (%I) ON TABLE %I.%I FROM %s', "
            "a.attname, n.nspname, c.relname, "
            "CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE quote_ident(r.rolname) END) "
            "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "CROSS JOIN LATERAL aclexplode(a.attacl) acl "
            "LEFT JOIN pg_roles r ON r.oid=acl.grantee "
            "WHERE n.nspname=:schema AND a.attnum>0 AND NOT a.attisdropped "
            "AND (r.rolname=:writer OR "
            "(c.relname=ANY(:tables) AND (acl.grantee=0 OR r.rolname=:runtime)))"
        ),
        {"schema": schema, "runtime": runtime, "writer": writer, "tables": list(SOURCE_TABLES)},
    )
    for statement in statements:
        await connection.execute(text(statement))
