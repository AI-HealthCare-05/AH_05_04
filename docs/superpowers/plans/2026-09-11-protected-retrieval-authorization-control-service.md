# Protected Retrieval Authorization Control Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Issue #368 C1 approval ingestion and grant/revoke/expire control-plane commands with authenticated control identities, atomic audit, payload-bound idempotency, and exact PostgreSQL privileges.

**Architecture:** Keep audit DTOs and the audit union in the existing protected-retrieval kernel. Put command DTOs, trusted-source Protocol, canonical command hashing, and pure approval verification in a focused control domain module; put SQL transactions in a separate PostgreSQL application service. Extend the isolated protected schema through a forward migration and reuse the existing global audit head and hash chain.

**Tech Stack:** Python 3.13, Pydantic v2 strict models, SQLAlchemy asyncio, PostgreSQL, Alembic, pytest/pytest-asyncio, Ruff, Mypy.

**Spec:** `docs/superpowers/specs/2026-09-11-protected-retrieval-authorization-control-service-design.md`

## Global Constraints

- Implement C1 only: approval ingestion, GRANT, REVOKE, and EXPIRE. Do not add identity administration, Dataset lifecycle transitions, or FREEZE.
- Add no dependency and no database function, procedure, trigger, RLS policy, stored business rule, or legacy exception.
- Preserve migration `368000000001`; add forward revision `368000000002` with data-preserving downgrade refusal.
- Keep `PROTECTED_RETRIEVAL_ENABLED=false` and all publication/external approval gates unchanged.
- Use strict DTOs, fixed safe reasons, database UTC, one explicit mutation transaction, and the lock order Dataset → grant scope/target → audit head.
- Never log or expose evidence bodies, actor identity, protected coordinates, digests, keys, SQL parameters, or protected content.
- Keep the target contract under `docs/contracts/targets/post-mvp-1/`; do not promote it to `current/` or close Issue #368.
- Execute inline in this isolated worktree because the user already requested implementation in the current task; no subagent delegation is used.

---

### Task 1: Approve and document the C1 shared contract

**Files:**
- Create: `docs/governance/decisions/2026-09-11-protected-retrieval-authorization-control.md`
- Modify: `docs/contracts/targets/post-mvp-1/protected-retrieval-infrastructure-v1.md`
- Modify: `docs/contracts/targets/post-mvp-1/README.md`
- Modify: `docs/contracts/README.md`

**Interfaces:**
- Consumes: `PD-368-20260909`, the approved design spec, and the user-confirmed owner coordination.
- Produces: coordinated `PD-368-R1` Candidate with exact C1 DTOs, audit fields, identity schema, idempotency, transaction order, safe reason, and C2 exclusion; designated PR review remains the approval evidence.

- [ ] **Step 1: Add the Decision amendment**

Record `Decision ID: PD-368-R1`, `Status: Candidate · Coordination Confirmed · PR Review Required`, implementation owner and named reviewers, then copy the exact command/result/audit field sets and the single new safe reason `CONTROL_COMMAND_CONFLICT` from the design. State that known denials commit a CONTROL denial audit before raising, while identity/audit-integrity failures mutate nothing and may have no denial audit.

- [ ] **Step 2: Align the target contract and both indexes**

Change only the C1 section from unspecified to the agreed command and transaction contract. Keep lifecycle/FREEZE, production source connector, external approvals, and effective enforcement explicitly unimplemented.

- [ ] **Step 3: Validate the documentation diff**

Run: `git diff --check`

Expected: exit 0 with no whitespace error; the diff contains no Current promotion, Issue closure, Production activation, HOLDOUT count change, or external approval claim.

- [ ] **Step 4: Commit**

```bash
git add docs/governance/decisions/2026-09-11-protected-retrieval-authorization-control.md docs/contracts/targets/post-mvp-1/protected-retrieval-infrastructure-v1.md docs/contracts/targets/post-mvp-1/README.md docs/contracts/README.md
git commit -m "📝 docs: #368 authorization control 계약 확정"
```

