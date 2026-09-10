"""persist the full runtime bundle canonical configuration behind bundle_manifest_hash

Every value that enters ``bundle_manifest_hash`` must be recoverable from storage, otherwise the
hash is an opaque token: two different pinned configurations can produce different hashes while
leaving byte-identical rows, and ``rag-runtime-v1.md``'s requirement to re-verify the Bundle
Manifest before an environment pointer change cannot be satisfied.

Decision: docs/governance/decisions/2026-09-10-runtime-bundle-canonical-configuration-persistence.md

Revision ID: 175a1b2c3d4e
Revises: 201a1b2c3d4e
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "175a1b2c3d4e"
down_revision: str | Sequence[str] | None = "201a1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ARTIFACT_KINDS = ("candidate_index", "knowledge_index", "rule_set", "guideline_set", "safety_policy")


def _count(connection: sa.Connection, table: str) -> int:
    return connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()


def _require_empty(connection: sa.Connection) -> None:
    """The new identity columns are NOT NULL with no backfillable default.

    ``rag-runtime-v1.md`` fixes Post-MVP-1 RAG Runtime as Local-only with ``PUBLIC_TRACK_F=false``
    and no Development/Staging environment, so there is no deployed bundle to migrate.  Inventing
    placeholder approval or policy hashes for existing rows would fabricate provenance for content
    that was never verified, which is exactly what this change exists to prevent.  Fail instead of
    guessing.
    """
    populated = {
        table: _count(connection, table)
        for table in ("rag_runtime_release_bundle", "rag_runtime_bundle_source")
        if _count(connection, table) > 0
    }
    if populated:
        rows = ", ".join(f"{table}={count}" for table, count in sorted(populated.items()))
        raise RuntimeError(
            "Runtime Bundle 행이 존재하면 이 migration을 적용할 수 없습니다 "
            f"({rows}). 승인·정책 hash를 추정해 backfill하지 않습니다. "
            "Local 재구축으로 Bundle을 다시 build하십시오."
        )


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE rag_runtime_release_bundle IN SHARE ROW EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE rag_runtime_bundle_source IN SHARE ROW EXCLUSIVE MODE"))
    _require_empty(connection)

    op.add_column("rag_runtime_release_bundle", sa.Column("environment_code", sa.String(length=50), nullable=False))
    op.add_column("rag_runtime_release_bundle", sa.Column("catalog_version", sa.String(length=80), nullable=False))
    op.add_column(
        "rag_runtime_release_bundle",
        sa.Column("catalog_manifest_hash", sa.String(length=64), nullable=False),
    )
    for kind in _ARTIFACT_KINDS:
        op.add_column("rag_runtime_release_bundle", sa.Column(f"{kind}_version", sa.String(length=80), nullable=True))

    op.create_check_constraint(
        "chk_rag_runtime_bundle_environment_code_nonblank",
        "rag_runtime_release_bundle",
        "length(trim(environment_code)) > 0",
    )
    op.create_check_constraint(
        "chk_rag_runtime_bundle_catalog_version_nonblank",
        "rag_runtime_release_bundle",
        "length(trim(catalog_version)) > 0",
    )
    op.create_check_constraint(
        "chk_rag_runtime_bundle_catalog_manifest_hash",
        "rag_runtime_release_bundle",
        "length(catalog_manifest_hash) = 64",
    )
    for kind in _ARTIFACT_KINDS:
        op.create_check_constraint(
            f"chk_rag_runtime_bundle_{kind}_ref_version_pair",
            "rag_runtime_release_bundle",
            f"({kind}_ref IS NULL) = ({kind}_version IS NULL)",
        )

    op.add_column("rag_runtime_bundle_source", sa.Column("source_version", sa.String(length=255), nullable=False))
    op.add_column("rag_runtime_bundle_source", sa.Column("canonical_checksum", sa.String(length=64), nullable=False))
    op.add_column("rag_runtime_bundle_source", sa.Column("approval_version", sa.String(length=80), nullable=False))
    op.add_column("rag_runtime_bundle_source", sa.Column("scope_policy_hash", sa.String(length=64), nullable=False))
    op.add_column(
        "rag_runtime_bundle_source",
        sa.Column("freshness_policy_hash", sa.String(length=64), nullable=False),
    )

    for name, expression in (
        ("chk_rag_runtime_bundle_source_version_nonblank", "length(trim(source_version)) > 0"),
        ("chk_rag_runtime_bundle_source_approval_nonblank", "length(trim(approval_version)) > 0"),
        ("chk_rag_runtime_bundle_source_canonical_checksum", "length(canonical_checksum) = 64"),
        ("chk_rag_runtime_bundle_source_scope_policy_hash", "length(scope_policy_hash) = 64"),
        ("chk_rag_runtime_bundle_source_freshness_policy_hash", "length(freshness_policy_hash) = 64"),
    ):
        op.create_check_constraint(name, "rag_runtime_bundle_source", expression)

    # Pin the snapshot version by reference rather than by copied string: the composite FK targets
    # uq_rag_source_snapshot_id_version, so a member cannot claim a version the snapshot does not
    # have.  #164's single-column fk_rag_runtime_bundle_source_snapshot is intentionally kept --
    # the composite constraint subsumes it, but dropping a merged constraint is a wider change
    # than this issue needs and tests/migration asserts its presence.
    op.create_foreign_key(
        "fk_rag_runtime_bundle_source_snapshot_version",
        "rag_runtime_bundle_source",
        "rag_source_snapshot",
        ["source_snapshot_id", "source_version"],
        ["id", "source_version"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE rag_runtime_release_bundle IN SHARE ROW EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE rag_runtime_bundle_source IN SHARE ROW EXCLUSIVE MODE"))
    _require_empty(connection)

    op.drop_constraint(
        "fk_rag_runtime_bundle_source_snapshot_version",
        "rag_runtime_bundle_source",
        type_="foreignkey",
    )

    for name in (
        "chk_rag_runtime_bundle_source_version_nonblank",
        "chk_rag_runtime_bundle_source_approval_nonblank",
        "chk_rag_runtime_bundle_source_canonical_checksum",
        "chk_rag_runtime_bundle_source_scope_policy_hash",
        "chk_rag_runtime_bundle_source_freshness_policy_hash",
    ):
        op.drop_constraint(name, "rag_runtime_bundle_source", type_="check")
    for column in (
        "freshness_policy_hash",
        "scope_policy_hash",
        "approval_version",
        "canonical_checksum",
        "source_version",
    ):
        op.drop_column("rag_runtime_bundle_source", column)

    for kind in _ARTIFACT_KINDS:
        op.drop_constraint(
            f"chk_rag_runtime_bundle_{kind}_ref_version_pair", "rag_runtime_release_bundle", type_="check"
        )
    for name in (
        "chk_rag_runtime_bundle_catalog_manifest_hash",
        "chk_rag_runtime_bundle_catalog_version_nonblank",
        "chk_rag_runtime_bundle_environment_code_nonblank",
    ):
        op.drop_constraint(name, "rag_runtime_release_bundle", type_="check")
    for kind in _ARTIFACT_KINDS:
        op.drop_column("rag_runtime_release_bundle", f"{kind}_version")
    for column in ("catalog_manifest_hash", "catalog_version", "environment_code"):
        op.drop_column("rag_runtime_release_bundle", column)
