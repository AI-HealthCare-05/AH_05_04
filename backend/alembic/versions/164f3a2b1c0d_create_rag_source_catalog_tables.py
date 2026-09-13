"""create rag source catalog tables

Revision ID: 164f3a2b1c0d
Revises: 171c0f751206
Create Date: 2026-09-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "164f3a2b1c0d"
down_revision: str | Sequence[str] | None = "171c0f751206"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_LIFECYCLE_STATUS_VALUES = ("DRAFT", "ACTIVE", "RETIRED", "REVOKED")
_ENDPOINT_LIFECYCLE_STATUS_VALUES = ("DRAFT", "VERIFIED", "RETIRED", "REVOKED")
_SOURCE_USAGE_STATUS_VALUES = ("DISABLED", "ENABLED")
_SOURCE_APPROVAL_STATUS_VALUES = ("PENDING", "APPROVED", "REVOKED")
_SNAPSHOT_VERIFICATION_STATUS_VALUES = ("PENDING", "CURRENT", "STALE", "FAILED")
_INGESTION_RUN_STATUS_VALUES = ("RUNNING", "SUCCEEDED", "SUCCEEDED_WITH_REJECTIONS", "NO_CHANGE", "FAILED")
_VERIFICATION_RESULT_STATUS_VALUES = ("PASSED", "FAILED", "NO_CHANGE")
_ALIAS_TARGET_TYPE_VALUES = ("PRODUCT", "INGREDIENT")
_COMPONENT_ROLE_VALUES = ("ACTIVE_INGREDIENT", "EXCIPIENT", "UNKNOWN")


def _sql_in_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


_RAG_SOURCE_CATALOG_TABLES = (
    "rag_medication_product_component",
    "rag_medication_alias",
    "rag_medication_ingredient",
    "rag_medication_product",
    "rag_source_snapshot_verification",
    "rag_source_ingestion_run",
    "rag_source_snapshot",
    "rag_source_operation",
    "rag_source_endpoint",
    "rag_source",
)

_RAG_SOURCE_SNAPSHOT_IMMUTABLE_COLUMNS = (
    "operation_id",
    "source_version",
    "raw_manifest_checksum",
    "canonical_checksum",
    "schema_version",
    "parser_version",
    "normalization_version",
    "canonicalization_spec_version",
    "record_count",
    "rejected_record_count",
    "collected_at",
    "supersedes_snapshot_id",
    "created_at",
)


def _ensure_downgrade_is_data_safe(connection: sa.engine.Connection) -> None:
    for table_name in _RAG_SOURCE_CATALOG_TABLES:
        connection.execute(sa.text(f"LOCK TABLE {table_name} IN ACCESS EXCLUSIVE MODE"))

    non_empty_tables: list[str] = []
    for table_name in _RAG_SOURCE_CATALOG_TABLES:
        count = connection.execute(sa.text(f"SELECT count(*) FROM {table_name}")).scalar_one()
        if count:
            non_empty_tables.append(table_name)

    if non_empty_tables:
        joined_tables = ", ".join(non_empty_tables)
        raise RuntimeError(
            "Cannot downgrade revision 164f3a2b1c0d while RAG Source/Catalog data exists. "
            f"Non-empty tables: {joined_tables}. Use a forward-fix migration or an approved backup and "
            "data-retention rollback procedure instead."
        )


def _create_snapshot_immutability_guard() -> None:
    immutable_checks = " OR\n                ".join(
        f"OLD.{column_name} IS DISTINCT FROM NEW.{column_name}"
        for column_name in _RAG_SOURCE_SNAPSHOT_IMMUTABLE_COLUMNS
    )
    op.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION prevent_rag_source_snapshot_mutation()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'rag_source_snapshot rows are append-only; use a forward-fix snapshot instead';
                END IF;

                IF {immutable_checks} THEN
                    RAISE EXCEPTION 'rag_source_snapshot immutable fields cannot be updated; append verification rows or create a new snapshot';
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_rag_source_snapshot_prevent_update
            BEFORE UPDATE ON rag_source_snapshot
            FOR EACH ROW
            EXECUTE FUNCTION prevent_rag_source_snapshot_mutation()
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_rag_source_snapshot_prevent_delete
            BEFORE DELETE ON rag_source_snapshot
            FOR EACH ROW
            EXECUTE FUNCTION prevent_rag_source_snapshot_mutation()
            """
        )
    )


