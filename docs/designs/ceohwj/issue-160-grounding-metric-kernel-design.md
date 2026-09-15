# Issue #160 Grounding·Citation Metric Kernel 설계

## 상태와 책임

- 추적 Issue: `#160`
- 구현 브랜치: `feat/160-grounding-metric-kernel`
- 기반 브랜치: `feat/160-rag-eval-schema-set-1-4`
- 구현 담당: 정현우 (`@ceohwj`)
- 책임 리뷰어: 김지혜 (`@Jye-rookie`)
- 변경 영역: AI Worker Evaluation의 순수 metric kernel과 비식별 합성 테스트
- 실제 실행 상태: `BLOCKED_BY_RAG_16`

이 브랜치는 승인된 `rag-eval.claim-citation-observation@1.0.0`과
`rag-eval.grounding-signal@1.0.0`을 소비하는 deterministic scorer만 구현한다. 실제 RAG-16 artifact가
연결되기 전에는 Evaluation Run을 만들거나 실행하지 않으며, `BLOCKED_BY_RAG_16`을 실제 Run 성공이나
실패 상태로 변환하지 않는다.

## 근거와 준수 문서

구현자는 편집 전에 다음 문서를 읽고 충돌 시 구현을 중단한다.

- `AGENTS.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `docs/privacy-safety.md`
- `docs/testing.md`
- `docs/contracts/targets/post-mvp-1/rag-grounding-citation-metrics-v1.md`
- `docs/governance/decisions/2026-09-14-rag-grounding-citation-metrics.md`

이 설계는 이미 승인된 target 계약 뒤의 구현이다. 기존 enum, JSON Schema, API/DTO, DB/message schema,
Runtime 상태 또는 공개 의미를 바꾸지 않는다. 공유 계약 변경이 필요하다고 판단되면 임의로 확장하지 말고
구현을 중단해 구현 담당자와 책임 리뷰어에게 보고한다.

## 목표

다섯 metric을 Case 기여값 `(numerator, denominator)`에서 micro ratio로 집계하고 승인된 distinct leakage
group 단위 percentile cluster bootstrap 95% CI를 계산한다.

| Metric ID | 단위 | 분자 | 분모 |
| --- | --- | --- | --- |
| `CITATION_PRECISION` | `CITATION` | `VALID_CITATION` edge 수 | emitted Citation edge 수 |
| `CITATION_COVERAGE` | `EXPECTED_CITATION` | 하나 이상의 유효 edge와 exact-match한 Gold expected Citation 수 | Gold expected Citation 수 |
| `UNSUPPORTED_CLAIM_RATE` | `CLAIM` | publishable하지 않은 emitted Claim 수 | emitted Claim 수 |
| `CRITICAL_UNSUPPORTED_CLAIM_RATE` | `CRITICAL_CLAIM` | publishable하지 않은 critical emitted Claim 수 | critical emitted Claim 수 |
| `UNCITED_MEDICAL_CLAIM_RATE` | `MEDICAL_CLAIM` | 유효 Citation이 없는 emitted Medical Claim 수 | emitted Medical Claim 수 |

모든 metric은 `metric_version=1.0.0`이다. DEV diagnostic scope는 `required=false`,
`estimator_id=MICRO_RATIO`, `estimator_version=1.0.0`,
`ci_method_id=PERCENTILE_CLUSTER_BOOTSTRAP`, `ci_method_version=1.0.0`, `level=0.95`,
`sidedness=TWO_SIDED`, `decision_basis=DIAGNOSTIC_ONLY`, `threshold=0`만 지원한다. 다른 알고리즘 서명은
추측해 계산하지 않고 `NOT_IMPLEMENTED/null`로 닫는다.

## 비목표와 금지 경계

다음 파일 또는 동작은 이 브랜치 범위가 아니다.

- Runtime adapter, Runtime assembly, RAG-16 handler 또는 StateGraph 연결
- `runner.py`, CLI, 실제 Evaluation Run 생성 또는 실행
- Provider 호출, 실제 검색, 실제 답변 생성 또는 실제 artifact 수집
- Repository, DB model, migration, API DTO 또는 OpenAPI
- Schema registry, Schema Set, exported JSON Schema 또는 기존 artifact enum 변경
- active comparison/evaluation policy, threshold, release gate 또는 publication flag 변경
- frozen HOLDOUT/SAFETY_REGRESSION Case, Gold, Evidence Mapping 또는 Rubric 수정·관찰
- 새로운 dependency, interface 계층, registry 또는 plugin 구조
- 질문, 답변, Claim, Source body, Provider payload, credential 또는 환자 데이터 저장

실제 RAG-16 부재를 표현하려고 `ExecutionStatus`나 공유 reason-code enum을 추가하지 않는다. 필요하면 #160의
repository-local 검증 상태 문서에 문자열 blocker `BLOCKED_BY_RAG_16`만 기록하되 Run artifact를 만들지 않는다.

## 제안 파일 경계

### 구현

- 생성: `ai_worker/tasks/evaluation/grounding_metrics.py`

이 모듈은 다음 기존 타입만 소비한다.

- `ValidatedDataset`, `EvaluationCaseContract`
- `CaseResult`, `MetricResult`, `MetricResults`
- `ClaimCitationObservation`, `GroundingSignal`과 관련 bounded enum
- `RatioContribution`, `canonical_ratio`, `percentile_cluster_bootstrap_ratio_ci`
- `ComparisonScope`, `ExecutionStatus`, `DecisionStatus`, `Partition`, `TaskType`

Runtime dataclass나 adapter를 import하지 않는다. 공개 진입점은 하나로 제한한다.

```python
def build_grounding_metrics(
    dataset: ValidatedDataset,
    case_results: tuple[CaseResult, ...],
    observations: tuple[ClaimCitationObservation, ...],
    grounding_signals: tuple[GroundingSignal, ...],
    *,
    expected_run_id: str,
    expected_input_sha256_by_case: Mapping[str, str],
) -> MetricResults:
    ...
