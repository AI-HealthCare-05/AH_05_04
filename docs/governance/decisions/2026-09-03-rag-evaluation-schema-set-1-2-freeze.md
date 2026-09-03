# Product Decision: RAG Evaluation Schema Set 1.2 Freeze

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-241-20260903` |
| 상태 | Approved · Implemented by PR #245 |
| 제안일 | 2026-09-03 |
| 제안자·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Product·Safety·Evaluation 계약 승인 |
| 교차 리뷰 | 김지혜 (`@Jye-rookie`) — Gold·Fixture 역할 의미, 송은영 (`@phina-io`) — Schema·Loader·export parity |
| 추적 Issue | [#241](https://github.com/AI-HealthCare-05/AH_05_04/issues/241) |
| 적용 범위 | Post-MVP-1 Track F Evaluation review provenance compatibility |

## 결정

`rag-eval.schema-set@1.2.0`을 #214 HOLDOUT·SAFETY_REGRESSION Dataset 후보가 실제 Team Gold 검토를 기록할 때 사용할 Evaluation authoring 계약으로 고정한다. 이 Decision은 기존 Schema Set `1.0.0`과 `1.1.0`의 bytes, member version, Loader 동작을 변경하지 않는다. 이 Schema 승인·병합은 #214 Dataset 자체의 지정 검토나 `REVIEWED`·`APPROVED`·`FROZEN` 전이를 대신하지 않는다.

| Immutable field | 값 |
| --- | --- |
| Schema Set ID | `rag-eval.schema-set` |
| Schema Set version | `1.2.0` |
| Schema Set SHA-256 | `1bdc6c8d2c5b62415b7f2f59e42ffdf7d67243ae4cccd1e6b3a3116daae73b06` |
| Canonical member root | `evals/schemas/1.2.0/` |
| Member count | `18` |

Schema Set hash는 member별 `{schema_id, schema_version, schema_sha256}`를 정렬한 canonical JSON의 SHA-256이다. Set version과 member version은 같다고 가정하지 않는다.

## Member versioning

다음 8개 member만 `1.2.0`이다.

- `rag-eval.case`
- `rag-eval.dataset-manifest`
- `rag-eval.evidence-mapping-manifest`
- `rag-eval.critical-claim-rubric`
- `rag-eval.evaluation-profile`
- `rag-eval.suite-definition`
- `rag-eval.evaluation-policy`
- `rag-eval.protected-artifact-receipt`

나머지 10개 member는 이전 canonical bytes와 member version을 그대로 재사용한다. Loader는 선택된 Dataset bundle의 모든 graph artifact payload version을 이 registry member version과 exact-match한다.

## Review provenance state contract

| `team_gold_status` | reviewer | approver | `evidence_review_refs` |
| --- | --- | --- | --- |
| `DRAFT` | `reviewed_by=null`, `reviewed_at=null` | 둘 다 `null` | 빈 배열 |
| `REVIEWED` | 둘 다 non-null | 둘 다 `null` | immutable reference 1개 이상 |
| `APPROVED` | 둘 다 non-null | 둘 다 non-null | immutable reference 1개 이상 |

reviewer·review timestamp 또는 approver·approval timestamp의 한쪽만 기록한 payload는 거부한다. 작성자, reviewer, approver는 서로 다른 identity여야 하고 system actor를 사용할 수 없다. 실제 review event가 없을 때 지정 reviewer 이름이나 authoring 시각을 review provenance에 기록하지 않는다.

`REVIEWED`·`APPROVED` 상태의 `reviewed_by.role`은 `EVALUATION_REVIEWER`만 허용한다. 이 역할은 팀 내부 Gold·Fixture·Evidence·Evaluation artifact의 검토 역할이며, `MEDICAL_REVIEWER`, 외부 의료 검토, 의료·약학 승인 또는 Production 공개 승인을 뜻하지 않는다. `external_medical_review_status`와 external approval receipt의 기존 규칙은 유지한다.

Exported Draft 2020-12 JSON Schema는 구조 preflight만 담당한다. author/reviewer/approver의 cross-field identity 중복, system actor, role 조합과 event timestamp 순서는 표준 JSON Schema만으로 portable하게 표현하지 않으며, Loader의 Pydantic `ReviewProvenanceV12` 검증이 이 관계 제약의 권위 있는 fail-closed 수용 경계다. JSON Schema 성공은 Dataset 수용·Freeze 자격을 뜻하지 않는다.

Safety Case와 End-to-End Case의 Team approval은 계속 `PRODUCT_SAFETY_REVIEWER | MEDICAL_REVIEWER`만 허용하고 Dataset Manifest Team approval은 `DATASET_CUSTODIAN`만 허용한다. `EVALUATION_REVIEWER`는 approval-role allowlist를 넓히지 않는다.

## Freeze and migration boundary

`FROZEN` Dataset의 Case Gold, Evidence Mapping, Critical Claim Rubric Team `APPROVED` closure는 1.1과 1.2에서 계속 fail-closed다. 외부 의료·약학·Privacy·Source 승인과 `PUBLIC_TRACK_F`는 이 Decision 범위 밖이며 계속 닫혀 있다.

#241은 #214 Dataset 파일을 수정하거나 Freeze하지 않는다. #241 병합 뒤 PR #236은 Schema Set 1.2를 참조하고 모든 DRAFT provenance의 reviewer field를 `null`로 이관한다. 그 후 실제 Gold·Fixture 검토 event의 immutable evidence와 `EVALUATION_REVIEWER` identity를 별도 commit에 기록하고, 지정 책임 리뷰 승인 뒤에만 Dataset 상태를 승격한다.

Runner·Reporter·Baseline은 #157 범위이며 이 Decision에 포함하지 않는다.
