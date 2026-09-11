# Protected Retrieval Infrastructure Adapter Design

## Status and authority

- Status: Requester-approved revised design; designated reviewer approval remains required before merge.
- Tracking: Issue #368, PR #432.
- Governing decisions: `PD-368-20260909`, `PD-398-R1`, and `PD-398-R2`.
- Implementation owner: 정현우 (`@ceohwj`).
- Required reviewers: 권가빈 (`@hazelnutflavoured`) for Product, Privacy, Safety, and Evaluation; 송은영
  (`@phina-io`) for Backend, Dataset Custodian, and Security controls.
- Custodian separation: if 송은영 participates in implementation, 김지혜 (`@Jye-rookie`) becomes the independent
  Dataset Custodian approver.

This revision supersedes the function-only design previously committed in PR #432. The latest `develop` policy from
PR #429 prohibits new database functions, procedures, triggers, and RLS even when an earlier Decision proposed them.
The repository implementation, real protected environment, HOLDOUT access, authoring, Freeze, Runner execution,
retention, disposal, and Production publication remain inactive.

## Goal

Persist the existing protected retrieval kernel in PostgreSQL while keeping policy decisions explicit in Python and
using only ordinary database constraints, transactions, and least-privilege roles. Synthetic integration tests must
prove the repository boundary without exposing protected content, credentials, deployed coordinates, or approval
evidence bodies.

## Scope

Included:

- a separate protected PostgreSQL connection and schema;
- `NOLOGIN` owner, data-access, and control group roles plus individual human/service login identities;
- tables for identity, Dataset binding, approval evidence, grants, capabilities, artifact envelopes, audit entries,
  and an audit head;
- Python Service/Repository validation for identity, grant, Dataset state, capability, operation, and audit lifecycle;
- ordinary FK, UNIQUE, CHECK, NOT NULL, and column-level privilege enforcement;
- transaction-scoped SQLAlchemy adapters and a role-policy provisioning/validation module;
- synthetic limited-login, concurrency, rollback, tamper, idempotency, and non-leakage tests;
- non-sensitive repository evidence.

Excluded:

- database functions, procedures, triggers, RLS, policies, or DB schedulers;
- HOLDOUT questions, Gold answers, hard negatives, Freeze Receipt bodies, key material, HMAC values, or deployed
  storage coordinates;
- human access through a general SQL shell;
- production credential creation, secret injection, backup, restore, rotation, or network provisioning;
- `run-protected-holdout` CLI registration and #178 Retriever execution;
- retention deletion or #425 disposal behavior;
- Track F publication or Production activation.

## Repository constraints

1. Python owns business rules and authorization decisions. SQL contains only ordinary DDL, DML, constraints, locks,
   and grants.
2. No existing migration or legacy exception manifest is changed to permit a new forbidden definition.
3. `PROTECTED_RETRIEVAL_ENABLED=false` remains the default. Normal Worker, protected data-access, and protected control
   identities are pairwise distinct and cannot be used as fallbacks for each other.
4. All protected operations use one explicit `AsyncSession` transaction and one protected engine with SQL echo off.
5. Runtime DTOs, safe reason codes, role/action/state rules, and public evidence fields remain unchanged.
6. No dependency is introduced.
7. Tests use disposable schemas, roles, logins, payloads, and opaque identifiers only.

## Considered approaches

### Python Service/Repository plus column privileges — selected

The existing kernel remains the policy source. A PostgreSQL repository resolves the authenticated login, loads and
locks current rows, applies conditional DML, and appends audit entries in the same transaction. A separate Python role
policy grants only the columns required by this flow and validates the effective privileges using a real limited
login. This matches current repository policy and the Source management precedent.

Cost: a credential that can reach PostgreSQL directly can issue the DML its role permits without invoking Python.
Network isolation, a controlled job entry point, short-lived credentials, and independent role verification are
therefore activation requirements rather than assumed guarantees.

### Data/control role separation — selected

A data-access group performs Author/Custodian/Runner artifact operations. A separate control group performs approval
ingestion, grant/revoke/expire, Dataset lifecycle, and Freeze transitions. This prevents every data credential from
receiving authorization-control DML and follows the separate Source management role precedent.

### Per-application-action database roles — rejected for this PR

Separate Author, Custodian, Runner, artifact-writer, and audit-writer roles would further reduce each credential's DML
surface but still cannot express Dataset-version grants. They add provisioning and rotation complexity beyond #368.

### Views as the authorization boundary — rejected

