"""create medication candidate search

Revision ID: 164a1b2c3d4e
Revises: c3f8a12d9e47
Create Date: 2026-09-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "164a1b2c3d4e"
down_revision: str | Sequence[str] | None = "c3f8a12d9e47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CANDIDATE_SEARCH_STATUS_VALUES = (
    "RUNNING",
    "READY",
    "AMBIGUOUS",
    "NO_CANDIDATE",
    "INGREDIENT_ONLY",
    "INVALID_INPUT",
    "INVALIDATED_INPUT_CHANGED",
    "INVALIDATED_USER_REJECTED",
    "EXPIRED",
    "FAILED",
    "CONSUMED",
)

_STATUS_REASON_VALUES = (
    "INVALID_INPUT",
    "MISSING_STRENGTH_MULTIPLE_VARIANTS",
    "ATTRIBUTE_CONFLICT",
    "IDENTIFIER_ATTRIBUTE_CONFLICT",
    "PRODUCT_NAME_REQUIRED",
)

_IDENTIFICATION_STATUS_VALUES = (
    "MATCHED",
    "UNRESOLVED",
)

_IDENTIFICATION_SOURCE_VALUES = (
    "USER_SELECTED",
    "USER_REJECTED",
)

_IDENTIFICATION_DECISION_REASON_VALUES = ("USER_REJECTED_DISPLAYED_CANDIDATE",)


def _sql_in_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "medication_candidate_search",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        # prescription_version_medication은 #169에서 생성된다. #164는 선행 테이블을
        # 중복 생성하지 않는다는 이슈 경계에 따라 우선 값만 저장한다.
        # FK는 prescription_version_medication 구현 이후 별도 migration에서 추가한다.
        sa.Column("prescription_version_medication_id", sa.CHAR(length=36), nullable=False),
        sa.Column("query_digest", sa.String(length=128), nullable=False),
        sa.Column("runtime_release_bundle_id", sa.CHAR(length=36), nullable=True),
        sa.Column("candidate_index_version_id", sa.CHAR(length=36), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                *_CANDIDATE_SEARCH_STATUS_VALUES,
                name="medicationcandidatesearchstatus",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("status_reason", sa.String(length=100), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("displayed_candidate_count", sa.Integer(), nullable=False),
        sa.Column("supersedes_search_id", sa.CHAR(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            f"status IN ({_sql_in_list(_CANDIDATE_SEARCH_STATUS_VALUES)})",
            name="chk_medication_candidate_search_status",
        ),
        sa.CheckConstraint("candidate_count >= 0", name="chk_medication_candidate_search_candidate_count"),
        sa.CheckConstraint(
            "displayed_candidate_count >= 0",
            name="chk_medication_candidate_search_displayed_count",
        ),
        sa.CheckConstraint(
            "displayed_candidate_count <= candidate_count",
            name="chk_medication_candidate_search_displayed_lte_candidate",
        ),
        sa.CheckConstraint(
            "status <> 'READY' OR (candidate_count >= 1 AND displayed_candidate_count = 1)",
            name="chk_medication_candidate_search_ready_counts",
        ),
        sa.CheckConstraint(
            "status <> 'INGREDIENT_ONLY' OR status_reason = 'PRODUCT_NAME_REQUIRED'",
            name="chk_medication_candidate_search_ingredient_reason",
        ),
        sa.CheckConstraint(
            "status <> 'INVALID_INPUT' OR status_reason = 'INVALID_INPUT'",
            name="chk_medication_candidate_search_invalid_input_reason",
        ),
        sa.CheckConstraint(
            f"status_reason IS NULL OR status_reason IN ({_sql_in_list(_STATUS_REASON_VALUES)})",
            name="chk_medication_candidate_search_status_reason",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_search_id"],
            ["medication_candidate_search.id"],
            name="fk_medication_candidate_search_supersedes",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_medication_candidate_search_active",
        "medication_candidate_search",
        ["prescription_version_medication_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('RUNNING', 'READY')"),
    )
    op.create_index(
        "idx_medication_candidate_search_medication_status",
        "medication_candidate_search",
        ["prescription_version_medication_id", "status", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "idx_medication_candidate_search_expires",
        "medication_candidate_search",
        ["expires_at", "id"],
        unique=False,
    )

    op.create_table(
        "medication_candidate_search_result",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("search_id", sa.CHAR(length=36), nullable=False),
        # 공식 제품 Catalog 테이블은 별도 Source/Catalog slice에서 생성된다.
        # 지금은 Candidate Result가 선택한 제품 identity 값을 보존하고,
        # Catalog 테이블 확정 후 product_id FK를 별도 migration에서 추가한다.
        sa.Column("product_id", sa.CHAR(length=36), nullable=True),
        sa.Column("product_name", sa.String(length=255), nullable=True),
        sa.Column("strength_text", sa.String(length=100), nullable=True),
        sa.Column("dosage_form", sa.String(length=100), nullable=True),
        sa.Column("manufacturer_name", sa.String(length=255), nullable=True),
        sa.Column("product_status", sa.String(length=50), nullable=True),
        sa.Column("result_rank", sa.Integer(), nullable=False),
        sa.Column("is_displayed", sa.Boolean(), nullable=False),
        sa.Column("selection_eligible", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("result_rank > 0", name="chk_medication_candidate_result_rank"),
        sa.CheckConstraint(
            "selection_eligible = false OR is_displayed = true",
            name="chk_medication_candidate_result_selectable_displayed",
        ),
        sa.CheckConstraint(
            "is_displayed = false OR (product_id IS NOT NULL AND product_name IS NOT NULL AND product_status IS NOT NULL)",
            name="chk_medication_candidate_result_display_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["search_id"],
            ["medication_candidate_search.id"],
            name="fk_medication_candidate_result_search",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("search_id", "result_rank", name="uq_medication_candidate_result_rank"),
    )
    op.create_index(
        "idx_medication_candidate_result_search",
        "medication_candidate_search_result",
        ["search_id", "result_rank", "id"],
        unique=False,
    )
    op.create_index(
        "idx_medication_candidate_result_product",
        "medication_candidate_search_result",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        "uq_medication_candidate_result_displayed",
        "medication_candidate_search_result",
        ["search_id"],
        unique=True,
        postgresql_where=sa.text("is_displayed = true"),
    )
    op.create_index(
        "uq_medication_candidate_result_selectable",
        "medication_candidate_search_result",
        ["search_id"],
        unique=True,
        postgresql_where=sa.text("selection_eligible = true"),
    )

    op.create_table(
        "medication_identification",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        # prescription_version_medication은 #169에서 생성된다. Candidate Search와 같은 이유로
        # 우선 값만 저장하고, FK는 선행 테이블 구현 이후 별도 migration에서 추가한다.
        sa.Column("prescription_version_medication_id", sa.CHAR(length=36), nullable=False),
        sa.Column("candidate_search_id", sa.CHAR(length=36), nullable=False),
        sa.Column("candidate_search_result_id", sa.CHAR(length=36), nullable=True),
        # 공식 Product Catalog 테이블 확정 전까지 FK 없이 공식 제품 identity 값만 보존한다.
        sa.Column("product_id", sa.CHAR(length=36), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                *_IDENTIFICATION_STATUS_VALUES,
                name="medicationidentificationstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                *_IDENTIFICATION_SOURCE_VALUES,
                name="medicationidentificationsource",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.String(length=100), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            f"status IN ({_sql_in_list(_IDENTIFICATION_STATUS_VALUES)})",
            name="chk_medication_identification_status",
        ),
        sa.CheckConstraint(
            f"source IN ({_sql_in_list(_IDENTIFICATION_SOURCE_VALUES)})",
            name="chk_medication_identification_source",
        ),
        sa.CheckConstraint(
            "status <> 'MATCHED' OR (source = 'USER_SELECTED' AND candidate_search_result_id IS NOT NULL "
            "AND product_id IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="chk_medication_identification_matched_payload",
        ),
        sa.CheckConstraint(
            "status <> 'UNRESOLVED' OR (source = 'USER_REJECTED' AND candidate_search_result_id IS NOT NULL "
            "AND product_id IS NULL AND confirmed_at IS NULL AND rejected_at IS NOT NULL "
            "AND decision_reason = 'USER_REJECTED_DISPLAYED_CANDIDATE')",
            name="chk_medication_identification_unresolved_source",
        ),
        sa.CheckConstraint(
            f"decision_reason IS NULL OR decision_reason IN ({_sql_in_list(_IDENTIFICATION_DECISION_REASON_VALUES)})",
            name="chk_medication_identification_decision_reason",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_search_id"],
            ["medication_candidate_search.id"],
            name="fk_medication_identification_search",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_search_result_id"],
            ["medication_candidate_search_result.id"],
            name="fk_medication_identification_result",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_search_id", name="uq_medication_identification_search"),
    )
    op.create_index(
        "uq_medication_identification_matched",
        "medication_identification",
        ["prescription_version_medication_id"],
        unique=True,
        postgresql_where=sa.text("status = 'MATCHED'"),
    )
    op.create_index(
        "idx_medication_identification_medication_created",
        "medication_identification",
        ["prescription_version_medication_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "idx_medication_identification_search",
        "medication_identification",
        ["candidate_search_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "idx_medication_identification_product",
        "medication_identification",
        ["product_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_medication_identification_matched",
        table_name="medication_identification",
    )
    op.drop_index(
        "idx_medication_identification_product",
        table_name="medication_identification",
    )
    op.drop_index(
        "idx_medication_identification_search",
        table_name="medication_identification",
    )
    op.drop_index(
        "idx_medication_identification_medication_created",
        table_name="medication_identification",
    )
    op.drop_table("medication_identification")
    op.drop_index(
        "uq_medication_candidate_result_selectable",
        table_name="medication_candidate_search_result",
    )
    op.drop_index(
        "uq_medication_candidate_result_displayed",
        table_name="medication_candidate_search_result",
    )
    op.drop_index(
        "idx_medication_candidate_result_product",
        table_name="medication_candidate_search_result",
    )
    op.drop_index(
        "idx_medication_candidate_result_search",
        table_name="medication_candidate_search_result",
    )
    op.drop_table("medication_candidate_search_result")
    op.drop_index(
        "idx_medication_candidate_search_expires",
        table_name="medication_candidate_search",
    )
    op.drop_index(
        "idx_medication_candidate_search_medication_status",
        table_name="medication_candidate_search",
    )
    op.drop_index(
        "uq_medication_candidate_search_active",
        table_name="medication_candidate_search",
    )
    op.drop_table("medication_candidate_search")
