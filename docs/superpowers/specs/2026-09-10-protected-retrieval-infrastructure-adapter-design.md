# Protected Retrieval Infrastructure Adapter Design

## Status and authority

- Status: Approved for repository implementation by the task requester on 2026-09-10.
- Tracking issue: #368.
- Governing decision: `PD-368-20260909` from PR #386.
- Implementation owner: 정현우 (`@ceohwj`).
- Required reviewers: 권가빈 (`@hazelnutflavoured`) for Product, Privacy, Safety, and Evaluation; 송은영
  (`@phina-io`) for Backend and Security controls.
- Custodian separation: if 송은영 participates in the ACL implementation, 김지혜 (`@Jye-rookie`) becomes the
  independent Dataset Custodian approver.

This design is an implementation target, not evidence that the protected environment, HOLDOUT access, authoring,
Freeze, Runner execution, retention job, or Production publication is active.

## Goal

Connect the existing protected retrieval security kernel to PostgreSQL without allowing a direct SQL path to bypass
role, Dataset state, grant revision, expiry, revoke, or audit checks. The repository implementation must be testable
with synthetic data while every real protected-environment activation gate remains closed.

## Scope

Included:

- a dedicated PostgreSQL schema and `NOLOGIN` owner/access roles;
- parameterized provisioning that does not commit deployed host, database, schema, login identity, or credential
  values;
- durable Dataset, authorization, approval evidence, artifact envelope, audit journal, and audit-head storage;
- function-only data-plane access for read, write, Freeze, and Runner preparation;
- direct SQL denial for tables, sequences, and functions not explicitly granted;
- an asynchronous SQLAlchemy adapter for the existing protected retrieval kernel;
- fail-closed configuration for a separate protected PostgreSQL credential;
- real PostgreSQL migration and negative integration tests using synthetic identifiers and payloads;
- non-sensitive implementation evidence and Decision status alignment.

Excluded:

- HOLDOUT question, Gold, or hard-negative authoring;
- `run-protected-holdout` CLI registration;
- the #178 Retriever Adapter and actual metric execution;
- production secret creation or injection;
- real protected schema, table location, login identity, key material, or authorization receipt body;
- automatic retention, disposal, lifecycle-marker, or deletion behavior tracked by #425;
- activation of `PUBLIC_TRACK_F` or any external approval gate.

## Constraints

1. Application Service remains the visible orchestration boundary. PostgreSQL functions exist only to prevent direct
   SQL from bypassing the same approved checks.
2. `protected_access_role` receives no direct table, sequence, or schema-create privileges.
3. Every callable `SECURITY DEFINER` function fixes `search_path` to `pg_catalog`, the trusted protected schema, and
   `pg_temp`; revokes `PUBLIC EXECUTE`; and is owned by the protected owner role.
4. Runtime principals come from `session_user`/`current_user` and an explicit protected identity mapping. Callers
   cannot supply an actor string as proof of identity.
5. Shared logins are not supported. Deployed individual human and service login roles are provisioned outside the
   repository and receive only membership in the `NOLOGIN` access role.
6. The adapter returns domain DTOs and opaque UUIDv4 references only. It never returns storage coordinates through
   the kernel, errors, logs, or public evidence.
7. SQLAlchemy echo is always disabled for the protected engine.
8. Repository tests use de-identified synthetic payload bytes and disposable test roles/schema names only.
9. No dependency is added. SQLAlchemy, asyncpg, Alembic, Pydantic, and the existing test stack are reused.
10. Disposal stays impossible: no delete API, retention worker, disposal enum, or lifecycle transition is added.

## Considered approaches

### Function-only protected access — selected

The access role can execute narrowly scoped functions but cannot access protected tables directly. Each function
derives the caller identity, locks the Dataset/grant/audit head, checks the allowed role/state/action, and appends the
corresponding audit transition in the same transaction.

This is the smallest design that closes the PR #386 `[WATCH]` finding for both reads and writes. It also provides a
SQL-negative-testable boundary and follows the approved `SECURITY DEFINER` precedent.

Cost: validation rules exist in Python and SQL. Contract tests must keep the role/action/state matrix and safe reason
codes aligned.

### RLS plus triggers — rejected

RLS can restrict row visibility but does not provide a complete, durable audit event for every `SELECT`. Trigger-based
write auditing also leaves reads on a different enforcement mechanism. It would introduce an RLS policy not approved
by PD-368 and increase policy-debugging cost.

### Direct DML with Python-only guard — rejected

This reuses the current kernel with fewer database objects, but any raw SQL session holding the access role could read
an unfrozen Dataset or write without approval and audit. It does not resolve the merge-time `[WATCH]` requirement.

## Architecture

### 1. Configuration and connection isolation

The Worker configuration adds an explicit `PROTECTED_RETRIEVAL_ENABLED` switch whose default is `false`. Enabling it
requires a complete, separate protected connection tuple and schema identifier. Partial configuration fails during
settings validation. Non-local values reject repository placeholder prefixes.

