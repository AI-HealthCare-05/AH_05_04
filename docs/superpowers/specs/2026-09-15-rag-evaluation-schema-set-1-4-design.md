# RAG Evaluation Schema Set 1.4 Design

## Status

- Scope: Issues #160 and #161 Evaluation schema prerequisite
- Owner: 정현우 (`@ceohwj`)
- Responsible reviewer: 김지혜 (`@Jye-rookie`) — Evaluation, Source provenance, and Safety fixture contract
- Design approval: user-approved on 2026-09-15
- Repository status: Candidate until the responsible reviewer approves the implementation Pull Request
- Runtime status: DEV projection contract only

## Goal

Create the smallest immutable Evaluation Schema Set that unblocks the approved DEV Grounding/Citation and
Safety/Rule-first contracts. Schema Set `1.4.0` reuses every `1.3.0` member byte-for-byte and adds exactly two
Evaluation-only artifact members:

- `rag-eval.claim-citation-observation@1.0.0`
- `rag-eval.grounding-signal@1.0.0`

This change defines and validates the observation boundary. It does not implement the #160 or #161 metric kernels,
connect Runtime adapters, inspect HOLDOUT or SAFETY_REGRESSION results, freeze a baseline, or make a Release decision.

## Why a New Schema Set

Schema Set `1.3.0` already has committed canonical bytes, a documented set hash, and consumers that bind to that
immutable reference. Adding members in place would silently change its manifest and invalidate those references.

Schema Set `1.4.0` therefore contains 23 members: all 21 members from `1.3.0` unchanged plus the two new artifact
members at member version `1.0.0`. The default exporter version remains `1.0.0` for backward compatibility.

Alternatives rejected:

- Mutating `1.3.0`: breaks its immutable schema-set hash and existing references.
- Bundling the scorer implementation: makes code depend on a shared contract before its version and member hash are
  reviewed, and expands the review surface beyond the prerequisite.
- Adding #159 human-judgment or three-variant comparison members: they are not required to unblock #160 or #161 and
  belong to a separate approval boundary.

## Architecture

```text
#180 validated Claim/Citation selection + authorization receipt
                              |
                              v
             claim-citation-observation@1.0.0
                              |
               same Run/Case/input/answer binding
                              |
                              v
                  grounding-signal@1.0.0
                              |
                              v
        future #160 grounding metrics + #161 critical union
```

Both artifacts are strict Pydantic validation models with deterministic canonical JSON Schema exports. They store
stable identifiers, bounded enums, immutable references, and SHA-256 values only. They do not store query text,
answer text, Claim text, Source body, Provider payload, credentials, or patient data.

## Claim–Citation Observation

### Envelope and binding

`rag-eval.claim-citation-observation@1.0.0` contains:

- `schema_id`, `schema_version`, and `observation_sha256`;
- `run_id`, `case_id`, and `task_type`;
- `dataset_code`, `dataset_version`, and `input_sha256`;
- non-null `answer_sha256` and `answer_variant_manifest_hash`;
- #180 validation execution status, decision, sorted bounded reason codes, and nullable validated-selection hash;
- nullable Citation authorization decision, sorted bounded reason codes, immutable receipt reference, and receipt hash;
- sorted `claims`.

Allowed task types are `ANSWER_GROUNDING | SAFETY | END_TO_END_RAG`. An observation exists whenever the corresponding
completed Case Result emitted any Claim or Citation. Its Run, Case, Dataset, input, answer, and answer-variant fields
must exact-match that Case Result and Run before a scorer consumes it.

The observation self-hash is the canonical SHA-256 of the complete model payload with `observation_sha256` omitted.
A supplied hash that does not match is rejected.

### Claim projection

Each Claim contains:

- `claim_key`;
- `claim_kind`: `MEDICAL | AUXILIARY | SAFETY_FALLBACK`;
- `criticality`: `CRITICAL | NON_CRITICAL`;
- `criticality_source`: `GOLD_EXACT_MATCH | APPROVED_REVIEW`;
- nullable `criticality_review_ref`;
- `support_status`: `SUPPORTED | PARTIALLY_SUPPORTED | CONTRADICTED | NOT_SUPPORTED`;
- `support_receipt_ref` and `support_receipt_sha256`;
- sorted `citations`.

`GOLD_EXACT_MATCH` forbids a criticality review reference. `APPROVED_REVIEW` requires an immutable reference that is
bound to the same Run, Case, answer, and Claim key. The observation schema validates the structural condition; the
future projection builder validates the referenced judgment contents.

Claim keys are unique and sorted by the repository's canonical UTF-16 ordering. The exact Claim key set must equal the
Case Result's `actual_claim_ids` set during future same-Case input validation.

### Citation edge projection

Each Citation contains:

- `citation_key` and parent `claim_key`;
- `source_type`: `PRESCRIPTION | KNOWLEDGE_CHUNK | INTERACTION_RULE | LIFESTYLE_GUIDELINE | SAFETY_POLICY`;
- `evidence_ref_id`, `source_version`, `locator`, and `content_sha256`;
- Evaluation edge `accepted` plus nullable bounded validation reason code;
- `authorized` plus nullable bounded authorization reason code;
- nullable authorization selection receipt reference/hash;
- `gold_source_matched`.

