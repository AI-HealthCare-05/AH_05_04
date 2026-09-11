"""Least-privilege grants for protected data and control planes."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_SELECT_COLUMNS: Mapping[str, Mapping[str, Sequence[str]]] = {
    "data": {
        "protected_identity": (
            "database_login",
            "actor_id",
            "actor_namespace",
            "principal_role",
            "identity_plane",
            "approval_role",
            "enabled",
        ),
        "protected_dataset": (
            "dataset_id",
            "dataset_version",
            "binding",
            "manifest_sha256",
            "protected_artifact_sha256",
            "hmac_key_version",
            "state",
            "state_revision",
            "authored_count",
            "review_complete",
            "lock_marker",
        ),
        "authorization_grant": (
            "grant_id",
            "revision",
            "effective_revision",
            "grant_body",
            "subject_actor_id",
            "subject_namespace",
            "subject_role",
            "dataset_id",
            "dataset_version",
            "manifest_sha256",
            "protected_artifact_sha256",
            "hmac_key_version",
            "actions",
            "valid_from",
            "expires_at",
            "revoked_at",
            "lock_marker",
        ),
        "operation_capability": (
            "nonce",
            "request_id",
            "operation_key",
            "grant_id",
            "grant_revision",
            "dataset_id",
            "dataset_version",
            "dataset_state_revision",
            "protected_artifact_sha256",
            "protected_action",
            "target_ref",
            "expires_at",
            "consumed_at",
            "operated_at",
        ),
        "protected_artifact": (
            "target_ref",
            "dataset_id",
            "dataset_version",
            "envelope",
            "envelope_sha256",
            "hmac_key_version",
        ),
        "audit_entry": (
            "sequence",
            "event_id",
            "event_kind",
            "operation_key",
            "entry_body",
            "previous_entry_sha256",
            "entry_sha256",
            "recorded_at",
        ),
        "audit_head": ("singleton", "sequence", "entry_sha256"),
    },
    "control": {
        "protected_identity": (
            "database_login",
            "actor_id",
            "actor_namespace",
            "principal_role",
            "identity_plane",
            "approval_role",
            "enabled",
        ),
        "protected_dataset": (
            "dataset_id",
            "dataset_version",
            "binding",
            "manifest_sha256",
            "protected_artifact_sha256",
            "hmac_key_version",
            "state",
            "state_revision",
            "authored_count",
            "review_complete",
            "lock_marker",
        ),
        "approval_evidence": ("source_event_id", "evidence", "canonical_raw_sha256", "recorded_at"),
        "authorization_grant": (
            "grant_id",
            "revision",
            "effective_revision",
            "grant_body",
            "subject_actor_id",
            "subject_namespace",
            "subject_role",
            "dataset_id",
            "dataset_version",
            "manifest_sha256",
            "protected_artifact_sha256",
            "hmac_key_version",
            "actions",
            "valid_from",
            "expires_at",
            "revoked_at",
            "lock_marker",
        ),
        "audit_entry": (
            "sequence",
            "event_id",
            "event_kind",
            "operation_key",
            "entry_body",
            "previous_entry_sha256",
            "entry_sha256",
            "recorded_at",
        ),
        "audit_head": ("singleton", "sequence", "entry_sha256"),
    },
}

_INSERT_COLUMNS: Mapping[str, Mapping[str, Sequence[str]]] = {
    "data": {
        "operation_capability": _SELECT_COLUMNS["data"]["operation_capability"][:12],
        "protected_artifact": _SELECT_COLUMNS["data"]["protected_artifact"],
        "audit_entry": _SELECT_COLUMNS["data"]["audit_entry"],
    },
    "control": {
        "approval_evidence": _SELECT_COLUMNS["control"]["approval_evidence"],
        "authorization_grant": tuple(
            column
            for column in _SELECT_COLUMNS["control"]["authorization_grant"]
            if column not in {"revoked_at", "lock_marker"}
        ),
        "audit_entry": _SELECT_COLUMNS["control"]["audit_entry"],
    },
}

_UPDATE_COLUMNS: Mapping[str, Mapping[str, Sequence[str]]] = {
    "data": {
        "protected_dataset": ("lock_marker",),
        "authorization_grant": ("lock_marker",),
        "operation_capability": ("consumed_at", "operated_at"),
        "protected_artifact": ("envelope", "envelope_sha256"),
        "audit_head": ("sequence", "entry_sha256"),
    },
    "control": {
        "protected_dataset": ("lock_marker",),
        "authorization_grant": ("effective_revision", "revoked_at", "lock_marker"),
        "audit_head": ("sequence", "entry_sha256"),
    },
}


def quoted_identifier(identifier: str) -> str:
    if _IDENTIFIER.fullmatch(identifier) is None:
        raise ValueError("protected database identifiers must be safe")
    return f'"{identifier}"'


def _column_list(columns: Sequence[str]) -> str:
    return ", ".join(quoted_identifier(column) for column in columns)


async def _validate_roles(
    connection: AsyncConnection, *, schema: str, owner: str, data_access: str, control: str
) -> None:
    if len({schema, owner, data_access, control}) != 4:
        raise ValueError("protected schema and roles must be distinct")
    for identifier in (schema, owner, data_access, control):
        quoted_identifier(identifier)
    rows = await connection.execute(
        text(
            "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolcanlogin, rolreplication, rolbypassrls "
            "FROM pg_roles WHERE rolname = ANY(:roles)"
        ),
        {"roles": [owner, data_access, control]},
    )
    states = {row.rolname: tuple(row)[1:] for row in rows}
    if set(states) != {owner, data_access, control} or any(any(state) for state in states.values()):
        raise ValueError("protected roles must exist as non-administrative NOLOGIN roles")


async def _revoke_existing(connection: AsyncConnection, *, schema: str, role: str, owner: str) -> None:
    schema_sql = quoted_identifier(schema)
    role_sql = quoted_identifier(role)
    owner_sql = quoted_identifier(owner)
    await connection.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema_sql} FROM {role_sql}"))
    await connection.execute(text(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema_sql} FROM {role_sql}"))
    await connection.execute(text(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {schema_sql} FROM {role_sql}"))
    await connection.execute(text(f"REVOKE CREATE ON SCHEMA {schema_sql} FROM {role_sql}"))
    await connection.execute(text(f"GRANT USAGE ON SCHEMA {schema_sql} TO {role_sql}"))
    await connection.execute(text(f"GRANT USAGE ON DOMAIN {schema_sql}.sha256_hex TO {role_sql}"))
    for object_type in ("TABLES", "SEQUENCES"):
        await connection.execute(
            text(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_sql} IN SCHEMA {schema_sql} "
                f"REVOKE ALL ON {object_type} FROM {role_sql}"
            )
        )
    columns = await connection.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = :schema ORDER BY table_name, ordinal_position"
        ),
        {"schema": schema},
    )
    for table_name, column_name in columns:
        target = f"{schema_sql}.{quoted_identifier(table_name)}"
        await connection.execute(
            text(f"REVOKE ALL ({quoted_identifier(column_name)}) ON TABLE {target} FROM {role_sql}")
        )


async def _grant_plane(connection: AsyncConnection, *, schema: str, role: str, plane: str) -> None:
    schema_sql = quoted_identifier(schema)
    role_sql = quoted_identifier(role)
    for table_name, columns in _SELECT_COLUMNS[plane].items():
        target = f"{schema_sql}.{quoted_identifier(table_name)}"
        await connection.execute(text(f"GRANT SELECT ({_column_list(columns)}) ON TABLE {target} TO {role_sql}"))
    for table_name, columns in _INSERT_COLUMNS[plane].items():
        target = f"{schema_sql}.{quoted_identifier(table_name)}"
        await connection.execute(text(f"GRANT INSERT ({_column_list(columns)}) ON TABLE {target} TO {role_sql}"))
    for table_name, columns in _UPDATE_COLUMNS[plane].items():
        target = f"{schema_sql}.{quoted_identifier(table_name)}"
        await connection.execute(text(f"GRANT UPDATE ({_column_list(columns)}) ON TABLE {target} TO {role_sql}"))


async def apply_protected_retrieval_role_policy(
    connection: AsyncConnection, *, schema: str, owner: str, data_access: str, control: str
) -> None:
    """Replace all protected runtime grants with the reviewed exact policy."""

    await _validate_roles(connection, schema=schema, owner=owner, data_access=data_access, control=control)
    for role, plane in ((data_access, "data"), (control, "control")):
        await _revoke_existing(connection, schema=schema, role=role, owner=owner)
        await _grant_plane(connection, schema=schema, role=role, plane=plane)


async def _validate_connection(
    connection: AsyncConnection,
    *,
    schema: str,
    data_access: str,
    control: str,
    plane: str,
) -> None:
    for identifier in (schema, data_access, control):
        quoted_identifier(identifier)
    expected_role = data_access if plane == "data" else control
    unexpected_role = control if plane == "data" else data_access
    unsafe = await connection.scalar(
        text(
            "SELECT role.rolsuper OR role.rolcreatedb OR role.rolcreaterole OR role.rolreplication "
            "OR role.rolbypassrls "
            "OR EXISTS (SELECT 1 FROM pg_class WHERE relowner = role.oid) "
            "OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = role.oid) "
            "OR EXISTS (SELECT 1 FROM pg_database WHERE datdba = role.oid) "
            "FROM pg_roles role WHERE role.rolname = current_user"
        )
    )
    memberships = set(
        await connection.scalars(
            text(
                "SELECT parent.rolname FROM pg_auth_members membership "
                "JOIN pg_roles parent ON parent.oid = membership.roleid "
                "JOIN pg_roles member ON member.oid = membership.member "
                "WHERE member.rolname = current_user"
            )
        )
    )
    membership_ok = await connection.scalar(
        text(
            "SELECT pg_has_role(current_user, :expected_role, 'MEMBER') "
            "AND NOT pg_has_role(current_user, :unexpected_role, 'MEMBER')"
        ),
        {"expected_role": expected_role, "unexpected_role": unexpected_role},
    )
    schema_ok = await connection.scalar(
        text(
            "SELECT has_schema_privilege(current_user, :schema, 'USAGE') "
            "AND NOT has_schema_privilege(current_user, :schema, 'CREATE')"
        ),
        {"schema": schema},
    )
    domain_ok = await connection.scalar(
        text("SELECT has_type_privilege(current_user, :domain, 'USAGE')"),
        {"domain": f"{schema}.sha256_hex"},
    )
    if unsafe or memberships != {expected_role} or not membership_ok or not schema_ok or not domain_ok:
        raise ValueError("protected connection identity or schema boundary is unsafe")

    tables = await connection.scalars(
        text("SELECT tablename FROM pg_tables WHERE schemaname = :schema ORDER BY tablename"),
        {"schema": schema},
    )
    for table_name in tables:
        relation = f"{schema}.{table_name}"
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "TRIGGER", "REFERENCES"):
            blanket = await connection.scalar(
                text("SELECT has_table_privilege(current_user, :relation, :privilege)"),
                {"relation": relation, "privilege": privilege},
            )
            if blanket:
                raise ValueError("protected connection has blanket table privileges")
        columns = await connection.scalars(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :table ORDER BY ordinal_position"
            ),
            {"schema": schema, "table": table_name},
        )
        for column_name in columns:
            for privilege, policy in (
                ("SELECT", _SELECT_COLUMNS[plane]),
                ("INSERT", _INSERT_COLUMNS[plane]),
                ("UPDATE", _UPDATE_COLUMNS[plane]),
            ):
                actual = await connection.scalar(
                    text("SELECT has_column_privilege(current_user, :relation, :column_name, :privilege)"),
                    {
                        "relation": relation,
                        "column_name": column_name,
                        "privilege": privilege,
                    },
                )
                expected = column_name in policy.get(table_name, ())
                if actual is not expected:
                    raise ValueError("protected connection column policy does not match")


async def validate_protected_data_connection(
    connection: AsyncConnection, *, schema: str, data_access: str, control: str
) -> None:
    await _validate_connection(
        connection,
        schema=schema,
        data_access=data_access,
        control=control,
        plane="data",
    )


async def validate_protected_control_connection(
    connection: AsyncConnection, *, schema: str, data_access: str, control: str
) -> None:
    await _validate_connection(
        connection,
        schema=schema,
        data_access=data_access,
        control=control,
        plane="control",
    )