def upgrade() -> None:
    op.create_table(
        "rag_source",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_code", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("owner_name", sa.String(length=255), nullable=True),
        sa.Column("license_name", sa.String(length=255), nullable=True),
        sa.Column("attribution_text", sa.Text(), nullable=True),
        sa.Column("purpose", sa.String(length=255), nullable=True),
        sa.Column("lifecycle_status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(source_code)) > 0", name="chk_rag_source_code_nonblank"),
        sa.CheckConstraint("length(trim(display_name)) > 0", name="chk_rag_source_display_name_nonblank"),
        sa.CheckConstraint(
            f"lifecycle_status IN ({_sql_in_list(_SOURCE_LIFECYCLE_STATUS_VALUES)})",
            name="chk_rag_source_lifecycle_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_code", name="uq_rag_source_code"),
    )

    op.create_table(
        "rag_source_endpoint",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_id", sa.CHAR(length=36), nullable=False),
        sa.Column("endpoint_code", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("official_url", sa.String(length=500), nullable=True),
        sa.Column("lifecycle_status", sa.String(length=20), nullable=False),
        sa.Column("runtime_status", sa.String(length=20), nullable=False),
        sa.Column("acquisition_status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(endpoint_code)) > 0", name="chk_rag_source_endpoint_code_nonblank"),
        sa.CheckConstraint("length(trim(display_name)) > 0", name="chk_rag_source_endpoint_display_name_nonblank"),
        sa.CheckConstraint(
            f"lifecycle_status IN ({_sql_in_list(_ENDPOINT_LIFECYCLE_STATUS_VALUES)})",
            name="chk_rag_source_endpoint_lifecycle_status",
        ),
        sa.CheckConstraint(
            f"runtime_status IN ({_sql_in_list(_SOURCE_USAGE_STATUS_VALUES)})",
            name="chk_rag_source_endpoint_runtime_status",
        ),
        sa.CheckConstraint(
            f"acquisition_status IN ({_sql_in_list(_SOURCE_APPROVAL_STATUS_VALUES)})",
            name="chk_rag_source_endpoint_acquisition_status",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["rag_source.id"], name="fk_rag_source_endpoint_source"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "endpoint_code", name="uq_rag_source_endpoint_code"),
    )

    op.create_index("idx_rag_source_endpoint_source", "rag_source_endpoint", ["source_id"])

    op.create_table(
        "rag_source_operation",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("endpoint_id", sa.CHAR(length=36), nullable=False),
        sa.Column("operation_code", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("runtime_status", sa.String(length=20), nullable=False),
        sa.Column("acquisition_status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(operation_code)) > 0", name="chk_rag_source_operation_code_nonblank"),
        sa.CheckConstraint("length(trim(display_name)) > 0", name="chk_rag_source_operation_display_name_nonblank"),
        sa.CheckConstraint(
            f"runtime_status IN ({_sql_in_list(_SOURCE_USAGE_STATUS_VALUES)})",
            name="chk_rag_source_operation_runtime_status",
        ),
        sa.CheckConstraint(
            f"acquisition_status IN ({_sql_in_list(_SOURCE_APPROVAL_STATUS_VALUES)})",
            name="chk_rag_source_operation_acquisition_status",
        ),
        sa.ForeignKeyConstraint(["endpoint_id"], ["rag_source_endpoint.id"], name="fk_rag_source_operation_endpoint"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint_id", "operation_code", name="uq_rag_source_operation_code"),
    )

    op.create_index("idx_rag_source_operation_endpoint", "rag_source_operation", ["endpoint_id"])

    op.create_table(
        "rag_source_snapshot",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("operation_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_version", sa.String(length=255), nullable=False),
        sa.Column("raw_manifest_checksum", sa.String(length=64), nullable=False),
        sa.Column("canonical_checksum", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("parser_version", sa.String(length=100), nullable=False),
        sa.Column("normalization_version", sa.String(length=100), nullable=False),
        sa.Column("canonicalization_spec_version", sa.String(length=100), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("rejected_record_count", sa.Integer(), nullable=False),
        sa.Column("verification_status", sa.String(length=20), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_snapshot_id", sa.CHAR(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(source_version)) > 0", name="chk_rag_source_snapshot_version_nonblank"),
        sa.CheckConstraint("length(raw_manifest_checksum) = 64", name="chk_rag_source_snapshot_raw_manifest_checksum"),
        sa.CheckConstraint("length(canonical_checksum) = 64", name="chk_rag_source_snapshot_canonical_checksum"),
        sa.CheckConstraint("record_count >= 0", name="chk_rag_source_snapshot_record_count"),
        sa.CheckConstraint("rejected_record_count >= 0", name="chk_rag_source_snapshot_rejected_record_count"),
        sa.CheckConstraint(
            "rejected_record_count <= record_count",
            name="chk_rag_source_snapshot_rejected_record_count_lte_record_count",
        ),
        sa.CheckConstraint(
            f"verification_status IN ({_sql_in_list(_SNAPSHOT_VERIFICATION_STATUS_VALUES)})",
            name="chk_rag_source_snapshot_verification_status",
        ),
        sa.ForeignKeyConstraint(["operation_id"], ["rag_source_operation.id"], name="fk_rag_source_snapshot_operation"),
        sa.ForeignKeyConstraint(
            ["supersedes_snapshot_id"],
            ["rag_source_snapshot.id"],
            name="fk_rag_source_snapshot_supersedes",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", "source_version", name="uq_rag_source_snapshot_operation_version"),
    )

    op.create_index(
        "idx_rag_source_snapshot_operation_status",
        "rag_source_snapshot",
        ["operation_id", "verification_status"],
    )
    op.create_index(
        "uq_rag_source_snapshot_current",
        "rag_source_snapshot",
        ["operation_id"],
        unique=True,
        postgresql_where=sa.text("verification_status = 'CURRENT'"),
    )

    op.create_table(
        "rag_source_ingestion_run",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("operation_id", sa.CHAR(length=36), nullable=False),
        sa.Column("run_group_key", sa.String(length=100), nullable=False),
        sa.Column("snapshot_id", sa.CHAR(length=36), nullable=True),
        sa.Column("run_status", sa.String(length=40), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.String(length=255), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(run_group_key)) > 0", name="chk_rag_source_ingestion_run_group_key_nonblank"),
        sa.CheckConstraint("attempt_number > 0", name="chk_rag_source_ingestion_run_attempt_positive"),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="chk_rag_source_ingestion_run_duration"),
        sa.CheckConstraint(
            f"run_status IN ({_sql_in_list(_INGESTION_RUN_STATUS_VALUES)})",
            name="chk_rag_source_ingestion_run_status",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"], ["rag_source_operation.id"], name="fk_rag_source_ingestion_run_operation"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["rag_source_snapshot.id"], name="fk_rag_source_ingestion_run_snapshot"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_id", "run_group_key", "attempt_number", name="uq_rag_source_ingestion_run_attempt"
        ),
    )

    op.create_index(
        "idx_rag_source_ingestion_run_operation_status",
        "rag_source_ingestion_run",
        ["operation_id", "run_status", "started_at"],
    )

    op.create_table(
        "rag_source_snapshot_verification",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("check_name", sa.String(length=100), nullable=False),
        sa.Column("verification_result", sa.String(length=20), nullable=False),
        sa.Column("details_summary", sa.String(length=255), nullable=True),
        sa.Column("verified_by", sa.String(length=100), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(check_name)) > 0", name="chk_rag_source_snapshot_verification_check_nonblank"),
        sa.CheckConstraint(
            f"verification_result IN ({_sql_in_list(_VERIFICATION_RESULT_STATUS_VALUES)})",
            name="chk_rag_source_snapshot_verification_result",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["rag_source_snapshot.id"], name="fk_rag_source_snapshot_verification_snapshot"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "idx_rag_source_snapshot_verification_snapshot",
        "rag_source_snapshot_verification",
        ["snapshot_id", "verified_at"],
    )

    op.create_table(
        "rag_medication_product",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_record_key", sa.String(length=255), nullable=False),
        sa.Column("code_system", sa.String(length=50), nullable=False),
        sa.Column("canonical_code", sa.String(length=100), nullable=False),
        sa.Column("product_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_product_name", sa.String(length=255), nullable=False),
        sa.Column("strength_text", sa.String(length=100), nullable=True),
        sa.Column("dosage_form", sa.String(length=100), nullable=True),
        sa.Column("manufacturer_name", sa.String(length=255), nullable=True),
        sa.Column("product_status", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "length(trim(source_record_key)) > 0", name="chk_rag_medication_product_record_key_nonblank"
        ),
        sa.CheckConstraint("length(trim(code_system)) > 0", name="chk_rag_medication_product_code_system_nonblank"),
        sa.CheckConstraint("length(trim(canonical_code)) > 0", name="chk_rag_medication_product_code_nonblank"),
        sa.CheckConstraint("length(trim(product_name)) > 0", name="chk_rag_medication_product_name_nonblank"),
        sa.CheckConstraint(
            "length(trim(normalized_product_name)) > 0",
            name="chk_rag_medication_product_normalized_name_nonblank",
        ),
        sa.CheckConstraint("length(trim(product_status)) > 0", name="chk_rag_medication_product_status_nonblank"),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["rag_source_snapshot.id"], name="fk_rag_medication_product_snapshot"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_snapshot_id", "code_system", "canonical_code", name="uq_rag_medication_product_snapshot_identity"
        ),
        sa.UniqueConstraint(
            "source_snapshot_id", "source_record_key", name="uq_rag_medication_product_snapshot_record"
        ),
        sa.UniqueConstraint("id", "source_snapshot_id", name="uq_rag_medication_product_id_snapshot"),
    )
    op.create_index("idx_rag_medication_product_identity", "rag_medication_product", ["code_system", "canonical_code"])
    op.create_index("idx_rag_medication_product_snapshot", "rag_medication_product", ["source_snapshot_id"])

    op.create_table(
        "rag_medication_ingredient",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_record_key", sa.String(length=255), nullable=False),
        sa.Column("ingredient_code_system", sa.String(length=50), nullable=True),
        sa.Column("ingredient_code", sa.String(length=100), nullable=True),
        sa.Column("ingredient_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_ingredient_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "length(trim(source_record_key)) > 0", name="chk_rag_medication_ingredient_record_key_nonblank"
        ),
        sa.CheckConstraint("length(trim(ingredient_name)) > 0", name="chk_rag_medication_ingredient_name_nonblank"),
        sa.CheckConstraint(
            "length(trim(normalized_ingredient_name)) > 0",
            name="chk_rag_medication_ingredient_normalized_name_nonblank",
        ),
        sa.CheckConstraint(
            "ingredient_code IS NULL OR length(trim(ingredient_code)) > 0",
            name="chk_rag_medication_ingredient_code_nonblank",
        ),
        sa.CheckConstraint(
            "ingredient_code IS NULL OR ingredient_code_system IS NOT NULL",
            name="chk_rag_medication_ingredient_code_system_required",
        ),
        sa.CheckConstraint(
            "ingredient_code_system IS NULL OR length(trim(ingredient_code_system)) > 0",
            name="chk_rag_medication_ingredient_code_system_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["rag_source_snapshot.id"], name="fk_rag_medication_ingredient_snapshot"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_snapshot_id", "normalized_ingredient_name", name="uq_rag_medication_ingredient_snapshot_name"
        ),
        sa.UniqueConstraint(
            "source_snapshot_id", "source_record_key", name="uq_rag_medication_ingredient_snapshot_record"
        ),
        sa.UniqueConstraint("id", "source_snapshot_id", name="uq_rag_medication_ingredient_id_snapshot"),
    )
    op.create_index("idx_rag_medication_ingredient_snapshot", "rag_medication_ingredient", ["source_snapshot_id"])
    op.create_index(
        "uq_rag_medication_ingredient_snapshot_code",
        "rag_medication_ingredient",
        ["source_snapshot_id", "ingredient_code_system", "ingredient_code"],
        unique=True,
        postgresql_where=sa.text("ingredient_code IS NOT NULL"),
    )

    op.create_table(
        "rag_medication_alias",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("product_id", sa.CHAR(length=36), nullable=True),
        sa.Column("ingredient_id", sa.CHAR(length=36), nullable=True),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("alias_text", sa.String(length=255), nullable=False),
        sa.Column("normalized_alias_text", sa.String(length=255), nullable=False),
        sa.Column("is_approved", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(alias_text)) > 0", name="chk_rag_medication_alias_text_nonblank"),
        sa.CheckConstraint(
            "length(trim(normalized_alias_text)) > 0",
            name="chk_rag_medication_alias_normalized_text_nonblank",
        ),
        sa.CheckConstraint(
            "(product_id IS NOT NULL AND ingredient_id IS NULL AND target_type = 'PRODUCT') OR "
            "(product_id IS NULL AND ingredient_id IS NOT NULL AND target_type = 'INGREDIENT')",
            name="chk_rag_medication_alias_single_target",
        ),
        sa.CheckConstraint(
            f"target_type IN ({_sql_in_list(_ALIAS_TARGET_TYPE_VALUES)})",
            name="chk_rag_medication_alias_target_type",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["rag_source_snapshot.id"], name="fk_rag_medication_alias_snapshot"
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "source_snapshot_id"],
            ["rag_medication_product.id", "rag_medication_product.source_snapshot_id"],
            name="fk_rag_medication_alias_product_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["ingredient_id", "source_snapshot_id"],
            ["rag_medication_ingredient.id", "rag_medication_ingredient.source_snapshot_id"],
            name="fk_rag_medication_alias_ingredient_snapshot",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_rag_medication_alias_snapshot", "rag_medication_alias", ["source_snapshot_id"])
    op.create_index(
        "uq_rag_medication_alias_product",
        "rag_medication_alias",
        ["product_id", "normalized_alias_text"],
        unique=True,
        postgresql_where=sa.text("product_id IS NOT NULL"),
    )
    op.create_index(
        "uq_rag_medication_alias_ingredient",
        "rag_medication_alias",
        ["ingredient_id", "normalized_alias_text"],
        unique=True,
        postgresql_where=sa.text("ingredient_id IS NOT NULL"),
    )

    op.create_table(
        "rag_medication_product_component",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("product_id", sa.CHAR(length=36), nullable=False),
        sa.Column("ingredient_id", sa.CHAR(length=36), nullable=False),
        sa.Column("component_role", sa.String(length=30), nullable=False),
        sa.Column("amount_value", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("amount_unit", sa.String(length=50), nullable=True),
        sa.Column("amount_text", sa.String(length=100), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("display_order > 0", name="chk_rag_medication_component_display_order"),
        sa.CheckConstraint(
            "amount_value IS NULL OR amount_value >= 0", name="chk_rag_medication_component_amount_value"
        ),
        sa.CheckConstraint(
            "amount_unit IS NULL OR length(trim(amount_unit)) > 0",
            name="chk_rag_medication_component_amount_unit_nonblank",
        ),
        sa.CheckConstraint(
            "amount_text IS NULL OR length(trim(amount_text)) > 0",
            name="chk_rag_medication_component_amount_text_nonblank",
        ),
        sa.CheckConstraint(
            f"component_role IN ({_sql_in_list(_COMPONENT_ROLE_VALUES)})",
            name="chk_rag_medication_component_role",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["rag_source_snapshot.id"], name="fk_rag_medication_component_snapshot"
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "source_snapshot_id"],
            ["rag_medication_product.id", "rag_medication_product.source_snapshot_id"],
            name="fk_rag_medication_component_product_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["ingredient_id", "source_snapshot_id"],
            ["rag_medication_ingredient.id", "rag_medication_ingredient.source_snapshot_id"],
            name="fk_rag_medication_component_ingredient_snapshot",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "ingredient_id", "component_role", name="uq_rag_medication_component_role"),
    )
    op.create_index("idx_rag_medication_component_snapshot", "rag_medication_product_component", ["source_snapshot_id"])
    op.create_index("idx_rag_medication_component_ingredient", "rag_medication_product_component", ["ingredient_id"])

    _create_snapshot_immutability_guard()


def downgrade() -> None:
    _ensure_downgrade_is_data_safe(op.get_bind())

    op.drop_index("idx_rag_medication_component_ingredient", table_name="rag_medication_product_component")
    op.drop_index("idx_rag_medication_component_snapshot", table_name="rag_medication_product_component")
    op.drop_table("rag_medication_product_component")
    op.drop_index("uq_rag_medication_alias_ingredient", table_name="rag_medication_alias")
    op.drop_index("uq_rag_medication_alias_product", table_name="rag_medication_alias")
    op.drop_index("idx_rag_medication_alias_snapshot", table_name="rag_medication_alias")
    op.drop_table("rag_medication_alias")
    op.drop_index("uq_rag_medication_ingredient_snapshot_code", table_name="rag_medication_ingredient")
    op.drop_index("idx_rag_medication_ingredient_snapshot", table_name="rag_medication_ingredient")
    op.drop_table("rag_medication_ingredient")
    op.drop_index("idx_rag_medication_product_snapshot", table_name="rag_medication_product")
    op.drop_index("idx_rag_medication_product_identity", table_name="rag_medication_product")
    op.drop_table("rag_medication_product")
    op.drop_index("idx_rag_source_snapshot_verification_snapshot", table_name="rag_source_snapshot_verification")
    op.drop_table("rag_source_snapshot_verification")
    op.drop_index("idx_rag_source_ingestion_run_operation_status", table_name="rag_source_ingestion_run")
    op.drop_table("rag_source_ingestion_run")
    op.execute("DROP TRIGGER IF EXISTS trg_rag_source_snapshot_prevent_delete ON rag_source_snapshot")
    op.execute("DROP TRIGGER IF EXISTS trg_rag_source_snapshot_prevent_update ON rag_source_snapshot")
    op.execute("DROP FUNCTION IF EXISTS prevent_rag_source_snapshot_mutation()")
    op.execute("DROP INDEX IF EXISTS uq_rag_source_snapshot_current")
    op.drop_index("idx_rag_source_snapshot_operation_status", table_name="rag_source_snapshot")
    op.drop_table("rag_source_snapshot")
    op.drop_index("idx_rag_source_operation_endpoint", table_name="rag_source_operation")
    op.drop_table("rag_source_operation")
    op.drop_index("idx_rag_source_endpoint_source", table_name="rag_source_endpoint")
    op.drop_table("rag_source_endpoint")
    op.drop_table("rag_source")
