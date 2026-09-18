# RAG Answer Quality Metric·Variant 계약 v1

| 항목 | 값 |
| --- | --- |
| 상태 | Approved Target |
| 구현 | Partially implemented — 구조화 Metric·DEV manifest routing 구현, human judgment·3-pair comparison 미구현 |
| Decision | [`PD-159-20260913`](../../../governance/decisions/2026-09-13-rag-answer-quality-metrics.md) |
| 추적 Issue | [#159](https://github.com/AI-HealthCare-05/AH_05_04/issues/159) |
| 구현 담당 | 정현우 (`@ceohwj`) |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — `APPROVED` |
| 승인 Evidence | [PR #475 review `5190544119`](https://github.com/AI-HealthCare-05/AH_05_04/pull/475#pullrequestreview-5190544119) · [`decision-approval-evidence.json`](../../../validation/rag/issue-159/decision-approval-evidence.json) |

## 1. 목적과 권위

이 문서는 `RAG-EVAL-004`의 Answer Quality DEV metric, Answer Variant, human-rubric 입력과 pair comparison
경계를 고정한 Approved Target이다. 구현·schema export·자동 테스트가 함께 병합되기 전에는 Current Runtime
계약이 아니다.

기존 [RAG Evaluation·Release Gate 목표 계약](../../targets/post-mvp-1/rag-evaluation-v1.md)의 상태축,
민감정보 금지, HOLDOUT 접근 통제와 Release 차단 규칙을 유지한다. 이 계약과 기존 Target이 충돌하면
새 Decision 또는 Contract Freeze로 정렬하기 전까지 구현을 차단한다.

## 2. 비목표

- Citation Precision/Coverage/Entailment와 Unsupported Claim 판정
- Safety Router·Rule-first·최종 Runtime Release 판정
- scorer 내부의 자연어 의미 추정 또는 비승인 LLM Judge 사용
- Human/LLM reasoning 전문 저장
- 새 3-way comparison schema
- RAG-15/16 Artifact 전 Runtime 세 Variant 실행 또는 HOLDOUT 결과 관찰

## 3. Answer Variant

`AnswerVariantId`는 다음 세 값만 허용한다.

| 값 | 의미 | 환자 공개 |
| --- | --- | --- |
| `ANS-BASE` | 명시적으로 고정한 pre-RAG 평가 전용 Baseline | 금지 |
| `ANS-RAG` | Retrieval·Generator 결과이며 최종 Runtime Release 경계 전 비교 Variant | 금지 |
| `ANS-FINAL` | RAG-16 Validator·Citation·Safety·Release 경계를 통과한 비교 Candidate | 평가 결과 자체는 금지 |

Answer 전용 config/schema 모델의 `variant_id`만 이 enum을 사용한다. Retrieval Variant의 기존 stable ID와
replay 계약은 변경하지 않는다. `variant_id` 문자열만 바꿔 같은 Artifact를 다른 Variant로 위장할 수 없도록
각 Run은 해당 Answer Variant manifest hash를 exact-match한다.

## 4. Human-rubric judgment 입력

`ANSWER_CORRECTNESS`와 `RELEVANCE`는 승인된 immutable judgment artifact만 소비한다. scorer는 답변 원문,
claim text 또는 reasoning을 읽어 label을 생성하지 않는다.

judgment record는 최소 다음 값에 결속한다.

- schema ID/version과 judgment ID/version
- `run_id`, `case_id`, `AnswerVariantId`
- Dataset Manifest hash와 Case input hash
- Answer Artifact의 `answer_sha256`
- Critical Claim Rubric ID/version/hash
- claim별 `claim_id`와 `CORRECT | INCORRECT` label
- Case 단위 `RELEVANT | IRRELEVANT` label
- reviewer identity, reviewed timestamp와 immutable approval evidence reference
- record self-hash

선택된 partition·slice에서 실행 완료된 `ANSWER_QUALITY` Case마다 승인된 judgment record가 정확히 하나
있어야 한다. 전체 record가 0개면 해당 human metric은 `NOT_EVALUATED/null`이다. 일부 Case만 존재하거나
중복·추가 Case가 있으면 분모를 축소하지 않고 `INVALID/null`로 처리한다.

각 record의 claim judgment ID 집합은 해당 Case Result의 `actual_claim_ids`와 exact-match해야 한다. 일부
claim만 판정한 artifact, 중복 claim, 다른 Answer hash·Variant·Case·Rubric에 결속한 artifact도 전체 입력을
`INVALID/null`로 처리한다. Loader는 record self-hash뿐 아니라 approval evidence reference가 가리키는
immutable resource의 file/content hash와 승인 identity를 exact-match한다. 검증하지 못한 reference를
문자열 존재만으로 승인 증빙으로 수용하지 않는다. 빈 judgment를 0점 또는 `FAIL`로 바꾸지 않는다.

자유서술 reasoning, Provider 원문, 사용자 질문·답변 원문과 실제 환자정보는 judgment artifact와 로그에
저장하지 않는다. 필요한 검토 사유는 승인 계약의 bounded reason code만 사용한다.

## 5. Metric formula

모든 Metric version은 `1.0.0`이다. `metric_value`는 `numerator / denominator`의 6자리
half-even canonical decimal이다. Case별 기여값도 분자·분모 쌍으로 유지하며 평균의 평균을 계산하지 않는다.

`ComparisonScope.unit_of_analysis`와 `MetricResult.unit_of_analysis`에는 CI의 표본 또는 재표집 단위가 아니라
아래 표의 Metric별 분모·분석 단위를 기록한다. 두 Artifact의 값은 exact-match해야 하며 Answer Metric에는
각각 `CLAIM | REQUIRED_CLAIM | CASE | EXPECTED_SECTION` 중 해당 값만 허용한다.

| Metric ID | 분석 단위 | 분자 | 분모 |
| --- | --- | --- | --- |
| `ANSWER_CORRECTNESS` | `CLAIM` | 승인 judgment가 `CORRECT`인 Actual claim 수 | 승인 완료된 Actual claim 수 |
| `REQUIRED_CLAIM_RECALL` | `REQUIRED_CLAIM` | `actual_claim_ids`에 존재하는 required Gold claim 수 | required Gold claim 수 |
| `RELEVANCE` | `CASE` | 승인 judgment가 `RELEVANT`인 Case 수 | 승인 완료된 Case 수 |
| `COMPLETENESS` | `EXPECTED_SECTION` | `actual_sections`에 존재하는 expected section 수 | expected section 수 |

`ANSWER_CORRECTNESS`는 Unsupported Claim·Citation Entailment를 대신하지 않는다. `COMPLETENESS`는 required
claim 회수를 중복 계산하지 않으며 section 구조의 충족만 측정한다. `omitted_sections`의 금지·누락 의미는
별도 Safety/Grounding metric이 소유하며 Completeness 분자에 합치지 않는다.

## 6. 입력 정합성과 상태

Metric builder는 선택된 partition·slice의 `ANSWER_QUALITY` Case와 Case Result를 exact set으로 검증한다.
중복·누락·추가 Result, Run ID 혼합, Dataset/Case/input hash 불일치 또는 잘못된 task type은 관련 Metric을
`INVALID/null`로 처리한다.

미완료 Case Result가 있으면 기존 실행 상태 우선순위 `INVALID > ERROR > NOT_IMPLEMENTED > NOT_EVALUATED`를
보존하고 decision을 만들지 않는다. human judgment 부재는 두 human metric에만 `NOT_EVALUATED/null`을
적용하며 구조화된 두 Metric 계산을 막지 않는다.

완료 입력의 상태는 다음과 같다.

| 조건 | execution status | decision status | reason code |
| --- | --- | --- | --- |
| 승인 Policy상 비필수 DEV diagnostic, 표본 충족 | `COMPLETED` | `N/A` | `null` |
| 분모 0 | `COMPLETED` | `INCONCLUSIVE` | `ZERO_DENOMINATOR` |
| 최소 Case 수 미달 | `COMPLETED` | `INCONCLUSIVE` | `MINIMUM_CASE_COUNT_NOT_MET` |
| 최소 독립 group 수 미달 | `COMPLETED` | `INCONCLUSIVE` | `MINIMUM_INDEPENDENT_GROUP_COUNT_NOT_MET` |
| judgment artifact 없음 | `NOT_EVALUATED` | `null` | `null` |
| judgment 결속·집합·hash 불일치 | `INVALID` | `null` | `null` |

DEV Comparison Policy는 네 Metric을 `DIAGNOSTIC_ONLY`, `required=false`, `threshold=0`으로만 구성할 수
있다. 활성 threshold와 `PASS | FAIL`은 별도 승인된 Policy version 없이는 생성하지 않는다.

## 7. 95% CI와 독립성

Comparison Policy는 Metric scope마다 다음을 고정한다.

- `estimator_id=MICRO_RATIO`, `estimator_version=1.0.0`
- `ci_method_id=PERCENTILE_CLUSTER_BOOTSTRAP`, `ci_method_version=1.0.0`
- `level=0.95`, `sidedness=TWO_SIDED`, 양의 iteration 수와 fixed seed
- `cluster_dimension`, `minimum_case_count`, `minimum_independent_group_count`

이 필드들의 직렬화 의미는 다음과 같이 분리한다. 새 CI 표본 단위 필드는 추가하지 않는다.

| 필드 | 기록하는 값 |
| --- | --- |
| `unit_of_analysis` | Metric별 분모·분석 단위. 위 표의 값이며 CI 재표집 단위가 아니다. |
| `sample_case_count` | partition·slice에 선택되어 해당 Metric의 분자·분모 기여값 쌍을 만든 Case 수 |
| `independence_unit` | 승인 Policy가 정한 독립 표본 group의 의미를 나타내는 stable ID |
| `cluster_dimension` | 각 Case에서 독립 group ID를 추출할 Dataset leakage-axis 필드 |
| `sample_independent_group_count` | `cluster_dimension`으로 추출한 distinct 독립 group ID 수 |

`ComparisonScope`의 `unit_of_analysis`, `independence_unit`, `cluster_dimension`은 계산된 `MetricResult`에
그대로 복사되어 exact-match해야 한다. `sample_case_count`는 Metric 분모와 같다는 뜻이 아니다. 예를 들어
`ANSWER_CORRECTNESS` 한 Case가 여러 `CLAIM` 분모 기여를 만들 수 있어도 Case 수는 1 증가한다.

bootstrap의 재표집 단위는 Case나 `unit_of_analysis`가 아니라 `cluster_dimension`으로 만든 distinct 독립
group이다. 승인된 leakage-axis group을 복원추출하고, 선택된 group에 속한 모든 Case의 분자·분모 기여값
쌍을 함께 포함해 micro ratio를 다시 계산한다. group을 뽑은 횟수만큼 그 group의 전체 Case 기여값도
반복 포함한다. 정렬은 기존 Evaluation canonical ordering을 사용하고 동일 seed·입력·iteration에서 같은
percentile bounds를 생성해야 한다.

분모가 0인 원표본은 CI를 만들지 않는다. 구현이 cluster 축이나 seed를 임의로 선택하지 않는다.

`ANSWER_CORRECTNESS`의 zero-claim / zero-denominator bootstrap 의미는 다음과 같다:

- zero-claim Case는 `RatioContribution(0, 0)`으로 유지하며, `0/1` 등 Case-level penalty로 변환하지 않는다. 필수 claim 누락은 `REQUIRED_CLAIM_RECALL`이 단독 소유한다.
- zero-denominator cluster도 CI sampling frame에 그대로 포함한다.
- bootstrap replicate의 합산 denominator가 0인 replicate만 제외(skip)하고, 나머지 valid replicate들로 percentile CI를 계산한다.
- zero-denominator replicate를 제외한 percentile CI는 "bootstrap 표본에서 하나 이상의 Actual claim이 포함된 조건"에 대한 conditional interval이며, unconditional CLAIM micro-ratio point estimate와 엄밀히 동일한 estimand로 해석하지 않는다. `minimum_valid_replicate_ratio`는 이 조건부 CI가 원 sampling frame에서 지나치게 작은 subset에 의존하는 것을 제한하는 DEV diagnostic safeguard다.
- scope 전체 point denominator가 0이면 `COMPLETED / INCONCLUSIVE / ZERO_DENOMINATOR`를 반환하며, `metric_value`와 CI는 `null`이다.
- valid replicate 비율이 `ComparisonPolicy.ci_parameters`의 `minimum_valid_replicate_ratio` 미만이면 `COMPLETED / INCONCLUSIVE`로 판정하고 reason code는 `MINIMUM_VALID_BOOTSTRAP_REPLICATE_RATIO_NOT_MET`를 사용한다. 단, 계산된 `metric_value`와 valid replicate가 존재할 때의 CI bounds는 유지한다.
- 이 CI sampling frame divergence(`ANSWER_CORRECTNESS`는 frame 유지·replicate 제외 vs `GROUNDING`/`SAFETY`는 positive-denominator cluster 사전 제외)는 기존 지표 의미 보존을 위한 의도적 결정이다.
- total/valid/excluded replicate 수와 valid ratio는 machine `MetricResult`나 Schema Set 1.5에 추가하지 않고, non-schema `report.md` diagnostic projection으로만 제공된다. diagnostic projection capability는 구현되나 실제 Run report emission은 authoritative human-judgment runtime wiring 완료 시점까지 유예된다. 이 진단값은 Release Gate나 자동화 판정의 입력이 아니다.

`RELEVANCE`, `REQUIRED_CLAIM_RECALL`, `COMPLETENESS` 등 다른 Answer 지표는 기존 3개 CI parameter(`iterations`, `level`, `sidedness`) 계약을 유지하며, bootstrap replicate에서 분모 0이 발생하는 Policy는 지원하지 않고 `NOT_IMPLEMENTED`로 처리한다.

## 8. 세 Variant pair comparison

기존 `rag-eval.comparison`의 baseline/candidate 두 Run schema를 재사용해 다음 세 Artifact를 독립 생성한다.

1. `ANS-BASE -> ANS-RAG`
2. `ANS-RAG -> ANS-FINAL`
3. `ANS-BASE -> ANS-FINAL`

현재 Retrieval 전용 comparison validator/builder를 Answer에 재사용한다고 주장하지 않는다. #159 구현은
Answer Variant manifest hash를 검증하는 Answer 전용 pair validator/builder를 추가하되, 공통 delta 계산과
`rag-eval.comparison` artifact schema는 재사용한다.

현재 Run bundle의 `ContentArtifactPath`와 `machine_artifact_files()`는 candidate Run당 `comparison.json` 하나만
허용하므로 세 pair를 Run bundle 안에 저장하지 않는다. 다음 experiment-level 외부 bundle을 사용한다.

```text
evals/results/<experiment_id>/answer-comparisons/
  ans-base--ans-rag/comparison.json
  ans-rag--ans-final/comparison.json
  ans-base--ans-final/comparison.json
  comparison-set-manifest.json
```

`rag-eval.answer-comparison-set-manifest@1.0.0`은 `experiment_id`, Dataset/Partition/Gold/Rubric/Metric Policy
reference, 정확히 세 pair의 baseline/candidate Variant와 Run ID, 상대 경로, comparison SHA-256, semantic
hash, pair별 `allowed_delta_keys`, manifest self-hash를 가진다. 세 pair key는 위 목록과 exact-match해야 하며
누락·중복·추가 pair, 경로 탈출, file/hash 불일치를 거부한다. 각 pair는 개별
`rag-eval.comparison@1.0.0`이며 새 3-way 결과 schema를 만들지 않는다.

모든 pair가 exact-match해야 하는 mandatory controlled-variable key는 다음과 같다.

- `CASE_SET`, `DATASET`, `PARTITION`, `GOLD`, `RUBRIC`, `METRIC_POLICY`
- `INPUT_CONTEXT`, `MODEL_CONFIGURATION`, `PROMPT_STRUCTURE`, `PARSER`
- `SEED`, `SAMPLING_PARAMETERS`, `TOKEN_LIMIT`, `TIMEOUT`

pair별 허용 delta는 다음 exact set으로 제한한다.

| Pair | `allowed_delta_keys` | 해석 |
| --- | --- | --- |
| `ANS-BASE -> ANS-RAG` | `RETRIEVAL_PIPELINE`, `SOURCE_INDEX`, `RUNTIME_BUNDLE`, `RETRIEVED_EVIDENCE` | RAG 도입 효과 비교 |
| `ANS-RAG -> ANS-FINAL` | `FINAL_VALIDATOR`, `CITATION_GATE`, `SAFETY_GATE`, `RELEASE_GATE` | 최종 검증·공개 경계 효과 비교. `ANS-FINAL` 입력 draft answer hash는 `ANS-RAG` 출력 hash와 exact-match |
| `ANS-BASE -> ANS-FINAL` | 위 두 pair 허용 delta의 합집합 | 전체 효과 요약. 개별 단계 귀속 근거로 사용 금지 |

`allowed_delta_keys`의 의미는 pair별 exact set이며 배열의 직렬화 순서는 의미를 갖지 않는다. 후속 deterministic builder는 재현 가능한 출력 순서를 선택할 수 있지만 consumer validation은 순서에 의존하지 않는다.

각 Run의 Git commit과 Answer Variant manifest hash는 기록하되 matched variable로 위장하지 않는다. 허용 목록
밖의 관찰 delta, mandatory key mismatch 또는 허용 delta의 필수 binding 누락은 `INVALID/null`이다. 세 pair
중 required pair가 없거나 미완료면 상위 비교·Gate를 `PASS`로 만들 수 없다.

### 8.1 Product Decision 5725554244 (Option 2: Controlled Variable Binding Seam)

Post-MVP-1 Product·Safety·Evaluation 승인자 권가빈 (`@hazelnutflavoured`)의 승인 결정([#159 comment 5725554244](https://github.com/AI-HealthCare-05/AH_05_04/issues/159#issuecomment-5725554244))에 따라 PR C의 Answer comparison 입력 바인딩 경계를 다음과 같이 확정한다.

1. **Option 2 Pure Typed Seam**: 14개 controlled variable 전체를 typed seam으로 표현한다.
2. **Run-derived 7 controls**: 현재 `RagEvaluationRun`에서 canonical source가 명확한 7개 key는 builder/seam 내부에서 직접 바인딩하며 caller가 override하지 못한다:
   - `CASE_SET` = `run.partition_manifest_hash`
   - `DATASET` = `run.dataset_manifest_sha256`
   - `PARTITION` = `canonical_sha256([partition.value for partition in run.evaluated_partitions])`
   - `GOLD` = `canonical_sha256({"resource_set_hash": run.resource_set_hash, "evidence_mapping_manifest_sha256": run.evidence_mapping_manifest_sha256})`
   - `RUBRIC` = `run.critical_claim_rubric_ref.hash`
   - `METRIC_POLICY` = `run.comparison_policy_ref.hash`
   - `MODEL_CONFIGURATION` = `run.model_config_hash`
3. **Supplemental 7 controls**: 현재 canonical extraction recipe가 없는 7개 key(`INPUT_CONTEXT`, `PROMPT_STRUCTURE`, `PARSER`, `SEED`, `SAMPLING_PARAMETERS`, `TOKEN_LIMIT`, `TIMEOUT`)는 caller-supplied arbitrary mapping으로 받지 않고 명시적 typed supplemental input(`AnswerComparisonSupplementalControls`)으로 받는다.
4. **Delta-axis authority extraction**: 8개 delta axis(`RETRIEVAL_PIPELINE`, `SOURCE_INDEX`, `RUNTIME_BUNDLE`, `RETRIEVED_EVIDENCE`, `FINAL_VALIDATOR`, `CITATION_GATE`, `SAFETY_GATE`, `RELEASE_GATE`)는 typed input(`AnswerComparisonDeltaBindings`)으로 수신하며, 실제 authoritative extraction recipe 및 runtime wiring은 후속 PR에서 확정한다.
5. **Policy Contract Test**: `ANSWER_CORRECTNESS` scope의 `minimum_valid_replicate_ratio = "0.9"` 존재를 contract test로 고정한다.
6. **Schema Set 1.5 불변**: 위 결정은 Schema Set 1.5(`cf481556cead9f99e4d424481e9ed5aed246899a4c893c45b19a2b7abcb89dc8`)를 변경하지 않는다.

### 8.2 PR C Deterministic Implementation Convention

다음 세부 사항은 Product 승인 사항이 아닌 PR C의 결정적 구현 규약(Deterministic Implementation Convention)이다:

1. **`comparison_sha256`**:
   - `ComparisonResult` 전체 payload의 canonical JSON 직렬화 바이트(`canonical_json_bytes(comparison.model_dump(mode="json"))`)의 SHA-256 hex 값이다.
2. **`comparison_semantic_hash`**:
   - Run 실행 시마다 변하는 고유 식별자(`run_id`, `baseline_run_id`, `candidate_run_id`) 3개 필드를 제외하고, 비교 의미를 구성하는 나머지 필드(`schema_id`, `schema_version`, `experiment_id`, `baseline_run_hash`, `candidate_run_hash`, `controlled_variable_checks`, `scope_comparisons`, `execution_status`, `decision_status`)를 포함하는 canonical payload의 SHA-256 hex 값이다.
3. **내부 Typed 구조**:
   - Supplemental controls, delta bindings, draft answer bindings, 14개 resolved controls, Run input은 typed dataclass로 표현하며 `dict[str, str]` arbitrary mapping을 금지한다.
4. **INVALID 상태 표현 규칙**:
   - Authority binding 누락(`None`): `controlled_variable_checks = ()`, `scope_comparisons = ()`, `execution_status = INVALID`, `decision_status = null`
   - 14개 control mismatch: 14개 checks를 구성하고 불일치 항목의 `matched = false` 기록, `scope_comparisons`는 계산 가능하면 유지, `execution_status = INVALID`, `decision_status = null`
   - Non-allowed delta mismatch 또는 Case/Draft mismatch: Schema 변경 없이 invariant(`not scope_comparisons`)를 만족하도록 `scope_comparisons = ()`를 설정해 `execution_status = INVALID`, `decision_status = null` 표현
   - Malformed typed input(예: non-hex SHA, 잘못된 길이 등): artifact를 생성하지 않고 `EvaluationValidationError`로 fail-close

## 9. Rubric fail-fast

Dataset Loader의 Critical Claim Rubric self-hash와 Dataset/Case reference exact-match 검증을 정본으로
재사용한다. #159는 별도 중복 validator를 만들지 않는다. Rubric mismatch는 Adapter resolve/execute와 Run
Artifact 생성 전에 `RUBRIC_MISMATCH`로 거부하며 다음을 자동 테스트한다.

- Answer Adapter 호출 0회
- `run.json`, `cases.jsonl`, `metrics.json`, comparison artifact 생성 0개
- 실제 Rubric·Case·Answer 원문 비로그

## 10. 구현 경계

승인 뒤 Phase B의 최소 변경은 다음과 같다.

- `ai_worker/tasks/evaluation/answer_metrics.py`
- `ai_worker/tasks/evaluation/manifest.py`의 `ANSWER_QUALITY` routing
- Answer Variant와 judgment artifact의 Pydantic/schema export
- `ai_worker/tests/evaluation/test_answer_metrics.py`
- config·loader·comparison·CLI의 필요한 fail-closed 회귀 테스트
- 승인된 DEV Comparison Policy와 합성 judgment fixture

기존 fixed-seed bootstrap을 함께 소비해야 하면 behavior-preserving 공통 통계 helper만 추출한다. Strategy,
Registry, Plugin, DB, API, Queue, cache와 새 dependency를 추가하지 않는다.

RAG-15/16 versioned Artifact 전에는 `BLOCKED_BY_RAG_RUNTIME_ARTIFACT`, 승인 Comparison/Evaluation Policy
전에는 `WAITING_FOR_APPROVED_COMPARISON_POLICY`를 유지한다. HOLDOUT을 load·execute하거나 결과를 관찰하지
않는다.

## 11. 최소 검증

- 손계산 가능한 네 Metric의 numerator·denominator·canonical value
- 서로 다른 Case별 분모에서 micro ratio와 평균의 평균이 달라지는 회귀 사례
- fixed-seed cluster bootstrap 반복 결정성
- `ComparisonScope`와 `MetricResult`의 Metric별 `unit_of_analysis` exact-match 및 CI group 필드의 분리
- Case 수가 서로 다른 독립 group fixture에서 Case 재표집이 아닌 group 재표집임을 검증
- partition·slice의 Case 수와 distinct 독립 group 수 집계
- `required=false` DEV diagnostic과 `required=true` scope 각각의 분모 0, 최소 Case·최소 독립 group 미달 `INCONCLUSIVE`
- human judgment 없음의 `NOT_EVALUATED/null`
- judgment Case exact-set·claim exact-set·approval evidence·Answer hash·Variant·Rubric mismatch의 `INVALID/null`
- incomplete Case Result 실행 상태 전파
- Answer Variant allowlist와 Retrieval Variant 비회귀
- Rubric mismatch 실행 전 Adapter·Artifact 0건
- 세 Answer pair와 comparison-set manifest의 exact-set·경로·identity·hash·controlled variable·허용 delta 검증
- 실제 환자정보·질문/답변·Provider 원문·reasoning 전문 비저장

## 12. 승인과 공개

PR #475의 최종 HEAD `ca27931f`에서 책임 리뷰어가 실제 `APPROVED` review를 제출해 이 문서는 Approved
Target으로 전이했다. 구현 PR은 schema/export parity, 관련 Evaluation 전체 테스트, Ruff, format, Mypy와
의료·AI eval 회귀 증빙을 포함한다.

문서 승인, DEV 구현, Runtime 통합, HOLDOUT 실행, Release `PASS`와 `PUBLIC_TRACK_F`는 서로 다른 상태다.
RAG-EVAL-005~008, RAG-15/16과 외부 승인이 완료되기 전에는 공개하지 않는다.
