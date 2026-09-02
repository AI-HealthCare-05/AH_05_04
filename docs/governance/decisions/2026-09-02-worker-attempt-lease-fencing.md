# Product Decision: Worker attempt·lease·fencing 및 ACK 경계

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-141-20260902` |
| 상태 | Approved |
| 결정일 | 2026-09-02 |
| 결정자 | 권가빈 — PM·Product acceptance (`@hazelnutflavoured`) |
| 추적 Issue | [#141](https://github.com/AI-HealthCare-05/AH_05_04/issues/141) |
| 승인 증빙 | [#141 승인 댓글](https://github.com/AI-HealthCare-05/AH_05_04/issues/141#issuecomment-5505174399) |
| 적용 범위 | Post-MVP-1 Worker Job 실행·lease·fencing·commit-before-ACK |

## 결정 1: attempt 증가 시점

- Job 접수 직후 `ai_job.attempt_count`는 `0`이다.
- 최초 Stream message의 `attempt`는 `1`이다.
- lease 획득 전 수신 attempt는 DB `attempt_count + 1`이어야 한다.
- Worker가 lease를 획득하는 같은 transaction에서
  `attempt_count`를 수신 attempt 값으로 갱신한다.
- 숫자형 실행 세대는 `attempt_count`로 판별한다.

## 결정 2: lease 획득과 중복 event

lease 획득은 Job을 먼저 `SELECT`한 뒤 별도 `UPDATE`하는 방식으로
구현하지 않는다. 위 진입 조건과 아직 소비되지 않은 event 조건을 모두
하나의 `UPDATE ... WHERE`에 포함한 원자적 조건부 갱신으로 구현한다.

- 영향 행이 `1`이면 lease 획득 성공
- 영향 행이 `0`이면 lease 미획득
- 실패 후 상태 구분이 필요하면 별도 read-only 조회를 수행
- 두 Worker가 동시에 실행해도 한 Worker만 조건부 UPDATE에 성공해야 함

신규 lease 획득 전에 다음 항목을 검증한다.

- Job 존재와 `job_type` 일치
- 수신 `event_id == ai_job.expected_event_id`
- 실제 Outbox event와 Job·event 연결 일치
- Outbox attempt와 수신 attempt 일치
- 수신 attempt와 DB attempt의 다음 실행 관계
- 허용된 Job 상태와 `available_at`
- 동일 event가 이미 처리됐는지 여부

`last_consumed_event_id`가 수신 event와 같고 실제 Job·Outbox 연결도
일치하면 이미 commit된 event로 처리한다. 이 경우 신규 lease를 만들거나
Provider·Handler·결과 저장을 반복하지 않고 ACK 경계로 이동한다.

## 결정 3: 실행 소유권과 fencing

lease 획득 transaction에서 다음을 원자적으로 수행한다.

- Job 상태를 `PROCESSING`으로 전환
- `attempt_count`를 수신 attempt로 갱신
- 실행마다 새로운 opaque `lease_token` 발급
- `lease_expires_at`과 `heartbeat_at` 설정

동일 attempt의 실행 소유권은 opaque `lease_token`으로 구분한다.
heartbeat와 결과·상태 변경은 다음 조건을 모두 사용한 조건부 갱신으로
fencing한다.

- `job_id`
- `attempt_count`
- `lease_token`
- `status=PROCESSING`
- 만료되지 않은 lease

조건부 갱신의 영향 행이 0건이면 실행 권한을 잃은 것으로 처리한다.
해당 Worker의 결과와 상태 변경은 commit하지 않는다.

동시성 제어는 긴 `SELECT FOR UPDATE` transaction이 아니라 optimistic
conditional UPDATE를 사용한다. Handler·Provider 실행 동안 DB row lock을
유지하지 않고, PostgreSQL의 원자적 UPDATE와 영향 행 수로 실행 소유자를
결정하기 위해 이 방식을 선택한다.

lease 획득과 attempt 생성 transaction은 Handler·Provider 호출 전에
commit한다. Handler·Provider 실행 중 heartbeat는 실행 transaction과 다른
`AsyncSession`의 짧은 transaction에서 갱신하고 즉시 commit한다. Handler가
반환한 뒤에는 도메인 결과 저장과 조건부 완료 갱신을 별도 transaction에서
함께 commit한다. 따라서 실행 경계는 `lease 획득 commit → Handler·Provider와
별도 heartbeat transaction → 결과·완료 commit → ACK` 순서다.

heartbeat와 결과 저장도 동일하게 조건부 UPDATE의 영향 행이 `1`인
경우만 성공으로 처리한다. 영향 행이 `0`이면 stale attempt, 이전 token,
만료 lease 또는 상태 변경 여부를 추정해 덮어쓰지 않고 실행 권한 상실로
처리한다.

Worker는 heartbeat의 조건부 갱신이 `0`건인 것을 감지하면 진행 중인
Handler·Provider task를 취소한다. 취소된 실행의 결과는 저장하지 않고 Job
상태도 변경하지 않으며 ACK하지 않는다.

## 결정 4: 결과 commit과 ACK

Guide·Chat·OCR Handler는 검증된 결과 반환까지만 담당한다.
Worker는 같은 DB transaction에서 다음을 처리한다.

- Handler 결과 식별자 검증
- 도메인 결과 저장
- Job terminal 상태 저장
- 현재 event를 `last_consumed_event_id`로 저장
- 실행 이력 저장

DB transaction commit이 성공한 이후에만 Stream ACK를 호출한다.

ACK 실패는 완료된 DB commit을 rollback하지 않는다. 동일 event가
재전달되면 이미 commit된 결과를 재사용하고 Provider·Handler와 결과
저장을 반복하지 않는다.

## `expected_event_id` 갱신 소유권

#141 Worker는 `ai_job.expected_event_id`를 검증 목적으로만 읽고 직접
생성하거나 추정해 갱신하지 않는다.

- 최초 Job·Outbox 생성 시 설정: #147
- retry용 새 Outbox 생성과 같은 transaction에서 갱신: #142
- 수신 event 검증과 소비 이력 기록: #141

#141은 실제 Outbox row, Job 연결, `expected_event_id`, 수신 attempt가
모두 일치할 때만 lease 획득을 시도한다. 연결이 불명확하면 새 Outbox나
expected event를 임의로 만들지 않는다.

## 결정 5: Handler와 DB side effect 소유권

OCR·Guide·Chat Handler는 Provider 또는 Graph 실행 결과와 정규화된
실패 분류를 Worker에 반환한다. 다음 항목은 Worker가 소유한다.

- Handler 결과의 `job_id`·`event_id` 검증
- 현재 `attempt_count + lease_token` 실행 소유권 검증
- 도메인 결과와 terminal Job 상태 저장
- `last_consumed_event_id` 저장
- DB transaction commit
- commit 이후 Stream ACK

Handler 또는 Handler가 호출하는 하위 Repository는 Worker의 fencing
검증 전에 별도 commit하지 않는다. 기존 동기 서비스가 내부 commit을
수행한다면 Worker Handler에서 그대로 재사용하지 않고 다음 중 하나로
분리한다.

- Worker가 소유한 transaction에 결과 저장을 편입
- DB commit을 수행하지 않는 실행 전용 Handler로 분리

Handler 결과 ID가 메시지와 다르거나 결과 저장 시점의 조건부 fencing
갱신이 0건이면 현재 transaction을 rollback하고 생성 결과를 폐기한다.

## 결정 6: 재시도와 승인 fallback

timeout, rate limit, 일시적 Provider·의존성 장애는 terminal 결과를
저장하지 못한 경우에만 재시도 대상으로 분류한다.

승인된 고정 fallback을 저장할 수 있으면 다음과 같이 종결한다.

- 도메인 결과: `REJECTED`
- Job 상태: `COMPLETED`
- 동일 Provider 호출 재시도 없음

schema 오류, 영구 입력 오류와 Safety 검증 오류는 재시도하지 않는다.
Safety 검증 실패도 승인 fallback 저장에 성공하면 `COMPLETED`이며,
fallback 저장까지 실패한 경우에만 `FAILED`로 종결한다.

동일 event의 결과가 이미 commit된 경우에는 Handler와 Provider를
다시 호출하지 않고 ACK만 수행한다.

## 후속 범위

다음 항목은 [#142](https://github.com/AI-HealthCare-05/AH_05_04/issues/142)
범위로 유지한다.

- 만료된 Pending message reclaim
- lease 만료 Job의 retry 전환
- 증가한 attempt의 새 Outbox 생성
- retry backoff
- quarantine·DLQ·Reconciler

#141에서는 만료되거나 이전 token을 가진 Worker의 heartbeat와
결과 저장을 차단한다.

## 적용과 검증

- 이 결정은 Approved target이며 구현 완료를 뜻하지 않는다.
- #141 구현 PR에서 Repository 조건부 갱신, Worker 실행 경계,
  계약 문서와 단위·PostgreSQL 통합 테스트를 함께 제출한다.
- 실제 환자 정보, 의료 원문, Provider 원문 오류나 비밀정보를
  lease·실행 이력 또는 테스트 fixture에 저장하지 않는다.
