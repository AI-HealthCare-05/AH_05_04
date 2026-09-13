"""Add Web Push subscriptions and separate delivery ledger (PD-469)."""

import sqlalchemy as sa
from alembic import op

revision = "469a1b2c3d4e"
down_revision = "166f30415263"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_subscription",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("profile_id", sa.CHAR(36), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("generation", sa.CHAR(36), nullable=False),
        sa.Column("endpoint_hmac", sa.String(length=64), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("key_id", sa.String(length=40), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND ciphertext IS NOT NULL) OR (revoked_at IS NOT NULL AND ciphertext IS NULL)",
            name="chk_push_subscription_revocation",
        ),
        sa.CheckConstraint("token_version >= 0", name="chk_push_subscription_token_version"),
        sa.ForeignKeyConstraint(["profile_id"], ["profile.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint_hmac", name="uq_push_subscription_endpoint"),
    )
    op.create_index("idx_push_subscription_profile", "push_subscription", ["profile_id"], unique=False)
    op.create_table(
        "push_delivery",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("notification_id", sa.CHAR(36), nullable=False),
        sa.Column("subscription_id", sa.CHAR(36), nullable=False),
        sa.Column("generation", sa.CHAR(36), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claim_token", sa.CHAR(36), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=30), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(status = 'ACCEPTED' AND accepted_at IS NOT NULL) OR (status != 'ACCEPTED' AND accepted_at IS NULL)",
            name="chk_push_delivery_accepted",
        ),
        sa.CheckConstraint(
            "(status = 'SENDING' AND claim_token IS NOT NULL AND claim_expires_at IS NOT NULL) OR (status != 'SENDING' AND claim_token IS NULL AND claim_expires_at IS NULL)",
            name="chk_push_delivery_claim",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENDING', 'ACCEPTED', 'FAILED', 'UNKNOWN', 'CANCELLED')",
            name="chk_push_delivery_status",
        ),
        sa.CheckConstraint("attempt_count BETWEEN 0 AND 3", name="chk_push_delivery_attempt"),
        sa.ForeignKeyConstraint(["notification_id"], ["notification_record.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subscription_id"], ["push_subscription.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id", "subscription_id", "generation", name="uq_push_delivery_generation"),
    )
    op.create_index("idx_push_delivery_due", "push_delivery", ["status", "next_attempt_at"], unique=False)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE push_subscription, push_delivery IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM push_subscription) OR EXISTS (SELECT 1 FROM push_delivery)")
    ).scalar_one():
        raise RuntimeError("Push subscription or delivery history blocks downgrade")
    op.drop_table("push_delivery")
    op.drop_table("push_subscription")
