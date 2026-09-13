-- #347 isolated synthetic database only. Never part of application Alembic migrations.
-- The installer verifies the database name/host before executing this file.
CREATE SCHEMA source_cleanup;
REVOKE ALL ON SCHEMA source_cleanup FROM PUBLIC;

CREATE TABLE source_cleanup.workspace (
    id text PRIMARY KEY,
    database_name text NOT NULL,
    root text NOT NULL UNIQUE,
    root_identity text NOT NULL,
    schema_hash text NOT NULL,
    evaluation_offset_days integer NOT NULL CHECK (evaluation_offset_days IN (0,31)),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE source_cleanup.object_receipt (
    workspace_id text NOT NULL REFERENCES source_cleanup.workspace(id),
    object_key text NOT NULL,
    generation text NOT NULL,
    checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    byte_size bigint NOT NULL CHECK (byte_size >= 0),
    artifact_kind text NOT NULL CHECK (artifact_kind IN ('RAW_RESPONSE', 'REJECTS')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (workspace_id, object_key)
);
-- Provisioned by the isolated DB administrator, never by an executor/reviewer.
CREATE TABLE source_cleanup.reviewer (
    actor name PRIMARY KEY,
    role text NOT NULL CHECK (role IN ('PM','DB_SECURITY'))
);
CREATE TABLE source_cleanup.batch_target (
    batch_hash text NOT NULL CHECK (batch_hash ~ '^[0-9a-f]{64}$'),
    object_ref text NOT NULL CHECK (object_ref ~ '^[0-9a-f]{64}$'),
    workspace_id text NOT NULL,
    object_key text NOT NULL,
    PRIMARY KEY (batch_hash, object_ref),
    UNIQUE (batch_hash, workspace_id, object_key),
    FOREIGN KEY (workspace_id,object_key) REFERENCES source_cleanup.object_receipt(workspace_id,object_key),
    CHECK (object_ref = encode(sha256(convert_to(batch_hash || ':' || object_key, 'UTF8')), 'hex'))
);
CREATE SEQUENCE source_cleanup.review_revision;
CREATE TABLE source_cleanup.review (
    revision bigint PRIMARY KEY DEFAULT nextval('source_cleanup.review_revision'),
    batch_hash text NOT NULL CHECK (batch_hash ~ '^[0-9a-f]{64}$'),
    role text NOT NULL CHECK (role IN ('PM', 'DB_SECURITY')),
    actor text NOT NULL DEFAULT current_user,
    executor text NOT NULL,
    policy_version text NOT NULL CHECK (policy_version = 'source-artifact-retention-v1'),
    valid_from timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at > valid_from)
);
CREATE TABLE source_cleanup.revocation (
    batch_hash text PRIMARY KEY,
    actor text NOT NULL DEFAULT current_user,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX cleanup_review_latest ON source_cleanup.review(batch_hash, role, revision DESC);

CREATE TABLE source_cleanup.audit (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_hash text NOT NULL,
    object_ref text NOT NULL,
    attempt_id text NOT NULL,
    event text NOT NULL CHECK (event IN ('INTENT','DELETED','FAILED','UNKNOWN','BLOCKED')),
    payload jsonb NOT NULL,
    intent_sequence bigint,
    recorded_by text NOT NULL DEFAULT current_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (attempt_id, event),
    UNIQUE (sequence,batch_hash,object_ref,attempt_id),
    FOREIGN KEY (intent_sequence,batch_hash,object_ref,attempt_id)
      REFERENCES source_cleanup.audit(sequence,batch_hash,object_ref,attempt_id),
    CHECK ((event='INTENT' AND intent_sequence IS NULL) OR (event<>'INTENT' AND intent_sequence IS NOT NULL)),
    CHECK (((payload->>'batch_hash'=batch_hash) AND (payload->>'object_ref'=object_ref)
      AND (payload->>'attempt_id'=attempt_id) AND (payload->>'event'=event)) IS TRUE),
    CHECK ((CASE event
      WHEN 'INTENT' THEN payload->>'reason'='FINAL_CHECKS_PASSED'
      WHEN 'DELETED' THEN payload->>'reason'='DELETE_CONFIRMED'
      WHEN 'BLOCKED' THEN payload->>'reason'='FINAL_RECHECK_FAILED'
      WHEN 'UNKNOWN' THEN payload->>'reason' IN ('DELETE_RESULT_UNKNOWN','MISSING_REQUIRES_RECONCILIATION')
      ELSE false END) IS TRUE)
);
CREATE UNIQUE INDEX cleanup_audit_one_outcome ON source_cleanup.audit(attempt_id) WHERE event <> 'INTENT';
