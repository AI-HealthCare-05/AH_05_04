# 비동기 Job 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved target — 2026-08-24 팀 인계 기준 |
| 구현·리뷰 | Not implemented · 구현 동기화와 관련 지정 리뷰어 검토 대기 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-a-async-foundation-v1.md` |
| Last verified | 2026-08-24 |

## 적용 범위

Post-MVP-1의 `OCR`, `GUIDE`, `CHAT` 작업에 적용한다. Barrier Check-in은 동기 규칙, OTC 안전성 판정은 확정 성분에 대한 동기 규칙으로 처리하므로 v1 Job 유형에 포함하지 않는다.

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

성공 응답은 `{"data": JobStatusResponse}`로 감싸고 오류는 공통 top-level 오류 envelope를 사용한다. Job과 `result_url`은 동일한 `user_id` 소유권 함수를 적용하며 존재하지만 소유하지 않은 ID도 `404`로 응답한다. 보호자·patient profile 권한은 v1 범위가 아니다. 모든 응답에 `Cache-Control: no-store`를 포함한다. `result_url`은 안전한 도메인 결과가 저장된 `COMPLETED`에서만 제공하고 그 전에는 `null`이다. Track F의 `REJECTED` fallback도 `COMPLETED + result_url`로 조회한다. `RETRY_WAIT`에서는 `Retry-After` 헤더와 같은 값의 `retry_after_seconds`를 제공한다. `error`는 도메인 결과를 저장하지 못한 terminal `FAILED`에서만 안전한 `{code, message}`를 반환하며 `attempt_count`, progress, `failure_detail`과 Provider 원문 오류는 외부 응답에 포함하지 않는다.

OCR·Guide·Chat 접수의 `202 Accepted` 응답은 HTTP `Location`과 `data.status_url`을 같은 Job 조회 URL로 제공한다.

`COMPLETED` 뒤 `result_url`이 가리키는 도메인 결과 endpoint는 다음으로 고정한다.

| domain_type | 결과 endpoint | 응답 |
|---|---|---|
| `OCR_JOB` | `GET /api/v1/ocr-jobs/{domain_id}` | 기존 OCR 응답 |
| `GUIDE` | `GET /api/v1/guides/{domain_id}` | 기존 Guide 응답 |
| `CHAT_MESSAGE` | `GET /api/v1/chat-sessions/{session_id}/messages` | 기존 메시지 목록 응답 |

Client는 `domain_id`로 URL을 조합하지 않고 Backend가 제공한 opaque `result_url`을 그대로 사용한다. 결과 endpoint에도 Job 조회와 같은 소유권 검사, active prescription version 검사와 `Cache-Control: no-store`를 적용한다.

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
- 완료된 Chat Job의 `result_url`은 기존 `GET /api/v1/chat-sessions/{session_id}/messages` 조회를 가리킨다. URL은 Backend가 생성하며 Client가 `domain_id`로 조합하지 않는다.
- Polling 간격은 클라이언트 기본 1초, 지수 backoff, 최대 5초로 하고 SSE는 v1 범위에서 제외한다.

## 공통 화면 재접속 복구

- Client는 OCR·Guide·Chat 접수 응답의 기존 `job_id`와 `status_url`을 해당 작업 화면 상태에 연결해 보존하고, 화면 이탈·재진입 시 새 Job을 접수하지 않고 같은 `status_url`의 polling을 재개한다.
- 복구 polling도 공통 6개 Job 상태, `Retry-After`, opaque `result_url`, 소유권과 `Cache-Control: no-store` 계약을 그대로 따른다. Client는 `domain_id`로 Job 또는 결과 URL을 직접 조합하지 않는다.
- Chat에서 Client가 기존 Job 정보를 보유하지 않은 경우에는 메시지 목록의 ASSISTANT `job_id`로 polling을 복구한다. OCR·Guide의 Job 정보 보존·재발견 연결은 구현 PR의 OpenAPI와 Frontend fixture에서 명시하고, 같은 `job_id`로 복구되는지 계약 테스트로 고정한다.

## 실행 lease와 보존

- Worker lease는 hard timeout보다 15초 길게 설정한다: `OCR 45초`, `GUIDE 75초`, `CHAT 60초`.
- 처리 중 Worker는 10초마다 heartbeat하고 lease가 만료된 뒤에만 다른 Worker가 reclaim한다.
- 실행 메타데이터와 Job은 terminal 전환 후 90일 보존한다. 더 엄격한 개인정보·감사 정책이 있으면 그 정책을 적용한다.

## 시도와 재시도

- `attempt_count`는 최초 Worker 실행에서 1이며 `max_attempts`는 최초 실행을 포함한다.
- 기본 `max_attempts`는 OCR 3, Guide 3, Chat 2다.
- 지연은 `min(5초 × 2^(attempt_count-1), 60초)`에 0~20% 양의 jitter를 더한다.
- timeout, rate limit, 일시적 Provider·의존성 장애만 재시도하고 영구 입력·schema·Safety 검증 오류는 즉시 종료한다. 외부 rate limit과 일시적 Provider 오류를 어떤 공통 `failure_code`로 정규화할지는 구현 PR의 오류 매핑 테스트로 고정하되, 아래 허용 목록 밖의 새 공개 code를 만들지 않는다.
- `failure_code`는 `TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `INVALID_INPUT`, `UNSUPPORTED_SCHEMA`, `SAFETY_VALIDATION_FAILED`, `RETRY_EXHAUSTED`, `INTERNAL_ERROR` 중 하나다.

## 확정된 오류 의미

- `400 IDEMPOTENCY_KEY_REQUIRED`: 필수 멱등 키 누락
- `400 IDEMPOTENCY_KEY_INVALID`: 멱등 키 형식 오류
- `409 IDEMPOTENCY_KEY_CONFLICT`: 같은 키에 다른 요청 지문
- `409 CHAT_JOB_IN_PROGRESS`: 같은 Chat session에 다른 키의 non-terminal Job 존재
- 활성 처방 version 경쟁은 공통 `409 PRESCRIPTION_VERSION_CONFLICT` 의미를 사용한다.
- 존재하지 않거나 소유하지 않은 Job·결과는 모두 `404`로 응답한다. 이 경우의 세부 `code` 명칭과 기타 입력 오류 code는 구현 PR에서 공통 오류 계약과 함께 확정하며 이 문서에서 새 값을 만들지 않는다.
