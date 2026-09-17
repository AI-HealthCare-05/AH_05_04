"""Create Track F Assessment·Eligibility Authority persistence table (#712).

Production Evidence Gate를 통과한 선택 청크(selected hit)의 불변 증거 권위(Evidence Authority)를 영속화합니다.
선행 거버넌스 PD-722(PR #725)에서 승인된 24h 유효기간 상한 및 하프오픈 구간 제약을 적용합니다.
Trigger·RLS·Stored Procedure·사용자 정의 DB 함수를 일체 추가하지 않고 표준 제약만 사용합니다.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "712a1b2c3d4e"
down_revision = "713a1b2c3d4e"
branch_labels = None
depends_on = None

_TABLE_NAME = "rag_evidence_authority"


def upgrade() -> None:
    op.create_table(
        _TABLE_NAME,
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("retrieval_run_id", sa.CHAR(length=36), nullable=False),
        sa.Column("knowledge_chunk_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_member_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_code", sa.String(length=100), nullable=False),
        sa.Column("source_version", sa.String(length=200), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("eligibility_receipt_artifact_code", sa.String(length=100), nullable=False),
        sa.Column("eligibility_receipt_version", sa.String(length=50), nullable=False),
        sa.Column("eligibility_receipt_sha256", sa.String(length=64), nullable=False),
        sa.Column("assessment_artifact_code", sa.String(length=100), nullable=False),
        sa.Column("assessment_artifact_version", sa.String(length=50), nullable=False),
        sa.Column("assessment_artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("verifier_artifact_code", sa.String(length=100), nullable=False),
        sa.Column("verifier_artifact_version", sa.String(length=50), nullable=False),
        sa.Column("verifier_artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("validity_policy_artifact_code", sa.String(length=100), nullable=False),
        sa.Column("validity_policy_version", sa.String(length=50), nullable=False),
        sa.Column("validity_policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assessment_valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assessment_valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rag_evidence_authority"),
        sa.UniqueConstraint(
            "retrieval_run_id",
            "knowledge_chunk_id",
            name="uq_rag_evidence_authority_run_chunk",
        ),
        sa.UniqueConstraint(
            "assessment_artifact_code",
            "assessment_artifact_version",
            "assessment_artifact_sha256",
            name="uq_rag_evidence_authority_assessment_ref",
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_run_id", "knowledge_chunk_id"],
            ["retrieval_hit.retrieval_run_id", "retrieval_hit.knowledge_chunk_id"],
            name="fk_rag_evidence_authority_hit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["rag_source_snapshot.id"],
            name="fk_rag_evidence_authority_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_member_id"],
            ["rag_source_snapshot_member.id"],
            name="fk_rag_evidence_authority_snapshot_member",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "assessment_valid_from < assessment_valid_until",
            name="chk_rag_evidence_authority_validity_window",
        ),
        sa.CheckConstraint(
            "evaluated_at >= assessment_valid_from AND evaluated_at < assessment_valid_until",
            name="chk_rag_evidence_authority_evaluated_at_in_window",
        ),
        sa.CheckConstraint(
            "length(trim(source_code)) > 0",
            name="chk_rag_evidence_authority_source_code_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(source_version)) > 0",
            name="chk_rag_evidence_authority_source_version_nonblank",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_evidence_authority_content_sha256",
        ),
        sa.CheckConstraint(
            "eligibility_receipt_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_evidence_authority_eligibility_sha256",
        ),
        sa.CheckConstraint(
            "assessment_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_evidence_authority_assessment_sha256",
        ),
        sa.CheckConstraint(
            "verifier_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_evidence_authority_verifier_sha256",
        ),
        sa.CheckConstraint(
            "validity_policy_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_evidence_authority_validity_policy_sha256",
        ),
    )
    op.create_index(
        "idx_rag_evidence_authority_assessment_ref",
        _TABLE_NAME,
        [
            "assessment_artifact_code",
            "assessment_artifact_version",
            "assessment_artifact_sha256",
        ],
    )


def _has_rows(table_name: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT 1 FROM {table_name} LIMIT 1")).first())


def _raise_if_evidence_authority_data_exists() -> None:
    """실제 authority 증거가 남아 있으면 downgrade를 거부합니다.

    이 표는 append-only historical 증거이므로 drop은 조용한 데이터 손실입니다. 검사와 drop
    사이에 write가 끼어들지 않도록 먼저 ACCESS EXCLUSIVE lock을 잡습니다.
    """
    bind = op.get_bind()
    bind.execute(sa.text(f"LOCK TABLE {_TABLE_NAME} IN ACCESS EXCLUSIVE MODE"))
    if _has_rows(_TABLE_NAME):
        raise RuntimeError(f"Refusing to downgrade evidence authority table with existing data: {_TABLE_NAME}")


def downgrade() -> None:
    _raise_if_evidence_authority_data_exists()
    op.drop_index("idx_rag_evidence_authority_assessment_ref", table_name=_TABLE_NAME)
    op.drop_table(_TABLE_NAME)
