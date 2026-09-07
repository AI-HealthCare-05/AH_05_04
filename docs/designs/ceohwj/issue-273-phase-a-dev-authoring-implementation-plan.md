# Issue #273 Phase A DEV Authoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Schema Set `1.3.0`으로 검증되는 공개 가능한 자연어 Retrieval DEV Case 60개, Gold 20개와 hard negative 80개로 구성된 합성 corpus, 전체 authoring graph 및 비민감 Phase A report를 저장소에 추가한다.

**Architecture:** `natural_language_retrieval_dev_authoring.py`가 승인된 고정 catalog에서 모든 JSON artifact를 canonical byte로 생성하고, 저장소에는 생성 결과를 함께 추적한다. 전역 Loader 계약은 변경하지 않고 전용 fixture test가 수량·분포·Gold·negative·privacy·leakage·hash·HOLDOUT 부재를 검증한다. 모든 review provenance는 `DRAFT`, 실제 Adapter와 metric은 `NOT_IMPLEMENTED/null`로 유지한다.

**Tech Stack:** Python 3.13, Pydantic v2, canonical JSON/SHA-256 utilities, pytest, Ruff, Mypy

**Spec:** `docs/designs/ceohwj/issue-273-natural-language-retrieval-evaluation-design.md`

## Global Constraints

- Dataset identity는 `rag-natural-language-retrieval-dev@1.0.0`, manifest schema version은 `1.3.0`이다.
- Case ID는 `rag-nlr-dev-001`부터 `rag-nlr-dev-060`까지 연속적이다.
- 주제는 5개이며 각각 Case 12개다: medication information, precautions, lifestyle management, storage, missed dose.
- 표현 유형은 6개이며 각각 Case 10개다: canonical, synonym, word-order/particle, colloquial, fragment, limited typo.
- base intent와 `transform_origin`은 정확히 20개이며 각 group은 Case 3개다.
- corpus는 Gold `KNOWLEDGE_CHUNK` 20개와 base intent별 4종 hard negative 80개, 합계 100개다.
- 모든 질문과 Evidence는 명시적으로 작성한 합성 데이터이며 실제 환자 발화, 실제 제품명, 실제 Provider body를 사용하지 않는다.
- query에는 공통 `SYNTHETIC_QUERY_*` 표식이나 모든 Case에 반복되는 합성 표식을 넣지 않는다. 합성 여부는 metadata로만 표현한다.
- 제품 식별자는 `NLR-MI01`~`NLR-MD04`의 reserved fixture allowlist만 사용한다.
- OTC 제품 추천·처방약–OTC 상호작용 질문은 포함하지 않으며 Issue #278에서만 다룬다.
- `review_provenance.team_gold_status=DRAFT`; `reviewed_by`, `reviewed_at`, `approved_by`, `approved_at`과 evidence review refs는 비워 둔다.
- Dataset은 `DRAFT`, Profile은 `runtime_eligible=false`, actual Run과 metric은 생성하지 않는다.
- HOLDOUT Case·Gold·identity·fingerprint·protected path는 일반 저장소에 만들지 않는다.
- 신규 dependency와 전역 schema/error code 변경은 하지 않는다.
- `.claude/`와 `skills-lock.json`은 사용자 소유이므로 수정하거나 커밋하지 않는다.

## Fixed Authoring Matrix

각 주제의 네 base intent는 아래 식별자와 reserved product code를 사용한다.

