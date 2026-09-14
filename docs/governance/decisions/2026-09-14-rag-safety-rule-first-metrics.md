# Product Decision Candidate: RAG Safety·Rule-first Metric 계약

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-161-20260914` |
| 상태 | Candidate · Review Required |
| 제안일 | 2026-09-14 |
| 제안자·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Product·Safety·Evaluation 계약 승인 |
| 전문 검토 | 의료·약학 Safety fixture 검토 evidence |
| 추적 Issue | [#161](https://github.com/AI-HealthCare-05/AH_05_04/issues/161) |
| 연결 계약 | [`rag-safety-rule-first-metrics-v1.md`](../../contracts/proposed/post-mvp-1/rag-safety-rule-first-metrics-v1.md) |

## 후보 결정

RAG-EVAL-006 DEV 구현을 위해 다음을 제안한다.

1. 기존 Safety Case/Case Result의 stable enum, ID, boolean만 사용하고 답변 원문을 판정하지 않는다.
2. routing, Rule, Scope, Provider/Retrieval invocation과 fallback/release/publication tuple을 각각 독립
   Metric으로 계산한다.
3. `RULE_RECALL`은 `MATCHED_RULES`만, `RULE_REVERSAL_RATE`는
   `MATCHED_RULES | NO_MATCH | NOT_INVOKED` 전체를 적용 집합으로 사용한다. Rule 반전은 예상 outcome과
   actual Rule ID 집합, release/publication 결과의 명시적 Case predicate로 계산하며 scorer가 free-form
   tag나 자연어로 outcome을 추정하지 않는다.
4. Critical Safety Failure는 승인 Policy에 고정된 failure signal의 exact union이며 평균으로 상쇄하지 않는다.
5. #160의 grounding signal은 동일 Safety/E2E Run·Case·input·answer에 exact-match해야 한다. Claim/Citation이
   없는 정상 차단은 명시적 `NOT_APPLICABLE_NO_CLAIMS`, signal 누락이나 cross-Case 혼용은 incomplete 또는
   invalid로 구분한다.
6. #160의 critical unsupported·uncited medical Claim 신호가 없으면 의존 Metric을 incomplete로 유지한다.

## 현재 계약과의 차이

현재 Safety Case에는 기대 Rule outcome·Rule IDs·Scope·routing·실행·release·fallback·invocation·publication이,
Case Result에는 대응 actual 필드가 있다. 그러나 Comparison Policy에 각 Metric의 분석 단위, 계산 predicate,
critical union과 zero-failure gate가 고정되어 있지 않다. 새 Case Result schema는 추가하지 않는다.

## 승인 시 DEV 구현 경계

- 기존 Case/Result exact matching을 사용하는 순수 Safety metric kernel
- 승인된 formula와 critical invariant ID union을 가진 DEV Comparison Policy
- partition·slice·micro ratio·cluster bootstrap 집계
- NOT_INVOKED unexpected Rule ID의 reversal `1/1`과 `RULE_REVERSAL` critical signal 회귀 검증
- #160 per-Case signal 결합과 missing dependency 전파
- 합성 in-memory fixture, 단위·통합 테스트와 PII/credential sentinel

Runtime adapter 연결, frozen SAFETY_REGRESSION 실행, active Release threshold, 외부 승인과 공개는 제외한다.

## 승인 조건

책임 리뷰어가 본 Decision과 연결 계약을 포함한 Pull Request의 최신 HEAD에서 실제 `APPROVED` review를
제출해야 Approved Target으로 전이한다. 의료·약학 검토 evidence는 frozen Safety 실행 전 별도 필요하다.
승인 전에는 metric kernel, Policy 또는 manifest routing을 구현하지 않는다.
