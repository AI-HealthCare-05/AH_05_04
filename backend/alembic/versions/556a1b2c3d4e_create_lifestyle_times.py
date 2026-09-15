"""Create the Track B lifestyle times current-state table (#556).

Revision ID: 556a1b2c3d4e
Revises: 168a1b2c3d4e
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "556a1b2c3d4e"
down_revision = "168a1b2c3d4e"
branch_labels = None
depends_on = None


def _raise_if_lifestyle_times_exists() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE lifestyle_times IN ACCESS EXCLUSIVE MODE"))
    if bind.execute(sa.text("SELECT 1 FROM lifestyle_times LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade lifestyle_times while lifestyle rows exist")


def upgrade() -> None:
    op.create_table(
        "lifestyle_times",
        sa.Column("profile_id", sa.CHAR(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("days", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("revision > 0", name="chk_lifestyle_times_revision_positive"),
        sa.CheckConstraint("jsonb_typeof(days) = 'array'", name="chk_lifestyle_times_days_array"),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profile.id"],
            name="fk_lifestyle_times_profile",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("profile_id", name="pk_lifestyle_times"),
    )


def downgrade() -> None:
    _raise_if_lifestyle_times_exists()
    op.drop_table("lifestyle_times")
