# Issue #216 RAG 평가 스키마 호환성 구현 계획

## 목표

기존 Schema Set `1.0.0`을 byte 단위로 보존하면서 `1.1.0` authoring 계약, version-aware registry와
export, manifest-driven loader dispatch, `FROZEN` Gold approval closure를 구현한다.

설계 정본은 `docs/designs/ceohwj/issue-216-rag-evaluation-schema-compatibility-design.md`다.

## 전역 제약

- `evals/schemas/1.0.0`의 기존 byte를 변경하지 않는다.
- `#214` Dataset Case나 `#157` Runner를 구현하지 않는다.
- OTC identity-insufficient 데이터는 `rag-eval.case`가 아니라 상위 Contract Receipt에 둔다.
- 각 동작은 실패 테스트를 먼저 확인한 뒤 최소 구현한다.
- source text assertion 대신 실제 model·loader·export 동작을 검증한다.

## Task 1: 1.1 작성 계약 고정

- `ai_worker/tests/evaluation/test_authoring_v1_1_schemas.py`에 `MATCHED_RULES`, `NO_MATCH`,
  `NOT_INVOKED` 정상·실패 사례를 먼저 추가한다.
- Rule ID cardinality, reason 불일치, Source·Bundle 모순, `MATCHED`가 아닌 identity를 거부한다.
- `ai_worker/tasks/evaluation/schemas/authoring_v1_1.py`에 enum, runtime fixture, expected shape,
  Case adapter와 `DatasetManifestV1_1`을 구현한다.
- 좁은 테스트를 다시 실행해 green을 확인한다.

## Task 2: Registry와 canonical schema export

- `schema_registry.py`, `schema_exports.py`와 export/parity 테스트를 수정한다.
- Schema Set `1.1.0`이 정확히 18개 unique member를 갖고, Case·Dataset만 1.1이며 나머지 member가
  1.0 byte와 `$id`를 유지하는지 먼저 실패 테스트로 고정한다.
- registry entry에 member version을 추가하고 Schema Set version별 immutable registry를 만든다.
- 기존 `SCHEMA_REGISTRY`는 1.0 alias로 유지한다.
- `evals/schemas/1.1.0`을 export하고 1.0 fresh export와 byte 동일성을 확인한다.

## Task 3: Loader dispatch와 freeze closure

- 실제 canonical resource로 임시 1.1 Dataset fixture를 구성한다.
- Manifest·Case version dispatch, Policy가 선택한 Schema Set, unknown version 거부,
  fake Rule ID 없는 no-match/not-invoked load를 테스트한다.
- `FROZEN` 상태에서 Case·Evidence Mapping·Rubric 중 하나가 `REVIEWED`이면
  `REVIEW_PROVENANCE_INVALID`인지 검증한다.
- typed authoring selector, version-aware model/hash 검증과 graph 등록을 구현한다.
- 기존 1.0 DEV 동작은 변경하지 않는다.

## Task 4: 계약 문서와 불변 참조 동결

- production loader/hash 알고리즘으로 canonical Schema Set hash를 계산한다.
- Decision, target contract, contract index, `evals/README.md`에 member-version 모델, Rule 의미,
  typed fixture, freeze closure, OTC 책임 경계와 불변 hash를 기록한다.
- 문서 링크와 `git diff --check`를 검증한다.

## Task 5: 통합 검증

```bash
uv run pytest ai_worker/tests/evaluation -q
uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
uv run mypy ai_worker/tasks/evaluation
git diff --check
```

두 Schema Set을 fresh export해 1.0 byte 불변과 1.1 결정성을 확인하고, secret·환자 데이터·placeholder·
범위 이탈을 점검한다. 최종 산출물은 `#214`에 immutable Schema Set 참조와 실제 검증 결과를 인계한다.
