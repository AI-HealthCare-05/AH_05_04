# OCR 작업 상태 조회 Backend 계약

## 목적

OCR 작업 실행·조회 API가 성공·실패 상태를 Frontend에 전달할 때 사용하는 필드와 오류 코드 기준을 기록합니다.

## 현재 구현 기준

- Endpoint: `POST /api/v1/documents/{document_id}/ocr-jobs`, `GET /api/v1/ocr-jobs/{domain_id}`
- `POST /api/v1/documents/{document_id}/ocr-jobs`는 공통 Job 접수 응답(`JobStatusResponse`)을 반환합니다. 완료된 OCR 결과는 `GET /api/v1/ocr-jobs/{domain_id}`에서 조회합니다.
- OCR 결과 조회 응답 `data`에는 `error_code`, `error_message`를 포함해 실패 상태를 화면에서 안내할 수 있도록 합니다.
- `error_message`는 외부 OCR 제공자의 원본 오류나 민감한 내부 예외 메시지를 그대로 노출하지 않고, Backend가 정의한 고정된 안전한 문구만 반환합니다.
- 문서에 연결된 OCR 작업이 여러 개이고 `created_at`이 동일한 경우, `created_sequence` 컬럼으로 최신 작업을 안정적으로 판별합니다.

## 실패 코드

| `error_code` | 상황 | HTTP status | `retryable` |
| --- | --- | ---: | :---: |
| `OCR_PROVIDER_TIMEOUT` | OCR 제공자 응답 시간 초과 | `503` | true |
| `OCR_PROVIDER_CALL_FAILED` | OCR 제공자 연결 실패 | `503` | true |
| `OCR_PROVIDER_UNAVAILABLE` | OCR 제공자 일시적 사용 불가 | `503` | true |
| `OCR_PROCESSING_FAILED` | 그 외 OCR 처리 중 오류 | `500` | false |

`retryable` 판정의 정본은 [공통 오류 응답 계약](./backend-error-response.md)의 「재시도 가능 여부」 절입니다.


## 상태 전이

| 전이 | 조건 | 저장 값 |
| --- | --- | --- |
| (생성) → `PENDING` | OCR 작업 생성 | `completed_at=null` |
| `PENDING` → `PROCESSING` | Provider 호출 시작 전 | `started_at` 기록, `completed_at=null` |
| `PROCESSING` → `COMPLETED` | 인식·구조화·필드 저장 성공 | `completed_at`, `engine_name` 기록. `error_code`·`error_message`는 `null` |
| `PROCESSING` → `FAILED` | 동기 MVP 경로의 Provider 실패·예산 소진·처리 실패, 또는 비동기 Worker 경로의 재시도 불가·소진 확정 | `completed_at`, `error_code` 기록 |

DB CHECK 제약으로 강제되는 불변식입니다.

- `PENDING`·`PROCESSING`은 `completed_at IS NULL`
- `COMPLETED`·`FAILED`는 `completed_at IS NOT NULL`
- `FAILED`는 `error_code IS NOT NULL`
- `COMPLETED`는 `error_code`·`error_message`가 모두 `NULL`

서비스 직접 실행 경로에서 전체 deadline이 소진되어 Provider를 호출하지 않은 경우에는 Job을
`PROCESSING`으로 남기지 않고 `FAILED`로 전이합니다. `error_code`는
`OCR_PROVIDER_TIMEOUT`이며 내부 사유는 응답 `details[].reason=DEADLINE_EXCEEDED`로
구분합니다.

### 비동기 Worker timeout과 재시도

#233으로 조립된 비동기 Worker 경로는 Provider 호출 전 `PENDING → PROCESSING`을
AI Job lease·attempt 획득과 같은 짧은 transaction에서 commit합니다. Handler가 hard
timeout에 도달하면 결과를 commit하거나 Stream 메시지를 ACK하지 않고,
OCR Job은 이미 commit된 `PROCESSING`을 유지합니다. lease 만료 전에 즉시
`FAILED`로 전이하지 않습니다.

만료된 lease의 reclaim, 재시도 가능 여부 판정, 다음 attempt 실행, 재시도 소진 후
`FAILED` 확정은 #142에서 구현합니다. 재시도 가능한 실패 동안 OCR Job은
`PROCESSING`을 유지하고, Handler 영구 실패 또는 재시도 소진으로 AI Job이 최종
실패할 때 연결된 OCR Job도 같은 transaction에서 `FAILED`와 `completed_at`을 저장합니다.
Worker의 상세 실패 코드는 AI Job에 유지하고, OCR 공개 상태에는 Provider timeout을
`OCR_PROVIDER_TIMEOUT`, Provider 가용성 실패를 `OCR_PROVIDER_UNAVAILABLE`, 그 밖의
실패를 안전한 `OCR_PROCESSING_FAILED`로 투영합니다.

## Post-MVP 이관

- 문서에 연결된 OCR 작업을 `job_id`로 명시적으로 식별해 사용자가 검수한 작업과 확정 대상 작업을 일치시키는 검증은 [처방 확정 Backend 계약](./prescription-confirmation.md)의 Post-MVP 이관 항목을 따릅니다.

## 검증과 변경 규칙

OCR 결과 저장·조회 구현 계약은 `backend/app/tests/ocr`에서 검증합니다. #233 비동기
Worker의 `PROCESSING` 전이·timeout·commit-before-ACK 경계는
`ai_worker/tests/core/test_sqlalchemy_ocr_execution_starter.py`와
`ai_worker/tests/core/test_consumer_execution.py`에서 검증합니다. reclaim·재시도 소진·최종
`FAILED` 전이와 연결된 OCR Job의 원자적 실패 처리는 #142의 단위 테스트와
`tests/integration/test_worker_recovery_repository.py` PostgreSQL 통합 테스트에서 검증합니다.

다음 변경은 이 문서, 구현, API 문서와 관련 테스트를 같은 PR에서 갱신해야 합니다.

- 실패 코드의 `retryable` 판정 변경
- `error_code`·`error_message` 필드 추가·삭제·의미 변경
- 최신 OCR 작업 판별 기준 변경
