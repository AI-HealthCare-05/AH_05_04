# Protected Retrieval Infrastructure Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the repository-side PostgreSQL protected retrieval adapter and prove that no direct SQL identity can bypass role, Dataset-state, grant, revoke, expiry, or audit enforcement.

**Architecture:** Preserve the existing protected retrieval domain DTOs and execution order while converting its infrastructure Protocol methods to async. A separate protected PostgreSQL migration creates a parameterized schema, `NOLOGIN` owner/access roles, function-only data access, immutable authorization/audit storage, and fixed-search-path definer functions. A thin SQLAlchemy adapter maps those functions to the kernel without registering a production CLI or enabling HOLDOUT access.

**Tech Stack:** Python 3.13, Pydantic 2, SQLAlchemy asyncio, asyncpg, Alembic, PostgreSQL 17, pytest/pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-10-protected-retrieval-infrastructure-adapter-design.md`

## Global Constraints

- Do not add dependencies.
- Do not add or expose HOLDOUT question, Gold, hard-negative, credential, key, schema-coordinate, or authorization-body values.
- Keep `PROTECTED_RETRIEVAL_ENABLED=false` by default and do not register `run-protected-holdout`.
- Give the runtime access role function `EXECUTE` only; never grant direct protected table or sequence privileges.
- Every definer function fixes `search_path`, revokes `PUBLIC EXECUTE`, and belongs to the protected `NOLOGIN` owner.
- Do not implement retention deletion, disposal, or #425 lifecycle behavior.
- Use synthetic payloads and disposable roles/schema names in all tests.
- Keep effective enforcement, access authorization, authoring, Freeze, Runner execution, and publication blocked in public evidence.

---

### Task 1: Align the approved Decision and repository references

**Files:**
- Rename: `docs/governance/decisions/2026-09-09-protected-retrieval-runner-access-control-candidate.md` → `docs/governance/decisions/2026-09-09-protected-retrieval-runner-access-control.md`
- Modify: repository Markdown and source files that reference the candidate path

**Interfaces:**
- Consumes: PR #386 approval and `PD-368-20260909`.
- Produces: one stable approved Decision path for later implementation evidence.

- [ ] **Step 1: Find every candidate-path reference**

Run: `rg -n "2026-09-09-protected-retrieval-runner-access-control-candidate" .`

- [ ] **Step 2: Rename and update the Decision status**

Change the status row to `Approved Target · Not implemented` and add a short approval-evidence note naming PR #386, the approved review actor, review timestamp, and reviewed commit OID already present in GitHub metadata. Preserve the explicit implementation and activation blockers.

- [ ] **Step 3: Update all references**

Use the new filename everywhere. Do not change unrelated Decision or contract wording.

- [ ] **Step 4: Verify document consistency**

Run: `rg -n "protected-retrieval-runner-access-control-(candidate|proposal)" .`
Expected: no output.

Run: `git diff --check`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add docs/governance docs/contracts docs/validation ai_worker
git commit -m "📝 docs: PD-368 승인 상태 정렬"
```

### Task 2: Convert the kernel infrastructure seam to async

**Files:**
- Modify: `ai_worker/tasks/evaluation/protected_retrieval.py`
- Modify: `ai_worker/tasks/evaluation/protected_retrieval_synthetic.py`
- Modify: `ai_worker/tests/evaluation/test_protected_retrieval.py`

**Interfaces:**
- Consumes: existing `AuthorizationLedger`, `GuardSession`, `ProtectedAuditJournal`, and `execute_protected_operation` behavior.
- Produces: awaitable `require_dataset`, `operation_history`, `append_operation`, `GuardSession.require_current`, `issue_capability`, and `consume` methods with unchanged DTO/result semantics.

- [ ] **Step 1: Write the failing async-spy test**

Add a test whose adapter methods set flags only after an `await asyncio.sleep(0)` boundary. Execute one authorized operation and assert every journal/guard method was awaited and the operation succeeded once. The production change caught by this test is a coordinator that calls an async database method without awaiting it.

- [ ] **Step 2: Verify RED**

