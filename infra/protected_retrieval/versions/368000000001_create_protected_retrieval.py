"""Create the isolated protected-retrieval storage and ACL boundary."""

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


def _qualified(name: str) -> str:
    return f"{_q(_setting('PROTECTED_DB_SCHEMA'))}.{_q(name)}"


def _function(name: str, arguments: str, returns: str, body: str) -> str:
    schema = _q(_setting("PROTECTED_DB_SCHEMA"))
    return f"""
        CREATE OR REPLACE FUNCTION {schema}.{_q(name)}({arguments})
        RETURNS {returns}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp
        AS $function$
        {body}
        $function$
    """


def _apply_acl(function_signatures: Sequence[str]) -> None:
    schema = _q(_setting("PROTECTED_DB_SCHEMA"))
    owner = _q(_setting("PROTECTED_DB_OWNER_ROLE"))
    access = _q(_setting("PROTECTED_DB_ACCESS_ROLE"))
    relation_names = (
        "protected_identity",
        "protected_dataset",
        "protected_artifact",
        "approval_evidence",
        "authorization_grant",
        "operation_capability",
        "operation_result",
        "audit_entry",
        "audit_head",
    )
    for name in relation_names:
        op.execute(f"ALTER TABLE {schema}.{_q(name)} OWNER TO {owner}")
        op.execute(f"REVOKE ALL ON TABLE {schema}.{_q(name)} FROM PUBLIC, {access}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema} FROM PUBLIC, {access}")
    op.execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {schema} FROM PUBLIC, {access}")
    op.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {schema} REVOKE ALL ON TABLES FROM PUBLIC")
    op.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {schema} REVOKE ALL ON SEQUENCES FROM PUBLIC")
    op.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {schema} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA {schema} TO {access}")
    for signature in function_signatures:
        op.execute(f"ALTER FUNCTION {schema}.{signature} OWNER TO {owner}")
        op.execute(f"REVOKE ALL ON FUNCTION {schema}.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {schema}.{signature} TO {access}")


