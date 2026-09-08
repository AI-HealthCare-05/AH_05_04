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

-- The executor supplies only the operation report, never trusted evidence fields.
-- session_user is the authenticated login, not the SECURITY DEFINER owner.
CREATE FUNCTION source_cleanup.append_audit(
    p_batch text, p_object text, p_attempt text, p_event text, p_reason text
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    pm source_cleanup.review;
    security source_cleanup.review;
    intent source_cleanup.audit;
    target record;
    evidence jsonb;
    effective_time timestamptz;
    recorded_time timestamptz := clock_timestamp();
    caller name := session_user;
BEGIN
    PERFORM p_attempt::uuid;
    IF p_event IS NULL OR p_reason IS NULL OR NOT (CASE p_event
        WHEN 'INTENT' THEN p_reason = 'FINAL_CHECKS_PASSED'
        WHEN 'DELETED' THEN p_reason = 'DELETE_CONFIRMED'
        WHEN 'BLOCKED' THEN p_reason = 'FINAL_RECHECK_FAILED'
        WHEN 'UNKNOWN' THEN p_reason IN ('DELETE_RESULT_UNKNOWN','MISSING_REQUIRES_RECONCILIATION')
        ELSE false END) THEN
        RAISE EXCEPTION 'CLEANUP_AUDIT_REPORT_INVALID';
    END IF;
    SELECT r.*, w.evaluation_offset_days, w.database_name INTO target
      FROM source_cleanup.batch_target b
      JOIN source_cleanup.object_receipt r USING (workspace_id,object_key)
      JOIN source_cleanup.workspace w ON w.id=b.workspace_id
     WHERE b.batch_hash=p_batch AND b.object_ref=p_object;
    IF NOT FOUND OR target.database_name <> current_database() THEN
        RAISE EXCEPTION 'CLEANUP_AUDIT_TARGET_INVALID';
    END IF;
    effective_time := recorded_time + make_interval(days => target.evaluation_offset_days);
    IF p_event = 'INTENT' THEN
        IF NOT pg_try_advisory_xact_lock_shared(347, hashtext(p_batch)) THEN
            RAISE EXCEPTION 'CLEANUP_REVIEW_BUSY';
        END IF;
        SELECT * INTO pm FROM source_cleanup.review
         WHERE batch_hash=p_batch AND role='PM' ORDER BY revision DESC LIMIT 1;
        SELECT * INTO security FROM source_cleanup.review
         WHERE batch_hash=p_batch AND role='DB_SECURITY' ORDER BY revision DESC LIMIT 1;
        IF pm.revision IS NULL OR security.revision IS NULL OR security.revision >= pm.revision
           OR pm.executor <> caller OR security.executor <> caller
           OR pm.actor = caller OR security.actor = caller
           OR NOT EXISTS (SELECT 1 FROM source_cleanup.reviewer WHERE actor=pm.actor AND role='PM')
           OR NOT EXISTS (SELECT 1 FROM source_cleanup.reviewer WHERE actor=security.actor AND role='DB_SECURITY')
           OR effective_time < greatest(pm.valid_from,security.valid_from)
           OR effective_time >= least(pm.expires_at,security.expires_at)
           OR EXISTS (SELECT 1 FROM source_cleanup.revocation WHERE batch_hash=p_batch) THEN
            RAISE EXCEPTION 'CLEANUP_AUDIT_APPROVAL_INVALID';
        END IF;
        PERFORM source_cleanup.lock_references();
        IF effective_time <= target.created_at + interval '30 days'
           OR EXISTS (SELECT 1 FROM public.rag_source_ingestion_artifact
                       WHERE storage_backend='LOCAL_PRIVATE' AND object_key=target.object_key) THEN
            RAISE EXCEPTION 'CLEANUP_AUDIT_REFERENCE_INVALID';
        END IF;
        evidence := jsonb_build_object(
            'batch_hash',p_batch, 'object_ref',p_object, 'attempt_id',p_attempt,
            'checksum',target.checksum, 'artifact_kind',target.artifact_kind,
            'policy_version',pm.policy_version,
            'receipt_id',encode(sha256(convert_to(p_batch || ':' || pm.revision || ':' || security.revision,'UTF8')),'hex'),
            'pm_actor',pm.actor, 'db_security_actor',security.actor,
            'executor',caller, 'references_verified',true
        );
    ELSE
        SELECT * INTO intent FROM source_cleanup.audit
         WHERE attempt_id=p_attempt AND event='INTENT';
        IF NOT FOUND OR intent.batch_hash <> p_batch OR intent.object_ref <> p_object
           OR intent.recorded_by <> caller THEN
            RAISE EXCEPTION 'CLEANUP_AUDIT_SEQUENCE';
        END IF;
        -- Recovery retains the original approval evidence rather than attributing it
        -- to an unrelated newer approval. It cannot rewrite the INTENT's identity.
        evidence := intent.payload;
    END IF;
    evidence := evidence || jsonb_build_object(
        'event',p_event, 'reason',p_reason,
        'occurred_at',to_char(recorded_time AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
    );
    INSERT INTO source_cleanup.audit(batch_hash,object_ref,attempt_id,event,payload,recorded_by,recorded_at)
    VALUES (p_batch,p_object,p_attempt,p_event,evidence,caller,recorded_time);
END;
$$;
REVOKE ALL ON FUNCTION source_cleanup.append_audit(text,text,text,text,text) FROM PUBLIC;

DO $$ DECLARE relation text; BEGIN
    FOREACH relation IN ARRAY ARRAY['workspace','object_receipt','batch_target','reviewer','review','revocation','audit'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON source_cleanup.%I FOR EACH ROW EXECUTE FUNCTION source_cleanup.reject_mutation()', relation);
        EXECUTE format('CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON source_cleanup.%I FOR EACH STATEMENT EXECUTE FUNCTION source_cleanup.reject_mutation()', relation);
    END LOOP;
END $$;
