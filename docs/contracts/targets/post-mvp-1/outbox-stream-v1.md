# Transactional Outbox와 Redis Stream 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Contract Freeze v4 target — 2026-08-27 검증 |
| 구현·리뷰 | Outbox 접수(#147), Redis Adapter·EventPublisher(#140), DB Outbox 선점·발행(#219), Worker lease·fencing(#141), reclaim·retry·quarantine·DLQ(#142) 구현 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-a-async-foundation-v1.md` |
| Last verified | 2026-08-27 |

## 생산 흐름

API는 하나의 DB transaction에서 다음을 커밋한다.

1. 멱등성 레코드
2. 도메인 placeholder 또는 요청 레코드
3. 새 Outbox `event_id`를 `expected_event_id`로 가진 `PENDING` Job
4. 같은 `event_id`의 Outbox 이벤트

Outbox publisher는 미발행 row를 짧은 lease로 선점하고 Redis Stream에 `XADD`한 뒤 `published_at`을 기록한다. 장애 경계상 중복 발행은 허용되며 전달 보장은 at-least-once다.

- Publisher lease는 30초이며 lease token으로 fencing한다.
- Publisher는 `status=PENDING AND available_at <= now()` row뿐 아니라 `status=CLAIMED AND claim_expires_at <= now()` row도 재선점할 수 있다. `CLAIMED` row를 재선점하지 않으면 Publisher가 row 선점 뒤 종료했을 때 event가 영구 정체된다.
- 발행 완료 갱신은 `event_id`, 현재 `claim_token`, `status=CLAIMED` 조건으로만 수행한다. 오래된 Publisher가 뒤늦게 돌아와 새 claim 소유자의 row를 `PUBLISHED`로 덮어쓰면 안 된다.
- Reconciler는 `RETRY_WAIT` due Job과 lease가 만료된 `PROCESSING` Job을 복구 대상으로 확인한다. 증가한 attempt의 후속 Outbox 생성은 `RETRY_WAIT` due Job에서만 수행하며, 미발행 `PENDING` Job의 기존 Outbox 재발행은 Publisher 책임이다. Reconciler는 미발행 `PENDING` Job에 대해 다음 attempt를 만들지 않는다.
- `PENDING` Job의 `expected_event_id`가 가리키는 Outbox가 없거나 Job·Outbox 연결이 깨진 경우는 데이터 무결성 오류로 기록하고 alert한다. 이 경우 Reconciler가 추정으로 새 Outbox를 만들지 않는다.

`OUTBOX_EVENT`는 최소 `event_id`, `job_id`, `attempt`, `event_kind`, `schema_version`, `status`, `available_at`, nullable claim token·만료, nullable `published_at`, nullable `stream_message_id`, `created_at`을 저장한다. `stream_message_id`는 Redis `XADD` 성공 후 같은 `event_id + claim_token + status=CLAIMED` fencing UPDATE로 `published_at`과 함께 기록한다. 상태는 `PENDING`, `CLAIMED`, `PUBLISHED`, `CANCELLED`이며 `(job_id, attempt, event_kind)`는 unique다. 최초 접수와 Reconciler 모두 Job의 `expected_event_id`와 Outbox `event_id`를 같은 transaction에서 설정한다.

#219 Publisher는 due row를 `available_at`, `event_id` 순서로 최대 100건 선점한다. claim transaction은 `FOR UPDATE SKIP LOCKED`로 경합을 분리하고 즉시 commit하며, Redis `XADD`를 실행하는 동안 DB row lock을 유지하지 않는다. `trace_id`를 포함한 envelope 검증 실패나 Redis 발행 실패 시에는 `PUBLISHED`·`published_at`·`stream_message_id`를 기록하지 않고, claim 만료 후 같은 Outbox를 재선점한다. 이 경계의 중복 `XADD`는 at-least-once 계약으로 허용한다.

## Stream envelope

- 실행 Stream: `oryak:jobs`
- Consumer Group: `ai-workers`
- Dead-letter Stream: `oryak:jobs:dead-letter`
- 전달 보장: at-least-once

v1 메시지에는 다음 필드만 둔다.

| 필드 | 설명 |
|---|---|
| `schema_version` | `1.0` |
| `event_id` | Outbox UUID, 중복 판별 키 |
| `event_kind` | `JOB_EXECUTE` |
| `job_id` | DB Job UUID |
| `job_type` | `OCR`, `GUIDE`, `CHAT` |
| `domain_type` / `domain_id` | 결과 대상 |
| `attempt` | 발행 시점 시도 번호 |
| `available_at` | 처리 가능 시각 |
| `enqueued_at` | 발행 시각 |
| `trace_id` | 관측성 연결 키 |

- 메시지는 8KiB 이하로 제한한다.
- 처방 내용, 약품명, 질문, 답변, OCR 텍스트, 사용자 식별정보를 싣지 않는다. Worker는 `job_id`로 권한이 제한된 DB 레코드를 조회한다.
- Consumer는 현재 major와 전환 중인 직전 major를 함께 지원한다. 이전 major는 해당 Outbox·Stream·PEL·예약 retry가 모두 0이고 마지막 처리 후 7일 관찰기간이 지난 뒤 제거한다.
- 필수 필드 오류나 지원하지 않는 schema는 Provider를 호출하지 않는다. `MESSAGE_QUARANTINE`과 별도 `DLQ_OUTBOX_EVENT`를 DB에 commit한 뒤 원본 메시지를 ACK하고 운영 경보를 발생시킨다. `DLQ_OUTBOX_EVENT`는 `quarantine_id`를 필수 FK로 사용해 정상 실행 Outbox의 필수 `job_id`와 분리한다. Job 또는 event ID를 파싱할 수 없으면 원문 대신 `stream_entry_id`, `message_digest`, 파싱 가능한 schema/trace metadata, failure code와 수신 시각만 저장한다.
- `MESSAGE_QUARANTINE.job_id`는 메시지에서 `job_id`를 파싱할 수 있고 해당 Job이 DB에 존재할 때만 채운다. 파싱된 값이 `ai_job`에 없으면 FK 위반으로 quarantine commit이 실패해 같은 poison message가 반복 전달될 수 있으므로 `job_id`는 `NULL`로 둔다.
- poison message가 정상 Job을 잘못 `FAILED`로 만들면 안 된다. Job 상태를 변경하려면 파싱한 `job_id`가 존재한다는 사실만으로는 부족하며, 실제 Outbox event, Job의 `expected_event_id`, Job-event 연결과 수신 attempt까지 모두 검증되어야 한다. 검증할 수 없으면 Job 상태는 변경하지 않고 quarantine과 DLQ만 기록한 뒤 ACK한다.
- DLQ publish 실패는 동일 `event_id`로 `min(5초 × 2^(attempt_count-1), 300초)`에 0~20% 양의 jitter를 더해 재시도한다. terminal `FAILED`나 자동 폐기는 두지 않으며 10회 연속 실패부터 매 시도 alert한다.

`DLQ_OUTBOX_EVENT`는 `event_id`, unique `quarantine_id`, `event_kind=QUARANTINE_RECORDED`, DLQ 자체 `schema_version=1.0`, nullable `original_schema_version`, `status=PENDING|CLAIMED|PUBLISHED`, `attempt_count`, `available_at`, nullable claim token·만료, nullable `last_error_code`, nullable `published_at`, `created_at`, `updated_at`을 저장한다. Dead-letter envelope에는 `event_id`, `quarantine_id`, `stream_entry_id`, `message_digest`, `failure_code`, nullable `original_schema_version`, nullable `trace_id`만 포함하고 원본 메시지나 의료정보를 넣지 않는다.

## 소비와 fencing

2026-09-02 [Product Decision `PD-141-20260902`](../../../governance/decisions/2026-09-02-worker-attempt-lease-fencing.md)에 따라 아래 attempt·lease·fencing·commit-before-ACK 기준을 승인했다.

Worker는 메시지 수신 후 DB Job을 다시 읽고 다음을 검증한다.

- Job 존재와 유형 일치
- 수신 `event_id`와 DB `expected_event_id` 일치
- attempt 기준:
  - lease 획득 전 진입 조건은 `outbox_event.attempt = ai_job.attempt_count + 1`이다.
  - Worker가 lease를 획득하는 같은 transaction에서 `ai_job.attempt_count`를 수신 attempt 값으로 갱신한다.
  - lease 획득 이후 Provider 호출·결과 저장·ACK 전 검증에서는 수신 attempt와 DB `attempt_count`가 일치해야 한다.
- 허용된 현재 상태
- `available_at` 도달
- 활성 처방 버전 일치
- 유효한 lease 획득

현재 목록만으로는 Worker artifact version과 Job의 Runtime Release Bundle 호환성을 보장하지 못한다. `RETRY_WAIT` 중 Bundle 변경과 구·신 Worker 동시 배포의 처리 방식은 [후속 Product Decision](../../../governance/post-mvp-1-document-authority.md#구현-전-재결정이-필요한-충돌)에서 상태 전이·drain 방식·계약 테스트와 함께 확정하며, 그 전에는 이 목록을 Bundle 검증이 완료된 구현 계약으로 해석하지 않는다.

신규 lease 획득 전에 `last_consumed_event_id`를 확인한다. 수신 event가 이미 소비됐고 실제 Job·Outbox 연결도 일치하면 신규 lease, Provider 호출과 결과 저장을 생략하고 ACK한다.

heartbeat와 상태 변경·결과 저장은 `job_id + attempt_count + lease_token + status=PROCESSING + 만료되지 않은 lease` 조건부 갱신으로 fencing한다. 영향 행이 0건이면 실행 권한을 잃은 것으로 처리하고 해당 결과와 상태 변경을 commit하지 않는다. 오래된 Worker는 새 lease 소유자의 결과를 덮어쓸 수 없다.

lease 획득과 attempt 생성은 짧은 transaction에서 먼저 commit하며, Handler·Provider 실행 중에는 Job row lock을 유지하지 않는다. 실행 중 heartbeat는 별도 `AsyncSession`의 transaction에서 조건부 갱신하고 즉시 commit한다. Handler 완료 후 도메인 결과와 Job terminal 상태·`last_consumed_event_id`는 다시 하나의 결과 transaction에서 원자적으로 commit한 뒤 ACK한다.

heartbeat 조건부 갱신이 `0`건이면 Worker는 진행 중인 Handler·Provider task를 취소하고 결과 저장과 ACK를 수행하지 않는다. 따라서 lease를 잃은 Worker가 새 lease 소유자와 Provider 호출을 계속 중복하지 않는다.

## ACK 불변식

다음 중 하나가 DB에 commit된 뒤에만 ACK한다.

- 결과와 `COMPLETED`
- `RETRY_WAIT`, 현재 event의 `last_consumed_event_id`, 비어 있는 expected event
- 최종 `FAILED`
- 최신 버전 불일치에 따른 `STALE`
- `MESSAGE_QUARANTINE`과 `quarantine_id` 기반 별도 `DLQ_OUTBOX_EVENT`

DB commit 전에 ACK하지 않는다. process crash로 재전달되면 Job 상태와 lease가 중복 실행을 차단한다.

Publisher의 `XADD` 성공 뒤 `published_at` 기록이 실패할 수 있으므로 같은 `event_id`의 Stream entry가 둘 이상 존재할 수 있다. Worker는 Job row를 잠그고 `last_consumed_event_id`를 확인해 이미 commit된 event를 Provider 호출 없이 ACK한다. expected event 불일치가 소비 이력으로 설명되지 않으면 quarantine commit 후 ACK한다.

Reconciler는 후속 `OUTBOX_EVENT`를 생성하는 같은 transaction에서 Job의 `expected_event_id`를 새 Outbox `event_id`로 갱신한다. Reconciler는 Job을 `PROCESSING`으로 직접 전환하지 않으며, `PROCESSING` 전환은 후속 event를 수신한 Worker가 lease를 획득하는 시점에 수행한다.

Worker가 `PROCESSING`으로 전환한 뒤 종료해 lease가 만료된 Job은 Reconciler가 복구 대상으로 확인한다. Reconciler는 만료된 lease, 현재 `attempt_count`, `expected_event_id`, `max_attempts`, `last_consumed_event_id`를 검증한 뒤 해당 attempt를 실패로 기록하고, 재시도 가능하면 Job을 `RETRY_WAIT`로 전환한다. 재시도 횟수를 소진했으면 최종 `FAILED`로 종결한다. 새 Provider 호출은 `RETRY_WAIT`의 `available_at` 도달 후 Reconciler가 증가한 attempt의 새 Outbox를 만들고, Worker가 그 event의 lease를 획득한 뒤에만 가능하다.

`available_at`이 지난 `RETRY_WAIT` Job은 Reconciler의 회수 대상이다. Redis 장애나 Publisher 종료로 발행되지 못한 `PENDING` Job은 Reconciler가 새 attempt를 만들지 않고 Publisher가 기존 Outbox row를 재선점해 발행한다. 정확한 실행 주기, batch size와 정체 판단 시간은 Worker 구현 PR에서 확정하되, DB row claim과 unique 제약으로 같은 retry Outbox가 중복 생성되지 않아야 한다.

## 재시도와 격리

접수 직후 `attempt_count`는 0이고, 최초 Stream message의 `attempt`는 1이다. Worker가 lease를 획득하는 transaction에서 `attempt_count`를 수신 attempt 값으로 갱신하며 최대 시도에는 최초 실행을 포함한다. 지연은 `min(5초 × 2^(attempt_count-1), 60초)`에 0~20% 양의 jitter를 더한다. timeout·rate limit·일시적 의존성 장애만 재시도하며 유효성 오류·지원하지 않는 schema·영구 입력·Safety 검증 오류는 재시도하지 않는다. 격리 Stream과 DLQ에도 원본 의료 데이터가 아닌 envelope와 오류 코드만 남긴다.

lease 만료로 회수된 Job도 현재 attempt를 사용한 실패로 계산한다. 같은 Stream 메시지를 `XAUTOCLAIM`으로 다시 받은 Worker는 Provider를 즉시 재호출하지 않고 DB Job 상태와 `attempt_count`를 먼저 확인한다. 해당 attempt가 이미 `RETRY_WAIT`, `FAILED`, `COMPLETED`, `STALE` 중 하나로 반영되어 있으면 Provider를 호출하지 않고 ACK한다. 다음 Provider 호출은 Reconciler가 증가한 attempt와 새 `OUTBOX_EVENT`를 만든 뒤에만 수행한다.

Safety validation 실패는 재시도하지 않지만 항상 Job `FAILED`를 뜻하지 않는다. Track F에서 생성 답변을 폐기하고 승인 fallback 저장에 성공하면 도메인 결과는 `REJECTED`, Job은 `COMPLETED`로 끝난다. fallback 저장까지 실패한 경우에만 Job을 `FAILED`로 전환한다.

publish가 완료된 Outbox·quarantine·DLQ 메타데이터의 30일 보존은 Privacy 승인 대상 기본안이다. 단순히 30일이 지났다는 이유만으로 정상 실행 Outbox를 삭제하지 않는다. 연결된 Job이 terminal 상태이고, 관련 Stream entry, PEL, 예약 retry와 재발행 대상이 모두 정리된 경우에만 삭제할 수 있다. 미발행 `PENDING|CLAIMED` DLQ Outbox와 연결된 `MESSAGE_QUARANTINE`은 TTL로 삭제하지 않는다. legal hold 또는 더 엄격한 감사 정책이 있으면 해당 정책을 적용한다.

Job 실행 메타데이터는 terminal 전환 후 90일 보존하므로, `AI_JOB.expected_event_id`와 `last_consumed_event_id`가 Outbox 삭제를 막으면 안 된다. 두 FK는 nullable이며 Outbox 삭제 시 `ON DELETE SET NULL` 또는 삭제 전 참조 해제로 처리한다. 도메인 결과 row의 `ai_job_id`도 결과 보존을 우선해 nullable FK와 `ON DELETE SET NULL`을 기본으로 하며, Job 삭제 때문에 사용자에게 보존해야 할 OCR·Guide·Chat 결과를 삭제하지 않는다.
