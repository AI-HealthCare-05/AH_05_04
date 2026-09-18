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

### 1. 배경 및 PR #823 완료 상태 반영

1. **PR #823 완료 반영**: PR #823(`3fa3e604d3df8512dffa49f733aef24e97acc1d0`)이 책임 리뷰어 권가빈(`@hazelnutflavoured`)의 승인을 받아 `develop`에 병합 완료됨에 따라, Issue #159의 남은 과제 중 "approved/versioned Answer Comparison Policy"(`rag-answer-quality-dev-comparison@1.0.0`) 항목은 충족 완료되었다. Issue #159는 Runtime Authority Extraction 및 후속 바인딩 구현을 위해 **OPEN** 상태를 유지한다.
2. **Phase A 목적**: Answer 3-Pair Comparison에서 요구되는 15개 authority binding(Supplemental controlled variables 7개 + Allowed delta axes 8개)에 대해, 평가 코드가 미완성 #180/#799 authority를 추정 구현하여 재작업하는 위험을 방지하기 위해, 현재 `develop` 실제 authority 전수 대조 및 canonical source / projection / hash recipe를 검토하고 미확정 항목의 blocker owner를 명시한다.
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
| `RUNTIME_BUNDLE` | `rag_runtime_release_bundle` (`id`, `bundle_manifest_hash`, `environment_code`) / #806 `OriginRequestGuardBinding` | #810 (PR #822) / #806 | Bundle 빌드/릴리스 및 per-request 런타임 바인딩 시점 | 후보 (Candidate): `(bundle_id, bundle_manifest_hash)` + canonical `environment_code` | 상류 차단 (#822 병합 및 #806 authority schema 완료 필요) | DB model 존재하나 환경 영속화(PR #822) 및 per-request 바인딩(#806) OPEN | `BLOCKED_BY_UPSTREAM_AUTHORITY` | PR #822 / Issue #810 (병합 대기), Issue #806 (OPEN) |
| `RETRIEVED_EVIDENCE` | `CaseResult.selected_evidence_ids` / `VerifiedGuideEvidenceHandoff` / `ProductionGuidelineEvidenceSet` | #159 / #180 (#760) | Evidence handoff 조립 및 projection 시점 | 미확정 (raw retrieval hits vs post-gate selected evidence; Run-level 집계) | 미확정 | Case 단위 evidence ID 존재, canonical stage 및 Run 집계 recipe 미확정 | `SOURCE_EXISTS_RECIPE_UNRESOLVED` | #159 canonical evidence stage (selected vs retrieved) 및 Run 집계 확정 |
| `FINAL_VALIDATOR` | 후보 소스: `GuidelineGenerationProvenance.validator_ref`, Claim-Citation validation/finalization authority | #180 | Guideline card 완료 및 Claim-Citation 검증 시점 | 상류 차단 (`validator_ref`는 실재하나 `ANS-RAG -> ANS-FINAL`의 `FINAL_VALIDATOR` 정본 동일시 계약 부재) | 상류 차단 | `validator_ref` 및 순수 검증 로직 존재하나 정본 validator immutable identity 미확정 | `BLOCKED_BY_UPSTREAM_AUTHORITY` | #180에서 ANS-RAG → ANS-FINAL 경계의 FINAL_VALIDATOR 의미와 immutable identity 확정 필요 |
| `CITATION_GATE` | `CitationAuthorizationReceipt` | #799 / #806 / #807 (#180) | Citation authorization receipt 발급 시점 | 상류 차단 (production receipt 발급은 #806 REQUEST Guard Decision 및 #807 PATIENT_CITATION Approval 전제) | 상류 차단 | 순수 검증 함수 존재, production receipt authority 및 영속화 미구현 | `BLOCKED_BY_UPSTREAM_AUTHORITY` | Issue #799 (OPEN), Issue #806 (OPEN), Issue #807 (OPEN) |
| `SAFETY_GATE` | 목표/후보: #180 Runtime Safety Gate decision / receipt (canonical immutable runtime Safety Gate authority not implemented; Evaluation Safety metric 재사용 금지) | #180 | 런타임 safety disposition 및 release 평가 시점 | 상류 차단 (`ANS-RAG -> ANS-FINAL` 경계 실행 Safety Gate 식별 immutable authority 미확정) | 상류 차단 | Safety 정책/risk level 존재, production safety gate receipt authority 미확정 | `BLOCKED_BY_UPSTREAM_AUTHORITY` | #180 runtime Safety Gate authority identity / receipt 확정 |
| `RELEASE_GATE` | 목표/후보: #180 Runtime Release Gate decision / receipt (canonical immutable runtime Release Gate authority not implemented; `ai_worker/tasks/evaluation/release_gate.py` 평가 릴리스 게이트와 도메인 엄격 분리) | #180 | 최종 런타임 릴리스 게이트 평가 시점 | 상류 차단 (`PASS | LIMITED | REJECTED | STALE` publication decision exact-bind immutable authority 미확정) | 상류 차단 | Release decision enum 존재, production release gate authority receipt 미구현 | `BLOCKED_BY_UPSTREAM_AUTHORITY` | #180 final runtime release authority / receipt 확정 |

