# Guide·Chat Session·Message·상태 구현 골격 v1

**문서 성격**: 구현 골격(제안)  
**담당자**: 송은영  
**리뷰어**: 남한솔, 정현우  
**우선순위**: P0  
**관련 트랙**: Track F (Guide·Chat RAG)

## 1) 문서 범위

이 문서는 Post-MVP-1에서 Guide·Chat/OTC를 동일한 비동기 Job 흐름으로 전환할 때의 구현 골격을 정리합니다.  
현재 단계에서는 아래 계약을 대조 기준으로 사용합니다.

- `docs/contracts/targets/post-mvp-1/async-job-v1.md`
- `docs/contracts/targets/post-mvp-1/idempotency-v1.md`
- `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md`
- `docs/contracts/targets/post-mvp-1/prescription-version-v1.md`
- `docs/contracts/targets/post-mvp-1/medication-identification-v1.md`
- `docs/contracts/targets/post-mvp-1/safety-result-v2.md`
- `docs/contracts/targets/post-mvp-1/rag-runtime-v1.md`
- `docs/contracts/current/profile-self-ownership-v1.md`

> ERD, 필드명/제약의 상세 구현은 아직 확정하지 않습니다. 구현 PR에서 실제 스키마와 migration을 작성할 때 별도 계약 또는 기존 계약 갱신으로 반영합니다.

이 문서는 실행 계약이 아니므로 현재 코드 동작을 대체하지 않습니다.  
현재 코드에서 동기 흐름이 남아 있는 부분은 `현황 설명`으로만 쓰고, 실제 구현 계약은 후속 계약 문서와 구현 PR에서 확정합니다.

## 2) 핵심 정리

### 2.1 비동기 접수로의 전환

- 동기 `201` 중심에서 벗어나, Guide/Chat 접수는 Post-MVP 기준 `202 Accepted + JobStatusResponse`로 진행한다.
- 세션 메시지 요청과 Guide 요청은 `Idempotency-Key`를 필수로 받는다.
- 접수 transaction에는 `IDEMPOTENCY_RECORD`를 반드시 함께 생성한다.
- `POST /api/v1/guides`, `POST /api/v1/chat-sessions/{session_id}/messages`는 상태 조회를 위한 `status_url`을 반환한다.
- `REVIEW_REQUIRED`는 Job 상태가 아니다. 다만 Job 유무는 기능별로 다르다: 자동 Guide는 Identification 실패 시 Job을 만들지 않는 동기 fallback으로 종료하지만, Chat은 Identification 이전에 이미 최소 Job(Safety Intake)과 Safety Triage를 수행하며, ROUTINE Preflight 실패는 새 fallback을 만들지 않고 **이미 생성된 Job**에 승인된 제한 응답을 저장한다(`rag-runtime-v1.md` "입력과 접수 Preflight" 섹션, "고정 실행 Graph"의 `medication_identification_preflight` 실패 분기). 이 저장에 쓰이는 정확한 `execution_status`/`release_decision` 조합은 `safety-result-v2.md`에서 확정한다. 이 구분을 지키지 않으면 Chat이 Safety Triage(긴급·응급 라우팅 포함)를 건너뛸 수 있다.

### 2.2 버전 경계

- Chat Session은 생성 시 `prescription_version_id`를 기준 버전으로 귀속한다(`prescription-version-v1.md` "하위 데이터 귀속").
- `AI_JOB`, `CHAT_MESSAGE`, `GUIDE`에는 처리 시점 버전 정합성이 반영되어야 하며, 처방 버전 변경 시 이전 non-terminal Job은 `STALE` 처리되어 현재 사용자 화면에 노출되지 않는다.
- `STALE`은 사용자 화면 비노출(`content = null`, `is_current = false`)을 의미하지만, Job/Safety 메타데이터는 보존한다.
- **Session의 Version 소비 불변식(Frontend 임의 추론 금지)**:
  - 사용자가 재접속해도 Session은 생성 시 귀속된 `prescription_version_id`를 계속 사용한다 — 재접속을 최신 Version 재조회 시점으로 취급하지 않는다.
  - Frontend는 최신 활성 Prescription Version을 자동으로 선택·추론해 Session에 대입하지 않는다. Version 귀속은 항상 Backend가 Session 생성 시점에 결정한 값을 따른다.
  - 같은 Session에 속한 Message·Job은 그 Session의 귀속 Version과 정합성을 유지해야 하며, Message/Job마다 다른 Version을 임의로 참조하지 않는다.
  - 처방 Version이 변경돼도 기존 Session을 새 Version으로 자동 재귀속하지 않는다 — 새 Version에서 이어가려면 새 Session이 필요하다(정확한 FK·저장 방식은 구현 PR에서 확정).