| Topic | Origin | Product | Gold intent |
| --- | --- | --- | --- |
| `TOPIC_MEDICATION_INFORMATION` | `NLR-MI01` | `NLR-MI01` | 제품의 합성 성분 정보 |
| `TOPIC_MEDICATION_INFORMATION` | `NLR-MI02` | `NLR-MI02` | 제품의 합성 제형·외형 정보 |
| `TOPIC_MEDICATION_INFORMATION` | `NLR-MI03` | `NLR-MI03` | 제품의 합성 사용 목적 정보 |
| `TOPIC_MEDICATION_INFORMATION` | `NLR-MI04` | `NLR-MI04` | 제품 라벨의 합성 식별 정보 |
| `TOPIC_PRECAUTIONS` | `NLR-PC01` | `NLR-PC01` | 복용 전 확인할 합성 주의사항 |
| `TOPIC_PRECAUTIONS` | `NLR-PC02` | `NLR-PC02` | 합성 알레르기 경고 정보 |
| `TOPIC_PRECAUTIONS` | `NLR-PC03` | `NLR-PC03` | 합성 이상 반응 관찰 정보 |
| `TOPIC_PRECAUTIONS` | `NLR-PC04` | `NLR-PC04` | 전문가 확인이 필요한 합성 조건 |
| `TOPIC_LIFESTYLE_MANAGEMENT` | `NLR-LM01` | `NLR-LM01` | 합성 수분 섭취 안내 |
| `TOPIC_LIFESTYLE_MANAGEMENT` | `NLR-LM02` | `NLR-LM02` | 합성 식사 습관 안내 |
| `TOPIC_LIFESTYLE_MANAGEMENT` | `NLR-LM03` | `NLR-LM03` | 합성 활동 안내 |
| `TOPIC_LIFESTYLE_MANAGEMENT` | `NLR-LM04` | `NLR-LM04` | 합성 상태 기록 안내 |
| `TOPIC_STORAGE` | `NLR-ST01` | `NLR-ST01` | 합성 보관 온도 정보 |
| `TOPIC_STORAGE` | `NLR-ST02` | `NLR-ST02` | 합성 빛·습기 차단 정보 |
| `TOPIC_STORAGE` | `NLR-ST03` | `NLR-ST03` | 합성 안전 보관 위치 정보 |
| `TOPIC_STORAGE` | `NLR-ST04` | `NLR-ST04` | 합성 원래 용기 보관 정보 |
| `TOPIC_MISSED_DOSE` | `NLR-MD01` | `NLR-MD01` | 누락을 일찍 알았을 때의 합성 안내 |
| `TOPIC_MISSED_DOSE` | `NLR-MD02` | `NLR-MD02` | 다음 시각이 가까울 때의 합성 안내 |
| `TOPIC_MISSED_DOSE` | `NLR-MD03` | `NLR-MD03` | 합성 중복 복용 금지 안내 |
| `TOPIC_MISSED_DOSE` | `NLR-MD04` | `NLR-MD04` | 반복 누락 시 전문가 상담 안내 |

각 주제의 네 origin에는 다음 표현 조합을 순서대로 적용한다. 이 패턴을 다섯 주제에 동일하게 적용하면 주제별 각 표현 2개, 전체 각 표현 10개가 된다.

```python
EXPRESSION_PLAN = (
    ("EXPRESSION_CANONICAL", "EXPRESSION_SYNONYM", "EXPRESSION_COLLOQUIAL"),
    ("EXPRESSION_WORD_ORDER_PARTICLE", "EXPRESSION_FRAGMENT", "EXPRESSION_LIMITED_TYPO"),
    ("EXPRESSION_CANONICAL", "EXPRESSION_WORD_ORDER_PARTICLE", "EXPRESSION_COLLOQUIAL"),
    ("EXPRESSION_SYNONYM", "EXPRESSION_FRAGMENT", "EXPRESSION_LIMITED_TYPO"),
)
```

hard negative type은 각 origin마다 다음 네 개를 정확히 하나씩 생성한다.

```python
NEGATIVE_TYPES = (
    "SAME_FAMILY_DIFFERENT_ATTRIBUTE",
    "SAME_TOPIC_DIFFERENT_FAMILY",
    "LEXICAL_OVERLAP_UNSUPPORTED",
    "CROSS_TOPIC_OVERLAP",
)
```

---

### Task 1: Deterministic authoring catalog와 생성기 경계

**Files:**
- Create: `ai_worker/tasks/evaluation/natural_language_retrieval_dev_authoring.py`
- Create: `ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py`

**Interfaces:**
- Consumes: `canonical_json_bytes`, `canonical_sha256`, `sha256_hex`, Schema Set `1.3.0` models and existing authoring graph conventions.
- Produces: `build_issue_273_dev_graph() -> dict[str, bytes]`, `write_issue_273_dev_graph(evals_root: Path) -> None`, immutable 20-intent catalog and reserved product allowlist.

- [ ] **Step 1: Write the failing identity and distribution test**

  Add a test importing `build_issue_273_dev_graph` and assert that the returned path map contains 60 Case paths, one knowledge-index resource, Evidence Mapping, rubric, authoring sidecar, Dataset manifest, Profile, two Policy files, Suite and protected artifact receipt. Assert contiguous Case IDs, five topics × 12, six expressions × 10, 20 transform origins × 3 and the exact reserved product allowlist.

