# Issue #273 Phase 0 Provenance Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Issue #273의 자연어 Retrieval Dataset을 Freeze하기 전에 필요한 provenance 계약 3종과 additive Schema Set `1.3.0` 후보, Loader 검증, 비민감 진행 보고서를 구현한다.

**Architecture:** Schema Set `1.2.0`의 18개 member와 bytes를 변경하지 않고 `DatasetManifestV13` 및 신규 계약 3종만 추가한 21-member Schema Set `1.3.0`을 만든다. Dataset graph는 authoring identity manifest만 직접 결속하고, index build receipt와 study split receipt는 단계 C/D의 실행 입력에서 별도로 결속할 수 있도록 독립 parser 계약으로 제공한다. 작성자는 후보 상태만 기록하며 사람 승인과 Dataset Freeze를 선기록하지 않는다.

**Tech Stack:** Python 3.13, Pydantic v2 strict models, Draft 2020-12 JSON Schema, pytest, ruff, mypy

**Spec:** `docs/designs/ceohwj/issue-273-natural-language-retrieval-evaluation-design.md`

## Global Constraints

- 기존 `evals/schemas/1.0.0/`, `1.1.0/`, `1.2.0/`의 파일과 canonical hash를 변경하지 않는다.
- 신규 Schema Set 후보 version은 `1.3.0`, member 수는 정확히 21개다.
- 신규 member version은 각각 `1.0.0`이다.
- 신규 schema ID는 `rag-eval.authoring-identity-manifest`, `rag-eval.index-build-receipt`, `rag-eval.study-split-receipt`다.
- 실제 reviewer event 전에는 `APPROVED`, `FROZEN`, 외부 의료·Privacy 승인 상태를 기록하지 않는다.
- HOLDOUT 질문 본문, Gold, fingerprint 값, protected 경로는 일반 저장소에 추가하지 않는다.
- 새 안정 실패 code를 추가하지 않고 기존 `SCHEMA_INVALID`, `MANIFEST_INVALID`, `HASH_MISMATCH`, `RESOURCE_MISSING`을 사용한다.
- 신규 dependency를 추가하지 않는다.
- `.claude/`와 `skills-lock.json`은 사용자 소유 파일이므로 수정하거나 커밋하지 않는다.

---

### Task 1: 신규 provenance Pydantic 계약

**Files:**
- Create: `ai_worker/tasks/evaluation/schemas/provenance_v1.py`
- Create: `ai_worker/tasks/evaluation/schemas/authoring_v1_3.py`
- Create: `ai_worker/tests/evaluation/test_provenance_v1_schemas.py`
- Modify: `ai_worker/tasks/evaluation/schemas/__init__.py`

**Interfaces:**
- Consumes: `StrictContractModel`, `ImmutableReference`, `ResourceReference`, `ReviewProvenanceV12`, `Sha256Hex`, `StableId`, `SemanticVersion`, `UtcTimestamp`.
- Produces: `AuthoringIdentityManifest`, `IndexBuildReceipt`, `StudySplitReceipt`, `DatasetManifestV13`, `parse_authoring_identity_manifest_bytes`, `parse_index_build_receipt_bytes`, `parse_study_split_receipt_bytes`.

- [ ] **Step 1: Write failing contract tests**

  Add literal valid payloads and tests proving: unknown keys fail; schema ID/version mismatch fails; self hashes are checked by each parser; authoring entries are sorted and have unique `case_id`/`member_order`; four leakage IDs are present; index bridge entries are sorted and unique by `evidence_ref_id`/`evidence_key`/`knowledge_chunk_ref`; study split has distinct DEV/HOLDOUT refs, four exact leakage axes, positive comparison counts, and `intersection_count=0`; HOLDOUT content/fingerprint fields are not part of the public receipt model; `DatasetManifestV13` requires `authoring_identity_manifest_ref` while preserving V1.2 provenance rules.

- [ ] **Step 2: Verify RED**

  Run: `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_provenance_v1_schemas.py -q`

  Expected: collection failure because `provenance_v1` and `authoring_v1_3` do not exist.

