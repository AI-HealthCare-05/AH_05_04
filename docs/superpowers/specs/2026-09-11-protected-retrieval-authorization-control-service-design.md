# Protected Retrieval Authorization Control Service Design

## Status and authority

- Status: Requester-approved C1/C2 scope split and self-reviewed C1 design. The designated domain reviewers must still
  approve the implementation pull request before merge.
- Tracking: Issue #368; policy foundation PR #373; governance Decision PR #386; PostgreSQL data-plane PR #432.
- Governing baseline: `PD-368-20260909` and
  `docs/contracts/targets/post-mvp-1/protected-retrieval-infrastructure-v1.md`.
- Implementation owner: 정현우 (`@ceohwj`).
- Required reviewers: 권가빈 (`@hazelnutflavoured`) for Product, Privacy, Safety, and Evaluation; 송은영
  (`@phina-io`) for Backend, Dataset Custodian, and Security controls.
- Custodian separation: if 송은영 implements any authorization-control ACL or service code, 김지혜
  (`@Jye-rookie`) becomes the independent Dataset Custodian approver.

The design review corrected one material overreach before this document was finalized: C1 does not predeclare C2
lifecycle command enum values or lifecycle privileges. C2 receives a separate Decision/contract revision and design.
This document is therefore an implementation-ready design for C1 only, subject to the contract amendment described
below. It does not activate the protected environment or satisfy external release gates.

## Goal

Implement the missing authorization-control application service behind the approved Protected Retrieval target. The
service ingests immutable approval evidence, grants authorization, revokes authorization, and records deterministic
expiry. It authenticates the real control-plane database login, applies separation-of-duty rules in Python, performs
each state change and its audits atomically, and provides payload-bound idempotency without exposing protected data.

## Scope

### C1 included

- trusted approval-evidence ingestion by opaque source event ID and expected canonical hash;
- grant, revoke, and expire commands;
- authenticated control identity and approval-role resolution from `session_user`;
- reuse of one pure authorization-approval verifier by synthetic and PostgreSQL implementations;
- subject/Dataset/version/digest/action/revision/validity and separation-of-duty checks;
- authorization audit plus a command audit for every successful mutation;
- command-denial audit where the audit substrate is available and trustworthy;
- request-ID idempotency bound to the canonical command hash;
- a forward-only schema migration and narrower control-role privileges;
- unit, migration, and real limited-login PostgreSQL integration tests using synthetic identifiers and evidence.

### C2 deferred

- identity registration and disable;
- Dataset registration and lifecycle transitions;
- Freeze and Freeze Receipt handling;
- lifecycle command/audit enums and approval matrices;
- production approval-source connector selection and deployment configuration;
- actual HOLDOUT content, Retriever execution, Runner registration, Production activation, retention, or disposal.

C1 depends on pre-provisioned enabled control identities and existing Dataset rows. That is an explicit activation
precondition, not an implicit C1 administration path.

## Repository constraints

1. Business authorization, separation of duty, revision checks, and transactions remain explicit Python
   Service/Repository behavior. No function, procedure, trigger, RLS policy, or stored business rule is introduced.
2. The new audit kind, command DTOs, identity shape, uniqueness rule, and transaction semantics are shared-contract
   changes. A Decision amendment and matching target contract/index/tests must land in the implementation PR.
3. Applied migration history is preserved. All schema changes use a new forward migration; downgrade refuses to drop
   populated C1 state.
4. `PROTECTED_RETRIEVAL_ENABLED=false` remains the default. Data, control, and normal Worker credentials remain
   distinct, fail closed, and are never used as fallbacks for one another.
5. No new dependency is required. SQL echo stays disabled. Logs and evidence contain fixed reasons and opaque IDs
   only, never evidence bodies, actors, protected coordinates, digests, keys, or content.
6. The target contract remains in `docs/contracts/targets/`. C1 does not promote the subsystem to current, close
   Issue #368, or change the effective-enforcement status from `NOT_IMPLEMENTED`.

## Considered approaches

### Separate C1 domain/application service and PostgreSQL adapter — selected

