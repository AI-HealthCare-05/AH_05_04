"""create one-cycle ERD tables (medical_document, ocr_job, extracted_field,
prescription, medication, knowledge_document, knowledge_chunk, guide,
guide_citation, chat_session, chat_message, chat_citation)

Revision ID: b7c4e19a2f3d
Revises: decfb71cc060
Create Date: 2026-08-19 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c4e19a2f3d"
down_revision: str | Sequence[str] | None = "decfb71cc060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "medical_document",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("user_id", sa.CHAR(length=36), nullable=False),
        sa.Column(
            "document_type",
            sa.Enum("PRESCRIPTION", name="documenttype", native_enum=False, length=30),
            nullable=False,
        ),
        sa.Column("original_file_name", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=500), nullable=False),
        sa.Column("file_mime_type", sa.String(length=100), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "upload_status",
            sa.Enum("UPLOADED", "FAILED", name="uploadstatus", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("document_type = 'PRESCRIPTION'", name="chk_document_type_prescription"),
        sa.CheckConstraint("upload_status IN ('UPLOADED', 'FAILED')", name="chk_document_upload_status"),
        sa.CheckConstraint("upload_status <> 'FAILED' OR error_code IS NOT NULL", name="chk_document_failed_error"),
        sa.CheckConstraint("file_size_bytes > 0", name="chk_document_file_size"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_document_user_uploaded", "medical_document", ["user_id", "uploaded_at", "id"])

    op.create_table(
        "ocr_job",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("document_id", sa.CHAR(length=36), nullable=False),
        sa.Column(
            "ocr_status",
            sa.Enum("PENDING", "PROCESSING", "COMPLETED", "FAILED", name="ocrstatus", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("engine_name", sa.String(length=100), nullable=True),
        sa.Column("model_version", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("ocr_status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')", name="chk_ocr_status"),
        sa.CheckConstraint(
            "(ocr_status IN ('PENDING', 'PROCESSING') AND completed_at IS NULL) "
            "OR (ocr_status IN ('COMPLETED', 'FAILED') AND completed_at IS NOT NULL)",
            name="chk_ocr_terminal_fields",
        ),
        sa.CheckConstraint("ocr_status <> 'FAILED' OR error_code IS NOT NULL", name="chk_ocr_failed_error"),
        sa.CheckConstraint(
            "ocr_status <> 'COMPLETED' OR (error_code IS NULL AND error_message IS NULL)",
            name="chk_ocr_exclusive_result",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["medical_document.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "document_id", name="uq_ocr_job_id_document"),
    )
    op.create_index("idx_ocr_document_created", "ocr_job", ["document_id", "created_at", "id"])

    op.create_table(
        "extracted_field",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("ocr_job_id", sa.CHAR(length=36), nullable=False),
        sa.Column("medication_index", sa.Integer(), nullable=False),
        sa.Column(
            "field_type",
            sa.Enum(
                "MEDICATION_NAME",
                "DOSE_VALUE",
                "DOSE_UNIT",
                "FREQUENCY_PER_DAY",
                "TIMING",
                "PRESCRIBED_DATE",
                "DURATION_DAYS",
                name="fieldtype",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("raw_value", sa.String(length=1000), nullable=True),
        sa.Column("confirmed_value", sa.String(length=1000), nullable=True),
        sa.Column("confidence_score", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column(
            "confirmation_status",
            sa.Enum("UNCONFIRMED", "CONFIRMED", name="confirmationstatus", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "field_type IN ('MEDICATION_NAME', 'DOSE_VALUE', 'DOSE_UNIT', 'FREQUENCY_PER_DAY', "
            "'TIMING', 'PRESCRIBED_DATE', 'DURATION_DAYS')",
            name="chk_field_type",
        ),
        sa.CheckConstraint("confirmation_status IN ('UNCONFIRMED', 'CONFIRMED')", name="chk_field_confirmation_status"),
        sa.CheckConstraint(
            "(field_type = 'PRESCRIBED_DATE' AND medication_index = 0) "
            "OR (field_type <> 'PRESCRIBED_DATE' AND medication_index > 0)",
            name="chk_field_medication_index",
        ),
        sa.CheckConstraint(
            "(confirmation_status = 'CONFIRMED' AND confirmed_value IS NOT NULL AND confirmed_at IS NOT NULL) "
            "OR (confirmation_status = 'UNCONFIRMED' AND confirmed_value IS NULL AND confirmed_at IS NULL)",
            name="chk_field_confirmation_fields",
        ),
        sa.CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="chk_field_confidence",
        ),
        sa.ForeignKeyConstraint(["ocr_job_id"], ["ocr_job.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ocr_job_id", "medication_index", "field_type", name="uq_extracted_field_identity"),
    )

    op.create_table(
        "prescription",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("document_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_ocr_job_id", sa.CHAR(length=36), nullable=False),
        sa.Column("prescribed_date", sa.Date(), nullable=False),
        sa.Column(
            "prescription_status",
            sa.Enum("CONFIRMED", name="prescriptionstatus", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("prescription_status = 'CONFIRMED'", name="chk_prescription_status"),
        sa.ForeignKeyConstraint(["document_id"], ["medical_document.id"]),
        sa.ForeignKeyConstraint(["source_ocr_job_id"], ["ocr_job.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
    )
    op.create_index("idx_prescription_source_ocr", "prescription", ["source_ocr_job_id"])

    op.create_table(
        "medication",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("prescription_id", sa.CHAR(length=36), nullable=False),
        sa.Column("medication_name", sa.String(length=255), nullable=False),
        sa.Column("dose_value", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("dose_unit", sa.String(length=50), nullable=True),
        sa.Column("frequency_per_day", sa.Integer(), nullable=True),
        sa.Column("timing_text", sa.String(length=255), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("dose_value IS NULL OR dose_value > 0", name="chk_medication_dose"),
        sa.CheckConstraint("frequency_per_day IS NULL OR frequency_per_day > 0", name="chk_medication_frequency"),
        sa.CheckConstraint("duration_days IS NULL OR duration_days > 0", name="chk_medication_duration"),
        sa.CheckConstraint("display_order > 0", name="chk_medication_display_order"),
        sa.ForeignKeyConstraint(["prescription_id"], ["prescription.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prescription_id", "display_order", name="uq_medication_prescription_order"),
    )

    op.create_table(
        "knowledge_document",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("document_version", sa.String(length=100), nullable=False),
        sa.Column(
            "document_status",
            sa.Enum("ACTIVE", "INACTIVE", name="knowledgedocumentstatus", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("document_status IN ('ACTIVE', 'INACTIVE')", name="chk_knowledge_document_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_url", "document_version", name="uq_knowledge_document_source_version"),
    )

    op.create_table(
        "knowledge_chunk",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("knowledge_document_id", sa.CHAR(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=False),
        sa.Column("vector_store_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("chunk_index >= 0", name="chk_knowledge_chunk_index"),
        sa.ForeignKeyConstraint(["knowledge_document_id"], ["knowledge_document.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_document_id", "chunk_index", name="uq_knowledge_chunk_order"),
        sa.UniqueConstraint("vector_store_key"),
    )

    op.create_table(
        "guide",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("prescription_id", sa.CHAR(length=36), nullable=False),
        sa.Column(
            "generation_status",
            sa.Enum(
                "PENDING",
                "GENERATING",
                "COMPLETED",
                "FAILED",
                name="guidegenerationstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "generation_status IN ('PENDING', 'GENERATING', 'COMPLETED', 'FAILED')",
            name="chk_guide_generation_status",
        ),
        sa.ForeignKeyConstraint(["prescription_id"], ["prescription.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_guide_prescription_requested", "guide", ["prescription_id", "requested_at", "id"])

    op.create_table(
        "guide_citation",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("guide_id", sa.CHAR(length=36), nullable=False),
        sa.Column("knowledge_chunk_id", sa.CHAR(length=36), nullable=False),
        sa.Column("claim_text", sa.String(length=1000), nullable=False),
        sa.Column("cited_text", sa.String(length=2000), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("display_order > 0", name="chk_guide_citation_order"),
        sa.ForeignKeyConstraint(["guide_id"], ["guide.id"]),
        sa.ForeignKeyConstraint(["knowledge_chunk_id"], ["knowledge_chunk.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guide_id", "display_order", name="uq_guide_citation_order"),
    )

    op.create_table(
        "chat_session",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("prescription_id", sa.CHAR(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column(
            "session_status",
            sa.Enum("ACTIVE", "CLOSED", name="chatsessionstatus", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("session_status IN ('ACTIVE', 'CLOSED')", name="chk_chat_session_status"),
        sa.ForeignKeyConstraint(["prescription_id"], ["prescription.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_chat_session_prescription_activity",
        "chat_session",
        ["prescription_id", "session_status", "last_message_at", "id"],
    )

    op.create_table(
        "chat_message",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("session_id", sa.CHAR(length=36), nullable=False),
        sa.Column("message_seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.Enum("USER", "ASSISTANT", name="chatrole", native_enum=False, length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "generation_status",
            sa.Enum(
                "NOT_APPLICABLE",
                "PENDING",
                "GENERATING",
                "COMPLETED",
                "FAILED",
                name="chatgenerationstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('USER', 'ASSISTANT')", name="chk_chat_message_role"),
        sa.CheckConstraint(
            "generation_status IN ('NOT_APPLICABLE', 'PENDING', 'GENERATING', 'COMPLETED', 'FAILED')",
            name="chk_chat_message_generation_status",
        ),
        sa.CheckConstraint("message_seq > 0", name="chk_chat_message_seq"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_session.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "message_seq", name="uq_chat_message_session_seq"),
    )

    op.create_table(
        "chat_citation",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("message_id", sa.CHAR(length=36), nullable=False),
        sa.Column("knowledge_chunk_id", sa.CHAR(length=36), nullable=False),
        sa.Column("claim_text", sa.String(length=1000), nullable=False),
        sa.Column("cited_text", sa.String(length=2000), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("display_order > 0", name="chk_chat_citation_order"),
        sa.ForeignKeyConstraint(["message_id"], ["chat_message.id"]),
        sa.ForeignKeyConstraint(["knowledge_chunk_id"], ["knowledge_chunk.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "display_order", name="uq_chat_citation_order"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("chat_citation")
    op.drop_table("chat_message")
    op.drop_index("idx_chat_session_prescription_activity", table_name="chat_session")
    op.drop_table("chat_session")
    op.drop_table("guide_citation")
    op.drop_index("idx_guide_prescription_requested", table_name="guide")
    op.drop_table("guide")
    op.drop_table("knowledge_chunk")
    op.drop_table("knowledge_document")
    op.drop_table("medication")
    op.drop_index("idx_prescription_source_ocr", table_name="prescription")
    op.drop_table("prescription")
    op.drop_table("extracted_field")
    op.drop_index("idx_ocr_document_created", table_name="ocr_job")
    op.drop_table("ocr_job")
    op.drop_index("idx_document_user_uploaded", table_name="medical_document")
    op.drop_table("medical_document")