- [ ] **Step 3: Implement the strict models and parsers**

  Use these public shapes:

  ```python
  class AuthoringIdentityEntry(StrictContractModel):
      member_order: int
      case_id: StableId
      question_template_id: StableId
      source_segment_id: StableId
      medication_family_id: StableId
      transform_origin_id: StableId
      question_template_spec: str
      source_snapshot_ref: ImmutableReference
      source_locator: str
      source_chunk_sha256: Sha256Hex
      medication_family_fixture_id: StableId
      base_intent_seed: StableId
      transform_spec: str

  class AuthoringIdentityManifest(StrictContractModel):
      schema_id: Literal["rag-eval.authoring-identity-manifest"]
      schema_version: Literal["1.0.0"]
      manifest_id: StableId
      manifest_version: SemanticVersion
      dataset_code: StableId
      dataset_version: SemanticVersion
      canonicalization_spec_version: SemanticVersion
      entries: tuple[AuthoringIdentityEntry, ...]
      manifest_sha256: Sha256Hex
  ```

  `IndexBuildReceipt` must bind `dataset_ref`, `evidence_mapping_ref`, `source_snapshot_ref`, `evidence_index_ref`, `build_config_ref`, `adapter_artifact_ref`, `canonicalization_spec_version`, ordered bridge entries, `built_at`, `built_by`, and `receipt_sha256`. `StudySplitReceipt` must bind DEV/HOLDOUT Dataset and authoring-manifest refs, common Index/config refs, the four axis summaries, `authorization_receipt_ref`, `recorded_at`, `recorded_by`, and `receipt_sha256`. Parsers accept bytes, reject duplicate JSON keys through the repository parser, validate Pydantic, and exact-match the self hash calculated with the hash field omitted.

- [ ] **Step 4: Verify GREEN**

  Run the Task 1 test file, then `ruff check`, `ruff format --check`, and `mypy` for the two new schema modules.

- [ ] **Step 5: Commit**

  Commit message: `✨ feat: Retrieval provenance 계약 모델 추가`

---

### Task 2: Additive Schema Set 1.3.0 후보와 계약 문서

**Files:**
- Modify: `ai_worker/tasks/evaluation/schema_registry.py`
- Modify: `ai_worker/tests/evaluation/test_schema_exports.py`
- Create: `evals/schemas/1.3.0/**/*.schema.json` through the canonical exporter
- Create: `docs/governance/decisions/2026-09-05-rag-evaluation-schema-set-1-3-candidate.md`
- Modify: `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`
- Modify: `docs/contracts/targets/post-mvp-1/README.md`
- Modify: `docs/contracts/README.md`
- Modify: `evals/README.md`

**Interfaces:**
- Consumes: Task 1 models and existing `SCHEMA_REGISTRY_V1_2`.
- Produces: `SCHEMA_REGISTRY_V1_3`, canonical 21-file export, immutable candidate Schema Set hash documented consistently.

- [ ] **Step 1: Write failing Schema Set tests**

  Tests require `SCHEMA_REGISTRIES["1.3.0"]`, exactly 21 unique paths/IDs, three new member versions `1.0.0`, `DatasetManifestV13` member version `1.3.0`, and byte-for-byte equality for every reused 1.2 member. Add a canonical export comparison against committed `evals/schemas/1.3.0/` and a documented-hash parity test covering the candidate Decision, RAG evaluation target contract, and `evals/README.md`.

- [ ] **Step 2: Verify RED**

  Run the new 1.3-focused tests. Expected failure: registry key `1.3.0` is absent.

- [ ] **Step 3: Implement registry and generate schemas**

  Add `SCHEMA_REGISTRY_V1_3` as the 1.2 registry with the Dataset Manifest entry replaced by `DatasetManifestV13@1.3.0` and three new entries:

  ```text
  authoring/rag-eval.authoring-identity-manifest.schema.json
  operational/rag-eval.index-build-receipt.schema.json
  operational/rag-eval.study-split-receipt.schema.json
  ```

  Generate with:

  `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run python -m ai_worker.tasks.evaluation.schema_exports --output evals/schemas/1.3.0 --schema-set-version 1.3.0`

- [ ] **Step 4: Record candidate governance without false approval**

  The new Decision document status is exactly `Candidate · Review Required`, names 정현우 as proposer/implementer and 권가빈 as responsible Product·Safety·Evaluation reviewer, and states that approval occurs only through the PR review event. Compute `_schema_set_hash(_SnapshotReader(Path("evals")), "1.3.0")` and place the emitted 64-hex value identically in all three authoritative documents. Preserve the documented 1.2 hash unchanged.

- [ ] **Step 5: Verify GREEN and compatibility**

  Run `test_schema_exports.py`, all existing schema export/authoring tests, and explicitly assert the committed 1.2 directory is unchanged with `git diff --exit-code origin/develop -- evals/schemas/1.0.0 evals/schemas/1.1.0 evals/schemas/1.2.0`.

- [ ] **Step 6: Commit**

  Commit message: `✨ feat: Evaluation Schema Set 1.3 후보 추가`

---

### Task 3: Loader graph에 authoring identity 결속

**Files:**
- Modify: `ai_worker/tasks/evaluation/loaders.py`
- Modify: `ai_worker/tasks/evaluation/errors.py` only if an existing code cannot represent the boundary; default is no change
- Create: `ai_worker/tests/evaluation/test_authoring_identity_loader.py`
- Modify: `ai_worker/tests/evaluation/test_loaders.py` only for shared fixture support

