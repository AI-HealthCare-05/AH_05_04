# Issue #214 RAG Evaluation Dataset Freeze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce the schema-valid, deterministic 153-case synthetic `rag-holdout-safety@1.0.0` Dataset candidate, obtain the named human reviews, and freeze the exact approved content for #157 without executing HOLDOUT or creating a Release decision.

**Architecture:** Treat the 153 committed Case JSON files as the only allocation source of truth. Dataset-specific tests derive the category/partition/task/archetype projection from those files, while the existing generic loader owns schema, graph, hash, leakage, privacy, and approval-closure validation. Authoring and freeze are separate commits: first publish a complete `DRAFT` candidate, then change provenance and Dataset status to `FROZEN` only after the assigned reviewers approve the exact content.

**Tech Stack:** Python 3.13, Pydantic v2, pytest, canonical I-JSON helpers, exported JSON Schema 2020-12, static JSON evaluation artifacts.

**Spec:** `docs/superpowers/specs/2026-09-02-issue-214-rag-evaluation-dataset-freeze-design.md`

> **2026-09-03 amendment:** #245 merged Schema Set `1.2.0`. This candidate must use its DRAFT provenance rules: reviewer and review timestamp are `null`, and review evidence is empty until a real `EVALUATION_REVIEWER` review occurs. Retrieval resource records exclude authoring-only `expected_*` fields so a future #157 runner cannot retrieve Gold outcome metadata.

## Global Constraints

- Consume `rag-eval.schema-set@1.2.0` SHA-256 `1bdc6c8d2c5b62415b7f2f59e42ffdf7d67243ae4cccd1e6b3a3116daae73b06`; do not change shared schemas or Loader acceptance semantics.
- Create exactly 153 synthetic Cases: `HOLDOUT=60`, `SAFETY_REGRESSION=93`, with the exact task and archetype matrices in the Spec.
- Keep `evals/retrieval/cases/dev-foundation-v1/` and Schema Set `1.0.0` byte-for-byte unchanged.
- Use only `SYNTHETIC` fixtures; never include patient data, OCR raw/normalized values, insurance codes, internal identifiers, Provider payloads, secrets, or licensed Source passages.
- Preserve all four leakage axes and never reuse an axis value across `HOLDOUT` and `SAFETY_REGRESSION`.
- Structured Claims, forbidden Claims, Evidence, Citations, Rule outcome, Scope, routing, fallback, invocation, and publication fields are the Gold authority; exact prose answers are not.
- Keep `runtime_eligible=false`, all Comparison Policy scopes diagnostic/non-required, and all Gate references empty.
- Do not execute or inspect HOLDOUT results. Validation may emit validation receipts only.
- Do not fabricate approval. The first implementation commit remains `DRAFT`; `FROZEN` and `APPROVED` provenance require the named human reviews on the exact candidate.

---

### Task 1: Lock the Dataset catalog contract with failing tests

**Files:**
- Create: `ai_worker/tests/evaluation/test_holdout_safety_dataset.py`

**Interfaces:**
- Consumes: `load_dataset(manifest_path: Path, *, evals_root: Path) -> ValidatedDataset`
- Produces: Dataset-specific assertions for identity, exact allocation, deterministic Case IDs, archetypes, leakage, Gold completeness, configuration safety, and future freeze closure.

- [ ] **Step 1: Add the absent-fixture acceptance test**

```python
EVALS_ROOT = Path(__file__).parents[3] / "evals"
MANIFEST = EVALS_ROOT / "retrieval/manifests/rag-holdout-safety-v1.dataset.json"


def test_holdout_safety_dataset_loads_with_exact_identity_and_counts() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)
    assert dataset.manifest.dataset_code == "rag-holdout-safety"
    assert dataset.manifest.dataset_version == "1.0.0"
    assert dataset.manifest.partition_counts.HOLDOUT == 60
    assert dataset.manifest.partition_counts.SAFETY_REGRESSION == 93
    assert len(dataset.cases) == 153
```