### Task 2: Add control command and audit contracts with pure approval verification

**Files:**
- Create: `ai_worker/tasks/evaluation/protected_retrieval_control.py`
- Create: `ai_worker/tests/evaluation/test_protected_retrieval_control.py`
- Modify: `ai_worker/tasks/evaluation/protected_retrieval.py:16-369`
- Modify: `ai_worker/tasks/evaluation/protected_retrieval_synthetic.py:45-130`
- Modify: `ai_worker/tests/evaluation/test_protected_retrieval.py`

**Interfaces:**
- Consumes: `ApprovalSourceEvidence`, `ProtectedAuthorizationGrant`, `VerifiedAuthorizationApproval`, `canonical_json_bytes`, and existing fixed audit reasons.
- Produces: `ControlCommandKind`, four strict command DTOs, `ControlCommandResult`, `TrustedApprovalSource`, `control_command_sha256`, `verify_authorization_approval`, and `ControlAuditOutcome`, `ControlAuditTargetKind`, and `ControlCommandAuditEntry` in the existing kernel audit union.

- [ ] **Step 1: Write failing command-contract tests**

Add tests that construct every command with literal UUIDv4/digest fixtures, reject UUIDv1 and extra fields, assert an independently calculated canonical hash literal, and prove changing each command field changes the hash. Add an async fake implementing:

```python
class TrustedApprovalSource(Protocol):
    async def fetch(self, source_event_id: str) -> ApprovalSourceEvidence:
        pass
```

Run: `uv run --group worker pytest ai_worker/tests/evaluation/test_protected_retrieval_control.py -q`

Expected: collection fails because `protected_retrieval_control` does not exist.

- [ ] **Step 2: Implement strict command contracts and hashing**

Implement these exact public models and enums:

```python
class ControlCommandKind(StrEnum):
    INGEST_APPROVAL = "INGEST_APPROVAL"
    GRANT = "GRANT"
    REVOKE = "REVOKE"
    EXPIRE = "EXPIRE"

class IngestApprovalCommand(StrictContractModel):
    request_id: str
    source_event_id: str = Field(min_length=1, max_length=160)
    expected_raw_sha256: Sha256Hex

class GrantAuthorizationCommand(StrictContractModel):
    request_id: str
    grant: ProtectedAuthorizationGrant
    expected_dataset_state_revision: int = Field(ge=1)

class RevokeAuthorizationCommand(StrictContractModel):
    request_id: str
    grant_id: str
    approval_source_event_id: str = Field(min_length=1, max_length=160)
    expected_raw_sha256: Sha256Hex
    expected_effective_revision: int = Field(ge=1)

class ExpireAuthorizationCommand(StrictContractModel):
    request_id: str
    grant_id: str
    expected_effective_revision: int = Field(ge=1)

class ControlCommandResult(StrictContractModel):
    request_id: str
    command_kind: ControlCommandKind
    target_id: str
    effective_revision: int | None
    authorization_audit_event_id: str | None
    reason_code: Literal["APPROVAL_VERIFIED", "AUTHORIZED", "REVOKED", "EXPIRED"]
```

Use one shared UUIDv4 validator for request and grant IDs. Build `payload = {"command_kind": kind.value, "command": command.model_dump(mode="json")}` and return `sha256(canonical_json_bytes(payload)).hexdigest()`.

- [ ] **Step 3: Write failing approval-policy and CONTROL-audit tests**

Cover the literal issuer matrix, action subsets, source action, raw hash, approved payload hash, implementation binding, self approval, participant approval, audit DTO strictness, and audit hash verification. Each test names the policy mutation it catches.

Run: `uv run --group worker pytest ai_worker/tests/evaluation/test_protected_retrieval_control.py ai_worker/tests/evaluation/test_protected_retrieval.py -q`

Expected: failures show the pure verifier, CONTROL enum, audit model, and audit union are absent.

- [ ] **Step 4: Implement the verifier and audit union**

Move the body of `InMemoryApprovalEvidenceVerifier._verify` into:

