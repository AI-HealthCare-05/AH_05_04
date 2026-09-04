# Issue #214 RAG 평가 Dataset 동결 구현 계획

## 목표

Schema-valid하고 결정적인 153개 합성 `rag-holdout-safety@1.0.0` Dataset을 작성하고, 지정된 사람의
실제 검토를 provenance에 결속해 `APPROVED/FROZEN`으로 동결한다. HOLDOUT 실행이나 Release 결정은
만들지 않는다.

설계 정본은 `docs/designs/ceohwj/issue-214-rag-evaluation-dataset-freeze-design.md`다.

## 전역 제약

- `rag-eval.schema-set@1.2.0`과 불변 hash를 사용한다.
- Case는 정확히 153개이며 `HOLDOUT=60`, `SAFETY_REGRESSION=93`이다.
- DEV fixture와 Schema Set 1.0 byte를 변경하지 않는다.
- 합성 fixture만 사용하고 환자·OCR·보험·내부 ID·Provider payload·secret·licensed 문장을 넣지 않는다.
- 네 Leakage 축을 두 partition 사이에서 재사용하지 않는다.
- structured Claim·Evidence·Citation·Rule·Scope·routing·fallback·invocation·publication을 Gold 정본으로 삼는다.
- `runtime_eligible=false`, diagnostic Policy, 빈 Gate ref를 유지한다.
- HOLDOUT 결과를 실행·열람하지 않고 approval을 꾸며내지 않는다.

## Task 1: Dataset catalog 계약

- Dataset identity, 153개 수, 60/93 분할과 task·category·archetype matrix를 실패 테스트로 고정한다.
- Case ID 형식과 네 Leakage 축을 검증한다.
- 아직 fixture가 없을 때 RED를 확인한 뒤 합성 catalog를 작성한다.

## Task 2: Evidence·Mapping·Rubric

- 합성 Evidence resource, Mapping과 Critical Claim Rubric을 작성한다.
- logical ID·version·locator·content hash를 결속하고 dangling·duplicate·locator mismatch를 거부한다.
- unsupported critical claim, Citation 누락, inactive Evidence, unsafe Rule 해석, scope·routing 위반을 rubric으로 고정한다.

## Task 3: 153개 structured-Gold Case

- exact allocation table에 따라 HOLDOUT 60개와 SAFETY_REGRESSION 93개를 작성한다.
- applicable task마다 Evidence, Claim, Citation, Rule, Scope, Safety, fallback, invocation, publication field를 채운다.
- 자연어 exact answer는 oracle로 쓰지 않고 한국어 `ko-KR` 의미 계약만 유지한다.

## Task 4: Manifest·Receipt·Profile·Policy·Suite

- Case와 Evidence graph의 canonical hash를 계산해 Dataset Manifest와 Case-only receipt를 만든다.
- Profile·Policy·Suite를 독립 version으로 결속하고 `runtime_eligible=false`를 유지한다.
- Comparison Policy는 validation-only이며 `holdout_execution_authorized=false`다.

## Task 5: 무결성·Leakage·Privacy·결정성

- 각 resource를 변조하고 직접 hash만 갱신해 stale downstream ref가 거부되는지 확인한다.
- 네 Leakage 축의 cross-partition 이동을 모두 거부한다.
- privacy key, credential, 환자·OCR·Provider payload sentinel을 노출 없이 거부한다.
- fresh load와 validation CLI를 두 번 실행해 run-specific field를 제외한 의미가 동일한지 확인한다.

## Task 6: 실제 검토와 Freeze

- 최초 candidate는 `DRAFT`, reviewer·timestamp·review evidence는 비워 둔다.
- 실제 review 뒤 `EVALUATION_REVIEWER` identity, UTC timestamp와 immutable review ref를 기록한다.
- artifact별 approver가 승인한 exact content만 `APPROVED`로 전환한다.
- 모든 child approval과 Dataset Custodian 검토 뒤 Dataset을 `FROZEN`으로 바꾼다.
- `@Jye-rookie` review `5102210603`, `@hazelnutflavoured` review `5102473823`을 결속했다.

