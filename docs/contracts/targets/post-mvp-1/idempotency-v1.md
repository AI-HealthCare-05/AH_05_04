# 멱등성 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Contract Freeze v4 target — 2026-08-27 |
| 구현·리뷰 | Not implemented · 구현 동기화와 관련 지정 리뷰어 검토 대기 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-a-async-foundation-v1.md`, `track-f-rag-citation-safety-v1.md`, [`PD-91-20260831`](../../../governance/decisions/2026-08-31-ocr-timeout-idempotency.md) |
| Last verified | 2026-08-31 |

## 적용 요청

비동기 Job을 생성하는 모든 POST 요청과 Post-MVP-1 B·C 동기 상태 변경, Track F 사용자 Candidate 확인·거절 요청은 `Idempotency-Key` 헤더를 요구한다. 키는 16~255자의 ASCII 영숫자와 `-._:`만 허용하며 로그에는 원문을 남기지 않는다. 누락·빈 값은 `400 IDEMPOTENCY_KEY_REQUIRED`, 형식 오류는 `400 IDEMPOTENCY_KEY_INVALID`다.

## 식별 범위와 요청 해시

비동기 접수의 고유 범위는 `(user_id, OpenAPI operation_id, key_hmac)`이다. `key_hmac`은 원문 키를 서버 비밀키로 versioned HMAC-SHA-256 처리한 값이며 원문은 저장하지 않는다. 동기 상태 변경도 같은 `key_hmac` 컬럼명을 사용하되 아래와 같이 `parent_resource_id`를 scope에 추가한다. Post-MVP-1은 인증 사용자가 직접 소유한 리소스에 수행하는 요청만 지원한다.

HMAC key rotation 중에는 구 writer와 신 writer가 동시에 최초 요청을 쓰지 못하게 한다. reader는 미만료 멱등 레코드가 존재할 수 있는 모든 retained key version을 조회해야 한다. 현재 key version과 직전 key version만 조회하는 방식은 rotation 주기가 최대 멱등 레코드 보존기간보다 길어 N-2 이하 미만료 레코드가 존재할 수 없을 때만 허용한다. writer가 서로 다른 active key version으로 같은 원문 key를 동시에 insert하면 서로 다른 `key_hmac`이 만들어져 DB unique constraint가 중복 Job을 막지 못한다. 따라서 key rotation 배포는 다음 중 하나를 만족해야 한다.

- rolling 배포 중 active write key version을 바꾸지 않고, 모든 writer가 같은 active version을 사용한 뒤에만 새 version write를 시작한다.
- active write key version 전환 전 기존 writer와 PENDING/non-terminal Job drain이 완료됐음을 확인한다.
- rotation 주기를 최대 멱등 레코드 보존기간보다 길게 제한하고, 보존기간 안에 둘 이상의 이전 key version으로 생성된 미만료 레코드가 없음을 운영 기준으로 보장한다.
- 혼합 writer를 허용해야 한다면 원문 key를 저장하지 않는 별도 rotation-invariant 원자 잠금 또는 unique digest를 먼저 승인·구현한다.

위 조건이 충족되지 않으면 HMAC key rotation 중 신규 멱등성 write를 배포하지 않는다.

현재 Backend 구현(#147/#215)은 `IDEMPOTENCY_HMAC_KEY`를 단일 active version만 조회한다. "rotation 주기를 보존기간보다 길게 제한"하는 세 번째 조건은 reader가 current+직전(N-1) key version을 함께 조회할 때만 성립하는데, 지금 구현은 current만 조회하므로 이 조건을 실제로 만족하지 못한다 — 교체 직전 생성된 레코드가 교체 이후에도 최대 `IDEMPOTENCY_RECORD_TTL_DAYS`만큼 남아 있어, 그 기간 안에 같은 요청이 새 키로 재시도되면 기존 레코드를 찾지 못한다. 따라서 현재는 네 조건 중 어느 것도 충족하지 못하는 상태이며, reader가 retained key version 전체를 조회하도록 구현하는 #235가 병합되기 전까지는 `IDEMPOTENCY_HMAC_KEY`를 교체하지 않는다.

요청 지문은 다음 값을 canonical JSON으로 직렬화한 SHA-256이다.

- 비동기 요청의 `job_type`
- HTTP method와 정규화한 route template
- path의 도메인 식별자
- 의미 있는 query와 body
- 처방 기반 요청이면 `prescription_version_id`
- 파일을 사용하는 요청이면 필요한 file content digest

인증 토큰, trace ID, 전송 시각은 지문에서 제외한다.

## 비동기 Job 처리 규칙

| 상황 | 결과 |
|---|---|
| 새 키 | 도메인 placeholder, Job, Outbox와 함께 한 DB transaction에서 저장 |
| 같은 키·같은 지문 | 저장된 `job_id`로 Job을 조회해 새 Job 없이 최신 상태의 `202` 응답 반환 |
| 같은 키·다른 지문 | `409 IDEMPOTENCY_KEY_CONFLICT` |
| 최초 transaction rollback | 키도 저장하지 않아 안전하게 재시도 가능 |

동시 최초 요청은 DB unique constraint로 하나만 승리시킨 뒤, 패자는 저장된 요청 지문을 비교해 위 규칙을 적용한다.

DB unique 제약은 최소 다음 범위를 보장한다. `expires_at`은 unique key에 포함하지 않는다. 만료 row를 새 요청처럼 처리하려면 위 보존 규칙처럼 기존 row를 먼저 원자적으로 reclaim하거나 삭제한 뒤 새 row를 생성한다.

| 구분 | unique 기준 |
| --- | --- |
| 비동기 요청 | `record_type`, `user_id`, `operation_id`, `key_hmac` |
| 동기 요청 | `record_type`, `user_id`, `operation_id`, `parent_resource_id`, `key_hmac` |

비동기 Job 멱등 레코드는 응답 body snapshot을 저장하지 않는다. 동일 요청은 저장된 `job_id`로 현재 Job을 조회해 최신 `202`를 반환한다.

## 동기 상태 변경 처리 규칙

동기 B·C·F 쓰기의 고유 범위는 `(user_id, OpenAPI operation_id, parent_resource_id, key_hmac)`이다. parent resource는 다음과 같다.

- B 일정: `prescription_version_medication_id`
- B Check-in·재알림: `occurrence_id`
- C Safety: `medication_checkin_id`; Barrier: `checkin_id`; ActionPlan 생성: `barrier_response_id`; ActionPlan 변경·follow-up: `support_action_plan_id`
- F Candidate 확인·거절: `prescription_version_medication_id`이며 request hash에 `candidate_search_result_id`, 확인·거절 action과 기대 [Runtime Release Bundle](./medication-identification-v1.md#identification-preflight)을 포함

권한·입력·revision·현재 상태 검사를 통과한 2xx mutation만 최초 성공 HTTP status와 canonical JSON body snapshot을 도메인 변경과 같은 transaction에서 저장한다. 4xx·5xx는 저장하지 않는다. 같은 키·같은 지문은 revision·현재 상태 검사보다 먼저 최초 snapshot을 그대로 재현하고, 같은 키·다른 지문은 `409 IDEMPOTENCY_KEY_CONFLICT`다.

snapshot은 암호화한 PostgreSQL `BYTEA`로 저장하고 application cap은 1MiB다. 암호화 envelope·key version·rotation과 migration의 정확한 구현은 Privacy·Security 리뷰를 받되 물리 타입을 다른 DB 전용 타입으로 대체하지 않는다. 의료 자유 텍스트와 Provider 원문은 넣지 않고 일반 로그에도 기록하지 않는다. 직렬화 결과가 cap을 넘으면 snapshot을 자르지 않으며 mutation 전에 `503 IDEMPOTENCY_RESPONSE_TOO_LARGE`와 alert로 실패한다.

## 보존

- 최초 접수 transaction이 성공한 시점부터 최소 24시간 유지하며 운영 설정 기본값은 7일이다.
- 만료 이후 같은 키는 새 요청으로 처리될 수 있으므로 사용자의 새 실행에는 항상 새 키를 발급한다. 다만 `expires_at`은 unique index를 자동 해제하지 않으므로, 만료 row를 새 요청처럼 처리하려면 만료 row를 원자적으로 reclaim하거나 삭제 job으로 제거된 뒤 새 row를 생성해야 한다.
- 만료 row 정리와 새 Job·mutation 생성은 같은 transaction 또는 DB unique 제약으로 보호한다. 정리 경쟁 중 같은 key가 새 Job, 새 Outbox, 새 Provider 호출을 중복 생성하면 안 된다.
- 감사·보안 정책이 더 긴 보존을 요구하면 더 긴 기간을 적용할 수 있다.

## 단일 테이블과 저장 필드

2026-08-31 [Product Decision `PD-91-20260831`](../../../governance/decisions/2026-08-31-ocr-timeout-idempotency.md)에 따라 비동기와 동기 멱등 레코드는 PostgreSQL 단일 `idempotency_record` 테이블에 저장하고 `record_type=ASYNC_JOB|SYNC_MUTATION`으로 구분한다. 별도 `sync_idempotency_record` 테이블은 만들지 않는다.

공통 필드는 `record_type`, `user_id`, `operation_id`, versioned `key_hmac`, `request_hash`, `created_at`, `expires_at`이다. `ASYNC_JOB`은 non-null `job_id`를 저장하고 `parent_resource_id`, `response_status`, `response_body_snapshot`은 null이다. `SYNC_MUTATION`은 non-null `parent_resource_id`, `response_status`, 암호화된 `response_body_snapshot`(암호화 후 `BYTEA`)을 저장하고 `job_id`는 null이다. DB CHECK 제약으로 이 타입별 nullability를 강제한다.

HMAC version의 물리 컬럼·인코딩과 키 교체 절차는 Privacy·보안 승인과 구현 PR에서 확정한다. HMAC key rotation 기간에는 미만료 record가 존재할 수 있는 모든 retained key version으로 계산한 `key_hmac`을 같은 scope에서 함께 조회해 기존 record를 찾는다. 현재·직전 key version만 조회하는 구현은 rotation 주기가 최대 멱등 레코드 보존기간보다 길어 N-2 이하 미만료 record가 존재할 수 없을 때만 허용한다. 같은 원문 key가 retained key version으로 저장되어 있는데 현재 key version만 조회해 새 Job이나 mutation을 만들면 안 된다. rotation 중에도 원문 `Idempotency-Key`는 저장하지 않는다. 또한 혼합 writer가 서로 다른 active key version으로 같은 원문 key의 최초 write를 동시에 수행할 수 있는 배포는 금지한다.
