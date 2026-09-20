# Product Decision: RAG Answer Quality Metric·Variant 계약

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-159-20260913` |
| 상태 | Approved |
| 제안일 | 2026-09-13 |
| 제안자·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Product·Safety·Evaluation `APPROVED` |
| 추적 Issue | [#159](https://github.com/AI-HealthCare-05/AH_05_04/issues/159) |
| 적용 범위 | Post-MVP-1 Track F Answer Quality DEV metric, variant, comparison 입력 계약 |
| 승인 Evidence | [PR #475 review `5190544119`](https://github.com/AI-HealthCare-05/AH_05_04/pull/475#pullrequestreview-5190544119) · [`decision-approval-evidence.json`](../../validation/rag/issue-159/decision-approval-evidence.json) |

## 결정

[#159 Answer Quality 계약](../../contracts/targets/post-mvp-1/rag-answer-quality-metrics-v1.md)은
RAG-EVAL-004의 Approved Target으로 다음 경계를 함께 고정한다.

1. Answer 전용 Variant는 `ANS-BASE | ANS-RAG | ANS-FINAL`이다.
2. `REQUIRED_CLAIM_RECALL`과 `COMPLETENESS`는 구조화된 Gold/Actual ID를 직접 계산한다.
3. `ANSWER_CORRECTNESS`와 `RELEVANCE`는 승인된 immutable human-rubric label을 집계하며 scorer가
   자연어 의미를 추정하지 않는다.
4. 각 Metric은 고유한 분모·분석 단위를 `unit_of_analysis`에 기록하고, point estimate는 micro ratio로
   계산한다. 이 값은 CI 재표집 단위를 나타내지 않는다.
5. 95% CI는 `cluster_dimension`으로 Case를 묶은 승인 Policy의 distinct 독립 group을 fixed seed로
   cluster bootstrap하고 매 표본에서 포함된 모든 Case의 분자·분모 기여값을 다시 합산한다.
6. 세 Variant는 기존 2-run `rag-eval.comparison` schema를 재사용한 세 개의 독립 pair로 비교하고,
   pair별 경로·hash·허용 delta를 별도 comparison-set manifest에 exact-set으로 결속한다.

PR #475 최종 HEAD에서 책임 리뷰어의 실제 Pull Request `APPROVED` event가 기록되었다. 이 승인은 아래 DEV
구현을 허용하지만 Runtime 통합, HOLDOUT 실행, 활성 Release `PASS` 또는 공개를 승인하지 않는다.

## 현재 계약과의 차이

현재 승인된 `dev-foundation-v1.comparison-policy`에는 `SYNTHETIC_VALIDATION`만 있고 네 Answer Metric의
formula·분석 단위·CI signature가 없다. `rag-eval.case-result`는 Answer의 `actual_claim_ids`,
`actual_sections`, `omitted_sections`를 보존하지만 claim correctness 또는 answer relevance의 승인 label은
보존하지 않는다.

현재 `DevVariant.variant_id`는 Retrieval과 Answer 모두에 쓰는 자유로운 stable ID다. 이를 전역 enum으로
좁히면 기존 Retrieval 계약을 불필요하게 변경하므로, 후보 계약은 Answer 전용 config/schema 경계만
제한한다.

현재 comparison artifact schema는 일반적인 baseline/candidate 두 Run을 표현할 수 있지만,
`comparison.py`의 검증·builder는 Retrieval 전용이다. #159 구현은 schema를 3-way 구조로 변경하지 않고
Answer 전용 pair 검증·builder와 pair 전용 외부 bundle manifest를 추가해야 한다.

## 승인 시 구현 경계

승인 뒤 Phase B는 합성 DEV에서 다음만 구현한다.

- Answer 전용 Variant config validation
- 승인된 judgment artifact의 fail-closed loader
- 네 Metric의 deterministic aggregation, 분자·분모, slice, 독립 group, 95% CI
- 필수 Case·judgment 누락, 분모 0, 표본·독립 group 부족 상태
- Critical Claim Rubric hash 불일치 시 Adapter 호출과 Run Artifact 생성 전 거부

RAG-15/16의 versioned Generator·Prompt·Model·Validator Artifact가 확보되기 전에는 실제 세 Variant 실행,
Baseline Freeze, HOLDOUT 결과 관찰, Runtime 통합과 #159 Close를 차단한다.

## 상태와 승격

이 Decision과 연결 계약은 PR #475 승인으로 `Approved/Approved Target`이 되었다. 승인 증거는 후속 상태
정합 변경에서 기록하며, 구현·schema export·자동 테스트가 없는 문서 승인만으로 `current/`로 승격하지
않는다.

### 승인 Evidence

| 리뷰어 | 상태 | Review ID | Submitted at (UTC) | 대상 commit OID |
| --- | --- | --- | --- | --- |
| 권가빈 (`@hazelnutflavoured`) | `APPROVED` | [`5190544119`](https://github.com/AI-HealthCare-05/AH_05_04/pull/475#pullrequestreview-5190544119) | `2026-09-13T11:26:42Z` | `ca27931ff54b3a48feb07e3b4a766620bfc34614` |

PR #475의 final HEAD는 위 commit이고 승인 뒤 추가 commit 없이 `2026-09-13T11:27:21Z`에 병합되었다.
required check `test`·`lint`·`frontend`와 그 하위 test lane은 모두 성공했다.

## 2026-09-18 후속 결정: ANSWER_CORRECTNESS Zero-Claim Bootstrap 의미와 의도적 CI Divergence

Issue #159 구현 중 발견된 zero-claim Case 및 zero-denominator bootstrap replicate 처리와 관련하여, Product·Safety·Evaluation 승인자 권가빈 (`@hazelnutflavoured`)의 결정([#159 comment 5723932925](https://github.com/AI-HealthCare-05/AH_05_04/issues/159#issuecomment-5723932925), [#159 comment 5724008612](https://github.com/AI-HealthCare-05/AH_05_04/issues/159#issuecomment-5724008612), 회신 [#159 comment 5724038459](https://github.com/AI-HealthCare-05/AH_05_04/issues/159#issuecomment-5724038459))에 따라 다음 후속 계약을 확정한다.

### 1. 확정 의미

1. `ANSWER_CORRECTNESS`는 CLAIM 단위 `MICRO_RATIO` 정의를 유지한다.
2. zero-claim Case는 분자 0, 분모 0의 `RatioContribution(0, 0)`으로 유지하며, `0/1` 등 Case-level penalty로 치환하지 않는다. 필수 claim 누락은 `REQUIRED_CLAIM_RECALL`에서 단독 측정한다.
3. CI sampling frame에는 zero-denominator cluster도 포함한다. cluster를 사전 제외하지 않는다.
4. bootstrap replicate의 denominator 합이 0인 replicate만 제외(skip)하고, 나머지 valid replicate들로 percentile CI를 산출한다.
5. scope 전체 denominator가 0이면 `COMPLETED / INCONCLUSIVE / ZERO_DENOMINATOR`로 판정한다.
6. valid replicate 비율이 `ComparisonScope.ci_parameters`의 `minimum_valid_replicate_ratio`(초기값 `"0.9"`) 미만이면 `COMPLETED / INCONCLUSIVE`로 판정하고 reason code는 `MINIMUM_VALID_BOOTSTRAP_REPLICATE_RATIO_NOT_MET`를 기록한다. 단, 계산된 `metric_value` 및 유효 replicate가 있을 때의 CI bounds는 보존한다.
7. valid / excluded replicate count와 ratio는 machine `MetricResult`나 Schema Set 1.5에 추가하지 않고 non-schema diagnostic / `report.md` projection 계층에만 기록한다. diagnostic projection capability는 구현되나 실제 Run report emission은 authoritative human-judgment runtime wiring 완료 시점까지 유예된다. 이 값은 Release Gate나 자동화 판정의 입력이 아니다.

### 2. 의도적 CI Divergence

지표별 CI sampling frame 및 zero-denominator 처리 규칙은 다음과 같이 분기하며, 이는 기존 지표 의미 보존을 위한 **의도적 divergence(Intentional Divergence)**이다:

- `ANSWER_CORRECTNESS`: zero-denominator cluster를 sampling frame에 유지하고 replicate 단위로 denominator 0을 제외한다.
- `GROUNDING` / `SAFETY`: 기존처럼 helper 호출 전 zero-denominator cluster를 `active_grouped`에서 제외하는 방식을 유지한다. 이번 변경으로 production 코드를 수정하지 않는다.
- `Retrieval`: 해당 ratio bootstrap helper 경로를 사용하지 않는다.

이 차이로 인한 정렬 여부는 향후 별도 후속 이슈에서 다루며, 이번 DEV kernel 범위에서 임의로 단일화하지 않는다.

### 3. 후속 결정 Evidence

| 결정자 | 상태 | 근거 | 일시 (UTC) |
| --- | --- | --- | --- |
| 권가빈 (`@hazelnutflavoured`) | `APPROVED` | [Issue #159 comment `5723932925`](https://github.com/AI-HealthCare-05/AH_05_04/issues/159#issuecomment-5723932925) · [comment `5724008612`](https://github.com/AI-HealthCare-05/AH_05_04/issues/159#issuecomment-5724008612) | `2026-09-18T02:07:41Z` |

## 2026-09-18 후속 결정: Answer 3-Pair Comparison Controlled Variable Binding Seam (Option 2)

Issue #159 PR C(Answer 3-pair comparison) 착수 시점의 controlled variable source 결속 및 authority extraction 경계에 대해, Product·Safety·Evaluation 책임 리뷰어 권가빈 (`@hazelnutflavoured`)의 승인 결정([#159 comment 5725554244](https://github.com/AI-HealthCare-05/AH_05_04/issues/159#issuecomment-5725554244))에 따라 다음 후속 계약을 확정한다.

### 1. Product-approved 결정 사항

1. **Option 2 Pure Typed Seam 채택**: 14개 controlled variable 전체를 typed seam으로 표현한다.
2. **Run-derived 7 controls 결속**: 현재 `RagEvaluationRun`에서 canonical source가 명확한 7개 key(`CASE_SET`, `DATASET`, `PARTITION`, `GOLD`, `RUBRIC`, `METRIC_POLICY`, `MODEL_CONFIGURATION`)는 seam 내부에서 바로 derive/바인딩하며 caller가 override하지 못한다.
3. **Supplemental 7 controls typed input**: 현재 authority extraction recipe가 없는 7개 key(`INPUT_CONTEXT`, `PROMPT_STRUCTURE`, `PARSER`, `SEED`, `SAMPLING_PARAMETERS`, `TOKEN_LIMIT`, `TIMEOUT`)는 caller-supplied arbitrary mapping으로 받지 않고 명시적 typed supplemental input으로 받는다.
4. **Delta authority extraction 후속 분리**: 8개 delta-axis key의 authoritative extraction recipe는 후속 PR(RagEvaluationRun 필드 확장 또는 별도 binding manifest)에서 확정하며, PR C에서는 typed input으로만 수신한다.
5. **Contract Test 고정**: #798 리뷰 이월 사항대로 `ANSWER_CORRECTNESS` scope의 `minimum_valid_replicate_ratio = "0.9"` 존재를 contract test로 고정한다.
6. **Schema Set 1.5 불변**: Schema Set 1.5(`cf481556cead9f99e4d424481e9ed5aed246899a4c893c45b19a2b7abcb89dc8`)는 변경하지 않는다.

### 2. Product 승인 Evidence

| 결정자 | 상태 | 근거 | 일시 (UTC) |
| --- | --- | --- | --- |
| 권가빈 (`@hazelnutflavoured`) | `APPROVED` | [Issue #159 comment `5725554244`](https://github.com/AI-HealthCare-05/AH_05_04/issues/159#issuecomment-5725554244) | `2026-09-18T05:20:21Z` |

### 3. PR C Deterministic Implementation Convention (구현 규약)

다음 세부 사항은 Product 승인 내용이 아니라 PR C의 결정적 구현 규약으로 분리하여 관리한다:

- **`comparison_sha256`**: full comparison payload의 canonical JSON 직렬화 바이트 SHA-256 hex.
- **`comparison_semantic_hash`**: `run_id`, `baseline_run_id`, `candidate_run_id` 3개 필드를 제외하고 나머지 비교 의미 필드를 canonical hashing.
- **내부 Dataclass 구조**: `AnswerComparisonSupplementalControls`, `AnswerComparisonDeltaBindings`, `AnswerComparisonRunInput` 등 명시적 internal dataclass 사용 (`dict` 임의 매핑 금지).
- **INVALID 상태 표현**:
  - authority binding 누락(None): `controlled_variable_checks = ()`, `scope_comparisons = ()`, `execution_status = INVALID`, `decision_status = null`
  - 14개 control mismatch: 14개 checks 유지(`matched = false`), `scope_comparisons` 계산 가능 시 유지, `execution_status = INVALID`, `decision_status = null`
  - non-allowed delta / case / draft mismatch: Schema 변경 없이 invariant 유지를 위해 `scope_comparisons = ()`로 `INVALID/null` 표현
  - malformed typed input: `EvaluationValidationError`로 fail-close.

## 2026-09-19 후속 결정: Answer Comparison Runtime Authority Extraction Phase A (15 Authority Bindings Audit)

> [!NOTE]
> **Historical Phase A snapshot.**
> Current authoritative status is superseded by the approved Phase B classification below.

### 1. 배경 및 PR #823 완료 상태 반영

1. **PR #823 완료 반영**: PR #823(`3fa3e604d3df8512dffa49f733aef24e97acc1d0`)이 책임 리뷰어 권가빈(`@hazelnutflavoured`)의 승인을 받아 `develop`에 병합 완료됨에 따라, Issue #159의 남은 과제 중 "approved/versioned Answer Comparison Policy"(`rag-answer-quality-dev-comparison@1.0.0`) 항목은 충족 완료되었다. Issue #159는 Runtime Authority Extraction 및 후속 바인딩 구현을 위해 **OPEN** 상태를 유지한다.
2. **Phase A 목적**: Answer 3-Pair Comparison에서 요구되는 15개 authority binding(Supplemental controlled variables 7개 + Allowed delta axes 8개)에 대해, 평가 코드가 미완성 Issue #180 / Issue #799 authority를 추정 구현하여 재작업하는 위험을 방지하기 위해, 현재 `develop` 실제 authority 전수 대조 및 canonical source / projection / hash recipe를 검토하고 미확정 항목의 blocker owner를 명시한다.
   - **식별자 주의**: `PD-799-20260918`은 Runtime Authority 관련 Decision ID이고, `Issue #799`는 Citation Authorization Production Authority Contract를 추적하는 GitHub Issue 번호다. 둘은 서로 다른 식별자다.