## 3) Frontend 결과 상태축(필수 분리)

Frontend는 단일 성공/실패 코드가 아니라 아래 축을 분리해 소비해야 한다.

- `generation_status`
- `execution_status`
- `evidence_status`
- `response_level`
- `release_decision`
- `safety_disposition`
- `fallback_code`
- `is_current`

최소 금지 조합

- `generation_status`는 리소스별로 별개 enum이다: `guide.generation_status`(`GuideGenerationStatus`)는 `PENDING`/`GENERATING`/`COMPLETED`/`FAILED` 4개뿐이고, `chat_message.generation_status`(`ChatGenerationStatus`)는 USER Message에서 항상 `NOT_APPLICABLE`, ASSISTANT Message에서 `PENDING`/`GENERATING`/`COMPLETED`/`FAILED`다(`backend/app/models/guides.py`, `chat.py` 참고). 두 enum 모두 `PENDING`/`GENERATING`/`FAILED`면 현재 결과 콘텐츠를 화면에 보여주지 않는다. `PROCESSING`/`RETRY_WAIT`/`STALE`은 이 `generation_status`들의 값이 아니라 별도 축인 `ai_job.status` 값이며, 이 값들일 때도 마찬가지로 현재 결과를 보여주지 않는다.
- `is_current = false`이면 최신 화면 결과로 노출하지 않는다.
- `STALE`은 실패/에러가 아니라 과거 결과 비공개 상태이며 `content = null` 로 유지한다.
- `safety_disposition`의 `URGENT_ROUTED` / `EMERGENCY_ROUTED`는 실패 UI로 오해하지 않고 안전 안내 UI로 분기한다.

## 4) 공개 결정 기준

`release_decision`은 공개/미공개 판단축이며 API 응답 처리축(`execution_status`)과 분리한다.

