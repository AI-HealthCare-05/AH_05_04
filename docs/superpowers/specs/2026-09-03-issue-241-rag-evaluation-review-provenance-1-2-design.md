# Issue #241 RAG Evaluation Review Provenance Schema 1.2 Design

## Status

- Issue: `#241`
- Scope: Evaluation review provenance compatibility before `#214` Dataset Freeze
- Authority: `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`, RAG `evaluation-plan.md@1.35`
- Delivery order: `#241` → `#214` / PR `#236` → `#157`

## Problem

Schema Set `1.0.0`과 `1.1.0`의 `ReviewProvenance`는 `team_gold_status`와 관계없이
`reviewed_by`와 `reviewed_at`을 필수로 요구한다. 이 때문에 아직 실제 검토가 수행되지 않은
`DRAFT` artifact도 검토자와 검토 시각을 미리 기록해야 한다. PR #236은 이 제약을 충족하려고
Gold·Fixture 교차 검토자를 `MEDICAL_REVIEWER`로 기록했지만, 실제 역할은 비임상 Evaluation
검토이며 해당 시각에도 검토가 수행되지 않았다.

`team_gold_status=DRAFT`라는 상태값만으로 허위 승인 주장을 막을 수는 있으나,
`reviewed_by`와 `reviewed_at` 자체가 완료된 review event를 뜻한다. 지정 검토자 인계 정보를
완료된 검토 provenance 필드에 기록하는 현재 방식은 상태와 event 의미를 서로 다르게 만든다.
또한 내부 Gold·Fixture 검토를 외부 의료 검토 역할로 표현하면 의료 승인으로 오독될 수 있다.

## Design Goals

1. 실제 검토 전 `DRAFT` artifact가 reviewer identity와 timestamp를 기록하지 않도록 한다.
2. `DRAFT`, `REVIEWED`, `APPROVED` 상태와 review·approval 필드를 양방향으로 결속한다.
3. 비임상 Evaluation 검토와 의료 검토를 역할 수준에서 구분한다.
4. 기존 Schema Set `1.0.0`과 `1.1.0`의 모델 동작과 canonical bytes를 변경하지 않는다.
5. Pydantic, exported JSON Schema, Loader가 동일한 `1.2.0` 계약을 집행한다.

## Chosen Approach

불변 Schema Set `1.0.0`과 `1.1.0`을 수정하지 않고 additive Schema Set
`rag-eval.schema-set@1.2.0`을 추가한다. `ReviewProvenanceV12`와 이를 직접 포함하는 artifact의
versioned 모델만 새로 정의한다. 이전 공통 모델을 전역 완화하는 방식은 기존 버전의 validation과
fresh export를 바꾸므로 사용하지 않는다.

Schema Set `1.2.0`은 여전히 18개 전체 member를 가진다. provenance definition이 exported
schema에 포함되는 다음 8개 member만 `1.2.0`으로 올리고 나머지 10개는 기존 member version과
canonical bytes를 재사용한다.

| Schema member | 1.2 member version | 이유 |
| --- | --- | --- |
| `rag-eval.case` | `1.2.0` | Case Gold review provenance |
| `rag-eval.dataset-manifest` | `1.2.0` | Dataset review/freeze provenance |
| `rag-eval.evidence-mapping-manifest` | `1.2.0` | Evidence mapping review provenance |
| `rag-eval.critical-claim-rubric` | `1.2.0` | Rubric review provenance |
| `rag-eval.evaluation-profile` | `1.2.0` | Profile review provenance |
| `rag-eval.suite-definition` | `1.2.0` | Suite review provenance |
| `rag-eval.evaluation-policy` | `1.2.0` | Policy review provenance |
| `rag-eval.protected-artifact-receipt` | `1.2.0` | `recorded_by` provenance |

`rag-eval.comparison-policy`와 결과·검증 artifact 등 provenance 변경을 포함하지 않는 10개
member는 기존 버전을 그대로 사용한다. Schema Set version을 모든 member version으로 복제하지
않는 1.1 원칙을 유지한다.

## Review Provenance 1.2 Contract

### Field shape

`ReviewProvenanceV12`는 다음 필드 requiredness를 갖는다.

- `authored_by: ActorRefV12`
- `reviewed_by: ActorRefV12 | null`
- `approved_by: ActorRefV12 | null`
- `authored_at: UtcTimestamp`
- `reviewed_at: UtcTimestamp | null`
- `approved_at: UtcTimestamp | null`
- 기존 Team Gold, external medical review, receipt, evidence reference 필드 유지

