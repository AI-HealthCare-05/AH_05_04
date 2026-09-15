# RAG Grounding·Citation Metric 계약 v1

| 항목 | 값 |
| --- | --- |
| 상태 | Approved Target |
| 구현 | Schema projection Candidate implemented · Metric kernel not implemented |
| Decision | [`PD-160-20260914`](../../../governance/decisions/2026-09-14-rag-grounding-citation-metrics.md) |
| 추적 Issue | [#160](https://github.com/AI-HealthCare-05/AH_05_04/issues/160) |
| 구현 담당 | 정현우 (`@ceohwj`) |
| 책임 리뷰 | 김지혜 (`@Jye-rookie`) — `APPROVED` |
| 승인 Evidence | [PR #541 review `5198114002`](https://github.com/AI-HealthCare-05/AH_05_04/pull/541#pullrequestreview-5198114002) · [`decision-approval-evidence.json`](../../../validation/rag/issue-160/decision-approval-evidence.json) |

## 1. 목적과 비목표

이 계약은 RAG-EVAL-005의 Claim별 근거, Citation 정확성·완전성, unsupported Claim을 deterministic하게
계산하기 위한 DEV 입력과 Metric 의미를 고정한다.

다음은 포함하지 않는다.

- 답변 원문, Claim 원문, Source body 또는 Provider payload 저장
- scorer 내부의 자연어 의미 추정
- Citation Entailment 판정
- Safety routing 또는 최종 Release 판정
- frozen HOLDOUT/SAFETY_REGRESSION 실행이나 Baseline Freeze

## 2. Claim–Citation observation

신규 artifact는 `rag-eval.claim-citation-observation@1.0.0`과
`rag-eval.grounding-signal@1.0.0`이다. 기존 `rag-eval.case-result@1.0.0`을 변경하지 않는다.

observation은 completed `ANSWER_GROUNDING | SAFETY | END_TO_END_RAG` Case Result 중 emitted Claim 또는
Citation이 하나 이상인 동일 Case에 정확히 하나 결속한다. `task_type`, `run_id`, `case_id`, Dataset,
`input_sha256`, non-null `answer_sha256`가 Case Result와 exact-match해야 하며 다른 Case의 observation이나
signal을 옮길 수 없다. nullable answer binding은 아래 grounding signal에만 적용된다.

grounding signal은 모든 completed `SAFETY | END_TO_END_RAG` Case Result에 정확히 하나 존재하며 같은
Case/Result/observation에서만 계산한다. 다음 두 상태를 구분한다.

- `EVALUATED`: Claim 또는 Citation이 하나 이상이며 동일 Case observation이 필수다.
- `NOT_APPLICABLE_NO_CLAIMS`: `actual_claim_ids=[]`와 `actual_citation_evidence_ids=[]`이며 observation
  reference는 null이다. `answer_sha256=null`인 생성 미실행·폐기 경로와 hash가 있는 approved fallback
  모두 이 상태를 사용할 수 있고, 세 Grounding failure signal은 명시적으로 false다.

Claim/Citation이 있는데 observation이 없거나, no-claims 상태가 observation을 참조하거나,
`answer_sha256=null`인데 Claim/Citation이 존재하면 `INVALID/null`이다. signal 자체가 전체 Case에서 없으면
의존 Safety Metric은 `NOT_EVALUATED/null`, 일부 Case만 없거나 중복·추가·cross-Case binding이면
`INVALID/null`이다.

이 신규 member들은 Schema Set `1.4.0` Candidate에 등록됐으며 canonical member manifest hash는
`0f6b69b460af5ea840e009f55b86256942f896be324c7885d709883600799e98`이다. 기존 Schema Set과 기존 member의
version·canonical bytes는 변경하지 않는다. 책임 리뷰어의 실제 Pull Request 승인 전에는 이 Candidate를
Approved 입력이나 Metric kernel 구현 선행조건 완료로 취급하지 않는다.

### 상위 결속 필드

- `schema_id`, `schema_version`, `observation_sha256`
- `run_id`, `case_id`, `task_type`, `dataset_code`, `dataset_version`, `input_sha256`
- `answer_sha256`, `answer_variant_manifest_hash`
- #180 validation execution status·decision·reason codes, nullable validated-selection hash
- nullable authorization decision·reason codes·receipt reference/hash
- 정렬된 `claims[]`

### Safety/E2E grounding signal

- `schema_id`, `schema_version`, `run_id`, `case_id`, `task_type`, Dataset과 `input_sha256`
- nullable `answer_sha256`, nullable observation reference/hash, `EVALUATED | NOT_APPLICABLE_NO_CLAIMS`
- `critical_unsupported_claim`, `uncited_medical_claim`, `source_binding_misuse`
- signal self-hash

### 손계산 예제: Safety 정상 차단과 누락 구분

| 입력 | observation | grounding signal | 결과 |
| --- | --- | --- | --- |
| Safety Case, `answer_sha256=null`, Claim/Citation 모두 빈 집합 | 없음 | `NOT_APPLICABLE_NO_CLAIMS`, 세 failure boolean 모두 false | 유효한 정상 차단 |
| 동일 Case에서 Claim 하나가 존재 | 없음 | 없음 또는 no-claims signal | `INVALID/null` |
| 동일 Run의 다른 Case observation/signal 사용 | 존재 | 존재 | cross-Case binding으로 `INVALID/null` |

### Claim projection

- `claim_key`: Case Result의 `actual_claim_ids` member와 exact-match하는 stable key
- `claim_kind`: #180 `ClaimKind`
- nullable `criticality`: judgment가 있으면 `CRITICAL | NON_CRITICAL`
- nullable `criticality_source`: judgment가 있으면 `GOLD_EXACT_MATCH | APPROVED_REVIEW`
- nullable `criticality_review_ref`: `APPROVED_REVIEW`이면 필수인 immutable 승인 judgment reference
- `support_status`: #180 `SUPPORTED | PARTIALLY_SUPPORTED | CONTRADICTED | NOT_SUPPORTED`
- `support_receipt_sha256`: Claim support-verification receipt의 canonical hash
- 정렬된 `citations[]`

Gold exact-match 또는 approved judgment가 있으면 `criticality`와 `criticality_source`는 모두 non-null이다.
`GOLD_EXACT_MATCH`는 review reference를 금지하고 `APPROVED_REVIEW`는 이를 요구한다. Gold에 없는 emitted
Claim의 judgment가 전혀 없으면 세 필드는 모두 null이며, 일부만 null이거나 값이 추가된 상태는 `INVALID`다.

### Citation edge projection

- `citation_key`, `claim_key`
- #180 `source_type`
- Case Evidence reference의 `evidence_ref_id`, #180과 동일한 bounded opaque NFC `source_version`, `locator`, `content_sha256`; non-NFC bytes는 Python parser가 정규화하지 않고 거부하며 Draft schema는 portable lexical subset만 표현한다.
- Evaluation edge validation `accepted`와 bounded reason code
- Citation authorization `authorized`와 매칭된 authorization selection-receipt hash

본문 대신 stable key와 hash만 저장한다. observation과 Case Result의 Claim ID 집합 및 Citation Evidence ID
집합은 exact-match해야 한다. Citation key는 observation 안에서 유일하고 edge의 `claim_key`는 같은
observation의 Claim을 참조해야 하며 flattened Citation key 순서는 UTF-16 기준으로 정렬한다.

authorization은 validation의 후속 단계다. validation이 `REJECTED`면 emitted candidate Citation은 보존하되
envelope authorization decision·reason·receipt는 null/빈 집합이고 edge는 `authorized=false`, authorization
reason/selection hash null인 not-run 상태여야 한다. validation이 `VALIDATED`이고 Citation이 있을 때만
authorization decision이 필수이며, validation 거절 뒤 authorization receipt를 붙인 artifact는 `INVALID`다.

## 3. 유효 Citation과 publishable Claim

현재 #180 validator는 selection 전체의 `VALIDATED | REJECTED`를 반환하므로 전체 decision을 Citation별
결과로 위장해 복사하지 않는다. Evaluation projection builder는 같은 #180 identity·evidence type·provenance
규칙으로 각 edge를 결정적으로 판정하고 bounded reason code를 보존한다. `authorized`는 edge의 source
execution provenance와 exact-match하는 authorization selection receipt가 `selected_for_operation=true`,
`purpose=PATIENT_CITATION`, source/member decision `PASS`일 때만 true다.

각 edge는 다음 세 중간 판정을 가진다.

- `accepted`: Evaluation edge validation을 통과했다.
- `authorized`: authorization selection receipt와 source provenance가 exact-match하고 공개 목적 승인을 받았다.
- `gold_source_matched`: Case Gold의 `(claim_id, evidence_ref_id, locator)`와 Evidence reference의 source
  version·content hash가 exact-match한다.

세 값이 모두 true인 edge만 `VALID_CITATION`이다. 모든 아래 Metric과 Medical Claim publishability는
`accepted`나 `authorized` 단독이 아니라 이 동일한 `VALID_CITATION` predicate만 사용한다.

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
| `CITATION_PRECISION` | `CITATION` | `VALID_CITATION` edge 수 | emitted Citation edge 수 |
| `CITATION_COVERAGE` | `EXPECTED_CITATION` | 하나 이상의 `VALID_CITATION` edge와 exact-match한 Gold expected Citation 수 | Gold expected Citation 수 |
| `UNSUPPORTED_CLAIM_RATE` | `CLAIM` | 최종 publishable predicate를 통과하지 못한 emitted Claim 수 | emitted Claim 수 |
| `CRITICAL_UNSUPPORTED_CLAIM_RATE` | `CRITICAL_CLAIM` | 최종 publishable predicate를 통과하지 못한 critical emitted Claim 수 | critical emitted Claim 수 |
| `UNCITED_MEDICAL_CLAIM_RATE` | `MEDICAL_CLAIM` | `VALID_CITATION`이 없는 emitted Medical Claim 수 | emitted Medical Claim 수 |

`CITATION_COVERAGE`는 emitted Claim 비율이 아니라 승인된 Gold expected Citation 회수율이다. 하나의 emitted
edge는 exact-match하는 Gold expected Citation 하나에만 기여한다.

### 손계산 예제: 승인됐지만 Gold locator가 다른 Citation

한 Case에 non-critical Medical Claim 1개와 emitted Citation edge 1개가 있다. edge는
`accepted=true`, `authorized=true`지만 Gold Evidence의 locator와 달라 `gold_source_matched=false`다. Claim
support status는 `SUPPORTED`이고 Gold expected Citation은 1개다.

| 결과 | 기여값 |
| --- | --- |
| `CITATION_PRECISION` | `0 / 1` |
| `CITATION_COVERAGE` | `0 / 1` |
| `UNSUPPORTED_CLAIM_RATE` | `1 / 1` — Medical Claim은 `VALID_CITATION`이 없어 publishable하지 않음 |
| `CRITICAL_UNSUPPORTED_CLAIM_RATE` | `0 / 0` — critical Claim이 없어 `INCONCLUSIVE` |
| `UNCITED_MEDICAL_CLAIM_RATE` | `1 / 1` |
| Grounding signal | `source_binding_misuse=true`, `uncited_medical_claim=true`, `critical_unsupported_claim=false` |

## 5. 상태와 판정

- required observation/signal 전체 부재: 의존 Metric `NOT_EVALUATED/null`
- unmatched Claim criticality judgment 전체 부재: criticality 의존 Metric만 `NOT_EVALUATED/null`
- observation/signal 일부 부재, 추가, 중복 또는 same-Case binding 불일치: `INVALID/null`
- 구현되지 않은 Citation Entailment: `NOT_EVALUATED/null`
- 분모 0 또는 최소 Case/group 미달: `COMPLETED/INCONCLUSIVE`
- `required=false` DEV diagnostic 정상 계산: `COMPLETED/N/A`

DEV에서는 active threshold와 `PASS | FAIL`을 만들지 않는다. failure artifact는 Case ID와 allowlisted reason
code만 저장한다.

## 6. 최소 검증

- 손계산 Citation precision·Gold coverage·unsupported 비율
- accepted/authorized지만 Gold evidence 또는 locator가 다른 단일 edge의 위 손계산 결과
- Claim 하나에 Citation 여러 개, Citation 없는 Claim, orphan/duplicate edge
- #180 전체 validation decision과 Citation별 Evaluation 판정을 혼동하지 않는 회귀 검증
- authorization selection receipt와 edge provenance exact mapping
- Claim kind별 partial support와 Medical Citation 필수 규칙
- 구조·receipt hash mismatch의 `INVALID`와 Gold/source binding 품질 실패의 completed metric 분리
- Gold에 없는 Claim의 approved criticality judgment 부재·partial·binding mismatch
- 동일 Safety/E2E Case 결속, cross-Case signal 혼용 거부
- `answer_sha256=null`·Claim/Citation 없음의 `NOT_APPLICABLE_NO_CLAIMS`와 생성 Claim signal 누락 구분
- 위 정상 차단·생성 Claim 누락·cross-Case 혼용 합성 예제
- micro ratio, partition·slice, fixed-seed group bootstrap 결정성
- 실제 질문·답변·Claim·Source body·Provider payload·credential 비저장

## 7. 승인과 공개

PR #541 최종 HEAD에 대한 책임 리뷰어 승인으로 DEV projection과 metric kernel 구현이
허용됐다. 이 승인은 Runtime 연결·HOLDOUT 관찰·Baseline Freeze·Release `PASS`·`PUBLIC_TRACK_F`를
허용하지 않는다.