```python
def verify_authorization_approval(
    grant: ProtectedAuthorizationGrant,
    evidence: ApprovalSourceEvidence,
    action: AuthorizationAuditAction,
    expected_raw_sha256: str,
) -> VerifiedAuthorizationApproval:
    if evidence.source_event_id != grant.approval_source_event_id:
        raise ProtectedSecurityError("APPROVAL_EVIDENCE_MISMATCH")
    if evidence.authorization_action is not action:
        raise ProtectedSecurityError("APPROVAL_ACTION_MISMATCH")
    binding = grant.control_implementation
    if (
        evidence.state != "APPROVED"
        or evidence.issuer != grant.issuer
        or evidence.target_commit_oid != binding.commit_oid
        or evidence.target_artifact_sha256 != binding.artifact_sha256
        or evidence.canonical_raw_sha256 != expected_raw_sha256
        or evidence.implementation_participants != binding.participants
    ):
        raise ProtectedSecurityError("APPROVAL_EVIDENCE_MISMATCH")
    if evidence.approved_grant_payload_sha256 != authorization_grant_approval_sha256(grant):
        raise ProtectedSecurityError("APPROVAL_GRANT_BINDING_MISMATCH")
    if evidence.issuer.actor == grant.subject.actor or evidence.issuer.actor in binding.participants:
        raise ProtectedSecurityError("SELF_APPROVAL_DENIED")
    expected_role = (
        ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER
        if grant.subject.role is ProtectedPrincipalRole.DATASET_CUSTODIAN
        else ProtectedApprovalRole.DATASET_CUSTODIAN
    )
    if evidence.issuer.role is not expected_role:
        raise ProtectedSecurityError("ISSUER_ROLE_DENIED")
    allowed_actions = {
        ProtectedPrincipalRole.HOLDOUT_AUTHOR: {ProtectedAction.READ, ProtectedAction.WRITE},
        ProtectedPrincipalRole.DATASET_CUSTODIAN: {ProtectedAction.READ, ProtectedAction.FREEZE},
        ProtectedPrincipalRole.PROTECTED_RUNNER: {ProtectedAction.READ, ProtectedAction.RUN},
    }
    if not set(grant.actions) <= allowed_actions[grant.subject.role]:
        raise ProtectedSecurityError("ACTION_NOT_GRANTED")
    return VerifiedAuthorizationApproval._from_verified(evidence, grant, action)
```

Make the synthetic verifier only select trusted evidence and delegate. Add `CONTROL_COMMAND_CONFLICT` to the safe set and audit reason, `CONTROL` to `ProtectedAuditEventKind`, define `ControlCommandAuditEntry` beside the other audit DTOs, and extend `ProtectedAuditEntry` without importing the control module from the kernel.

```python
class ControlAuditOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    DENIED = "DENIED"

class ControlAuditTargetKind(StrEnum):
    APPROVAL_SOURCE_EVENT = "APPROVAL_SOURCE_EVENT"
    AUTHORIZATION_GRANT = "AUTHORIZATION_GRANT"

class ControlCommandAuditEntry(StrictContractModel):
    event_kind: Literal[ProtectedAuditEventKind.CONTROL]
    sequence: int = Field(ge=1)
    event_id: str
    command_kind: Literal["INGEST_APPROVAL", "GRANT", "REVOKE", "EXPIRE"]
    executed_by: ActorIdentity
    target_kind: ControlAuditTargetKind
    target_id: str
    command_sha256: Sha256Hex
    outcome: ControlAuditOutcome
    result_effective_revision: int | None
    authorization_audit_event_id: str | None
    reason_code: ProtectedAuditReason
    recorded_at: datetime
    previous_entry_sha256: Sha256Hex | None
    entry_sha256: Sha256Hex
```

- [ ] **Step 5: Verify the domain task**

Run: `uv run --group worker pytest ai_worker/tests/evaluation/test_protected_retrieval_control.py ai_worker/tests/evaluation/test_protected_retrieval.py -q`

Expected: all tests pass with no warning.

- [ ] **Step 6: Commit**