- [ ] **Step 2: Run the test and verify RED**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run pytest ai_worker/tests/evaluation/test_holdout_safety_dataset.py -q`

Expected: FAIL because `rag-holdout-safety-v1.dataset.json` does not exist.

- [ ] **Step 3: Encode the exact projection tables in the test**

Add immutable expected mappings for:

```python
EXPECTED_PARTITIONS = {"HOLDOUT": 60, "SAFETY_REGRESSION": 93}
EXPECTED_TASKS = {
    ("HOLDOUT", "RETRIEVAL"): 11,
    ("HOLDOUT", "ANSWER_QUALITY"): 15,
    ("HOLDOUT", "ANSWER_GROUNDING"): 15,
    ("HOLDOUT", "END_TO_END_RAG"): 19,
    ("SAFETY_REGRESSION", "SAFETY"): 56,
    ("SAFETY_REGRESSION", "END_TO_END_RAG"): 37,
}
```

Derive category and archetype from required `slice_ids` values `category:<code>` and `archetype:<code>`, then assert the complete matrices from Spec section 6. Validate the Case ID regex:

```python
r"^rag-hs-v1-(?:h|s)-[a-z0-9-]+-(?:ret|ansq|grnd|safe|e2e)-[a-z0-9-]+-[0-9]{3}$"
```

- [ ] **Step 4: Commit the failing contract test**

```bash
git add ai_worker/tests/evaluation/test_holdout_safety_dataset.py
git commit -m "test(evals): define holdout safety dataset contract"
```

### Task 2: Author Evidence resources, Mapping, and Critical Claim Rubric

**Files:**
- Create: `evals/retrieval/evidence/resources/rag-holdout-safety-v1/*.json`
- Create: `evals/retrieval/evidence/rag-holdout-safety-v1.evidence-mapping.json`
- Create: `evals/retrieval/manifests/rag-holdout-safety-v1.critical-claim-rubric.json`

**Interfaces:**
- Consumes: `canonical_json_bytes()`, `canonical_sha256()`, `sha256_hex()` and the existing authoring schemas.
- Produces: Stable Evidence IDs, exact locators, content hashes, and Rubric reason codes referenced by all Cases.

- [ ] **Step 1: Create minimal synthetic Evidence records**

Use one or more synthetic records per approved evidence type. Each file is canonical JSON with stable record keys and no real medical assertion, for example:

```json
{"record_id":"SYNTHETIC_KNOWLEDGE_MED_INFO","statement":"SYNTHETIC_APPROVED_MEDICATION_INFORMATION"}
```

- [ ] **Step 2: Create the Evidence Mapping candidate**

Use `schema_version=1.2.0`, `mapping_id=rag-holdout-safety-evidence`, `mapping_version=1.0.0`, sorted unique entries, `target_kind=FIXTURE_RECORD`, and exact file hashes. Keep `team_gold_status=DRAFT`, `reviewed_by=null`, `reviewed_at=null`, `evidence_review_refs=[]`, and approval fields `null` until the named human review occurs. Record `@Jye-rookie` as `EVALUATION_REVIEWER` with immutable review evidence only after that completed review.

- [ ] **Step 3: Create the Rubric candidate**

Include stable rules and reason codes for unsupported critical claim, missing medical Citation, locator mismatch, inactive Evidence, unsafe no-rule interpretation, prohibited prescription–prescription safety statement, Prompt Injection/policy override, and urgent/emergency routing. Use all applicable claim-bearing task types and scope `SYNTHETIC_RAG_HOLDOUT_SAFETY`.

- [ ] **Step 4: Validate schema and self-hashes**

Run focused model validation for the Mapping and Rubric, then assert each referenced resource file hash matches `content_sha256`.

- [ ] **Step 5: Commit the Evidence and Rubric candidate**

```bash
git add evals/retrieval/evidence evals/retrieval/manifests/rag-holdout-safety-v1.critical-claim-rubric.json
git commit -m "feat(evals): add holdout safety evidence and rubric"
```

### Task 3: Author the exact 153 structured-Gold Case files

**Files:**
- Create: `evals/retrieval/cases/rag-holdout-safety-v1/*.json` (153 files)

**Interfaces:**
- Consumes: `rag-eval.case@1.2.0`, final Evidence Mapping IDs/locators, final Rubric reference, and the Spec matrices.
- Produces: The sole allocation authority consumed by the Dataset Manifest and conformance tests.

- [ ] **Step 1: Build an execution-only authoring script outside the repository**

Create `/private/tmp/build_issue214_dataset.py` with explicit in-memory matrix constants copied from Spec section 6. The script must call `EVALUATION_CASE_ADAPTER_V1_2.validate_python(payload)` before writing each canonical JSON file and must reject duplicate Case IDs or leakage-axis reuse across partitions. Do not commit this script or an allocation manifest.

- [ ] **Step 2: Generate HOLDOUT cases**

Generate exactly 60 files with task totals `R=11`, `AQ=15`, `AG=15`, `E2E=19`. Use partition-specific synthetic Leakage tokens and complete task-applicable Gold. `MATCHED_RULES` has non-empty Rule IDs; `NO_MATCH` has empty Rule IDs; all healthy Rule inputs use Source and Bundle `ELIGIBLE` with `dependency_fault=NONE`.

- [ ] **Step 3: Generate SAFETY_REGRESSION cases**

Generate exactly 93 files with `SAFETY=56`, `END_TO_END_RAG=37` and the exact archetype counts. Encode canonical causes:

```text
SAFETY_ROUTED -> source ELIGIBLE, bundle ELIGIBLE, dependency NONE
SOURCE_INELIGIBLE -> source EXPIRED|INACTIVE|CONFLICTING, bundle SOURCE_INELIGIBLE, dependency NONE
BUNDLE_INELIGIBLE -> source ELIGIBLE, bundle SCOPE_INELIGIBLE|MEMBER_INELIGIBLE, dependency NONE
PROVIDER_TIMEOUT -> MATCHED_RULES|NO_MATCH, expected status TIMED_OUT, provider invoked
RETRIEVAL_FAILURE -> MATCHED_RULES|NO_MATCH, expected status DEPENDENCY_ERROR, retrieval invoked
```

Candidate-skips-required-Rule Cases retain `MATCHED_RULES` and the expected Rule ID; they do not use `NOT_INVOKED` Gold.

- [ ] **Step 4: Validate structured Gold semantics**

For every claim-bearing Case assert Citations reference only supporting Evidence and exact Mapping locators. For the non-supporting Evidence archetype, assert that Evidence is absent from `supporting_evidence_ref_ids` and `expected_citations`. Assert every Case has four non-empty Leakage axes and `data_classification=SYNTHETIC`.

- [ ] **Step 5: Run the catalog test**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run pytest ai_worker/tests/evaluation/test_holdout_safety_dataset.py -q`

Expected: the absent Manifest test still fails; direct Case schema/catalog tests pass.

- [ ] **Step 6: Commit the Case candidate**

```bash
git add evals/retrieval/cases/rag-holdout-safety-v1
git commit -m "feat(evals): add holdout safety gold cases"
```

### Task 4: Bind the Dataset Manifest, receipt, Profile, Policies, and Suite

**Files:**
- Create: `evals/retrieval/manifests/rag-holdout-safety-v1.dataset.json`
- Create: `evals/provenance/rag-holdout-safety-v1.protected-artifact-receipt.json`
- Create: `evals/profiles/rag-holdout-safety-v1.profile.json`
- Create: `evals/policies/rag-holdout-safety-v1.comparison-policy.json`
- Create: `evals/policies/rag-holdout-safety-v1.evaluation-policy.json`
- Create: `evals/suites/rag-holdout-safety-v1.suite.json`

**Interfaces:**
- Consumes: final Case file hashes, Mapping/Rubric hashes, Schema Set hash, and existing Profile/Policy/Suite models.
- Produces: A complete loadable `DRAFT` graph and immutable references for #157.

- [ ] **Step 1: Create the Suite and Profile**

The Suite selects all 153 Case IDs, both partitions, and all five task types, using `adapter_id=rag-evaluation-runner.v1` and `pass_rule=ALL_SELECTED_CASES_RECORDED_NO_RELEASE_DECISION`. The Profile is `runtime_eligible=false`, includes no Gate refs, and requires the Suite.

- [ ] **Step 2: Create the diagnostic Comparison Policy**

Create only `required=false` scopes with canonical threshold `0`; no scope may authorize Release or HOLDOUT execution. The schema-required approver is the `SYSTEM/rag-eval-draft-validator` validation actor, not a human policy approval. A later approved comparison policy is a separate #157 execution prerequisite.

- [ ] **Step 3: Create the Evaluation Policy**

Bind Profile, Comparison Policy, both partition references, Suite, and `rag-eval.schema-set@1.2.0` hash `1bdc6c8d...b06`. Derive the member manifest and policy self-hash from canonical content.

- [ ] **Step 4: Create the Case-only protected receipt**

Bind the sorted Case paths and Dataset resource-set hash. Keep receipt provenance `DRAFT` before human review; the receipt must never claim Team approval.

- [ ] **Step 5: Create the DRAFT Dataset Manifest**

Set `status=DRAFT`, `frozen_at=null`, `fixture_git_commit_sha=null`, and reference the protected receipt. Use exact `partition_counts`, sorted Case resources, Mapping/Rubric refs, resource-set hash, and manifest self-hash. Dataset provenance remains unapproved until the freeze stage.

- [ ] **Step 6: Run the complete loader acceptance test**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run pytest ai_worker/tests/evaluation/test_holdout_safety_dataset.py -q`

Expected: PASS for the complete DRAFT graph.

- [ ] **Step 7: Commit the bound Dataset graph**

```bash
git add evals
git commit -m "feat(evals): bind holdout safety dataset graph"
```

### Task 5: Add integrity, leakage, privacy, and determinism regression coverage

**Files:**
- Modify: `ai_worker/tests/evaluation/test_holdout_safety_dataset.py`

**Interfaces:**
- Consumes: committed Dataset graph and existing stable loader error codes.
- Produces: Mutation-sensitive regression proof for the #214 acceptance boundary.

- [ ] **Step 1: Add copied-fixture mutation helpers**

Copy `evals/` to `tmp_path`, mutate one artifact, refresh only its immediate hash, and call `load_dataset()`. Keep helpers Dataset-specific and do not alter production Loader behavior.

- [ ] **Step 2: Add graph and Gold negative tests**

Cover stale downstream references, duplicate Case/Evidence/Claim/Rubric IDs, Citation locator mismatch, unsupported Evidence incorrectly attached to Gold, deprecated `END_TO_END_FINAL`/`NOT_RUN`, and a child approval downgraded below `APPROVED` for the future frozen fixture.

- [ ] **Step 3: Add four-axis leakage tests**

For each axis, mutate a Case so its value crosses partitions, recompute dependent Case/Manifest/receipt hashes, and assert `EVAL_LEAKAGE_DETECTED`.

- [ ] **Step 4: Add privacy tests**

Inject each repository deny-key/value category into Case, Evidence, receipt, and Policy copies. Assert validation fails with a stable non-sensitive code and never echoes the sentinel.

- [ ] **Step 5: Add deterministic double-load and double-CLI validation tests**

Load from two fresh reads and compare Dataset manifest, resource set, Mapping, Rubric, Profile, Policy, Suite, Schema Set, and Case-set references. Run validation-only CLI twice to separate result files and compare semantic receipt fields after excluding receipt ID and timestamps.

- [ ] **Step 6: Run Evaluation tests**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run --with jsonschema pytest ai_worker/tests/evaluation -q`

- [ ] **Step 7: Commit regression coverage**

```bash
git add ai_worker/tests/evaluation/test_holdout_safety_dataset.py
git commit -m "test(evals): verify dataset freeze invariants"
```

### Task 6: Document the DRAFT handoff and request content review

**Files:**
- Modify: `evals/README.md`
- Modify: `docs/superpowers/specs/2026-09-02-issue-214-rag-evaluation-dataset-freeze-design.md`

**Interfaces:**
- Consumes: calculated immutable references from the complete DRAFT graph.
- Produces: Reviewer instructions and the future #157 handoff boundary.

- [ ] **Step 1: Document the Dataset distinction and validation command**

Explain DEV versus frozen synthetic HOLDOUT/SAFETY_REGRESSION, prohibit in-place editing of Dataset `1.0.0` after freeze, list all immutable artifact references, and state that validation is not Release, clinical, Privacy, Source, or Production approval.

- [ ] **Step 2: Record the resolved #216 prerequisite**

Record #216's merged Schema Set `1.1.0` reference as a resolved historical prerequisite, and hand off the current candidate's Schema Set `1.2.0` immutable reference. Retain `WAITING_FOR_APPROVED_COMPARISON_POLICY` as the later HOLDOUT execution blocker.

The current post-final-review-remediation DRAFT graph handed to the named reviewers is:

The scored natural-language surface is Korean (`ko-KR`) for queries, Gold claims, forbidden semantic rules,
Evidence statements, and Rubric descriptions. Stable IDs, enums, reason codes, locators, and synthetic contract
tokens retain their canonical spelling.

| Item | Immutable ID@version | SHA-256 |
| --- | --- | --- |
| Dataset Manifest | `rag-holdout-safety@1.0.0` | `1feaca37deca87466acf6b28a429c9484f9718c014ff687618a2540e8ef63717` |
| Case resource set | `rag-holdout-safety@1.0.0` | `e0f997f1085f4cce397bc473af80442a02535ecdcae38a1ba29c3a8ceecf3eb2` |
| HOLDOUT partition | `rag-holdout-safety:HOLDOUT@1.0.0` | `e376dc8b347babf097fca9f507bed55696d43d684f119f8b455da89ea6e23d9b` |
| SAFETY_REGRESSION partition | `rag-holdout-safety:SAFETY_REGRESSION@1.0.0` | `4678cc81a98703b3154b08f8297c2dca32399e926065341a5a617959b95d0131` |
| Evidence Mapping | `rag-holdout-safety-evidence@1.0.0` | `6f623450952b55e321009970381b65c6b266f56a37e1f750f59ca232c5a4c437` |
| Critical Claim Rubric | `rag-holdout-safety-critical-claims@1.0.0` | `afa570eec5bf30a7c4ce518e9483be8a5c24ab99230f946ffdcfe0a46c997cd2` |
| Evaluation Profile | `rag-holdout-safety-profile@1.0.0` | `812ff6bb8cce18cd0e0c80f22ac468005a128e4ed2b30f21ad0381d7b91a0ed1` |
| Comparison Policy (validation-only) | `rag-holdout-safety-comparison@1.0.0` | `9d15cccbb271c3b3bd0735352a7e58f3c2b590d81df991f47de5db7ef292189f` |
| Evaluation Policy | `rag-holdout-safety-policy@1.0.0` | `d4f254adfe28a2cc789c02ce7de18d26e79ff1ead21e2bbebdf3df0eda551f8e` |
| Evaluation Policy member manifest | `rag-holdout-safety-policy@1.0.0` | `034b0c58816774512e6b90ba9c96265f2b01eda2d001438e15b56ec12c2e48bb` |
| Suite | `rag-holdout-safety-validation-suite@1.0.0` | `b942271d8c842a0e3e6fd8c5fb595678aa5504ee1571f12e0cacaf01283042e4` |
| Selected Case set | `rag-holdout-safety-validation-suite@1.0.0` | `df3e20f532548ed92b5c4231a95d0d8f4be268ad6494155d70cc5ccc73a94bbd` |
| Case-only protected artifact receipt | `rag-holdout-safety-protected-receipt@1.0.0` | `f04011915018dd178841171da2bcc652178c9724be9f1905248e03786147c1ca` |
| Artifact Schema Set | `rag-eval.schema-set@1.2.0` | `1bdc6c8d2c5b62415b7f2f59e42ffdf7d67243ae4cccd1e6b3a3116daae73b06` |

The receipt reference uses its canonical file hash; its internal `receipt_hash` is
`188bc557265a322c85ac332195a4b7aeab7e05701fe7463e78f508f69070ef24`.

These values remain DRAFT review inputs. They do not record completed human review, Dataset approval, freeze,
HOLDOUT execution, or Release authorization. Task 7 remains gated on the actual named review events.

- [ ] **Step 3: Run DRAFT verification**

```bash
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run --with jsonschema pytest ai_worker/tests/evaluation -q
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run mypy ai_worker/tasks/evaluation ai_worker/tests/evaluation/test_holdout_safety_dataset.py
git diff --check
```

- [ ] **Step 4: Push the DRAFT candidate and request assigned review**

Request `@Jye-rookie` for Gold/Evidence and `@hazelnutflavoured` for Dataset/Safety approval, with `@phina-io` for Schema/Loader cross-review. Explicitly say the PR is not yet frozen and approvals are not pre-populated.

### Task 7: Freeze only the exact human-approved candidate

**Files:**
- Modify: all approved Case JSON files only if provenance status/timestamps require promotion
- Modify: `evals/retrieval/evidence/rag-holdout-safety-v1.evidence-mapping.json`
- Modify: `evals/retrieval/manifests/rag-holdout-safety-v1.critical-claim-rubric.json`
- Modify: `evals/retrieval/manifests/rag-holdout-safety-v1.dataset.json`
- Modify: dependent receipt/policy refs and hashes affected by the approval-only content change

**Interfaces:**
- Consumes: explicit GitHub review approvals from the named distinct humans on the DRAFT candidate.
- Produces: final `FROZEN` Dataset with complete child-Gold approval closure.

- [ ] **Step 1: Verify reviewer identities and approvals**

Do not continue unless GitHub shows `@Jye-rookie` completed the assigned review of the exact candidate and the designated approvers approved it with roles matching the artifact schemas. Record the real review event timestamp and transition every required provenance record from `DRAFT` to `REVIEWED` before applying approval.

- [ ] **Step 2: Promote provenance without altering Gold content**

For every required child, add the actual `@Jye-rookie` review timestamp and `EVALUATION_REVIEWER` identity with immutable review evidence, verify `authored_at < reviewed_at`, and record the `REVIEWED` transition. Then set each approved child to `team_gold_status=APPROVED` with its actual approver actor and approval timestamp, requiring `reviewed_at <= approved_at`. Set Dataset `status=FROZEN`, `frozen_at` to the freeze event, and Dataset approval to `DATASET_CUSTODIAN`. Recompute every affected canonical hash and downstream reference; do not change questions, Gold, allocation, or Leakage axes in this step.

- [ ] **Step 3: Prove freeze closure and determinism**

Run the complete Evaluation suite, validate the Dataset twice, compare all semantic hashes, and run the negative test that downgrades one child to `REVIEWED`.

- [ ] **Step 4: Request final Dataset Custodian review on the freeze commit**

The exact freeze commit must be approved before merge. If review requests content changes, return to `DRAFT`, publish a new reviewed candidate, and repeat this task.

- [ ] **Step 5: Record the #157 immutable handoff**

Post Dataset Manifest/resource-set, partition, Mapping, Rubric, Profile, Comparison Policy, Evaluation Policy, Suite, Schema Set, and Case-only receipt refs. State that #157 may implement against DEV but must not execute HOLDOUT until the independent Policy checkpoint is approved.
