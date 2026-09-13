# Guideline Card typed port 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Target 투영 · RAG-15 persistence-free kernel 구현 검토 중(#179, PR #414) · Current 아님 |
| 구현 담당 | 정현우 — AI/RAG Guideline Card |
| 책임 리뷰 | 권가빈 — Safety·제품 수용, 김지혜 — Source provenance·RAG-14 연결 |
| 상위 승인 근거 | [PD-125 RAG P0 Contract Freeze](../../../governance/decisions/2026-08-31-rag-p0-contract-freeze.md), [RAG Runtime v1](./rag-runtime-v1.md), [Safety Result·Citation v2](./safety-result-v2.md) |
| Source 승인 경계 | [PD-362 Source Snapshot 승인·거부 경계](../../../governance/decisions/2026-09-09-source-snapshot-approval-boundary.md) |
| 추적 Issue | [#179](https://github.com/AI-HealthCare-05/AH_05_04/issues/179) |

## 목적과 상태

이 문서는 RAG-14 Evidence Gate 결과와 승인된 Source provenance를 환자 표시 전
`GuidelineCardOutcome`으로 고정하는 RAG-15 typed port 계약이다. 현재 구현 범위는
`ai_worker.tasks.rag.guideline_card.finalize_guideline_card`의 persistence-free kernel과
합성 fixture·회귀 테스트다.

RAG-05 production Source adapter, DB 저장, Backend/OpenAPI DTO, RAG-16 Citation
Authorization·Release Gate 연결은 이 구현에 포함되지 않는다. 따라서 이 문서와 PR의
병합만으로 Current Runtime, 환자 공개 또는 외부 승인 완료를 주장하지 않는다.

## 입력 계약

`GuidelineCardRequest`는 다음 필드를 가진다.

| 필드 | 필수성 | 계약 |
| --- | --- | --- |
| `medication_identities` | 필수, 1개 이상 | 확정 처방 Version의 약품 식별자만 허용하고 중복을 거부한다. |
| `evidence_gate_outcome` | 필수 | RAG-14 `EvidenceGateOutcome`. 성공 경로는 같은 요청에서 평가된 `SUCCEEDED/SUFFICIENT` 결과만 허용한다. |
| `draft` | 조건부 | 생성 성공 경로에서 필수다. `generation_failure`가 있으면 partial draft는 검증·공개하지 않고 버린다. |
| `generation_failure` | 조건부 | 생성 실패 경로에서 필수다. partial `draft`와 함께 전달돼도 이 실패가 우선한다. |
| `policy` | 필수 | action template, 최대 Claim 수, 불확실성·상담 문구 hash를 결속한 `VersionedGuidelinePolicy`. |
| `provenance` | 필수 | prompt·model·parser·validator의 불변 Artifact 참조다. |
| `approved_fallbacks` | 필수 | `GuidelineFallbackCode` 전체에 대해 코드별 정확히 하나의 승인 Artifact가 있어야 한다. |
| `evaluated_at` | 필수 | timezone-aware UTC이며 RAG-14 `trace.evaluated_at`과 정확히 같아야 한다. |
| `approved_evidence_bindings` | 성공 시 필수, 1개 이상 | Gate가 선택한 Evidence와 medication·scope·action·assessment를 결속한다. 실패 fallback 경로에서는 비어 있을 수 있다. |

입력 객체는 호출자가 소유한 mutable graph로 간주한다. Finalizer는 어떤 외부 verifier도
호출하기 전에 전체 request graph와 verifier 입력을 분리 snapshot하고, 이후 구조 검증·승인·
Card 및 fallback 출력 모두 request snapshot만 사용한다. 호출 중 원 policy, draft, medication,
Gate, provenance, binding 또는 fallback graph가 바뀌어도 검증·출력에는 반영하지 않는다.
Request/fallback snapshot 생성·구조 검증 실패는 `VALIDATION_FAILED`, verifier에 전달한 분리
입력의 변조나 verifier 예외는 `DEPENDENCY_UNAVAILABLE`로 fail-closed한다.

## 승인 검증 포트

`GuidelineApprovalVerifierPort.verify(artifact_ref)`는 다음 두 결과만 반환한다.

- `GuidelineApprovalVerificationSuccess`: 요청한 `artifact_ref`와 exact-match하는 참조 및
  별도 `verifier_artifact_ref`를 반환한다.
- `GuidelineApprovalVerificationFailure`: 해당 Artifact의 승인 근거가 없거나 일치하지 않음을 뜻한다.

Artifact의 self-hash 일치는 무결성 검사일 뿐 승인으로 취급하지 않는다. 모든 fallback,
Guideline policy, `ApprovedGuidelineEvidenceBinding`은 외부 verifier 성공이 필수다. 예외,
전달한 verifier 입력의 변조, protocol 밖 응답, 요청 Artifact 참조가 다른 success 또는
유효하지 않은 verifier Artifact 참조는 dependency failure로 닫는다. 선언된
`GuidelineApprovalVerificationFailure`만 승인 거부에 따른 validation failure다. 성공 Card와
fallback에는 실제 `verifier_artifact_ref`를 보존한다.

Fallback catalog 자체를 snapshot·구조 검증·승인 검증하지 못한 경우 어떤 표시 문구도
신뢰할 수 없으므로 `fallback_code`는 남기되 `fallback=None`이다. 소비자는 이 상태에서
코드로 문구를 합성하거나 미승인 기본 문구를 표시하면 안 된다. 검증된 fallback context를
확보한 뒤 policy 또는 binding 승인이 실패한 경우에만 해당 코드의
`VerifiedGuidelineFallback`을 반환할 수 있다.

## Source eligibility verifier 연결

RAG-14의 production `EvidenceEligibilityVerifierPort`는 자체 규칙을 새로 만들지 않고
Approved [PD-362](../../../governance/decisions/2026-09-09-source-snapshot-approval-boundary.md)의
Snapshot 사용 가능 판정을 정본으로 사용해야 한다. 각 선택 Evidence의 Source·Endpoint·Operation,
Snapshot 승인, Freshness Policy, 현재 Snapshot, version/hash/Receipt provenance를 원 요청 목적과
환경에 대해 검증하고 그 판정에 결속된 eligibility receipt를 반환한다.

최소한 `SNAPSHOT_NOT_APPROVED`, `SNAPSHOT_VALIDATION_FAILED`, `SNAPSHOT_SUPERSEDED`,
`SNAPSHOT_FRESHNESS_STALE`, `SNAPSHOT_PROVENANCE_INVALID`이면 eligibility를 거부한다.
RAG-15는 RAG-14가 선택한 evidence provenance와 `assessment_artifact_ref`,
`eligibility_receipt_ref`, `retrieval_receipt_ref`, 평가 시각을 exact-match하며 이 판정을
재구현하지 않는다. `GuidelineApprovalVerifierPort`는 policy·fallback·binding 승인용으로,
Source eligibility verifier와 서로 대체할 수 없다.

현재 RAG-14 typed port는 `EvidenceEligibilityVerificationFailure`의 세부 Source reason을
`EVIDENCE_INELIGIBLE`로 사영한다. 따라서 PD-362의 Snapshot 거부 reason은 Source 판정·감사
Receipt에 보존하되 RAG-15에서는 `EVIDENCE_INSUFFICIENT`로 닫힌다. 이를 RAG-15의
`EVIDENCE_STALE`로 재분류하거나 문자열에서 추정하지 않는다. Source reason을 RAG-15/RAG-16까지
전달해야 한다면 RAG-14 typed failure와 상태 계약을 별도 Decision으로 확장해야 한다.

## Claim·Citation·문구 검증

- Scope는 `FOOD_CAUTION`, `DAILY_ACTIVITY`만 허용하고 action class와 승인된 고정 action
  template을 exact-match한다.
- 모든 Claim은 요청의 medication identity 하나에 결속되고 Citation을 1개 이상 가진다.
- Citation의 `source_snapshot_ref`, `source_version`, `locator`, `content_sha256`은 Gate가
  선택한 `LIFESTYLE_GUIDELINE` provenance와 exact-match한다.
- Binding은 Gate selection의 canonical projection hash, assessment Artifact, medication,
  scope, action class와 action text hash를 결속한다.
- action text뿐 아니라 `uncertainty_text`와 `consultation_text`에도 진단 확정, 복용 중단,
  용량 증감, 새 처방 지시 등 금지 의료 행동 검증을 동일하게 적용한다. 승인 policy hash가
  일치해도 금지 문구가 있으면 Card 전체를 `VALIDATION_REJECTED`로 거부한다.
- v1의 `uncertainty_text`, `consultation_text`와 코드별 fallback text는 닫힌 승인 copy
  registry와 exact-match한다. Policy·fallback Artifact hash 및 외부 verifier 승인은 이
  allowlist를 대체하지 않는다. 문구 변경·추가는 새 version과 Safety 책임 리뷰가 필요하다.
- 사용자 표시 문자열은 한국어 안전 문구 범위와 길이를 검증하고 출력 시 분리 복사한다.

## Outcome 상태 조합

`card`와 `fallback`은 상호 배타적이다. 아래 조합 외에는 유효한 공개 후보가 아니다.

| 상황 | `status` | `reason` | `fallback_code` | payload |
| --- | --- | --- | --- | --- |
| Card 생성·전체 결속 성공 | `GENERATED` | `CARD_GENERATED` | `None` | `card` 필수, `fallback=None` |
| 근거 부족·eligibility 거부 | `NO_RESULT` | `EVIDENCE_INSUFFICIENT` | `NO_APPROVED_EVIDENCE` | 검증된 fallback |
| 근거 충돌 | `NO_RESULT` | `EVIDENCE_CONFLICTED` | `CONFLICTING_EVIDENCE` | 검증된 fallback |
| RAG-14 assessment 유효기간 만료 | `NO_RESULT` | `EVIDENCE_STALE` | `NO_APPROVED_EVIDENCE` | 검증된 fallback |
| Provider timeout | `NO_RESULT` | `PROVIDER_TIMEOUT` | `PROVIDER_TIMEOUT` | 검증된 fallback |
| dependency 실패 | `NO_RESULT` | `DEPENDENCY_UNAVAILABLE` | `DEPENDENCY_UNAVAILABLE` | 검증된 fallback 또는 승인 context가 없으면 `None` |
| 요청·Gate·생성·승인 validation 실패 | `VALIDATION_REJECTED` | `VALIDATION_FAILED` | `VALIDATION_FAILED` | 검증된 fallback 또는 승인 context가 없으면 `None` |
| 처방 Version 불일치 | `STALE` | `PRESCRIPTION_STALE` | `PRESCRIPTION_STALE` | 검증된 fallback |
| 실행 Context 불일치 | `STALE` | `EXECUTION_CONTEXT_STALE` | `EXECUTION_CONTEXT_STALE` | 검증된 fallback |
| 지원하지 않는 요청 | `LIMITED` | `UNSUPPORTED_REQUEST` | `UNSUPPORTED_REQUEST` | 검증된 fallback |

RAG-14 assessment 유효기간 만료가 `NO_APPROVED_EVIDENCE`로 사영되는 것은
[Safety Result·Citation v2](./safety-result-v2.md)의 승인된 공개 코드 계약을 따른다.
근거 자체가 부족한 경우와 assessment 갱신이 필요한 경우의 RAG-15 대응은 내부
`reason=EVIDENCE_INSUFFICIENT|EVIDENCE_STALE`로 구분한다. 이 차이를 공개 문구 enum 확장으로
표현하지 않으며, 새 fallback code가 필요하면 별도 Decision과 Safety 책임 리뷰를 선행한다.
PD-362 Snapshot freshness 거부의 운영 대응은 위 Source eligibility 경계대로 Source 판정·감사
Receipt에서 구분한다.

## RAG-16 소비 경계

RAG-16은 이 outcome을 입력으로 받되 다음을 추정하지 않는다.

- `GENERATED`만으로 공개하지 않고 원 REQUEST guard, 별도 `CITATION_AUTHORIZATION/PASS`,
  Claim-Citation 검증과 최종 Release Gate를 모두 확인한다.
- `fallback_code`가 있어도 `fallback=None`이면 표시 가능한 결과가 아니다. 임의 문구 생성 없이
  안전한 결과 commit 실패로 처리하며 Safety Result v2의 Job failure 경계를 따른다.
- `fallback`이 존재하면 `code`, text Artifact와 `approval_verifier_ref`를 함께 저장·검증한다.
- Card의 Citation·binding·policy verifier provenance를 누락하거나 문자열 ID로 축약하지 않는다.

## 최소 검증

- RAG-14 선택 provenance·receipt·평가 시각의 exact binding
- Claim의 medication·scope·action template·Citation 결속
- policy, binding, fallback의 외부 승인 성공·거부·dependency failure
- verifier 호출 중 원 request·fallback 및 분리 verifier 입력 변조 회귀
- action, uncertainty, consultation 모든 출력 필드의 금지 의료 행동 회귀
- 위 상태 조합과 `fallback=None` 소비 경계
- Source stale과 evidence insufficient의 동일 공개 code·상이한 내부 reason