```bash
git add ai_worker/tasks/evaluation/protected_retrieval.py ai_worker/tasks/evaluation/protected_retrieval_control.py ai_worker/tasks/evaluation/protected_retrieval_synthetic.py ai_worker/tests/evaluation/test_protected_retrieval.py ai_worker/tests/evaluation/test_protected_retrieval_control.py
git commit -m "✨ feat: #368 authorization control 계약 추가"
```

### Task 3: Add the forward schema and exact C1 privileges

**Files:**
- Create: `infra/protected_retrieval/versions/368000000002_add_authorization_control.py`
- Modify: `infra/python/protected_retrieval_role_policy.py`
- Modify: `tests/migration/test_protected_retrieval_migration.py`

**Interfaces:**
- Consumes: protected revision `368000000001` and `validate_protected_control_connection`.
- Produces: DATA/CONTROL identity shapes, grant-scope revision uniqueness, CONTROL audit storage, and a control role limited to C1 columns.

- [ ] **Step 1: Write failing migration and ACL tests**

Extend the disposable fixture with Product/Safety and Custodian control logins. Assert existing identities backfill to DATA; valid CONTROL rows require `approval_role`; invalid mixed/null shapes fail; the same actor can have one DATA plus one CONTROL login but not two rows in one plane; parallel scope revisions fail; CONTROL audit inserts succeed. Assert the control login cannot INSERT/disable identities or INSERT/change Dataset lifecycle fields.

Run: `uv run --group app --group worker pytest tests/migration/test_protected_retrieval_migration.py -q`

Expected: new assertions fail against revision `368000000001` because identity-plane columns and CONTROL audit kind are absent and current ACL is broader.

- [ ] **Step 2: Implement revision `368000000002`**

Alter `protected_identity` with `identity_plane`, nullable `principal_role`, nullable `approval_role`, the exact DATA/CONTROL shape CHECK, and per-plane actor UNIQUE. Add the authorization-grant scope/revision UNIQUE. Replace the audit `event_kind` and operation-key CHECK constraints so CONTROL requires null `operation_key`. Preserve rows during upgrade and refuse downgrade when CONTROL identities/audits or revision-2-dependent rows exist.

- [ ] **Step 3: Narrow the role policy to C1**

Set control INSERT tables to `approval_evidence`, `authorization_grant`, and `audit_entry`; set control UPDATE to grant mutation columns, Dataset `lock_marker`, and audit head. Include new identity columns in SELECT and remove identity/Dataset administration columns.

- [ ] **Step 4: Verify migration and ACL behavior**

Run the command from Step 1.

Expected: the migration suite passes or reports only the repository-standard environment skip when `PROTECTED_TEST_DATABASE_URL` is unavailable.

- [ ] **Step 5: Commit**

```bash
git add infra/protected_retrieval/versions/368000000002_add_authorization_control.py infra/python/protected_retrieval_role_policy.py tests/migration/test_protected_retrieval_migration.py
git commit -m "🗃️ db: #368 authorization control 저장 경계 추가"
```

### Task 4: Implement approval ingestion and grant transactions

**Files:**
- Create: `ai_worker/adapters/postgresql_protected_retrieval_control.py`
- Create: `tests/integration/rag/test_protected_retrieval_control_postgresql.py`
- Modify: `ai_worker/adapters/postgresql_protected_retrieval.py:245-475`

**Interfaces:**
- Consumes: Task 2 command/verifier/audit contracts, Task 3 schema/ACL, `PostgresqlTrustedClock`, and the existing global audit hash rules.
- Produces: `PostgresqlProtectedAuthorizationControlService.ingest_approval` and `.grant`, plus shared audit parsing that recognizes all three audit kinds.

- [ ] **Step 1: Write failing real-service ingestion tests**

Using the real disposable PostgreSQL fixture and limited control login, assert: unauthorized login is rejected before fake-source invocation; exact source content inserts once; exact request replay returns the stored success; same request with changed hash raises `CONTROL_COMMAND_CONFLICT`; source/body mismatch persists no evidence; logs/exceptions contain no fixture evidence or actor.