Updatable or security-barrier views move row filtering back into implicit database logic and still cannot provide the
complete role/action/state and audit lifecycle without functions, triggers, or RLS.

### Function-only access — superseded

The earlier draft exposed `SECURITY DEFINER` functions while denying table access. PR #429 and current repository
policy explicitly prohibit that implementation.

## Trust and threat model

Trusted:

- the reviewed protected Python package and immutable deployment commit;
- the protected owner/migration operator during explicit provisioning;
- PostgreSQL transactions, locks, ordinary constraints, and column privileges;
- an authenticated individual login mapped to exactly one enabled protected identity;
- the controlled protected environment only after its external gates are evidenced.

Untrusted:

- request-supplied actor, role, Dataset state, grant revision, time, or capability fields;
- normal Backend/Worker/CI credentials and developer checkout configuration;
- replayed, expired, revoked, malformed, or cross-Dataset evidence;
- SQL errors, logs, and public evidence as carriers of protected content;
- any direct SQL path available outside the controlled protected environment.

Column grants limit damage but cannot make Python validation unavoidable for a leaked access credential. Direct DML
could cause unauthorized changes or audit denial-of-service. Constraints and Python verification detect corruption
but do not prove business authorization. Repository merge can therefore establish only repository implementation;
effective enforcement remains blocked until credential and network controls make the reviewed Python path the only
reachable operational entry point.

## Architecture

### 1. Configuration and connection isolation

The protected data and control connection tuples remain fail-closed. Enabling requires a common protected host, port,
database, and schema plus distinct data-access and control users/passwords. Partial settings, non-Local placeholders,
or identity equality with each other or the normal Worker fail without printing values.

The explicit engine factory keeps `NullPool`, `pool_pre_ping`, bounded timeout, neutral application name, and
`echo=False`. It is not registered in the normal consumer or CLI.

### 2. Provisioning and roles

1. A bootstrap administrator creates parameterized `NOLOGIN` owner, data-access, and control roles plus a distinct
   migration login.
2. The migration login receives only the temporary owner membership needed by the protected Alembic environment.
3. Alembic creates the schema and ordinary relations as the owner role.
4. `infra/python/protected_retrieval_role_policy.py` revokes defaults, grants exact columns, and validates the owner,
   data-access, and control boundaries.
5. Individual short-lived logins receive data-access or control membership only after external approval. Custodian
   workflows requiring both use a dedicated approved login whose two memberships are explicitly verified.
6. A real limited-login smoke test runs before activation.

No deployed name or credential is committed, and no migration creates a LOGIN role.

### 3. Relations and constraints

The existing logical relations remain, but all stored functions and function ACLs are removed:

- `protected_identity`: immutable `session_user` to actor/application-role mapping;
- `protected_dataset`: authoritative Dataset binding and state revision;
- `protected_artifact`: opaque envelope bound to Dataset/version/digest/key version;
- `approval_evidence`: immutable verified source envelope and raw hash;
- `authorization_grant`: immutable grant body, revision, validity, actions, and revoke state;
- `operation_capability`: single-use request/grant/Dataset/action/target binding;
- `audit_entry`: append-only global sequence and hash-chain entry;
- `audit_head`: singleton sequence/hash checkpoint.

FKs bind artifacts, grants, and capabilities. UNIQUE constraints bind operation keys, event IDs, and nonces. CHECK
constraints validate enum values, digest shape, timestamp order, revisions/counts, capability
timestamp order, and the audit singleton. Role/state policy is not encoded in CHECK constraints.

Rows requiring `SELECT ... FOR UPDATE` expose a constant `lock_marker=0` guarded by `CHECK (lock_marker = 0)`. The
access role may update only that column; changing it remains impossible.

### 4. Exact data and control privileges

Both roles receive schema `USAGE`, no `CREATE`, no ownership, no administration, and no `DELETE`, `TRUNCATE`, `TRIGGER`,
`REFERENCES`, or blanket table privilege.

| Relation | Data-access role | Control role |
| --- | --- | --- |
| `protected_identity` | selected identity columns | selected columns; approved identity insert/disable columns |
| `protected_dataset` | selected binding columns; `UPDATE(lock_marker)` | selected columns; approved Dataset insert/transition columns |
| `approval_evidence` | no privilege | selected columns and approved immutable insert columns |
| `authorization_grant` | selected grant columns; `UPDATE(lock_marker)` | selected columns; approved grant insert/revoke columns |
| `operation_capability` | selected columns; required-column `INSERT`; `UPDATE(consumed_at, operated_at)` | no privilege |
| `protected_artifact` | selected columns; required-column `INSERT`; `UPDATE(envelope, envelope_sha256)` | no envelope privilege |
| `audit_entry` | selected columns and required-column `INSERT` | selected columns and required-column `INSERT` |
| `audit_head` | selected columns and `UPDATE(sequence, entry_sha256)` | same |