Add a small domain contract and pure verifier beside the existing protected-retrieval kernel, then implement a
PostgreSQL application service with explicit transactions. This preserves the already merged data-plane service,
keeps approval-policy logic reusable in synthetic tests, and isolates control-only SQL and privileges.

### Implement the full control plane in one pull request — rejected

Identity administration, Dataset lifecycle, and Freeze require different approver matrices, state-machine decisions,
and evidence rules. Combining them with authorization control would materially enlarge the review surface and would
force unapproved lifecycle semantics into the shared contract.

### Encode command replay only in authorization/operation audits — rejected

Approval ingestion has no authorization mutation, and authorization audits identify domain transitions rather than
the executing command. Overloading either existing entry type would make replay and accountability ambiguous.

### Add a separate mutable `control_command` table — rejected

The append-only global audit can be the immutable request-ID ledger when it stores the command hash and result. A
second mutable table would duplicate truth, create reconciliation states, and add privileges without improving the
contract.

## Complexity justification

1. A direct set of repository functions is insufficient because all four commands share login binding, trusted
   evidence, idempotency, hash-chain verification, lock order, and atomic audit obligations.
2. The proposed structure is one domain module plus one PostgreSQL application-service adapter, a forward migration,
   and targeted runtime assembly. It does not create a framework or general command bus.
3. Maintenance cost is one additional audit union variant, one control identity discriminator, and one service test
   suite. Reviewers must keep its canonical serialization and migration constraints synchronized.
4. Alternatives were a full control-plane service, existing-audit overloading, and a separate command table. Each
   adds ambiguity or broader scope as described above.
5. The structure is needed now because PR #432 deliberately left the control-plane path unimplemented, while direct
   DML under its broad control ACL would otherwise be the only way to populate grants and approval evidence.

## Architecture

### Domain and application contract

Create `ai_worker/tasks/evaluation/protected_retrieval_control.py` containing only C1 command/result models, command
audit models, the approval-source Protocol, canonical command hashing, and pure authorization-approval verification.
The existing `ai_worker/tasks/evaluation/protected_retrieval.py` adds the `CONTROL` audit union member and only the
safe reason/enum values approved by the contract amendment.

Move the policy currently embedded in `InMemoryApprovalEvidenceVerifier` into a pure
`verify_authorization_approval(grant, evidence, action, expected_raw_sha256)` function. It returns the existing sealed
`VerifiedAuthorizationApproval`; callers cannot construct a valid seal. Both the synthetic verifier and PostgreSQL
service call this function, so tests do not maintain a second authorization matrix.

Create `ai_worker/adapters/postgresql_protected_retrieval_control.py` for the application service and control-only
repositories. Reuse narrowly extracted audit-chain parsing/appending helpers from the data-plane adapter if needed;
do not extend the existing approximately 1,000-line adapter with a second service flow.

Add `create_protected_authorization_control_service` to `ai_worker/core/runtime_assembly.py`. It uses only the
protected control engine and calls the existing exact-privilege validator before returning a service. It is not
registered in the normal Worker or any public CLI.

### Command contracts

All IDs below are canonical lowercase UUIDv4 strings and all models use the repository's strict Pydantic base.

```text
IngestApprovalCommand
  request_id
  source_event_id
  expected_raw_sha256

GrantAuthorizationCommand
  request_id
  grant: ProtectedAuthorizationGrant
  expected_dataset_state_revision

RevokeAuthorizationCommand
  request_id
  grant_id
  approval_source_event_id
  expected_raw_sha256
  expected_effective_revision

ExpireAuthorizationCommand
  request_id
  grant_id
  expected_effective_revision

ControlCommandResult
  request_id
  command_kind: INGEST_APPROVAL | GRANT | REVOKE | EXPIRE
  target_id
  effective_revision: integer or null
  authorization_audit_event_id: UUIDv4 or null
  reason_code: APPROVAL_VERIFIED | AUTHORIZED | REVOKED | EXPIRED
```

`target_id` is the opaque source event ID for ingestion and the grant UUID for authorization commands. The public
result represents success only and does not return the evidence, subject, issuer, Dataset, hashes, timestamps, or
database identity. A denied command raises `ProtectedSecurityError` with its fixed safe reason after any trustworthy
denial audit commits; it never returns a partially successful result.

### Trusted approval source

