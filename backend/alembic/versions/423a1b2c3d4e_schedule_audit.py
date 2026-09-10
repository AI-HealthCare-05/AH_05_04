"""PD-417 schedule audit, baseline evidence and cancellation instants.

Revision ID: 423a1b2c3d4e
Revises: 3984b5c6d7e8
"""

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "423a1b2c3d4e"
down_revision = "3984b5c6d7e8"
branch_labels = None
depends_on = None


def _capture_baseline(connection: sa.Connection) -> None:
    # No actor, mutation timestamp or synthetic revision transition is reconstructed.
    rows = (
        connection.execute(
            sa.text("""
        SELECT s.id, s.revision, s.start_local_date, s.end_mode, s.end_local_date,
               s.status, s.source,
               COALESCE((SELECT json_agg(t.local_time::text ORDER BY t.local_time)
                         FROM medication_schedule_time t
                         WHERE t.medication_schedule_id = s.id
                           AND t.schedule_revision = (
                               SELECT max(t2.schedule_revision) FROM medication_schedule_time t2
                               WHERE t2.medication_schedule_id = s.id AND t2.schedule_revision <= s.revision
                           )), '[]'::json) AS local_times
        FROM medication_schedule s ORDER BY s.id
    """)
        )
        .mappings()
        .all()
    )
    if not rows:
        return
    destination = context.get_x_argument(as_dictionary=True).get("schedule_baseline_path")
    if not destination:
        raise RuntimeError("Existing schedules require -x schedule_baseline_path=/protected/path/baseline.json")
    path = Path(destination)
    repository = Path(__file__).resolve().parents[3]
    parent = path.parent.resolve(strict=True)
    info = parent.stat()
    if (
        not path.is_absolute()
        or parent == repository
        or repository in parent.parents
        or stat.S_IMODE(info.st_mode) & 0o077
        or info.st_uid != os.getuid()
    ):
        raise RuntimeError("Baseline directory must be owned, private (0700) and outside the repository")
    payload = {
        "kind": "MIGRATION_BASELINE_NOT_MUTATION_HISTORY",
        "migration_revision": revision,
        "captured_at": datetime.now(UTC).isoformat(),
        "database_commit_asserted": False,
        "schedules": [dict(row) for row in rows],
    }
    # Exclusive create: never overwrite a previous attempt, follow a symlink, or log payload.
    descriptor = os.open(parent / path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(payload, output, default=str, sort_keys=True, ensure_ascii=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE medication_schedule, medication_schedule_time IN ACCESS EXCLUSIVE MODE"))
    _capture_baseline(connection)
    op.add_column("medication_occurrence", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "medication_schedule_audit",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("medication_schedule_id", sa.CHAR(36), nullable=False),
        sa.Column("from_revision", sa.Integer(), nullable=False),
        sa.Column("to_revision", sa.Integer(), nullable=False),
        sa.Column("before_snapshot", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("after_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("changed_by", sa.CHAR(36), nullable=True),
        sa.Column("change_source", sa.String(20), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["medication_schedule_id"], ["medication_schedule.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["changed_by"], ["user.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("medication_schedule_id", "to_revision", name="uq_schedule_audit_to_revision"),
        sa.CheckConstraint("from_revision >= 0", name="chk_schedule_audit_from_revision"),
        sa.CheckConstraint("to_revision = from_revision + 1", name="chk_schedule_audit_revision_step"),
        sa.CheckConstraint(
            "(from_revision = 0 AND before_snapshot IS NULL) OR (from_revision > 0 AND before_snapshot IS NOT NULL)",
            name="chk_schedule_audit_before_snapshot",
        ),
        sa.CheckConstraint(
            "(change_source = 'USER' AND changed_by IS NOT NULL) OR "
            "(change_source = 'SCHEDULER' AND changed_by IS NULL)",
            name="chk_schedule_audit_actor",
        ),
    )

    # New table default grants must not bypass the reviewed Runtime INSERT-only policy.
    statements = connection.scalars(
        sa.text(
            "SELECT DISTINCT format('REVOKE ALL ON TABLE %s FROM %s', c.oid::regclass, "
            "CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE quote_ident(r.rolname) END) "
            "FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) acl "
            "LEFT JOIN pg_roles r ON r.oid=acl.grantee "
            "WHERE c.oid='medication_schedule_audit'::regclass AND acl.grantee<>c.relowner"
        )
    ).all()
    for statement in statements:
        connection.execute(sa.text(statement))


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE medication_schedule_audit, medication_occurrence IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM medication_schedule_audit)")) or connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM medication_occurrence WHERE cancelled_at IS NOT NULL)")
    ):
        raise RuntimeError("Cannot downgrade: schedule audit or cancellation history exists")
    op.drop_table("medication_schedule_audit")
    op.drop_column("medication_occurrence", "cancelled_at")
