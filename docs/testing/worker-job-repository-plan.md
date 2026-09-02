# Worker Job Repository 입출력 테스트 계획

| 항목 | 값 |
| --- | --- |
| 상태 | Core implementation verified — `PD-141-20260902` |
| 구현 이슈 | [#141](https://github.com/AI-HealthCare-05/AH_05_04/issues/141) |
| 기준 계약 | `async-job-v1.md`, `outbox-stream-v1.md` |
| 승인 Decision | [`PD-141-20260902`](../governance/decisions/2026-09-02-worker-attempt-lease-fencing.md) |
| 대상 | Worker lease·heartbeat·fencing·결과 commit Repository |
| 제외 | #142 reclaim·retry·quarantine·DLQ, #147 Outbox Publisher |

## 목적

Worker Repository가 유효한 실행 소유자의 DB 변경만 허용하고,
결과와 Job 상태가 commit된 이후에만 Stream ACK가 가능하도록
입력·출력과 DB 불변식을 테스트로 고정한다.

## 현재 확정된 스키마 사실

- `ai_job.attempt_count`의 초기값은 `0`이다.
- `ai_job`에는 `status`, `expected_event_id`,
  `last_consumed_event_id`, `attempt_count`, `max_attempts`,
  `lease_token`, `lease_expires_at`, `heartbeat_at`이 존재한다.
- `PROCESSING` Job은 `lease_token`과 `lease_expires_at`이
  모두 존재해야 한다.
- `ai_job_attempt`의 `(ai_job_id, attempt_no)`는 unique다.
- `outbox_event`의 `(job_id, attempt, event_kind)`는 unique다.
- 결과 저장과 Job 상태 변경은 같은 `AsyncSession`과
  transaction을 사용해야 한다.
- DB commit 이전에는 Stream ACK를 호출하지 않는다.
- commit 이후 ACK 실패가 발생해도 이미 commit된 DB 결과는
  rollback하지 않는다.

## 승인된 Repository 조건

- `attempt_count`는 lease 획득 transaction에서 수신 attempt로 갱신한다.
- 신규 lease 전에 동일 event의 처리 완료 여부를 확인한다.
- 중복 event 생략은 `last_consumed_event_id`와 실제 Job·Outbox 연결이 모두 일치하는 경우에만 허용한다.
- 실행 세대는 `attempt_count`, 동일 세대의 소유자는 opaque `lease_token`으로 검증한다.
- heartbeat와 결과 저장은 `job_id + attempt_count + lease_token + PROCESSING + 만료되지 않은 lease` 조건부 갱신을 사용한다.
- 조건부 갱신 결과가 0건이면 실행 권한 상실로 처리한다.
- 결과·terminal 상태·`last_consumed_event_id`는 같은 transaction에서 저장하고 commit 이후에만 ACK한다.
- ACK 실패는 완료된 commit을 되돌리지 않는다.
- reclaim·retry Outbox·quarantine·DLQ는 #142 범위다.
- lease 획득은 진입 조건 전체를 `WHERE`에 포함한 하나의 조건부 `UPDATE`로 수행한다.
- lease 획득 UPDATE의 영향 행이 `1`일 때만 성공이며, `0`이면 실행 권한을 얻지 못한 것으로 처리한다.
- 동시성 제어는 `SELECT FOR UPDATE`가 아닌 optimistic conditional UPDATE를 사용한다.
- #141은 `expected_event_id`를 읽고 검증하지만 갱신하지 않는다. 최초 값은 #147, retry용 새 값은 #142가 Outbox와 같은 transaction에서 갱신한다.

## 테스트 계층

1. Repository 단위 테스트
   - 생성 SQL과 조건부 갱신 조건
   - 영향 행 수에 따른 반환값
2. PostgreSQL 통합 테스트
   - row lock과 동시 Worker 경합
   - transaction rollback
   - unique 제약과 멱등성
3. Consumer 통합 테스트
   - 결과 저장 → Job 전이 → commit → ACK 순서
   - commit 실패 및 ACK 실패 경계

## 승인과 무관한 Repository·Transaction 테스트 케이스

| ID | 입력·상황 | 실행 | 기대 결과 | 검증 위치 |
| --- | --- | --- | --- | --- |
| `WJR-001` | 같은 `AsyncSession`을 사용하는 Handler·ResultStore·Transaction | 정상 결과 저장 후 commit | Handler와 ResultStore 변경이 함께 저장됨 | PostgreSQL 통합 |
| `WJR-002` | Handler 결과의 `job_id`가 메시지와 불일치 | Consumer 실행 | ResultStore 미호출, transaction rollback, ACK 미호출 | Consumer·PostgreSQL 통합 |
| `WJR-003` | Handler가 예외 또는 cancellation 발생 | Consumer 실행 | 현재 transaction rollback, ACK 미호출 | Consumer·PostgreSQL 통합 |
| `WJR-004` | ResultStore 저장 실패 | 결과 저장 시도 | Handler가 남긴 변경도 rollback, ACK 미호출 | PostgreSQL 통합 |
| `WJR-005` | DB commit 실패 | 결과와 Job 변경 저장 시도 | 전체 rollback, ACK 미호출 | Consumer 통합 |
| `WJR-006` | 정상 저장과 commit 성공 | Consumer 실행 | `save → commit → ACK` 순서 유지 | Consumer·PostgreSQL 통합 |
| `WJR-007` | commit 성공 후 ACK 실패 | ACK 호출 | commit 결과 유지, rollback 미호출, 재전달 허용 | Consumer·PostgreSQL 통합 |
| `WJR-008` | Handler와 ResultStore가 서로 다른 session 사용 | Consumer commit | 원자성이 깨지는 음성 대조 결과 확인 | PostgreSQL 통합 |
| `WJR-009` | 동일 `(ai_job_id, attempt_no)` 두 번 저장 | 두 번째 `ai_job_attempt` insert | unique 제약으로 중복 실행 이력 차단 | PostgreSQL 통합 |
| `WJR-010` | 원본 DB·Provider 예외에 민감 문자열 포함 | 안전 오류 변환 | 외부 오류와 exception chain에 원문이 포함되지 않음 | 단위·통합 |

## 현재 구현된 테스트 연결

| 테스트 ID | 현재 검증 파일 |
| --- | --- |
| `WJR-001`, `WJR-002`, `WJR-003`, `WJR-004`, `WJR-005`, `WJR-006`, `WJR-007`, `WJR-008`, `WJR-010` | `tests/integration/test_worker_consumer_session_sharing.py` |
| SQLAlchemy commit·rollback 위임 | `ai_worker/tests/core/test_sqlalchemy_transaction.py` |
| `WJR-009` 스키마 제약 존재 | `backend/app/tests/models/test_async_job_schema.py` |
| Core lease·fencing 타입 계약 | `ai_worker/tests/core/test_job_execution.py` |
| `WJR-101`~`WJR-106`, `WJR-108`~`WJR-112` | `ai_worker/tests/core/test_sqlalchemy_job_execution_repository.py` |
| `WJR-107`, `WJR-113`, `WJR-114` | `tests/integration/test_worker_job_execution_repository.py` |
| `WJR-113`, `WJR-115`, `WJR-117`, `WJR-123` | `ai_worker/tests/core/test_consumer_execution.py` |
| `WJR-118`, `WJR-122` 재시도 분류 계약 | `ai_worker/tests/core/test_retry.py` |
| `WJR-119`~`WJR-121` 공통 저장·rollback 경계 | `ai_worker/tests/core/test_consumer_execution.py` |


`WJR-009`의 실제 PostgreSQL 중복 INSERT 테스트는 아직 추가되지 않았다.
`WJR-119`~`WJR-121`의 Guide·Chat별 fallback payload와 도메인 상태 검증은
각 Handler·ResultStore 구현에서 추가하며, #141은 Worker가 소유하는
조건부 완료·commit·rollback 경계를 고정한다.


## 승인된 Repository 입출력

| 작업 | 입력 | 출력 | DB 변경 |
| --- | --- | --- | --- |
| delivery 상태 조회 | `job_id`, `event_id`, `job_type`, `attempt`, `now` | 실행 가능·이미 처리·거부 사유 | 없음 |
| lease 획득 | `job_id`, `event_id`, `attempt`, `now`, lease duration | lease token·attempt·만료 시각 또는 획득 실패 | Job 상태·attempt·lease·heartbeat, Attempt 이력 |
| heartbeat 갱신 | `job_id`, `attempt`, `lease_token`, `now`, 새 만료 시각 | 실행 권한 유지 여부 | heartbeat·lease 만료 시각 |
| 결과 commit 준비 | `job_id`, `event_id`, `attempt`, `lease_token`, 완료 상태·시각 | 조건부 갱신 성공 여부 | Job terminal 상태·소비 event·Attempt 이력 |
| 중복 전달 확인 | `job_id`, `event_id`, `attempt` | 이미 commit됨·처리 필요·격리 필요 | 없음 |

lease 획득·attempt 생성과 최종 결과 저장·완료 갱신은 Consumer가 소유한
`AsyncSession`에서 서로 분리된 순차 transaction으로 실행한다. lease
transaction은 Handler·Provider 호출 전에 commit한다. 도메인 ResultStore와
Job Repository의 최종 변경은 하나의 결과 transaction에서 함께 commit한다.

실행 중 heartbeat는 Consumer 실행 Session을 공유하지 않고 별도
`AsyncSession`의 짧은 transaction에서 조건부 갱신 후 즉시 commit한다.
heartbeat 또는 최종 완료 갱신의 영향 행이 0건이면 생성 결과를 폐기하고
ACK하지 않는다.

## Repository 구현 테스트 Matrix

| ID | 입력·사전 상태 | 결정안 기준 기대 결과 |
| --- | --- | --- |
| `WJR-101` | `PENDING`, DB attempt `0`, 수신 attempt `1`, event 일치 | lease 획득 성공과 `PROCESSING` 전환 |
| `WJR-102` | `expected_event_id`와 수신 event 불일치 | lease 미획득, DB 변경 없음 |
| `WJR-103` | Outbox가 없거나 Job·event 연결 불일치 | lease 미획득, DB 변경 없음 |
| `WJR-104` | 수신 attempt가 허용된 다음 attempt와 불일치 | lease 미획득, DB 변경 없음 |
| `WJR-105` | `available_at`이 미래 | lease 미획득, DB 변경 없음 |
| `WJR-106` | 허용되지 않은 Job 상태 | lease 미획득, DB 변경 없음 |
| `WJR-107` | 두 Worker가 같은 Job·event·attempt로 동시에 조건부 UPDATE 실행 | 정확히 한 Worker만 영향 행 `1`로 lease를 획득하고, 다른 Worker는 영향 행 `0`으로 실패 |
| `WJR-108` | 현재 attempt·token으로 heartbeat | heartbeat와 만료 시각 갱신 |
| `WJR-109` | 이전 attempt 또는 token으로 heartbeat | 영향 행 0건, 실행 권한 상실 |
| `WJR-110` | 만료된 lease로 heartbeat | 영향 행 0건, 실행 권한 상실 |
| `WJR-111` | 현재 lease 소유자가 결과 저장 | 결과와 terminal 상태가 함께 commit됨 |
| `WJR-112` | 이전 token을 가진 Worker가 늦게 결과 저장 | 결과·Job 변경 모두 없음 |
| `WJR-113` | 결과 commit 후 동일 event 재전달 | Handler·Provider 재호출 없이 ACK 가능 상태 반환 |
| `WJR-114` | 결과 commit 이후 ACK 실패 후 재전달 | 결과와 Attempt 이력이 각각 1건 유지 |
| `WJR-115` | heartbeat 조건부 갱신 실패 후 Handler 완료 | 늦은 결과 폐기, ACK 미호출 |
| `WJR-116` | Handler 또는 하위 Repository가 Worker transaction 전에 별도 commit 시도 | 별도 commit을 허용하지 않고 Worker 소유 transaction만 사용 |
| `WJR-117` | Handler 완료 후 결과 저장 직전에 fencing 조건이 무효화됨 | 조건부 갱신 0건, 도메인 결과와 Job 상태 모두 rollback |
| `WJR-118` | timeout·rate limit·일시적 의존성 장애이며 terminal 결과 없음 | 정규화된 재시도 가능 실패로 반환하고 terminal 결과를 저장하지 않음 |
| `WJR-119` | 승인된 고정 fallback 저장 성공 | 도메인 `REJECTED`와 Job `COMPLETED`를 원자적으로 저장하고 재시도하지 않음 |
| `WJR-120` | Safety 검증 실패 후 승인 fallback 저장 성공 | Job `COMPLETED`, 동일 Provider 호출 재시도 없음 |
| `WJR-121` | Safety fallback 저장까지 실패 | Job `FAILED`, 안전한 failure code만 저장 |
| `WJR-122` | schema·영구 입력 오류 | 재시도하지 않고 승인된 terminal 경로로 처리 |
| `WJR-123` | 동일 event 결과가 이미 commit됨 | 신규 lease·Handler·Provider·결과 저장 없이 ACK만 호출 |


### `WJR-107` PostgreSQL 동시성 검증 방법

- 서로 다른 `AsyncSession` 두 개를 사용한다.
- 두 Worker가 동일한 Job·event·attempt를 입력으로 사용한다.
- 두 작업을 barrier에서 동시에 시작한다.
- lease 획득 로직은 사전 `SELECT FOR UPDATE` 없이 단일 조건부 UPDATE를 실행한다.
- 반환 결과는 성공 1건, 실패 1건이어야 한다.
- DB `attempt_count`는 한 번만 증가한다.
- `ai_job_attempt` 실행 이력은 1건만 존재해야 한다.
- 실패한 Worker의 token으로 heartbeat·결과 저장을 시도하면 영향 행이 0이어야 한다.

### Handler transaction 검증 원칙

- Handler와 하위 Repository는 `commit()`을 호출하지 않는다.
- Handler는 결과 또는 정규화된 실패 분류만 반환한다.
- 결과의 `job_id`·`event_id` 검증 후 Worker transaction에 저장한다.
- 저장 직전 fencing 조건이 무효하면 모든 미완료 결과를 rollback한다.
- 기존 동기 Guide·Chat 서비스의 내부 commit 경로는 Worker Handler에서
  직접 재사용하지 않는다.
