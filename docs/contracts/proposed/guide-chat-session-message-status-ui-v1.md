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
- `REVIEW_REQUIRED`는 Job 상태가 아니며, Job을 만들지 않는 동기 fallback 종료로 정의한다.

### 2.2 버전 경계

- Chat Session은 생성 시 `prescription_version_id`를 기준 버전으로 귀속한다.
- `AI_JOB`, `CHAT_MESSAGE`, `GUIDE`에는 처리 시점 버전 정합성이 반영되어야 하며, 처방 버전 변경 시 이전 non-terminal Job은 `STALE` 처리되어 현재 사용자 화면에 노출되지 않는다.
- `STALE`은 사용자 화면 비노출(`content = null`, `is_current = false`)을 의미하지만, Job/Safety 메타데이터는 보존한다.

## 3) Frontend 결과 상태축(필수 분리)

Frontend는 단일 성공/실패 코드가 아니라 아래 축을 분리해 소비해야 한다.

- `generation_status`
- `execution_status`
- `response_level`
- `release_decision`
- `safety_disposition`
- `fallback_code`
- `is_current`

최소 금지 조합

- `generation_status`가 `PENDING` / `PROCESSING` / `RETRY_WAIT` / `FAILED` / `STALE`이면 현재 결과 콘텐츠를 화면에 보여주지 않는다.
- `is_current = false`이면 최신 화면 결과로 노출하지 않는다.
- `STALE`은 실패/에러가 아니라 과거 결과 비공개 상태이며 `content = null` 로 유지한다.
- `safety_disposition`의 `URGENT_ROUTED` / `EMERGENCY_ROUTED`는 실패 UI로 오해하지 않고 안전 안내 UI로 분기한다.

## 4) 공개 결정 기준

`release_decision`은 공개/미공개 판단축이며 API 응답 처리축(`execution_status`)과 분리한다.

- `release_decision` 값: `PASS`, `LIMITED`, `REJECTED`, `STALE`
- `execution_status` 값: 실행 성공/실패 계열
- `fallback_code` 값: `NO_APPROVED_EVIDENCE`, `CONFLICTING_EVIDENCE`, `SAFETY_ROUTED`, `PROVIDER_TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `VALIDATION_FAILED`, `PRESCRIPTION_STALE`, `UNSUPPORTED_REQUEST`
- `REJECTED`는 실패로 끝내지 않고 승인된 fallback 응답 또는 안전안내를 제공한다.
- `release_decision=PASS`는 RAG 결과 공개 판단이며 Evaluation의 `PASS`/`FAIL` 판정과 다른 축이다.

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
| `REVIEW_REQUIRED` 동기 종료(비-Job 경로) | `docs/contracts/targets/post-mvp-1/medication-identification-v1.md` 및 `rag-runtime-v1.md`와 대조 | Job 상태로 오해되지 않도록 Preflight 실패 처리와 RAG 차단 조건을 명확히 함 |
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
- `REVIEW_REQUIRED`는 Job 없이 동기 fallback으로 종료되고, RAG/Composer 호출이 선행되지 않는다.
- `STALE`은 `content = null`, `is_current = false`로 현재 화면 비노출이며 Job/Safety 메타데이터는 보존한다.
- OTC는 기존 Chat transport를 사용하고, Identification/Preflight 미완료 시 Rule-first 평가를 강제 차단한다.
- Frontend 상태축은 `generation_status`, `execution_status`, `response_level`, `release_decision`, `safety_disposition`, `fallback_code`, `is_current`가 분리되어 해석된다.
- `ai_job_id` 소유권/연결 규칙이 구현 PR의 DB 제약과 정합된다.