3. **READY 판정 기준 엄격 적용**: READY는 (1) canonical source 실재, (2) owner 명확, (3) canonical projection 기승인, (4) hash recipe 기승인의 4대 조건을 모두 충족할 때만 적용한다. Phase A에서 새로 제안된 candidate recipe는 책임 리뷰어의 승인 전까지 `SOURCE_EXISTS_RECIPE_UNRESOLVED`로 유지하며 임의 과승격하지 않는다.
4. **상태 어휘 고정**: authority 상태는 `READY`, `SOURCE_EXISTS_RECIPE_UNRESOLVED`, `BLOCKED_BY_UPSTREAM_AUTHORITY` 3개 어휘로만 엄격히 분류한다.

### 2. 15 Authority Bindings 전수 대조 매트릭스

| Key | Canonical Source Object / Field | Owner Issue | Lifecycle | Canonical / Candidate Projection | SHA-256 Preimage (Hash Recipe) | Current Availability | Status | Blocker |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `INPUT_CONTEXT` | `EvaluationCaseContract` / `case_resource.json` context, `CaseInputBinding` | #159 / #180 | Case / Dataset 준비 시점 | 미확정 (query vs patient context vs case input projection) | 미확정 | Case resource 존재, variant 비교 projection 미확정 | `SOURCE_EXISTS_RECIPE_UNRESOLVED` | #159 실험 수준 input context canonical projection 확정 |
| `PROMPT_STRUCTURE` | `DevVariant.prompt_version` / `GuidelineGenerationProvenance.prompt_ref` | #159 / #180 | Config 로드 / RAG-15 Runtime Preflight | 미확정 (template AST vs `prompt_ref.content_sha256`) | 미확정 | `prompt_version` 문자열 및 runtime `prompt_ref` 존재, 3-variant projection 미확정 | `SOURCE_EXISTS_RECIPE_UNRESOLVED` | #159 / #180 3개 variant 간 canonical prompt structure projection 확정 |
| `PARSER` | `GuidelineGenerationProvenance.parser_ref` | #159 / #180 | RAG-15 Preflight / Generator 초기화 | 미확정 (typed AnswerModelConfig schema) | 미확정 | RAG provenance에 존재, Evaluation Answer config typed schema 미확정 | `SOURCE_EXISTS_RECIPE_UNRESOLVED` | #159 typed Answer model config 정의 및 parser authority 지정 |
| `SEED` | `DevExecutionRequest.seed` | #159 | Execution Request 설정 시점 | 후보 (Candidate): `{"seed": request.seed}` | 후보 (Candidate): `canonical_sha256({"seed": request.seed})` | `DevExecutionRequest.seed` 존재 (recipe 미승인) | `SOURCE_EXISTS_RECIPE_UNRESOLVED` | #159 Decision에서 projection/hash recipe 승인 필요 |
| `SAMPLING_PARAMETERS` | `DevVariant.parameters` | #159 | Variant 정의 시점 | 미확정 (sampling-only key whitelist; `token_limit`/`timeout` 제외) | 미확정 | `parameters` dict 존재, sampling 전용 key whitelist 미확정 | `SOURCE_EXISTS_RECIPE_UNRESOLVED` | #159 sampling parameter projection key set whitelist 확정 |
| `TOKEN_LIMIT` | `DevVariant.parameters["token_limit"]` / Generator provider config | #159 / #180 | Variant config / Generator provider 호출 시점 | 미확정 (`{"token_limit": int}`) | 미확정 | Config dict에 존재, 실제 런타임 호출 제약 결속 미확정 | `SOURCE_EXISTS_RECIPE_UNRESOLVED` | #159 / #180 token limit authority 및 projection recipe 합의 |
| `TIMEOUT` | `DevVariant.parameters["timeout"]` / Generator provider client config | #159 / #180 | Variant config / Generator client 초기화 시점 | 미확정 (`{"timeout_ms": int}`) | 미확정 | Config dict에 존재, 실제 런타임 provider timeout 결속 미확정 | `SOURCE_EXISTS_RECIPE_UNRESOLVED` | #159 / #180 timeout authority 및 projection recipe 합의 |
| `RETRIEVAL_PIPELINE` | `VersionedEvidenceRetrievalConfiguration` / `ActualRetrievalModelConfig` | #159 / #178 / #180 | Retrieval config 생성 및 seal 시점 | 미확정 (`MODEL_CONFIGURATION` 제외 pipeline config, `ANS-BASE` 표현) | 미확정 | `retrieval_config` content hash 존재, baseline 표현 및 추출 seam 미확정 | `SOURCE_EXISTS_RECIPE_UNRESOLVED` | #159 / #180 retrieval pipeline projection 및 `ANS-BASE` non-retrieval 표현 확정 |
| `SOURCE_INDEX` | `ActualRetrievalModelConfig.knowledge_index_ref` / `RagRuntimeReleaseBundle.knowledge_index_manifest_hash` | #159 / #800 | Knowledge index 빌드 및 Bundle 활성화 시점 | 미확정 (`knowledge_index_ref.hash` 직접 사용 vs canonical projection; `ANS-BASE` 표현) | 미확정 | `knowledge_index_ref` 존재, `ANS-BASE` 바인딩 및 exact recipe 미확정 | `SOURCE_EXISTS_RECIPE_UNRESOLVED` | #159 `source_index_hash` baseline/candidate exact recipe 규정 |
| `RUNTIME_BUNDLE` | `rag_runtime_release_bundle` (`id`, `bundle_manifest_hash`, `environment_code`) + `RequestGuardRuntimeBindingObservation` (#806) | Issue #810 / Issue #806 (PR #828 병합 완료) | Bundle 빌드/릴리스 및 per-request 런타임 바인딩 시점 | 후보 (Candidate): `(bundle_id, bundle_manifest_hash, environment)` 최소 프로젝션 | 미확정 (#806 소스는 실재하나 #159 canonical projection 및 hash recipe 미승인) | PR #828 병합으로 `RequestGuardRuntimeBindingObservation` 실재. 단, #159 비교 레시피 미확정. | `SOURCE_EXISTS_RECIPE_UNRESOLVED` | #159 Decision에서 canonical RUNTIME_BUNDLE projection/hash recipe 승인 필요 |
| `RETRIEVED_EVIDENCE` | `CaseResult.selected_evidence_ids` / `VerifiedGuideEvidenceHandoff` / `ProductionGuidelineEvidenceSet` | #159 / #180 (#760) | Evidence handoff 조립 및 projection 시점 | 미확정 (raw retrieval hits vs post-gate selected evidence; Run-level 집계) | 미확정 | Case 단위 evidence ID 존재, canonical stage 및 Run 집계 recipe 미확정 | `SOURCE_EXISTS_RECIPE_UNRESOLVED` | #159 canonical evidence stage (selected vs retrieved) 및 Run 집계 확정 |
| `FINAL_VALIDATOR` | 후보 소스: `GuidelineGenerationProvenance.validator_ref`, Claim-Citation validation/finalization authority | #180 | Guideline card 완료 및 Claim-Citation 검증 시점 | 상류 차단 (`validator_ref`는 실재하나 `ANS-RAG -> ANS-FINAL`의 `FINAL_VALIDATOR` 정본 동일시 계약 부재) | 상류 차단 | `validator_ref` 및 순수 검증 로직 존재하나 정본 validator immutable identity 미확정 | `BLOCKED_BY_UPSTREAM_AUTHORITY` | #180에서 ANS-RAG → ANS-FINAL 경계의 FINAL_VALIDATOR 의미와 immutable identity 확정 필요 |
| `CITATION_GATE` | `CitationAuthorizationReceipt` | Issue #799 / Issue #806 / Issue #807 (Issue #180) | Citation authorization receipt 발급 시점 | 상류 차단 (production receipt 발급은 Issue #806 REQUEST Guard Decision 및 Issue #807 PATIENT_CITATION Approval 전제) | 상류 차단 | 순수 검증 함수 존재, production receipt authority 및 영속화 미구현 | `BLOCKED_BY_UPSTREAM_AUTHORITY` | Issue #799 (OPEN), Issue #806 (PR #828 완료), Issue #807 (OPEN) |
| `SAFETY_GATE` | 목표/후보: #180 Runtime Safety Gate decision / receipt (canonical immutable runtime Safety Gate authority not implemented; Evaluation Safety metric 재사용 금지) | #180 | 런타임 safety disposition 및 release 평가 시점 | 상류 차단 (`ANS-RAG -> ANS-FINAL` 경계 실행 Safety Gate 식별 immutable authority 미확정) | 상류 차단 | Safety 정책/risk level 존재, production safety gate receipt authority 미확정 | `BLOCKED_BY_UPSTREAM_AUTHORITY` | #180 runtime Safety Gate authority identity / receipt 확정 |
| `RELEASE_GATE` | 목표/후보: #180 Runtime Release Gate decision / receipt (canonical immutable runtime Release Gate authority not implemented; `ai_worker/tasks/evaluation/release_gate.py` 평가 릴리스 게이트와 도메인 엄격 분리) | #180 | 최종 런타임 릴리스 게이트 평가 시점 | 상류 차단 (`PASS / LIMITED / REJECTED / STALE` publication decision exact-bind immutable authority 미확정) | 상류 차단 | Release decision enum 존재, production release gate authority receipt 미구현 | `BLOCKED_BY_UPSTREAM_AUTHORITY` | #180 final runtime release authority / receipt 확정 |

### 3. 상태별 분류 요약

- **`READY` (0개)**:
  - 현재 승인된 정본 계약 기준으로 4대 조건(source 존재, owner 명확, projection 승인 완료, recipe 승인 완료)을 충족하는 항목 없음. Phase A에서 제안된 candidate recipe는 reviewer 승인 전까지 `SOURCE_EXISTS_RECIPE_UNRESOLVED`로 유지함.
- **`SOURCE_EXISTS_RECIPE_UNRESOLVED` (11개)**:
  - `INPUT_CONTEXT`, `PROMPT_STRUCTURE`, `PARSER`, `SEED`, `SAMPLING_PARAMETERS`, `TOKEN_LIMIT`, `TIMEOUT`, `RETRIEVAL_PIPELINE`, `SOURCE_INDEX`, `RETRIEVED_EVIDENCE`, `RUNTIME_BUNDLE`:
  - 소스 객체 또는 파라미터가 존재하나, 변형 간 canonical projection, 화이트리스트, 비검색 baseline(`ANS-BASE`) 표현, 또는 Decision 승인 절차가 미완료되어 평가 코드가 임의 추정 구현을 방지해야 하는 항목.
  - PR #828 / Issue #806 병합(`5c99a538`)으로 `RequestGuardRuntimeBindingObservation`이 도입되어 `RUNTIME_BUNDLE`이 상류 차단에서 이 분류로 이동함.
- **`BLOCKED_BY_UPSTREAM_AUTHORITY` (4개)**:
  - `FINAL_VALIDATOR` (#180 차단)
  - `CITATION_GATE` (Issue #799, Issue #807 차단)
  - `SAFETY_GATE` (#180 차단)
  - `RELEASE_GATE` (#180 차단)
  - 상류 도메인의 정본 계약 및 프로덕션 authority receipt가 완성되지 않았으므로, 평가 계층에서 가상 authority를 합성하지 않고 명시적으로 차단 상태를 유지함.

### 4. Storage Architecture 권고안: Separate Binding Manifest Preferred

Phase A 전수 대조 결과, authority binding의 저장 아키텍처는 다음 권고를 확정한다:

- **Recommended storage**: `SEPARATE_BINDING_MANIFEST_PREFERRED`
- **Implementation decision**: `PENDING_UPSTREAM_AUTHORITY_FREEZE`

#### 권고 근거
1. **상류 런타임 도메인 격리**: `RUNTIME_BUNDLE`, `FINAL_VALIDATOR`, `CITATION_GATE`, `SAFETY_GATE`, `RELEASE_GATE` 등 8개 delta 중 5개가 런타임 도메인(Issue #180, Issue #799, Issue #806, Issue #807)의 authority receipt를 참조한다. 이들 상류 authority는 현재 진행 중이며 점진적으로 정착되므로, `RagEvaluationRun`에 직접 종속시키면 상류 변화마다 평가 스키마가 영향을 받는다.
2. **Schema Set 1.5 불변 유지 및 Version Churn 방지**: `RagEvaluationRun`은 이미 승인 고정된 Schema Set 1.5(`cf481556cead9f99e4d424481e9ed5aed246899a4c893c45b19a2b7abcb89dc8`)의 핵심 엔티티다. 15개 hash 필드를 `RagEvaluationRun`에 추가하면 `rag-eval.run` 스키마 버전 상향, 기존 러너·로더·마이그레이션 전체에 연쇄 churn이 발생한다.
3. **기존 Answer Comparison 아키텍처 정합성**: PR #808에서 이미 `AnswerComparisonRunInput` 및 `AnswerComparisonSupplementalControls` / `AnswerComparisonDeltaBindings`를 분리된 typed seam으로 설계하여, 외부 binding manifest를 자연스럽게 수용할 수 있는 기반이 마련되어 있다.

단, 본 단계에서는 추천 방향만 기록하며 새 schema, DTO 또는 Python 코드는 일절 구현하지 않는다 (`Implementation: NOT STARTED`).

### 5. Phase B 착수 조건

1. `READY` 항목 (0개): 현재 즉각 구현 대상 없음.
2. `SOURCE_EXISTS_RECIPE_UNRESOLVED` 항목 (11개): `SEED` 및 `RUNTIME_BUNDLE`을 포함하여 candidate recipe 및 변형 간 projection에 대한 #159 Decision 책임 리뷰어 승인 완료 시 구현 착수.
3. `BLOCKED_BY_UPSTREAM_AUTHORITY` 항목 (4개): Issue #806은 PR #828로 완료되어 `RUNTIME_BUNDLE` 소스가 확보되었다. 남은 upstream authority는 Issue #807, Issue #799, Issue #180이다. 각 항목은 자신의 canonical authority와 immutable identity가 완료되기 전 평가 바인딩을 구현하지 않는다.

---

## 2026-09-19 후속 결정: Answer Comparison Runtime Authority Binding Phase B (10 Canonical Recipes + 1 Explicitly Unresolved Binding & Binding Manifest Contract) — Approved

### 1. 상태 재정렬 요약 (Rebaseline) 및 승인 Evidence

PR #828 / Issue #806 병합(`5c99a538`) 완료를 반영하여 authority 상태를 재정렬하고, PR #833에서 책임 리뷰어 권가빈(`@hazelnutflavoured`)의 Phase B 계약 승인이 완료되었다.

#### Phase B 승인 Evidence

| 항목 | 값 |
| --- | --- |
| Responsible Reviewer | 권가빈 (`@hazelnutflavoured`, PM / Track F Acceptance) |
| Status | `APPROVED` (Phase B DEV contract implementation approval) |
| PR | #833 |
| Reviewed/Approved Final HEAD | `ef8a78c8f6ad1c037d203d1b376d899e0dbffbcd` |
| Merge Commit | `5128cfdee8d9791a76fe6331cea2314420184cc9` |
| Approval Comment | [`5740823309`](https://github.com/AI-HealthCare-05/AH_05_04/pull/833#issuecomment-5740823309) |
| Approval Text | `PD-159-20260913 Phase B / PR #833 final HEAD ef8a78c8 APPROVED` |
| Approval Date | 2026-09-19 |

- `RequestGuardRuntimeBindingObservation`(`environment`, `bundle_id`, `bundle_manifest_hash`) 소스가 `develop`에 도입됨에 따라 `RUNTIME_BUNDLE`은 더 이상 #806 자체로 차단되지 않으며, Phase B에서 canonical projection 및 hash recipe가 승인 완료되었다.
- 본 Phase B 계약 승인으로 10 Canonical Recipes(7 supplemental + 3 delta)의 projection 및 hash recipe가 승인/확정(`DEFINED / APPROVED`)되었다.
- **Phase A 이력 분류 대체 (Superseded)**: Phase A 당시의 잠정 분류(`SOURCE_EXISTS_RECIPE_UNRESOLVED` 11개)는 본 Phase B 승인 분류로 공식 대체(superseded)된다.
- **상태 어휘 확장 (Status Vocabulary)**:
  - 기존 3개 상태(`READY`, `SOURCE_EXISTS_RECIPE_UNRESOLVED`, `BLOCKED_BY_UPSTREAM_AUTHORITY`)만으로는 "레시피는 승인되었으나 바인딩 구현/materialization이 아직 완료되지 않은 상태"를 모순 없이 표현할 수 없으므로, **`RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING`** 어휘를 공식 추가한다.
  - **정의**: Canonical projection/hash recipe는 Phase B에서 승인되었으나, 그 recipe를 실제 `ANS-BASE` / `ANS-RAG` / `ANS-FINAL` execution authority에서 추출·검증·materialize하여 Answer Runtime Binding Manifest에 기록하는 production/DEV binding implementation은 아직 완료되지 않은 상태.
  - **엄격한 구별**:
    $$\text{RECIPE\_APPROVED\_BINDING\_IMPLEMENTATION\_PENDING} \neq \text{RECIPE\_UNRESOLVED}$$
    $$\text{RECIPE\_APPROVED\_BINDING\_IMPLEMENTATION\_PENDING} \neq \text{READY}$$
    $$\text{RECIPE\_APPROVED\_BINDING\_IMPLEMENTATION\_PENDING} \neq \text{BLOCKED\_BY\_UPSTREAM\_AUTHORITY}$$
  - **계층 분리 원칙**:
    $$\text{Authority Recipe Status} \neq \text{Authoritative Runtime Carrier Status} \neq \text{Pair Execution Readiness}$$
    $$\text{Recipe Approved} \neq \text{Binding Implemented} \neq \text{Carrier Ready} \neq \text{Pair Executable}$$
    (예: `INPUT_CONTEXT`는 Run bundle source가 존재하지만, authoritative manifest construction + 3-Variant materialization + typed seam projection wiring이 미구현 상태이므로 본 상태에 속함)
- **Phase B 승인 후 전체 authority 상태 재분류 (Exact 15 Bindings)**:
  - **`READY`**: **0개** (실제 pair execution ready 항목 없음)
  - **`RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING`**: **10개**
    - Supplemental 7: `INPUT_CONTEXT`, `PROMPT_STRUCTURE`, `PARSER`, `SEED`, `SAMPLING_PARAMETERS`, `TOKEN_LIMIT`, `TIMEOUT`
    - Delta 3: `RETRIEVAL_PIPELINE`, `SOURCE_INDEX`, `RUNTIME_BUNDLE`
  - **`SOURCE_EXISTS_RECIPE_UNRESOLVED`**: **1개**
    - `RETRIEVED_EVIDENCE` (`ProductionGuidelineEvidenceSet`과 evaluation case 간 authoritative carrier 및 Run-level aggregation recipe 미확정)
  - **`BLOCKED_BY_UPSTREAM_AUTHORITY`**: **4개**
    - `FINAL_VALIDATOR` (#180), `CITATION_GATE` (#807/#799/#180), `SAFETY_GATE` (#180), `RELEASE_GATE` (#180)
- 실질 산출물: **10 Canonical Recipes + 1 Explicitly Unresolved Binding (`RETRIEVED_EVIDENCE`) + Separate Binding Manifest Contract** (Approved Target)

### 2. 10 Canonical Recipes 규격, Carrier 상태 분리 및 1 Explicitly Unresolved Binding

#### 2.1 7 Supplemental Controls의 Runtime Carrier 상태 분리
Authority binding 체계는 다음 세 계층을 엄격히 분리한다:
$$\text{Authority Recipe Status} \neq \text{Authoritative Runtime Carrier Status} \neq \text{Pair Execution Readiness}$$
$$\text{Recipe Approved} \neq \text{Binding Implemented} \neq \text{Carrier Ready} \neq \text{Pair Executable}$$
$$\text{source object exists} \neq \text{ANS-BASE / ANS-RAG / ANS-FINAL 실행에서 그 값을 authoritative하게 materialize할 carrier가 이미 존재함}$$

| Supplemental Key | Canonical Recipe | Phase B State | Current Source Object | Variant Execution Carrier Status | 판정 및 세부 사유 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `INPUT_CONTEXT` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `LoadedRunBundle.cases` (`CaseResult.case_id`, `CaseResult.input_sha256`) | `RUN_BUNDLE` (Run 완성 시 실재; 3-Variant actual run 미실행) | 실제 완료된 Run bundle이 존재할 때 carrier 계약은 실재함. 단, `ANS-BASE`/`ANS-RAG`/`ANS-FINAL` 3-Variant actual execution materialization은 미실행 (`NOT YET EXECUTED`). |
| `SEED` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `DevExecutionRequest.seed` (`SafeInteger`) | `UNRESOLVED` (미구현) | `RagEvaluationRun`에는 seed 자체가 저장되지 않음. Persisted Run에서 seed 재구성 불가. Manifest carrier 별도 구현 전까지 `NOT YET IMPLEMENTED` / `UNRESOLVED`. |
| `PROMPT_STRUCTURE` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `GuidelineGenerationProvenance.prompt_ref` (`ImmutableArtifactRef`) | `UNRESOLVED` (미구현) | RAG Generator provenance 소스는 실재하나, `ANS-BASE`/`ANS-RAG`/`ANS-FINAL` 각 Variant의 actual execution carrier 미구현. `ANS-BASE`가 Generator를 호출한다고 가정할 수 없으며, mandatory controlled variable이므로 임의 `NOT_APPLIED` 불가 (3-Variant exact-match 필수). 실제 baseline 실행 모델 확정 전까지 carrier `UNRESOLVED`. |
| `PARSER` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `GuidelineGenerationProvenance.parser_ref` (`ImmutableArtifactRef`) | `UNRESOLVED` (미구현) | `PROMPT_STRUCTURE`와 동일 원칙. 3 Variant 전체에서 동일 parser identity를 authoritative하게 공급하는 execution carrier 미구현. |
| `SAMPLING_PARAMETERS` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `OpenAIGuidelineGeneratorAdapter` 실제 호출 파라미터 (`temperature=0`) | `UNRESOLVED` (미구현) | RAG runtime actual invocation 소스는 실재하나, 3-Variant 공통 carrier로 미구현. |
| `TOKEN_LIMIT` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `DevVariant.parameters["token_limit"]` + Adapter `_max_output_tokens` | `UNRESOLVED` (미구현) | `DevVariant.parameters`는 Variant config일 뿐 actual 3-run runtime carrier가 아님. config ↔ runtime exact-binding rule 승인됨 (PR #833), 3-variant carrier 미구현. |
| `TIMEOUT` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `DevVariant.parameters["timeout"]` + Adapter `_timeout_seconds` | `UNRESOLVED` (미구현) | `TOKEN_LIMIT`와 동일. config ↔ runtime exact-binding rule 승인됨 (PR #833), 3-variant carrier 미구현. |

#### 2.2 10 Canonical Recipes 규격 및 1 Explicitly Unresolved Binding (Full Specification)

| Key | Binding Field | Canonical Source Object | Owner Issue | Canonical Projection 규격 | Hash Preimage / Hashing Rule | Variant Semantics (ANS-BASE 포함) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `INPUT_CONTEXT` | `supplemental_controls.input_context_hash` | `LoadedRunBundle.cases` (`CaseResult.case_id`, `CaseResult.input_sha256`) | #159 | `{"cases": [{"case_id": c.case_id, "input_sha256": c.input_sha256} for c in cases], "projection_version": "answer-input-context-v1"}` (UTF-16 BE `case_id` 순 정렬, 원문 질문·환자 텍스트 일절 비포함) | `canonical_sha256(projection)` | Controlled variable (`ANS-BASE`, `ANS-RAG`, `ANS-FINAL` 3개 변형 전수 exact-match 필수) |
| `PROMPT_STRUCTURE` | `supplemental_controls.prompt_structure_hash` | `GuidelineGenerationProvenance.prompt_ref` (`ImmutableArtifactRef`) | #159 | 불변 아티팩트 참조 자체 사용 (`artifact_code="guideline-prompt"`, `version=...`, `content_sha256=...`) | `prompt_ref.content_sha256` (기존 불변 아티팩트 SHA-256 직접 재사용) | Controlled variable (프롬프트 템플릿 지침/구조 불변 결속) |
| `PARSER` | `supplemental_controls.parser_hash` | `GuidelineGenerationProvenance.parser_ref` (`ImmutableArtifactRef`) | #159 | 불변 아티팩트 참조 자체 사용 (`artifact_code="guideline-parser"`, `version=...`, `content_sha256=...`) | `parser_ref.content_sha256` (기존 불변 아티팩트 SHA-256 직접 재사용) | Controlled variable (structured parser 정본 불변 결속) |
| `SEED` | `supplemental_controls.seed_hash` | `DevExecutionRequest.seed` (`SafeInteger`) | #159 | `{"projection_version": "answer-seed-v1", "seed": request.seed}` | `canonical_sha256(projection)` | Controlled variable (재현 가능한 난수 시드 불변 결속) |
| `SAMPLING_PARAMETERS` | `supplemental_controls.sampling_parameters_hash` | `OpenAIGuidelineGeneratorAdapter` 실제 호출 파라미터 (`temperature=0`) | #159 | `{"projection_version": "answer-sampling-parameters-v1", "temperature": "0"}` (실제 invocation에 전달되지 않는 `top_p`, `frequency_penalty`, `presence_penalty` 제외) | `canonical_sha256(projection)` (정규 10진 문자열 인코딩) | Controlled variable (실제 runtime invocation 결속 샘플링 파라미터 불변 결속) |
| `TOKEN_LIMIT` | `supplemental_controls.token_limit_hash` | `DevVariant.parameters["token_limit"]` exact-bound to `OpenAIGuidelineGeneratorAdapter._max_output_tokens` | #159 | `{"max_output_tokens": int(max_output_tokens), "projection_version": "answer-token-limit-v1"}` | `canonical_sha256(projection)` | Controlled variable (정수 토큰 상한 불변 결속) |
| `TIMEOUT` | `supplemental_controls.timeout_hash` | `DevVariant.parameters["timeout"]` exact-bound to `OpenAIGuidelineGeneratorAdapter._timeout_seconds` | #159 | `{"projection_version": "answer-timeout-v1", "timeout_seconds": str(Decimal(str(timeout_seconds)))}` (손실성 정수 ms round 제거, canonical decimal seconds 인코딩) | `canonical_sha256(projection)` | Controlled variable (실제 Provider client timeout 불변 결속) |
| `RETRIEVAL_PIPELINE` | `delta_bindings.retrieval_pipeline_hash` | `VersionedEvidenceRetrievalConfiguration.artifact_ref` (`ImmutableArtifactRef`) | #159 | • `ANS-RAG`/`ANS-FINAL`: 불변 아티팩트 참조 사용 (`selection_limit`은 별도 `EvidenceRetrievalKernelRequest` 소스이므로 configuration projection에서 배제)<br>• `ANS-BASE`: `{"axis": "RETRIEVAL_PIPELINE", "binding_state": "NOT_APPLIED", "projection_version": "answer-authority-binding-v1"}` | • `ANS-RAG`/`ANS-FINAL`: `retrieval_config.artifact_ref.content_sha256` 직접 재사용 (`compute_canonical_hash()` 결속값)<br>• `ANS-BASE`: `canonical_sha256(NOT_APPLIED_projection)` | Allowed delta (`ANS-BASE` vs `ANS-RAG`); Controlled match (`ANS-RAG` vs `ANS-FINAL`) |
| `SOURCE_INDEX` | `delta_bindings.source_index_hash` | `ActualRetrievalModelConfig.knowledge_index_ref` (`ImmutableReference`) / `RagRuntimeReleaseBundle.knowledge_index_manifest_hash` | #159 | • `ANS-RAG`/`ANS-FINAL`: 불변 참조 자체 사용<br>• `ANS-BASE`: `{"axis": "SOURCE_INDEX", "binding_state": "NOT_APPLIED", "projection_version": "answer-authority-binding-v1"}` | • `ANS-RAG`/`ANS-FINAL`: `knowledge_index_ref.hash` 직접 재사용<br>• `ANS-BASE`: `canonical_sha256(NOT_APPLIED_projection)` | Allowed delta (`ANS-BASE` vs `ANS-RAG`); Controlled match (`ANS-RAG` vs `ANS-FINAL`) |
| `RUNTIME_BUNDLE` | `delta_bindings.runtime_bundle_hash` | `RequestGuardRuntimeBindingObservation` (`environment`, `bundle_id`, `bundle_manifest_hash` via PR #828 / #806) | #159 / #806 | • `ANS-RAG`/`ANS-FINAL`: `{"bundle_id": str(obs.bundle_id), "bundle_manifest_hash": obs.bundle_manifest_hash, "environment": obs.environment.value, "projection_version": "answer-runtime-bundle-binding-v1"}` (decision_id, user_id, scope 등 요청별 가변 메타데이터 배제)<br>• `ANS-BASE`: `{"axis": "RUNTIME_BUNDLE", "binding_state": "NOT_APPLIED", "projection_version": "answer-authority-binding-v1"}` | • `ANS-RAG`/`ANS-FINAL`: `canonical_sha256(projection)`<br>• `ANS-BASE`: `canonical_sha256(NOT_APPLIED_projection)` | Allowed delta (`ANS-BASE` vs `ANS-RAG`); Controlled match (`ANS-RAG` vs `ANS-FINAL`) |
| `RETRIEVED_EVIDENCE` | `delta_bindings.retrieved_evidence_hash` | `ProductionGuidelineEvidenceSet` vs `CaseResult.selected_evidence_ids` | #159 / #180 (#760) | **Unresolved Binding (Recipe Unresolved)**<br>• `ANS-RAG`/`ANS-FINAL`: 미확정 (이유: `ProductionGuidelineEvidenceSet`에 `case_id`가 없고 `CaseResult.selected_evidence_ids`는 단순 ID 튜플만 보유하여 실제 generator 소비 evidence content hash를 온전히 증명하지 못함. 향후 authoritative carrier 확정 필요)<br>• `ANS-BASE`: `{"axis": "RETRIEVED_EVIDENCE", "binding_state": "NOT_APPLIED", "projection_version": "answer-authority-binding-v1"}` | • `ANS-RAG`/`ANS-FINAL`: Carrier 및 Stage 확정 전까지 `null` 유지<br>• `ANS-BASE`: `canonical_sha256(NOT_APPLIED_projection)` | Allowed delta (`ANS-BASE` vs `ANS-RAG`); Controlled match (`ANS-RAG` vs `ANS-FINAL`) |

### 3. `NOT_APPLIED` Typed State 및 Variant별 Delta Semantics

1. **`NOT_APPLIED` Typed State**:
   - 비적용 축은 임의의 fake 값(`"none"`, `"LOCAL"`)을 쓰지 않고 명시적인 Canonical Typed State로 프로젝션한다:
     `{"axis": "<AXIS_KEY>", "binding_state": "NOT_APPLIED", "projection_version": "answer-authority-binding-v1"}`
   - `NOT_APPLIED`는 `missing`, `blocked`, `null fallback`, `synthetic authority`와 구별되는 명시적 variant semantics이다.
2. **Variant별 Delta 바인딩**:
   - **`ANS-BASE`**: 4개 retrieval axes = `NOT_APPLIED`, 4개 finalization axes = `NOT_APPLIED`.
   - **`ANS-RAG`**: 3개 retrieval axes = 실제 정본 바인딩, 1개 retrieval axis (`RETRIEVED_EVIDENCE`) = `null`, 4개 finalization axes = `NOT_APPLIED`.
   - **`ANS-FINAL`**: 3개 retrieval axes = 실제 정본 바인딩 (`ANS-RAG`와 일치), 1개 retrieval axis (`RETRIEVED_EVIDENCE`) = `null`, 4개 finalization axes = 실제 정본 바인딩 (상류 미완료 시 `null` / Blocked).
3. **Pairwise Readiness 및 Fail-Closed 보장**:
   - **`ANS-BASE -> ANS-RAG`**: `NOT_APPLIED` semantics 도입으로 4개 finalization authority의 부재(#180, #807, #799)가 본 비교를 가로막는 구조적 종속 문제는 완전히 해결되었다 (두 Variant 모두 동일한 `NOT_APPLIED` 해시를 가지므로 non-allowed deltas가 일치). 그러나 **`ANS-RAG`의 `RETRIEVED_EVIDENCE` authoritative binding이 아직 `null`(`None`) 상태이며 7개 Supplemental Controls의 runtime carrier/extractor가 미구현 상태이므로, 현재 #808 `_check_delta_bindings()` 커널 실행 시 `delta_binding_missing = True`가 발동되어 `NOT READY` (fail-closed / `INVALID`, `decision_status = None`) 상태**로 처리된다.
     - **실제 Pair 실행 전제 조건 (Prerequisites)**:
       - A: 7개 Supplemental Controls가 `ANS-BASE`와 `ANS-RAG` 양쪽에서 authoritative carrier로 materialize될 것
       - B: 두 Variant에서 7개 supplemental bindings가 모두 non-None일 것
       - C: 7개 mandatory supplemental hashes가 exact-match할 것
       - D: `RETRIEVED_EVIDENCE` authoritative carrier/recipe가 해결되어 non-None으로 제공될 것
       - E: 나머지 3개 retrieval delta 축의 authoritative binding이 확보될 것
       - F: 4개 finalization 축은 두 Variant 모두 canonical `NOT_APPLIED`로 exact-match할 것
     - **핵심 원칙**: `NOT_APPLIED` semantics 해결로 #180/#807/#799 finalization authority가 선행조건이 되는 구조적 문제는 제거되었으나, Pair execution은 `RETRIEVED_EVIDENCE`뿐 아니라 7개 Supplemental Controls의 authoritative runtime carrier가 모두 확보된 후에만 가능하다.
   - **`ANS-RAG -> ANS-FINAL` 및 `ANS-BASE -> ANS-FINAL`**: 7개 Supplemental Controls carrier 미구현, `RETRIEVED_EVIDENCE` 미해결(`null`), 그리고 `ANS-FINAL`의 4개 finalization 축 상류 차단(`None` / `null`)으로 인해 `delta_binding_missing = True`가 발동되어 `NOT READY` (fail-closed / `INVALID`, `decision_status = None`) 상태로 자동 차단된다.

### 4. Separate Binding Manifest 계약 규격

- **Schema ID**: `rag-eval.answer-runtime-binding-manifest`
- **Schema Version**: `1.0.0`
- **계약 문서**: [`docs/contracts/targets/post-mvp-1/answer-runtime-binding-manifest-v1.md`](../../contracts/targets/post-mvp-1/answer-runtime-binding-manifest-v1.md)
- **Self-Hash 계산**: `canonical_sha256(payload, excluded_top_level_keys=frozenset({"manifest_sha256"}))`
- **Run-Scoped 무결성 특성**:
  - `manifest_sha256`은 특정 Evaluation Run에 결속된 run-scoped artifact integrity hash이다.
  - Preimage에는 `schema_id`, `schema_version`, `experiment_id`, `run_id`, `variant_id`, `supplemental_controls`, `delta_bindings`가 모두 포함된다.
  - 동일한 run-scoped manifest payload 재계산 시 deterministic artifact integrity hash를 생성한다.
  - `run_id`는 manifest artifact가 특정 evaluation run의 authority bindings를 증명하므로 self-hash preimage에 유지되며, `same authority configuration + different run_id -> different manifest_sha256`은 정상적인 기대 동작이다.
  - Cross-run semantic equality 판정은 개별 바인딩 해시(`input_context_hash`, `prompt_structure_hash`, ...)로 수행되며, `manifest_sha256`을 cross-run 비교용으로 쓰지 않는다.
  - 임의 생성 UUID(`manifest_id`), 벽시계 타임스탬프(`created_at`), 외부 GitHub 이슈 메타데이터(`blocker_issue`)는 manifest wire 및 해시 프리이미지에서 완전히 배제된다.
  - `run_id`는 Evaluation 공통 `CanonicalUuid` 계약(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)과 정렬한다.
- **기존 Artifact 구별**: 3개 pair comparison artifact를 묶는 `answer-comparison-set-manifest`와 본 `answer-runtime-binding-manifest`는 별개의 독립 manifest로 관리된다.

### 5. PR #808 Typed Seam 결속 및 Extractor 책임 분리

- **Seam 투영**:
  - 본 Manifest의 `supplemental_controls` 7개 필드는 `AnswerComparisonSupplementalControls`에 1:1 매핑된다.
  - `delta_bindings` 8개 필드는 `AnswerComparisonDeltaBindings`에 1:1 매핑된다 (`RETRIEVED_EVIDENCE` 및 `ANS-FINAL`의 미해결 차단 항목은 `None` 매핑).
- **Authoritative Extractor의 책임 분리**:
  - #808 비교 커널은 투영된 7개 supplemental hash가 non-None인지와 exact-match하는지만 검증하며, upstream carrier의 진위성을 보장하지 않는다.
  - 향후 구현될 Authoritative Manifest Extractor가 각 Variant의 authoritative source에서 값을 추출하고 임의 caller 주입을 차단한 후 검증된 해시만을 #808 seam으로 투영할 책임을 갖는다.
  $$\text{Authoritative Carrier / Extractor Validation} \longrightarrow 7\text{ Supplemental Hashes} \longrightarrow \text{\#808 Exact-Match}$$

### 6. 승인 완료 및 구현 착수 Gate (Approval Completed & Implementation Gate)

1. **승인 완료**: 책임 리뷰어 권가빈(`@hazelnutflavoured`)의 승인 코멘트 `5740823309`에 따라 Phase B 계약(10 Canonical Recipes + 1 Explicitly Unresolved Binding 및 Binding Manifest Contract) 승인이 완료되었다 (`APPROVED`).
2. **구현 착수 허용**: Phase B contract approval 완료 및 상태 정합에 따라 다음 항목의 Python 구현을 착수할 수 있다:
   - **`RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` 10개**: Phase B 승인 완료. Manifest DTO/schema(`rag-eval.answer-runtime-binding-manifest@1.0.0`), 10 canonical recipe helpers, authoritative extractor/binding validation(`CanonicalUuid`, 64-hex hash pattern, self-hash integrity), #808 typed seam projection(`AnswerComparisonSupplementalControls`, `AnswerComparisonDeltaBindings`) 및 fail-closed 구현 착수 허용.
   - **`SOURCE_EXISTS_RECIPE_UNRESOLVED` 1개 (`RETRIEVED_EVIDENCE`)**: Canonical source/projection/hash recipe 승인 전 구현 금지 (`null` 유지).
   - **`BLOCKED_BY_UPSTREAM_AUTHORITY` 4개 (`FINAL_VALIDATOR`, `CITATION_GATE`, `SAFETY_GATE`, `RELEASE_GATE`)**: 각 upstream canonical authority 완료 전 actual binding 구현 금지 (`null` 유지).
3. **구현 금지 항목 유지**: 단 carrier unresolved 항목으로 인해 actual 3-variant runtime execution은 불가하며, 다음은 여전히 엄격히 금지된다:
   - `RETRIEVED_EVIDENCE` fake carrier
   - `ANS-BASE` synthetic supplemental authority
   - `FINAL_VALIDATOR`
   - `CITATION_GATE`
   - `SAFETY_GATE`
   - `RELEASE_GATE`
   - actual 3-variant execution
   - HOLDOUT
   - Baseline Freeze
   - `PUBLIC_TRACK_F`

## 2026-09-20 후속 결정: Answer Runtime Authority Carrier / Materialization Coordinate (Phase C-1 Audit) — Proposed / Review Required

### 1. 목적 및 원칙 (Purpose & Principles)
- **책임자 및 검토 체계**:
  - 구현 책임자: 정현우 (`@junghyunwoo`)
  - 책임 리뷰어: 송은영 (`@phina-io`) — **Review Required** (승인 전 Frozen/Approved 사용 불가)
  - 인수 근거 제공: 권가빈 (`@hazelnutflavoured`)
  - 평가/Worker 전문 근거 제공: 김지혜 (`@Jye-rookie`)
- **감사 및 설계 원칙**:
  1. PR #851(Phase B)에서 구현된 10개 Canonical Recipe 및 Manifest 검증/사영 커널을 그대로 재사용한다.
  2. Source Object 실재와 Run-bound Authoritative Carrier를 엄격히 분리한다.
  3. Authoritative Source Exists, Run-bound Carrier Exists, Exact Lookup Coordinate Exists, Exact Validation Possible, Materialization Lifecycle Point Defined의 6대 조건이 모두 충족될 때만 Extractor 구현을 허용한다.
  4. 확인되지 않은 authority는 임시 구현하지 않으며, fail-closed를 유지한다.

### 2. 15 Authority Bindings 전수 감사 및 구현 판정 결과 (Audit Findings)
- **감사 기준**: 최신 `develop` (`8f29710547c8a405736d4a50dcac41bc10a90ed3`)
- **전수 판정 요약**:
  - **`IMPLEMENTABLE NOW = NONE` (0개)**:
    - 6대 조건을 모두 충족하는 유일한 축은 `INPUT_CONTEXT`이나, 이는 이미 PR #851에서 `compute_input_context_binding_hash(bundle: LoadedRunBundle)`로 구현 완료되었으므로 불필요한 신규 래퍼 클래스를 생성하지 않는다 (`IMPLEMENTED` 재사용).
    - 나머지 14개 Binding은 Lookup Coordinate 부재, Case 집계 계약 미정, 또는 상류 런타임 이슈 차단으로 인해 구현 판정 조건을 미충족한다.
  - **`CODE CHANGE = NONE`**:
    - 불필요한 코드 변경 및 날조(fabrication) 방지를 위해 프로덕션/테스트 코드 변경 없이 문서 정합만 수행한다.

### 3. Materialization Coordinate 제안 (Proposed / Review Required)
- **Lifecycle Point**:
  - **`ALL_CASES_COMPLETED_PRE_SEAL` (Proposed / Review Required)**
  - 모든 필수 Case 실행 완료 및 Controlled-Variable 불변성 검증 통과 후, `RagEvaluationRun` 봉인(`result_content_manifest_hash` 계산) 직전에 `AnswerRuntimeAuthorityBindingManifest` 생성을 제안한다.
  - 책임 리뷰어(`@phina-io`)의 정식 승인 전까지는 확정(Frozen/Approved)이 아닌 제안(Proposed) 상태로 유지한다.
- **Case Aggregation Rule (Proposed / Review Required)**:
  - Controlled-Variable Invariant: 모든 필수 Case에서 관측된 controlled value가 단일 동일 값이어야 한다 ($\forall c \in \text{CompletedRequiredCases}, \, \text{value}(c) == \text{value}_0$).
  - 케이스별 값 불일치(Drift) 또는 누락 발생 시 즉시 `fail-closed` (`EvaluationValidationError(STATE_COMBINATION_INVALID)`).
  - 현재 `develop`에 해당 집계/검증 커널이 부재하므로 계약 공백(Contract Gap)으로 기록하며, 단일 케이스 값을 전체 Run 값으로 임의 과승격하지 않는다.
- **Storage Architecture 및 Publication Coordinate (철회 및 미확정 명시)**:
  - 기존의 `<run_dir>/answer_runtime_binding_manifest.json` 직접 발행 안은 **철회**한다.
  - 현재 `ContentArtifactPath` (Schema Set 1.5) 및 `publisher.py`의 번들 화이트리스트에 해당 파일명이 존재하지 않으므로, 승인 없이 번들 내 파일명을 확정할 수 없다.
  - 상태: **`MATERIALIZATION_TIMING_PROPOSED`** 및 **`STORAGE_COORDINATE_UNRESOLVED`**.
  - **엄격 금지 사항**:
    - `ContentArtifactPath` 무승인 확장 금지
    - `publisher.py` 번들 화이트리스트 무승인 수정 금지
    - Schema Set 변경 금지
    - `RagEvaluationRun` 스키마 필드 추가 금지 (Option B 기각 유지)
    - 임의 sidecar 경로 확정 금지

### 4. 상류 권위 및 인접 이슈 정합 (Reconciliation)
1. **#806 Request Guard Runtime Binding**:
   - `SqlAlchemyRequestGuardRuntimeBindingReader.read_exact()`는 `RequestGuardRuntimeBindingRef(artifact_code, version, content_sha256)`를 필수로 요구하나, `RagEvaluationRun`에는 해당 ref 필드가 부재하다.
   - `RagEvaluationRun`의 런타임 필드는 `runtime_eligible=True` + `END_TO_END_RAG` + `LOCAL` 전용이므로 Answer Quality에서 재사용 불가하며, `candidate_guard_decision_id`는 UUID일 뿐 ref가 아니다.
   - 따라서 현 상태는 **`RUNTIME_BUNDLE_SOURCE_EXISTS BUT EVALUATION_RUN_LOOKUP_COORDINATE_MISSING`**으로 유지한다.
2. **#807 / #853 Citation Approval Pin**:
   - #807은 `PATIENT_CITATION` Source Use Approval 정본 권위이고 #853은 런타임 번들 매니페스트 v2 핀 권위이다.
   - `SourceUseApproval != Citation Authorization != CITATION_GATE binding`이므로, #807/#853을 #159 CITATION_GATE로 승격하지 않고 15축 스키마를 불변으로 유지한다.
3. **#799 및 #180 Blocker 유지**:
   - Issue #799(Citation Authorization) 및 Issue #180(Final Validator, Safety Gate, Release Gate)은 계속 OPEN 및 상류 차단 상태를 유지한다.
   - `release_gate.py` 평가 게이트를 런타임 Release Gate authority로 혼용·재사용하는 것을 엄격히 금지한다.
4. **#860 Guide·Chat Backend Public Projection Seam (develop `8f297105`)**:
   - Guide·Chat Backend public projection seam이 추가되었으나, 이는 #180 final Authorized/Release result의 향후 소비 경계만 정의한다.
   - Answer Runtime authority carrier, Citation Authorization production receipt, `FINAL_VALIDATOR` authority, `CITATION_GATE` authority, `SAFETY_GATE` authority, `RELEASE_GATE` authority를 일체 제공하지 않는다.
   - 따라서 Phase C-1 15-binding audit 결과와 `IMPLEMENTABLE NOW = NONE` 판정에는 변화가 없다.

### 5. 후속 이슈 관리 및 작업 상태 (Next Steps)
- **후속 이슈 분리 원칙**:
  - Case-level Provenance Aggregation: 새 이슈를 생성하지 않고 **#159 Phase C-2**의 작업 범위로 유지한다.
  - Evaluation Run ↔ #806 Bridge: 새 이슈를 생성하지 않고 **#162**에 discovery comment로 연결한다.
- **작업 상태**:
  - Issue #159는 본 Phase C-1 PR 이후에도 계속 **OPEN** 상태로 유지된다.

## 공개 경계

이 후보의 승인이나 DEV 구현은 `PUBLIC_TRACK_F`를 해제하지 않는다. Answer Quality 통과만으로도 공개할
수 없으며 RAG-EVAL-005~008, RAG-15/16 Runtime 통합과 외부 의료·약학·Source·Privacy·Safety 승인이
별도로 필요하다.