Run: `uv run --group app --group worker pytest tests/integration/rag/test_protected_retrieval_control_postgresql.py -q`

Expected: collection fails because the PostgreSQL control service does not exist.

- [ ] **Step 2: Implement authenticated ingestion and generic CONTROL append**

Create a service owning an `AsyncEngine`, schema, data/control role names, and `TrustedApprovalSource`. Validate the limited connection in a read-only preparation session before `fetch`; repeat validation in the mutation transaction. Verify the global chain, insert immutable evidence conditionally, append CONTROL, update the head, and implement verified replay by request ID and command hash.

- [ ] **Step 3: Write failing grant transaction tests**

Seed a Dataset and DATA subject through the owner fixture. Assert exact issuer login/evidence identity, Dataset binding/revision, current database time, role/action matrix, subject identity, next scope revision, and approval payload hash. Assert mutation + AUTHORIZATION + CONTROL + head update commit together, and forced audit failure leaves no grant.

- [ ] **Step 4: Implement grant with ordered locks**

Within one transaction: verify replay; lock Dataset; lock the subject/Dataset grant scope; lock audit head through chain verification; validate persisted evidence with `verify_authorization_approval`; require `revision == max_revision + 1`; insert the grant; append AUTHORIZATION GRANT with `APPROVAL_VERIFIED`; append CONTROL GRANT with `AUTHORIZED`; commit one result.

- [ ] **Step 5: Verify ingestion and grant**

Run the Task 4 integration test command.

Expected: all implemented ingestion/grant tests pass, with no evidence body in captured output.

- [ ] **Step 6: Commit**

```bash
git add ai_worker/adapters/postgresql_protected_retrieval.py ai_worker/adapters/postgresql_protected_retrieval_control.py tests/integration/rag/test_protected_retrieval_control_postgresql.py
git commit -m "✨ feat: #368 approval ingestion과 grant 구현"
```

### Task 5: Implement revoke, expire, denial audit, and concurrency replay

**Files:**
- Modify: `ai_worker/adapters/postgresql_protected_retrieval_control.py`
- Modify: `tests/integration/rag/test_protected_retrieval_control_postgresql.py`

**Interfaces:**
- Consumes: Task 4 service, command replay, ordered audit append, and conditional grant DML.
- Produces: `.revoke`, `.expire`, post-commit fixed denial delivery, and concurrency-safe exact replay.

- [ ] **Step 1: Write failing revoke/expire tests**

Assert REVOKE requires persisted REVOKE evidence, exact issuer login, grant binding, and expected effective revision; it sets `revoked_at`, increments effective revision once, and appends both audits. Assert EXPIRE rejects database time before `expires_at`, succeeds at/after it without approval evidence, records the actual executor only in CONTROL, and increments once.

- [ ] **Step 2: Implement revoke and expire**

Lock target grant then audit head, validate immutable grant body and current effective revision, execute one conditional UPDATE, append the authorization transition and CONTROL success, and update the head in the same transaction.

- [ ] **Step 3: Write failing denial/concurrency tests**

Assert a known issuer, policy, revision, or early-expiry denial commits one CONTROL DENIED entry and then raises its fixed error outside the transaction. Assert exact denial replay raises the same reason without another entry. Run two identical request IDs concurrently and assert one state change plus one CONTROL event; change one payload field and assert conflict.

- [ ] **Step 4: Implement post-commit denial and uniqueness recovery**

Retain `ProtectedSecurityError` while inside the transaction, append CONTROL DENIED with no domain audit or mutation, commit, then raise. On audit `event_id` uniqueness conflict, roll back fully, open a fresh session, verify the winner's global chain and command hash, and return/raise its stored outcome.

- [ ] **Step 5: Verify all control integration behavior**

Run: `uv run --group app --group worker pytest tests/integration/rag/test_protected_retrieval_control_postgresql.py tests/integration/rag/test_protected_retrieval_postgresql.py -q`

Expected: both control and existing data-plane suites pass.

- [ ] **Step 6: Commit**

