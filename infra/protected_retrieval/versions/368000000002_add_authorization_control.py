"""Add the authorization-control identity and audit boundaries."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import context, op
from sqlalchemy import text

revision: str = "368000000002"
down_revision: str | None = "368000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _setting(name: str) -> str:
    value = context.config.attributes.get(name)
    if not isinstance(value, str):
        raise RuntimeError(f"{name} is required")
    return value


def _q(identifier: str) -> str:
    return f'"{identifier}"'


def upgrade() -> None:
    schema = _q(_setting("PROTECTED_DB_SCHEMA"))

    op.execute(f"ALTER TABLE {schema}.protected_identity ADD COLUMN identity_plane text")
    op.execute(f"ALTER TABLE {schema}.protected_identity ADD COLUMN approval_role text")
    op.execute(f"UPDATE {schema}.protected_identity SET identity_plane = 'DATA'")
    op.execute(f"ALTER TABLE {schema}.protected_identity ALTER COLUMN identity_plane SET NOT NULL")
    op.execute(f"ALTER TABLE {schema}.protected_identity ALTER COLUMN principal_role DROP NOT NULL")
    op.execute(
        f"ALTER TABLE {schema}.protected_identity "
        "DROP CONSTRAINT protected_identity_actor_namespace_actor_id_key"
    )
    op.execute(
        f"ALTER TABLE {schema}.protected_identity ADD CONSTRAINT protected_identity_plane_check "
        "CHECK (identity_plane IN ('DATA', 'CONTROL'))"
    )
    op.execute(
        f"ALTER TABLE {schema}.protected_identity ADD CONSTRAINT protected_identity_approval_role_check "
        "CHECK (approval_role IS NULL OR approval_role IN ('DATASET_CUSTODIAN', 'PRODUCT_SAFETY_REVIEWER'))"
    )
    op.execute(
        f"ALTER TABLE {schema}.protected_identity ADD CONSTRAINT protected_identity_plane_role_check CHECK ("
        "(identity_plane = 'DATA' AND principal_role IS NOT NULL AND approval_role IS NULL) OR "
        "(identity_plane = 'CONTROL' AND principal_role IS NULL AND approval_role IS NOT NULL))"
    )
    op.execute(
        f"ALTER TABLE {schema}.protected_identity ADD CONSTRAINT protected_identity_actor_plane_key "
        "UNIQUE (actor_namespace, actor_id, identity_plane)"
    )
    op.execute(
        f"ALTER TABLE {schema}.authorization_grant ADD CONSTRAINT authorization_grant_scope_revision_key "
        "UNIQUE (subject_actor_id, subject_namespace, subject_role, dataset_id, dataset_version, revision)"
    )
    op.execute(f"ALTER TABLE {schema}.audit_entry DROP CONSTRAINT audit_entry_event_kind_check")
    op.execute(
        f"ALTER TABLE {schema}.audit_entry ADD CONSTRAINT audit_entry_event_kind_check "
        "CHECK (event_kind IN ('AUTHORIZATION', 'OPERATION', 'CONTROL'))"
    )


def downgrade() -> None:
    schema = _q(_setting("PROTECTED_DB_SCHEMA"))
    connection = op.get_bind()
    control_rows = connection.execute(
        text(
            f"""
            SELECT
                (SELECT count(*) FROM {schema}.protected_identity WHERE identity_plane = 'CONTROL') +
                (SELECT count(*) FROM {schema}.audit_entry WHERE event_kind = 'CONTROL')
            """
        )
    ).scalar_one()
    if control_rows:
        raise RuntimeError("authorization control downgrade refused while C1 durable rows exist")

    op.execute(f"ALTER TABLE {schema}.audit_entry DROP CONSTRAINT audit_entry_event_kind_check")
    op.execute(
        f"ALTER TABLE {schema}.audit_entry ADD CONSTRAINT audit_entry_event_kind_check "
        "CHECK (event_kind IN ('AUTHORIZATION', 'OPERATION'))"
    )
    op.execute(
        f"ALTER TABLE {schema}.authorization_grant DROP CONSTRAINT authorization_grant_scope_revision_key"
    )
    op.execute(f"ALTER TABLE {schema}.protected_identity DROP CONSTRAINT protected_identity_actor_plane_key")
    op.execute(f"ALTER TABLE {schema}.protected_identity DROP CONSTRAINT protected_identity_plane_role_check")
    op.execute(f"ALTER TABLE {schema}.protected_identity DROP CONSTRAINT protected_identity_approval_role_check")
    op.execute(f"ALTER TABLE {schema}.protected_identity DROP CONSTRAINT protected_identity_plane_check")
    op.execute(
        f"ALTER TABLE {schema}.protected_identity ADD CONSTRAINT "
        "protected_identity_actor_namespace_actor_id_key UNIQUE (actor_namespace, actor_id)"
    )
    op.execute(f"ALTER TABLE {schema}.protected_identity ALTER COLUMN principal_role SET NOT NULL")
    op.execute(f"ALTER TABLE {schema}.protected_identity DROP COLUMN approval_role")
    op.execute(f"ALTER TABLE {schema}.protected_identity DROP COLUMN identity_plane")
