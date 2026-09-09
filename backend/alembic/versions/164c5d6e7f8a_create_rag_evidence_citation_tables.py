"""create rag evidence citation tables

Revision ID: 164c5d6e7f8a
Revises: 164b6c7d8e9f
Create Date: 2026-09-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "164c5d6e7f8a"
down_revision: str | Sequence[str] | None = "164b6c7d8e9f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KNOWLEDGE_TYPE_VALUES = ("SOURCE_DOCUMENT", "SOURCE_RECORD", "SAFETY_POLICY")
_EVIDENCE_TYPE_VALUES = (
    "KNOWLEDGE_CHUNK",
    "PRODUCT_FACT",
    "INGREDIENT_FACT",
    "INTERACTION_RULE",
    "LIFESTYLE_GUIDELINE",
    "SAFETY_POLICY",
)
_EVIDENCE_STATUS_VALUES = ("DRAFT", "APPROVED")
_RULE_TYPE_VALUES = ("INTERACTION", "CONTRAINDICATION", "HIGH_RISK_SYMPTOM", "SAFETY_FALLBACK")
_GUIDELINE_TYPE_VALUES = ("MEDICATION_GUIDE", "LIFESTYLE", "LIMITED_RESPONSE", "SAFETY_FALLBACK")
_CITATION_TARGET_TYPE_VALUES = ("GUIDE", "CHAT_MESSAGE", "RAG_RESULT", "SAFETY_RESULT")
_CITATION_CLAIM_KIND_VALUES = ("MEDICAL", "AUXILIARY", "SAFETY_FALLBACK")
_CITATION_SUPPORT_STATUS_VALUES = ("SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED", "NOT_SUPPORTED")
_CITATION_AUTHORIZATION_STATUS_VALUES = ("PENDING", "REJECTED")
_CITATION_RELEASE_STATUS_VALUES = ("NOT_PUBLIC",)
_RAG_EVIDENCE_CITATION_TABLES = (
    "rag_citation",
    "rag_evidence_guideline",
    "rag_evidence_rule",
    "rag_evidence",
    "rag_evidence_knowledge",
)


def _sql_in_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _ensure_downgrade_is_data_safe(connection: sa.engine.Connection) -> None:
    for table_name in _RAG_EVIDENCE_CITATION_TABLES:
        connection.execute(sa.text(f"LOCK TABLE {table_name} IN ACCESS EXCLUSIVE MODE"))
    non_empty_tables = [
        table_name
        for table_name in _RAG_EVIDENCE_CITATION_TABLES
        if connection.execute(sa.text(f"SELECT count(*) FROM {table_name}")).scalar_one()
    ]
    if non_empty_tables:
        joined_tables = ", ".join(non_empty_tables)
        raise RuntimeError(
            "Cannot downgrade revision 164c5d6e7f8a while RAG Evidence/Citation data exists. "
            f"Non-empty tables: {joined_tables}. Use a forward-fix migration or an approved backup and "
            "data-retention rollback procedure instead."
        )


def _create_append_only_guard() -> None:
    op.execute(
        sa.text("""
        CREATE OR REPLACE FUNCTION prevent_rag_evidence_citation_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'RAG Evidence/Citation rows are append-only; use an approved forward-fix or lifecycle transition';
        END;
        $$ LANGUAGE plpgsql;
    """)
    )
    for table_name in _RAG_EVIDENCE_CITATION_TABLES:
        op.execute(
            sa.text(f"""
            CREATE TRIGGER trg_{table_name}_append_only_update
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_rag_evidence_citation_mutation()
        """)
        )
        op.execute(
            sa.text(f"""
            CREATE TRIGGER trg_{table_name}_append_only_delete
            BEFORE DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_rag_evidence_citation_mutation()
        """)
        )


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_rag_source_snapshot_id_version",
        "rag_source_snapshot",
        ["id", "source_version"],
    )
    op.create_table(
        "rag_evidence_knowledge",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("knowledge_key", sa.String(length=160), nullable=False),
        sa.Column("knowledge_type", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_locator", sa.String(length=500), nullable=True),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(knowledge_key)) > 0", name="chk_rag_evidence_knowledge_key_nonblank"),
        sa.CheckConstraint("length(trim(title)) > 0", name="chk_rag_evidence_knowledge_title_nonblank"),
        sa.CheckConstraint("length(content_digest) = 64", name="chk_rag_evidence_knowledge_digest_length"),
        sa.CheckConstraint(
            f"knowledge_type IN ({_sql_in_list(_KNOWLEDGE_TYPE_VALUES)})", name="chk_rag_evidence_knowledge_type"
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["rag_source_snapshot.id"], name="fk_rag_evidence_knowledge_snapshot"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_snapshot_id", "knowledge_key", name="uq_rag_evidence_knowledge_snapshot_key"),
        sa.UniqueConstraint("id", "source_snapshot_id", name="uq_rag_evidence_knowledge_id_snapshot"),
    )
    op.create_index("idx_rag_evidence_knowledge_snapshot", "rag_evidence_knowledge", ["source_snapshot_id"])

    op.create_table(
        "rag_evidence",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("knowledge_id", sa.CHAR(length=36), nullable=True),
        sa.Column("product_id", sa.CHAR(length=36), nullable=True),
        sa.Column("ingredient_id", sa.CHAR(length=36), nullable=True),
        sa.Column("evidence_key", sa.String(length=180), nullable=False),
        sa.Column("evidence_type", sa.String(length=40), nullable=False),
        sa.Column("evidence_status", sa.String(length=20), nullable=False),
        sa.Column("source_locator", sa.String(length=500), nullable=True),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(evidence_key)) > 0", name="chk_rag_evidence_key_nonblank"),
        sa.CheckConstraint("length(evidence_digest) = 64", name="chk_rag_evidence_digest_length"),
        sa.CheckConstraint(
            "source_locator IS NULL OR length(trim(source_locator)) > 0", name="chk_rag_evidence_locator_nonblank"
        ),
        sa.CheckConstraint(
            "evidence_type != 'KNOWLEDGE_CHUNK' OR knowledge_id IS NOT NULL",
            name="chk_rag_evidence_knowledge_chunk_has_knowledge",
        ),
        sa.CheckConstraint(
            "evidence_type != 'PRODUCT_FACT' OR product_id IS NOT NULL",
            name="chk_rag_evidence_product_fact_has_product",
        ),
        sa.CheckConstraint(
            "evidence_type != 'INGREDIENT_FACT' OR ingredient_id IS NOT NULL",
            name="chk_rag_evidence_ingredient_fact_has_ingredient",
        ),
        sa.CheckConstraint(f"evidence_type IN ({_sql_in_list(_EVIDENCE_TYPE_VALUES)})", name="chk_rag_evidence_type"),
        sa.CheckConstraint(
            f"evidence_status IN ({_sql_in_list(_EVIDENCE_STATUS_VALUES)})", name="chk_rag_evidence_status"
        ),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["rag_source_snapshot.id"], name="fk_rag_evidence_snapshot"),
        sa.ForeignKeyConstraint(
            ["knowledge_id", "source_snapshot_id"],
            ["rag_evidence_knowledge.id", "rag_evidence_knowledge.source_snapshot_id"],
            name="fk_rag_evidence_knowledge_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "source_snapshot_id"],
            ["rag_medication_product.id", "rag_medication_product.source_snapshot_id"],
            name="fk_rag_evidence_product_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["ingredient_id", "source_snapshot_id"],
            ["rag_medication_ingredient.id", "rag_medication_ingredient.source_snapshot_id"],
            name="fk_rag_evidence_ingredient_snapshot",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_snapshot_id", "evidence_key", name="uq_rag_evidence_snapshot_key"),
        sa.UniqueConstraint("id", "source_snapshot_id", name="uq_rag_evidence_id_snapshot"),
        sa.UniqueConstraint("id", "source_snapshot_id", "evidence_status", name="uq_rag_evidence_id_snapshot_status"),
    )
    op.create_index("idx_rag_evidence_snapshot_status", "rag_evidence", ["source_snapshot_id", "evidence_status"])

    op.create_table(
        "rag_evidence_rule",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("evidence_id", sa.CHAR(length=36), nullable=False),
        sa.Column("rule_key", sa.String(length=160), nullable=False),
        sa.Column("rule_type", sa.String(length=40), nullable=False),
        sa.Column("rule_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(rule_key)) > 0", name="chk_rag_evidence_rule_key_nonblank"),
        sa.CheckConstraint("length(rule_digest) = 64", name="chk_rag_evidence_rule_digest_length"),
        sa.CheckConstraint(f"rule_type IN ({_sql_in_list(_RULE_TYPE_VALUES)})", name="chk_rag_evidence_rule_type"),
        sa.ForeignKeyConstraint(["evidence_id"], ["rag_evidence.id"], name="fk_rag_evidence_rule_evidence"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_id", "rule_key", name="uq_rag_evidence_rule_key"),
    )
    op.create_table(
        "rag_evidence_guideline",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("evidence_id", sa.CHAR(length=36), nullable=False),
        sa.Column("guideline_key", sa.String(length=160), nullable=False),
        sa.Column("guideline_type", sa.String(length=40), nullable=False),
        sa.Column("guideline_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(guideline_key)) > 0", name="chk_rag_evidence_guideline_key_nonblank"),
        sa.CheckConstraint("length(guideline_digest) = 64", name="chk_rag_evidence_guideline_digest_length"),
        sa.CheckConstraint(
            f"guideline_type IN ({_sql_in_list(_GUIDELINE_TYPE_VALUES)})", name="chk_rag_evidence_guideline_type"
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["rag_evidence.id"], name="fk_rag_evidence_guideline_evidence"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_id", "guideline_key", name="uq_rag_evidence_guideline_key"),
    )
    op.create_table(
        "rag_citation",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("evidence_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("evidence_status", sa.String(length=20), nullable=False),
        sa.Column("target_type", sa.String(length=30), nullable=False),
        sa.Column("target_id", sa.String(length=80), nullable=False),
        sa.Column("claim_key", sa.String(length=160), nullable=False),
        sa.Column("claim_kind", sa.String(length=30), nullable=False),
        sa.Column("support_status", sa.String(length=30), nullable=False),
        sa.Column("authorization_status", sa.String(length=20), nullable=False),
        sa.Column("release_status", sa.String(length=20), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("source_title", sa.String(length=500), nullable=False),
        sa.Column("source_version", sa.String(length=255), nullable=False),
        sa.Column("source_locator", sa.String(length=500), nullable=False),
        sa.Column(
            "public_excerpt",
            sa.String(length=1000),
            nullable=True,
            comment="Approved source excerpt only; patient text, OCR text, prescription text, and provider raw output are prohibited.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(target_id)) > 0", name="chk_rag_citation_target_id_nonblank"),
        sa.CheckConstraint("length(trim(claim_key)) > 0", name="chk_rag_citation_claim_key_nonblank"),
        sa.CheckConstraint("display_order > 0", name="chk_rag_citation_display_order"),
        sa.CheckConstraint(
            "public_excerpt IS NULL OR length(trim(public_excerpt)) > 0",
            name="chk_rag_citation_public_excerpt_nonblank",
        ),
        sa.CheckConstraint("release_status = 'NOT_PUBLIC'", name="chk_rag_citation_public_guard_deferred"),
        sa.CheckConstraint(
            "claim_kind != 'MEDICAL' OR support_status != 'PARTIALLY_SUPPORTED'",
            name="chk_rag_citation_medical_not_partially_supported",
        ),
        sa.CheckConstraint(
            f"target_type IN ({_sql_in_list(_CITATION_TARGET_TYPE_VALUES)})", name="chk_rag_citation_target_type"
        ),
        sa.CheckConstraint(
            f"claim_kind IN ({_sql_in_list(_CITATION_CLAIM_KIND_VALUES)})", name="chk_rag_citation_claim_kind"
        ),
        sa.CheckConstraint(
            f"evidence_status IN ({_sql_in_list(_EVIDENCE_STATUS_VALUES)})",
            name="chk_rag_citation_evidence_status",
        ),
        sa.CheckConstraint(
            f"support_status IN ({_sql_in_list(_CITATION_SUPPORT_STATUS_VALUES)})",
            name="chk_rag_citation_support_status",
        ),
        sa.CheckConstraint(
            f"authorization_status IN ({_sql_in_list(_CITATION_AUTHORIZATION_STATUS_VALUES)})",
            name="chk_rag_citation_authorization_status",
        ),
        sa.CheckConstraint(
            f"release_status IN ({_sql_in_list(_CITATION_RELEASE_STATUS_VALUES)})",
            name="chk_rag_citation_release_status",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "source_snapshot_id", "evidence_status"],
            ["rag_evidence.id", "rag_evidence.source_snapshot_id", "rag_evidence.evidence_status"],
            name="fk_rag_citation_evidence_snapshot_status",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id", "source_version"],
            ["rag_source_snapshot.id", "rag_source_snapshot.source_version"],
            name="fk_rag_citation_snapshot_version",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("target_type", "target_id", "claim_key", name="uq_rag_citation_target_claim"),
        sa.UniqueConstraint("target_type", "target_id", "display_order", name="uq_rag_citation_display_order"),
    )
    op.create_index("idx_rag_citation_target", "rag_citation", ["target_type", "target_id"])
    op.create_index("idx_rag_citation_evidence", "rag_citation", ["evidence_id"])
    op.create_index("idx_rag_citation_source_snapshot", "rag_citation", ["source_snapshot_id"])
    _create_append_only_guard()


def downgrade() -> None:
    connection = op.get_bind()
    _ensure_downgrade_is_data_safe(connection)
    for table_name in _RAG_EVIDENCE_CITATION_TABLES:
        op.execute(f"DROP TRIGGER trg_{table_name}_append_only_delete ON {table_name}")
        op.execute(f"DROP TRIGGER trg_{table_name}_append_only_update ON {table_name}")
    op.execute("DROP FUNCTION prevent_rag_evidence_citation_mutation()")
    op.drop_index("idx_rag_citation_source_snapshot", table_name="rag_citation")
    op.drop_index("idx_rag_citation_evidence", table_name="rag_citation")
    op.drop_index("idx_rag_citation_target", table_name="rag_citation")
    op.drop_table("rag_citation")
    op.drop_table("rag_evidence_guideline")
    op.drop_table("rag_evidence_rule")
    op.drop_index("idx_rag_evidence_snapshot_status", table_name="rag_evidence")
    op.drop_table("rag_evidence")
    op.drop_index("idx_rag_evidence_knowledge_snapshot", table_name="rag_evidence_knowledge")
    op.drop_table("rag_evidence_knowledge")
    op.drop_constraint("uq_rag_source_snapshot_id_version", "rag_source_snapshot", type_="unique")
