# Safety Result·Citation 계약 v2

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Target · Not implemented — RAG-00 / 2026-09-01 |
| 구현 담당 | 정현우 — Citation·Validator·Release Gate |
| 책임 리뷰 | 권가빈 — Safety·제품 수용, 송은영 — Backend·DB·공개 DTO, 남한솔 — 환자 표시·오류 UX |
| 외부 정본 | Manifest `post-mvp-rag-evaluation-contract@2026-08-29.11`; Design `1.50` SHA-256 `e83415326dd08cda61353d7cd8bf4e6d591bb99f51a8a3daa498421d8772535a`; DB `1.47` SHA-256 `f88ec11aaa6671184f2d0f5076219bf2ad51525b9e6a136ec5389afd2af82aea` |
| 기존 선행 계약 | [Safety Result 계약 v1](./safety-result-v1.md) — v2 승인 이력을 위해 v1 파일을 유지하며 Current Runtime으로 간주하지 않음 |
| Last verified | 2026-09-01 |

## 목적과 승격 경계

이 문서는 RAG-00에서 필요한 Safety Result v2 상태·오류·다형 Citation 목표를 고정한다. 기존 v1은 Approved Contract Freeze v4의 선행 Target으로 유지한다. v2는 DTO·OpenAPI·Migration·Contract Test와 지정 리뷰어 승인이 같은 구현 PR에 포함되기 전에는 Current Runtime이 아니다.

## 상태 축과 공개 판정

| 상태 그룹 | 값 |
| --- | --- |
| `ai_job.status` | `PENDING`, `PROCESSING`, `RETRY_WAIT`, `COMPLETED`, `FAILED`, `STALE` |
| `execution_status` | `SUCCEEDED`, `NO_RESULT`, `TIMED_OUT`, `DEPENDENCY_ERROR`, `VALIDATION_ERROR` |
| `evidence_status` | `SUFFICIENT`, `INSUFFICIENT`, `CONFLICTED`, `STALE` |
| `response_level` | `ROUTINE`, `URGENT`, `EMERGENCY`, `UNKNOWN` |
| `safety_disposition` | `NORMAL`, `URGENT_ROUTED`, `EMERGENCY_ROUTED`, `BLOCKED_ACTION`, `UNKNOWN_RISK` |
| `release_decision` | `PASS`, `LIMITED`, `REJECTED`, `STALE` |
| `is_current` | `true`, `false` |

`AI_JOB=COMPLETED`나 `execution_status=SUCCEEDED`만으로 공개하지 않는다. 최종 `release_gate`가 `release_decision`, `is_current`, Claim-Citation 검증과 Safety 결과를 함께 확인한다.

| 상황 | execution | release | safety | 공개 |
| --- | --- | --- | --- | --- |
| 승인 정상 답변 | `SUCCEEDED` | `PASS` | `NORMAL` | 가능 |
| 승인 긴급 안내 | `SUCCEEDED` | `PASS` | `URGENT_ROUTED` | 가능 |
| 승인 응급 안내 | `SUCCEEDED` | `PASS` | `EMERGENCY_ROUTED` | 가능 |
| 진단·처방 변경·용량 조절·복용 중단 등 금지 행동 요청 | `SUCCEEDED` | `LIMITED` | `BLOCKED_ACTION` | 금지 행동을 수행하지 않는 승인 안내만 가능 |
| 위험 수준 판단 불가 | `NO_RESULT` | `REJECTED` | `UNKNOWN_RISK` | 승인 fallback만 가능, 일반 Retrieval·Provider 금지 |
| 그 밖의 승인된 범위 제한 안내 | `SUCCEEDED` | `LIMITED` | Router 결과 | 제한 응답만 가능 |
| 근거 없음·충돌 | `NO_RESULT` | `REJECTED` | Router 결과 | 승인 fallback만 가능 |
| Provider·검증 실패 | 해당 실패 상태 | `REJECTED` | Router 결과 | 승인 fallback만 가능 |
| 실행 Context 불일치 | 원래 값 보존 | `STALE` | 원래 값 보존 | 금지, `is_current=false` |

## STALE과 공개 오류

- 처방 Version 불일치: 공개 `PRESCRIPTION_STALE`
- Patient Context·Identification·Runtime Bundle·환경 Revision·Resolver Member 불일치: 공개 `EXECUTION_CONTEXT_STALE`
- 내부 `stale_reason`: `PATIENT_CONTEXT_STALE`, `IDENTIFICATION_STALE`, `RUNTIME_RELEASE_STALE`, `RUNTIME_ENVIRONMENT_SUSPENDED`, `RESOLVER_MEMBER_REVOKED`

