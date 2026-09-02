# Worker Job Repository 입출력 테스트 계획

| 항목 | 값 |
| --- | --- |
| 상태 | Draft — Product Decision 승인 대기 |
| 구현 이슈 | [#141](https://github.com/AI-HealthCare-05/AH_05_04/issues/141) |
| 기준 계약 | `async-job-v1.md`, `outbox-stream-v1.md` |
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

## Product Decision 승인 대기 항목

다음 항목은 승인 전 테스트 기대값이나 구현으로 확정하지 않는다.

- `attempt_count`를 수신 attempt로 갱신하는 정확한 시점
- lease 획득을 허용할 Job 상태
- 숫자형 attempt와 opaque `lease_token`의 fencing 역할 분담
- heartbeat 조건부 갱신 조건
- lease 만료 Worker의 결과 저장 거부 조건
- 동일 event 재전달 시 Repository 반환 결과
- lease 만료 후 reclaim·retry 전환 책임

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

`WJR-009`의 실제 PostgreSQL 중복 INSERT 테스트는 아직 추가되지 않았다.


## Product Decision 승인 대기 Repository 입출력 초안

> 이 절은 구현 기준이 아닌 검토용 초안이다. Product Decision 승인 후
> 메서드명·반환 상태·조건부 갱신 조건을 확정하고 테스트 코드로 전환한다.

| 작업 | 입력 | 출력 | DB 변경 |
| --- | --- | --- | --- |
| delivery 상태 조회 | `job_id`, `event_id`, `job_type`, `attempt`, `now` | 실행 가능·이미 처리·거부 사유 | 없음 |
| lease 획득 | `job_id`, `event_id`, `attempt`, `now`, lease duration | lease token·attempt·만료 시각 또는 획득 실패 | Job 상태·attempt·lease·heartbeat, Attempt 이력 |
| heartbeat 갱신 | `job_id`, `attempt`, `lease_token`, `now`, 새 만료 시각 | 실행 권한 유지 여부 | heartbeat·lease 만료 시각 |
| 결과 commit 준비 | `job_id`, `event_id`, `attempt`, `lease_token`, 완료 상태·시각 | 조건부 갱신 성공 여부 | Job terminal 상태·소비 event·Attempt 이력 |
| 중복 전달 확인 | `job_id`, `event_id`, `attempt` | 이미 commit됨·처리 필요·격리 필요 | 없음 |

모든 작업은 호출자가 주입한 동일 `AsyncSession`을 사용하고 직접 commit하지 않는다.
도메인 ResultStore와 Job Repository 변경은 Consumer가 소유한 하나의
transaction에서 함께 commit한다.

## Product Decision 승인 후 활성화할 테스트

| ID | 입력·사전 상태 | 결정안 기준 기대 결과 |
| --- | --- | --- |
| `WJR-101` | `PENDING`, DB attempt `0`, 수신 attempt `1`, event 일치 | lease 획득 성공과 `PROCESSING` 전환 |
| `WJR-102` | `expected_event_id`와 수신 event 불일치 | lease 미획득, DB 변경 없음 |
| `WJR-103` | Outbox가 없거나 Job·event 연결 불일치 | lease 미획득, DB 변경 없음 |
| `WJR-104` | 수신 attempt가 허용된 다음 attempt와 불일치 | lease 미획득, DB 변경 없음 |
| `WJR-105` | `available_at`이 미래 | lease 미획득, DB 변경 없음 |
| `WJR-106` | 허용되지 않은 Job 상태 | lease 미획득, DB 변경 없음 |
| `WJR-107` | 두 Worker가 같은 Job lease 동시 획득 | 한 Worker만 성공 |
| `WJR-108` | 현재 attempt·token으로 heartbeat | heartbeat와 만료 시각 갱신 |
| `WJR-109` | 이전 attempt 또는 token으로 heartbeat | 영향 행 0건, 실행 권한 상실 |
| `WJR-110` | 만료된 lease로 heartbeat | 영향 행 0건, 실행 권한 상실 |
| `WJR-111` | 현재 lease 소유자가 결과 저장 | 결과와 terminal 상태가 함께 commit됨 |
| `WJR-112` | 이전 token을 가진 Worker가 늦게 결과 저장 | 결과·Job 변경 모두 없음 |
| `WJR-113` | 결과 commit 후 동일 event 재전달 | Handler·Provider 재호출 없이 ACK 가능 상태 반환 |
| `WJR-114` | 결과 commit 이후 ACK 실패 후 재전달 | 결과와 Attempt 이력이 각각 1건 유지 |
| `WJR-115` | heartbeat 조건부 갱신 실패 후 Handler 완료 | 늦은 결과 폐기, ACK 미호출 |

위 기대 결과는 Product Decision 승인 답변과 일치하는지 대조한 뒤 확정한다.
