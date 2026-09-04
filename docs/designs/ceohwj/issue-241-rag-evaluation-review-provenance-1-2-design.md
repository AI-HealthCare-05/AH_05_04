# Issue #241 RAG 평가 검토 Provenance Schema 1.2 설계

## 상태와 범위

- 이슈: `#241`
- 범위: `#214` Dataset Freeze 이전의 Evaluation review provenance 호환성 보정
- 정본: `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`, RAG `evaluation-plan.md@1.35`
- 구현 순서: `#241` → `#214` / PR `#236` → `#157`
- 결과: `rag-eval.schema-set@1.2.0`으로 구현·병합 완료

## 문제

Schema Set `1.0.0`과 `1.1.0`은 `team_gold_status`와 무관하게 `reviewed_by`, `reviewed_at`을
필수로 요구했다. 실제 검토 전 `DRAFT` artifact에도 검토자와 시각을 미리 기록해야 했고, 내부
Evaluation 검토를 `MEDICAL_REVIEWER`로 표현해 외부 의료 승인으로 오독할 위험이 있었다.

`DRAFT` 상태만으로 허위 승인을 막더라도 reviewer와 timestamp는 완료된 review event를 뜻한다.
지정 예정 정보를 완료 provenance 필드에 넣으면 상태와 event 의미가 충돌한다.

## 설계 목표

1. 실제 검토 전 `DRAFT`에는 reviewer identity와 timestamp를 기록하지 않는다.
2. `DRAFT`, `REVIEWED`, `APPROVED`와 review·approval 필드를 양방향으로 결속한다.
3. 내부 Evaluation 검토와 외부 의료 검토를 역할 수준에서 분리한다.
4. Schema Set `1.0.0`, `1.1.0`의 동작과 canonical byte를 보존한다.
5. Pydantic·Loader는 전체 관계를 fail-closed하고 JSON Schema는 portable 구조를 preflight한다.

## 선택한 방식

기존 Set을 수정하지 않고 additive `rag-eval.schema-set@1.2.0`을 추가한다. 18개 member 중
provenance definition을 포함하는 다음 8개만 `1.2.0`으로 올리고 나머지 10개는 기존 version과
canonical byte를 재사용한다.

| Schema member | 1.2 member version | 이유 |
| --- | --- | --- |
| `rag-eval.case` | `1.2.0` | Case Gold review provenance |
| `rag-eval.dataset-manifest` | `1.2.0` | Dataset review/freeze provenance |
| `rag-eval.evidence-mapping-manifest` | `1.2.0` | Evidence Mapping provenance |
| `rag-eval.critical-claim-rubric` | `1.2.0` | Rubric provenance |
| `rag-eval.evaluation-profile` | `1.2.0` | Profile provenance |
| `rag-eval.suite-definition` | `1.2.0` | Suite provenance |
| `rag-eval.evaluation-policy` | `1.2.0` | Policy provenance |
| `rag-eval.protected-artifact-receipt` | `1.2.0` | `recorded_by` provenance |

## ReviewProvenanceV12 계약

모든 JSON key는 계속 명시하며 optional은 key 생략이 아니라 `null` 값 허용을 뜻한다.

| `team_gold_status` | `reviewed_by` / `reviewed_at` | `approved_by` / `approved_at` | `evidence_review_refs` |
| --- | --- | --- | --- |
| `DRAFT` | 둘 다 `null` | 둘 다 `null` | 빈 배열 |
| `REVIEWED` | 둘 다 non-null | 둘 다 `null` | 1개 이상 |
| `APPROVED` | 둘 다 non-null | 둘 다 non-null | 1개 이상 |

필드 쌍의 한쪽만 기록한 partial event는 거부한다. `reviewed_at >= authored_at`,
`approved_at >= reviewed_at`이어야 한다. 작성자·검토자·승인자 identity 분리, system actor 금지,
self-approval 금지, 외부 의료 approval receipt, evidence ref 정렬·중복 금지 규칙은 유지한다.

`ActorRoleV12`에는 `EVALUATION_REVIEWER`를 추가한다. 이 역할은 내부 Dataset Gold·Fixture·Evidence
교차 검토에만 쓰며 외부 의료·약학 승인을 대신하지 않는다. `external_medical_review_status`와 외부
receipt 규칙, Safety·Dataset approver 역할 제한은 변경하지 않는다. 1.0과 1.1은 새 역할을 거부한다.

## Python model과 Loader 경계

- `common_v1_2.py`: `ActorRoleV12`, `ActorRefV12`, `ReviewProvenanceV12`
- `authoring_v1_2.py`: 다섯 Case variant, Dataset Manifest, Evidence Mapping, Rubric, Receipt
- `policy_v1_2.py`: Evaluation Profile, Suite, Evaluation Policy
- Comparison Policy: 기존 version 유지

Loader bundle은 Manifest·Case뿐 아니라 Evidence Mapping, Rubric, Profile, Policy, Suite, Receipt model도
명시적으로 선택한다. graph의 모든 `schema_id/schema_version`은 registry member와 exact-match해야 한다.
unknown 또는 mixed version은 `EVAL_SCHEMA_INVALID`나 `EVAL_MANIFEST_INVALID`로 실패한다.

`FROZEN` child-Gold closure는 1.1과 1.2 모두 적용한다. non-frozen 1.2는 reviewer가 `null`인
`DRAFT`를 허용하지만 `FROZEN`은 모든 Case·Evidence Mapping·Rubric의 Team `APPROVED`를 요구한다.

## JSON Schema와 검증

JSON Schema에는 Team Gold 상태 matrix를 Draft 2020-12 conditional로 export한다. actor identity 중복,
system actor·role 조합, timestamp 순서처럼 portable JSON Schema만으로 안전하게 비교할 수 없는 규칙은
Pydantic과 Loader가 권위 있게 검증한다. JSON Schema 단독 통과는 Dataset 수용을 뜻하지 않는다.

다음 byte 불변식을 회귀 테스트로 고정한다.

- committed 1.0과 1.1은 fresh export와 동일하다.
- 1.2에서 재사용한 10개 member는 원본 canonical byte와 동일하다.
- committed 1.2는 fresh export와 동일하다.
- Schema Set hash는 정렬된 18개 `{schema_id, schema_version, schema_sha256}`에서 결정된다.

## #214 이관 경계와 비목표

`#241`은 PR #236의 Dataset resource를 수정하지 않는다. 병합 후 #214가 8개 artifact type을 1.2로
이관하고, `DRAFT` reviewer 필드를 `null`로 둔 뒤 실제 검토 event와 immutable evidence를 기록한다.
Product·Safety 승인과 최종 재검토 전에는 `APPROVED/FROZEN`으로 전환하지 않는다.

153개 Dataset 내용, 실제 검토 수행, 외부 의료·약학·Privacy·Source 승인, Dataset Freeze,
Production gate, Runner·Reporter·Baseline·Metric·CI·Release Gate는 이 이슈의 비목표다.
