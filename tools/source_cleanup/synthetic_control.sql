-- #347 isolated synthetic database only. Never part of application Alembic migrations.
-- The installer verifies the database name/host before executing this file.
CREATE SCHEMA source_cleanup;
REVOKE ALL ON SCHEMA source_cleanup FROM PUBLIC;

-- A read-only executor can take the fixed reference lock without obtaining row-write privileges.
CREATE FUNCTION source_cleanup.lock_references() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN LOCK TABLE public.rag_source_ingestion_artifact IN SHARE MODE NOWAIT; END;
$$;
REVOKE ALL ON FUNCTION source_cleanup.lock_references() FROM PUBLIC;

CREATE TABLE source_cleanup.workspace (
    id text PRIMARY KEY,
    database_name text NOT NULL,
    root text NOT NULL UNIQUE,
    root_identity text NOT NULL,
    schema_hash text NOT NULL,
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
CREATE SEQUENCE source_cleanup.review_revision;
CREATE TABLE source_cleanup.review (
    revision bigint PRIMARY KEY,
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
-- All SQL clients participate, including INSERTs outside the CLI. A conflict aborts
-- immediately; callers must retry explicitly. Revision order is assigned under lock.
CREATE FUNCTION source_cleanup.lock_review_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NOT pg_try_advisory_xact_lock(347, hashtext(NEW.batch_hash)) THEN
        RAISE EXCEPTION 'CLEANUP_REVIEW_BUSY';
    END IF;
    IF TG_TABLE_NAME = 'review' THEN
        IF EXISTS (SELECT 1 FROM source_cleanup.revocation WHERE batch_hash=NEW.batch_hash) THEN
            RAISE EXCEPTION 'CLEANUP_BATCH_REVOKED';
        END IF;
        NEW.revision := nextval('source_cleanup.review_revision');
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION source_cleanup.lock_review_change() FROM PUBLIC;
CREATE TRIGGER cleanup_review_lock BEFORE INSERT ON source_cleanup.review
FOR EACH ROW EXECUTE FUNCTION source_cleanup.lock_review_change();
CREATE TRIGGER cleanup_revocation_lock BEFORE INSERT ON source_cleanup.revocation
FOR EACH ROW EXECUTE FUNCTION source_cleanup.lock_review_change();
CREATE INDEX cleanup_review_latest ON source_cleanup.review(batch_hash, role, revision DESC);

CREATE TABLE source_cleanup.audit (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_hash text NOT NULL,
    object_ref text NOT NULL,
    attempt_id text NOT NULL,
    event text NOT NULL CHECK (event IN ('INTENT','DELETED','FAILED','UNKNOWN','BLOCKED')),
    payload jsonb NOT NULL,
    recorded_by text NOT NULL DEFAULT current_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (attempt_id, event)
);
CREATE UNIQUE INDEX cleanup_audit_one_outcome ON source_cleanup.audit(attempt_id) WHERE event <> 'INTENT';

CREATE FUNCTION source_cleanup.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'CLEANUP_APPEND_ONLY'; END;
$$;
CREATE FUNCTION source_cleanup.check_audit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE intent source_cleanup.audit;
BEGIN
    IF NEW.payload->>'batch_hash' IS DISTINCT FROM NEW.batch_hash
       OR NEW.payload->>'object_ref' IS DISTINCT FROM NEW.object_ref
       OR NEW.payload->>'attempt_id' IS DISTINCT FROM NEW.attempt_id
       OR NEW.payload->>'event' IS DISTINCT FROM NEW.event THEN
        RAISE EXCEPTION 'CLEANUP_AUDIT_BINDING';
    END IF;
    IF NEW.event <> 'INTENT' THEN
        SELECT * INTO intent FROM source_cleanup.audit
         WHERE attempt_id = NEW.attempt_id AND event = 'INTENT';
        IF NOT FOUND OR intent.batch_hash <> NEW.batch_hash OR intent.object_ref <> NEW.object_ref THEN
            RAISE EXCEPTION 'CLEANUP_AUDIT_SEQUENCE';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER cleanup_audit_check BEFORE INSERT ON source_cleanup.audit
FOR EACH ROW EXECUTE FUNCTION source_cleanup.check_audit();

DO $$ DECLARE relation text; BEGIN
    FOREACH relation IN ARRAY ARRAY['workspace','object_receipt','review','revocation','audit'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON source_cleanup.%I FOR EACH ROW EXECUTE FUNCTION source_cleanup.reject_mutation()', relation);
        EXECUTE format('CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON source_cleanup.%I FOR EACH STATEMENT EXECUTE FUNCTION source_cleanup.reject_mutation()', relation);
    END LOOP;
END $$;
