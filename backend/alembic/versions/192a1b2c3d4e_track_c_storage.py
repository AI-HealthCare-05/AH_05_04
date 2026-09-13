"""Track C confirmed storage foundation (#192); no HandlerConfig schema.

Revision ID: 192a1b2c3d4e
Revises: 166f30415263
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "192a1b2c3d4e"
down_revision = "166f30415263"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "safety_assessment",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("medication_checkin_id", sa.CHAR(length=36), nullable=False),
        sa.Column("checkin_revision", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("symptom_codes", postgresql.JSONB(), nullable=False),
        sa.Column("response_level", sa.String(length=20), nullable=False),
        sa.Column("safety_disposition", sa.String(length=20), nullable=False),
        sa.Column("message_code", sa.String(length=100), nullable=False),
        sa.Column("copy_version", sa.String(length=100), nullable=False),
        sa.Column("source_version", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(response_level = 'ROUTINE' AND safety_disposition = 'NORMAL') OR (response_level = 'URGENT' AND safety_disposition = 'URGENT_ROUTED') OR (response_level = 'EMERGENCY' AND safety_disposition = 'EMERGENCY_ROUTED') OR (response_level = 'UNKNOWN' AND safety_disposition = 'UNKNOWN_RISK') OR safety_disposition = 'BLOCKED_ACTION'",
            name="chk_safety_disposition",
        ),
        sa.CheckConstraint("jsonb_typeof(symptom_codes) = 'array'", name="chk_safety_symptoms_array"),
        sa.CheckConstraint("response_level IN ('ROUTINE', 'URGENT', 'EMERGENCY', 'UNKNOWN')", name="chk_safety_level"),
        sa.CheckConstraint("checkin_revision > 0 AND revision > 0", name="chk_safety_revisions"),
        sa.ForeignKeyConstraint(
            ["medication_checkin_id"], ["medication_checkin.id"], name="fk_safety_checkin", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "medication_checkin_id", "checkin_revision", name="uq_safety_parent_reference"),
        sa.UniqueConstraint("medication_checkin_id", "checkin_revision", "revision", name="uq_safety_checkin_revision"),
    )
    op.create_table(
        "barrier_response",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("medication_checkin_id", sa.CHAR(length=36), nullable=False),
        sa.Column("checkin_revision", sa.Integer(), nullable=False),
        sa.Column("safety_assessment_id", sa.CHAR(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("response_status", sa.String(length=20), nullable=False),
        sa.Column("barrier_code", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(response_status = 'ANSWERED' AND barrier_code IS NOT NULL) OR (response_status = 'DECLINED' AND barrier_code IS NULL)",
            name="chk_barrier_response",
        ),
        sa.CheckConstraint(
            "barrier_code IN ('FORGOT', 'SCHEDULE_OR_TRAVEL', 'INSTRUCTIONS_UNCLEAR', 'NEED_DOUBT', 'MEDICATION_CONCERN', 'ACCESS_OR_COST')",
            name="chk_barrier_code",
        ),
        sa.CheckConstraint("checkin_revision > 0 AND revision > 0", name="chk_barrier_revisions"),
        sa.ForeignKeyConstraint(
            ["safety_assessment_id", "medication_checkin_id", "checkin_revision"],
            ["safety_assessment.id", "safety_assessment.medication_checkin_id", "safety_assessment.checkin_revision"],
            name="fk_barrier_safety_reference",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "medication_checkin_id", "checkin_revision", "revision", name="uq_barrier_checkin_revision"
        ),
    )
    op.create_table(
        "support_action_plan",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("barrier_response_id", sa.CHAR(length=36), nullable=False),
        sa.Column("support_code", sa.String(length=40), nullable=False),
        sa.Column("rule_version", sa.String(length=100), nullable=False),
        sa.Column("copy_version", sa.String(length=100), nullable=False),
        sa.Column("action_config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND completed_at IS NULL AND cancelled_at IS NULL) OR (status = 'COMPLETED' AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR (status = 'CANCELLED' AND cancelled_at IS NOT NULL AND completed_at IS NULL)",
            name="chk_action_plan_state",
        ),
        sa.CheckConstraint("jsonb_typeof(action_config_snapshot) = 'object'", name="chk_action_plan_snapshot"),
        sa.CheckConstraint(
            "support_code IN ('REMINDER_SETUP', 'ROUTINE_OR_TRAVEL_PLAN', 'INSTRUCTION_REVIEW', 'PURPOSE_REVIEW', 'MEDICATION_CONCERN_GUIDANCE', 'ACCESS_SUPPORT')",
            name="chk_action_plan_support",
        ),
        sa.ForeignKeyConstraint(
            ["barrier_response_id"], ["barrier_response.id"], name="fk_action_plan_barrier", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_action_plan_barrier", "support_action_plan", ["barrier_response_id"], unique=False)
    op.create_index(
        "uq_action_plan_active_barrier",
        "support_action_plan",
        ["barrier_response_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "action_plan_followup",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("support_action_plan_id", sa.CHAR(length=36), nullable=False),
        sa.Column("response", sa.String(length=20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("response IN ('HELPED', 'NOT_HELPED', 'NOT_SURE')", name="chk_followup_response"),
        sa.CheckConstraint("revision > 0", name="chk_followup_revision"),
        sa.ForeignKeyConstraint(
            ["support_action_plan_id"], ["support_action_plan.id"], name="fk_followup_action_plan", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("support_action_plan_id", name="uq_followup_action_plan"),
    )
    op.create_table(
        "action_plan_followup_audit",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("followup_id", sa.CHAR(length=36), nullable=False),
        sa.Column("from_response", sa.String(length=20), nullable=False),
        sa.Column("to_response", sa.String(length=20), nullable=False),
        sa.Column("from_revision", sa.Integer(), nullable=False),
        sa.Column("to_revision", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.CHAR(length=36), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "from_response IN ('HELPED', 'NOT_HELPED', 'NOT_SURE') AND to_response IN ('HELPED', 'NOT_HELPED', 'NOT_SURE')",
            name="chk_followup_audit_response",
        ),
        sa.CheckConstraint("from_revision > 0 AND to_revision = from_revision + 1", name="chk_followup_audit_revision"),
        sa.ForeignKeyConstraint(["changed_by"], ["user.id"], name="fk_followup_audit_actor", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["followup_id"], ["action_plan_followup.id"], name="fk_followup_audit_followup", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("followup_id", "to_revision", name="uq_followup_audit_revision"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    # Hold all tables until the transaction ends to exclude writes during the guard.
    connection.execute(
        sa.text(
            "LOCK TABLE safety_assessment, barrier_response, support_action_plan, action_plan_followup, action_plan_followup_audit IN ACCESS EXCLUSIVE MODE"
        )
    )
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM safety_assessment)")).scalar_one():
        raise RuntimeError("Track C history exists; downgrade refused, use forward-fix")
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM barrier_response)")).scalar_one():
        raise RuntimeError("Track C history exists; downgrade refused, use forward-fix")
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM support_action_plan)")).scalar_one():
        raise RuntimeError("Track C history exists; downgrade refused, use forward-fix")
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM action_plan_followup)")).scalar_one():
        raise RuntimeError("Track C history exists; downgrade refused, use forward-fix")
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM action_plan_followup_audit)")).scalar_one():
        raise RuntimeError("Track C history exists; downgrade refused, use forward-fix")
    op.drop_table("action_plan_followup_audit")
    op.drop_table("action_plan_followup")
    op.drop_table("support_action_plan")
    op.drop_table("barrier_response")
    op.drop_table("safety_assessment")