```bash
git add ai_worker/adapters/postgresql_protected_retrieval_control.py tests/integration/rag/test_protected_retrieval_control_postgresql.py
git commit -m "✨ feat: #368 revoke expire 멱등 감사 구현"
```

### Task 6: Wire fail-closed runtime assembly

**Files:**
- Modify: `ai_worker/core/runtime_assembly.py`
- Modify: `ai_worker/tests/core/test_runtime_assembly.py`
- Modify: `ai_worker/Dockerfile`
- Modify: `scripts/ci/verify_worker_image_startup.sh`

**Interfaces:**
- Consumes: `create_protected_control_engine`, the Task 5 service, and existing protected configuration validation.
- Produces: `create_protected_authorization_control_service(config, approval_source)` with validation and cleanup on failure.

- [ ] **Step 1: Write failing runtime tests**

Assert disabled/incomplete config fails before engine use; the factory passes only the control engine and exact schema/role names; startup validation runs; validation failure closes the engine; data/control identity equality is rejected by existing config validation.

Run: `uv run --group worker pytest ai_worker/tests/core/test_runtime_assembly.py -q`

Expected: failures show the service factory is missing.

- [ ] **Step 2: Implement runtime construction**

Add:

```python
async def create_protected_authorization_control_service(
    config: Config,
    approval_source: TrustedApprovalSource,
) -> PostgresqlProtectedAuthorizationControlService:
    if any(
        value is None
        for value in (
            config.PROTECTED_DB_SCHEMA,
            config.PROTECTED_DB_ACCESS_ROLE,
            config.PROTECTED_DB_CONTROL_ROLE,
        )
    ):
        raise RuntimeError("PROTECTED_RETRIEVAL_CONFIG_INVALID")
    assert config.PROTECTED_DB_SCHEMA is not None
    assert config.PROTECTED_DB_ACCESS_ROLE is not None
    assert config.PROTECTED_DB_CONTROL_ROLE is not None
    service = PostgresqlProtectedAuthorizationControlService(
        create_protected_control_engine(config),
        schema=config.PROTECTED_DB_SCHEMA.get_secret_value(),
        data_access_role=config.PROTECTED_DB_ACCESS_ROLE,
        control_role=config.PROTECTED_DB_CONTROL_ROLE,
        approval_source=approval_source,
    )
    try:
        await service.validate()
    except Exception:
        await service.close()
        raise
    return service
```

Construct the control engine, instantiate the service, call `validate()`, close on failure, and return only after exact privilege and identity validation.

- [ ] **Step 3: Preserve the protected-off Worker image boundary**

Add only the new runtime module required by imports to the existing Docker copy boundary and update the image-startup script so importing `ai_worker.main` with protected retrieval disabled still succeeds.

- [ ] **Step 4: Verify runtime and image boundary**

Run: `uv run --group worker pytest ai_worker/tests/core/test_runtime_assembly.py -q`

Run: `bash scripts/ci/verify_worker_image_startup.sh`

Expected: runtime tests pass and the script prints `worker-image-protected-off-import-ok`.

- [ ] **Step 5: Commit**

```bash
git add ai_worker/core/runtime_assembly.py ai_worker/tests/core/test_runtime_assembly.py ai_worker/Dockerfile scripts/ci/verify_worker_image_startup.sh
git commit -m "🔧 chore: #368 control service runtime 연결"
```

### Task 7: Align non-sensitive evidence without overstating readiness

**Files:**
- Modify: `ai_worker/tasks/evaluation/protected_retrieval_infrastructure_evidence.py`
- Modify: `ai_worker/tests/evaluation/test_protected_retrieval_infrastructure_evidence.py`
- Modify: `docs/validation/rag/issue-273/protected-runner-infrastructure-adapter.json`
- Modify: `docs/validation/rag/issue-273/protected-runner-infrastructure-adapter.md`

**Interfaces:**
- Consumes: implemented C1 files and passing verification counts.
- Produces: allowlisted repository evidence stating C1 repository implementation while keeping effective enforcement and C2/external gates closed.