Every insert grant is column-scoped; neither role receives table-level `INSERT`. Default privileges close future
tables/sequences. The validator enumerates every table and column; unexpected grants, ownership, membership, or
schema-create rights fail provisioning and startup validation.

### 5. Principal and policy validation

The repository reads `session_user`, requires exactly one enabled identity row, and verifies that database membership
matches the operation plane: data access for read/write/run and control access for approval/grant/revoke/expire/Freeze.
Its actor namespace and role must equal the request principal. A binding denial records the authenticated principal,
never the claimed principal. If identity resolution or audit persistence is unavailable, no mutation or artifact
access is attempted.

The existing kernel remains authoritative for role/action/state, Freeze/Runner evidence, grant binding, validity,
expiry, revoke, replay, and safe reason codes. Database rows replace request-supplied Dataset and grant values before
those rules run.

### 6. Transaction flow

1. Runtime construction opens a data-plane connection and validates the limited `session_user`, role memberships,
   schema ownership, and exact table/column privileges. Every later transaction repeats that validation.
2. A preparation transaction resolves the principal, verifies operation history, locks Dataset → grant → audit head,
   revalidates the kernel policy against DB time, appends `INTENT`, issues and consumes the capability, and commits.
3. Only after durable `INTENT` exists does a separate execution transaction re-read and lock the current Dataset and
   grant, validate that they still match the prepared values, and claim the capability operation once.
4. READ/RUN compares the actual envelope hash, stored envelope hash, and capability-approved artifact hash before
   invoking the callback. WRITE applies the equivalent capability binding before mutation.
5. The operation and `SUCCEEDED` terminal audit commit together. A callback failure or connection failure rolls back
   this phase and a fresh transaction appends `UNKNOWN`.
6. If the recovery transaction is unavailable, the already committed `INTENT` remains durable and blocks automatic
   replay. A later request cannot repeat the content access merely because `UNKNOWN` persistence failed.

The data-plane implementation therefore uses short-lived phase-specific `AsyncSession` instances rather than a
caller-owned transaction spanning the callback. Zero-row conditional DML maps to fixed safe reasons, not raw SQL
errors. The control-plane transaction order remains a target until the services in §7 are implemented.

### 7. Control-plane flow

Approval evidence ingestion, grant, revoke, expire, identity disable, Dataset transition, and Freeze use the control
engine. Each command carries an opaque request ID and expected revision/hash, resolves the authenticated approver,
locks rows in the defined order, checks self-approval and implementation-participant separation, applies one
conditional mutation, and appends an authorization or lifecycle audit entry in the same transaction. Duplicate request
IDs replay only a verified prior result; different payloads under one request ID fail as conflicts.

### 8. Audit integrity

The runtime role can insert but cannot update/delete audit entries. Before append, Python locks `audit_head`, verifies
the durable tail, bindings, hash chain, and transition, inserts exactly `head.sequence + 1`, then updates the head in
the same transaction. UNIQUE constraints reject duplicate sequence/event values.

A leaked credential can insert a forged row or tamper with the permitted head columns. This is detectable and causes
subsequent operations to fail closed, but prevention depends on the external credential/network gate.

### 9. Error and non-leakage boundary

Known failures map to the existing `ProtectedSecurityError` allowlist. Unknown SQLSTATEs, malformed rows, connection
loss, and row-count mismatches map to fail-closed internal/audit/unknown outcomes. Logs may contain a fixed reason,
action, and opaque request UUID only; they exclude actor identity, protected coordinates, SQL parameters, evidence
bodies, payloads, keys, and protected digests. SQL echo remains disabled.

### 10. Superseded-function replacement map

| Removed SQL function responsibility | Python/ordinary-SQL replacement |
| --- | --- |
| resolve principal | principal repository selects by `session_user` and validates role membership |
| load Dataset / approval | typed repository `SELECT` plus strict Pydantic decoding |
| find / require grant | grant repository query followed by kernel binding, revoke, revision, and expiry checks |
| lock operation | ordered `SELECT ... FOR UPDATE` using constant lock-marker privileges |
| audit checkpoint / history | audit repository selects head, global tail, and operation entries and verifies hashes |
| append operation | Python builds/verifies the entry, then audit insert and head update share one transaction |
| issue capability | Python validates current locked rows and performs required-column `INSERT` |
| consume capability | conditional timestamp `UPDATE ... RETURNING` |
| read artifact | conditional capability operation mark followed by bound artifact `SELECT` |
| write artifact | conditional capability operation mark followed by bound artifact `INSERT ... ON CONFLICT` |
| internal audit checkpoint | removed; tests inspect ordinary audit relations through the owner fixture only |