JSON object에서는 모든 키를 계속 명시한다. Optional은 키 생략 허용이 아니라 값 `null` 허용을
뜻한다. 따라서 canonical payload shape는 안정적으로 유지된다.

### Team Gold state matrix

| `team_gold_status` | `reviewed_by` / `reviewed_at` | `approved_by` / `approved_at` | `evidence_review_refs` |
| --- | --- | --- | --- |
| `DRAFT` | 둘 다 `null` | 둘 다 `null` | 빈 배열 |
| `REVIEWED` | 둘 다 non-null | 둘 다 `null` | 1개 이상 |
| `APPROVED` | 둘 다 non-null | 둘 다 non-null | 1개 이상 |

짝의 한쪽만 기록하는 부분 event는 모든 상태에서 거부한다. `reviewed_at`은 `authored_at`보다
빠를 수 없고, `approved_at`은 `reviewed_at`보다 빠를 수 없다. 기존 작성자·검토자·승인자 identity
분리, system actor 금지, implementer self-approval 금지, external medical approval receipt 규칙,
evidence reference 정렬·중복 금지 규칙은 그대로 유지한다.

`DRAFT`의 `evidence_review_refs=[]`는 아직 review event가 없다는 상태와 맞춘다. `REVIEWED`와
`APPROVED`는 실제 검토 근거를 immutable reference로 최소 한 건 남겨 reviewer identity와
timestamp만으로 완료 검토를 주장하지 못하게 한다.

### Actor role

`ActorRoleV12`에 `EVALUATION_REVIEWER`를 추가한다. 이 역할은 팀 내부의 Dataset Case Gold,
Fixture, Evidence 또는 Evaluation artifact 교차 검토를 나타내며 `MEDICAL_REVIEWER`와 다르다.

- `EVALUATION_REVIEWER`는 `reviewed_by`에 사용할 수 있다.
- 이 역할만으로 외부 의료·약학 승인을 주장할 수 없다.
- `external_medical_review_status`와 외부 approval receipt 규칙은 바꾸지 않는다.
- Safety Case와 Dataset Manifest의 `approved_by` 역할 제한은 기존
  `PRODUCT_SAFETY_REVIEWER | MEDICAL_REVIEWER`, `DATASET_CUSTODIAN` 규칙을 유지한다.
- Schema Set `1.0.0`과 `1.1.0`은 새 역할을 계속 거부한다.

## Python Model Boundaries

새 `common_v1_2.py`는 기존 안정 타입을 재사용하면서 `ActorRoleV12`, `ActorRefV12`,
`ReviewProvenanceV12`를 소유한다. 기존 `common.py`의 enum과 provenance 모델은 수정하지 않는다.

`authoring_v1_2.py`는 1.1 Rule outcome·runtime fixture 계약을 그대로 상속하고 다음 모델의
`schema_version`과 provenance 타입만 바꾼다.

- 다섯 Case variant와 discriminated union adapter
- Dataset Manifest
- Evidence Mapping Manifest
- Critical Claim Rubric
- Protected Artifact Receipt

`policy_v1_2.py`는 Evaluation Profile, Suite Definition, Evaluation Policy의 기존 구조와 validator를
재사용하고 `schema_version`과 provenance 타입만 바꾼다. Comparison Policy는 변경하지 않는다.

Versioned 모델의 validator는 1.1의 Rule·eligibility·fault 제약과 기존 approval-role 제약을 모두
보존한다. 타입 override 때문에 기존 validator가 누락되지 않는지 모델 단위 회귀 테스트로
검증한다.

## Loader Dispatch

Loader의 version bundle은 Manifest와 Case adapter뿐 아니라 다음 모델까지 명시적으로 소유한다.

- Evidence Mapping Manifest
- Critical Claim Rubric
- Evaluation Profile
- Evaluation Policy
- Suite Definition
- Protected Artifact Receipt

Manifest의 `schema_version`으로 authoring bundle을 선택한 뒤 모든 Dataset 구성 artifact를 같은
bundle의 모델로 검증한다. `1.0.0` bundle은 기존 1.0 모델, `1.1.0` bundle은 Case·Manifest만 1.1이고
나머지는 기존 1.0 모델, `1.2.0` bundle은 위 8개 versioned 모델을 사용한다.

