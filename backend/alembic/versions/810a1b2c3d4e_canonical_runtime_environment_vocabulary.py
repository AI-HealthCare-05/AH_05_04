"""Canonical runtime environment vocabulary persistence (PD-799-20260918, Issue #810).

Revision ID: 810a1b2c3d4e
Revises: 809a2b3c4d5e
Create Date: 2026-09-18
"""

from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "810a1b2c3d4e"
down_revision = "809a2b3c4d5e"
branch_labels = None
depends_on = None

_ENVIRONMENT_TABLE = "rag_runtime_environment"
_RELEASE_BUNDLE_TABLE = "rag_runtime_release_bundle"

_ENV_CHECK_NAME = "chk_rag_runtime_environment_code"
_BUNDLE_CHECK_NAME = "chk_rag_runtime_bundle_environment_code"

_CANONICAL_VALUES = ("LOCAL", "TEST", "CLOSED_DEMO", "PRODUCTION")
_ALLOWED_VOCABULARY_SQL = "environment_code IN ('LOCAL', 'TEST', 'CLOSED_DEMO', 'PRODUCTION')"


def _validate_existing_data(connection: Any) -> None:
    for table_name in (_ENVIRONMENT_TABLE, _RELEASE_BUNDLE_TABLE):
        query = sa.text(f"SELECT DISTINCT environment_code FROM {table_name}")
        result = connection.execute(query)
        codes = [row[0] for row in result]
        invalid = [code for code in codes if code not in _CANONICAL_VALUES]
        if invalid:
            raise RuntimeError(
                f"{table_name} contains non-canonical environment_code values: {invalid}. "
                f"Allowed vocabulary: {list(_CANONICAL_VALUES)}"
            )


def upgrade() -> None:
    bind = op.get_bind()
    _validate_existing_data(bind)

    op.create_check_constraint(
        _ENV_CHECK_NAME,
        _ENVIRONMENT_TABLE,
        _ALLOWED_VOCABULARY_SQL,
    )
    op.create_check_constraint(
        _BUNDLE_CHECK_NAME,
        _RELEASE_BUNDLE_TABLE,
        _ALLOWED_VOCABULARY_SQL,
    )


def downgrade() -> None:
    op.drop_constraint(_BUNDLE_CHECK_NAME, _RELEASE_BUNDLE_TABLE, type_="check")
    op.drop_constraint(_ENV_CHECK_NAME, _ENVIRONMENT_TABLE, type_="check")
