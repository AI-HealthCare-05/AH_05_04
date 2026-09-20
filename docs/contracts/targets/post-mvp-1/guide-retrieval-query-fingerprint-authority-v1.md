# Guide Retrieval Query Fingerprint Authority v1

| Item | Value |
| --- | --- |
| Status | Approved Target · B2-2 blocker freeze · no production implementation |
| Issue | #180 B2-2 |
| Audited base | `origin/develop` `77224170df54726e45bce4f1491042b998d7fc88` |
| Result | `GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_BLOCKED_BY_ALGORITHM_AUTHORITY_MISSING` |

## 1. Purpose and boundary

This contract records the authority audit for the already-frozen B2 canonical
query text. It does not recreate that projection, create a secret subsystem,
or make `retrieve_medication_guidance` callable.

The input remains the exact, already-validated `SensitiveText` produced by
`guide-medication-retrieval-query-authority-v1.md`. Fingerprinting must not
normalize, trim, casefold, append a suffix to, or otherwise mutate that value.

## 2. Authority audit

| Question | Authoritative evidence | Result |
| --- | --- | --- |
| Q1. Production algorithm approved? | Approved `PD-315-20260908` says that a query digest uses an “approved versioned HMAC preimage.” | Blocked: it does not name an exact production algorithm identifier or digest construction. |
| Q2. `key_version` naming approved? | No Current or Approved Target contract/Decision names a Guide query key-version authority. | Missing. |
| Q3. Key owner/storage exists? | No Guide query key owner, storage, rotation, or retention authority exists. | Missing. |
| Q4. Worker runtime key dependency exists? | `ai_worker` configuration and runtime assembly contain no approved Guide query key injection dependency. | Missing. |
| Q5. Production `QueryBindingVerifierPort` implementation exists? | `evidence_retrieval.py` defines only the port and result types; no concrete production implementation exists. | Missing. |
| Q6. Verifier artifact identity convention approved? | `ImmutableArtifactRef` validates a generic immutable reference, but no policy projection defines a query verifier artifact identity. | Missing. |
| Q7. Producer and verifier can consume one authority? | There is no approved Worker secret/config owner for either side. | Missing. |

`PD-315-20260908` is Approved (2026-09-17), but its generic HMAC direction
does not satisfy the required exact production algorithm identifier. The
Proposed search contract says that Privacy·Security approval owns the algorithm
and retained key versions; it is not an approved algorithm/key policy.

The #178 design examples (`HMAC-SHA-256` / `query-hmac@1`) and `sha256` / `v1`
fixtures are design or synthetic material. Production MUST NOT promote `sha256` / `v1` synthetic fixtures,
and MUST NOT treat the design example as approval.

The Backend idempotency HMAC configuration has a distinct data purpose, owner,
field names, lifecycle, and runtime boundary. B2 MUST NOT reuse the Backend idempotency HMAC key as a Guide query key.

## 3. Binding semantics retained from the existing kernel

`QueryBindingVerifierPort.verify(query, query_fingerprint)` remains the only
port. A future production producer and verifier must independently bind the
exact query and claimed fingerprint before search. The verifier must return the
existing `QueryBindingVerificationSuccess` or
`QueryBindingVerificationFailure` result; it must not introduce a duplicate
port or failure enum.

When the required authority is approved, the policy must define the exact
algorithm, UTF-8 query-byte rule, key_version naming authority, digest format,
key owner, Worker injection dependency, deterministic verifier artifact
identity, retained-version behavior, and failure mapping. The caller MUST NOT select algorithm, key_version, secret, or hash function.

The existing kernel semantics remain: malformed or mismatched binding is
`INVALID_BINDING`; an approved key dependency failure is `DEPENDENCY_ERROR`;
and a success receipt must exact-match the requested fingerprint. No query
value or key value may be placed in a receipt, artifact, exception, audit
record, log, or public DTO.

## 4. Blocker and exit criterion

The narrow first unresolved gate is the absence of an exact approved production
algorithm identifier. Therefore B2 is
`GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_BLOCKED_BY_ALGORITHM_AUTHORITY_MISSING`.
The related key/version, secret ownership/injection, verifier implementation,
and verifier-artifact gaps are independently recorded above; none may be
filled by inference from the generic HMAC Decision.

No production implementation is present. No production Python implementation is added. A future implementation is
permitted only after all of the following are available in Current/Approved
authority:

- exact algorithm and digest format;
- key_version naming authority;
- secret/key owner or storage authority and approved Worker runtime key-injection dependency;
- deterministic verifier artifact identity; and
- exact `QueryBindingVerifierPort` policy semantics.

Until then, `retrieve_medication_guidance` remains `MISSING_SEMANTIC_CALLABLE`
and the overall state remains `THIN_LANGGRAPH_BLOCKED_BY_CANONICAL_CALLABLE_GAPS`.

## 5. Security rules and non-scope

This blocker freeze MUST NOT add an HMAC key hardcode, test-secret production
default, fallback key, random key, empty-key acceptance, arbitrary environment
variable, Vault/SecretManager/KMS abstraction, database secret table, key
rotation service, or secret provisioning workflow. A raw query and key
material MUST NOT be exposed in logs, errors, receipts, artifacts, audits, or
public DTOs.

Non-scope: B1 carrier implementation, B3 REQUEST lookup, B4 outcome binding,
B5 terminal replay, retrieval runtime, RRF, Evidence Gate, database/migration,
Backend, Frontend, LangGraph, Worker/#577, and `PUBLIC_TRACK_F`.