def upgrade() -> None:
    schema = _q(_setting("PROTECTED_DB_SCHEMA"))
    sha_check = "CHECK (VALUE ~ '^[0-9a-f]{64}$')"
    op.execute(f"CREATE DOMAIN {schema}.sha256_hex AS text {sha_check}")
    op.execute(
        f"""
        CREATE TABLE {schema}.protected_identity (
            database_login name PRIMARY KEY,
            actor_id text NOT NULL CHECK (actor_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{{0,159}}$'),
            actor_namespace text NOT NULL CHECK (actor_namespace IN ('GITHUB_LOGIN','SERVICE_IDENTITY','SYSTEM')),
            principal_role text NOT NULL CHECK (principal_role IN ('HOLDOUT_AUTHOR','DATASET_CUSTODIAN','PROTECTED_RUNNER')),
            enabled boolean NOT NULL DEFAULT true,
            UNIQUE (actor_namespace, actor_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE {schema}.protected_dataset (
            dataset_id text NOT NULL,
            dataset_version text NOT NULL CHECK (dataset_version ~ '^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$'),
            binding jsonb NOT NULL CHECK (jsonb_typeof(binding) = 'object'),
            manifest_sha256 {schema}.sha256_hex NOT NULL,
            protected_artifact_sha256 {schema}.sha256_hex NOT NULL,
            hmac_key_version text NOT NULL,
            state text NOT NULL CHECK (state IN ('ACCESS_AUTHORIZED','AUTHORING','REVIEW_READY','FROZEN')),
            state_revision integer NOT NULL CHECK (state_revision > 0),
            authored_count integer NOT NULL CHECK (authored_count BETWEEN 0 AND 40),
            review_complete boolean NOT NULL,
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
            grant_id uuid NOT NULL REFERENCES {schema}.authorization_grant (grant_id),
            grant_revision integer NOT NULL,
            dataset_id text NOT NULL,
            dataset_version text NOT NULL,
            dataset_state_revision integer NOT NULL,
            protected_artifact_sha256 {schema}.sha256_hex NOT NULL,
            protected_action text NOT NULL CHECK (protected_action IN ('READ','WRITE','FREEZE','RUN')),
            target_ref uuid NOT NULL,
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            operated_at timestamptz,
            UNIQUE (operation_key, grant_id, dataset_state_revision, protected_action, target_ref)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE {schema}.operation_result (
            operation_key text PRIMARY KEY,
            result_ref uuid NOT NULL UNIQUE,
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
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
            entry_sha256 {schema}.sha256_hex
        )
        """
    )
    op.execute(f"INSERT INTO {schema}.audit_head DEFAULT VALUES")

    functions: dict[str, tuple[str, str, str]] = {
        "resolve_principal": (
            "",
            "jsonb",
            f"""
            DECLARE resolved jsonb;
            BEGIN
                SELECT jsonb_build_object(
                    'actor', jsonb_build_object('actor_id', actor_id, 'namespace', actor_namespace),
                    'role', principal_role
                ) INTO resolved
                FROM {schema}.protected_identity
                WHERE database_login = session_user::name AND enabled;
                IF resolved IS NULL THEN
                    RAISE EXCEPTION USING MESSAGE = 'AUTHORIZATION_NOT_FOUND', ERRCODE = 'P0001';
                END IF;
                RETURN resolved;
            END
            """,
        ),
        "load_dataset": (
            "requested_dataset_id text, requested_dataset_version text",
            "jsonb",
            f"""
            DECLARE resolved jsonb;
            BEGIN
                PERFORM {schema}.resolve_principal();
                SELECT binding INTO resolved FROM {schema}.protected_dataset
                WHERE dataset_id = requested_dataset_id AND dataset_version = requested_dataset_version;
                IF resolved IS NULL THEN
                    RAISE EXCEPTION USING MESSAGE = 'DATASET_STATE_MISMATCH', ERRCODE = 'P0001';
                END IF;
                RETURN resolved;
            END
            """,
        ),
        "load_approval": (
            "requested_source_event_id text",
            "jsonb",
            f"""
            DECLARE resolved jsonb;
            BEGIN
                PERFORM {schema}.resolve_principal();
                SELECT evidence INTO resolved FROM {schema}.approval_evidence
                WHERE source_event_id = requested_source_event_id;
                IF resolved IS NULL THEN
                    RAISE EXCEPTION USING MESSAGE = 'APPROVAL_NOT_VERIFIED', ERRCODE = 'P0001';
                END IF;
                RETURN resolved;
            END
            """,
        ),
        "audit_checkpoint": (
            "",
            "jsonb",
            f"""
            DECLARE head {schema}.audit_head%ROWTYPE;
            BEGIN
                PERFORM {schema}.resolve_principal();
                SELECT * INTO head FROM {schema}.audit_head WHERE singleton FOR UPDATE;
                RETURN jsonb_build_object('sequence', head.sequence, 'entry_sha256', head.entry_sha256);
            END
            """,
        ),
        "find_grant": (
            "request_body jsonb",
            "jsonb",
            f"""
            DECLARE resolved jsonb;
            DECLARE principal jsonb;
            BEGIN
                principal := {schema}.resolve_principal();
                IF principal <> request_body->'principal' THEN
                    RAISE EXCEPTION USING MESSAGE = 'GUARD_BINDING_MISMATCH', ERRCODE = 'P0001';
                END IF;
                SELECT grant_body INTO resolved FROM {schema}.authorization_grant
                WHERE subject_actor_id = principal#>>'{{actor,actor_id}}'
                  AND subject_namespace = principal#>>'{{actor,namespace}}'
                  AND subject_role = principal->>'role'
                  AND dataset_id = request_body#>>'{{dataset,dataset_id}}'
                  AND dataset_version = request_body#>>'{{dataset,dataset_version}}'
                  AND manifest_sha256 = request_body#>>'{{dataset,manifest_sha256}}'
                  AND protected_artifact_sha256 = request_body#>>'{{dataset,protected_artifact_sha256}}'
                  AND hmac_key_version = request_body#>>'{{dataset,hmac_key_version}}'
                  AND request_body->>'action' = ANY(actions)
                  AND revoked_at IS NULL
                ORDER BY revision DESC LIMIT 1;
                RETURN resolved;
            END
            """,
        ),
        "require_grant": (
            "requested_grant_id uuid",
            "jsonb",
            f"""
            DECLARE resolved jsonb;
            BEGIN
                PERFORM {schema}.resolve_principal();
                SELECT grant_body INTO resolved FROM {schema}.authorization_grant
                WHERE grant_id = requested_grant_id AND revoked_at IS NULL;
                IF resolved IS NULL THEN
                    RAISE EXCEPTION USING MESSAGE = 'AUTHORIZATION_REVOKED', ERRCODE = 'P0001';
                END IF;
                RETURN resolved;
            END
            """,
        ),
        "operation_history": (
            "request_body jsonb",
            "SETOF jsonb",
            f"""
            BEGIN
                PERFORM {schema}.resolve_principal();
                RETURN QUERY SELECT entry_body FROM {schema}.audit_entry
                WHERE event_kind = 'OPERATION'
                  AND operation_key = request_body->>'operation_key'
                  AND entry_body->'principal' = request_body->'principal'
                  AND entry_body->'target_ref' = request_body->'target_ref'
                  AND entry_body->>'protected_action' = request_body->>'action'
                  AND entry_body->>'dataset_id' = request_body#>>'{{dataset,dataset_id}}'
                  AND entry_body->>'dataset_version' = request_body#>>'{{dataset,dataset_version}}'
                  AND entry_body->>'manifest_sha256' = request_body#>>'{{dataset,manifest_sha256}}'
                  AND entry_body->>'protected_artifact_sha256' =
                      request_body#>>'{{dataset,protected_artifact_sha256}}'
                ORDER BY sequence;
            END
            """,
        ),
        "lock_operation": (
            "request_body jsonb, requested_grant_id uuid",
            "void",
            f"""
            BEGIN
                IF {schema}.resolve_principal() <> request_body->'principal' THEN
                    RAISE EXCEPTION USING MESSAGE = 'GUARD_BINDING_MISMATCH', ERRCODE = 'P0001';
                END IF;
                PERFORM 1 FROM {schema}.protected_dataset
                WHERE dataset_id = request_body#>>'{{dataset,dataset_id}}'
                  AND dataset_version = request_body#>>'{{dataset,dataset_version}}' FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING MESSAGE = 'DATASET_STATE_MISMATCH', ERRCODE = 'P0001';
                END IF;
                PERFORM 1 FROM {schema}.authorization_grant
                WHERE grant_id = requested_grant_id FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING MESSAGE = 'AUTHORIZATION_NOT_FOUND', ERRCODE = 'P0001';
                END IF;
                PERFORM 1 FROM {schema}.audit_head WHERE singleton FOR UPDATE;
            END
            """,
        ),
        "append_operation": (
            "request_body jsonb, entry_body jsonb",
            "jsonb",
            f"""
            DECLARE head {schema}.audit_head%ROWTYPE;
            DECLARE inserted_sequence bigint;
            DECLARE latest_outcome text;
            DECLARE next_outcome text := entry_body->>'outcome';
            BEGIN
                IF {schema}.resolve_principal() <> request_body->'principal' THEN
                    RAISE EXCEPTION USING MESSAGE = 'AUDIT_BINDING_MISMATCH', ERRCODE = 'P0001';
                END IF;
                SELECT * INTO head FROM {schema}.audit_head WHERE singleton FOR UPDATE;
                IF entry_body->>'event_kind' <> 'OPERATION'
                   OR entry_body->>'operation_key' <> request_body->>'operation_key'
                   OR entry_body->>'request_id' <> request_body->>'request_id'
                   OR entry_body->'principal' <> request_body->'principal'
                   OR entry_body->>'protected_action' <> request_body->>'action'
                   OR entry_body->'target_ref' <> request_body->'target_ref'
                   OR entry_body->>'dataset_id' <> request_body#>>'{{dataset,dataset_id}}'
                   OR entry_body->>'dataset_version' <> request_body#>>'{{dataset,dataset_version}}'
                   OR entry_body->>'manifest_sha256' <> request_body#>>'{{dataset,manifest_sha256}}'
                   OR entry_body->>'protected_artifact_sha256' <>
                      request_body#>>'{{dataset,protected_artifact_sha256}}'
                   OR entry_body->>'hmac_key_version' <> request_body#>>'{{dataset,hmac_key_version}}'
                   OR (entry_body->>'dataset_state_revision')::integer <>
                      (request_body#>>'{{dataset,state_revision}}')::integer THEN
                    RAISE EXCEPTION USING MESSAGE = 'AUDIT_BINDING_MISMATCH', ERRCODE = 'P0001';
                END IF;
                SELECT prior.entry_body->>'outcome' INTO latest_outcome
                FROM {schema}.audit_entry AS prior
                WHERE prior.event_kind = 'OPERATION'
                  AND prior.operation_key = request_body->>'operation_key'
                  AND prior.entry_body->'principal' = request_body->'principal'
                  AND prior.entry_body->'target_ref' = request_body->'target_ref'
                ORDER BY prior.sequence DESC LIMIT 1;
                IF NOT (
                    (next_outcome = 'INTENT' AND (latest_outcome IS NULL OR latest_outcome = 'DENIED'))
                    OR (next_outcome = 'DENIED' AND (
                        latest_outcome IS NULL OR latest_outcome = 'DENIED'
                        OR (latest_outcome = 'INTENT' AND (entry_body->>'closes_intent')::boolean)
                    ))
                    OR (next_outcome IN ('SUCCEEDED','UNKNOWN') AND latest_outcome = 'INTENT')
                ) THEN
                    RAISE EXCEPTION USING MESSAGE = 'AUDIT_TRANSITION_INVALID', ERRCODE = 'P0001';
                END IF;
                IF COALESCE(entry_body->>'previous_entry_sha256', '') <> COALESCE(head.entry_sha256::text, '') THEN
                    RAISE EXCEPTION USING MESSAGE = 'AUDIT_CAS_CONFLICT', ERRCODE = 'P0001';
                END IF;
                INSERT INTO {schema}.audit_entry (
                    sequence, event_id, event_kind, operation_key, entry_body, previous_entry_sha256,
                    entry_sha256, recorded_at
                ) VALUES (
                    head.sequence + 1, (entry_body->>'event_id')::uuid, 'OPERATION',
                    request_body->>'operation_key', entry_body,
                    NULLIF(entry_body->>'previous_entry_sha256', '')::{schema}.sha256_hex,
                    (entry_body->>'entry_sha256')::{schema}.sha256_hex,
                    (entry_body->>'recorded_at')::timestamptz
                ) RETURNING sequence INTO inserted_sequence;
                IF inserted_sequence <> head.sequence + 1 OR (entry_body->>'sequence')::bigint <> inserted_sequence THEN
                    RAISE EXCEPTION USING MESSAGE = 'AUDIT_CAS_CONFLICT', ERRCODE = 'P0001';
                END IF;
                UPDATE {schema}.audit_head SET sequence = inserted_sequence,
                    entry_sha256 = (entry_body->>'entry_sha256')::{schema}.sha256_hex WHERE singleton;
                RETURN entry_body;
            END
            """,
        ),
        "issue_capability": (
            "request_body jsonb, requested_grant_id uuid",
            "jsonb",
            f"""
            DECLARE dataset_row {schema}.protected_dataset%ROWTYPE;
            DECLARE grant_row {schema}.authorization_grant%ROWTYPE;
            DECLARE nonce_value uuid := gen_random_uuid();
            DECLARE expires timestamptz;
            BEGIN
                IF {schema}.resolve_principal() <> request_body->'principal' THEN
                    RAISE EXCEPTION USING MESSAGE = 'GUARD_BINDING_MISMATCH', ERRCODE = 'P0001';
                END IF;
                SELECT * INTO dataset_row FROM {schema}.protected_dataset
                WHERE dataset_id = request_body#>>'{{dataset,dataset_id}}'
                  AND dataset_version = request_body#>>'{{dataset,dataset_version}}' FOR UPDATE;
                SELECT * INTO grant_row FROM {schema}.authorization_grant
                WHERE grant_id = requested_grant_id FOR UPDATE;
                IF dataset_row.dataset_id IS NULL OR grant_row.grant_id IS NULL OR grant_row.revoked_at IS NOT NULL THEN
                    RAISE EXCEPTION USING MESSAGE = 'AUTHORIZATION_NOT_FOUND', ERRCODE = 'P0001';
                END IF;
                IF dataset_row.binding <> request_body->'dataset'
                   OR grant_row.subject_actor_id <> request_body#>>'{{principal,actor,actor_id}}'
                   OR grant_row.subject_namespace <> request_body#>>'{{principal,actor,namespace}}'
                   OR grant_row.subject_role <> request_body#>>'{{principal,role}}'
                   OR grant_row.dataset_id <> dataset_row.dataset_id
                   OR grant_row.dataset_version <> dataset_row.dataset_version
                   OR grant_row.manifest_sha256 <> dataset_row.manifest_sha256
                   OR grant_row.protected_artifact_sha256 <> dataset_row.protected_artifact_sha256
                   OR grant_row.hmac_key_version <> dataset_row.hmac_key_version
                   OR NOT (request_body->>'action' = ANY(grant_row.actions)) THEN
                    RAISE EXCEPTION USING MESSAGE = 'GUARD_BINDING_MISMATCH', ERRCODE = 'P0001';
                END IF;
                IF clock_timestamp() < grant_row.valid_from OR clock_timestamp() >= grant_row.expires_at THEN
                    RAISE EXCEPTION USING MESSAGE = 'AUTHORIZATION_EXPIRED', ERRCODE = 'P0001';
                END IF;
                IF NOT (
                    (grant_row.subject_role = 'HOLDOUT_AUTHOR'
                        AND request_body->>'action' IN ('READ','WRITE')
                        AND dataset_row.state IN ('ACCESS_AUTHORIZED','AUTHORING'))
                    OR (grant_row.subject_role = 'DATASET_CUSTODIAN'
                        AND request_body->>'action' = 'READ')
                    OR (grant_row.subject_role = 'DATASET_CUSTODIAN'
                        AND request_body->>'action' = 'FREEZE'
                        AND dataset_row.state = 'REVIEW_READY'
                        AND dataset_row.authored_count = 40
                        AND dataset_row.review_complete
                        AND dataset_row.binding->'leakage_axis_intersections' = '[0,0,0,0]'::jsonb)
                    OR (grant_row.subject_role = 'PROTECTED_RUNNER'
                        AND request_body->>'action' IN ('READ','RUN')
                        AND dataset_row.state = 'FROZEN'
                        AND dataset_row.binding->'freeze_receipt_ref' IS NOT NULL
                        AND dataset_row.binding->'execution_authorization_ref' IS NOT NULL
                        AND dataset_row.binding->'retriever_binding_ref' IS NOT NULL)
                ) THEN
                    RAISE EXCEPTION USING MESSAGE = 'ROLE_ACTION_STATE_DENIED', ERRCODE = 'P0001';
                END IF;
                expires := LEAST(grant_row.expires_at, clock_timestamp() + interval '5 minutes');
                INSERT INTO {schema}.operation_capability (
                    nonce, request_id, operation_key, grant_id, grant_revision, dataset_id,
                    dataset_version, dataset_state_revision, protected_artifact_sha256,
                    protected_action, target_ref, expires_at
                ) VALUES (
                    nonce_value, (request_body->>'request_id')::uuid, request_body->>'operation_key',
                    grant_row.grant_id, grant_row.revision, dataset_row.dataset_id, dataset_row.dataset_version,
                    dataset_row.state_revision, dataset_row.protected_artifact_sha256,
                    request_body->>'action', (request_body#>>'{{target_ref,value}}')::uuid, expires
                );
                RETURN jsonb_build_object(
                    'request_id', request_body->>'request_id', 'grant_id', grant_row.grant_id,
                    'grant_revision', grant_row.revision, 'dataset_state_revision', dataset_row.state_revision,
                    'protected_artifact_sha256', dataset_row.protected_artifact_sha256,
                    'action', request_body->>'action', 'target_ref', request_body->'target_ref',
                    'nonce', nonce_value, 'expires_at', expires
                );
            END
            """,
        ),
        "consume_capability": (
            "requested_nonce uuid",
            "void",
            f"""
            BEGIN
                PERFORM {schema}.resolve_principal();
                UPDATE {schema}.operation_capability SET consumed_at = clock_timestamp()
                WHERE nonce = requested_nonce AND consumed_at IS NULL AND expires_at > clock_timestamp();
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING MESSAGE = 'CAPABILITY_ALREADY_CONSUMED', ERRCODE = 'P0001';
                END IF;
            END
            """,
        ),
        "read_artifact": (
            "requested_nonce uuid",
            "bytea",
            f"""
            DECLARE payload bytea;
            DECLARE target_value uuid;
            BEGIN
                UPDATE {schema}.operation_capability SET operated_at = clock_timestamp()
                WHERE nonce = requested_nonce AND consumed_at IS NOT NULL AND operated_at IS NULL
                  AND protected_action IN ('READ','RUN')
                RETURNING target_ref INTO target_value;
                SELECT envelope INTO payload FROM {schema}.protected_artifact
                WHERE target_ref = target_value;
                IF payload IS NULL THEN
                    RAISE EXCEPTION USING MESSAGE = 'CAPABILITY_BINDING_MISMATCH', ERRCODE = 'P0001';
                END IF;
                RETURN payload;
            END
            """,
        ),
        "write_artifact": (
            "requested_nonce uuid, payload bytea, payload_sha256 text",
            "uuid",
            f"""
            DECLARE capability {schema}.operation_capability%ROWTYPE;
            BEGIN
                UPDATE {schema}.operation_capability SET operated_at = clock_timestamp()
                WHERE nonce = requested_nonce AND consumed_at IS NOT NULL AND operated_at IS NULL
                  AND protected_action = 'WRITE'
                RETURNING * INTO capability;
                IF capability.nonce IS NULL THEN
                    RAISE EXCEPTION USING MESSAGE = 'CAPABILITY_BINDING_MISMATCH', ERRCODE = 'P0001';
                END IF;
                INSERT INTO {schema}.protected_artifact (
                    target_ref, dataset_id, dataset_version, envelope, envelope_sha256, hmac_key_version
                ) SELECT capability.target_ref, capability.dataset_id, capability.dataset_version,
                    payload, payload_sha256::{schema}.sha256_hex, dataset.hmac_key_version
                FROM {schema}.protected_dataset dataset
                WHERE dataset.dataset_id = capability.dataset_id
                  AND dataset.dataset_version = capability.dataset_version
                ON CONFLICT (target_ref) DO UPDATE SET envelope = EXCLUDED.envelope,
                    envelope_sha256 = EXCLUDED.envelope_sha256;
                RETURN capability.target_ref;
            END
            """,
        ),
    }
    signatures: list[str] = []
    for name, (arguments, returns, body) in functions.items():
        op.execute(_function(name, arguments, returns, body))
        argument_types = {
            "": "",
            "requested_dataset_id text, requested_dataset_version text": "text,text",
            "request_body jsonb": "jsonb",
            "requested_source_event_id text": "text",
            "requested_grant_id uuid": "uuid",
            "request_body jsonb, entry_body jsonb": "jsonb,jsonb",
            "request_body jsonb, requested_grant_id uuid": "jsonb,uuid",
            "requested_nonce uuid": "uuid",
            "requested_nonce uuid, payload bytea, payload_sha256 text": "uuid,bytea,text",
        }[arguments]
        signatures.append(f"{_q(name)}({argument_types})")
    op.execute(
        _function(
            "internal_audit_checkpoint",
            "",
            "bigint",
            f"BEGIN RETURN (SELECT sequence FROM {schema}.audit_head WHERE singleton); END",
        )
    )
    owner = _q(_setting("PROTECTED_DB_OWNER_ROLE"))
    op.execute(f'ALTER FUNCTION {schema}."internal_audit_checkpoint"() OWNER TO {owner}')
    _apply_acl(signatures)


def downgrade() -> None:
    schema = _q(_setting("PROTECTED_DB_SCHEMA"))
    connection = op.get_bind()
    protected_rows = connection.execute(
        text(
            f"""
            SELECT
                (SELECT count(*) FROM {schema}.protected_dataset) +
                (SELECT count(*) FROM {schema}.protected_artifact) +
                (SELECT count(*) FROM {schema}.authorization_grant) +
                (SELECT count(*) FROM {schema}.audit_entry)
            """
        )
    ).scalar_one()
    if protected_rows:
        raise RuntimeError("protected retrieval downgrade refused while durable rows exist")
    for name in (
        "audit_head",
        "audit_entry",
        "operation_result",
        "operation_capability",
        "authorization_grant",
        "approval_evidence",
        "protected_artifact",
        "protected_dataset",
        "protected_identity",
    ):
        op.execute(f"DROP TABLE {schema}.{_q(name)} CASCADE")
    op.execute(f"DROP DOMAIN {schema}.sha256_hex")
