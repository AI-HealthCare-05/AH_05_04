# Product Decision: RAG P0 공유 계약 Freeze

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-125-20260831` |
| 상태 | Approved Target · Not implemented — 2026-09-01 |
| 제안일 | 2026-08-31 |
| 제안자 | 정현우 — AI/RAG 구현 담당 |
| 추적 Issue | [#125](https://github.com/AI-HealthCare-05/AH_05_04/issues/125) |
| 적용 범위 | Post-MVP-1 Track F RAG P0 Approved Target |

## 결정

외부 Authority Manifest `post-mvp-rag-evaluation-contract@2026-08-29.11`을 RAG P0 공유 계약의 정본으로 채택한다. 아래 다섯 논리 계약을 하나의 변경 세트로 관리한다.

- Medication Candidate Search·Identification v1 — 저장소에서는 기존 `medication-identification-v1.md`에 통합
- RAG Source 수집·정규화 v1
- Rule-first Curated Evidence RAG Runtime v1
- RAG Evaluation·Release Gate v1
- Safety Result·Citation v2

### Authority Receipt

| Artifact | Version·Role | SHA-256 |
| --- | --- | --- |
| Authority Manifest | `2026-08-29.11` | `f2c98884c841d3fccdbec552f14aad1fd471730eae6d80c472c1b332ed95a570` |
| `rag-design` | `1.50 · NORMATIVE_ARCHITECTURE` | `e83415326dd08cda61353d7cd8bf4e6d591bb99f51a8a3daa498421d8772535a` |
| `rag-db-schema` | `1.47 · NORMATIVE_PHYSICAL_TARGET` | `f88ec11aaa6671184f2d0f5076219bf2ad51525b9e6a136ec5389afd2af82aea` |
| `rag-source-policy` | `1.18 · NORMATIVE_SOURCE_GOVERNANCE` | `35842d2cbe54201ff9fb5580616055eda613fe4c16ac6d60daa7f8859d2f28e3` |
| `rag-evaluation-plan` | `1.35 · NORMATIVE_EVALUATION` | `526f83dedc05a777c0963bfa10bb8bd8ebd940ab3eb12523f4c8fa15447e542f` |
| `rag-implementation-order` | `1.16 · NON_NORMATIVE_EXECUTION_ORDER` | `15b6296e7c28e1603cc55ebcee357048f9061ebd522ffc5e8e1dcf3c17f9732e` |

`rag-implementation-order`는 Issue 분할·진행 순서에만 사용하며 enum·DTO·DB·평가 의미를 덮어쓰지 않는다. Manifest 파일의 SHA-256과 각 문서 항목은 2026-09-01 로컬 Authority Bundle에서 재검증했다.

평가 Experiment Type은 `END_TO_END_RAG`를 사용하며 `END_TO_END_FINAL`은 사용하지 않는다. Local 평가 후보 Bundle에는 Run 단위 `EVALUATION_CANDIDATE`, Required Case 단위 `EVALUATION_REQUEST` Guard를 적용한다. 두 Guard는 환자용 API나 Application 결과를 생성하지 않으며 합성·승인 비식별 Dataset과 승인 Runner로 제한한다.

Chat은 단일 `JOB_EXECUTE` 안에서 Safety Intake를 먼저 실행한다. `URGENT`, `EMERGENCY`, `UNKNOWN`은 일반 RAG를 호출하지 않고 승인 Safety Flow로 끝낸다. `ROUTINE`만 Identification Preflight 후 Rule·Retrieval·Composer로 진행하며 단계별 두 번째 Outbox를 만들지 않는다.

Chat Job은 접수 Transaction에서 최소 Intake Context만 고정한다. `ROUTINE` 판정 뒤 현재 처방·Identification·Bundle을 잠금 재검증하여 Full Execution Context를 원자적으로 확장하고, 불일치하면 일반 RAG 없이 `STALE`로 종료한다. Guide는 Identification Preflight 통과 후 Job·Full Context·Identification member·Outbox를 하나의 Transaction으로 생성한다.

Full Context의 Patient Context는 사용자가 확인한 질환 코드·정확한 대상의 알레르기·임신 상태·확정 Check-in·직접 선택 Barrier만 최소 구조화하여 고정한다. 신장·간 기능, 검사 결과, 진료 메모, 미확정 OCR, 식생활 Profile, LLM 추론 정보와 이전 Assistant 답변은 제외한다.

Chat 접수에는 최소 Safety Source Selection의 `REQUEST` Guard를 Intake Context와 함께 고정한다. `ROUTINE` Chat과 Guide는 일반 Rule·Retrieval 전에 별도 Full `REQUEST` Guard로 Bundle 전체 적격성과 실제 Source·Snapshot Selection을 검사하고 그 Decision을 Full Context에 고정한다. Citation 공개 전에는 원 Intake 또는 Full REQUEST와 동일 Bundle·환경·Manifest·Scope에 결속된 별도 `CITATION_AUTHORIZATION` Guard에서 실제 Citation Source의 `PATIENT_CITATION` 목적 승인을 다시 검사한다. Retrieval 없는 Safety Citation도 이 Guard를 생략할 수 없으며 Retrieval 승인은 환자 Citation 승인을 대신하지 않는다.

Citation 목표 유형은 `PRESCRIPTION`, `KNOWLEDGE_CHUNK`, `INTERACTION_RULE`, `LIFESTYLE_GUIDELINE`, `SAFETY_POLICY`의 다섯 가지다. 실행 Context가 최신이 아니면 공개 오류 `EXECUTION_CONTEXT_STALE`과 `release_decision=STALE`로 fail-closed 처리한다. `UNKNOWN`은 `NO_RESULT/REJECTED/UNKNOWN_RISK`, 진단·처방 변경·용량 조절·복용 중단 등 금지 행동은 `SUCCEEDED/LIMITED/BLOCKED_ACTION`으로 고정한다.

Source Runtime 적격성은 Source lifecycle 하나로 판단하지 않는다. Source·Endpoint·Operation 상태, 목적·환경별 Source Use Approval, License·Clinical 상태, Scope, Snapshot Freshness, Bundle Member와 Revocation을 모두 검사한다. Source Client는 HTTPS·승인 Host·Redirect/IP·Timeout·응답 크기·Content-Type·XML DTD/XXE·동시 수집·본문 성공 코드와 Retry 분류를 fail-closed로 검증한다.

정량 평가는 RAG Retrieval·Answer·Citation·Rule·Scope·Safety만 대상으로 한다. OCR·Resolver·Candidate 품질 비교는 RAG Metric에서 제외하고 별도 Contract Receipt의 `COMPLETED/PASS`만 선행조건으로 연결한다. 평가는 `AUTHORING | DEV | HOLDOUT | SAFETY_REGRESSION` Partition과 Leakage Group을 고정하고 실행 상태 `NOT_IMPLEMENTED | NOT_EVALUATED | INVALID | ERROR | COMPLETED`와 nullable 판정 `PASS | FAIL | INCONCLUSIVE | N/A`를 분리한다. 미실행은 `NOT_EVALUATED/null`이며 실행 완료 후 분모·표본·독립 Group 부족일 때만 `COMPLETED/INCONCLUSIVE`다.

## 기존 계약과의 대체·유지 관계

- 이 Decision과 Runtime v1이 승인되면 기존 [MFDS 공식 의약품 식별 계약 v1](../../contracts/targets/post-mvp-1/medication-identification-v1.md)의 Job 생성 조건은 Chat에 한해 위 2단계 Intake·Preflight 계약으로 대체된다. 자동 Guide의 “모든 활성 약제 `MATCHED` 후 Job 생성” 조건은 유지한다.
- Candidate Search 생성은 `Idempotency-Key` 적용 대상이 아니다. 사용자 후보 확인·거절만 기존 [멱등성 계약 v1](../../contracts/targets/post-mvp-1/idempotency-v1.md)의 Track F 규칙을 따른다.
- Candidate Search 생성은 상위 Prescription과 대상 Prescription Version Medication을 잠그고, 약제별 활성 `RUNNING | READY` Search를 Partial Unique로 최대 하나만 허용한다. 같은 Query Digest·Runtime Release Bundle·Candidate Index의 Search는 기존 행을 재사용하고, Context가 다르면 기존 활성 Search를 `INVALIDATED_INPUT_CHANGED`로 전환한 뒤 새 Search를 같은 Transaction에서 만든다. 최신 `MATCHED`가 있으면 승인된 재식별 경로 밖의 새 Search를 만들지 않는다.
- 후보 확인 요청은 `prescription_version_medication_id + candidate_search_result_id`, 거절 요청은 `search_id + candidate_search_result_id`를 사용하며 두 요청 모두 `Idempotency-Key`가 필수다. Candidate Search Finalizer는 Result 전체, Gate·표시 Flag·최종 상태를 하나의 Transaction에서 확정한다. 거절 후 새로 확정한 Prescription Version Medication에는 별도 후속 Search를 만들고 과거 Result를 재선택하지 않는다. P0에는 Prescription Version 간 안정적 Medication 계보 Key가 없으므로 `supersedes_search_id`는 `null`로 유지하며 추정 연결하지 않는다. 향후 계보 Key·Migration·무결성 규칙·Contract Test가 승인된 뒤에만 이 예약 필드를 활성화한다.
- 후보 확인 Transaction은 전역 잠금 순서에 따라 약제·Search·Result를 잠근 뒤 해당 Search가 유일한 활성 `READY`이고 최신 Identification에 새 `MATCHED`가 없는지 재검사한다. 먼저 성공한 Transaction만 Identification과 Search `CONSUMED`를 원자 저장한다. 같은 Search의 동일 멱등 재시도만 최초 결과를 재현하며, 다른 Search ID의 경쟁 확인은 `409 CANDIDATE_SEARCH_STALE` 또는 최신 Identification 변경 시 `409 IDENTIFICATION_CONTEXT_STALE`로 끝나고 신규 Identification을 만들지 않는다.
- PR #96의 활성 Prescription Version Medication에 없는 보험코드는 RAG P0 입력·검색 신호가 아니다. 별도 OCR·Prescription 공유 계약, Migration, 승인 MFDS Identifier Source와 Contract Test가 승인되기 전까지 보험코드 Feature는 비활성이다. HIRA 데이터는 사용하지 않는다.
- 검수 전 OCR·LLM 값은 Candidate 입력이 아니다. 사용자가 명시적으로 확정하여 활성 불변 Prescription Version Medication에 저장된 `medication_name`과 nullable `strength_text`만 사용한다.

이 Decision과 Target 문서를 현재 Runtime으로 주장하지 않는다. 외부 논리 계약 `medication-candidate-identification-v1`은 기존 의약품 식별 Target에 통합하며 상충하는 별도 Target을 만들지 않는다. Safety v1은 Approved v4 이력으로 보존하고 v2를 후속 Target으로 추가한다.

## 승인·승격 기록과 입력 Receipt

이 Decision과 Target 문서는 다음 담당 리뷰 영역을 모두 포함한다.

- 권가빈 (`@hazelnutflavoured`): Product·Safety·Evaluation 범위
- 송은영 (`@phina-io`): Backend·DB·소유권·Transaction
- 김지혜 (`@Jye-rookie`): OCR 확정 입력 경계와 PR #96 재사용
- 남한솔 (`@solia142`): Candidate 확인 UI·공개 DTO·`no-store`

Issue와 PR에는 각 담당자의 실제 GitHub 계정을 지정한다. 계정 매핑을 추정하지 않는다. RAG-00 변경 매핑이 승인 검토 후 달라졌으므로 승격 커밋에 대해 지정 리뷰어 재검토를 요청한다.

| 입력 Receipt | 현재 상태 | 적용 규칙 |
| --- | --- | --- |
| [Issue #136 Guide·Chat 책임 경계·상태 표시 기준](https://github.com/AI-HealthCare-05/AH_05_04/issues/136) | `OPEN` · 2026-09-01 기준 산출물·연결 PR 미확정 | RAG-00에서 같은 Session·Message·상태 UI 문서를 중복 작성하지 않는다. 확정 Contract·Receipt가 생긴 범위만 후속 구현에 사용하며, #125 통합 완료·Close는 Receipt 연결 전까지 차단한다. |

`#136 OPEN`은 RAG-00 Target 문서의 상태를 Current로 낮추거나 미승인 정책을 추정해 채울 근거가 아니다. 후속 Guide·Chat 구현은 #136에서 확정된 책임·표시 계약과 이 Decision의 RAG Runtime 경계를 함께 검증한다.

## 구현·공개 경계

- 이 Decision 승인은 RAG·LangGraph·Parser·DB migration·OpenAPI·Frontend 또는 Evaluation Run 구현 완료를 뜻하지 않는다.
- 실제 RAG 실행과 MFDS API 연동 확인은 Local 환경에서만 수행한다. Development·Staging 서버는 구축하지 않는다.
- 실제 환자정보와 API Key를 repository·fixture·로그에 저장하지 않는다.
- 구현 PR은 shared DTO, migration, OpenAPI, Contract·Integration Test와 RAG 평가 증빙을 함께 연결한다.
- 외부 Source·Privacy·의료·약학·Safety 승인 전에는 `PUBLIC_TRACK_F=false`를 유지한다.
- Runtime Bundle의 Worker 배포·재시도 호환성과 OTC 자유 입력 Identity는 별도 후속 Decision 전까지 Current 승격을 차단한다.
