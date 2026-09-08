"""Restrict Snapshot transitions to a DB-owned, audited boundary.

Revision ID: 165e8f706152
Revises: 165d7e6f5041
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "165e8f706152"
down_revision: str | Sequence[str] | None = "165d7e6f5041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Bind to the migration schema, never the caller's search_path or a custom GUC.
    connection = op.get_bind()
    schema = connection.dialect.identifier_preparer.quote(
        connection.execute(sa.text("SELECT current_schema()")).scalar_one()
    )
    op.execute(
        sa.text(f"""
        CREATE FUNCTION guard_rag_snapshot_state_write() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, {schema}, pg_temp AS $$
        BEGIN
            IF current_user = pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = TG_RELID)) THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.verification_status <> 'PENDING' OR NEW.verified_at IS NOT NULL OR NEW.effective_at IS NOT NULL THEN
                    RAISE EXCEPTION 'Snapshot must start PENDING without publication timestamps';
                END IF;
            ELSIF NEW.verification_status IS DISTINCT FROM OLD.verification_status
               OR NEW.verified_at IS DISTINCT FROM OLD.verified_at
               OR NEW.effective_at IS DISTINCT FROM OLD.effective_at THEN
                RAISE EXCEPTION 'Snapshot state requires the DB-owned transition function';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER trg_rag_snapshot_state_write
        BEFORE INSERT OR UPDATE ON rag_source_snapshot
        FOR EACH ROW EXECUTE FUNCTION guard_rag_snapshot_state_write();
    """)
    )
    op.execute(
        sa.text(f"""
        CREATE FUNCTION transition_rag_source_snapshot(
            p_snapshot_id text, p_expected text, p_next text,
            p_verified_at timestamptz, p_effective_at timestamptz, p_selected_by text
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp AS $$
        DECLARE
            target rag_source_snapshot%ROWTYPE;
            operation_key char(36);
        BEGIN
            SELECT operation_id INTO operation_key FROM rag_source_snapshot WHERE id = p_snapshot_id;
            IF NOT FOUND THEN RETURN false; END IF;
            PERFORM 1 FROM rag_source_operation WHERE id = operation_key FOR UPDATE;
            SELECT * INTO target FROM rag_source_snapshot WHERE id = p_snapshot_id FOR UPDATE;
            IF target.verification_status IS DISTINCT FROM p_expected THEN RETURN false; END IF;
            IF p_next IS NULL OR NOT (
                (p_expected = 'PENDING' AND p_next IN ('CURRENT', 'FAILED')) OR
                (p_expected = 'STALE' AND p_next = 'CURRENT') OR
                (p_expected = 'CURRENT' AND p_next = 'STALE')
            ) THEN RAISE EXCEPTION 'Invalid Snapshot transition'; END IF;
            IF p_next = 'CURRENT' THEN
                IF p_effective_at IS NULL THEN RAISE EXCEPTION 'Selection timestamp required'; END IF;
                IF target.rejected_record_count > 0 AND NOT EXISTS (
                    SELECT 1 FROM rag_source_snapshot_verification
                    WHERE snapshot_id = p_snapshot_id AND check_name = 'snapshot-publication-approval'
                      AND verification_result = 'PASSED' AND length(trim(verified_by)) > 0
                ) THEN RAISE EXCEPTION 'Publication approval required'; END IF;
            END IF;
            IF p_expected = 'PENDING' AND p_verified_at IS NULL THEN
                RAISE EXCEPTION 'Verification timestamp required';
            END IF;
            IF (p_next <> 'CURRENT' AND p_effective_at IS NOT NULL)
                OR (p_expected <> 'PENDING' AND p_verified_at IS NOT NULL) THEN
                RAISE EXCEPTION 'Invalid Snapshot timestamp transition';
            END IF;
            UPDATE rag_source_snapshot SET verification_status = p_next,
                verified_at = COALESCE(p_verified_at, verified_at),
                effective_at = COALESCE(p_effective_at, effective_at)
                WHERE id = p_snapshot_id;
            IF p_next = 'CURRENT' THEN
                INSERT INTO rag_source_snapshot_verification
                    (id, snapshot_id, check_name, verification_result, verified_at, verified_by, details_summary)
                VALUES (gen_random_uuid()::text, p_snapshot_id, 'snapshot-current-selection', 'PASSED',
                    p_effective_at, p_selected_by, 'DB-owned transition; session=' || session_user);
            END IF;
            RETURN true;
        END;
        $$;
    """)
    )

    signature = "transition_rag_source_snapshot(text, text, text, timestamptz, timestamptz, text)"
    op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    # Preserve the existing Runtime DML authority, without giving every DB role a definer entry point.
    roles = connection.execute(
        sa.text("""
        SELECT DISTINCT pg_get_userbyid(acl.grantee)
        FROM pg_class c, LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) acl
        WHERE c.oid = 'rag_source_snapshot'::regclass
          AND acl.privilege_type = 'UPDATE' AND acl.grantee <> 0
    """)
    )
    for role in roles.scalars():
        quoted_role = connection.dialect.identifier_preparer.quote(role)
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {quoted_role}")


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE rag_source_snapshot IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM rag_source_snapshot)")).scalar_one():
        raise RuntimeError(
            "Cannot downgrade revision 165e8f706152 Snapshot transition protection while snapshots exist; use a forward-fix."
        )
    op.execute("DROP FUNCTION transition_rag_source_snapshot(text, text, text, timestamptz, timestamptz, text)")
    op.execute("DROP TRIGGER trg_rag_snapshot_state_write ON rag_source_snapshot")
    op.execute("DROP FUNCTION guard_rag_snapshot_state_write()")
