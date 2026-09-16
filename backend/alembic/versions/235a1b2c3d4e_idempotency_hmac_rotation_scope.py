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


def downgrade() -> None:
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
