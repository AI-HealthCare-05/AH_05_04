# Issue #214 RAG Evaluation HOLDOUT·SAFETY_REGRESSION Dataset Freeze Design

## 1. Status and decision

- Issue: [#214](https://github.com/AI-HealthCare-05/AH_05_04/issues/214)
- Upstream foundation: #122 / PR #210
- Downstream runner: #157
- Design status: approved direction, implementation pending
- Execution boundary: local files and deterministic validation only

This change creates a new, versioned, synthetic evaluation Dataset that contains both `HOLDOUT` and
`SAFETY_REGRESSION` cases. It freezes the Dataset input contract needed by #157 without implementing a
Runner, Metric calculator, Provider adapter, database persistence, or Release Gate.

The existing `dev-foundation-v1` Dataset remains unchanged and continues to represent a non-runtime,
diagnostic `DEV` fixture. The new Dataset is a separate immutable version; it does not rename, promote, or
reinterpret the DEV fixture.

## 2. Authority and conflict resolution

The implementation follows these sources in order:

1. `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`
2. `docs/governance/decisions/2026-08-31-rag-p0-contract-freeze.md`
3. The schemas and loader merged by #122 under `ai_worker/tasks/evaluation/`
4. `docs/privacy-safety.md` and `docs/testing.md`
5. The separately maintained RAG document set identified by
   `rag-document-set-authority-manifest.json` version `2026-08-29.11`, whose normative evaluation source is
   `evaluation-plan.md` version `1.35` with SHA-256
   `526f83e796e7c3c262dfb84e1ef42a838a51b844123663f38384a14c565e542f`

The `FinalProject Documents/` folder is not an authority for Dataset size or partition allocation in this
design. The normative RAG evaluation plan supplies the 12-category, 153-Case initial workload. The repository
target and #122 schemas remain authoritative for machine-readable fields, enum values, validation, and status
semantics. If a RAG planning statement cannot be represented by the current approved schema, this issue does
not silently invent a field; it records the gap for a separately approved contract change.

Older local planning documents use `END_TO_END_FINAL`, `NOT_RUN`, and sometimes map an unexecuted required
cell to `INCONCLUSIVE`. Those values conflict with the newer approved repository target and are not copied
into the Dataset. The exact values used here are:

- Experiment type: `END_TO_END_RAG`
- Unexecuted required work: `NOT_EVALUATED` with `decision_status=null`
- Insufficient sample after completed execution: `COMPLETED/INCONCLUSIVE`

Resolver quality cases identified as R0–R3 in older planning are not duplicated in this Dataset. Candidate
Resolver and OCR quality remain upstream Contract Receipt concerns. This Dataset owns Retrieval, Answer,
Grounding, Citation, Rule-first, Scope, Safety, and end-to-end RAG expectations only.

## 3. Goals and non-goals

### Goals

- Provide immutable synthetic `HOLDOUT` and `SAFETY_REGRESSION` inputs for #157.
- Bind every Case to reviewed Gold expectations, Evidence, a Critical Claim Rubric, and four Leakage axes.
- Make Dataset, Case set, Evidence mapping, Rubric, Profile, Policy, and Suite hashes reproducible.
- Preserve a machine-verifiable approval transition from authoring to Dataset Custodian freeze.
- Ensure validation never creates a Release `PASS` or implies Production eligibility.

### Non-goals

- Metric computation, confidence intervals, threshold activation, or Baseline/Candidate comparison.
- Provider, RAG runtime, database, API, or Frontend execution.
- Patient data, OCR source values, internal identifiers, credentials, or Provider payloads.
- Medical, pharmacy, Privacy, Source, or Production approval.
- Public activation or a change to `PUBLIC_TRACK_F`.

## 4. Dataset identity and repository layout

The Dataset uses these stable identities:

| Item | Value |
| --- | --- |
| Dataset code | `rag-holdout-safety` |
| Dataset version | `1.0.0` |
| File prefix | `rag-holdout-safety-v1` |
| Scope | `SYNTHETIC_RAG_HOLDOUT_SAFETY` |
| Classification | `SYNTHETIC` |
| Final Dataset status | `FROZEN` |
| Runtime eligible | `false` |

Files are added under the existing #122 layout:

```text
evals/
├── policies/
│   ├── rag-holdout-safety-v1.comparison-policy.json
│   └── rag-holdout-safety-v1.evaluation-policy.json
├── profiles/
│   └── rag-holdout-safety-v1.profile.json
├── provenance/
│   └── rag-holdout-safety-v1.protected-artifact-receipt.json
├── retrieval/
│   ├── cases/rag-holdout-safety-v1/*.json
│   ├── evidence/rag-holdout-safety-v1.evidence-mapping.json
│   ├── evidence/resources/rag-holdout-safety-v1/*.json
│   └── manifests/
│       ├── rag-holdout-safety-v1.critical-claim-rubric.json
│       └── rag-holdout-safety-v1.dataset.json
└── suites/
    └── rag-holdout-safety-v1.suite.json
```

The committed schema set remains `1.0.0`. This work adds schema instances, not new schema fields, enums, or
result artifact types.

## 5. Provenance and freeze model

`DatasetManifest` requires exactly one of `fixture_git_commit_sha` and `protected_artifact_receipt_ref`.
This design uses `protected_artifact_receipt_ref` and leaves `fixture_git_commit_sha=null`.

The repository uses squash merge. A commit SHA recorded from an intermediate PR commit may disappear after
merge, and a file cannot reliably contain the SHA of the commit that contains itself. Content-addressed
resource hashes plus a protected artifact receipt therefore provide the stable Dataset input provenance.
The Runner and Baseline implementation commit are recorded later by #157 in the Baseline Freeze Receipt.

The protected artifact receipt proves integrity, not approval. Its `recorded_by.team_gold_status` remains
`REVIEWED` and never becomes `APPROVED`, as required by the #122 schema.

The Dataset freeze transition occurs inside the PR in two reviewable stages:

1. Authoring stage
   - Dataset `status=DRAFT`
   - Dataset and Case provenance `team_gold_status=REVIEWED`
   - `approved_by=null`, `approved_at=null`, `frozen_at=null`
   - All canonical hashes and the protected artifact receipt are present.
2. Freeze stage
   - The Dataset Custodian reviews the complete Case, Gold, Evidence, Rubric, and Leakage graph.
   - After a recorded approving review, a follow-up commit changes the Dataset to `status=FROZEN`.
   - Dataset provenance becomes `team_gold_status=APPROVED` with the real Dataset Custodian actor and review
     timestamp; `frozen_at` records the same freeze event.
   - The Dataset Custodian performs a final review on the exact freeze commit.

The implementer must not pre-populate or fabricate approval. Self-approval is rejected by the schema and is
not treated as a recoverable validation error.

After merge, any Case, Gold, Evidence, Rubric, Profile, Policy, or Suite content change creates a new Dataset
version and invalidates prior Baseline receipts. Version `1.0.0` is never edited in place for tuning.

## 6. Case allocation and catalog contract

Version `1.0.0` contains exactly 153 synthetic cases: 60 `HOLDOUT` and 93 `SAFETY_REGRESSION`. This is the
normative RAG evaluation plan's initial workload, not a permanent maximum and not proof of statistical
sufficiency. Metric-specific minimum Case counts, independent-group counts, estimators, confidence intervals,
and thresholds remain owned by later approved Comparison Policy versions in #158–#163. An executed scope
below those approved minimums becomes `COMPLETED/INCONCLUSIVE`, never `PASS`.

| Evaluation-plan category | Total | HOLDOUT | SAFETY_REGRESSION |
| --- | ---: | ---: | ---: |
| Prescription medication information and directions | 20 | 20 | 0 |
| Prescription medication–OTC interaction | 20 | 8 | 12 |
| Adverse effects and precautions | 15 | 10 | 5 |
| Prescription-linked food and activity guidance | 15 | 12 | 3 |
| Insufficient approved Evidence | 10 | 4 | 6 |
| Prescription–prescription interaction outside supported scope | 10 | 3 | 7 |
| Individual food, beverage, or supplement interaction outside supported scope | 10 | 3 | 7 |
| High-risk symptom or medication-change request | 15 | 0 | 15 |
| Expired, inactive, or conflicting Source | 10 | 0 | 10 |
| Source purpose or Scope approval violation | 10 | 0 | 10 |
| Inactive Endpoint or Operation member | 8 | 0 | 8 |
| Provider or Retrieval failure | 10 | 0 | 10 |
| **Total** | **153** | **60** | **93** |

Allocation is risk-based within a category rather than a mechanical category split:

- `HOLDOUT` contains representative frozen quality cases for Retrieval, Answer, Grounding, Citation, and
  stable Scope behavior under valid inputs and approved Sources.
- `SAFETY_REGRESSION` contains cases whose incorrect handling can create harm or bypass a fail-closed boundary,
  including critical or forbidden claims, high-risk routing, unsupported or out-of-scope requests, Rule-first
  reversal, insufficient identity, Source or Scope ineligibility, Prompt Injection, and dependency failures.
- The schema `task_type` records the primary evaluator (`RETRIEVAL`, `ANSWER_QUALITY`, `ANSWER_GROUNDING`,
  `SAFETY`, or `END_TO_END_RAG`); the evaluation-plan category is retained as a stable Slice/tag. A category
  does not create a new task-type enum.
- The allocation unit is a complete Leakage group. Cases sharing any required Leakage axis cannot be split
  merely to satisfy a numeric quota. Authoring must select independent seeds so both the 60/93 counts and the
  no-leakage invariant hold simultaneously.

Every Case uses `dataset_code=rag-holdout-safety`, `dataset_version=1.0.0`, a canonical `input_sha256`, and the
task-specific expected-value model already frozen by #122. Non-applicable expected fields remain explicitly
`null` as required by that contract.

The 153-Case count is frozen for the initial version. It is not a promise that all future versions remain at
153. `HOLDOUT` v1 Cases are immutable. `SAFETY_REGRESSION` is append-only through a new Dataset version when a
new harmful failure class is found or when the activated prescription–OTC Rule Set contains more positive
Rule members than the frozen Safety cases can cover. Existing Safety Cases are never removed, rewritten, or
moved to HOLDOUT.

## 7. Synthetic data, Gold, and Evidence design

All identities and structured fixture values use synthetic tokens. Natural-language query or Gold text, if
needed to exercise a validator, must describe a fictional medication and fictional context and must not be
copied from a real patient, prescription, OCR output, Provider response, or licensed Source passage.

Each of the 153 Cases includes structured Gold. The Dataset does not use one canonical prose answer as an
exact-match oracle because multiple safe phrasings may be correct. The schema's structured expected object is
the scoring authority:

- Retrieval Gold: `relevant_evidence_refs` and `required_evidence_refs`
- Answer Gold: required and optional `gold_claims[]`
- Prohibited output Gold: `forbidden_claims[]` with criticality and stable reason codes
- Citation Gold: Claim-to-Evidence-to-locator `expected_citations[]`
- Rule and Scope Gold: `expected_rule_ids[]` and `expected_scope_codes[]`
- Safety and output Gold: expected response level, safety disposition, execution status, Release decision,
  fallback code, required/omitted sections, and risk level
- Side-effect sentinels: expected Provider invocation, Retrieval invocation, and publication permission

Retrieval Cases use exact Evidence-set expectations. Claim-bearing Cases are evaluated against required and
forbidden semantic claims plus exact Evidence and locator bindings. Safety Cases additionally use deterministic
routing, fallback, invocation, and publication sentinels. A human-readable reference response may exist only
as reviewer guidance; it is not the normative Gold and is not scored by exact prose equality.

Missing, unapproved, or internally inconsistent Gold invalidates a Case. A Dataset cannot be `FROZEN` unless
all 153 Cases and their Gold have the required Team review provenance. External medical review remains a
separate state. Until that review is approved where required, the Dataset may support local structural and
closed-demo evaluation but cannot establish clinical or Production approval.

Evidence resources use only the existing approved types:

- `PRESCRIPTION`
- `KNOWLEDGE_CHUNK`
- `INTERACTION_RULE`
- `LIFESTYLE_GUIDELINE`
- `SAFETY_POLICY`

Evidence Mapping owns each Evidence logical ID, type, version, locator, fixture resource reference, and
content hash. Gold Claims can cite only Evidence listed in their `supporting_evidence_ref_ids`. Expected
Citations must reference an existing Gold Claim, one of that claim's supporting Evidence IDs, and the exact
Evidence Mapping locator.

The Critical Claim Rubric contains unique rule IDs and reason codes covering at least:

- unsupported critical medical claim
- missing required medical Citation
- Citation locator mismatch
- inactive or expired Evidence
- unsafe no-rule interpretation
- prohibited prescription–prescription safety statement
- policy override or Prompt Injection compliance
- incorrect urgent or emergency routing

The Rubric and every Safety/end-to-end Case use Product Safety or Medical reviewer roles allowed by the
schema. Team Dataset approval remains separate from external medical approval. External review fields use
the exact #122 schema states and cannot be changed to `APPROVED` without the required immutable approval
receipt. Synthetic classification does not make a medical Gold judgment `NOT_APPLICABLE` by itself.

## 8. Leakage controls

Every Case records all four required Leakage axes:

- `question_template`
- `source_segment`
- `medication_family`
- `transform_origin`

No value on any axis may appear in both `HOLDOUT` and `SAFETY_REGRESSION`. Closely related positive and
negative transformations stay within the same partition and share a `transform_origin` when they derive
from the same authored seed. A new derived Case cannot be placed in another partition merely to increase
the apparent independent sample count.

The loader remains the enforcement point for cross-partition leakage. Tests additionally assert the exact
partition-to-group map for the committed Dataset so accidental regrouping fails before hash regeneration.

The HOLDOUT content is repository-visible but governance-protected: it cannot be used for prompt, retrieval,
or policy tuning after freeze. A tuning need creates a DEV Case or a new Dataset version; it never edits the
frozen HOLDOUT in place.

## 9. Profile, Policy, and Suite

The evaluation Profile requires:

- experiment types: `ANSWER_GROUNDING_SAFETY`, `END_TO_END_RAG`, `KNOWLEDGE_RETRIEVAL`
- partitions: `HOLDOUT`, `SAFETY_REGRESSION`
- the single Dataset execution Suite
- no Release Gate references in this issue
- `runtime_eligible=false`

`runtime_eligible=false` is deliberate. Dataset freeze makes the input immutable; it does not prove the
Runner, required Metrics, Contract Receipts, comparison policy, independent approval, or Release Gate.

The Suite selects all 153 cases, both partitions, and all five task types. It is `required=true` because #157
must preserve every selected Case in the Run Bundle. Its adapter and command identify the future #157
execution surface, but this PR never invokes it:

```text
adapter_id: rag-evaluation-runner.v1
command: uv run python -m ai_worker.tasks.evaluation run
pass_rule: ALL_SELECTED_CASES_RECORDED_NO_RELEASE_DECISION
```

The Comparison Policy contains diagnostic, non-release scopes only. Each scope is `required=false`, uses a
non-release decision basis, and does not activate the illustrative thresholds from older planning documents.
The threshold field required by schema is serialized as canonical `0` and is explicitly non-normative because
the scope is diagnostic. #158–#163 must create a new approved Policy version before using any threshold for a
Release decision.

The Evaluation Policy binds the Profile, Comparison Policy, both partition references, Suite, and schema set.
It contains no required Gate references. All member ordering and member-manifest hashes are derived from
canonical content.

## 10. Hash and reference flow

The build order is deterministic:

1. Write Evidence resource files and calculate their file hashes.
2. Write Evidence Mapping entries and calculate its canonical manifest hash.
3. Write the Critical Claim Rubric and calculate its canonical hash.
4. Write Cases using final Evidence and Rubric references; calculate each Case file hash.
5. Build sorted Case resources and derive partition counts, resource set hash, and protected artifact receipt.
6. Build Suite expected Case set hash.
7. Build Profile, Comparison Policy, and Evaluation Policy hashes and member references.
8. Build the Dataset Manifest with all final references and derive its manifest hash.
9. Load the complete graph twice from fresh reads and compare every canonical hash.

The existing canonical JSON implementation is the only serializer and hashing authority. Hand-formatted JSON,
filesystem enumeration order, timestamps generated during validation, and absolute paths never affect a hash.

## 11. Validation and failure behavior

Validation is fail-closed before any Runner work. The Dataset is rejected for:

- invalid or duplicate JSON keys
- schema/version mismatch
- stale, missing, or mismatched content hashes
- missing, duplicate, unsorted, or cross-Dataset Case resources
- partition count mismatch
- cross-partition Leakage on any axis
- missing Evidence, Claim, Citation, Rule, or Rubric references
- Citation locator mismatch
- invalid review provenance or self-approval
- `FROZEN` without Dataset Custodian approval and `frozen_at`
- forbidden privacy key or sensitive value
- symlink, traversal, absolute path, or root escape
- deprecated `END_TO_END_FINAL` or `NOT_RUN` values

Errors expose stable non-sensitive codes only. They do not include query text, Gold text, resource paths supplied
by an attacker, patient-like values, or full validation payloads.

The validation-only CLI may emit its existing validation receipt. It must not emit `run.json`, metrics,
comparison, gate, Baseline receipts, or a Release decision.

## 12. Testing strategy

Implementation follows test-first changes around the existing #122 loader and schemas.

### Dataset acceptance

- Exact Dataset identity, version, status, classification, and partition counts.
- Exact 153-Case catalog, the 12-category 60/93 allocation matrix, and expected partition/task matrix.
- Complete structured Gold for every Case, including task-applicable Evidence, Claims, Citations, Rule, Scope,
  Safety, fallback, invocation, and publication expectations.
- Complete Evidence, Rubric, Profile, Policy, Suite, and protected receipt graph.
- Dataset Custodian approval requirements for the final frozen fixture.
- `runtime_eligible=false`, no Gate refs, and non-release Policy scopes.

### Integrity and leakage regression

- Mutate every resource type and recompute only the immediate hash; loader still rejects stale downstream refs.
- Move a Case across partitions for each Leakage axis; loader rejects it.
- Duplicate a Case ID, logical Evidence ID, Claim ID, Rubric rule ID, or reason code; loader rejects it.
- Change Citation Evidence or locator while recomputing affected hashes; semantic reference validation rejects it.
- Replace `END_TO_END_RAG` with `END_TO_END_FINAL` or an execution state with `NOT_RUN`; schema validation rejects it.

### Privacy and safety regression

- Inject each #122 forbidden privacy key into Case, context, expected output, Evidence, receipt, and Policy data.
- Inject patient identifiers, OCR raw/normalized values, insurance codes, Provider payloads, credentials, and
  secrets; validation rejects them without echoing the sentinel.
- Assert the committed Dataset contains synthetic classification only.

### Determinism

- Load the Dataset twice from fresh reads.
- Compare Dataset manifest, resource set, Evidence mapping, Rubric, Profile, Policy, Suite, schema set, and Case
  set hashes.
- Run the validation CLI twice into separate temporary output directories and compare semantic receipt content
  after excluding receipt identity and recording time fields defined as run-specific.

### Required commands

```bash
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run --with jsonschema pytest ai_worker/tests/evaluation -q
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run mypy ai_worker/tasks/evaluation
git diff --check
```

The PR records the two validation runs and their hash comparison. Full application, database, Provider, and
Frontend suites are not required because this change does not touch those surfaces.

## 13. Documentation and handoff

The PR updates `evals/README.md` with:

- the distinction between DEV, frozen synthetic HOLDOUT/SAFETY_REGRESSION, and future protected clinical data
- the validation command
- the Dataset/Profile/Policy/Suite references consumed by #157
- the prohibition on editing version `1.0.0` after freeze
- the statement that Dataset freeze is not Release, clinical, Privacy, Source, or Production approval

The PR does not promote the entire RAG Evaluation target document to `current/`. Metrics, Runner, database,
end-to-end execution, Gate decisions, and external approvals remain unimplemented, so the target as a whole
is still `Approved Target · Not implemented` outside this completed slice.

The #157 handoff contains the exact immutable references and hashes for:

- Dataset Manifest and resource set
- partition references
- Evidence Mapping
- Critical Claim Rubric
- Evaluation Profile
- Comparison Policy
- Evaluation Policy
- Suite
- artifact schema set
- protected artifact receipt

## 14. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Frozen data is tuned against after becoming visible. | Immutable versioning, no in-place edits, and derived tuning cases go to DEV. |
| Team approval is fabricated before review. | Two-stage PR transition and final review on the exact freeze commit. |
| Squash merge invalidates a recorded fixture commit. | Use content hashes and protected artifact receipt; #157 records Runner/Baseline commits later. |
| The initial 153-Case workload is mistaken for a permanent maximum or Release-quality statistical sample. | `runtime_eligible=false`, diagnostic Policy scopes, no Gate refs, explicit future `INCONCLUSIVE` behavior, and append-only Safety versioning. |
| A later active OTC Rule Set is larger than the frozen positive-Rule coverage. | Keep HOLDOUT v1 immutable and publish a new Dataset version that appends one or more Safety Cases until every active positive Rule member is executed. |
| A prose reference answer is mistaken for the Gold oracle. | Make structured Claims, forbidden Claims, Evidence, Citation, routing, fallback, invocation, and publication expectations normative; reference prose is reviewer guidance only. |
| Older local enums leak into fixtures. | Exact schema enums plus deprecated-token regression search. |
| Resolver/OCR quality is mixed into RAG scores. | Exclude R0–R3 and accept only upstream Contract Receipts in later E2E evaluation. |
| Synthetic data is mistaken for clinical approval. | Separate Team Dataset approval from external medical, pharmacy, Privacy, and Source gates. |

## 15. Acceptance boundary

Issue #214 is complete only when the merged Dataset is `FROZEN`, the final freeze commit has Dataset Custodian
approval, all integrity/privacy/leakage tests pass, and the #157 handoff references are recorded. Completion
does not mean that #157 can produce a Release decision; it means #157 has immutable, validated inputs from
which to implement deterministic execution and Baseline receipts.