Run: `UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest ai_worker/tests/evaluation/test_protected_retrieval.py -k awaits_infrastructure_protocols -q`
Expected: FAIL because the current coordinator treats returned coroutines as domain values.

- [ ] **Step 3: Make Protocol and coordinator calls awaitable**

Use these signatures:

```python
class AuthorizationLedger(Protocol):
    async def require_dataset(self, request: ProtectedOperationRequest) -> ProtectedDatasetBinding: ...

class GuardSession(Protocol):
    async def require_current(self, grant_id: str) -> ProtectedAuthorizationGrant: ...
    async def issue_capability(
        self, request: ProtectedOperationRequest, grant: ProtectedAuthorizationGrant
    ) -> ProtectedAuthorizationCapability: ...
    async def consume(self, capability: ProtectedAuthorizationCapability) -> None: ...

class ProtectedAuditJournal(Protocol):
    async def operation_history(self, request: ProtectedOperationRequest) -> tuple[OperationAuditEntry, ...]: ...
    async def append_operation(
        self,
        request: ProtectedOperationRequest,
        grant: ProtectedAuthorizationGrant | None,
        outcome: OperationAuditOutcome,
        reason_code: ProtectedAuditReason | str,
        capability: ProtectedAuthorizationCapability | None = None,
        result: ProtectedOperationResult | None = None,
        *,
        closes_intent: bool = False,
    ) -> OperationAuditEntry: ...
```

Update the synthetic methods to `async def` and await them throughout `execute_protected_operation` without changing validation order.

- [ ] **Step 4: Verify GREEN and regression**

Run: `UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest ai_worker/tests/evaluation/test_protected_retrieval.py -q`
Expected: all protected retrieval tests pass.

- [ ] **Step 5: Commit**

```bash
git add ai_worker/tasks/evaluation/protected_retrieval.py ai_worker/tasks/evaluation/protected_retrieval_synthetic.py ai_worker/tests/evaluation/test_protected_retrieval.py
git commit -m "♻️ refactor: protected retrieval DB 경계 비동기화"
```

### Task 3: Add fail-closed protected connection configuration

**Files:**
- Modify: `ai_worker/core/config.py`
- Modify: `ai_worker/tests/core/test_config.py`
- Modify: `envs/.env.example`

**Interfaces:**
- Produces: `Config.protected_database_url: URL` and disabled-by-default protected settings.
- Consumes: existing `DeploymentEnvironment`, `SecretStr`, and SQLAlchemy `URL.create` conventions.

- [ ] **Step 1: Write configuration failure tests**

Add tests proving:

```python
assert local_config.PROTECTED_RETRIEVAL_ENABLED is False
```

and that enabled configuration fails when any protected host, database, user, password, or schema value is absent; non-local placeholder values fail; and normal `DB_*` values are never used as fallback.

- [ ] **Step 2: Verify RED**

Run: `UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest ai_worker/tests/core/test_config.py -k protected_retrieval -q`
Expected: FAIL because the settings do not exist.

- [ ] **Step 3: Add settings and validators**

Add:

```python
PROTECTED_RETRIEVAL_ENABLED: bool = False
PROTECTED_DB_HOST: str | None = None
PROTECTED_DB_PORT: int = Field(default=5432, ge=1, le=65535)
PROTECTED_DB_NAME: str | None = None
PROTECTED_DB_USER: str | None = None
PROTECTED_DB_PASSWORD: SecretStr | None = None
PROTECTED_DB_SCHEMA: SecretStr | None = None
```

When enabled, normalize and require every value, reject `replace-with-` outside Local, reject exact equality with the normal Worker connection identity, and build the URL with `URL.create`. Do not expose a URL property while disabled.

- [ ] **Step 4: Verify GREEN**

Run: `UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest ai_worker/tests/core/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai_worker/core/config.py ai_worker/tests/core/test_config.py envs/.env.example
git commit -m "✨ feat: protected retrieval 연결 설정 차단 경계 추가"
```

### Task 4: Create the isolated protected PostgreSQL migration

