"""Persist canonical Guide release projections.

Revision ID: 2ba3431f4ef2
Revises: 869a1b2c3d4e
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2ba3431f4ef2"
down_revision = "869a1b2c3d4e"
branch_labels = None
depends_on = None

GUIDE_RELEASE_SHAPE = """
(release_projection_version IS NULL AND release_decision IS NULL
 AND release_is_current IS NULL AND answer_claim_action_texts IS NULL
 AND answer_uncertainty_text IS NULL AND answer_consultation_text IS NULL
 AND fallback_code IS NULL AND fallback_text IS NULL)
OR
(release_projection_version IS NOT NULL AND release_is_current IS NOT NULL AND
 ((release_decision = 'PASS' AND release_is_current AND content IS NOT NULL
   AND answer_claim_action_texts IS NOT NULL
   AND jsonb_typeof(answer_claim_action_texts) = 'array'
   AND jsonb_array_length(answer_claim_action_texts) > 0
   AND answer_uncertainty_text IS NOT NULL AND answer_consultation_text IS NOT NULL
   AND fallback_code IS NULL AND fallback_text IS NULL)
  OR
  (release_decision IN ('LIMITED', 'REJECTED') AND release_is_current AND content IS NULL
   AND answer_claim_action_texts IS NULL AND answer_uncertainty_text IS NULL
   AND answer_consultation_text IS NULL AND fallback_code IS NOT NULL
   AND fallback_text IS NOT NULL)
  OR
  (release_decision = 'STALE' AND NOT release_is_current AND content IS NULL
   AND answer_claim_action_texts IS NULL AND answer_uncertainty_text IS NULL
   AND answer_consultation_text IS NULL AND fallback_code IS NOT NULL
   AND fallback_text IS NOT NULL)))
"""

CITATION_VARIANT = """
(knowledge_chunk_id IS NOT NULL AND claim_text IS NOT NULL AND cited_text IS NOT NULL
 AND card_target_ref IS NULL AND claim_key IS NULL AND evidence_key IS NULL
 AND source_type IS NULL AND source_snapshot_id IS NULL
 AND source_snapshot_member_id IS NULL AND source_code IS NULL
 AND source_version IS NULL AND locator IS NULL AND content_sha256 IS NULL)
OR
(knowledge_chunk_id IS NULL AND claim_text IS NULL AND cited_text IS NULL
 AND card_target_ref IS NOT NULL AND claim_key IS NOT NULL AND evidence_key IS NOT NULL
 AND source_type IS NOT NULL AND source_snapshot_id IS NOT NULL
 AND source_snapshot_member_id IS NOT NULL AND source_code IS NOT NULL
 AND source_version IS NOT NULL AND locator IS NOT NULL AND content_sha256 IS NOT NULL)