Citation keys are unique across the observation, each edge must point to its containing Claim, and Citation ordering is
canonical. The exact Evidence ID multiset projected by edges must match the Case Result's flat
`actual_citation_evidence_ids`; flat duplicate Evidence IDs remain representable through unique Citation keys.

An accepted or authorized edge can still have `gold_source_matched=false`. That is a scored quality failure, not an
artifact-integrity error. A missing required receipt binding, an unknown enum, a duplicate key, an orphan edge, or a
mixed Run/Case binding is invalid.

The schema records the same wire values as #180 but does not import mutable Runtime dataclasses as the Evaluation
contract. Projection-builder tests will prove the mapping explicitly when that builder is implemented.

## Grounding Signal

`rag-eval.grounding-signal@1.0.0` contains:

- `schema_id`, `schema_version`, and `signal_sha256`;
- `run_id`, `case_id`, `task_type`, Dataset identity, and `input_sha256`;
- nullable `answer_sha256`;
- `status`: `EVALUATED | NOT_APPLICABLE_NO_CLAIMS`;
- nullable observation reference and `observation_sha256`;
- `critical_unsupported_claim`, `uncited_medical_claim`, and `source_binding_misuse`.

Only `SAFETY | END_TO_END_RAG` task types are allowed. Every completed applicable Case has exactly one same-Case
signal.

State invariants:

- `EVALUATED` requires non-null answer and observation bindings.
- `NOT_APPLICABLE_NO_CLAIMS` requires null answer and observation bindings and requires all three failure booleans to
  be false.
- A Case Result with any emitted Claim or Citation cannot use `NOT_APPLICABLE_NO_CLAIMS`.
- A missing, duplicate, additional, or cross-Case signal is not converted to an all-false result.

The signal self-hash is the canonical SHA-256 of the complete model payload with `signal_sha256` omitted.

## Registry and Export

The implementation adds a dedicated Evaluation projection schema module and registers the two models under artifact
paths:

- `artifacts/rag-eval.claim-citation-observation.schema.json`
- `artifacts/rag-eval.grounding-signal.schema.json`

`SCHEMA_REGISTRY_V1_4` is derived from `SCHEMA_REGISTRY_V1_3` plus those members. Existing registry entries are reused
as the same immutable entries; no existing member version changes. `_SCHEMA_SET_MEMBER_COUNTS` records 23 members and
the CLI accepts `--schema-set-version 1.4.0` without changing its default.

Fresh canonical exports are committed under `evals/schemas/1.4.0/`. The resulting schema-set hash is documented in a
new Candidate Decision, `evals/README.md`, and the authoritative Evaluation target contract. Existing documented
Schema Set hashes remain unchanged.

## Validation and Error Handling

Model validation fails closed for:

- extra fields, unknown enum values, malformed UUID/version/hash/reference values;
- non-canonical ordering or duplicate Claim/Citation/reason-code members;
- Citation edges whose `claim_key` does not equal their containing Claim;
- inconsistent accepted/authorized reason and receipt combinations;
- an invalid observation or signal self-hash;
- an invalid `NOT_APPLICABLE_NO_CLAIMS` tuple.

Cross-artifact exact matching against Run, Case Result, Gold Evidence, and #180 receipts belongs to the future pure
projection/metric input validator. This schema PR does not pretend that JSON Schema alone can validate external
artifact content.

## Testing

The focused test suite covers:

1. Valid observation and evaluated/no-claims signal examples.
2. Every bounded enum member and one unsupported value.
3. Duplicate, unsorted, orphan, and self-hash mismatch rejection.
4. Conditional validation/authorization receipt requirements.
5. `NOT_APPLICABLE_NO_CLAIMS` accepting only the exact all-false null-binding tuple.
6. Schema Set `1.4.0` containing exactly 23 unique members.
7. All 21 inherited `1.3.0` member documents remaining byte-for-byte identical.
8. Fresh `1.4.0` export matching committed files byte-for-byte.
9. Documented Schema Set hash matching the committed canonical set.
10. Privacy sentinels proving the models and schemas expose no free-form body fields.
11. Existing `1.0.0` through `1.3.0` export regression tests remaining unchanged and passing.

## Delivery Boundary

This PR may update the schema models, package exports, registry, canonical JSON Schema files, Candidate Decision,
Evaluation target contract/index, `evals/README.md`, and focused tests. It must not modify frozen Dataset Cases, Gold,
Evidence Mapping, Critical Claim Rubric, Runtime adapters, comparison policies, metric kernels, Release thresholds, or
publication flags.

After the responsible reviewer approves and the PR is merged, #160 and #161 may implement their pure DEV projection
builders and metric kernels against `rag-eval.schema-set@1.4.0`. That later approval still does not authorize Runtime,
HOLDOUT, baseline freeze, Release `PASS`, or `PUBLIC_TRACK_F`.