- [ ] **Step 2: Verify RED**

  Run: `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py -q`

  Expected: collection failure because `natural_language_retrieval_dev_authoring` does not exist.

- [ ] **Step 3: Implement the typed catalog and path-only graph skeleton**

  Define frozen `BaseIntent`, `QuestionVariant` and `EvidenceRecord` dataclasses. Keep the 20 rows in `BASE_INTENTS`, enforce `EXPRESSION_PLAN`, and expose `RESERVED_PRODUCT_CODES`. `build_issue_273_dev_graph()` initially returns canonical Case payload bytes plus the graph-member paths required by later tasks; no filesystem write occurs in this function.

- [ ] **Step 4: Verify GREEN**

  Run the focused test and confirm the exact distribution passes.

- [ ] **Step 5: Commit**

  Commit: `✨ feat: Issue 273 DEV authoring catalog 추가`

### Task 2: Gold 20개와 hard negative 80개

**Files:**
- Modify: `ai_worker/tasks/evaluation/natural_language_retrieval_dev_authoring.py`
- Modify: `ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py`
- Generate: `evals/retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json`
- Generate: `evals/retrieval/evidence/rag-natural-language-retrieval-dev-v1.evidence-mapping.json`

**Interfaces:**
- Consumes: Task 1 `BASE_INTENTS` and Case identifiers.
- Produces: 100 unique index records and 20 Evidence Mapping entries referenced by all Cases.

- [ ] **Step 1: Write failing Gold/corpus tests**

  Assert exactly 20 records have `record_kind=GOLD`, exactly 80 have `record_kind=HARD_NEGATIVE`, each origin owns one Gold and four distinct negative types, Gold and negative IDs/hashes are disjoint, every negative has `adversarial_for_transform_origin`, and all Case required/relevant refs resolve to the single Gold for their origin.

- [ ] **Step 2: Verify RED**

  Expected: missing record metadata and Evidence Mapping failures.

- [ ] **Step 3: Implement corpus and mapping generation**

  Generate IDs `ev-nlr-<origin-lower>-gold` and `ev-nlr-<origin-lower>-neg-<01..04>`. Use natural Korean synthetic statements derived from the fixed intent text, with same-family and lexical-overlap negatives deliberately sharing the product code but never the target attribute. Evidence Mapping entries use `KNOWLEDGE_CHUNK`, fixed locator `$.records[<gold-index>]`, file byte SHA-256, stable key, source version and fixture reference.

- [ ] **Step 4: Write canonical artifacts and verify GREEN**

  Call `write_issue_273_dev_graph(Path("evals"))`, then run the focused test. Generated bytes must equal a fresh in-memory build.

- [ ] **Step 5: Commit**

  Commit: `✨ feat: Issue 273 합성 Gold corpus 추가`

### Task 3: Schema Set 1.3 Dataset authoring graph

**Files:**
- Modify: `ai_worker/tasks/evaluation/natural_language_retrieval_dev_authoring.py`
- Modify: `ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py`
- Generate: `evals/retrieval/cases/rag-natural-language-retrieval-dev-v1/*.json`
- Generate: `evals/retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json`
- Generate: `evals/retrieval/manifests/rag-natural-language-retrieval-dev-v1.critical-claim-rubric.json`
- Generate: `evals/retrieval/manifests/rag-natural-language-retrieval-dev-v1.authoring-identities.json`
- Generate: `evals/profiles/rag-natural-language-retrieval-dev-v1.profile.json`
- Generate: `evals/policies/rag-natural-language-retrieval-dev-v1.comparison-policy.json`
- Generate: `evals/policies/rag-natural-language-retrieval-dev-v1.evaluation-policy.json`
- Generate: `evals/suites/rag-natural-language-retrieval-dev-v1.suite.json`
- Generate: `evals/provenance/rag-natural-language-retrieval-dev-v1.protected-artifact-receipt.json`

**Interfaces:**
- Consumes: Task 2 corpus/mapping and `load_dataset()`.
- Produces: a complete DRAFT `DatasetManifestV13` graph that Loader accepts.

- [ ] **Step 1: Write failing graph validation tests**

  Assert `load_dataset()` succeeds, every Case is `RETRIEVAL/SYNTHETIC/DEV/DRAFT`, required equals relevant and has one Gold, query is a Korean natural sentence rather than a synthetic token, `slice_ids` is sorted `ALL + Topic + Expression`, authoring identity source provenance resolves to the Case Gold resource, all graph self/file/resource hashes match and no HOLDOUT path exists.

