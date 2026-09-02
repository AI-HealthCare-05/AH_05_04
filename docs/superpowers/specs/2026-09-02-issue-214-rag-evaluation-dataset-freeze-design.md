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
5. Planning and architecture context under `FinalProject Documents/`

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

## 6. Case catalog

Version `1.0.0` contains 19 synthetic cases: 8 `HOLDOUT` and 11 `SAFETY_REGRESSION`. The count establishes a
deterministic initial Baseline input; it does not claim statistical sufficiency for Release thresholds.
Metric-specific minimum sample and independent-group counts are evaluated later by #158–#163. Insufficient
completed samples must become `COMPLETED/INCONCLUSIVE`, never `PASS`.

### HOLDOUT cases

| Case ID suffix | Task type | Contract exercised |
| --- | --- | --- |
| `holdout-retrieval-approved-hit-001` | `RETRIEVAL` | Relevant approved Evidence is retrieved and ranked. |
| `holdout-retrieval-no-hit-001` | `RETRIEVAL` | Irrelevant Evidence is not treated as a relevant hit. |
| `holdout-answer-quality-required-claims-001` | `ANSWER_QUALITY` | Required claims and sections are present without forbidden claims. |
| `holdout-answer-quality-insufficient-evidence-001` | `ANSWER_QUALITY` | Missing approved Evidence produces a limited response expectation. |
| `holdout-answer-grounding-citation-001` | `ANSWER_GROUNDING` | Claim, supporting Evidence, and locator form an exact Citation chain. |
| `holdout-answer-grounding-conflict-001` | `ANSWER_GROUNDING` | Conflicting Evidence prevents an unsupported definitive answer. |
| `holdout-e2e-routine-001` | `END_TO_END_RAG` | Normal approved-Evidence route with Citation and non-public synthetic result. |
| `holdout-e2e-out-of-scope-rx-rx-001` | `END_TO_END_RAG` | Prescription–prescription judgment is scope-limited and does not claim safety. |

### SAFETY_REGRESSION cases

| Case ID suffix | Task type | Contract exercised |
| --- | --- | --- |
| `safety-prompt-injection-001` | `SAFETY` | Untrusted instructions cannot override the fixed evaluation or answer policy. |
| `safety-critical-unsupported-claim-001` | `SAFETY` | A critical unsupported medical claim is forbidden and rejected. |
| `safety-missing-medical-citation-001` | `SAFETY` | A medical claim without a required Citation is forbidden. |
| `safety-citation-locator-mismatch-001` | `SAFETY` | Citation locator mismatch fails the expected grounding chain. |
| `safety-inactive-source-001` | `SAFETY` | Inactive or expired Evidence cannot support a publishable result. |
| `safety-urgent-routing-001` | `SAFETY` | High-risk synthetic context requires urgent routing. |
| `safety-emergency-routing-001` | `SAFETY` | Emergency synthetic context requires emergency routing. |
| `safety-otc-identity-insufficient-001` | `SAFETY` | Insufficient OTC identity blocks Rule evaluation and requests more information. |
| `safety-no-rule-not-safe-001` | `SAFETY` | Absence of a Rule never becomes a safe/no-interaction claim. |
| `safety-e2e-otc-rule-first-001` | `END_TO_END_RAG` | Approved OTC interaction Rule executes before generative retrieval and cites Rule Evidence. |
| `safety-e2e-stale-context-001` | `END_TO_END_RAG` | Stale Bundle or context cannot produce a current publishable result. |

Every Case uses `dataset_code=rag-holdout-safety`, `dataset_version=1.0.0`, a canonical `input_sha256`, and a
task-specific expected-value model. Non-applicable expected fields remain explicitly `null` as required by
the #122 contract.

## 7. Synthetic data and Evidence design

All identities and structured fixture values use synthetic tokens. Natural-language query or Gold text, if
needed to exercise a validator, must describe a fictional medication and fictional context and must not be
copied from a real patient, prescription, OCR output, Provider response, or licensed Source passage.

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

The Rubric and every safety/end-to-end Case use Product Safety or Medical reviewer roles allowed by the
schema. Team Dataset approval remains separate from external medical approval. External review fields may
remain `NOT_REQUESTED` for this synthetic structural freeze, but the Dataset cannot be used to claim clinical
or Production approval.

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

The Suite selects all 19 cases, both partitions, and all five task types. It is `required=true` because #157
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
- Exact 19-case catalog and expected partition/task matrix.
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
| Small synthetic Dataset is mistaken for a Release-quality statistical sample. | `runtime_eligible=false`, diagnostic Policy scopes, no Gate refs, and explicit future `INCONCLUSIVE` behavior. |
| Older local enums leak into fixtures. | Exact schema enums plus deprecated-token regression search. |
| Resolver/OCR quality is mixed into RAG scores. | Exclude R0–R3 and accept only upstream Contract Receipts in later E2E evaluation. |
| Synthetic data is mistaken for clinical approval. | Separate Team Dataset approval from external medical, pharmacy, Privacy, and Source gates. |

## 15. Acceptance boundary

Issue #214 is complete only when the merged Dataset is `FROZEN`, the final freeze commit has Dataset Custodian
approval, all integrity/privacy/leakage tests pass, and the #157 handoff references are recorded. Completion
does not mean that #157 can produce a Release decision; it means #157 has immutable, validated inputs from
which to implement deterministic execution and Baseline receipts.
