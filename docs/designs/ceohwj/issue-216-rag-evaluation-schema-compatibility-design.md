# Issue #216 RAG 평가 스키마 호환성 설계

## 상태와 범위

- 이슈: `#216`
- 범위: `#214` Dataset Freeze 이전의 평가 작성 스키마 호환성 보정
- 정본: `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`, RAG `evaluation-plan.md@1.35`
- 구현 순서: `#216` → `#214` → `#157`
- 결과: `rag-eval.schema-set@1.1.0`으로 구현·병합 완료

## 문제

Schema Set `1.0.0`은 Interaction Rule ID를 꾸며내지 않고 다음 세 결과를 구분할 수 없었다.

- 하나 이상의 승인 Rule이 일치함
- Rule 평가를 실행했지만 일치 항목이 없음
- 앞선 승인 경계에서 차단되어 Rule 평가를 실행하지 않음

또한 Source·Bundle 부적격과 Provider·Retrieval 장애를 typed fixture로 표현할 수 없었고,
`FROZEN` Dataset이 선택한 Case Gold·Evidence Mapping·Critical Claim Rubric이 `REVIEWED`에
머물러도 승인될 수 있었다.

OTC `AMBIGUOUS | UNMATCHED` 식별은 Evaluation Case 입력이 아니다. 이 값은 상위
Candidate/Resolver Contract Receipt가 소유하며 기존 필수 Receipt graph를 통해
`END_TO_END_RAG`를 차단한다.

## 설계 목표

1. 기존 `1.0.0` byte와 loader 동작을 바꾸지 않고 additive Schema Set `1.1.0`을 추가한다.
2. Rule 결과와 Rule ID cardinality를 기계 검증 가능한 계약으로 만든다.
3. Source·Bundle 상태와 dependency fault를 free-form tag 없이 표현한다.
4. `FROZEN` Dataset의 필수 Gold dependency가 승인되지 않았으면 fail-closed한다.
5. `#214`가 사용할 결정적 Schema Set ID·version·hash를 제공한다.

## 버전 모델

`rag-eval.schema-set@1.1.0`은 18개 member 전체를 포함한다.

- `rag-eval.case@1.1.0`: 변경
- `rag-eval.dataset-manifest@1.1.0`: 변경
- 나머지 16개 member: 기존 `1.0.0` 계약과 byte를 재사용

`evals/schemas/1.1.0/`은 closed-world 검증을 위해 전체 member를 보관한다. Schema Set hash는
정렬된 `{schema_id, schema_version, schema_sha256}`로 계산한다. Loader는 Set version이 아니라
선택된 registry member version과 payload version을 비교한다.

기존 공개 API 호환성은 다음과 같이 유지한다.

- `SCHEMA_REGISTRY`, `schema_documents()`의 기본 의미는 `1.0.0`이다.
- version-aware 조회·export API는 명시적 Schema Set version을 받는다.
- export CLI의 기본 version도 `1.0.0`이다.

## 작성 계약 1.1

`RuntimeFixtureV1_1`은 다음 필드를 필수로 추가한다.

| 필드 | 허용값 |
| --- | --- |
| `source_eligibility_status` | `ELIGIBLE`, `EXPIRED`, `INACTIVE`, `CONFLICTING` |
| `bundle_eligibility_status` | `ELIGIBLE`, `SOURCE_INELIGIBLE`, `SCOPE_INELIGIBLE`, `MEMBER_INELIGIBLE` |
| `dependency_fault` | `NONE`, `PROVIDER_TIMEOUT`, `RETRIEVAL_FAILURE` |

Source가 부적격이면 Bundle은 반드시 `SOURCE_INELIGIBLE`이어야 한다. Scope와 member 실패는
Bundle 축에서만 표현한다.

Safety·End-to-End Gold에는 다음 Rule 결과를 추가한다.

| 필드 | 허용값 |
| --- | --- |
| `expected_rule_outcome` | `MATCHED_RULES`, `NO_MATCH`, `NOT_INVOKED` |
| `expected_rule_not_invoked_reason` | `null`, `SAFETY_ROUTED`, `SOURCE_INELIGIBLE`, `BUNDLE_INELIGIBLE` |

불변식은 다음과 같다.

- `MATCHED_RULES`: `expected_rule_ids`는 비어 있지 않고 reason은 `null`이며 Source와 Bundle은 적격이다.
- `NO_MATCH`: `expected_rule_ids=[]`, reason은 `null`, Source와 Bundle은 적격이다.
- `NOT_INVOKED`: `expected_rule_ids=[]`, typed reason이 필요하고 dependency fault는 `NONE`이며 Provider·Retrieval을 호출하지 않는다.
- `SAFETY_ROUTED`: Source·Bundle은 적격이고 Safety disposition은 `NORMAL`이 아니며 Provider·Retrieval 호출이 없다.
- `PROVIDER_TIMEOUT`: Provider 호출 뒤 `TIMED_OUT`을 요구한다.
- `RETRIEVAL_FAILURE`: Retrieval 호출 뒤 `DEPENDENCY_ERROR`를 요구한다.
- Safety·End-to-End의 `expected_scope_codes`는 모든 Rule 결과에서 비어 있지 않아야 한다.

이 교차 필드 규칙은 Python Case model과 exported JSON Schema 조건문 양쪽에서 검증한다.

## Loader와 Freeze 경계

Loader는 Dataset Manifest의 `schema_id`와 `schema_version`을 먼저 읽고 불변 authoring bundle을
선택한다. 알 수 없는 version은 `SCHEMA_INVALID`로 실패한다. Policy의
`artifact_schema_set_ref.reference.version`은 hash 검증과 graph 등록에 사용할 registry를 고른다.
Manifest와 Policy의 Schema Set이 다르면 fail-closed한다.

`DatasetManifest@1.1.0`이 `FROZEN`이면 모든 Case, Evidence Mapping, Critical Claim Rubric의
`review_provenance.team_gold_status`가 `APPROVED`여야 한다. 하나라도 아니면
`REVIEW_PROVENANCE_INVALID`다. 이 규칙은 cross-resource loader 불변식이며 단일 JSON Schema로
거짓 표현하지 않는다. 기존 `1.0.0` DEV fixture는 그대로 load 가능하다.

## 검증과 비목표

- Rule 결과, cardinality, reason, eligibility 모순, OTC `MATCHED` 전용 경계를 model test로 검증한다.
- 1.0 회귀, 1.1 dispatch, Schema Set hash, child approval closure를 loader test로 검증한다.
- 두 Schema Set의 결정성, Draft 2020-12 적합성, 재사용 member byte 동일성을 검증한다.
- 153개 Dataset 작성·동결은 `#214`, Runner·Metric·Release Gate는 `#157` 이후 범위다.
- Runtime Source/Rule/Bundle과 Candidate/Resolver 평가는 이 이슈에서 구현하지 않는다.
