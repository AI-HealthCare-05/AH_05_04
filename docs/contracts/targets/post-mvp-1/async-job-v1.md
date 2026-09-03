# 비동기 Job 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Contract Freeze v4 target — 2026-08-27 |
| 구현·리뷰 | Partially implemented (#148) — 공통 Job 상태 조회(`GET /jobs/{job_id}`)와 OCR·Guide rediscovery GET(`GET /documents/{id}/ocr-jobs`, `GET /prescriptions/{id}/guides`) 구현·테스트 완료. OCR·Guide·Chat 접수(POST) 3종의 `accept_job()` 연결과 Publisher·Worker·Reconciler는 Not implemented — 전체 승격 대기 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-a-async-foundation-v1.md`, [`PD-91-20260831`](../../../governance/decisions/2026-08-31-ocr-timeout-idempotency.md) |
| Last verified | 2026-08-31 |

## 적용 범위

Post-MVP-1의 `OCR`, `GUIDE`, `CHAT` 작업에 적용한다. Barrier·Check-in, Candidate Search·사용자 Identification 확인과 Identification Preflight는 동기 경계이므로 새 Job 유형에 포함하지 않는다. OTC 상호작용 질문은 별도 `OTC_CHECK`가 아니라 기존 `CHAT` Job을 사용한다.

## 상태와 전이

Job 상태는 다음 여섯 개로 고정한다.

| 상태 | 의미 | 허용되는 다음 상태 |
|---|---|---|
| `PENDING` | 접수되어 발행 대기 또는 소비 대기 | `PROCESSING`, `STALE`, `FAILED` |
| `PROCESSING` | 유효한 lease를 가진 Worker가 처리 중 | `RETRY_WAIT`, `COMPLETED`, `FAILED`, `STALE` |
| `RETRY_WAIT` | 재시도 가능 오류로 다음 실행 대기 | `PROCESSING`, `FAILED`, `STALE` |
| `COMPLETED` | 결과가 영속 저장된 성공 종결 | 없음 |
| `FAILED` | 재시도 불가 또는 재시도 소진 종결 | 없음 |
| `STALE` | 더 최신 처방 버전·요청에 의해 결과 반영 불가 | 없음 |

- `REVIEW_REQUIRED`는 Job 상태가 아니다. OCR 결과의 사용자 검수 상태다.
- `CANCELLED`, `TIMED_OUT`를 별도 Job 상태로 추가하지 않는다. 최종 실패는 `failure_code`, nullable `failure_detail`, nullable `dead_lettered_at`으로 기록한다.
- terminal 상태는 `COMPLETED`, `FAILED`, `STALE`이다.
- `COMPLETED`는 Provider 성공이 아니라 안전한 도메인 결과 commit을 뜻한다. Track F의 `PASS`, `LIMITED`뿐 아니라 생성 내용을 폐기하고 승인 fallback을 저장한 `REJECTED` 결과도 `COMPLETED`다.
- `FAILED`는 재시도 소진 뒤에도 도메인 결과나 승인 fallback을 commit하지 못한 실행 실패에만 사용한다. Chat의 Safety 검증 실패는 재시도하지 않되 승인 fallback 저장에 성공하면 `REJECTED + COMPLETED`, 그 저장도 실패하면 `FAILED`다.
- 모든 전이는 DB의 현재 상태와 `lease_token`을 조건으로 한 원자적 갱신으로 수행한다.

## Job과 도메인 결과 관계

`AI_JOB`은 공통 실행 상태를 관리하고 `OCR_JOB`, `GUIDE`, ASSISTANT `CHAT_MESSAGE`가 도메인 placeholder와 결과를 관리한다. 각 도메인 row에는 unique `ai_job_id` FK를 두며 polymorphic `resource_id` 하나로 이 관계를 대체하지 않는다.

Job에는 유형·상태, nullable `prescription_version_id`, nullable `expected_event_id`, nullable `last_consumed_event_id`, `attempt_count`, `max_attempts`, `available_at`, lease token·만료·heartbeat, 실패 code·detail·dead-lettered 시각과 생성·시작·완료·갱신 시각을 저장한다. 외부 `domain_type`, `domain_id`, `status_url`, `result_url`은 Job과 도메인 관계에서 응답용으로 구성한다. 특히 opaque `result_url`을 DB 필수 컬럼으로 고정하지 않는다. `domain_type` 외부 값은 `OCR_JOB`, `GUIDE`, `CHAT_MESSAGE` 중 하나다.

Job 테이블이 상태의 기준 원본이다. Redis Stream 메시지만으로 상태를 판정하지 않는다.

## 접수·조회 API 목표

Post-MVP-1 target의 비동기 접수·조회 API는 다음 네 개로 고정한다. OpenAPI의 `servers.url`에는 `http://localhost:8000/api/v1`처럼 `/api/v1` prefix가 포함될 수 있지만, 아래 표는 실제 Backend route와 응답 URL 기준으로 작성한다. 실제 응답의 `Location` 헤더와 `data.status_url`은 `/api/v1/jobs/{job_id}` 형태의 opaque URL로 내려준다.

| Method | Path | 역할 | `Idempotency-Key` | 요청 body |
|---|---|---|---|---|
| `POST` | `/api/v1/documents/{document_id}/ocr-jobs` | OCR Job 접수 | 필수 | 없음 |
| `POST` | `/api/v1/guides` | Guide Job 접수 | 필수 | `CreateGuideRequest { prescription_id }` |
| `POST` | `/api/v1/chat-sessions/{session_id}/messages` | Chat Job 접수 | 필수 | `SendChatMessageRequest { content }` |
| `GET` | `/api/v1/jobs/{job_id}` | 공통 Job 상태 조회 | 사용하지 않음 | 없음 |

OCR 접수는 기존 라우터의 path parameter 방식과 `202` 응답을 유지한다. Guide와 Chat은 현재 MVP의 동기 `201` 응답 방식과 Post-MVP-1 target의 비동기 `202 + JobStatusResponse` 응답 방식을 명확히 구분하고, 구현 PR에서 같은 API path를 Job 기반 응답으로 전환한다. 접수 3종의 멱등 키 형식은 [멱등성 계약](./idempotency-v1.md)을 따른다.

## 공통 조회 응답

`GET /api/v1/jobs/{job_id}`는 다음 envelope를 반환한다.

```json
{
  "data": {
    "job_id": "uuid",
    "job_type": "CHAT",
    "status": "PROCESSING",
    "domain_type": "CHAT_MESSAGE",
    "domain_id": "uuid",
    "prescription_version_id": "uuid",
    "status_url": "/api/v1/jobs/uuid",
    "result_url": null,
    "retry_after_seconds": null,
    "error": null,
    "created_at": "2026-08-23T00:00:00Z",
    "updated_at": "2026-08-23T00:00:01Z"
  }
}
```

성공 응답은 `{"data": JobStatusResponse}`로 감싸고 오류는 공통 top-level 오류 envelope를 사용한다. 공통 오류 응답의 `details`는 객체가 아니라 배열이며, 구체 형식은 [Backend 오류 응답 계약](../../current/backend-error-response.md)을 따른다. #117 병합 이후 Job과 `result_url`이 가리키는 도메인 결과는 SELF `profile_id` 또는 부모 chain의 `profile_id`를 기준으로 소유권을 확인한다. Job과 도메인 결과의 소유권 기준이 서로 맞지 않거나 인증 사용자의 SELF profile에 속하지 않으면 fail-closed `404`로 응답한다. 모든 성공·오류 응답에 `Cache-Control: no-store`를 포함한다. `result_url`은 안전한 도메인 결과가 저장된 `COMPLETED`에서만 제공하고 그 전에는 `null`이다. Track F의 `REJECTED` fallback도 `COMPLETED + result_url`로 조회한다. `RETRY_WAIT`에서는 `Retry-After` 헤더와 같은 값의 `retry_after_seconds`를 제공한다. `error`는 도메인 결과를 저장하지 못한 terminal `FAILED`에서만 안전한 `{code, message}`를 반환하며 `attempt_count`, progress, `failure_detail`과 Provider 원문 오류는 외부 응답에 포함하지 않는다.

OCR·Guide·Chat 접수의 `202 Accepted` 응답은 HTTP `Location`과 `data.status_url`을 같은 Job 조회 URL로 제공한다.

공통 header 기준은 다음과 같다.

| Header | 적용 API | 조건 | 설명 |
|---|---|---|---|
| `Cache-Control: no-store` | 접수 3종, 상태 조회, 결과 조회 | 항상 | Job 상태와 의료·AI 결과를 캐시하지 않는다. |
| `Location` | 접수 3종 | `202 Accepted` | `data.status_url`과 같은 Job 조회 URL이다. |
| `Retry-After` | 상태 조회 | `status = RETRY_WAIT` | `retry_after_seconds`와 같은 초 단위 값이며, 아래 최소값 규칙을 따른다. |
| `WWW-Authenticate: Bearer` | 접수 3종, 상태 조회 | `401` | 인증 실패 응답에 포함한다. |

`Retry-After`/`retry_after_seconds`는 `available_at`이 지나도 `0`을 반환하지 않는다. `available_at` 경과 후에도 Job은 Reconciler 주기 → 새 Outbox 생성 → Publisher `XADD`(#219) → Worker lease 획득까지 `RETRY_WAIT`를 유지하므로, `0`을 보내면 Client가 대기 없이 재조회를 반복한다. 최소 1초 하한을 적용하며(#148), 정확한 하한값은 #142에서 Reconciler 실행 주기가 확정되면 그 값(또는 그 값 기반 최소치)으로 교체한다. `Retry-After`는 Backend CORS 설정의 `Access-Control-Expose-Headers`에 포함해 cross-origin Frontend가 읽을 수 있어야 한다(#148).

승인된 ERD에는 `AI_JOB_ATTEMPT.attempt_status=BLOCKED` enum 값이 포함된다. 다만 이 값의 의미, 기록 조건, 상태 전이와 OCR·Guide·Chat 적용 범위는 후속 Decision 대상이다. ERD나 enum을 Proposed로 낮추지 않되, 전이 계약이 승인될 때까지 Worker 구현에서 `BLOCKED`를 생성·전이·저장하지 않는다. Track F의 `safety_disposition=BLOCKED_ACTION`과는 연동하지 않는다.

`COMPLETED` 뒤 `result_url`이 가리키는 도메인 결과 endpoint는 다음으로 고정한다.

| domain_type | 결과 endpoint | 응답 |
|---|---|---|
| `OCR_JOB` | `GET /api/v1/ocr-jobs/{domain_id}` | 기존 OCR 응답 |
| `GUIDE` | `GET /api/v1/guides/{domain_id}` | 기존 Guide 응답 |
| `CHAT_MESSAGE` | `GET /api/v1/chat-sessions/{session_id}/messages` | 기존 메시지 목록 응답 |

Client는 `domain_id`로 URL을 조합하지 않고 Backend가 제공한 opaque `result_url`을 그대로 사용한다. `CHAT_MESSAGE`의 `domain_id`는 ASSISTANT message id이므로 Backend는 `chat_message` row의 `session_id`로 메시지 목록 URL을 구성한다. Chat의 `result_url`은 단일 메시지 조회가 아니라 메시지 목록 조회이므로, Client는 목록 응답에서 `id == domain_id`인 ASSISTANT 메시지를 해당 Job의 결과로 선택한다. Backend는 `COMPLETED` Job의 `result_url`이 가리키는 메시지 목록 응답에 `domain_id` 메시지가 포함되도록 보장한다. 페이지네이션이 도입되면 이 보장을 유지하는 query parameter를 `result_url`에 포함하거나 단건 메시지 조회 endpoint를 별도 계약으로 추가한다. 결과 endpoint에도 Job 조회와 같은 소유권 검사, active prescription version 검사와 `Cache-Control: no-store`를 적용한다.

## Chat 접수 계약

`POST /api/v1/chat-sessions/{session_id}/messages`는 `Idempotency-Key`를 요구하고 `202 Accepted`를 반환한다. 같은 transaction에서 USER 메시지, 비어 있는 ASSISTANT 메시지, Job, Idempotency 레코드, Outbox 이벤트를 생성하고 Job의 `expected_event_id`를 새 Outbox `event_id`로 설정한다.

```json
{
  "data": {
    "job_id": "uuid",
    "job_type": "CHAT",
    "status": "PENDING",
    "domain_type": "CHAT_MESSAGE",
    "domain_id": "uuid",
    "prescription_version_id": "uuid",
    "status_url": "/api/v1/jobs/uuid",
    "result_url": null,
    "retry_after_seconds": null,
    "error": null,
    "created_at": "2026-08-23T00:00:00Z",
    "updated_at": "2026-08-23T00:00:00Z"
  }
}
```

- 한 세션에는 terminal이 아닌 Chat Job을 하나만 허용한다.
- Backend는 Chat session row를 `SELECT ... FOR UPDATE`로 잠근 뒤 non-terminal Job을 확인하고 USER message, ASSISTANT placeholder, Job, Idempotency, Outbox를 같은 transaction에서 생성한다.
- 다른 키의 두 번째 요청은 공통 오류 envelope의 `409 CHAT_JOB_IN_PROGRESS`를 반환하며 별도 `status_url` 필드를 추가하지 않는다.
- 동일 키 재전송은 [멱등성 계약](./idempotency-v1.md)에 따라 기존 `202` 응답을 재현한다.
- ASSISTANT 메시지 응답은 nullable `job_id`와 `generation_status`를 포함한다. Client는 자신이 저장한 기존 Job을 계속 polling하고, 재접속으로 Job 정보가 없으면 메시지 목록의 `job_id`로 polling을 복구한다.
- 완료된 Chat Job의 `result_url`은 기존 `GET /api/v1/chat-sessions/{session_id}/messages` 조회를 가리킨다. URL은 Backend가 생성하며 Client가 `domain_id`로 조합하지 않는다. Client는 목록에서 `id == domain_id`인 ASSISTANT 메시지를 해당 Job 결과로 선택하며, Backend는 해당 메시지가 목록 응답에 포함되도록 보장한다.
- Polling 간격은 클라이언트 기본 1초, 지수 backoff, 최대 5초로 하고 SSE는 v1 범위에서 제외한다.

## 공통 화면 재접속 복구

- Client는 OCR·Guide·Chat 접수 응답의 기존 `job_id`와 `status_url`을 해당 작업 화면 상태에 연결해 보존하고, 화면 이탈·재진입 시 새 Job을 접수하지 않고 같은 `status_url`의 polling을 재개한다.
- 복구 polling도 공통 6개 Job 상태, `Retry-After`, opaque `result_url`, 소유권과 `Cache-Control: no-store` 계약을 그대로 따른다. Client는 `domain_id`로 Job 또는 결과 URL을 직접 조합하지 않는다.
- Chat에서 Client가 기존 Job 정보를 보유하지 않은 경우에는 메시지 목록의 ASSISTANT `job_id`로 polling을 복구한다. OCR·Guide의 Job 정보 보존·재발견 연결은 구현 PR의 OpenAPI와 Frontend fixture에서 명시하고, 같은 `job_id`로 복구되는지 계약 테스트로 고정한다.

## 실행 lease와 보존

- 2026-08-31 [Product Decision `PD-91-20260831`](../../../governance/decisions/2026-08-31-ocr-timeout-idempotency.md)에 따라 실행 상한은 `OCR hard timeout 60초 / lease 75초`, `GUIDE hard timeout 60초 / lease 75초`, `CHAT hard timeout 45초 / lease 60초`로 고정한다. OCR 60초는 현재 Provider 상한 CLOVA 20초 + 구조화 LLM 30초의 순차 실행과 종료 처리 여유를 포함하고 lease는 hard timeout보다 15초 길다.
- 처리 중 Worker는 10초마다 heartbeat하고 lease가 만료된 뒤에만 다른 Worker가 reclaim한다. heartbeat는 Provider 호출과 독립적으로 갱신한다.
- lease가 만료된 Job은 즉시 `FAILED`로 바꾸지 않는다. 다른 Worker 또는 Reconciler가 DB의 Job 상태, `attempt_count`, `expected_event_id`, `available_at`을 확인한 뒤 재획득·재시도·실패 처리를 판단한다.
- 실행 메타데이터와 Job은 terminal 전환 후 90일 보존한다. 더 엄격한 개인정보·감사 정책이 있으면 그 정책을 적용한다.
- 도메인 결과 row는 Job보다 오래 보존될 수 있으므로 `OCR_JOB`, `GUIDE`, ASSISTANT `CHAT_MESSAGE`의 `ai_job_id` FK는 nullable로 두고 Job 삭제 시 `ON DELETE SET NULL` 또는 삭제 전 참조 해제를 적용한다. Job 삭제 때문에 사용자에게 보존해야 할 OCR·Guide·Chat 결과를 삭제하지 않는다.

## 시도와 재시도

- 접수 직후 `attempt_count`는 0이며, 최초 Worker 실행 시도 번호는 1이다. Worker가 lease를 획득하는 transaction에서 `attempt_count`를 수신 attempt 값으로 갱신하고, 이후 Provider 호출·결과 저장·ACK 전 검증에서는 수신 attempt와 DB `attempt_count`가 일치해야 한다.
- `max_attempts`는 최초 실행을 포함한다.
- 기본 `max_attempts`는 OCR 3, Guide 3, Chat 2다.
- 지연은 `min(5초 × 2^(attempt_count-1), 60초)`에 0~20% 양의 jitter를 더한다.
- timeout, rate limit, 일시적 Provider·의존성 장애만 재시도하고 영구 입력·schema·Safety 검증 오류는 즉시 종료한다. 외부 rate limit과 일시적 Provider 오류를 어떤 공통 `failure_code`로 정규화할지는 구현 PR의 오류 매핑 테스트로 고정하되, 아래 허용 목록 밖의 새 공개 code를 만들지 않는다.
- `failure_code`는 `TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `INVALID_INPUT`, `UNSUPPORTED_SCHEMA`, `SAFETY_VALIDATION_FAILED`, `RETRY_EXHAUSTED`, `INTERNAL_ERROR` 중 하나다.
- lease 만료로 회수된 Job도 현재 attempt를 사용한 실패로 계산한다. #141의 현재 구현 범위에서는 동일 event의 Job·Outbox 연결과 `last_consumed_event_id`가 일치하고 Job 상태가 `COMPLETED` 또는 `FAILED`인 경우에만 Provider를 재호출하지 않고 ACK한다. `RETRY_WAIT`·`STALE` 전이와 해당 상태의 재전달 멱등 처리, 다음 attempt와 새 Outbox event 생성은 #142에서 구현·검증 범위를 확장한다.

2026-09-02 [Product Decision `PD-141-20260902`](../../../governance/decisions/2026-09-02-worker-attempt-lease-fencing.md)에 따라 `attempt_count=0`에서 시작하고, Worker가 lease를 획득하는 transaction에서 수신 attempt로 갱신하는 기준을 승인했다. 실행 소유권은 `attempt_count + lease_token`으로 검증하며, heartbeat·결과 저장의 조건부 갱신이 0건이면 실행 권한 상실로 처리한다.

## 오류 응답 적용 기준

Job 접수·상태 조회에서 요청 자체가 실패한 경우에는 `data`로 감싸지 않고 Backend 공통 오류 envelope를 반환한다.

오류 응답 형식, `details` 배열 구조, `trace_id`, `Cache-Control: no-store`, 인증 오류, 기본 404/405, 서버 오류 기준은 [Backend 오류 응답 계약](../../current/backend-error-response.md)을 따른다.

이 문서에서는 비동기 Job 흐름에서 추가로 필요한 오류 상황만 정의한다.

| 상황 | 처리 기준 |
|---|---|
| `Idempotency-Key` 누락 | [멱등성 계약](./idempotency-v1.md)의 `IDEMPOTENCY_KEY_REQUIRED` |
| `Idempotency-Key` 형식 오류 | [멱등성 계약](./idempotency-v1.md)의 `IDEMPOTENCY_KEY_INVALID` |
| 같은 key로 다른 요청 지문 접수 | [멱등성 계약](./idempotency-v1.md)의 `IDEMPOTENCY_KEY_CONFLICT` |
| 같은 Chat session에 다른 key의 non-terminal Job 존재 | `409 CHAT_JOB_IN_PROGRESS` |
| Job 또는 결과가 없거나 다른 사용자 소유 | fail-closed `404`, 세부 code는 Backend 오류 응답 계약과 구현 PR에서 확정 |
| active prescription version 충돌 | 처방 버전 계약의 `PRESCRIPTION_VERSION_CONFLICT` |

같은 `Idempotency-Key`와 같은 요청 지문의 재전송은 오류가 아니며, 멱등성 계약에 따라 기존 Job의 최신 `202 Accepted` 응답을 반환한다.
