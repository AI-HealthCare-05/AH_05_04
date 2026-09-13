# Product Decision Candidate: RAG Answer Quality Metric·Variant 계약

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-159-20260913` |
| 상태 | Candidate · Review Required |
| 제안일 | 2026-09-13 |
| 제안자·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Product·Safety·Evaluation 계약 승인 |
| 추적 Issue | [#159](https://github.com/AI-HealthCare-05/AH_05_04/issues/159) |
| 적용 범위 | Post-MVP-1 Track F Answer Quality DEV metric, variant, comparison 입력 계약 |

## 후보 결정

[#159 Answer Quality 계약 제안](../../contracts/proposed/post-mvp-1/rag-answer-quality-metrics-v1.md)을
RAG-EVAL-004의 구현 전 검토 대상으로 제안한다. 이 후보는 다음 경계를 함께 고정한다.

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

책임 리뷰어의 실제 Pull Request review event가 기록되기 전에는 이 후보를 Approved Target, 구현 승인,
활성 Metric 또는 Release `PASS` 근거로 취급하지 않는다.

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

이 Decision과 연결 계약은 `Candidate/Proposed`다. 책임 리뷰어가 formula·human judgment 경계·Variant·pair
비교를 승인하면 같은 PR에서 상태와 계약 경로를 저장소 문서 권위 규칙에 맞게 갱신한다. 구현·schema
export·자동 테스트가 없는 문서 승인만으로 `current/`로 승격하지 않는다.

## 공개 경계

이 후보의 승인이나 DEV 구현은 `PUBLIC_TRACK_F`를 해제하지 않는다. Answer Quality 통과만으로도 공개할
수 없으며 RAG-EVAL-005~008, RAG-15/16 Runtime 통합과 외부 의료·약학·Source·Privacy·Safety 승인이
별도로 필요하다.