"""

FALLBACK_CODES = (
    "NO_APPROVED_EVIDENCE",
    "CONFLICTING_EVIDENCE",
    "PROVIDER_TIMEOUT",
    "DEPENDENCY_UNAVAILABLE",
    "VALIDATION_FAILED",
    "PRESCRIPTION_STALE",
    "EXECUTION_CONTEXT_STALE",
    "UNSUPPORTED_REQUEST",
)


def upgrade() -> None:
    op.add_column("guide", sa.Column("release_projection_version", sa.String(100), nullable=True))
    op.add_column("guide", sa.Column("release_decision", sa.String(20), nullable=True))
    op.add_column("guide", sa.Column("release_is_current", sa.Boolean(), nullable=True))
    op.add_column(
        "guide",
        sa.Column("answer_claim_action_texts", postgresql.JSONB(none_as_null=True), nullable=True),
    )
    op.add_column("guide", sa.Column("answer_uncertainty_text", sa.Text(), nullable=True))
    op.add_column("guide", sa.Column("answer_consultation_text", sa.Text(), nullable=True))
    op.add_column("guide", sa.Column("fallback_code", sa.String(100), nullable=True))
    op.add_column("guide", sa.Column("fallback_text", sa.Text(), nullable=True))
    op.create_check_constraint("chk_guide_release_projection_shape", "guide", GUIDE_RELEASE_SHAPE)
    op.create_check_constraint(
        "chk_guide_release_projection_version",
        "guide",
        "release_projection_version IS NULL OR release_projection_version = 'guide-runtime-release-projection-v1'",
    )
    fallback_values = ", ".join(f"'{code}'" for code in FALLBACK_CODES)
    op.create_check_constraint(
        "chk_guide_release_fallback_code",
        "guide",
        f"fallback_code IS NULL OR fallback_code IN ({fallback_values})",
    )

    op.alter_column("guide_citation", "knowledge_chunk_id", nullable=True)
    op.alter_column("guide_citation", "claim_text", nullable=True)
    op.alter_column("guide_citation", "cited_text", nullable=True)
    op.add_column("guide_citation", sa.Column("card_target_ref", sa.String(200), nullable=True))
    op.add_column("guide_citation", sa.Column("claim_key", sa.String(200), nullable=True))
    op.add_column("guide_citation", sa.Column("evidence_key", sa.String(200), nullable=True))
    op.add_column("guide_citation", sa.Column("source_type", sa.String(50), nullable=True))
    op.add_column("guide_citation", sa.Column("source_snapshot_id", sa.CHAR(36), nullable=True))
    op.add_column("guide_citation", sa.Column("source_snapshot_member_id", sa.CHAR(36), nullable=True))
    op.add_column("guide_citation", sa.Column("source_code", sa.String(100), nullable=True))
    op.add_column("guide_citation", sa.Column("source_version", sa.String(200), nullable=True))
    op.add_column("guide_citation", sa.Column("locator", sa.String(500), nullable=True))
    op.add_column("guide_citation", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_guide_citation_source_snapshot",
        "guide_citation",
        "rag_source_snapshot",
        ["source_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_guide_citation_source_snapshot_member",
        "guide_citation",
        "rag_source_snapshot_member",
        ["source_snapshot_member_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint("chk_guide_citation_variant", "guide_citation", CITATION_VARIANT)
    op.create_check_constraint(
        "chk_guide_citation_content_hash",
        "guide_citation",
        "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "chk_guide_citation_source_type",
        "guide_citation",
        "source_type IS NULL OR source_type = 'LIFESTYLE_GUIDELINE'",
    )


def downgrade() -> None:
    connection = op.get_bind()
    release_count = connection.scalar(
        sa.text("SELECT count(*) FROM guide WHERE release_projection_version IS NOT NULL")
    )
    runtime_citation_count = connection.scalar(
        sa.text("SELECT count(*) FROM guide_citation WHERE knowledge_chunk_id IS NULL")
    )
    if release_count or runtime_citation_count:
        raise RuntimeError("cannot downgrade while Guide release projection data exists")

    op.drop_constraint("chk_guide_citation_source_type", "guide_citation", type_="check")
    op.drop_constraint("chk_guide_citation_content_hash", "guide_citation", type_="check")
    op.drop_constraint("chk_guide_citation_variant", "guide_citation", type_="check")
    op.drop_constraint("fk_guide_citation_source_snapshot_member", "guide_citation", type_="foreignkey")
    op.drop_constraint("fk_guide_citation_source_snapshot", "guide_citation", type_="foreignkey")
    for column in (
        "content_sha256",
        "locator",
        "source_version",
        "source_code",
        "source_snapshot_member_id",
        "source_snapshot_id",
        "source_type",
        "evidence_key",
        "claim_key",
        "card_target_ref",
    ):
        op.drop_column("guide_citation", column)
    op.alter_column("guide_citation", "cited_text", nullable=False)
    op.alter_column("guide_citation", "claim_text", nullable=False)
    op.alter_column("guide_citation", "knowledge_chunk_id", nullable=False)

    op.drop_constraint("chk_guide_release_fallback_code", "guide", type_="check")
    op.drop_constraint("chk_guide_release_projection_version", "guide", type_="check")
    op.drop_constraint("chk_guide_release_projection_shape", "guide", type_="check")
    for column in (
        "fallback_text",
        "fallback_code",
        "answer_consultation_text",
        "answer_uncertainty_text",
        "answer_claim_action_texts",
        "release_is_current",
        "release_decision",
        "release_projection_version",
    ):
        op.drop_column("guide", column)
