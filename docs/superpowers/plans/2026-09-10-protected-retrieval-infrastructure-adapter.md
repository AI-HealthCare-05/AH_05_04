# Protected Retrieval Infrastructure Adapter Implementation Plan

> **For Codex:** Execute this plan inline with `superpowers:executing-plans`. The current task does not authorize
> subagent delegation. Apply `superpowers:test-driven-development` to every behavior change and use a uniquely named
> disposable PostgreSQL database for migration and limited-login tests.

**Goal:** Replace the superseded protected PostgreSQL function boundary with an ordinary-schema, least-privilege,
Python transaction adapter while keeping the protected environment and publication gates disabled.

**Architecture:** Alembic owns ordinary tables, constraints, ownership, and closed defaults only. A dedicated Python
role-policy module grants and validates exact data/control column privileges. The existing protected-retrieval kernel
continues to own policy decisions while SQLAlchemy repositories resolve the authenticated principal, lock rows in a
fixed order, perform conditional DML, and append hash-chain audit records in the caller-owned transaction.

**Tech stack:** Python 3.13, Pydantic v2, SQLAlchemy asyncio, Alembic, PostgreSQL, pytest, Ruff, Mypy.

---

## Task 1: Lock the ordinary-schema contract with migration tests

**Files:**

- Modify: `tests/migration/test_protected_retrieval_migration.py`
- Modify: `infra/protected_retrieval/env.py`
- Modify: `infra/protected_retrieval/versions/368000000001_create_protected_retrieval.py`

1. Replace function-execution assertions with behavioral assertions that a migrated schema contains no user-defined
   functions, procedures, triggers, or policies; omits `operation_result`; contains required lock markers and ordinary
   constraints; and keeps public/default privileges closed.
2. Extend the disposable fixture with distinct `NOLOGIN` owner, data-access, and control roles plus separate data,
   control, and denied logins. Keep login provisioning outside the migration.
3. Run the focused migration tests against a unique disposable database and confirm they fail because the current
   migration still creates functions and lacks the control boundary.
4. Rewrite the migration to create only the approved relations, constraints, indexes, owners, and closed defaults.
   Add constant lock-marker columns where limited `SELECT FOR UPDATE` needs an update privilege. Remove all function
   DDL, function ACLs, and `operation_result`.
5. Update the protected Alembic environment to require and validate the separate control role without granting it
   migration ownership.
6. Re-run the migration tests and `uv run python scripts/ci/check_database_logic.py`; require both to pass.

## Task 2: Provision and validate exact data/control role policy

**Files:**

- Create: `infra/python/protected_retrieval_role_policy.py`
- Modify: `tests/migration/test_protected_retrieval_migration.py`

1. Add real limited-login tests proving each required data/control operation succeeds and cross-plane or blanket
   INSERT/UPDATE/DELETE/TRUNCATE/CREATE/sequence access fails. Assert effective privileges, not generated SQL text.
2. Run the focused tests and confirm failure because no protected role-policy module exists.
3. Implement identifier validation, complete revoke/default-privilege closure, exact column grants, and exhaustive
   startup validation for owner/data/control identity separation, memberships, ownership, schema rights, table rights,
   and column rights.
4. Apply the policy in the fixture, connect as each real login, and re-run the tests until green.

## Task 3: Make configuration and runtime assembly plane-specific

**Files:**

- Modify: `ai_worker/tests/core/test_config.py`
- Modify: `ai_worker/tests/core/test_runtime_assembly.py`
- Modify: `ai_worker/core/config.py`
- Modify: `ai_worker/core/runtime_assembly.py`

1. Add tests requiring complete data and control connection tuples, pairwise-distinct data/control/normal Worker
   identities, fail-closed partial configuration, redacted secrets, disabled-by-default behavior, `NullPool`, SQL echo
   off, and distinct application names.
2. Run the focused tests and confirm they fail against the single protected credential model.
3. Add control user/password configuration and separate data/control URL properties. Keep host, port, database, and
   schema common and reject placeholders outside Local.
4. Replace the single engine factory with explicit data/control factories and keep adapter construction bound to an
   already-open transaction.