**Files:**
- Create: `infra/protected_retrieval/alembic.ini`
- Create: `infra/protected_retrieval/env.py`
- Create: `infra/protected_retrieval/script.py.mako`
- Create: `infra/protected_retrieval/versions/368000000001_create_protected_retrieval.py`
- Create: `tests/migration/test_protected_retrieval_migration.py`
- Modify: `tests/contract/test_python_test_inventory.py` only if its existing classification requires an explicit path entry

**Interfaces:**
- Consumes environment variables `PROTECTED_DATABASE_URL`, `PROTECTED_DB_SCHEMA`, `PROTECTED_DB_OWNER_ROLE`, and `PROTECTED_DB_ACCESS_ROLE`.
- Produces a separate Alembic head `368000000001` that never runs in the normal Backend migration chain.

- [ ] **Step 1: Write the failing disposable-PostgreSQL migration test**

The test must create unique synthetic role/schema identifiers, run the separate Alembic config, and query PostgreSQL behavior. It must prove normal app and access identities cannot directly select, insert, update, delete, truncate, use sequences, create schema objects, or execute unapproved functions. It must also prove the access identity can call the approved principal-resolution function.

The production changes caught are missing schema isolation, an accidental direct DML grant, or a function left executable by `PUBLIC`.

- [ ] **Step 2: Verify RED**

Run: `UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest tests/migration/test_protected_retrieval_migration.py -q`
Expected: FAIL because the protected migration config does not exist.

- [ ] **Step 3: Implement the separate migration environment**

`env.py` must refuse missing variables, validate every identifier with `^[A-Za-z_][A-Za-z0-9_]{0,62}$`, configure its own version table inside the protected schema, and disable SQL value logging. It must never import normal Backend settings.

- [ ] **Step 4: Implement protected roles, relations, and functions**

Create relations for identity, Dataset binding, artifact envelope, approval evidence, grant, operation reservation/result, audit entry, and audit head. Use constraints for UUID/hash/enum/UTC-compatible values and unique operation scope.

Create specific definer functions for identity resolution, grant/revoke, operation history, begin operation, capability consume, artifact read/write, Dataset transition, and terminal audit append. Functions derive `session_user`, enforce the approved role/action/state matrix, use fixed safe reason codes, lock in Dataset → grant → operation → audit-head order, and expose no generic SQL executor.

Revoke every schema/object/default privilege from `PUBLIC`, normal app/migration roles, and the access role. Grant only schema `USAGE` plus explicit approved function `EXECUTE` to the access role.

- [ ] **Step 5: Add metadata and downgrade safety assertions**

Query `pg_proc`, `pg_namespace`, `pg_roles`, `information_schema.role_*_grants`, and `aclexplode` to assert owner, fixed `search_path`, privilege denial, and default privileges. Downgrade must fail if protected Dataset, artifact, grant, or audit rows exist; an empty disposable schema may downgrade cleanly.

- [ ] **Step 6: Verify GREEN**

Run: `UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest tests/migration/test_protected_retrieval_migration.py -q`
Expected: PASS or an explicit environment skip only when the disposable PostgreSQL URL is unavailable.

- [ ] **Step 7: Commit**

```bash
git add infra/protected_retrieval tests/migration/test_protected_retrieval_migration.py tests/contract/test_python_test_inventory.py
git commit -m "✨ feat: protected retrieval PostgreSQL 경계 추가"
```

### Task 5: Implement the SQLAlchemy protected retrieval adapter

**Files:**
- Create: `ai_worker/adapters/postgresql_protected_retrieval.py`
- Create: `tests/integration/rag/test_protected_retrieval_postgresql.py`
- Modify: `tests/contract/test_python_test_inventory.py` only if needed for the new integration suite classification

**Interfaces:**
- Consumes: the async Protocols from Task 2 and SQL functions from Task 4.
- Produces: `PostgresqlTrustedClock`, `PostgresqlApprovalEvidenceVerifier`, `PostgresqlAuthorizationLedger`, `PostgresqlProtectedAuditJournal`, `PostgresqlAuthorizationGuard`, and `PostgresqlProtectedArtifactOperation`.