모든 Context 불일치 종결은 `AI_JOB=STALE + release_decision=STALE + is_current=false`다. 내부 상세 사유는 공개 DTO에 노출하지 않는다. Source Snapshot 만료는 실행 Context STALE이 아니라 `evidence_status=STALE + execution_status=NO_RESULT + release_decision=REJECTED + NO_APPROVED_EVIDENCE`로 처리한다.

공개 `fallback_code`는 `NO_APPROVED_EVIDENCE`, `CONFLICTING_EVIDENCE`, `SAFETY_ROUTED`, `PROVIDER_TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `VALIDATION_FAILED`, `PRESCRIPTION_STALE`, `EXECUTION_CONTEXT_STALE`, `UNSUPPORTED_REQUEST`로 제한한다. `REJECTED`에서는 생성한 의료 답변을 버리고 승인된 고정 fallback만 공개한다. fallback도 안전하게 commit하지 못한 실행만 `AI_JOB=FAILED`다.

## Claim-Citation 계약

허용 Source 유형은 다음 다섯 가지다.

- `PRESCRIPTION`
- `KNOWLEDGE_CHUNK`
- `INTERACTION_RULE`
- `LIFESTYLE_GUIDELINE`
- `SAFETY_POLICY`

각 Citation은 하나의 Claim과 연결되고 `source_type`에 맞는 유형별 Evidence FK를 정확히 하나 가진다. 범용 문자열 `source_id` 하나로 여러 Evidence 대상을 참조하지 않는다. Source 기반 Citation은 실행에서 실제 사용한 Source Snapshot·Endpoint/Operation 또는 Artifact Member, Bundle 승인 Version, Runtime Guard Decision과 locator까지 재현할 수 있어야 한다. `PRESCRIPTION`만 Source 실행 Provenance FK가 nullable일 수 있다.

Source 기반 Citation은 원 환자 요청의 `REQUEST/PASS`만으로 공개할 수 없다. Citation Finalizer는 별도 `CITATION_AUTHORIZATION/PASS` Guard를 요구하고, 이 Guard가 원 REQUEST의 Bundle·환경·Manifest Hash·정렬 요청 Scope를 exact-match하며 실제 Citation Source·Member의 `PATIENT_CITATION` 목적 승인을 다시 확인해야 한다. Citation은 해당 Guard에서 `selected_for_operation=true`이고 Source·Member Decision이 모두 `PASS`인 실행 Usage만 참조한다.

의료 Claim은 `SUPPORTED`일 때만 공개한다. `PARTIALLY_SUPPORTED`는 비의료 보조 Claim에만 제한적으로 허용하고 `CONTRADICTED`, `NOT_SUPPORTED`는 공개하지 않는다.

공개 Citation DTO는 다음으로 제한한다.

- `citation_id`, `claim_key`, `source_type`
- `title`, nullable `url`
- `source_version`, `locator`
- 짧은 표시용 `excerpt`

내부 score, Guard 상세, Source 원문 전체와 폐기 답변은 공개하지 않는다.

## 최소 Contract Test

- 상태축 허용 조합과 `is_current=false` 공개 차단
- Chat `response_level`별 Provider·Retrieval 호출 여부와 공개 결과, `UNKNOWN/UNKNOWN_RISK` 일반 실행 0건
- 금지 행동 요청의 `SUCCEEDED/LIMITED/BLOCKED_ACTION`과 처방 변경·중단 지시 생성 0건
- Source 만료와 실행 Context STALE 의미 분리
- 공개 `EXECUTION_CONTEXT_STALE`과 내부 `stale_reason` 분리
- Claim별 유형 FK 정확히 하나, 잘못된 유형·FK 조합 차단
- 다섯 Citation 유형의 Source Snapshot·locator·실행 Usage 재현
- Citation별 별도 `CITATION_AUTHORIZATION/PASS`, 원 REQUEST·Scope exact-match와 `PATIENT_CITATION` 승인
- 의료 Claim의 Citation 누락·변조·근거 불일치 공개 0건
- Candidate·Identification·OTC·Chat/Guide 접수·상태·결과·오류 응답 `Cache-Control: no-store`

## 공개 게이트

v2 DTO·OpenAPI·Migration·Contract/Integration Test와 지정 리뷰어 승인이 완료되기 전에는 신규 Citation 유형과 `EXECUTION_CONTEXT_STALE` 경로의 Release Gate를 열지 않는다. 필수 외부 의료·약학·Source·Privacy·Safety 승인이 완료될 때까지 `PUBLIC_TRACK_F=false`를 유지한다.