**Interfaces:**
- Consumes: `DatasetManifestV13.authoring_identity_manifest_ref` and `AuthoringIdentityManifest`.
- Produces: V1.3 `_AuthoringContract`, loaded identity binding in `ValidatedDataset`, exact Case/leakage/hash validation.

- [ ] **Step 1: Write failing Loader tests**

  Build one minimal V1.3 Dataset graph from literal fixtures. Prove valid load succeeds; missing sidecar is `RESOURCE_MISSING`; raw file hash/ref mismatch is `MANIFEST_INVALID`; self hash mismatch is `HASH_MISMATCH`; Dataset code/version mismatch, missing/extra/duplicate Case, and each of four leakage ID mismatches are `MANIFEST_INVALID`; V1.2 graph still loads with no identity sidecar.

- [ ] **Step 2: Verify RED**

  Run: `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_authoring_identity_loader.py -q`

  Expected: failure because Loader has no 1.3 authoring contract or identity resource.

- [ ] **Step 3: Implement minimal Loader support**

  Extend `_AuthoringContract` with an optional identity model, add V1.3 mapping, load `retrieval/manifests/<prefix>.authoring-identities.json`, check the Dataset manifest immutable ref against file bytes, check exact Dataset and Case set binding, and exact-match each entry's four IDs against `case.leakage_group_ids`. Add the sidecar to `graph_members`, `resource_hashes`, and `ValidatedDataset`; leave V1.0–V1.2 behavior unchanged.

- [ ] **Step 4: Verify GREEN**

  Run the focused loader tests, `test_loaders.py`, `test_schema_exports.py`, then ruff/mypy for changed evaluation modules.

- [ ] **Step 5: Commit**

  Commit message: `✨ feat: Authoring identity Loader 결속 추가`

---

### Task 4: Phase 0 진행 상태와 report.md projection

**Files:**
- Create: `ai_worker/tasks/evaluation/natural_language_retrieval_validation.py`
- Create: `ai_worker/tests/evaluation/test_natural_language_retrieval_validation_report.py`
- Create: `docs/validation/rag/issue-273/status.json`
- Create: `docs/validation/rag/issue-273/report.md`
- Modify: `docs/designs/ceohwj/issue-273-natural-language-retrieval-evaluation-design.md`

**Interfaces:**
- Consumes: Schema Set 1.3 candidate ID/version/hash and repository-relative non-sensitive status input.
- Produces: `Issue273ValidationStatus`, `parse_status_bytes`, `render_report`, deterministic committed report.

- [ ] **Step 1: Write failing report tests**

  Require strict unknown-key rejection, sorted unique blockers, `actual_run_ref=null` while Adapter is blocked, no Metric fields before a verified Run, canonical status hash, exact report bytes, and rejection of query/Evidence/provider/protected-path keys anywhere in the JSON.

- [ ] **Step 2: Verify RED**

  Run the focused test. Expected collection failure because the validation module is absent.

- [ ] **Step 3: Implement status model and renderer**

  Record `phase=PHASE_0_SCHEMA_CANDIDATE`, `schema_set_status=REVIEW_REQUIRED`, `dataset_status=NOT_CREATED`, `gold_review_status=NOT_STARTED`, `holdout_freeze_status=NOT_STARTED`, `adapter_status=NOT_IMPLEMENTED`, `actual_run_ref=null`, `release_eligible=false`, and blockers `BLOCKED_BY_EVAL_SCHEMA_EXTENSION`, `BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER`, `BLOCKED_BY_RAG_14_ADAPTER`, `WAITING_FOR_HOLDOUT_FREEZE`. The report must say that no DEV/HOLDOUT questions or actual metrics were created and that PR approval is still required.

- [ ] **Step 4: Update the design with the concrete candidate**

  Replace the design's unresolved Schema Set language with `rag-eval.schema-set@1.3.0` plus its computed candidate hash, link the candidate Decision, and retain the rule that this is not approved until the designated review event.

- [ ] **Step 5: Verify GREEN**

  Run the focused report test, all evaluation tests, ruff, format check, mypy, `git diff --check`, and inspect the full diff for protected data or false approval language.

- [ ] **Step 6: Commit**

  Commit message: `📝 docs: Issue 273 Phase 0 검증 보고서 추가`

---

## Final Verification

- [ ] Run `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation -q`.
- [ ] Run `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation`.
- [ ] Run `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check`.
- [ ] Run `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run mypy ai_worker/tasks/evaluation`.
- [ ] Run `git diff --check` and inspect `git diff origin/develop...HEAD`.
- [ ] Verify `evals/schemas/1.0.0`, `1.1.0`, and `1.2.0` are unchanged.
- [ ] Request a final code/spec/security and architecture review before claiming Phase 0 candidate completion.
