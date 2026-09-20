# Guide Retrieval Query Fingerprint Authority v1

| Item | Value |
| --- | --- |
| Status | Approved Target · B2 production authority implemented |
| Issue | #180 B2 |
| Result | `GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_READY` |

## 1. Boundary

The exact validated `SensitiveText` from
`guide-medication-retrieval-query-authority-v1.md` is fingerprinted without
normalization, trimming, case folding, or suffixing. The implementation is
`ai_worker.tasks.rag.production_query_binding` and consumes only the typed
`GuideQueryHmacKeyProvider`; it has no configuration or environment access.

The Backend composition root owns configuration and performs the sole secret
translation:

```text
backend/app/core/config.py
  → backend/app/core/__init__.py global config
  → backend/app/dependencies/services.py
  → GuideQueryHmacKeyDependency : GuideQueryHmacKeyProvider
```

`get_guide_query_fingerprint_producer` and
`get_guide_query_binding_verifier` are FastAPI composition-root dependencies.
They do not alter Guide or Chat generator assembly.

## 2. Frozen authority

| Authority | Frozen value |
| --- | --- |
| Algorithm | `HMAC-SHA-256` |
| Preimage | `UTF-8("query-hmac@1") + NUL + UTF-8(exact normalized query)` |
| Digest | 64-character lowercase hexadecimal HMAC digest |
| Key version | exact `guide-query-hmac-key@<positive-integer>`; initial `guide-query-hmac-key@1` |
| Config | `GUIDE_QUERY_HMAC_KEY` and `GUIDE_QUERY_HMAC_KEY_VERSION` only |
| Artifact identity | `guide-query-binding-verifier@1.0.0` plus canonical, secret-free policy-projection SHA-256 |

The producer uses the active approved version. The verifier resolves the
claimed exact version, so its protocol permits a future retained-version
provider; no rotation service, storage subsystem, or retained key is added in
this scope.

## 3. Failure and secret semantics

Malformed fingerprint syntax, an unapproved algorithm or key-version claim,
and a digest mismatch return the existing `INVALID_BINDING` result. A missing
or unavailable approved typed key dependency returns `DEPENDENCY_ERROR`.

The raw query or key material is held by an opaque `ApprovedGuideQueryHmacKey` and
MUST NOT appear in repr, log, error, receipt, artifact, DTO, or persistence. There is no
HMAC key hardcode, fallback key, random key, empty-key acceptance, direct
`os.getenv`/`os.environ` read in domain code, database secret table, or new
secret subsystem. Production MUST NOT reuse the Backend idempotency HMAC key.

The caller MUST NOT select algorithm, key_version, secret, or hash function.
`QueryBindingVerifierPort` remains the existing verification boundary; this
implementation returns its existing success/failure result types.

## 4. Readiness and non-scope

B2 is `GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_READY`. This resolves only
the B2 producer, verifier, immutable artifact identity, and Backend injection
boundary. It does not make `retrieve_medication_guidance` callable:
`GUIDE_RUNTIME_REQUEST_CARRIER_MISSING` (B1) and
`GUIDE_RETRIEVAL_TERMINAL_REPLAY_PAYLOAD_UNAVAILABLE` (B5) still leave
`THIN_LANGGRAPH_BLOCKED_BY_CANONICAL_CALLABLE_GAPS` in force.

Out of scope: B1 carrier implementation, B3 REQUEST lookup, B4 outcome
binding, B5 replay recovery, retrieval/RRF/Evidence Gate changes, database or
migration work, Worker/#577, LangGraph implementation, public Track F, and
Chat orchestration.