## 검증 명령

```bash
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run --with jsonschema pytest ai_worker/tests/evaluation -q
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
UV_CACHE_DIR=/private/tmp/ah_issue214_uv_cache uv run mypy ai_worker/tasks/evaluation
git diff --check
```

## #157 인계값

아래 label은 회귀 테스트가 사용하는 불변 artifact 이름이다.

| 항목 | 불변 ID@version | SHA-256 |
| --- | --- | --- |
| Dataset Manifest | `rag-holdout-safety@1.0.0` | `2c42b2969387d7efaf4f3806e33ee502032b6fb7243bc6a1198434239395f09d` |
| Case resource set | `rag-holdout-safety@1.0.0` | `094d89292e52971fe5e9148336c533b43936caa70e0c9ea44a5572354cc9b6df` |
| HOLDOUT partition | `rag-holdout-safety:HOLDOUT@1.0.0` | `0f8dab92ee78a995904ce336d8dbf6739773e86556db479c8efe6775c2e0692b` |
| SAFETY_REGRESSION partition | `rag-holdout-safety:SAFETY_REGRESSION@1.0.0` | `381e808cea848ed6a94335ce262cd7df2594279ababc62cc9fbcc141643bcbe3` |
| Evidence Mapping | `rag-holdout-safety-evidence@1.0.0` | `86f70e09de3dfff719572be40a61540452fc7ebacdaedd5050b9fecb936f2d2a` |
| Critical Claim Rubric | `rag-holdout-safety-critical-claims@1.0.0` | `d47433965c83dce1f70d393242b9ed3e37072946853053e76e2a829bb58e1525` |
| Evaluation Profile | `rag-holdout-safety-profile@1.0.0` | `812ff6bb8cce18cd0e0c80f22ac468005a128e4ed2b30f21ad0381d7b91a0ed1` |
| Comparison Policy (validation-only) | `rag-holdout-safety-comparison@1.0.0` | `9d15cccbb271c3b3bd0735352a7e58f3c2b590d81df991f47de5db7ef292189f` |
| Evaluation Policy | `rag-holdout-safety-policy@1.0.0` | `6173a883d31421c1b9b197d68c4403bba3b24599c1dfd152eeb617279bac50ee` |
| Evaluation Policy member manifest | `rag-holdout-safety-policy@1.0.0` | `02dd78aff64b457fa898e798310e45bcdeef4c31aa264bd470be160a80de94a3` |
| Suite | `rag-holdout-safety-validation-suite@1.0.0` | `b942271d8c842a0e3e6fd8c5fb595678aa5504ee1571f12e0cacaf01283042e4` |
| Selected Case set | `rag-holdout-safety-validation-suite@1.0.0` | `df3e20f532548ed92b5c4231a95d0d8f4be268ad6494155d70cc5ccc73a94bbd` |
| Case-only protected artifact receipt | `rag-holdout-safety-protected-receipt@1.0.0` | `9bce4d35aa3af797ebbfd77fe73a6f6c3b69580080ff63a085e957e9732e973e` |
| Protected receipt internal self-hash | `rag-holdout-safety-protected-receipt@1.0.0` | `1b88575a5454131d315d71774dceeb6e979fd440959ed3ce3f2635b02c0a7fa7` |
| Artifact Schema Set | `rag-eval.schema-set@1.2.0` | `1bdc6c8d2c5b62415b7f2f59e42ffdf7d67243ae4cccd1e6b3a3116daae73b06` |

## 완료 경계

Dataset·Case·Evidence Mapping·Rubric은 `APPROVED`, Dataset은 `FROZEN`이다. 이 완료는 Runner,
HOLDOUT 실행, 임상 승인, Release decision, Production 공개를 승인하지 않는다. `#157`은 DEV 검증부터
시작하며 별도 Comparison Policy 승인 전에는 `WAITING_FOR_APPROVED_COMPARISON_POLICY` 상태를 유지한다.
