"""Create RAG-07B Candidate Index persistence tables (#168).

BUILDING만 이 스키마로 쓸 수 있다. READY/RETIRED 전환과 환경 pointer는 RAG-17(#180)의
몫이라 이 revision은 그 컬럼을 건드리지 않는다. partial/failed build는 row 자체를 남기지
않으므로 별도의 실패 상태 정리 로직이 필요 없다.
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

revision = "168a1b2c3d4e"
down_revision = "166f50617283"
branch_labels = None
depends_on = None

VERSION_TABLE = "rag_candidate_index_version"
MEMBER_TABLE = "rag_candidate_index_member"


def upgrade() -> None:
    op.create_table(
        VERSION_TABLE,
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("index_code", sa.String(120), nullable=False),
        sa.Column("index_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="BUILDING"),
        sa.Column("build_mode", sa.String(20), nullable=False),
        sa.Column("catalog_set_id", sa.CHAR(36), nullable=False),
        sa.Column("catalog_version", sa.String(100), nullable=False),
        sa.Column("catalog_manifest_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(100), nullable=False),
        sa.Column("normalization_version", sa.String(100), nullable=False),
        sa.Column("lexical_config_version", sa.String(100), nullable=False),
        sa.Column("search_order_version", sa.String(100), nullable=False),
        sa.Column("candidate_limit", sa.Integer(), nullable=False),
        sa.Column("display_limit", sa.Integer(), nullable=False),
        sa.Column("embedding_provider", sa.String(120), nullable=True),
        sa.Column("embedding_model", sa.String(120), nullable=True),
        sa.Column("embedding_model_version", sa.String(80), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("distance_metric", sa.String(20), nullable=True),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("product_identity_count", sa.Integer(), nullable=False),
        sa.Column("product_name_count", sa.Integer(), nullable=False),
        sa.Column("approved_alias_count", sa.Integer(), nullable=False),
        sa.Column("vector_count", sa.Integer(), nullable=False),
        sa.Column("member_set_hash", sa.String(64), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rag_candidate_index_version"),
        sa.UniqueConstraint("index_code", "index_version", name="uq_rag_candidate_index_version"),
        sa.UniqueConstraint("content_hash", name="uq_rag_candidate_index_content_hash"),
        sa.ForeignKeyConstraint(
            ["catalog_set_id"],
            ["rag_catalog_set.id"],
            name="fk_rag_candidate_index_version_catalog_set",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('BUILDING', 'READY', 'RETIRED', 'FAILED')", name="chk_rag_candidate_index_status"
        ),
        sa.CheckConstraint("build_mode IN ('LEXICAL_ONLY', 'HYBRID')", name="chk_rag_candidate_index_build_mode"),
        sa.CheckConstraint("length(trim(index_code)) > 0", name="chk_rag_candidate_index_code_nonblank"),
        sa.CheckConstraint("length(trim(index_version)) > 0", name="chk_rag_candidate_index_version_nonblank"),
        sa.CheckConstraint(
            "length(trim(catalog_version)) > 0", name="chk_rag_candidate_index_catalog_version_nonblank"
        ),
        sa.CheckConstraint(
            "catalog_manifest_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_candidate_index_catalog_manifest_hash"
        ),
        sa.CheckConstraint("length(trim(schema_version)) > 0", name="chk_rag_candidate_index_schema_version_nonblank"),
        sa.CheckConstraint(
            "length(trim(normalization_version)) > 0",
            name="chk_rag_candidate_index_normalization_version_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(lexical_config_version)) > 0", name="chk_rag_candidate_index_lexical_config_nonblank"
        ),
        sa.CheckConstraint(
            "length(trim(search_order_version)) > 0", name="chk_rag_candidate_index_search_order_nonblank"
        ),
        sa.CheckConstraint("candidate_limit > 0", name="chk_rag_candidate_index_candidate_limit"),
        sa.CheckConstraint(
            "display_limit > 0 AND display_limit <= candidate_limit", name="chk_rag_candidate_index_display_limit"
        ),
        sa.CheckConstraint("member_count >= 0", name="chk_rag_candidate_index_member_count"),
        sa.CheckConstraint("product_identity_count >= 0", name="chk_rag_candidate_index_product_identity_count"),
        sa.CheckConstraint("product_name_count >= 0", name="chk_rag_candidate_index_product_name_count"),
        sa.CheckConstraint("approved_alias_count >= 0", name="chk_rag_candidate_index_approved_alias_count"),
        sa.CheckConstraint("vector_count >= 0", name="chk_rag_candidate_index_vector_count"),
        sa.CheckConstraint("member_set_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_candidate_index_member_set_hash"),
        sa.CheckConstraint("configuration_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_candidate_index_configuration_hash"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_candidate_index_content_hash"),
        sa.CheckConstraint(
            "(build_mode = 'LEXICAL_ONLY' AND embedding_provider IS NULL AND embedding_model IS NULL "
            "AND embedding_model_version IS NULL AND embedding_dimension IS NULL AND distance_metric IS NULL) OR "
            "(build_mode = 'HYBRID' AND embedding_provider IS NOT NULL AND embedding_model IS NOT NULL "
            "AND embedding_model_version IS NOT NULL AND embedding_dimension IS NOT NULL "
            "AND distance_metric IS NOT NULL)",
            name="chk_rag_candidate_index_build_mode_embedding_shape",
        ),
        sa.CheckConstraint(
            "distance_metric IS NULL OR distance_metric = 'COSINE'", name="chk_rag_candidate_index_distance_metric"
        ),
        sa.CheckConstraint(
            "embedding_dimension IS NULL OR embedding_dimension BETWEEN 1 AND 2000",
            name="chk_rag_candidate_index_embedding_dimension",
        ),
    )
    op.create_index(
        "uq_rag_candidate_index_building_per_code",
        VERSION_TABLE,
        ["index_code"],
        unique=True,
        postgresql_where=sa.text("status = 'BUILDING'"),
    )

    op.create_table(
        MEMBER_TABLE,
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("candidate_index_version_id", sa.CHAR(36), nullable=False),
        sa.Column("entry_type", sa.String(20), nullable=False),
        sa.Column("identity_entity_type", sa.String(20), nullable=False),
        sa.Column("identity_code_system", sa.String(100), nullable=False),
        sa.Column("identity_canonical_code", sa.String(200), nullable=False),
        sa.Column("product_ref", sa.String(200), nullable=False),
        sa.Column("entry_ref", sa.String(200), nullable=False),
        sa.Column("alias_ref", sa.String(200), nullable=True),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("product_name", sa.String(500), nullable=False),
        sa.Column("strength_text", sa.String(255), nullable=True),
        sa.Column("dosage_form", sa.String(255), nullable=True),
        sa.Column("manufacturer_name", sa.String(255), nullable=True),
        sa.Column("product_source_snapshot_id", sa.CHAR(36), nullable=False),
        sa.Column("entry_source_snapshot_id", sa.CHAR(36), nullable=False),
        sa.Column("alias_source_snapshot_id", sa.CHAR(36), nullable=True),
        sa.Column("catalog_version", sa.String(100), nullable=False),
        sa.Column("catalog_manifest_hash", sa.String(64), nullable=False),
        sa.Column("normalization_version", sa.String(100), nullable=False),
        sa.Column("member_key", sa.String(300), nullable=False),
        sa.Column("member_content_hash", sa.String(64), nullable=False),
        sa.Column("embedding", VECTOR(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rag_candidate_index_member"),
        sa.UniqueConstraint("candidate_index_version_id", "member_key", name="uq_rag_candidate_index_member_key"),
        sa.ForeignKeyConstraint(
            ["candidate_index_version_id"],
            [f"{VERSION_TABLE}.id"],
            name="fk_rag_candidate_index_member_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_source_snapshot_id"],
            ["rag_source_snapshot.id"],
            name="fk_rag_candidate_index_member_product_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["entry_source_snapshot_id"],
            ["rag_source_snapshot.id"],
            name="fk_rag_candidate_index_member_entry_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["alias_source_snapshot_id"],
            ["rag_source_snapshot.id"],
            name="fk_rag_candidate_index_member_alias_snapshot",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "entry_type IN ('PRODUCT_NAME', 'APPROVED_ALIAS')", name="chk_rag_candidate_index_member_entry_type"
        ),
        sa.CheckConstraint(
            "identity_entity_type IN ('PRODUCT', 'INGREDIENT')",
            name="chk_rag_candidate_index_member_identity_entity_type",
        ),
        sa.CheckConstraint(
            "length(trim(identity_code_system)) > 0", name="chk_rag_candidate_index_member_identity_code_system"
        ),
        sa.CheckConstraint(
            "length(trim(identity_canonical_code)) > 0",
            name="chk_rag_candidate_index_member_identity_canonical_code",
        ),
        sa.CheckConstraint("length(trim(product_ref)) > 0", name="chk_rag_candidate_index_member_product_ref"),
        sa.CheckConstraint("length(trim(entry_ref)) > 0", name="chk_rag_candidate_index_member_entry_ref"),
        sa.CheckConstraint("length(trim(display_text)) > 0", name="chk_rag_candidate_index_member_display_text"),
        sa.CheckConstraint("length(trim(normalized_text)) > 0", name="chk_rag_candidate_index_member_normalized_text"),
        sa.CheckConstraint("length(trim(product_name)) > 0", name="chk_rag_candidate_index_member_product_name"),
        sa.CheckConstraint("length(trim(catalog_version)) > 0", name="chk_rag_candidate_index_member_catalog_version"),
        sa.CheckConstraint(
            "catalog_manifest_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_candidate_index_member_catalog_manifest_hash"
        ),
        sa.CheckConstraint(
            "length(trim(normalization_version)) > 0",
            name="chk_rag_candidate_index_member_normalization_version",
        ),
        sa.CheckConstraint("length(trim(member_key)) > 0", name="chk_rag_candidate_index_member_key_nonblank"),
        sa.CheckConstraint(
            "member_content_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_candidate_index_member_content_hash"
        ),
        sa.CheckConstraint(
            "(alias_ref IS NULL) = (alias_source_snapshot_id IS NULL)",
            name="chk_rag_candidate_index_member_alias_pair",
        ),
        sa.CheckConstraint(
            "embedding IS NULL OR vector_dims(embedding) BETWEEN 1 AND 2000",
            name="chk_rag_candidate_index_member_embedding_dimension",
        ),
    )
    op.create_index("idx_rag_candidate_index_member_version", MEMBER_TABLE, ["candidate_index_version_id"])


def downgrade() -> None:
    bind = op.get_bind()
    for table in (MEMBER_TABLE, VERSION_TABLE):
        if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar():
            raise RuntimeError("Candidate Index build records exist; downgrade would drop determinism evidence.")
    op.drop_index("idx_rag_candidate_index_member_version", table_name=MEMBER_TABLE)
    op.drop_table(MEMBER_TABLE)
    op.drop_index("uq_rag_candidate_index_building_per_code", table_name=VERSION_TABLE)
    op.drop_table(VERSION_TABLE)