The protected engine is assembled only by an explicit protected runtime factory. It does not replace or reuse the
normal Worker `DB_USER`/`DB_PASSWORD` engine and is not registered in the current CLI or consumer registry.

Deployment coordinates are environment values. Test values are fixed synthetic names that cannot match a deployed
environment. The engine uses `pool_pre_ping`, the existing connect timeout, no SQL echo, and an application name that
contains no Dataset or actor value.

### 2. Protected PostgreSQL objects

Provisioning creates two group roles:

- protected owner role: `NOLOGIN`, owns the schema, tables, sequences, and functions;
- protected access role: `NOLOGIN`, has schema `USAGE` and explicit function `EXECUTE` only.

The schema stores these logical relations:

- Dataset binding and monotonic state revision;
- opaque artifact envelope bytes and their digest/key-version binding;
- immutable approval source evidence and canonical raw hash;
- authorization grant and effective revision/revoked/expired state;
- append-only authorization/operation audit entries;
- the single global audit sequence/head checkpoint;
- opaque operation result references used for idempotent replay.

Logical relation and function names are part of the implementation contract; the deployed database and schema
coordinates remain secret configuration. No relation has a general-purpose update/delete interface. State changes,
grant changes, and operation lifecycle transitions occur through functions only.

Every table denies `PUBLIC`, normal `app_user`, normal migration/CI roles, and the protected access role. Default
privileges repeat the denial for future tables, sequences, and functions. The access role receives only the approved
function signatures.

### 3. SQL enforcement functions

Functions are split by responsibility rather than exposing a generic command executor:

- resolve the authenticated principal from the database session identity;
- read and verify immutable approval evidence;
- grant, revoke, and observe expiry with authorization audit atomicity;
- load the current Dataset binding;
- begin a protected operation by validating principal, role, action, Dataset state, binding, grant revision, and
  expiry, then appending `INTENT`;
- read or write an opaque artifact envelope only after a valid operation capability exists;
- transition to `REVIEW_READY` or `FROZEN` only when the approved evidence predicates hold;
- close an operation as `SUCCEEDED`, `UNKNOWN`, or an intent-closing `DENIED`;
- read operation history and verified opaque results for replay/reconciliation decisions.

The functions use row locks on the Dataset, grant, operation scope, and audit head in a consistent order. The global
audit head is updated with a compare-and-swap predicate. A mismatch fails the transaction with a fixed safe reason
code. SQL exceptions do not include payload, object coordinates, approval body, or caller-provided text.

### 4. Kernel async boundary

The current kernel defines synchronous journal and guard-session methods, while every SQLAlchemy/asyncpg operation is
awaitable. The Protocols and coordinator therefore become asynchronous without changing DTOs, enums, safe reason
codes, validation order, or outcome transitions.

The following calls become awaitable:

- Dataset lookup;
- operation history lookup;
- audit append;
- guard-session current-grant validation;
- capability issue and consumption.

The synthetic implementation changes in lockstep, preserving its existing behavior and all 65 baseline tests. The
PostgreSQL implementation receives one `AsyncSession` owned by the application service. `AuthorizationGuard.hold()`
acquires the database locks inside that transaction, and all journal, ledger, and operation calls use the same
session until the terminal audit append commits or the transaction rolls back.

This is an internal infrastructure seam change. It does not change a public API, portable JSON schema, kernel enum,
or shared error meaning.

### 5. PostgreSQL adapter components

The adapter contains focused implementations of the existing Protocols:

- trusted clock backed by PostgreSQL `clock_timestamp()` normalized to UTC;
- approval evidence verifier backed by immutable protected rows;
- authorization ledger backed by protected grant functions;
- audit journal backed by operation-history and append functions;
- authorization guard backed by transaction-scoped database locks;
- artifact operation backed by function-only opaque-envelope read/write access.

The adapter maps database output into existing strict Pydantic models. Missing fields, unknown enum values, malformed
UUIDs/hashes, non-UTC timestamps, and extra result fields fail with a fixed `ProtectedSecurityError`; raw database
exceptions are chained internally only where they cannot leak through the public error or logging boundary.

### 6. Operation flow

1. The application service opens one protected `AsyncSession` transaction.
2. The adapter derives the principal from the database identity and loads the Dataset binding.
3. The kernel resolves replay history and a matching active grant.
4. The guard locks Dataset, grant, operation scope, and audit head.
5. The kernel revalidates the grant and Dataset binding.
6. The journal appends `INTENT` durably.
7. The guard issues and consumes a single-use capability bound to request, grant revision, Dataset revision, digest,
   action, target, nonce, and expiry.
8. The artifact function performs the minimal approved operation without returning storage coordinates.
9. The journal appends `SUCCEEDED`; an uncertain side effect appends `UNKNOWN` and blocks automatic retry.
10. The transaction commits. Any pre-side-effect failure rolls back and records a safe denial when the audit boundary
    remains available.

### 7. Audit durability and retention boundary

Database ownership and explicit privilege denial prevent update, delete, and truncate of audit relations by runtime
identities. No audit-table trigger is introduced. The
adapter verifies continuous sequence numbers, previous-entry hashes, self-hashes, and the durable head before trusting
history.