- [ ] **Step 2: Verify RED**

  Expected: graph members and cross-hashes are absent.

- [ ] **Step 3: Generate the complete graph in dependency order**

  Build corpus → Evidence Mapping → rubric → Cases → authoring sidecar → Profile/Comparison Policy/Suite → Evaluation Policy → protected receipt → Dataset manifest. Recompute every hash from canonical bytes. Use a `SYSTEM_VALIDATOR` only where the existing Comparison Policy schema requires an approver; do not record a human review event. Keep `fixture_git_commit_sha=null`, `frozen_at=null`, `runtime_eligible=false`, and Dataset status `DRAFT`.

- [ ] **Step 4: Verify privacy, leakage and deterministic regeneration**

  Add negative assertions for email/phone/API-key sentinels, actual product denylist, duplicate query, duplicate Case ID, wrong distribution, missing Gold, negative-as-Gold, authoring source mismatch and any repository HOLDOUT resource. Assert two in-memory builds produce identical path maps and bytes.

- [ ] **Step 5: Verify GREEN**

  Run the focused fixture test and existing Loader/schema export tests.

- [ ] **Step 6: Commit**

  Commit: `✨ feat: Issue 273 DEV Dataset graph 추가`

### Task 4: Phase A status와 report projection

**Files:**
- Modify: `ai_worker/tasks/evaluation/natural_language_retrieval_validation.py`
- Modify: `ai_worker/tests/evaluation/test_natural_language_retrieval_validation_report.py`
- Modify: `docs/validation/rag/issue-273/status.json`
- Modify: `docs/validation/rag/issue-273/report.md`
- Modify: `evals/README.md`

**Interfaces:**
- Consumes: verified Dataset graph identity and exact check results.
- Produces: strict Phase A status and deterministic human-readable report.

- [ ] **Step 1: Write failing Phase A status tests**

  Require `phase=PHASE_A_DEV_AUTHORING`, Dataset `DRAFT`, created DEV 60, Gold 20, corpus 100, topics 5, expressions 6, independent groups 20, Gold review `NOT_STARTED`, HOLDOUT `NOT_STARTED`, Adapter `NOT_IMPLEMENTED`, `actual_run_ref=null`, no metric fields and blockers limited to protected runner, actual Adapter and HOLDOUT Freeze.

- [ ] **Step 2: Verify RED**

  Expected: current Phase 0 literal status model rejects Phase A.

- [ ] **Step 3: Implement the Phase A model transition and regenerate report**

  Update the strict literals/catalog, compute the canonical status self-hash, and generate `report.md` only through `render_report()`. The report states that DEV authoring exists but is unreviewed, actual retrieval was not run, no baseline metric exists and DEV cannot produce a Release PASS.

- [ ] **Step 4: Verify exact projection and evidence counts**

  Run the fixture, report, Loader and schema export test commands; store only exact successful command/result pairs in status.

- [ ] **Step 5: Commit**

  Commit: `📝 docs: Issue 273 Phase A 검증 보고서 갱신`

### Task 5: Final verification and review readiness

**Files:**
- Verify all files changed by Tasks 1–4.

**Interfaces:**
- Consumes: complete Phase A diff.
- Produces: review-ready branch with no false approval or protected content.

- [ ] **Step 1: Run focused and full checks**

  ```bash
  UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py -q
  UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation -q
  UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
  UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
  UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run mypy ai_worker/tasks/evaluation
  git diff --check
  ```

- [ ] **Step 2: Inspect scope and safety**

  Confirm there are no HOLDOUT contents, actual metrics, real product names, patient data, credentials, invented human review identities or unrelated changes. Confirm Schema Set `1.0.0`–`1.3.0` canonical bytes are unchanged.

- [ ] **Step 3: Independent final review**

  Review contract consistency, medical/privacy safety, hash reproducibility and test adequacy. Address every HIGH/MEDIUM finding and repeat verification.

- [ ] **Step 4: Prepare the PR**

  Use the repository PR template, link Issue #273, name 정현우 as implementation owner and 권가빈 (`@hazelnutflavoured`) as responsible Product·Safety·Evaluation reviewer. State explicitly that Gold review, Dataset Freeze, actual Adapter execution, metrics and HOLDOUT remain future work.

