# 공통 Job 상태 조회 Backend 계약 v1

## 목적

`GET /api/v1/jobs/{job_id}`가 실제로 구현·등록되어(#148) 반환하는 응답 필드, 6개 Job 상태의 의미, 소유권 이중 확인과 오류 계약을 기록합니다. 이 문서는 [비동기 Job 계약 v1](../targets/post-mvp-1/async-job-v1.md)(target) 중 이 endpoint 부분만 Current로 승격한 것입니다 — OCR·Guide·Chat 접수(`POST`)와 Worker·Reconciler·재시도 계약은 아직 target 문서에만 있고 이 문서의 범위가 아닙니다.

## 현재 MVP 기준

- Endpoint: `GET /api/v1/jobs/{job_id}`
- 응답: `{"data": JobStatusResponse}` (오류는 top-level 공통 오류 envelope)
- 모든 성공·오류 응답에 `Cache-Control: no-store`를 포함합니다.
- `JobStatusData` 필드: `job_id`, `job_type`, `status`, `domain_type`, `domain_id`, `prescription_version_id`(nullable), `status_url`, `result_url`(nullable), `retry_after_seconds`(nullable), `error`(nullable `{code, message}`), `created_at`, `updated_at`.
- 현재 실제로 `AiJob` row를 만드는 production 경로는 없습니다 — OCR/Guide/Chat 접수(`POST`)가 아직 `JobIntakeService.accept_job()`에 연결되지 않았습니다(#148, Worker/Publisher #219·Handler #232/#233 준비 전까지 팀 결정으로 보류). 이 endpoint 자체의 응답 계약은 구현·테스트로 뒷받침되지만, 실사용자가 오늘 이 endpoint로 실제 Job을 조회할 방법은 아직 없습니다.

## 6개 Job 상태

| 상태 | 의미 |
| --- | --- |
| `PENDING` | 접수되어 발행 대기 또는 소비 대기 |
| `PROCESSING` | 유효한 lease를 가진 Worker가 처리 중 |
| `RETRY_WAIT` | 재시도 가능 오류로 다음 실행 대기 |
| `COMPLETED` | 결과가 영속 저장된 성공 종결 |
| `FAILED` | 재시도 불가 또는 재시도 소진 종결 |
| `STALE` | 더 최신 처방 버전·요청에 의해 결과 반영 불가 |

상태 정의와 허용 전이의 정본은 [비동기 Job 계약 v1](../targets/post-mvp-1/async-job-v1.md)의 「상태와 전이」입니다 — 전이를 만드는 Worker·Reconciler 구현은 아직 없고, 이 문서는 이미 특정 상태인 `AiJob` row를 조회했을 때의 응답 규칙만 다룹니다.

## `result_url` 규칙

- `COMPLETED`에서만 값을 채우고, 그 외 상태에서는 `null`입니다.
- `STALE`은 결과 row가 존재해도 `result_url`을 `null`로 숨깁니다 — 최신 결과처럼 노출되면 안 됩니다.
- `domain_type`별 결과 endpoint: `OCR_JOB → GET /api/v1/ocr-jobs/{domain_id}`, `GUIDE → GET /api/v1/guides/{domain_id}`, `CHAT_MESSAGE → GET /api/v1/chat-sessions/{session_id}/messages`.

## `Retry-After`/`retry_after_seconds` 규칙

- `RETRY_WAIT`에서만 값이 있고, 같은 값을 HTTP `Retry-After` 헤더와 `data.retry_after_seconds`로 함께 제공합니다.
- `available_at`이 지나도 `0`을 반환하지 않습니다 — Reconciler 주기 → 새 Outbox 생성 → Publisher `XADD` → Worker lease 획득까지는 `RETRY_WAIT`가 유지되므로, `0`을 보내면 Client가 대기 없이 재조회를 반복합니다. 최소 1초 하한을 적용합니다.
- CORS `Access-Control-Expose-Headers`에 `Retry-After`를 포함해 cross-origin Frontend가 `fetch()`로 값을 읽을 수 있게 합니다.

## `error` 규칙

- 도메인 결과를 저장하지 못한 terminal `FAILED`에서만 안전한 `{code, message}`를 반환하고, 그 외 상태에서는 `null`입니다.
- `code`는 `AiJobFailureCode` Literal(`TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `INVALID_INPUT`, `UNSUPPORTED_SCHEMA`, `SAFETY_VALIDATION_FAILED`, `RETRY_EXHAUSTED`, `INTERNAL_ERROR`)이며 `message`는 코드별 고정된 안전한 문구입니다.
- `attempt_count`, progress, `failure_detail`, Provider 원문 오류는 응답에 포함하지 않습니다.

## 소유권 이중 확인

1. `ai_job.user_id`로 1차 필터링합니다.
2. 도메인 row의 `profile_id` chain(SELF profile 기준)으로 재확인합니다.

두 기준이 어긋나거나 도메인 row 자체가 없으면 fail-closed `404`(`AI_JOB_NOT_FOUND`)입니다.

## 오류 응답

| HTTP | `code` | 조건 |
| --- | --- | --- |
| `401` | `UNAUTHORIZED` \| `INVALID_TOKEN` \| `EXPIRED_TOKEN` | 인증 정보가 없거나 유효하지 않거나 만료됨. `WWW-Authenticate: Bearer` 헤더 포함 |
| `404` | `AI_JOB_NOT_FOUND` | Job이 없거나 다른 사용자 소유, 또는 소유권 이중 확인 실패 |
| `422` | `VALIDATION_FAILED` | `job_id` path parameter가 UUID 형식이 아님. FastAPI 기본 `HTTPValidationError`가 아니라 전역 `RequestValidationError` 핸들러가 만드는 `ErrorResponse`가 실제 응답입니다 |

각 코드의 의미와 `retryable` 판정은 [Backend 공통 오류 응답 계약](./backend-error-response.md)을 따릅니다.

## 검증과 변경 규칙

구현 계약은 `backend/app/tests/job_apis/test_job_status_api.py`(route-level), `backend/app/tests/services/test_job_status.py`(service-level)에서 검증합니다.

다음 변경은 이 문서, 구현, OpenAPI와 관련 테스트를 같은 PR에서 갱신해야 합니다.

- `JobStatusData` 필드 추가·삭제·의미 변경
- 6개 상태 중 이 endpoint가 노출하는 필드 규칙(`result_url`, `retry_after_seconds`, `error`) 변경
- 소유권 이중 확인 기준 변경
- 이 endpoint의 오류 코드·HTTP status 변경
