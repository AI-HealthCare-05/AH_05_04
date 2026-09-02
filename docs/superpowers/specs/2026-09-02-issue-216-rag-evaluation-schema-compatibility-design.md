# Issue #216 RAG Evaluation Schema Compatibility Design

## Status

- Issue: `#216`
- Scope: Evaluation authoring schema compatibility before `#214` Dataset Freeze
- Authority: `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`, RAG `evaluation-plan.md@1.35`
- Delivery order: `#216` → `#214` → `#157`

## Problem

Schema Set `1.0.0` cannot represent three distinct Rule outcomes without inventing an Interaction Rule ID:

- one or more approved rules matched;
- Rule evaluation ran and found no match;
- Rule evaluation did not run because an approved earlier boundary stopped execution.

It also lacks typed inputs for Source/Bundle ineligibility and provider/retrieval faults. Finally, a `FROZEN` Dataset Manifest can be approved while a selected Case Gold, Evidence Mapping, or Critical Claim Rubric remains only `REVIEWED`.

OTC `AMBIGUOUS | UNMATCHED` medication identification is not an Evaluation Case input. It remains owned by the upstream Candidate/Resolver Contract Receipt and gates `END_TO_END_RAG` through the existing required Contract Receipt graph.

## Design Goals

1. Add an additive Schema Set `1.1.0` without changing committed `1.0.0` bytes or loader behavior.
2. Encode Rule outcome and Rule ID cardinality as a machine-validated contract.
3. Encode deterministic Source, Bundle, and dependency fault fixtures without free-form tags.
4. Fail closed when a `FROZEN` 1.1 Dataset has any unapproved required Gold dependency.
5. Produce a deterministic Schema Set ID/version/hash for `#214`.

## Versioning Model

`rag-eval.schema-set@1.1.0` is a complete 18-member schema set. Each registry member carries its own immutable schema version.

- `rag-eval.case@1.1.0`: changed
- `rag-eval.dataset-manifest@1.1.0`: changed
- the other 16 members remain their existing `1.0.0` contracts

The directory `evals/schemas/1.1.0/` contains the complete set so exact-path validation and hashing stay closed-world. Reused documents retain their `:1.0.0` `$id`; they are copied byte-for-byte from Schema Set `1.0.0`. The Schema Set hash is computed from sorted `{schema_id, schema_version, schema_sha256}` members, so the set version never impersonates a member version.

The Loader compares Case and Dataset Manifest payload versions with the selected registry members, not with the Schema Set version. This permits a future additive Set to reuse unchanged authoring members without weakening exact member-version validation.

The public Python APIs remain backward compatible:

- `SCHEMA_REGISTRY` and `schema_documents()` continue to mean `1.0.0`.
- new version-aware lookup/export APIs accept an explicit Schema Set version.
- the export CLI accepts an optional version whose default is `1.0.0`.

## Authoring Contract 1.1

### Typed runtime fixture

`RuntimeFixtureV1_1` extends the immutable runtime references with required fields:

| Field | Values |
| --- | --- |
| `source_eligibility_status` | `ELIGIBLE`, `EXPIRED`, `INACTIVE`, `CONFLICTING` |
| `bundle_eligibility_status` | `ELIGIBLE`, `SOURCE_INELIGIBLE`, `SCOPE_INELIGIBLE`, `MEMBER_INELIGIBLE` |
| `dependency_fault` | `NONE`, `PROVIDER_TIMEOUT`, `RETRIEVAL_FAILURE` |

`SOURCE_INELIGIBLE` requires a non-`ELIGIBLE` Source status. A non-eligible Source requires `SOURCE_INELIGIBLE`; this prevents internally contradictory fixtures. Scope and member failures are represented only on the Bundle axis.

### Rule outcome

Safety and End-to-End Gold add:

| Field | Values |
| --- | --- |
| `expected_rule_outcome` | `MATCHED_RULES`, `NO_MATCH`, `NOT_INVOKED` |
| `expected_rule_not_invoked_reason` | `null`, `SAFETY_ROUTED`, `SOURCE_INELIGIBLE`, `BUNDLE_INELIGIBLE` |

Other task types require both fields as explicit `null`, preserving the existing explicit-applicability shape. Answer Quality and Answer Grounding also require `expected_rule_ids=null`; Rule IDs are not meaningful without a Rule outcome.