5. Re-run the focused tests until green.

## Task 4: Replace function calls with explicit repository SQL

**Files:**

- Modify: `tests/integration/rag/test_protected_retrieval_postgresql.py`
- Modify: `ai_worker/adapters/postgresql_protected_retrieval.py`

1. Adapt the existing synthetic integration fixture to the exact role policy and add failure cases for authenticated
   principal mismatch, disabled identity, missing/revoked/expired grant, stale Dataset revision, repeated capability
   consumption, and audit-head/tail tamper. Keep expectations hand-derived and inspect ordinary relations only through
   the owner fixture.
2. Run the focused integration tests and confirm the old adapter fails because its SQL functions no longer exist.
3. Implement explicit parameterized SELECT/INSERT/UPDATE SQL for trusted time, principal resolution, Dataset/grant
   loading, ordered locks, conditional capability issue/consume/operate, artifact read/write, audit verification,
   append, and replay. Decode stored JSON through existing Pydantic DTOs and map database failures to the fixed safe
   reason allowlist.
4. Preserve one caller-owned `AsyncSession` transaction and the existing kernel interfaces; do not register a normal
   Worker or CLI path.
5. Re-run integration tests until green, including atomic rollback and idempotent replay.

## Task 5: Implement the minimum control-plane repository boundary

**Files:**

- Modify: `tests/integration/rag/test_protected_retrieval_postgresql.py`
- Modify: `ai_worker/adapters/postgresql_protected_retrieval.py`
- Modify: `ai_worker/core/runtime_assembly.py`

1. Add integration tests for approval evidence insertion, grant creation, revoke/expire, identity disable, Dataset
   transition, and Freeze. Prove self-approval/implementation-participant conflicts, stale expected revisions, and
   duplicate request payload conflicts fail without partial state or audit writes.
2. Confirm tests fail before implementation.
3. Add an explicit control repository/service using the control session, opaque request IDs, expected revisions/hashes,
   conditional DML, and the same audit-head transaction. Reuse existing DTO validators and safe errors.
4. Expose construction only through the explicit protected control engine; keep activation and CLI registration absent.
5. Re-run focused integration and runtime tests until green.

## Task 6: Regenerate non-sensitive evidence and align governed documents

**Files:**

- Modify: `ai_worker/tests/evaluation/test_protected_retrieval_infrastructure_evidence.py`
- Modify: `ai_worker/tasks/evaluation/protected_retrieval_infrastructure_evidence.py`
- Modify: generated protected-retrieval evidence JSON/Markdown and their index/report references
- Modify: the approved Decision/contract status references already changed by PR #432, if their implementation claims
  or artifact hashes are stale

1. Add tests that reject forbidden DB objects, single-plane credentials, stale artifact hashes, leaked values, and any
   claim that effective protected enforcement or publication is active.
2. Confirm the updated tests fail against stale evidence.
3. Update the evidence builder and generated artifacts to describe the Python/ordinary-SQL implementation, include
   current hashes and check results, and retain `NOT_IMPLEMENTED`/blocked activation states required by the design.
4. Run focused evidence tests and the repository evidence generator/check command documented by the module.

## Task 7: Full verification and PR handoff

**Files:** All changed files.

1. Run focused protected kernel, config, runtime, migration, role-policy, integration, and evidence tests.
2. Run both database policy checks, relevant Evaluation suites, protected Alembic empty upgrade/downgrade/upgrade, and
   the repository migration suite against a unique disposable database.
3. Run `uv run ruff check .`, `uv run ruff format --check .`, the repository Mypy command, and
   `bash scripts/ci/run_test.sh` as required by `CONTRIBUTING.md`; record any environment-only gap exactly.
4. Run `git diff --check`, inspect the complete diff, confirm the legacy exception manifest and activation defaults
   are unchanged, and verify no secret or protected payload appears.
5. Commit coherent TDD slices, push the rebased branch, update the PR body from the repository template, retain
   `@hazelnutflavoured` and `@phina-io` as responsible reviewers, and report the remaining external activation gates.
