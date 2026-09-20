# Guide Medication Retrieval Query Authority v1

| Item | Value |
| --- | --- |
| Status | Approved Target · query-text and B2 fingerprint authority frozen |
| Version | `guide-medication-retrieval-query-v1` |
| Issue | #180 B2 |
| Result | `GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_READY` |

## 1. Purpose and boundary

This contract defines only the deterministic text input for one Guide medication
retrieval unit. It binds a verified medication to its identification-time,
human-readable snapshots; it does not create a Sync carrier, issue a
`QueryFingerprint`, verify a query binding, execute search, or introduce a
Worker/#577 path.

The Worker MUST receive the values through the future B1 Sync carrier. It MUST
NOT read a Backend ORM, a current product table, or any latest catalog row.

## 2. Source-field matrix

| Field | Source | Pinned? | Current Worker carrier? | Allowed for query? | Reason |
| --- | --- | --- | --- | --- | --- |
| `prescription_version_medication_id` | verified identification membership | Yes | No | No | Identity binding, not human query text |
| `code_system` | verified identification result | Yes | No | No | Stable identity namespace, not query text |
| `canonical_code` | verified identification result | Yes | No | No | Stable identity, not a natural-language term |
| `medication_name_snapshot` | candidate-search snapshot created from the prescription-version medication | Yes | No | Yes | Original request-bound human-readable medication name |
| `strength_text_snapshot` | nullable candidate-search snapshot created with the medication-name snapshot | Yes | No | Yes | Original request-bound human-readable strength when present |
| `product_name` | catalog/candidate result | Not selected | No | No | It is not the approved verified-medication query source |
| dosage form | catalog/candidate metadata | Not selected | No | No | Requires an unapproved source choice |
| ingredient name/code | catalog/current source metadata | Not selected | No | No | Requires an unapproved translation or lookup |
| `display_order` | prescription presentation data | Not selected | No | No | Does not identify a retrieval query unit |

The snapshot fields are pinned at candidate-search creation. The identification
service persists `medication_name_snapshot=medication.medication_name` and
`strength_text_snapshot=medication.strength_text`; current catalog changes
therefore cannot alter this input.

## 3. Exact projection

one verified medication → one canonical query unit. Multiple medications MUST
NOT be concatenated.

The projection MUST NOT concatenate multiple medications.

For one future B1 carrier item:

1. `medication_name_snapshot` is required.
2. When `strength_text_snapshot is None`, the query text is exactly
   `medication_name_snapshot`.
3. When `strength_text_snapshot` is present, the query text is exactly
   `medication_name_snapshot + ASCII SPACE + strength_text_snapshot`.
4. The resulting text is wrapped as `SensitiveText normalized_query` only
   after it satisfies the existing `validate_query_text()` boundary.

The source values and resulting text MUST already be Unicode NFC, nonblank,
free of leading/trailing whitespace, within the existing length limit, and free
of forbidden characters. The projection MUST NOT silently normalize, strip,
lower/casefold, collapse whitespace, remove punctuation, normalize units,
transliterate, or translate brand to ingredient.

The projection MUST NOT use `canonical_code` as query text, concatenate
`code_system` with an identity, read the current product catalog, append an
intent suffix, or use free-text prescription/request input. In particular, it
MUST NOT append “복약 안내”, “복용법”, “주의사항”, “생활습관”, “음식”, or
“부작용”.

The Worker MUST NOT read the current product catalog for this projection.
The projection MUST NOT append an intent suffix.

## 4. Fingerprint and binding authority

The B2 authority is implemented in
`guide-retrieval-query-fingerprint-authority-v1.md`: `HMAC-SHA-256`, exact
`query-hmac@1` UTF-8 preimage bytes, 64-character lowercase hexadecimal digest,
and `guide-query-hmac-key@<positive-integer>` (initially
`guide-query-hmac-key@1`). `GuideQueryFingerprintProducer` and the existing
`QueryBindingVerifierPort` result boundary consume the same typed Backend
composition-root provider; the raw key is not part of this query projection.

Repository uses of `sha256` / `v1` remain synthetic/evaluation fixtures and
MUST NOT become an alternate Guide fingerprint policy.

## 5. Current state and non-scope

The query-text portion and B2 fingerprint/binding authority are frozen, but the
B1 Sync carrier has not been implemented. The exact B2 state is
`GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_READY`; this does not itself make
the retrieval callable ready.

Excluded: Backend DTO/API or ORM access, database migration, current/latest
lookup, query heuristics, retrieval/RRF/Evidence Gate changes, LangGraph, B3
REQUEST authority lookup, B5 replay payload recovery, Worker/#577, and public
Track F activation.