```text
TrustedApprovalSource.fetch(source_event_id) -> ApprovalSourceEvidence
```

The service never accepts an evidence body as authoritative caller input. `fetch` returns one immutable canonical
record from an approved source; the command supplies only its opaque ID and expected raw hash. The source fetch occurs
outside the database transaction to avoid holding locks across external I/O. The transaction then validates the
fetched record, source ID, canonical hash, issuer, and persisted state again before mutation.

C1 defines this Protocol and an in-memory fake for tests. It does not invent a production connector because the
approved target does not identify the authoritative approval system or its authentication mechanism. Repository C1
can be complete against the port, but operational evidence ingestion and activation remain blocked until a separately
reviewed connector and external gate evidence exist.

### Control identity model

The same human may legitimately have distinct data and control database logins, and Product/Safety reviewers have no
data-plane principal role. A new migration changes `protected_identity` as follows:

- add `identity_plane` with values `DATA` and `CONTROL`, backfilling existing rows as `DATA`;
- make `principal_role` nullable and add nullable `approval_role` with values `DATASET_CUSTODIAN` and
  `PRODUCT_SAFETY_REVIEWER`;
- enforce exactly one shape: `DATA` requires `principal_role` and forbids `approval_role`; `CONTROL` requires
  `approval_role` and forbids `principal_role`;
- replace `UNIQUE(actor_namespace, actor_id)` with
  `UNIQUE(actor_namespace, actor_id, identity_plane)`; keep `database_login` as the primary key.

Every command resolves `session_user` to exactly one enabled `CONTROL` row and verifies exact membership in the
configured control group, absence of data-group membership, schema ownership, and expected column privileges. The
command does not accept an executor identity. A missing, disabled, ambiguous, or privilege-mismatched identity fails
closed before source use or mutation.

Grant and revoke additionally require the authenticated executor actor and approval role to equal the verified
approval evidence issuer. Expire is deterministic and may be executed by either enabled control approval role after
the database clock reaches `expires_at`; its authorization audit retains the original grant issuer, while the CONTROL
audit records the actual executor. This avoids an invented `SYSTEM` approval role.

### Approval and grant rules

Approval verification requires all existing bindings:

- source action equals GRANT or REVOKE as requested;
- source state is `APPROVED`;
- issuer, implementation commit/artifact, participants, canonical raw hash, and approved grant payload hash match;
- issuer is neither the subject nor an implementation participant;
- a Dataset Custodian grant is issued by Product/Safety; Author and Runner grants are issued by a Dataset Custodian;
- granted actions are a subset of the existing role/action allowlist.

Ingestion accepts GRANT or REVOKE evidence and stores the exact strict DTO plus canonical raw hash once. Reusing a
source event ID with byte-equivalent canonical content is idempotent; different content is a command conflict.

Grant locks the Dataset and every grant in the subject/Dataset scope. It requires an existing persisted verified
evidence row, an exact Dataset/version/manifest/artifact/key binding, the command's expected Dataset state revision,
an enabled subject data identity, an enabled issuer control identity, and the database UTC clock within the grant
window. The requested grant revision must equal the maximum revision in that scope plus one, starting at one. Add an
ordinary UNIQUE constraint over subject actor ID/namespace/role, Dataset ID/version, and revision so concurrent grants
cannot create parallel current revisions.

Revoke locks the target grant, requires its expected effective revision, verifies persisted REVOKE evidence against
the immutable grant body, and conditionally sets `revoked_at` plus `effective_revision + 1`. A revoked grant cannot be
revoked again.

Expire locks the target grant, requires its expected effective revision, rejects an already revoked or expired
effective state, and uses database UTC time. It cannot succeed before `expires_at`; on success it increments
`effective_revision` without inventing approval evidence. The immutable grant body and original revision do not
change.

### Command audit and idempotency

Add `CONTROL` to `ProtectedAuditEventKind` and a `ControlCommandAuditEntry` to the audit union. Its canonical body is:

```text
event_kind: CONTROL
sequence
event_id: request_id
command_kind: INGEST_APPROVAL | GRANT | REVOKE | EXPIRE
executed_by: ActorIdentity
target_kind: APPROVAL_SOURCE_EVENT | AUTHORIZATION_GRANT
target_id
command_sha256
outcome: SUCCEEDED | DENIED
result_effective_revision: integer or null
authorization_audit_event_id: UUIDv4 or null
reason_code
recorded_at
previous_entry_sha256
entry_sha256
```

The command hash is the SHA-256 of canonical JSON containing the command kind and the complete validated command
payload, including `request_id`. The hash deliberately excludes fetched source material and database-derived state;
those are separately bound by the command's expected values and domain audit.

For grant, revoke, and expire, the authorization audit and successful CONTROL audit are appended in the same
transaction as the mutation. Ingestion appends only a successful CONTROL audit. Known denials append one CONTROL
`DENIED` entry when the identity and audit chain can be trusted. If principal resolution or audit verification fails,
the service performs no mutation and does not claim to have audited the denial.

On replay, the service loads the CONTROL entry by `event_id`, verifies the complete global chain and stored entry
hash, and compares command kind and command hash. An exact successful match reconstructs the stored result without a
new mutation or audit. An exact denied match raises the stored fixed reason again. A different payload under the same
request ID raises the new fixed reason `CONTROL_COMMAND_CONFLICT`.

There is no separate command table. A concurrent duplicate first loses the audit `event_id` uniqueness race and rolls
back its whole transaction, then opens a fresh transaction to verify and return the winner. If the winner's hash does
not match, the loser returns `CONTROL_COMMAND_CONFLICT`. No partial mutation may survive this path.

### Transaction and lock order

For commands that need source evidence:

1. validate configuration, the control connection, and `session_user` in a short read-only preparation transaction
   without logging values; reject an unauthorized login before contacting the trusted source;
2. fetch immutable evidence by source event ID outside the transaction;
3. open one `AsyncSession.begin()` transaction and revalidate `session_user`, role memberships, and exact privileges;
4. check for a replay; if found, verify the global audit and return or conflict;
5. load strict rows and acquire command-specific locks in the global order
   Dataset → grant scope/target grant → audit head;
6. recheck the request ID after locks, then validate trusted evidence and current database state;
7. on success, apply one conditional mutation and append domain plus CONTROL audits; on a known denial, append only a
   CONTROL denial audit and retain the error for post-commit delivery;
8. update the audit head and commit once, then raise the retained fixed denial outside the transaction if present.

Expire follows the same sequence without step 2. Ingestion has no Dataset/grant lock and locks only the evidence key
through its unique insert plus audit head. No command changes its mutation target before the audit chain is known to be
valid. Zero-row DML, malformed rows, unknown SQLSTATEs, connection loss, and audit failures roll back and map to the
approved fixed safe reason set; raw database text is never returned or logged.

### Exact role-policy delta

The control group receives only:

- SELECT on the approved identity, Dataset, evidence, grant, audit-entry, and audit-head columns;
- INSERT on the required approval-evidence, authorization-grant, and audit-entry columns;
- UPDATE on grant `effective_revision`, `revoked_at`, and `lock_marker`;
- UPDATE on Dataset `lock_marker` only;
- UPDATE on audit-head `sequence` and `entry_sha256`.

Remove the currently pre-granted identity INSERT/UPDATE and Dataset lifecycle INSERT/UPDATE columns until C2. The data
role does not change. Both roles retain no ownership, schema CREATE, DELETE, TRUNCATE, TRIGGER, REFERENCES, blanket
table INSERT, or access to protected artifact envelopes outside their existing data-plane needs.

## Error contract

C1 reuses existing fixed reasons whenever their meaning is exact, including `APPROVAL_*`, `SELF_APPROVAL_DENIED`,
`ISSUER_ROLE_DENIED`, `AUTHORIZATION_*`, `DATASET_*`, and `AUDIT_*`. It adds only
`CONTROL_COMMAND_CONFLICT` for a reused request ID whose canonical payload differs. Missing or invalid control identity
is intentionally reported as the existing non-enumerating `AUTHORIZATION_NOT_FOUND`; unexpected failures remain
`INTERNAL_ERROR`.

The Decision amendment must add the new reason and clarify that a denial may be unaudited only when identity or audit
integrity itself is unavailable; mutation is always forbidden in that case.

