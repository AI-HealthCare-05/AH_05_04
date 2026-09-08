# Issue #273 HOLDOUT Freeze Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HOLDOUT 콘텐츠를 공개하지 않으면서 Issue #273의 접근 통제·역할 분리·Freeze 선행조건을 결정적 준비 패킷과 Phase B2 상태로 고정한다.

**Architecture:** Issue-local generator가 승인된 DEV Dataset manifest를 입력으로 비런타임 JSON/Markdown 준비 패킷을 생성한다. 패킷은 실제 승인·작성·Freeze를 모두 `false`로 유지하고, 보호 환경에서 나중에 충족할 계약과 공개 가능한 receipt envelope만 기술한다. 기존 Schema Set 1.3과 `StudySplitReceipt`는 변경하지 않는다.

**Tech Stack:** Python 3.13, Pydantic v2, canonical JSON/SHA-256 utilities, pytest, Ruff, Mypy

**Spec:** `docs/designs/ceohwj/issue-273-holdout-freeze-preparation-design.md`

## Global Constraints

- 브랜치는 `273-holdout-freeze-preparation`이며 `origin/develop@e28e7f0c92801692635c4cca338b9485d47fa649`에서 시작한다.
- 현재 Dataset은 `rag-natural-language-retrieval-dev@1.0.0`, `DRAFT`, unfrozen이다.
- 승인된 DEV Dataset manifest SHA-256은 `b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2`다.
- HOLDOUT은 계획 40개, 생성 0개를 유지하며 5개 주제별 계획은 정확히 8개다.
- 공개 artifact에 HOLDOUT 질문·Gold·negative label·fingerprint/HMAC 값·키·credential·보호 위치를 기록하지 않는다.
- 구현 담당은 `@ceohwj`, Product/Evaluation 검토는 `@hazelnutflavoured`, 요청 Custodian은 `@phina-io`다.
- `@phina-io`가 접근 통제를 구현하면 독립 승인자는 `@Jye-rookie`다.
- 실제 Adapter, Runner, Run, Metric, Baseline, Release 판정과 OTC #278은 범위 밖이다.
- 신규 dependency와 공유 schema·Loader·error code 변경은 하지 않는다.

---

### Task 1: 결정적 HOLDOUT Freeze 준비 패킷

**Files:**
- Create: `ai_worker/tasks/evaluation/natural_language_retrieval_holdout_preparation.py`
- Create: `ai_worker/tests/evaluation/test_natural_language_retrieval_holdout_preparation.py`
- Generate: `docs/validation/rag/issue-273/holdout-freeze-preparation.json`
- Generate: `docs/validation/rag/issue-273/holdout-freeze-preparation.md`

**Interfaces:**
- Consumes: `build_issue_273_dev_graph(dataset_approved=True)`, `DATASET_MANIFEST_PATH`, `canonical_json_bytes`, `canonical_sha256`, `sha256_hex`.
- Produces: `build_holdout_freeze_preparation(evals_root: Path) -> dict[str, JsonValue]`, `render_holdout_freeze_preparation_markdown(packet: dict[str, JsonValue]) -> str`, `write_holdout_freeze_preparation(repository_root: Path) -> None`.

- [ ] **Step 1: Write the failing preparation contract test**

  Assert exact literals for `purpose=PREPARATION_ONLY`, `preparation_status=PREPARATION_READY`,
  `protected_runner_issue_status=NOT_CREATED`,
  `access_control_start_gate=[PROTECTED_RETRIEVAL_RUNNER_ISSUE_CREATED]`, Dataset ref/hash, 40 planned and
  0 authored HOLDOUT questions, five literal topic counts of 8, four ordered leakage axes, role identities,
  `access_authorized=false`, `freeze_recorded=false`, `actual_run_ref=null`, and a valid self-hash.

- [ ] **Step 2: Verify RED**

  Run:

  ```bash
  UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_holdout_preparation.py -q
  ```

  Expected: collection fails because `natural_language_retrieval_holdout_preparation` does not exist.

- [ ] **Step 3: Implement the minimal packet builder**

  Add the three public interfaces. Read and validate the approved DEV manifest, require `status=DRAFT`, `frozen_at=null`, `partition_counts={DEV: 60, HOLDOUT: 0}`, exact manifest self-hash and approved Dataset provenance. Construct only public policy metadata and compute `preparation_sha256` excluding itself.