Cardinality and consistency rules:

- `MATCHED_RULES`: `expected_rule_ids` is non-empty, reason is `null`, and Source and Bundle are eligible.
- `NO_MATCH`: `expected_rule_ids=[]`, reason is `null`, and Source and Bundle are eligible.
- `NOT_INVOKED`: `expected_rule_ids=[]`, a non-null typed reason, `dependency_fault=NONE`, and no provider/retrieval invocation are required.
- `SAFETY_ROUTED` requires `ELIGIBLE` Source and Bundle inputs, a non-`NORMAL` Safety disposition, and no provider/retrieval invocation; it cannot mask an eligibility root cause.
- `SOURCE_INELIGIBLE` requires a non-eligible Source fixture.
- `BUNDLE_INELIGIBLE` requires `SCOPE_INELIGIBLE | MEMBER_INELIGIBLE`; Source failures use `SOURCE_INELIGIBLE` and cannot be relabeled.

Provider and Retrieval faults occur after the Rule-first step and therefore cannot coexist with `NOT_INVOKED`. `PROVIDER_TIMEOUT` requires `TIMED_OUT` after provider invocation; `RETRIEVAL_FAILURE` requires `DEPENDENCY_ERROR` after retrieval invocation. Either fault can coexist with a `MATCHED_RULES | NO_MATCH` result that was already produced.

`expected_scope_codes` is the non-empty expected request Scope submitted by the Evaluation Request Guard, not a list derived from matched Rule IDs. It remains required for all Safety and End-to-End Rule outcomes, including `NO_MATCH` and `NOT_INVOKED`, so the Guard can exact-match the Case Scope and reject an empty request Scope.

These cross-field rules are enforced by the Case model, not only by Python call sites, and exported JSON Schema contains equivalent conditional constraints.

Medication fixtures remain `MATCHED`-only. OTC identity-insufficient cases are prohibited from being duplicated into `rag-eval.case`; their immutable upstream receipts are selected through the existing Evaluation Policy required Contract Receipt and Run/Gate graph.

## Loader Dispatch

The loader reads the Dataset Manifest object's `schema_id` and `schema_version` before model validation and selects one immutable authoring contract bundle:

- `1.0.0`: existing manifest, Case adapter, Evidence Mapping, and Rubric models;
- `1.1.0`: 1.1 manifest and Case adapter, with the unchanged 1.0 Evidence Mapping and Rubric members.

Unknown versions fail with `SCHEMA_INVALID`. Case resources must use the manifest's selected Case schema version. The policy's `artifact_schema_set_ref.reference.version` selects the registry/directory used for hash verification and graph registration. A manifest/policy schema-set mismatch fails closed.

## Frozen Gold Closure

After all referenced resources are loaded, a `DatasetManifest@1.1.0` with `status=FROZEN` is valid only when:

- every Case `review_provenance.team_gold_status` is `APPROVED`;
- Evidence Mapping `review_provenance.team_gold_status` is `APPROVED`;
- Critical Claim Rubric `review_provenance.team_gold_status` is `APPROVED`.

Failure maps to `REVIEW_PROVENANCE_INVALID`. This is a cross-resource loader invariant; it is not falsely expressed as a standalone JSON Schema constraint. Draft and non-frozen datasets may contain `DRAFT` or `REVIEWED` children. The 1.0 DEV fixture remains unchanged and loadable.

## Validation and Evidence

- Model tests cover every Rule outcome, wrong cardinality, mismatched typed reason, contradictory eligibility, and the OTC `MATCHED`-only boundary.
- Loader tests prove 1.0 regression compatibility, 1.1 version dispatch, Schema Set hash selection, and each child-approval failure.
- Export/parity tests prove both Schema Sets are deterministic, strict Draft 2020-12 documents and that all reused 1.0 members are byte-identical.
- Canonical committed Schema Set `1.1.0` supplies the exact `rag-eval.schema-set@1.1.0` hash recorded in the contract and handed to `#214`.

## Non-Goals

- Writing or freezing the 153 Case Dataset (`#214`)
- Runner, reports, baseline, metrics, CI, or Release Gate implementation (`#157` onward)
- Runtime Source/Rule/Bundle implementation
- Candidate/Resolver evaluation or copying upstream identity details into Evaluation Cases
