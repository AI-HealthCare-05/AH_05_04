# Issue #216 RAG Evaluation Schema Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver backward-compatible RAG Evaluation Schema Set `1.1.0`, typed Rule/fault fixtures, fail-closed frozen Gold closure, and an immutable Schema Set reference for `#214`.

**Architecture:** Preserve all 1.0 models and bytes. Add separate 1.1 authoring models, a versioned complete schema registry with per-member versions, manifest-driven loader dispatch, and a cross-resource frozen approval check.

**Tech Stack:** Python 3.13, Pydantic v2, JSON Schema Draft 2020-12, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-02-issue-216-rag-evaluation-schema-compatibility-design.md`

## Global Constraints

- Do not change any committed byte under `evals/schemas/1.0.0`.
- Do not add Dataset cases or implement `#214`/`#157` work.
- Keep OTC identity-insufficient data in upstream Contract Receipts, outside `rag-eval.case`.
- Write each behavioral test first and observe the intended failure before production changes.
- Use literal expected values and real model/loader/export behavior; no source-text assertions.

---

### Task 1: Lock the 1.1 authoring behavior with failing model tests

**Files:**

- Create: `ai_worker/tests/evaluation/test_authoring_v1_1_schemas.py`
- Create: `ai_worker/tasks/evaluation/schemas/authoring_v1_1.py`

**Steps:**

1. Add literal Case fixtures derived from the existing Safety fixture and tests for `MATCHED_RULES`, `NO_MATCH`, and `NOT_INVOKED`.
2. Add negative tests for wrong Rule ID cardinality, absent/mismatched not-invoked reason, contradictory Source/Bundle eligibility, and identity status other than `MATCHED`.
3. Run `uv run pytest ai_worker/tests/evaluation/test_authoring_v1_1_schemas.py -q` and confirm failure because the 1.1 module/contracts do not exist.
4. Implement enums, `RuntimeFixtureV1_1`, explicit 1.1 expected shapes, Case models/adaptor, and `DatasetManifestV1_1` with the smallest validators satisfying the tests.
5. Re-run the file and commit the green model slice.

### Task 2: Version registry and canonical schema exports

**Files:**

- Modify: `ai_worker/tasks/evaluation/schema_registry.py`
- Modify: `ai_worker/tasks/evaluation/schema_exports.py`
- Modify: `ai_worker/tests/evaluation/test_schema_exports.py`
- Modify: `ai_worker/tests/evaluation/test_external_schema_parity.py`
- Create: `evals/schemas/1.1.0/**/*.json`

**Steps:**

1. Add failing tests that request Schema Set `1.1.0`, assert exactly 18 unique members, assert Case/Dataset member versions are 1.1, and assert reused member bytes and `$id` values remain 1.0.
2. Add failing JSON Schema validation tests for Rule outcome cardinality and typed reason consistency.
3. Run the narrow export/parity tests and record the expected missing-version failures.
4. Add `member_version` to registry entries, immutable registries keyed by Schema Set version, and explicit version lookup. Preserve `SCHEMA_REGISTRY` as the 1.0 alias.
5. Add version parameters to export APIs/CLI, emit `$id` from each member version, and extend conditional schema post-processing for the 1.1 Case contract.
6. Export `evals/schemas/1.1.0`, run the tests, and verify `evals/schemas/1.0.0` remains byte-identical to a fresh 1.0 export.
7. Commit the green schema/export slice.

### Task 3: Add manifest-driven loader compatibility and frozen closure

**Files:**

- Modify: `ai_worker/tasks/evaluation/loaders.py`
- Modify: `ai_worker/tests/evaluation/test_loaders.py`
- Modify: `ai_worker/tests/evaluation/test_foundation_fixture.py`

**Steps:**

1. Add a temporary-directory 1.1 dataset fixture assembled from real canonical resources, recalculating literal resource and self hashes only in test setup utilities.
2. Add failing tests proving manifest/Case version dispatch, policy-selected Schema Set `1.1.0`, unknown-version rejection, and successful no-match/not-invoked loads without fake Rule IDs.
3. Add parameterized failing tests showing `FROZEN` rejects a `REVIEWED` Case, Evidence Mapping, or Rubric with `REVIEW_PROVENANCE_INVALID`.
4. Run the narrow loader tests and observe each new branch fail against the 1.0-only loader.
5. Introduce a typed authoring-contract selector, version-aware Case/model validation, version-aware Schema Set hash verification, and graph registration.
6. Add the 1.1-only frozen child approval closure after child loading. Do not change the 1.0 DEV behavior.
7. Re-run loader and foundation fixture tests and commit the green loader slice.

### Task 4: Freeze the contract documentation and immutable Schema Set reference

**Files:**

- Create: `docs/governance/decisions/2026-09-02-rag-evaluation-schema-set-1-1-freeze.md`
- Modify: `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`
- Modify: `docs/contracts/README.md`
- Modify: `evals/README.md`

**Steps:**

1. Compute the canonical 1.1 Schema Set hash with the production loader/hash algorithm after committed export generation.
2. Record the member-version model, Rule semantics, typed fixture values, frozen closure, OTC ownership boundary, and exact immutable `rag-eval.schema-set@1.1.0` hash in the Decision and target contract.
3. Update the contract index and Evaluation README with status, location, generation command, and downstream `#214` reference.
4. Validate document links and run `git diff --check`.
5. Commit the documentation freeze slice.

### Task 5: Integrated verification and delivery

**Files:** all changed files

**Steps:**

1. Run `uv run pytest ai_worker/tests/evaluation -q`.
2. Run `uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation`.
3. Run `uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check`.
4. Run `uv run mypy ai_worker/tasks/evaluation`.
5. Run a fresh export comparison for both Schema Sets and verify no 1.0 byte changed.
6. Run `git diff --check`, inspect the complete diff, and scan for secrets, patient data, placeholders, and scope drift.
7. Commit any verification-only corrections, push `feat/216-rag-eval-schema-compat`, and report the immutable Schema Set reference and exact checks.

