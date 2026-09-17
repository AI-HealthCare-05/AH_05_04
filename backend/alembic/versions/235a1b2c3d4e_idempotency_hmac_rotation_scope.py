"""Include HMAC key version in idempotency uniqueness (#235)."""

import sqlalchemy as sa
from alembic import op

revision = "235a1b2c3d4e"
down_revision = "633a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "uq_idempotency_async_scope",
        table_name="idempotency_record",
        postgresql_where=sa.text("record_type = 'ASYNC_JOB'"),
    )
    op.drop_index(
        "uq_idempotency_sync_scope",
        table_name="idempotency_record",
        postgresql_where=sa.text("record_type = 'SYNC_MUTATION'"),
    )
    op.create_index(
        "uq_idempotency_async_scope",
        "idempotency_record",
        ["record_type", "user_id", "operation_id", "key_hmac_version", "key_hmac"],
        unique=True,
        postgresql_where=sa.text("record_type = 'ASYNC_JOB'"),
    )
    op.create_index(
        "uq_idempotency_sync_scope",
        "idempotency_record",
        [
            "record_type",
            "user_id",
            "operation_id",
            "parent_resource_id",
            "key_hmac_version",
            "key_hmac",
        ],
        unique=True,
        postgresql_where=sa.text("record_type = 'SYNC_MUTATION'"),
    )


def _ensure_downgrade_unique_scope_is_data_safe(connection: sa.engine.Connection) -> None:
    connection.execute(sa.text("LOCK TABLE idempotency_record IN ACCESS EXCLUSIVE MODE"))

    async_conflicts = connection.execute(
        sa.text(
            """
            SELECT count(*)
            FROM (
                SELECT 1
                FROM idempotency_record
                WHERE record_type = 'ASYNC_JOB'
                GROUP BY record_type, user_id, operation_id, key_hmac
                HAVING count(*) > 1
            ) AS conflicts
            """
        )
    ).scalar_one()
    sync_conflicts = connection.execute(
        sa.text(
            """
            SELECT count(*)
            FROM (
                SELECT 1
                FROM idempotency_record
                WHERE record_type = 'SYNC_MUTATION'
                GROUP BY record_type, user_id, operation_id, parent_resource_id, key_hmac
                HAVING count(*) > 1
            ) AS conflicts
            """
        )
    ).scalar_one()

    if async_conflicts or sync_conflicts:
        raise RuntimeError(
            "Cannot downgrade revision 235a1b2c3d4e while idempotency records would conflict "
            "under the previous key_hmac-only unique scope "
            f"(async_conflicts={async_conflicts}, sync_conflicts={sync_conflicts}). "
            "Retain this revision or resolve duplicate scope rows with an approved forward-fix migration."
        )


def downgrade() -> None:
    bind = op.get_bind()
    _ensure_downgrade_unique_scope_is_data_safe(bind)

    op.drop_index(
        "uq_idempotency_sync_scope",
        table_name="idempotency_record",
        postgresql_where=sa.text("record_type = 'SYNC_MUTATION'"),
    )
    op.drop_index(
        "uq_idempotency_async_scope",
        table_name="idempotency_record",
        postgresql_where=sa.text("record_type = 'ASYNC_JOB'"),
    )
    op.create_index(
        "uq_idempotency_async_scope",
        "idempotency_record",
        ["record_type", "user_id", "operation_id", "key_hmac"],
        unique=True,
        postgresql_where=sa.text("record_type = 'ASYNC_JOB'"),
    )
    op.create_index(
        "uq_idempotency_sync_scope",
        "idempotency_record",
        ["record_type", "user_id", "operation_id", "parent_resource_id", "key_hmac"],
        unique=True,
        postgresql_where=sa.text("record_type = 'SYNC_MUTATION'"),
    )