그 다음 Evaluation Policy의 `artifact_schema_set_ref.reference.version`과 Schema Set registry를
확인하고, Dataset graph에 포함된 각 artifact의 `schema_id/schema_version`이 registry member
version과 exact-match하는지 검증한다. Manifest와 Case만 확인하던 기존 검증을 provenance가
변경된 모든 Dataset 구성 member로 확장한다. 알 수 없는 버전이나 혼합 버전은
`EVAL_SCHEMA_INVALID` 또는 `EVAL_MANIFEST_INVALID`로 fail-closed한다.

`FROZEN` child-Gold closure는 Schema Set 1.1과 1.2 모두에 적용한다. 1.2에서 DRAFT reviewer가
null이어도 non-frozen load는 허용하지만, `FROZEN`은 기존대로 모든 Case, Evidence Mapping,
Critical Claim Rubric의 Team `APPROVED`를 요구한다.

## JSON Schema Export and Parity

Schema registry에 `SCHEMA_REGISTRY_V1_2`를 추가하고 18개 unique member를 검증한다. Export CLI는
명시적인 `--schema-set-version 1.2.0`을 지원하되 기본값 `1.0.0`을 유지한다.

Export 후처리는 `ReviewProvenanceV12` definition에 Team Gold state matrix와 같은 Draft 2020-12
conditional을 추가한다. Pydantic과 JSON Schema에 동일한 positive/negative payload matrix를
적용해 parity를 확인한다.

다음 불변 조건을 byte-level test로 고정한다.

- committed `evals/schemas/1.0.0/`은 fresh 1.0 export와 동일하다.
- committed `evals/schemas/1.1.0/`은 fresh 1.1 export와 동일하다.
- 1.2에서 재사용한 10개 member는 원본 member canonical bytes와 동일하다.
- committed `evals/schemas/1.2.0/`은 fresh 1.2 export와 동일하다.
- Schema Set hash는 18개 정렬 tuple
  `{schema_id, schema_version, schema_sha256}`에서 결정적으로 계산된다.

계산된 1.2 hash는 Decision, `rag-evaluation-v1.md`, `evals/README.md`에 기록하고 문서의 hash를
계산값과 비교하는 테스트를 추가한다. 이 테스트는 Schema 변경 뒤 문서 문자열이 stale 상태로
남는 것을 막는다.

## #214 Migration Boundary

#241은 PR #236의 153개 Dataset 파일을 수정하지 않는다. #241 병합 뒤 PR #236이 최신 `develop`을
반영하고 다음을 수행한다.

1. Dataset graph의 8개 affected artifact type을 member version `1.2.0`으로 이관한다.
2. Team `DRAFT`인 모든 provenance에서 `reviewed_by`, `reviewed_at`을 `null`로 두고
   `evidence_review_refs=[]`를 유지한다.
3. 실제 Gold·Fixture 검토가 끝난 뒤 `EVALUATION_REVIEWER` identity, 실제 timestamp,
   immutable review evidence reference를 별도 commit으로 기록한다.
4. Product·Safety 승인과 최종 재검토 뒤에만 `APPROVED/FROZEN`으로 전환한다.

PR #236의 NO_MATCH 입력, duplicate ingredient, Evidence 정답 메타데이터 문제는 Dataset 의미
수정이므로 #241에 섞지 않고 #236에서 처리한다.

## Validation

- Model tests: 각 Team Gold 상태의 valid shape와 null/non-null pair 오류
- Role tests: 1.2 `EVALUATION_REVIEWER` 허용, 1.0·1.1 거부, approval role 불변
- Schema parity tests: 같은 payload matrix를 Pydantic과 exported JSON Schema에 적용
- Loader tests: 1.2 complete graph load, mixed member version 거부, unknown version 거부
- Closure tests: 1.2 FROZEN child DRAFT/REVIEWED 거부
- Regression tests: 1.0 DEV fixture, 1.1 authoring behavior, committed schema bytes 불변
- Documentation hash test: 계산된 Schema Set 1.2 hash와 세 문서 값 exact-match
- Static checks: Ruff, format check, mypy, `git diff --check`

## Non-Goals

- 153개 HOLDOUT·SAFETY_REGRESSION Dataset 내용 또는 hash graph 수정
- Gold·Fixture 실제 검토 수행이나 reviewer를 미리 기록하는 것
- 외부 의료·약학·Privacy·Source 승인
- Dataset Freeze 또는 Production 공개 Gate 활성화
- Runner, Reporter, Baseline, Metric, CI 또는 Release Gate 구현