Grant/revoke/expire, identity administration, approval ingestion, and Freeze transitions remain target control-plane
Service methods rather than replacements with another implicit database execution path. They are not implemented by
PR #432.

## Testing strategy

Static policy:

- `check_database_logic.py` finds no new function/procedure/trigger/RLS definition;
- the legacy exception manifest is unchanged;
- no blanket protected-table privilege is introduced.

Migration and role policy:

- empty upgrade/downgrade succeeds; non-empty downgrade refuses data loss;
- owner/data/control/migration identities are distinct and non-administrative;
- `PUBLIC`, normal Backend/Worker/CI, and unapproved logins have no schema access;
- real data and control logins have exactly §4 privileges and cannot use the other plane unless explicitly approved;
- forbidden UPDATE/DELETE/TRUNCATE/CREATE/sequence access fails;
- future-object defaults remain closed.

Repository integration:

- positive Author write, Custodian read/Freeze, Runner read/run, and independent grant/revoke use synthetic payloads;
- principal mismatch, disabled identity, missing/expired/revoked/mismatched grant, invalid role/state, and incomplete
  evidence fail before artifact access;
- capability and artifact operation are exactly once under concurrency;
- revoke or Dataset revision changes cannot pass held locks;
- replay returns only a verified opaque result;
- audit truncation, forged insert, head rollback, hash mismatch, illegal transition, and terminal failure fail closed;
- rollback leaves no partial capability, artifact, or terminal audit;
- DTOs, errors, logs, engine configuration, and public evidence contain no protected values.

Repository verification includes focused protected tests, both DB policy scripts, full Evaluation and migration suites,
Ruff, format, Mypy, `git diff --check`, inventory, and `scripts/ci/run_test.sh`. Concurrent worktrees must use a uniquely
named disposable database rather than the shared local `test` database.

## Evidence and activation states

Until revised implementation and designated approvals exist, evidence must mark the redesign pending and keep:

- repository adapter: `PARTIALLY_IMPLEMENTED`;
- effective enforcement: `NOT_IMPLEMENTED`;
- HOLDOUT authorization: `NOT_RECORDED`;
- authoring count: `0`;
- Freeze and Runner execution: blocked;
- disposal: `BLOCKED_BY_ISSUE_425`;
- Production/publication: blocked by `EXT-PRIV-001` and the Track F external gate.

Activation additionally requires infrastructure ownership, network/entry-point restriction, short-lived credential
injection/rotation, independent limited-login verification, encrypted backup/restore, audit retention, incident
response readiness, and external approvals.

## Design review

Resolved blockers:

1. Forbidden DB functions and function ACLs are removed from the target design.
2. The superseded Source precedent is replaced by PD-398-R1/R2 Python transactions.
3. Direct-credential bypass is explicit and blocks effective activation.
4. Audit privileges guarantee append-only rows, while hash/transition correctness is explicitly a Python guarantee.
5. Constant lock markers define limited-role row locking without writable business fields.
6. Unique disposable DBs prevent concurrent worktree migration interference.
7. A separate control role prevents ordinary data credentials from receiving grant/revoke/Freeze DML.
8. The unused `operation_result` table is removed; verified terminal audit entries remain the single replay source.

Remaining reviewer gates:

- Backend/Security must approve §4, dual-role Custodian handling, and confirm that credential/network controls make
  Python the only operational path.
- Product/Privacy/Safety/Evaluation must accept the residual-risk statement and unchanged activation blocks.
- Implementation must prove every privilege claim with a real limited login; static scanning alone is insufficient.
- PR #432 must retain `PARTIALLY_IMPLEMENTED` until approval ingestion, grant/revoke/expire, Dataset transition, and
  Freeze Application Services are implemented and reviewed.

## Completion criteria

The design is implementation-ready when the requester and designated reviewers accept the privilege matrix,
transaction/lock order, residual risk, and activation gates. Repository implementation is complete only after
forbidden DB definitions are zero, all checks pass, evidence hashes match, and PR #432 receives independent approvals.