### 3. 상태별 분류 요약

- **`READY` (0개)**:
  - 현재 승인된 정본 계약 기준으로 4대 조건(source 존재, owner 명확, projection 승인 완료, recipe 승인 완료)을 충족하는 항목 없음. Phase A에서 제안된 candidate recipe는 reviewer 승인 전까지 `SOURCE_EXISTS_RECIPE_UNRESOLVED`로 유지함.
- **`SOURCE_EXISTS_RECIPE_UNRESOLVED` (10개)**:
  - `INPUT_CONTEXT`, `PROMPT_STRUCTURE`, `PARSER`, `SEED`, `SAMPLING_PARAMETERS`, `TOKEN_LIMIT`, `TIMEOUT`, `RETRIEVAL_PIPELINE`, `SOURCE_INDEX`, `RETRIEVED_EVIDENCE`:
  - 소스 객체 또는 파라미터가 존재하나, 변형 간 canonical projection, 화이트리스트, 비검색 baseline(`ANS-BASE`) 표현, 또는 Decision 승인 절차가 미완료되어 평가 코드가 임의 추정 구현을 방지해야 하는 항목.
- **`BLOCKED_BY_UPSTREAM_AUTHORITY` (5개)**:
  - `RUNTIME_BUNDLE` (PR #822 / #810, #806 차단)
  - `FINAL_VALIDATOR` (#180 차단)
  - `CITATION_GATE` (#799, #806, #807 차단)
  - `SAFETY_GATE` (#180 차단)
  - `RELEASE_GATE` (#180 차단)
  - 상류 도메인의 정본 계약 및 프로덕션 authority receipt가 완성되지 않았으므로, 평가 계층에서 가상 authority를 합성하지 않고 명시적으로 차단 상태를 유지함.

### 4. Storage Architecture 권고안: Separate Binding Manifest Preferred

Phase A 전수 대조 결과, authority binding의 저장 아키텍처는 다음 권고를 확정한다:

- **Recommended storage**: `SEPARATE_BINDING_MANIFEST_PREFERRED`
- **Implementation decision**: `PENDING_UPSTREAM_AUTHORITY_FREEZE`

#### 권고 근거
1. **상류 런타임 도메인 격리**: `RUNTIME_BUNDLE`, `FINAL_VALIDATOR`, `CITATION_GATE`, `SAFETY_GATE`, `RELEASE_GATE` 등 8개 delta 중 5개가 런타임 도메인(#180, #799, #806, #807)의 authority receipt를 참조한다. 이들 상류 authority는 현재 진행 중이며 점진적으로 정착되므로, `RagEvaluationRun`에 직접 종속시키면 상류 변화마다 평가 스키마가 영향을 받는다.
2. **Schema Set 1.5 불변 유지 및 Version Churn 방지**: `RagEvaluationRun`은 이미 승인 고정된 Schema Set 1.5(`cf481556cead9f99e4d424481e9ed5aed246899a4c893c45b19a2b7abcb89dc8`)의 핵심 엔티티다. 15개 hash 필드를 `RagEvaluationRun`에 추가하면 `rag-eval.run` 스키마 버전 상향, 기존 러너·로더·마이그레이션 전체에 연쇄 churn이 발생한다.
3. **기존 Answer Comparison 아키텍처 정합성**: PR #808에서 이미 `AnswerComparisonRunInput` 및 `AnswerComparisonSupplementalControls` / `AnswerComparisonDeltaBindings`를 분리된 typed seam으로 설계하여, 외부 binding manifest를 자연스럽게 수용할 수 있는 기반이 마련되어 있다.

단, 본 단계에서는 추천 방향만 기록하며 새 schema, DTO 또는 Python 코드는 일절 구현하지 않는다 (`Implementation: NOT STARTED`).

### 5. Phase B 착수 조건

1. `READY` 항목 (0개): 현재 즉각 구현 대상 없음.
2. `SOURCE_EXISTS_RECIPE_UNRESOLVED` 항목 (10개): `SEED`를 포함하여 candidate recipe 및 변형 간 projection에 대한 #159 Decision 책임 리뷰어 승인 완료 시 구현 착수.
3. `BLOCKED_BY_UPSTREAM_AUTHORITY` 항목 (5개): 각 upstream Issue(#822/#810, #806, #807, #799, #180)의 정본 receipt 계약 및 영속화가 완료되어 PR 병합된 이후에만 평가 바인딩 구현 착수.

## 공개 경계

이 후보의 승인이나 DEV 구현은 `PUBLIC_TRACK_F`를 해제하지 않는다. Answer Quality 통과만으로도 공개할
수 없으며 RAG-EVAL-005~008, RAG-15/16 Runtime 통합과 외부 의료·약학·Source·Privacy·Safety 승인이
별도로 필요하다.
