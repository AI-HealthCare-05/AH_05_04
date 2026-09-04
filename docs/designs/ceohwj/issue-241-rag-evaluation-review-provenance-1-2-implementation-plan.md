# Issue #241 RAG 평가 검토 Provenance 1.2 구현 계획

## 목표

`DRAFT`에 허위 reviewer를 요구하지 않고 실제 review event를 추적할 수 있는 불변 Schema Set
`1.2.0`을 추가한다. 기존 1.0·1.1의 동작과 byte는 유지한다.

설계 정본은 `docs/designs/ceohwj/issue-241-rag-evaluation-review-provenance-1-2-design.md`다.

## 전역 제약

- `evals/schemas/1.0.0`, `1.1.0`의 committed byte를 보존한다.
- Schema Set은 정확히 18개 unique member path와 schema ID를 유지한다.
- dependency를 추가하지 않고 합성 평가 fixture만 사용한다.
- `DRAFT`는 review·approval field가 `null`이고 evidence가 비어 있어야 한다.
- `REVIEWED`, `APPROVED`는 reviewer field와 immutable review evidence를 요구한다.
- `EVALUATION_REVIEWER`는 내부 역할이며 외부 의료 승인을 대신하지 않는다.
- `#214` Dataset resource, Freeze 상태, Runner 동작을 수정하지 않는다.

## Task 1: 1.2 actor와 provenance model

- `test_common_schemas.py`에 상태 matrix, pair 불일치, timestamp 순서, actor identity·role 테스트를 먼저 추가한다.
- `common_v1_2.py`에 `ActorRoleV12`, `ActorRefV12`, `ReviewProvenanceV12`를 구현한다.
- 기존 external-review와 evidence-ref 규칙을 보존하고 좁은 model test를 통과시킨다.

## Task 2: Versioned authoring·policy artifact

- `authoring_v1_2.py`, `policy_v1_2.py`와 package export를 추가한다.
- 다섯 Case variant, Dataset Manifest, Evidence Mapping, Rubric, Receipt, Profile, Suite, Policy가
  `ReviewProvenanceV12`를 사용하는지 실패 테스트로 고정한다.
- 1.1 Rule·eligibility·fault validator와 기존 approval-role 제한을 유지한다.
- Comparison Policy는 1.0 계약을 재사용한다.

## Task 3: Registry·canonical export·Loader graph

- `SCHEMA_REGISTRY_V1_2`와 `SCHEMA_REGISTRIES["1.2.0"]`을 추가한다.
- 18개 중 정확히 8개 provenance member만 1.2인지 검증한다.
- Review provenance JSON Schema conditional을 상태 matrix와 일치시킨다.
- Loader contract bundle이 graph의 모든 provenance-bearing artifact model을 선택하게 한다.
- mixed member version, unknown version, 1.2 `FROZEN` child DRAFT/REVIEWED를 거부한다.

## Task 4: Canonical Schema Set과 계약 문서

- fresh export를 `/private/tmp`에 생성해 18개 파일을 `evals/schemas/1.2.0`에 반영한다.
- 1.0·1.1은 재생성하거나 수정하지 않는다.
- 계산된 `rag-eval.schema-set@1.2.0` hash를 Decision, target contract, index와 `evals/README.md`에 기록한다.
- committed export와 fresh export의 byte 동일성, 문서 hash exact-match를 테스트한다.

## Task 5: 통합 검증과 인계

```bash
uv run pytest ai_worker/tests/evaluation -q
uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
uv run mypy ai_worker/tasks/evaluation
git diff --check
```

이전 schema directory가 바뀌지 않았고 의도한 파일만 변경됐는지 확인한다. `#214`에는 1.2 불변 참조,
상태 matrix, `EVALUATION_REVIEWER` 의미와 실제 검토 이후에만 Freeze할 수 있다는 경계를 인계한다.