- [ ] **Step 1: Write the failing positive integration test**

Seed only synthetic Dataset/envelope/approval/grant values through protected control functions. Execute an authorized Author write and an authorized Runner read through `execute_protected_operation`. Assert the opaque result, single side effect, audit `INTENT → SUCCEEDED`, and zero protected scalars in returned models/log capture.

- [ ] **Step 2: Verify RED**

Run: `UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest tests/integration/rag/test_protected_retrieval_postgresql.py -q`
Expected: FAIL because the adapter classes do not exist.

- [ ] **Step 3: Implement model mapping and safe database errors**

Every SQL result maps into the existing strict Pydantic DTO. Map explicitly recognized authorization/audit SQLSTATEs to existing safe reason codes. Map all other DBAPI/connection/malformed-row failures to `INTERNAL_ERROR`, `AUDIT_UNAVAILABLE`, or `OPERATION_OUTCOME_UNKNOWN` according to whether a side effect may have happened. Never format the original statement, parameters, or exception message into `ProtectedSecurityError`.

- [ ] **Step 4: Implement ledger, journal, and guard on one session**

Require a caller-owned `AsyncSession` transaction. Use only qualified function calls and bind parameters for data. The guard holds row/advisory locks until the terminal append. Reject a session whose authenticated database identity is absent from the protected identity map.

- [ ] **Step 5: Implement opaque artifact operation**

Consume the database capability exactly once before reading/writing envelope bytes. Return only `ProtectedOperationResult(result_ref=<opaque UUIDv4>, reason_code="PROTECTED_OPERATION_SUCCEEDED")`. Keep payload bytes private to the adapter callback boundary.

- [ ] **Step 6: Add negative and concurrency tests**

Add real tests for missing/expired/revoked/mismatched grants, unfrozen Runner read, frozen Author write, self-approval, implementation-participant approval, direct SQL denial, failed intent, terminal audit loss, duplicate operation replay, concurrent revoke/state transition, audit CAS conflict, tampered history, and forbidden-value non-leakage.

- [ ] **Step 7: Verify GREEN**

Run: `UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest tests/integration/rag/test_protected_retrieval_postgresql.py -q`
Expected: PASS or an explicit environment skip only when the dedicated disposable test database URL is unavailable.

Run: `UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest ai_worker/tests/evaluation/test_protected_retrieval.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add ai_worker/adapters/postgresql_protected_retrieval.py tests/integration/rag/test_protected_retrieval_postgresql.py tests/contract/test_python_test_inventory.py
git commit -m "✨ feat: protected retrieval PostgreSQL adapter 구현"
```

### Task 6: Add explicit protected runtime assembly without activation

**Files:**
- Modify: `ai_worker/core/runtime_assembly.py`
- Modify: `ai_worker/tests/core/test_runtime_assembly.py`

**Interfaces:**
- Consumes: `Config.protected_database_url` and the Task 5 adapter classes.
- Produces: an explicit `create_protected_retrieval_engine(config)` and transaction-scoped adapter factory that are never called by normal Worker assembly while the feature flag is false.

- [ ] **Step 1: Write the failing assembly tests**

Assert normal `create_runtime()` never reads protected settings or creates a second engine. Assert the explicit factory refuses disabled configuration, uses the separate URL, forces SQL echo off, and returns no CLI/handler registration.

- [ ] **Step 2: Verify RED**

Run: `UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest ai_worker/tests/core/test_runtime_assembly.py -k protected_retrieval -q`
Expected: FAIL because the explicit factory does not exist.

- [ ] **Step 3: Implement the minimal explicit factory**

Keep the factory outside normal runtime startup. Use `NullPool` initially so short-lived protected credentials and connection identity do not survive between approved executions. Do not add retry or reconnect behavior.

- [ ] **Step 4: Verify GREEN**

Run: `UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest ai_worker/tests/core/test_runtime_assembly.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai_worker/core/runtime_assembly.py ai_worker/tests/core/test_runtime_assembly.py
git commit -m "✨ feat: protected retrieval 명시적 runtime 조립 추가"
```