- [ ] **Step 1: Write failing evidence tests**

Assert the builder reports approval ingestion and grant/revoke/expire repository status from verified files/tests, rejects forbidden recursive fields, and still reports lifecycle/FREEZE, production source connector, protected environment, HOLDOUT authoring/run, and effective enforcement as unimplemented/not started.

Run: `uv run --group worker pytest ai_worker/tests/evaluation/test_protected_retrieval_infrastructure_evidence.py -q`

Expected: failures show C1 evidence fields are absent.

- [ ] **Step 2: Implement and render the allowlisted evidence**

Add only booleans/counts/status strings and repository-relative refs. Regenerate JSON/Markdown with the existing renderer; do not include actor IDs, source IDs, evidence bodies, protected coordinates, credentials, or digests from a protected environment.

- [ ] **Step 3: Verify evidence and status honesty**

Run the Step 1 command and `git diff --check`.

Expected: tests pass; `effective_enforcement_status` remains `NOT_IMPLEMENTED`; HOLDOUT remains 0/unapproved/unfrozen/unexecuted.

- [ ] **Step 4: Commit**

```bash
git add ai_worker/tasks/evaluation/protected_retrieval_infrastructure_evidence.py ai_worker/tests/evaluation/test_protected_retrieval_infrastructure_evidence.py docs/validation/rag/issue-273/protected-runner-infrastructure-adapter.json docs/validation/rag/issue-273/protected-runner-infrastructure-adapter.md
git commit -m "📝 docs: #368 authorization control 증빙 정렬"
```

### Task 8: Full verification and branch review

**Files:**
- Review: every file changed since `origin/develop`.

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces: fresh verification evidence and a reviewable branch with no unrelated change.

- [ ] **Step 1: Run focused protected tests**

```bash
uv run --group app --group worker pytest ai_worker/tests/evaluation/test_protected_retrieval.py ai_worker/tests/evaluation/test_protected_retrieval_control.py ai_worker/tests/core/test_runtime_assembly.py ai_worker/tests/evaluation/test_protected_retrieval_infrastructure_evidence.py tests/migration/test_protected_retrieval_migration.py tests/integration/rag/test_protected_retrieval_postgresql.py tests/integration/rag/test_protected_retrieval_control_postgresql.py -q
```

Expected: all available tests pass; PostgreSQL-only tests skip solely when the configured test database is unavailable.

- [ ] **Step 2: Run static checks**

```bash
uv run --group app --group worker ruff check ai_worker/tasks/evaluation/protected_retrieval.py ai_worker/tasks/evaluation/protected_retrieval_control.py ai_worker/tasks/evaluation/protected_retrieval_synthetic.py ai_worker/adapters/postgresql_protected_retrieval.py ai_worker/adapters/postgresql_protected_retrieval_control.py ai_worker/core/runtime_assembly.py ai_worker/tests tests/integration/rag tests/migration/test_protected_retrieval_migration.py infra/python/protected_retrieval_role_policy.py infra/protected_retrieval/versions/368000000002_add_authorization_control.py
uv run --group app --group worker ruff format --check ai_worker infra/python infra/protected_retrieval tests
uv run --group app --group worker mypy ai_worker
```

Expected: every command exits 0.

- [ ] **Step 3: Run repository gates**

Run: `bash scripts/ci/check_no_db_logic.sh`

Run: `bash scripts/ci/run_test.sh`

Run: `git diff --check origin/develop...HEAD`

Expected: every available gate exits 0 and the DB logic check finds no prohibited object.

- [ ] **Step 4: Review the complete branch diff**

Run: `git diff --stat origin/develop...HEAD`

Run: `git diff origin/develop...HEAD`

Confirm every change maps to Issue #368 C1, no secret/protected content is present, the target was not promoted, C2 was not implemented, and named reviewers remain documented.

- [ ] **Step 5: Record final verification-only fixes**

If verification exposed a code defect, first add a failing regression test, verify its expected failure, apply the minimal fix, rerun the affected and full gates, then commit the test and fix together with a scope-specific message.
