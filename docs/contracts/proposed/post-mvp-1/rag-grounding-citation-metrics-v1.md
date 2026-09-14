# RAG Grounding·Citation Metric 계약 v1 제안

| 항목 | 값 |
| --- | --- |
| 상태 | Proposed · Review Required |
| 구현 | Not implemented |
| Decision | [`PD-160-20260914`](../../../governance/decisions/2026-09-14-rag-grounding-citation-metrics.md) |
| 추적 Issue | [#160](https://github.com/AI-HealthCare-05/AH_05_04/issues/160) |
| 구현 담당 | 정현우 (`@ceohwj`) |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Product·Safety·Evaluation |

## 1. 목적과 비목표

이 계약은 RAG-EVAL-005의 Claim별 근거, Citation 정확성·완전성, unsupported Claim을 deterministic하게
계산하기 위한 DEV 입력과 Metric 의미를 제안한다.

다음은 포함하지 않는다.

- 답변 원문, Claim 원문, Source body 또는 Provider payload 저장
- scorer 내부의 자연어 의미 추정
- Citation Entailment 판정
- Safety routing 또는 최종 Release 판정
- frozen HOLDOUT/SAFETY_REGRESSION 실행이나 Baseline Freeze

## 2. Claim–Citation observation

신규 artifact는 `rag-eval.claim-citation-observation@1.0.0`이다. 기존 `rag-eval.case-result@1.0.0`을
변경하지 않으며, 한 completed `ANSWER_GROUNDING` Case Result에 정확히 하나 결속한다.

이 신규 member는 다음 승인 Evaluation Schema Set version에 등록한다. 기존 Schema Set과 기존 member의
version·canonical bytes는 변경하지 않는다. 승인될 Schema Set version과 member manifest hash가 정해지기
전에는 schema/export/registry 구현을 시작하지 않는다.

### 상위 결속 필드

- `schema_id`, `schema_version`, `observation_sha256`
- `run_id`, `case_id`, `dataset_code`, `dataset_version`, `input_sha256`
- `answer_sha256`, `answer_variant_manifest_hash`
- #180 validation decision/reason codes, nullable validated-selection hash와 nullable authorization-receipt hash
- 정렬된 `claims[]`

### Claim projection

- `claim_key`: Case Result의 `actual_claim_ids` member와 exact-match하는 stable key
- `claim_kind`: #180 `ClaimKind`
- `criticality`: Case Gold/Rubric의 `CRITICAL | NON_CRITICAL`
- `criticality_source`: `GOLD_EXACT_MATCH | APPROVED_REVIEW`
- `criticality_review_ref`: Gold에 없는 emitted Claim이면 필수인 immutable 승인 judgment reference
- `support_status`: #180 `SUPPORTED | PARTIALLY_SUPPORTED | CONTRADICTED | NOT_SUPPORTED`
- `support_receipt_sha256`: Claim support-verification receipt의 canonical hash
- 정렬된 `citations[]`

### Citation edge projection

- `citation_key`, `claim_key`
- #180 `source_type`
- Case Evidence reference의 `evidence_ref_id`, `source_version`, `locator`, `content_sha256`
- Evaluation edge validation `accepted`와 bounded reason code
- Citation authorization `authorized`와 매칭된 authorization selection-receipt reference/hash

본문 대신 stable key와 hash만 저장한다. observation과 Case Result의 Claim ID 집합 및 Citation Evidence ID
집합은 exact-match해야 한다. Citation key는 observation 안에서 유일하고 edge의 `claim_key`는 같은
observation의 Claim을 참조해야 한다.

## 3. 유효 Citation과 publishable Claim

현재 #180 validator는 selection 전체의 `VALIDATED | REJECTED`를 반환하므로 전체 decision을 Citation별
결과로 위장해 복사하지 않는다. Evaluation projection builder는 같은 #180 identity·evidence type·provenance
규칙으로 각 edge를 결정적으로 판정하고 bounded reason code를 보존한다. `authorized`는 edge의 source
execution provenance와 exact-match하는 authorization selection receipt가 `selected_for_operation=true`,
`purpose=PATIENT_CITATION`, source/member decision `PASS`일 때만 true다.

`accepted=true`, `authorized=true`이고 Case Gold의 `(claim_id, evidence_ref_id, locator)` 및 Evidence
reference의 source version·content hash와 exact-match하는 edge만 Metric상 유효 Citation이다.

- `SUPPORTED`는 publishable이다.
- `PARTIALLY_SUPPORTED`는 `AUXILIARY` Claim에만 publishable이다.
- 의료 Claim은 `SUPPORTED`이며 유효 Citation이 하나 이상이어야 publishable이다.
- `CONTRADICTED | NOT_SUPPORTED`는 publishable하지 않다.

Gold Claim ID와 exact-match하면 Gold/Rubric criticality를 사용한다. Gold에 없는 emitted Claim은 claim text를
저장하지 않는 별도 immutable criticality judgment가 observation의 Run/Case/answer/claim key와 exact-match할
때만 criticality Metric에 기여한다. 이 judgment가 전혀 없으면 criticality 의존 Metric은
`NOT_EVALUATED/null`, 일부·중복·추가·binding mismatch면 `INVALID/null`이다.

unknown enum, 중복 ID, orphan edge, flat Case Result와의 집합 불일치, Run/Case/Variant/hash 또는 실제 #180
receipt와의 불일치는 입력 무결성 실패로 `INVALID/null`이다. 반면 observation이 진실하게 보존한
validation/authorization 거절이나 Gold/source/locator 불일치는 품질 실패로 계산하고 per-Case
`SOURCE_BINDING_MISUSE` signal을 만든다.

## 4. Metric formula

모든 Metric은 `metric_version=1.0.0`, Case 기여값 `(numerator, denominator)`을 합산한 micro ratio와 승인
Policy의 distinct leakage group cluster bootstrap 95% CI를 사용한다.

| Metric ID | 분석 단위 | 분자 | 분모 |
| --- | --- | --- | --- |
| `CITATION_PRECISION` | `CITATION` | 유효 Citation edge 수 | emitted Citation edge 수 |
| `CITATION_COVERAGE` | `EXPECTED_CITATION` | 하나 이상의 유효 emitted edge와 exact-match한 Gold expected Citation 수 | Gold expected Citation 수 |
| `UNSUPPORTED_CLAIM_RATE` | `CLAIM` | publishable하지 않은 emitted Claim 수 | emitted Claim 수 |
| `CRITICAL_UNSUPPORTED_CLAIM_RATE` | `CRITICAL_CLAIM` | publishable하지 않은 critical emitted Claim 수 | critical emitted Claim 수 |
| `UNCITED_MEDICAL_CLAIM_RATE` | `MEDICAL_CLAIM` | 유효 Citation이 없는 emitted Medical Claim 수 | emitted Medical Claim 수 |

`CITATION_COVERAGE`는 emitted Claim 비율이 아니라 승인된 Gold expected Citation 회수율이다. 하나의 emitted
edge는 exact-match하는 Gold expected Citation 하나에만 기여한다.

## 5. 상태와 판정

- projection 전체 부재: `NOT_EVALUATED/null`
- unmatched Claim criticality judgment 전체 부재: criticality 의존 Metric만 `NOT_EVALUATED/null`
- projection 일부 부재, 추가, 중복 또는 binding 불일치: `INVALID/null`
- 구현되지 않은 Citation Entailment: `NOT_EVALUATED/null`
- 분모 0 또는 최소 Case/group 미달: `COMPLETED/INCONCLUSIVE`
- `required=false` DEV diagnostic 정상 계산: `COMPLETED/N/A`

DEV에서는 active threshold와 `PASS | FAIL`을 만들지 않는다. failure artifact는 Case ID와 allowlisted reason
code만 저장한다.

## 6. 최소 검증

- 손계산 Citation precision·Gold coverage·unsupported 비율
- Claim 하나에 Citation 여러 개, Citation 없는 Claim, orphan/duplicate edge
- #180 전체 validation decision과 Citation별 Evaluation 판정을 혼동하지 않는 회귀 검증
- authorization selection receipt와 edge provenance exact mapping
- Claim kind별 partial support와 Medical Citation 필수 규칙
- 구조·receipt hash mismatch의 `INVALID`와 Gold/source binding 품질 실패의 completed metric 분리
- Gold에 없는 Claim의 approved criticality judgment 부재·partial·binding mismatch
- micro ratio, partition·slice, fixed-seed group bootstrap 결정성
- 실제 질문·답변·Claim·Source body·Provider payload·credential 비저장

## 7. 승인과 공개

책임 리뷰어의 실제 Pull Request 승인 전 이 문서는 구현 근거가 아니다. 승인되더라도 DEV projection과
metric kernel만 허용하며, Runtime 연결·HOLDOUT 관찰·Baseline Freeze·Release `PASS`·`PUBLIC_TRACK_F`는
별도 승인 대상이다.