## TDD and verification strategy

### Unit tests first

- strict UUIDv4, enum, forbidden-extra, UTC, and nullability validation for all command/result/audit models;
- canonical command hashes are deterministic and change for every semantically relevant field;
- pure verifier covers issuer matrices, action subsets, self approval, implementation participants, source action,
  source/raw/payload/implementation bindings, and sealed return values;
- synthetic verifier delegates to the same pure function;
- audit hash and union parsing accept CONTROL and reject malformed/unknown variants;
- result reconstruction returns only allowlisted fields.

### Migration and role-policy tests

- forward upgrade preserves existing rows and backfills `identity_plane=DATA`;
- DATA/CONTROL shape constraints and per-plane actor uniqueness reject invalid combinations;
- grant-scope revision uniqueness rejects concurrent parallel revisions;
- audit kind accepts CONTROL while preserving AUTHORIZATION and OPERATION constraints;
- downgrade refuses while C1 identity, evidence, grant, or CONTROL audit state would be lost;
- schema inspection proves no function, procedure, trigger, RLS policy, or new exception-manifest entry;
- exact limited-login ACL inspection proves removed identity/Dataset administration privileges and required C1 columns.

### Real limited-login PostgreSQL integration tests

- valid ingest, grant, revoke, and expire paths;
- Product/Safety may grant Custodian; Custodian may grant Author/Runner;
- self approval and implementation-participant approval are denied;
- database login, command issuer, and evidence issuer mismatch are denied;
- missing, altered, wrong-action, or wrong-hash trusted evidence is denied;
- Dataset binding/state revision, subject, grant revision, and validity mismatches are denied;
- early expiry is denied and due expiry increments effective revision once;
- same request/payload concurrently causes one effect and a verified replay;
- same request/different payload conflicts;
- conditional DML, authorization-audit, CONTROL-audit, or head-update failure leaves no mutation;
- audit tamper, tail truncation, unexpected membership, or privilege drift fails closed;
- logs, exceptions, and public evidence contain no evidence body, actor identity, protected coordinate, digest, or SQL
  parameter.

After targeted tests, run the repository-required Ruff format/lint, mypy, relevant worker tests, protected migration
tests, and the applicable CI script from `CONTRIBUTING.md`/`docs/testing.md`. Report any environment-gated integration
test separately; never replace it with a mock-only claim.

## Documentation and status changes in the implementation pull request

Before or with code, add a focused Decision amendment (provisionally `PD-368-R1`) approving the C1 command DTOs,
CONTROL audit schema, identity-plane schema, revision uniqueness, idempotency, transaction order, reason-code delta,
and C1/C2 split. Update the target contract and `docs/contracts/README.md` in the same pull request.

After implementation evidence exists, update only the repository-implementation rows in the #368 evidence builder,
JSON/Markdown evidence, status report, and implementation matrix. Keep external approval, credential/network isolation,
approval-source connector, protected environment, HOLDOUT authoring, Freeze, Runner execution, and publication rows
closed. The target contract is not promoted to `current/` and Issue #368 is not closed by C1.

## C2 handoff boundary

C2 starts only after its own Decision/contract revision and design define:

- identity registration/disable approvers and replay behavior;
- Dataset registration plus ACCESS_AUTHORIZED → AUTHORING → REVIEW_READY → FROZEN transitions;
- Freeze evidence, lifecycle audit shape, expected revisions, and separation of duty;
- the exact identity/Dataset INSERT and UPDATE privileges restored to the control role.

C1 must not pre-create C2 command enum values, lifecycle audit variants, transition methods, or broad Dataset/identity
privileges. The identity-plane schema is the sole C2-adjacent change permitted in C1 because C1 itself requires
Product/Safety and Custodian control identities distinct from data principals.

## Acceptance boundary

C1 is implementation-complete only when the Decision/target contract, migration, domain models, PostgreSQL service,
runtime assembly, synthetic reuse, exact ACL, unit tests, and real limited-login integration tests agree and fresh
verification passes. Merge still requires the named independent reviewers. Operational enforcement remains
`NOT_IMPLEMENTED` until a production trusted-source connector and every external privacy/security/environment gate are
separately evidenced.
