"""Create ordinary storage for the isolated protected-retrieval boundary."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import context, op
from sqlalchemy import text

revision: str = "368000000001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _setting(name: str) -> str:
    value = context.config.attributes.get(name)
    if not isinstance(value, str):
        raise RuntimeError(f"{name} is required")
    return value


def _q(identifier: str) -> str:
    return f'"{identifier}"'


def _relations() -> tuple[str, ...]:
    return (
        "protected_identity",
        "protected_dataset",
        "protected_artifact",
        "approval_evidence",
        "authorization_grant",
        "operation_capability",
        "audit_entry",
        "audit_head",
    )


def _close_default_privileges() -> None:
    schema = _q(_setting("PROTECTED_DB_SCHEMA"))
    owner = _q(_setting("PROTECTED_DB_OWNER_ROLE"))
    access = _q(_setting("PROTECTED_DB_ACCESS_ROLE"))
    control = _q(_setting("PROTECTED_DB_CONTROL_ROLE"))
    for grantee in ("PUBLIC", access, control):
        op.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {schema} REVOKE ALL ON TABLES FROM {grantee}")
        op.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {schema} REVOKE ALL ON SEQUENCES FROM {grantee}"
        )
    op.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {schema} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC")


def _lock_down_relations() -> None:
    schema = _q(_setting("PROTECTED_DB_SCHEMA"))
    owner = _q(_setting("PROTECTED_DB_OWNER_ROLE"))
    access = _q(_setting("PROTECTED_DB_ACCESS_ROLE"))
    control = _q(_setting("PROTECTED_DB_CONTROL_ROLE"))
    for name in _relations():
        relation = f"{schema}.{_q(name)}"
        op.execute(f"ALTER TABLE {relation} OWNER TO {owner}")
        op.execute(f"REVOKE ALL ON TABLE {relation} FROM PUBLIC, {access}, {control}")
    op.execute(f"ALTER DOMAIN {schema}.sha256_hex OWNER TO {owner}")
    op.execute(f"REVOKE ALL ON DOMAIN {schema}.sha256_hex FROM PUBLIC, {access}, {control}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema} FROM PUBLIC, {access}, {control}")
    _close_default_privileges()


def upgrade() -> None:
    schema = _q(_setting("PROTECTED_DB_SCHEMA"))
    op.execute(f"CREATE DOMAIN {schema}.sha256_hex AS text CHECK (VALUE ~ '^[0-9a-f]{{64}}$')")
    op.execute(
        f"""
        CREATE TABLE {schema}.protected_identity (
            database_login name PRIMARY KEY,
            actor_id text NOT NULL CHECK (actor_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{{0,159}}$'),
            actor_namespace text NOT NULL
                CHECK (actor_namespace IN ('GITHUB_LOGIN','SERVICE_IDENTITY','SYSTEM')),
            principal_role text NOT NULL
                CHECK (principal_role IN ('HOLDOUT_AUTHOR','DATASET_CUSTODIAN','PROTECTED_RUNNER')),
            enabled boolean NOT NULL DEFAULT true,
            UNIQUE (actor_namespace, actor_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE {schema}.protected_dataset (
            dataset_id text NOT NULL,
            dataset_version text NOT NULL
                CHECK (dataset_version ~ '^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$'),
            binding jsonb NOT NULL CHECK (jsonb_typeof(binding) = 'object'),
            manifest_sha256 {schema}.sha256_hex NOT NULL,
            protected_artifact_sha256 {schema}.sha256_hex NOT NULL,
            hmac_key_version text NOT NULL,
            state text NOT NULL CHECK (state IN ('ACCESS_AUTHORIZED','AUTHORING','REVIEW_READY','FROZEN')),
            state_revision integer NOT NULL CHECK (state_revision > 0),
            authored_count integer NOT NULL CHECK (authored_count BETWEEN 0 AND 40),
            review_complete boolean NOT NULL,
            lock_marker smallint NOT NULL DEFAULT 0 CHECK (lock_marker = 0),
            PRIMARY KEY (dataset_id, dataset_version)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE {schema}.protected_artifact (
            target_ref uuid PRIMARY KEY,
            dataset_id text NOT NULL,
            dataset_version text NOT NULL,
            envelope bytea NOT NULL,
            envelope_sha256 {schema}.sha256_hex NOT NULL,
            hmac_key_version text NOT NULL,
            FOREIGN KEY (dataset_id, dataset_version)
                REFERENCES {schema}.protected_dataset (dataset_id, dataset_version)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE {schema}.approval_evidence (
            source_event_id text PRIMARY KEY,
            evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
            canonical_raw_sha256 {schema}.sha256_hex NOT NULL,
            recorded_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE {schema}.authorization_grant (
            grant_id uuid PRIMARY KEY,
            revision integer NOT NULL CHECK (revision > 0),
            effective_revision integer NOT NULL CHECK (effective_revision >= revision),
            grant_body jsonb NOT NULL CHECK (jsonb_typeof(grant_body) = 'object'),
            subject_actor_id text NOT NULL,
            subject_namespace text NOT NULL,
            subject_role text NOT NULL,
            dataset_id text NOT NULL,
            dataset_version text NOT NULL,
            manifest_sha256 {schema}.sha256_hex NOT NULL,
            protected_artifact_sha256 {schema}.sha256_hex NOT NULL,
            hmac_key_version text NOT NULL,
            actions text[] NOT NULL CHECK (cardinality(actions) > 0),
            valid_from timestamptz NOT NULL,
            expires_at timestamptz NOT NULL CHECK (valid_from < expires_at),
            revoked_at timestamptz,
            lock_marker smallint NOT NULL DEFAULT 0 CHECK (lock_marker = 0),
            UNIQUE (grant_id, revision),
            FOREIGN KEY (dataset_id, dataset_version)
                REFERENCES {schema}.protected_dataset (dataset_id, dataset_version)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE {schema}.operation_capability (
            nonce uuid PRIMARY KEY,
            request_id uuid NOT NULL,
            operation_key text NOT NULL,
            grant_id uuid NOT NULL,
            grant_revision integer NOT NULL,
            dataset_id text NOT NULL,
            dataset_version text NOT NULL,
            dataset_state_revision integer NOT NULL CHECK (dataset_state_revision > 0),
            protected_artifact_sha256 {schema}.sha256_hex NOT NULL,
            protected_action text NOT NULL CHECK (protected_action IN ('READ','WRITE','FREEZE','RUN')),
            target_ref uuid NOT NULL,
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            operated_at timestamptz,
            FOREIGN KEY (grant_id, grant_revision)
                REFERENCES {schema}.authorization_grant (grant_id, revision),
            FOREIGN KEY (dataset_id, dataset_version)
                REFERENCES {schema}.protected_dataset (dataset_id, dataset_version),
            CHECK (consumed_at IS NULL OR consumed_at <= expires_at),
            CHECK (operated_at IS NULL OR consumed_at IS NOT NULL),
            UNIQUE (operation_key, grant_id, dataset_state_revision, protected_action, target_ref)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE {schema}.audit_entry (
            sequence bigint PRIMARY KEY CHECK (sequence > 0),
            event_id uuid NOT NULL UNIQUE,
            event_kind text NOT NULL CHECK (event_kind IN ('AUTHORIZATION','OPERATION')),
            operation_key text,
            entry_body jsonb NOT NULL CHECK (jsonb_typeof(entry_body) = 'object'),
            previous_entry_sha256 {schema}.sha256_hex,
            entry_sha256 {schema}.sha256_hex NOT NULL UNIQUE,
            recorded_at timestamptz NOT NULL,
            CHECK ((event_kind = 'OPERATION') = (operation_key IS NOT NULL))
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE {schema}.audit_head (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            sequence bigint NOT NULL DEFAULT 0 CHECK (sequence >= 0),
            entry_sha256 {schema}.sha256_hex,
            CHECK ((sequence = 0) = (entry_sha256 IS NULL))
        )
        """
    )
    op.execute(f"INSERT INTO {schema}.audit_head DEFAULT VALUES")
    _lock_down_relations()


def downgrade() -> None:
    schema = _q(_setting("PROTECTED_DB_SCHEMA"))
    connection = op.get_bind()
    protected_rows = connection.execute(
        text(
            f"""
            SELECT
                (SELECT count(*) FROM {schema}.protected_identity) +
                (SELECT count(*) FROM {schema}.protected_dataset) +
                (SELECT count(*) FROM {schema}.protected_artifact) +
                (SELECT count(*) FROM {schema}.approval_evidence) +
                (SELECT count(*) FROM {schema}.authorization_grant) +
                (SELECT count(*) FROM {schema}.operation_capability) +
                (SELECT count(*) FROM {schema}.audit_entry)
            """
        )
    ).scalar_one()
    if protected_rows:
        raise RuntimeError("protected retrieval downgrade refused while durable rows exist")
    for name in reversed(_relations()):
        op.execute(f"DROP TABLE {schema}.{_q(name)}")
    op.execute(f"DROP DOMAIN {schema}.sha256_hex")
