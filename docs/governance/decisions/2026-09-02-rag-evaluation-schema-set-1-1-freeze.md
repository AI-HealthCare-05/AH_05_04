# Product Decision: RAG Evaluation Schema Set 1.1 Freeze

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-216-20260902` |
| 상태 | Implemented candidate · Designated reviewer approval pending |
| 제안일 | 2026-09-02 |
| 제안자·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Product·Safety·Evaluation 계약 승인 |
| 교차 리뷰 | 김지혜 (`@Jye-rookie`) — Gold·Fixture, 송은영 (`@phina-io`) — Schema·Loader |
| 추적 Issue | [#216](https://github.com/AI-HealthCare-05/AH_05_04/issues/216) |
| 적용 범위 | Post-MVP-1 Track F Evaluation authoring compatibility |

## 결정

`rag-eval.schema-set@1.1.0`을 `#214` Dataset Freeze가 사용할 Evaluation authoring 계약 후보로 고정한다. 이 Decision의 코드·Schema 구현은 완료됐지만 지정 책임 리뷰어의 PR 승인을 받기 전에는 승인된 Dataset Freeze 입력으로 사용할 수 없다.

Schema Set은 18개 전체 member를 가진다. 변경된 `rag-eval.case`와 `rag-eval.dataset-manifest`만 member version `1.1.0`이며, 나머지 16개 member는 기존 `1.0.0` 계약과 canonical bytes를 그대로 사용한다. Set version과 member version을 동일하다고 추정하지 않는다.

| Immutable field | 값 |
| --- | --- |
| Schema Set ID | `rag-eval.schema-set` |
| Schema Set version | `1.1.0` |
| Schema Set SHA-256 | `ee60c207cd461d1a415d75d4b77e9976cb5055d4462893b51e1180a7ab2bb108` |
| Canonical member root | `evals/schemas/1.1.0/` |
| Member count | `18` |

Schema Set hash는 member별 `{schema_id, schema_version, schema_sha256}`를 정렬한 canonical JSON의 SHA-256이다. Loader는 Evaluation Policy의 `artifact_schema_set_ref`와 이 tuple을 exact-match하고, Case·Dataset Manifest payload version은 Set version이 아니라 선택된 registry member version과 비교한다.

## Rule Gold 의미

`SAFETY | END_TO_END_RAG` Case는 다음 두 필드를 필수로 기록한다.

- `expected_rule_outcome=MATCHED_RULES`: `expected_rule_ids`가 1개 이상이며 not-invoked reason은 `null`
- `expected_rule_outcome=NO_MATCH`: Rule 평가가 실행됐으나 일치 Rule이 없으며 `expected_rule_ids=[]`, reason은 `null`
- `expected_rule_outcome=NOT_INVOKED`: 선행 차단으로 Rule 평가가 실행되지 않았으며 `expected_rule_ids=[]`, reason은 typed value

허용 not-invoked reason은 `SAFETY_ROUTED | SOURCE_INELIGIBLE | BUNDLE_INELIGIBLE`다. Source·Bundle eligibility와 dependency fault는 각각 별도 enum 축으로 기록하며 free-form tag나 가짜 Interaction Rule ID로 대체하지 않는다.

`expected_scope_codes`는 Rule ID의 적용 범위가 아니라 Case가 EVALUATION_REQUEST Guard에 제출할 기대 요청 Scope다. 빈 Scope는 Guard 계약상 실패하므로 `MATCHED_RULES | NO_MATCH | NOT_INVOKED` 모두에서 1개 이상을 유지한다. Answer Quality·Answer Grounding은 Rule outcome을 소유하지 않으므로 `expected_rule_ids=null`을 사용한다.

## Typed Fixture

- Source eligibility: `ELIGIBLE | EXPIRED | INACTIVE | CONFLICTING`
- Bundle eligibility: `ELIGIBLE | SOURCE_INELIGIBLE | SCOPE_INELIGIBLE | MEMBER_INELIGIBLE`
- Dependency fault: `NONE | PROVIDER_TIMEOUT | RETRIEVAL_FAILURE`

Source가 비적격이면 Bundle은 `SOURCE_INELIGIBLE`이어야 하고, Bundle이 `SOURCE_INELIGIBLE`이면 Source도 비적격이어야 한다. 이 경우 not-invoked reason은 `SOURCE_INELIGIBLE`이며 `BUNDLE_INELIGIBLE`로 바꾸지 않는다. `BUNDLE_INELIGIBLE` reason은 `SCOPE_INELIGIBLE | MEMBER_INELIGIBLE`에만 사용한다. Rule이 실행된 `MATCHED_RULES | NO_MATCH`는 Source·Bundle이 모두 적격일 때만 허용한다.

Provider·Retrieval fault는 Rule-first 단계 이후의 실행 실패이므로 `NOT_INVOKED`와 공존할 수 없다. `NOT_INVOKED`는 `dependency_fault=NONE`, `expected_provider_invocation=false`, `expected_retrieval_invocation=false`를 요구해 선행 차단 뒤 부분 실행을 금지한다. `PROVIDER_TIMEOUT`은 provider 호출이 시작된 `TIMED_OUT`, `RETRIEVAL_FAILURE`는 retrieval 호출이 시작된 `DEPENDENCY_ERROR`와 결속하며, 이미 확정된 `MATCHED_RULES | NO_MATCH` 결과와 독립적으로 기록한다.

## Dataset Freeze 승인 closure

`rag-eval.dataset-manifest@1.1.0`이 `FROZEN`이면 Loader는 Manifest 자체 승인 외에도 다음 모든 필수 자원의 `review_provenance.team_gold_status=APPROVED`를 확인한다.

- Manifest에 포함된 모든 Case Gold
- Evidence Mapping Manifest
- Critical Claim Rubric

하나라도 `DRAFT | REVIEWED`이면 `EVAL_REVIEW_PROVENANCE_INVALID`로 실패한다. 이 규칙은 cross-resource Loader invariant이며 단일 JSON Schema가 승인 closure를 보장한다고 주장하지 않는다. 기존 `1.0.0` DEV Dataset의 동작은 유지한다.

## OTC Identity 소유 경계

Evaluation Case의 Medication fixture는 계속 `MATCHED`만 허용한다. OTC `AMBIGUOUS | UNMATCHED` preflight, Candidate/Resolver 품질과 내부 판정값은 상류 Candidate/Resolver Contract Suite와 불변 Contract Receipt가 단일 소유한다. `END_TO_END_RAG`는 Evaluation Policy와 Run/Gate의 required Contract Receipt graph로 이를 결속하고 Evaluation Dataset에 중복 Case나 내부 결과를 저장하지 않는다.

## 승인·후속 경계

- 이 변경은 153개 Dataset 작성·Freeze를 포함하지 않는다. 해당 작업은 `#214`가 이 Schema Set의 지정 리뷰 승인을 확인한 뒤 수행한다.
- Runner·Reporter·Baseline은 `#157` 범위다.
- 외부 의료·약학·Privacy·Source 승인과 Production 공개는 포함하지 않으며 `PUBLIC_TRACK_F=false`를 유지한다.
- 지정 책임 리뷰와 교차 리뷰가 완료되기 전에는 이 Decision 상태를 `Approved`로 변경하지 않는다.