- `release_decision` 값: `PASS`, `LIMITED`, `REJECTED`, `STALE`
- `execution_status` 값: 실행 성공/실패 계열
- `evidence_status` 값: `SUFFICIENT`, `INSUFFICIENT`, `CONFLICTED`, `STALE`(`safety-result-v2.md` 정본 목록과 일치)
- `fallback_code` 값: `NO_APPROVED_EVIDENCE`, `CONFLICTING_EVIDENCE`, `SAFETY_ROUTED`, `PROVIDER_TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `VALIDATION_FAILED`, `PRESCRIPTION_STALE`, `EXECUTION_CONTEXT_STALE`, `UNSUPPORTED_REQUEST`(`safety-result-v2.md` 정본 목록과 일치)
- `REJECTED`는 실패로 끝내지 않고 승인된 fallback 응답 또는 안전안내를 제공한다.
- `release_decision=PASS`는 RAG 결과 공개 판단이며 Evaluation의 `PASS`/`FAIL` 판정과 다른 축이다.

### 4.1 Frontend 최소 허용 조합(구현자가 추론하지 않도록 명시)

`ai_job.status`와 `generation_status`는 서로 다른 축이며(위 "3) Frontend 결과 상태축" 참고), 아래는 `ai_job.status` × `release_decision` 조합 중 Frontend가 반드시 구분해야 하는 최소 관계다. 정확한 값은 `safety-result-v2.md` "상태 축과 공개 판정"이 정본이다.

| `ai_job.status` | `release_decision` | 의미 | 화면 처리 |
| --- | --- | --- | --- |
| `COMPLETED` | `PASS` | 정상 답변 또는 승인된 긴급·응급 안내 공개 가능 | 정상 콘텐츠 표시 |
| `COMPLETED` | `LIMITED` | 금지 행동 요청 등, 승인된 범위 제한 안내만 공개 가능 | 제한 안내 UI로 표시 (실패 아님) |
| `COMPLETED` | `REJECTED` | 생성 답변은 폐기하고 승인된 고정 fallback만 공개 | `fallback_code` 기반 안내 표시. **`ai_job.status=FAILED`가 아니다** — Job 자체는 정상 종결이다 |
| `FAILED` | 해당 없음(값 없음) | fallback조차 안전하게 commit하지 못한 실행 실패 | 계약된 안전한 오류 응답만 표시(원문·상세 사유 노출 금지) |
| `STALE` | `STALE` | 처리 중 active 처방 Version 변경으로 결과 반영 불가 | `content = null`, `is_current = false`로 비노출 |

- `AI_JOB=COMPLETED` 또는 `execution_status=SUCCEEDED` 값만으로는 공개 가능 여부를 판단하지 않는다. 최종 공개는 `release_decision`·`is_current`·Citation 검증·Safety 결과를 함께 확인하는 `release_gate` 판정을 따른다.
- `citations[]` 공개 여부는 `release_decision` 값 자체가 결정하지 않는다. 개별 Citation이 별도 `CITATION_AUTHORIZATION/PASS` Guard를 통과하고 최종 `release_gate` 검증까지 통과했을 때만 그 Citation을 공개한다(`safety-result-v2.md` "Claim-Citation 계약"). `PASS`뿐 아니라 `LIMITED`의 제한 응답이나 `REJECTED`의 승인된 fallback에도, 정본이 허용하고 개별 `CITATION_AUTHORIZATION/PASS`를 통과한 Citation은 포함될 수 있다. `STALE`은 `is_current=false`로 전체 비공개이므로 `citations[]`도 공개하지 않는다. Frontend는 `response_level` 또는 `release_decision` 값만 보고 `citations[]`를 임의로 숨기거나 노출하지 않는다. 정확한 Citation DTO/fixture 구현은 [#180](https://github.com/AI-HealthCare-05/AH_05_04/issues/180)/[#186](https://github.com/AI-HealthCare-05/AH_05_04/issues/186) 후속 계약 범위로 둔다.

## 5) OTC는 기존 Chat transport 사용

- 별도 OTC API/OTC 전용 Job은 만들지 않는다.
- 기존 Chat Session/Chat Message/Chat Job 채널을 재사용한다.
- 단, Identification과 Preflight가 안정적으로 완료되기 전에는 Rule-first 평가를 진행하지 않는다.
- 처방·버전 변경/안전 라우팅 시 결과는 공통 상태축으로 관리한다.

## 6) 소유권 검사 기준

- Guide/Chat/Job의 조회/결과 접근은 `profile_id` 기반 소유권으로 평가한다.
- 해당 리소스가 현재 사용자 SELF profile 또는 부모 chain `profile_id`와 다르면 `404`로 차단한다.
- Job과 결과 도메인 간 소유권 기준이 다를 경우에도 fail-closed로 404 처리한다.

## 7) 구현 전 준비/분리사항

- `ai_job_id`는 도메인 테이블(`GUIDE`, `CHAT_MESSAGE`)에 보존하며, `AI_JOB`에서 별도 `domain_type/domain_id` 컬럼을 둔다고 가정하지 않는다.
- `AI_CONTEXT_SNAPSHOT`의 `prescription_version_id`/`clinical_state_hash` 같은 추가 구조는 범위를 넘어가므로 후속 v2에서 다룬다.
- DB FK 방식(직접 저장/parent join)과 실제 migration는 구현 PR에서 결정한다.

## 8) 참고 정합성 링크

- `docs/contracts/targets/post-mvp-1/async-job-v1.md`: status/202/result_url/STALE
- `docs/contracts/targets/post-mvp-1/idempotency-v1.md`: idempotency scope/hash
- `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md`: Outbox/ACK/재시도
- `docs/contracts/targets/post-mvp-1/medication-identification-v1.md`: Identification/Preflight
- `docs/contracts/targets/post-mvp-1/safety-result-v2.md`: Safety/fallback 상태축
- `docs/contracts/current/profile-self-ownership-v1.md`: 소유권 기준

## 9) 실 구현 시 계약 정리 방식

본 문서는 현재 Proposed 상태입니다. 이 문서가 승인되면 바로 Current로 보지 않고, 우선 아래 중 하나의 방식으로 계약을 정리합니다.

- 별도 목표 계약이 필요하면 `docs/contracts/targets/post-mvp-1/guide-chat-session-message-status-ui-v1.md`를 생성합니다.
- 이미 존재하는 계약과 중복되는 항목은 해당 정본 계약과 대조하고, 필요한 경우 같은 PR에서 함께 수정합니다.
- 구현이 완료되고 테스트로 검증된 항목만 `current/` 계약으로 승격합니다.

| 원칙 / 항목 | 계약 정리 위치 | 구현 PR 확인 방식 |
| --- | --- | --- |
| Guide·Chat 접수 `202 Accepted + JobStatusResponse`, `status_url` polling | `docs/contracts/targets/post-mvp-1/async-job-v1.md`와 대조 | 기존 비동기 Job 계약과 충돌하지 않는지 확인하고, 변경이 필요하면 해당 계약도 함께 수정 |
| `Idempotency-Key`, `IDEMPOTENCY_RECORD` 동반 transaction 접수 | `docs/contracts/targets/post-mvp-1/idempotency-v1.md`와 대조 | 접수 transaction, 중복 요청, 충돌 응답이 기존 멱등성 계약과 일치하는지 확인 |
| `REVIEW_REQUIRED`: 자동 Guide는 비-Job 동기 종료, Chat은 기존 Job에 제한 응답 저장 | `docs/contracts/targets/post-mvp-1/medication-identification-v1.md` 및 `rag-runtime-v1.md`와 대조 | 자동 Guide와 Chat의 Job 유무 차이가 유지되는지, Chat이 Safety Triage를 건너뛰지 않는지 확인 |
| Session/Message/Job 결과 상태축 분리 | 필요 시 신규 `targets/post-mvp-1/guide-chat-session-message-status-ui-v1.md`로 승격 | Frontend/Backend/RAG가 공유해야 하는 상태축이면 별도 목표 계약으로 분리 |
| STALE(`content=null`, `is_current=false`) 비노출 및 메타데이터 보존 | `prescription-version-v1.md`, `safety-result-v2.md`와 대조 | 버전 변경과 공개 결정 규칙이 기존 STALE 계약과 충돌하지 않는지 확인 |
| OTC 기존 Chat transport 사용 및 Rule-first 선행 게이트 | `rag-runtime-v1.md`, `medication-identification-v1.md`와 대조 | 별도 OTC API/Job을 만들지 않는 방향과 Identification 선행 조건을 확인 |
| PROFILE 소유권 기반 조회/결과 접근 제어 | `docs/contracts/current/profile-self-ownership-v1.md`와 대조 | Current 소유권 계약을 따르며, 필요한 구현 세부사항은 구현 PR에서 검증 |
| `ai_job_id`/도메인 연동 방향 | `async-job-v1.md` 및 구현 PR의 DB 설계와 대조 | FK/조회 경로는 구현 PR에서 확정하고, 계약 변경이 필요하면 별도 반영 |

이 문서의 승인만으로 API, DB, UI 계약이 자동 변경되지는 않습니다. 실제 변경은 관련 구현 PR에서 코드, 테스트, OpenAPI/DTO, 계약 문서를 함께 수정할 때 확정합니다.

## 10) 구현 PR 확인 기준

이 문서를 후속 구현 PR에서 사용할 때는 다음 항목을 확인합니다.

- Profile 소유권 기준이 Current(`profile_id`/부모 chain)으로 고정되어 있고, 위반 접근은 `404`로 반환한다.
- `POST /api/v1/guides`, `POST /api/v1/chat-sessions/{session_id}/messages`에서 Idempotency/202/Job 상태 조회 계약이 일관된다.
- `REVIEW_REQUIRED`는 자동 Guide에서만 Job 없이 동기 fallback으로 종료된다. Chat은 Identification 이전에 이미 생성된 Job·Safety Triage를 유지하며, ROUTINE Preflight 실패도 그 Job에 제한 응답을 저장한다 — 두 경우 모두 RAG/Composer 호출은 선행되지 않는다.
- `STALE`은 `content = null`, `is_current = false`로 현재 화면 비노출이며 Job/Safety 메타데이터는 보존한다.
- OTC는 기존 Chat transport를 사용하고, Identification/Preflight 미완료 시 Rule-first 평가를 강제 차단한다.
- Frontend 상태축은 `generation_status`, `execution_status`, `evidence_status`, `response_level`, `release_decision`, `safety_disposition`, `fallback_code`, `is_current`가 분리되어 해석된다.
- `citations[]` 공개는 `release_decision` 값 자체가 아니라 개별 `CITATION_AUTHORIZATION/PASS`와 최종 `release_gate` 판정을 따르며, `LIMITED`·승인된 `REJECTED` fallback에도 통과한 Citation이 포함될 수 있다. Frontend가 `response_level`/`release_decision`만으로 `citations[]`를 임의 숨김·노출하지 않는다.
- `ai_job_id` 소유권/연결 규칙이 구현 PR의 DB 제약과 정합된다.