- [ ] **Step 4: Add fail-closed privacy and state tests**

  For independent mutations, assert rejection of a non-zero HOLDOUT count, changed manifest hash, duplicated leakage axis, merged role identities, prefilled access approval, prefilled Freeze and any recursive key containing `query`, `gold_body`, `record_label`, `fingerprint_value`, `hmac_value`, `key_material`, `credential`, or `protected_path`.

- [ ] **Step 5: Implement validation and deterministic Markdown projection**

  Validate fixed counts/order/roles before returning. Render responsibilities, access-control checklist, exact authoring and Freeze start gates, allowed future public receipt fields and all remaining blockers without exposing protected values.

- [ ] **Step 6: Verify GREEN and write generated artifacts**

  Run the focused test, call `write_holdout_freeze_preparation(Path.cwd())`, then assert committed JSON/Markdown bytes equal a fresh build and render.

### Task 2: Phase B2 machine status and report projection

**Files:**
- Modify: `ai_worker/tasks/evaluation/natural_language_retrieval_validation.py`
- Modify: `ai_worker/tests/evaluation/test_natural_language_retrieval_validation_report.py`
- Modify: `docs/validation/rag/issue-273/status.json`
- Modify: `docs/validation/rag/issue-273/report.md`
- Modify: `evals/README.md`

**Interfaces:**
- Consumes: Task 1 preparation artifact raw SHA-256.
- Produces: strict `Issue273ValidationStatus` Phase B2 parser and `render_report(raw_status: bytes) -> bytes` projection.

- [ ] **Step 1: Write failing Phase B2 status tests**

  Require `schema_version=1.2.0`, `phase=PHASE_B2_HOLDOUT_FREEZE_PREPARATION`, `status_label=Phase B2 · HOLDOUT Freeze Preparation Ready`, preparation raw/self hashes, `holdout_preparation_status=PREPARATION_READY`, `holdout_freeze_status=NOT_STARTED`, and exactly four UTF-16-sorted blockers including `WAITING_FOR_HOLDOUT_ACCESS_AUTHORIZATION`.

- [ ] **Step 2: Verify RED**

  Run the report test module. Expected: current Phase B literals reject the Phase B2 payload.

- [ ] **Step 3: Implement the minimal Phase B2 transition**

  Extend the validation check catalog with the focused preparation test, add the preparation reference model, replace Phase B phase/status literals, and keep Dataset `DRAFT`, created HOLDOUT `0`, Adapter `NOT_IMPLEMENTED`, `actual_run_ref=null`, and `release_eligible=false`.

- [ ] **Step 4: Update the report projection and README**

  Render the preparation artifact ref, distinguish `PREPARATION_READY` from authorization/Freeze, state the exact next action order, and retain #178/#278 and Production boundaries. Update the Issue #273 README section with the same current-state distinction.

- [ ] **Step 5: Regenerate status and report**

  Run all catalog commands, record their exact successful results, recompute `status_sha256`, and write `report.md` only from `render_report()`.

- [ ] **Step 6: Verify GREEN**

  Run the focused preparation and report tests together and confirm exact deterministic projections.

### Task 3: Integrated verification and final review

**Files:**
- Verify all files changed by Tasks 1–2 and the two design documents.

**Interfaces:**
- Consumes: complete Phase B2 diff.
- Produces: review-ready branch that cannot be mistaken for authorization, Freeze, execution, or Release evidence.

- [ ] **Step 1: Run focused checks**

  ```bash
  UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_holdout_preparation.py ai_worker/tests/evaluation/test_natural_language_retrieval_validation_report.py -q
  ```

- [ ] **Step 2: Run the evaluation suite and static checks**

  ```bash
  UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation -q
  UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
  UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
  UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run mypy ai_worker/tasks/evaluation
  git diff --check
  ```

- [ ] **Step 3: Review security and state mutations**

  Confirm no HOLDOUT content/path/key/digest entered the repository, no shared contract changed, no approval or Freeze was prefilled, and all downstream execution/Release blockers remain.

- [ ] **Step 4: Review the complete diff**

  Inspect `git diff origin/develop...HEAD` plus uncommitted changes, correct any actionable finding, rerun affected checks, and record remaining risks without claiming completion beyond `PREPARATION_READY`.