```

별도 approved-review criticality payload 계약이 현재 입력에 존재하지 않으므로 scorer는 이를 새 wire schema로
발명하지 않는다. `GOLD_EXACT_MATCH`는 Case의 Gold Claim criticality와 exact-match해야 한다.
`APPROVED_REVIEW`는 observation 구조가 유효해도 승인 judgment 본문의 Run/Case/answer/claim binding을 scorer가
검증할 입력이 없으면 해당 criticality 의존 metric을 `NOT_EVALUATED/null`로 둔다. 이 제한을 우회하려는 새
artifact/DTO 추가는 별도 계약 변경이다.

### 테스트

- 생성: `ai_worker/tests/evaluation/test_grounding_metrics.py`

합성 fixture는 우선 테스트 내부의 작은 builder/pytest fixture로 둔다. 별도 JSON 파일이 중복을 실질적으로
줄일 때만 `ai_worker/tests/evaluation/fixtures/` 아래에 추가한다. 어떤 경우에도 `evals/retrieval/cases/`,
Gold, Evidence Mapping 또는 Rubric 파일을 수정하지 않는다.

## 입력 결속과 fail-closed 순서

scorer는 계산 전에 입력 전체의 identity와 cardinality를 검증한다. 검증 순서를 고정해 같은 손상에 같은 상태를
반환한다.

1. 대상 Case는 `ANSWER_GROUNDING | SAFETY | END_TO_END_RAG`이면서 scope의 partition/slice에 속해야 한다.
2. `expected_input_sha256_by_case` key 집합과 대상 Case 집합은 exact-match해야 한다.
3. Case Result는 대상 Case마다 정확히 하나이며 추가·누락·중복이 없어야 한다.
4. 모든 Result의 Run, Case, task type, dataset code/version, partition, input hash는 Case와 exact-match해야 한다.
5. completed Result에 emitted Claim 또는 Citation이 있으면 observation이 동일 Case에 정확히 하나 있어야 한다.
6. observation의 Run, Case, task type, dataset, input, answer hash는 Result와 exact-match해야 한다. scorer는
   typed model을 직접 받은 경우에도 `observation_sha256`을 제외한 canonical payload의 hash를 재계산해
   self-hash가 다르면 `INVALID/null`로 닫는다.
7. observation Claim key 집합은 `actual_claim_ids`와 exact-match해야 한다.
8. flattened Citation의 distinct `evidence_ref_id` 집합은 `actual_citation_evidence_ids`와 exact-match해야 한다.
   Citation key 중복, orphan Claim edge, 추가 edge 또는 불완전 집합은 허용하지 않는다.
9. Safety/E2E completed Case는 grounding signal이 정확히 하나여야 한다. `EVALUATED` signal은 동일 observation과
   Run/Case/task/dataset/input/answer/hash가 exact-match해야 한다. `signal_sha256`도 같은 방식으로 재계산한다.
10. Claim/Citation이 모두 없는 Safety/E2E Case만 `NOT_APPLICABLE_NO_CLAIMS`를 사용할 수 있다. 이때
    observation은 없고 signal의 세 failure boolean은 모두 false여야 한다.

위 구조·self-hash·same-Case 결속이 하나라도 깨지면 해당 scope의 의존 metric은 `INVALID/null`이다. 입력 전체가
없다는 사실과 일부만 손상된 상태를 섞지 않는다.

## VALID_CITATION과 mismatch 의미

Citation edge는 다음 조건을 모두 만족할 때만 `VALID_CITATION`이다.

1. `accepted=true`
2. `authorized=true`
3. Gold expected Citation의 `(claim_id, evidence_ref_id, locator)` exact-match
4. Evidence Mapping entry의 `evidence_ref_id`, evidence/source type, source version, locator,
   `content_sha256` exact-match

같은 Gold expected Citation에는 최대 한 edge만 coverage 분자로 기여한다. 한 edge가 여러 expected Citation에
중복 기여하지 않는다.

두 mismatch 부류를 구분한다.

- scorer가 Case Gold와 Evidence Mapping에서 재계산한 exact-match 결과와 edge의 `gold_source_matched`가 다른
  경우: observation 무결성 실패인 `INVALID/null`. 예를 들어 source/locator가 다른데
  `gold_source_matched=true`로 위장하거나, 모두 일치하는데 false로 기록하면 invalid다.
- observation이 validation/authorization 거절 또는 Gold source/type/version/locator/content mismatch를
  `gold_source_matched=false`로 정직하게 보존한 경우: completed 품질 실패. `accepted=true`,
  `authorized=true`여도 이 분류는 변하지 않는다. edge는 Precision 분모에 포함하지만 분자에서 제외하고,
  expected Citation은 Coverage 분자에서 제외하며, 해당 Safety/E2E signal의 `source_binding_misuse`는
  true여야 한다.

grounding signal boolean이 scorer의 재계산값과 다르면 `INVALID/null`이다. signal을 metric 계산의 진실로
사용하지 말고 observation과 Gold/Evidence 입력에서 재계산한 뒤 대조한다.

scorer 입력에는 실제 #180 validation/authorization receipt 본문이 없다. 따라서 receipt 본문의 provenance를
재검증했다고 주장하지 않고, `ClaimCitationObservation`의 승인 상태와 구조적 hash tuple을 입력 사실로 사용한다.
실제 receipt에서 observation을 만드는 exact mapping은 승인 계약이 요구하는 별도 pure projection builder 책임이다.
이 브랜치에서 Runtime receipt adapter를 끌어오거나 receipt DTO를 새로 만들지 않는다.

## Claim publishability

- `MEDICAL`: `support_status=SUPPORTED`이며 `VALID_CITATION`이 하나 이상일 때만 publishable
- `AUXILIARY`: `SUPPORTED` 또는 `PARTIALLY_SUPPORTED`이면 publishable
- `SAFETY_FALLBACK`: 승인 계약의 supported 상태만 publishable하며 Citation 필요 조건을 의료 Claim에만 적용
- `CONTRADICTED | NOT_SUPPORTED`: 항상 unsupported
- `MEDICAL + PARTIALLY_SUPPORTED`: Citation이 있어도 unsupported

`UNCITED_MEDICAL_CLAIM_RATE`는 support status와 무관하게 유효 Citation이 없는 emitted Medical Claim을 센다.
`UNSUPPORTED_CLAIM_RATE`는 위 최종 publishable predicate를 통과하지 못한 Claim을 센다.

Gold Claim과 `claim_key`가 exact-match하면 observation의 `criticality_source`는 `GOLD_EXACT_MATCH`이고
criticality도 Gold와 exact-match해야 한다. Gold Claim인데 criticality가 없거나 다른 값이면 `INVALID/null`이다.
Gold에 없는 Claim은 judgment 전체가 없으면 criticality metric에서만 제외하는 것이 아니라, 해당 Case의
criticality 판단을 수행할 수 없으므로 `CRITICAL_UNSUPPORTED_CLAIM_RATE`를 `NOT_EVALUATED/null`로 둔다.
일부 필드만 있거나 추가·중복·binding mismatch가 확인되면 critical metric은 `INVALID/null`이다.

## 상태 우선순위

상태는 다음 순서로 결정한다.

1. 구조·identity·cardinality·same-Case·self-hash·signal 불일치: `INVALID/null`
2. Case Result 자체의 비완료 상태: `INVALID > ERROR > NOT_IMPLEMENTED > NOT_EVALUATED` 우선순위로 전파
3. 필수 observation/signal이 모든 대상 Case에서 전부 없음: 의존 metric `NOT_EVALUATED/null`
4. unmatched Claim approved criticality judgment 검증 입력이 전부 없음: critical metric만
   `NOT_EVALUATED/null`
5. 분모 0: `COMPLETED/INCONCLUSIVE`, `reason_code=ZERO_DENOMINATOR`, numerator와 denominator 보존,
   metric value와 CI는 null
6. 최소 Case 수 미달: `COMPLETED/INCONCLUSIVE`, `reason_code=MINIMUM_CASE_COUNT_NOT_MET`
7. 최소 독립 group 수 미달: `COMPLETED/INCONCLUSIVE`,
   `reason_code=MINIMUM_INDEPENDENT_GROUP_COUNT_NOT_MET`
8. 나머지 `required=false` DEV diagnostic: `COMPLETED/N/A`

`MetricResult`의 기존 상태 validator를 그대로 만족해야 한다. incomplete metric은 decision, sample count,
numerator, denominator, value, CI, reason code를 모두 null로 둔다. completed zero-denominator metric만 count를
보존한다.

## 집계와 95% CI

Case별로 각 metric의 `RatioContribution`을 먼저 만든다. scope가 지정한 leakage group dimension의 distinct
group으로 contribution을 묶고, 전체 numerator와 denominator를 합산해 micro ratio를 계산한다.

- 값: `canonical_ratio(total_numerator, total_denominator)`
- CI: `percentile_cluster_bootstrap_ratio_ci(...)`
- seed, iterations, level은 승인 scope 값을 사용
- group ID 정렬과 난수 순서는 기존 `metric_support.py`가 소유
- denominator 0이면 bootstrap을 호출하지 않는다

metric별 bootstrap sampling frame은 해당 metric의 denominator가 하나 이상인 leakage group으로 구성한다.
한 group 안에서는 denominator 0인 Case contribution도 그대로 보존하지만, group 전체 denominator가 0이면 그
group은 그 metric의 분석 단위를 전혀 포함하지 않으므로 CI sampling frame과
`sample_independent_group_count`에서 제외한다. `sample_case_count`는 partition/slice의 입력 무결성을 통과한 전체
Case 수를 유지한다. 전체 group이 제외되면 전체 denominator도 0이므로 bootstrap을 호출하지 않고
`COMPLETED/INCONCLUSIVE`와 `ZERO_DENOMINATOR`를 반환한다. 이 규칙으로 기존
`percentile_cluster_bootstrap_ratio_ci`의 의미를 바꾸지 않고 metric별 zero-unit cluster를 처리한다.

## 손계산 합성 fixture

최소 fixture는 아래 Case 기여값을 직접 검증한다.

### Case A: Citation 하나는 valid, 하나는 locator mismatch

- Medical Claim 1개, `SUPPORTED`, non-critical
- emitted Citation 2개
- Gold expected Citation 2개
- 첫 edge는 accepted/authorized/Gold/Evidence exact-match
- 둘째 edge는 accepted/authorized지만 Gold locator와 다름

기대값:

- Precision `1/2`
- Coverage `1/2`
- Unsupported `0/1` if the valid edge supports the Medical Claim
- Critical Unsupported `0/0`
- Uncited Medical `0/1`
- `source_binding_misuse=true`

### Case B: Citation 없는 critical Medical Claim

- Medical Claim 1개, `SUPPORTED`, Gold exact-match criticality `CRITICAL`
- emitted Citation 0개
- Gold expected Citation 1개

기대값:

- Precision `0/0`
- Coverage `0/1`
- Unsupported `1/1`
- Critical Unsupported `1/1`
- Uncited Medical `1/1`
- `critical_unsupported_claim=true`
- `uncited_medical_claim=true`

### Case C: Auxiliary partial support

- Auxiliary Claim 1개, `PARTIALLY_SUPPORTED`, non-critical
- Citation 0개, expected Citation 0개

기대값:

- Precision `0/0`
- Coverage `0/0`
- Unsupported `0/1`
- Critical Unsupported `0/0`
- Uncited Medical `0/0`

### Micro aggregate A+B

- Precision `1/2`, value `0.5`
- Coverage `1/3`, value `0.333333`
- Unsupported `1/2`, value `0.5`
- Critical Unsupported `1/1`, value `1`
- Uncited Medical `1/2`, value `0.5`

CI의 lower/upper는 같은 seed와 iterations에서 두 번 호출해 byte-identical함을 검증하고, 독립적으로 구성한
group contribution으로 예상 percentile bound를 고정한다.

## 필수 테스트 목록

TDD 순서를 지킨다. production code를 만들기 전에 테스트를 추가하고, 새 모듈 import 또는 아직 없는 동작 때문에
RED가 되는지 확인한 뒤 최소 구현으로 GREEN을 만든다.

1. 다섯 metric의 단일 Case 손계산 numerator/denominator/value
2. 위 Case A+B micro ratio 손계산
3. fixed-seed group bootstrap 95% CI 결정성
4. Citation 없는 Medical Claim과 critical unsupported Claim
5. Claim 하나에 여러 Citation edge
6. source type/version/locator/content hash 각각의 mismatch
7. rejected validation/authorization의 completed quality failure
8. duplicate Citation key, orphan edge, 추가/누락 Claim과 Evidence set
9. mixed Run/Case/task/dataset/input/answer/variant/hash
10. Safety/E2E signal exact-one과 scorer 재계산 boolean 대조
11. `NOT_APPLICABLE_NO_CLAIMS` 정상 차단
12. 전체 observation 부재의 `NOT_EVALUATED`
13. 일부 observation/signal 부재 또는 cross-Case signal의 `INVALID`
14. criticality judgment 전체 부재, partial tuple, Gold mismatch
15. metric별 zero denominator
16. minimum Case/group 미달
17. 지원하지 않는 scope signature의 `NOT_IMPLEMENTED`
18. 개인정보·의료 원문 field/value를 생성하지 않는 합성 fixture sentinel
19. 입력 tuple을 변경하지 않는 순수성 및 같은 입력의 byte-identical 결과

테스트는 mock 호출 횟수가 아니라 실제 typed model과 반환 artifact를 검증한다.

## 구현 순서

1. `test_grounding_metrics.py`에 최소 hand-calculated Case A 테스트를 작성한다.
2. focused pytest를 실행해 예상 이유로 RED인지 확인한다.
3. scope 검증, binding validator, Case contribution, aggregate builder를 가장 작은 함수로 구현한다.
4. 각 상태와 mismatch 테스트를 하나씩 RED → GREEN으로 추가한다.
5. 중복이 확인된 경우에만 내부 helper를 추출하고 모든 테스트를 다시 실행한다.
6. 기존 `metric_support.py`를 재사용하며 다른 metric 동작을 변경하지 않는다.
7. 전체 Evaluation suite와 정적 검사를 실행한다.
8. 실제 Run, Runtime 연결 또는 blocker 해제 변경이 없는지 diff를 검토한다.

## 검증 명령

```bash
UV_CACHE_DIR=/private/tmp/ah05_issue160_metric_uv_cache uv run pytest \
  ai_worker/tests/evaluation/test_grounding_metrics.py -q