### Task 7: Record non-sensitive implementation evidence and verify the repository

**Files:**
- Modify: `docs/governance/decisions/2026-09-09-protected-retrieval-runner-access-control.md`
- Create: `docs/validation/rag/issue-273/protected-runner-infrastructure-adapter.json`
- Create: `docs/validation/rag/issue-273/protected-runner-infrastructure-adapter.md`
- Modify: `ai_worker/tasks/evaluation/natural_language_retrieval_protected_runner_foundation.py` or create a focused evidence builder if existing immutability rules require a new artifact
- Modify: corresponding deterministic evidence tests

**Interfaces:**
- Consumes: verified implementation commit inputs and test results.
- Produces: exact-field non-sensitive evidence that distinguishes repository implementation from real-environment enforcement.

- [ ] **Step 1: Write the failing evidence test**

Assert the new artifact records repository adapter implementation while preserving:

```json
{
  "effective_enforcement_status": "NOT_IMPLEMENTED",
  "access_authorized": false,
  "holdout_authored": false,
  "freeze_recorded": false,
  "actual_run_ref": null,
  "disposal_status": "BLOCKED_BY_ISSUE_425"
}
```

Reject recursively any field or scalar containing protected path, credential, key material, HMAC value, question,
Gold, hard-negative label, schema coordinate, or authorization receipt body.

- [ ] **Step 2: Verify RED**

Run the focused evidence test and expect failure because the artifact/builder does not exist.

- [ ] **Step 3: Implement deterministic evidence generation**

Bind the Decision ID/path, implementation files and SHA-256 values, test command summaries, responsible reviewers, and remaining activation gates. Do not claim the real SQL tests ran if they were skipped for lack of a disposable database.

- [ ] **Step 4: Run focused and full verification**

Run, in order:

```bash
UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest ai_worker/tests/evaluation/test_protected_retrieval.py ai_worker/tests/core/test_config.py ai_worker/tests/core/test_runtime_assembly.py -q
UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest tests/migration/test_protected_retrieval_migration.py tests/integration/rag/test_protected_retrieval_postgresql.py -q
UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest ai_worker/tests/evaluation -q
UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run pytest tests/contract/test_python_test_inventory.py -q
UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run ruff check ai_worker/tasks/evaluation/protected_retrieval.py ai_worker/tasks/evaluation/protected_retrieval_synthetic.py ai_worker/adapters/postgresql_protected_retrieval.py ai_worker/core/config.py ai_worker/core/runtime_assembly.py ai_worker/tests/evaluation/test_protected_retrieval.py ai_worker/tests/core/test_config.py ai_worker/tests/core/test_runtime_assembly.py tests/migration/test_protected_retrieval_migration.py tests/integration/rag/test_protected_retrieval_postgresql.py
UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run ruff format ai_worker/tasks/evaluation/protected_retrieval.py ai_worker/tasks/evaluation/protected_retrieval_synthetic.py ai_worker/adapters/postgresql_protected_retrieval.py ai_worker/core/config.py ai_worker/core/runtime_assembly.py ai_worker/tests/evaluation/test_protected_retrieval.py ai_worker/tests/core/test_config.py ai_worker/tests/core/test_runtime_assembly.py tests/migration/test_protected_retrieval_migration.py tests/integration/rag/test_protected_retrieval_postgresql.py --check
UV_CACHE_DIR=/private/tmp/ah368_uv_cache uv run mypy backend/app ai_worker
git diff --check
```

Then run `bash scripts/ci/run_test.sh` when the repository-defined local PostgreSQL environment is available. Report every skip or unavailable operational check exactly.

- [ ] **Step 5: Review the complete diff against the spec**

Confirm every completion criterion and global constraint has direct code/test evidence. Confirm no unrelated file or generated secret was added.

- [ ] **Step 6: Commit**

```bash
git add docs/validation docs/governance ai_worker/tasks/evaluation ai_worker/tests/evaluation
git commit -m "📝 docs: #368 adapter 구현 증빙 기록"
```
