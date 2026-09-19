# PATIENT_CITATION Source Use Approval Authority v1 (#807)

Status: Proposed — implementation, migration, and local PostgreSQL/ACL evidence are present;
final CI and reviewer evidence are pending.

Owner: `@ceohwj`  \
Required reviewer: `@phina-io`  \
Specialist FYI: `@Jye-rookie`, `@hazelnutflavoured`

## Scope

This contract stores historical Source Use Approval facts for an exact Source Snapshot, canonical
runtime environment, Source Use Purpose, and immutable approval version. The #807 completion target
is `PATIENT_CITATION`; the vocabulary also reserves `PRODUCT_IDENTIFICATION`, `SAFETY_ROUTING`,
`RULE_DERIVATION`, and `RETRIEVAL` as distinct purposes.

`PATIENT_CITATION` Source Use Approval is not any of the following:

- `RETRIEVAL` approval
- Runtime Bundle membership
- Snapshot `CURRENT`/`ACTIVE` status
- Citation Source or Member Decision
- CitationAuthorizationReceipt or citation release

## Exact identity and stored facts

The exact lookup identity is:

```text
source_snapshot_id
source_code
source_version
environment
purpose
approval_version
```

The persisted row additionally contains `valid_from`, `expires_at`, `revoked_at`, `revoked_by`,
`revoked_reason`, approval actor, approval evidence, row ID, and creation time. Member-specific
fields are intentionally absent.

The writer resolves the Source Snapshot through the exact
`Snapshot → Operation → Endpoint → Source` chain and rejects a caller-supplied
`source_code`/`source_version` mismatch. Environment values are case-sensitive and limited to
`LOCAL`, `TEST`, `CLOSED_DEMO`, and `PRODUCTION`; no trim, case conversion, alias, or fallback is
allowed.

Usability is a pure evaluation at an explicitly supplied time:

```text
valid_from <= evaluation_time < expires_at
AND revoked_at IS NULL
```

This helper does not infer Snapshot currentness, Source lifecycle, freshness, endpoint status, or
member eligibility.

## Persistence and read boundary

The dedicated `rag_source_use_approval` table is separate from the Catalog-only
`catalog_source_approval` table. The Repository owns no commit or rollback. Same immutable identity
and same semantic payload is an idempotent retry; the same identity with different semantic content
fails closed. Semantic update and delete APIs do not exist. Revocation is one-way and retryable only
with the same revocation payload.

The AI Worker reads through a SQLAlchemy Core, read-only adapter. It requires all six exact identity
fields, including `approval_version`, and provides no latest/current/newest selector. It does not
import Backend ORM models and does not issue Citation decisions.

The existing `SOURCE_WRITER_USER` is the only approval writer boundary. The dedicated
`SOURCE_USE_APPROVAL_TABLES` policy grants that role `SELECT, INSERT`, plus `UPDATE` on only
`revoked_at`, `revoked_by`, and `revoked_reason`; Runtime/AI Worker gets `SELECT` only. The table
is not added to `DB_APP_USER` append-only or mutable write sets. It is also kept outside
`SOURCE_TABLES` so Knowledge Index Builder does not inherit access through the generic Source read
set. No new privileged role is introduced.

## Explicit non-scope

This contract does not implement REQUEST Guard authority, Citation Source/Member Decision,
CitationAuthorizationReceipt, citation orchestration, Guide runtime, Release Gate, Frontend, or
`PUBLIC_TRACK_F`.