UV_CACHE_DIR=/private/tmp/ah05_issue160_metric_uv_cache uv run pytest \
  ai_worker/tests/evaluation -q

UV_CACHE_DIR=/private/tmp/ah05_issue160_metric_uv_cache uv run ruff check \
  ai_worker/tasks/evaluation/grounding_metrics.py \
  ai_worker/tests/evaluation/test_grounding_metrics.py

UV_CACHE_DIR=/private/tmp/ah05_issue160_metric_uv_cache uv run ruff format \
  ai_worker/tasks/evaluation/grounding_metrics.py \
  ai_worker/tests/evaluation/test_grounding_metrics.py --check

UV_CACHE_DIR=/private/tmp/ah05_issue160_metric_uv_cache uv run mypy \
  ai_worker/tasks/evaluation

git diff --check
git status --short --branch
git diff --stat
git diff -- \
  ai_worker/tasks/evaluation/grounding_metrics.py \
  ai_worker/tests/evaluation/test_grounding_metrics.py \
  docs/validation/rag/issue-160
```

전체 저장소 완료 검사는 `CONTRIBUTING.md`에 따라 `uv run ruff check .`,
`uv run ruff format . --check`, `uv run mypy backend/app ai_worker`, `bash scripts/ci/run_test.sh`이다. 로컬 자원
또는 병렬 작업 때문에 실행하지 못한 검사는 통과로 간주하지 않고 PR에 명시한다.

## 완료 기준

- Citation Precision/Coverage가 Gold expected Citation 및 exact Evidence binding으로 손계산과 일치한다.
- Citation 없는 의료 Claim, critical unsupported Claim, source/type/version/locator/content mismatch가 각각
  계약대로 계산되거나 fail-closed된다.
- 모든 completed metric이 numerator, denominator, canonical value와 95% CI를 보존한다.
- zero denominator, 전체 미관찰, 일부 fixture 부재, invalid observation 상태가 서로 구분된다.
- 합성 fixture와 hand-calculated test가 focused suite에서 통과한다.
- 기존 Evaluation suite, Ruff, formatting, Mypy와 diff 검사가 통과하거나 미실행 사유가 명시된다.
- Runtime adapter, runner, 실제 Run/artifact, schema/export, DB/API, policy/threshold, release/publication 변경이 없다.
- 실제 RAG-16 연결 증빙이 없으므로 `BLOCKED_BY_RAG_16`은 유지된다.
