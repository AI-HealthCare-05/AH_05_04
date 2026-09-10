"""add refresh session table and password reset token table

Revision ID: 206a1b2c3d4e
Revises: 164c5d6e7f8a
Create Date: 2026-09-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "206a1b2c3d4e"
down_revision: str | Sequence[str] | None = "201a1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ensure_refresh_rotation_downgrade_is_data_safe(connection: sa.engine.Connection) -> None:
    """PR #404 리뷰: password_reset_token만 검사하던 원래 guard는 그 테이블이
    비어 있으면 refresh_session에 실제 로그인 세션 row가 남아 있어도 검사 없이
    downgrade를 통과시켜, 운영 세션 계보 데이터가 조용히 삭제될 수 있었다. 두
    테이블을 하나의 LOCK 문으로 고정 순서(password_reset_token, refresh_session)
    에 걸어 각각 검사한다."""
    connection.execute(sa.text("LOCK TABLE password_reset_token, refresh_session IN ACCESS EXCLUSIVE MODE"))
    counts = {
        table: connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in ("password_reset_token", "refresh_session")
    }
    populated = {table: count for table, count in counts.items() if count}
    if populated:
        summary = ", ".join(f"{table}={count}" for table, count in populated.items())
        raise RuntimeError(
            f"Cannot downgrade revision 206a1b2c3d4e while rows exist ({summary}). "
            "Use a forward-fix migration or an approved backup and data-retention "
            "rollback procedure instead."
        )


def upgrade() -> None:
    op.create_table(
        "refresh_session",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("user_id", sa.CHAR(length=36), nullable=False),
        sa.Column("active_jti", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_refresh_session_user"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_refresh_session_user", "refresh_session", ["user_id"])

    op.create_table(
        "password_reset_token",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("user_id", sa.CHAR(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(token_hash) = 64", name="chk_password_reset_token_hash_length"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_password_reset_token_user"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_password_reset_token_token_hash",
        "password_reset_token",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "idx_password_reset_token_user_created",
        "password_reset_token",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    _ensure_refresh_rotation_downgrade_is_data_safe(bind)

    op.drop_index("idx_password_reset_token_user_created", table_name="password_reset_token")
    op.drop_index("ix_password_reset_token_token_hash", table_name="password_reset_token")
    op.drop_table("password_reset_token")

    op.drop_index("idx_refresh_session_user", table_name="refresh_session")
    op.drop_table("refresh_session")