The repository implementation can prove append-only behavior and restore a disposable synthetic test database. It
cannot prove the deployed backup location, encryption, credential rotation, legal hold, or one-year operational
retention. Those remain activation evidence owned by Backend/Security and Privacy. No timed deletion is scheduled.

### 8. Activation states

Repository merge may set the implementation status to `IMPLEMENTED` only when code, protected migration/provisioning,
tests, and non-sensitive evidence agree. It must keep these states closed:

- effective enforcement: `NOT_IMPLEMENTED` until the real protected environment is provisioned and independently
  verified;
- HOLDOUT access authorization: `NOT_RECORDED` until an independent authorization event is recorded;
- authoring: blocked until environment ownership, access method, log/artifact non-exposure, credential rotation,
  backup/restore, and applicable Privacy approval are evidenced;
- Freeze and Runner execution: blocked until 40 protected cases, complete review, zero leakage-axis intersections,
  Freeze Receipt, execution authorization, and #178 bindings exist;
- disposal: blocked until #425 is approved and implemented;
- Production/publication: blocked until the complete Track F external gate is satisfied.

## Error handling

- Configuration errors fail at process startup without echoing configured values.
- Authorization and policy failures use the existing fixed `ProtectedSecurityError` reason-code allowlist.
- Unknown database results, SQLSTATEs not explicitly mapped, connection loss after a possible side effect, terminal
  audit failure, and malformed rows map to fail-closed internal/audit-uncertain outcomes.
- `UNKNOWN` operations never retry automatically.
- Logs may contain the fixed reason code, operation type, and an opaque request UUID only. They may not contain actor
  identity, Dataset content, authorization receipt body, SQL parameters, schema coordinates, or artifact bytes.

## Testing strategy

### Kernel regression

- Run the existing protected retrieval suite before changes.
- Convert one behavior at a time to async and verify each test fails for the missing awaitable boundary before updating
  the synthetic implementation.
- Preserve all role/state, audit lifecycle, tamper, concurrency, idempotency, expiry, and non-leakage assertions.

### Configuration tests

- disabled-by-default behavior;
- rejection of partial protected connection settings;
- rejection of placeholder credentials outside Local;
- prohibition of normal Worker credential fallback;
- protected engine SQL echo always disabled;
- safe URL construction for reserved password characters.

### PostgreSQL migration and ACL tests

- protected owner/access roles have no superuser, database-create, role-create, or replication authority;
- `PUBLIC`, normal app, migration, and CI identities have no protected schema/object access;
- protected access has no direct table or sequence privileges;
- future-object default privileges preserve denials;
- every definer function has the fixed trusted `search_path`, protected owner, revoked `PUBLIC EXECUTE`, and only the
  approved access-role grant;
- direct access to unfrozen or frozen artifacts fails for all unapproved identities;
- direct update/delete/truncate of Dataset, grant, and audit relations fails;
- downgrade refuses to destroy a non-empty protected schema and requires a forward fix.

### PostgreSQL adapter integration tests

- positive author, Custodian, and Runner operations use only synthetic opaque envelopes;
- missing, expired, revoked, forged, or mismatched grants fail before data access;
- an unfrozen Runner read and a frozen Author write fail;
- self-approval and implementation-participant approval fail;
- `INTENT` failure prevents the operation;
- terminal audit failure produces `UNKNOWN` or rollback according to side-effect observability;
- concurrent revoke/state change cannot pass the locked guard;
- duplicate operation keys execute once and replay only a verified opaque result;
- audit tamper, tail truncation, CAS conflict, and invalid transition fail closed;
- returned models and logs contain none of the forbidden protected values.

### Repository verification

- targeted kernel, config, migration, and adapter suites;
- complete `ai_worker/tests/evaluation` suite;
- complete migration suite against the disposable PostgreSQL test database;
- Ruff check and format check for changed paths;
- Mypy for `backend/app` and `ai_worker`;
- `git diff --check` and full diff review;
- repository test inventory contract;
- the repository-required full `bash scripts/ci/run_test.sh` when the local PostgreSQL environment is available.

## Documentation and evidence

The implementation change must:

- rename the PD-368 Decision file to remove the candidate suffix and set its status to `Approved Target · Not
  implemented` before later recording implementation evidence;
- update every repository link to the renamed Decision;
- add a focused implementation design and plan without changing unrelated contracts;
- update the #368 non-sensitive evidence with exact implementation status and hashes;
- state which real-environment checks remain unrun and keep access/authoring/Freeze/run/publication flags closed;
- avoid creating a Current contract unless the implementation PR contains the required migration, tests, evidence,
  and designated approvals.

## Completion criteria

The repository implementation is complete when the parameterized protected infrastructure, asynchronous adapter, and
synthetic PostgreSQL tests prove the approved policy without any direct table-access bypass. Completion does not claim
that a real protected environment exists. Real activation requires separate operational evidence and independent
authorization as described above.
