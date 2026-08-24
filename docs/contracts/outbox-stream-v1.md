# Transactional Outbox와 Redis Stream 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved target — 2026-08-24 팀 인계 기준 |
| 구현·리뷰 | Not implemented · 구현 동기화와 관련 지정 리뷰어 검토 대기 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-a-async-foundation-v1.md` |
| Last verified | 2026-08-24 |

## 생산 흐름

API는 하나의 DB transaction에서 다음을 커밋한다.

1. 멱등성 레코드
2. 도메인 placeholder 또는 요청 레코드
3. 새 Outbox `event_id`를 `expected_event_id`로 가진 `PENDING` Job
4. 같은 `event_id`의 Outbox 이벤트

Outbox publisher는 미발행 row를 짧은 lease로 선점하고 Redis Stream에 `XADD`한 뒤 `published_at`을 기록한다. 장애 경계상 중복 발행은 허용되며 전달 보장은 at-least-once다.

- Publisher lease는 30초이며 lease token으로 fencing한다.
- Reconciler는 DB row claim으로 due Job을 선점해 여러 instance가 동시에 실행되어도 같은 retry Outbox를 하나만 만든다.

`OUTBOX_EVENT`는 최소 `event_id`, `job_id`, `attempt`, `event_kind`, `schema_version`, `status`, `available_at`, nullable claim token·만료, nullable `published_at`, `created_at`을 저장한다. 상태는 `PENDING`, `CLAIMED`, `PUBLISHED`, `CANCELLED`이며 `(job_id, attempt, event_kind)`는 unique다. 최초 접수와 Reconciler 모두 Job의 `expected_event_id`와 Outbox `event_id`를 같은 transaction에서 설정한다.

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
- DLQ publish 실패는 동일 `event_id`로 `min(5초 × 2^(attempt_count-1), 300초)`에 0~20% 양의 jitter를 더해 재시도한다. terminal `FAILED`나 자동 폐기는 두지 않으며 10회 연속 실패부터 매 시도 alert한다.

`DLQ_OUTBOX_EVENT`는 `event_id`, unique `quarantine_id`, `event_kind=QUARANTINE_RECORDED`, DLQ 자체 `schema_version=1.0`, nullable `original_schema_version`, `status=PENDING|CLAIMED|PUBLISHED`, `attempt_count`, `available_at`, nullable claim token·만료, nullable `last_error_code`, nullable `published_at`, `created_at`, `updated_at`을 저장한다. Dead-letter envelope에는 `event_id`, `quarantine_id`, `stream_entry_id`, `message_digest`, `failure_code`, nullable `original_schema_version`, nullable `trace_id`만 포함하고 원본 메시지나 의료정보를 넣지 않는다.

## 소비와 fencing

Worker는 메시지 수신 후 DB Job을 다시 읽고 다음을 검증한다.

- Job 존재와 유형 일치
- 수신 `event_id`와 DB `expected_event_id` 일치
- 수신 attempt와 DB `attempt_count` 일치
- 허용된 현재 상태
- `available_at` 도달
- 활성 처방 버전 일치
- 유효한 lease 획득

상태 변경과 결과 저장은 `job_id + lease_token + expected_status` 조건부 갱신으로 fencing한다. 오래된 Worker는 새 lease 소유자의 결과를 덮어쓸 수 없다.

## ACK 불변식

다음 중 하나가 DB에 commit된 뒤에만 ACK한다.

- 결과와 `COMPLETED`
- `RETRY_WAIT`, 현재 event의 `last_consumed_event_id`, 비어 있는 expected event
- 최종 `FAILED`
- 최신 버전 불일치에 따른 `STALE`
- `MESSAGE_QUARANTINE`과 `quarantine_id` 기반 별도 `DLQ_OUTBOX_EVENT`

DB commit 전에 ACK하지 않는다. process crash로 재전달되면 Job 상태와 lease가 중복 실행을 차단한다.

Publisher의 `XADD` 성공 뒤 `published_at` 기록이 실패할 수 있으므로 같은 `event_id`의 Stream entry가 둘 이상 존재할 수 있다. Worker는 Job row를 잠그고 `last_consumed_event_id`를 확인해 이미 commit된 event를 Provider 호출 없이 ACK한다. expected event 불일치가 소비 이력으로 설명되지 않으면 quarantine commit 후 ACK한다.

`RETRY_WAIT` commit 후 Reconciler가 due Job을 선점해 새로운 event ID와 증가한 attempt의 후속 Outbox를 만든다. Worker가 retry 메시지를 Redis에 직접 추가하지 않는다.

## 재시도와 격리

`attempt_count`는 최초 실행에서 1이고 최대 시도에는 최초 실행을 포함한다. 지연은 `min(5초 × 2^(attempt_count-1), 60초)`에 0~20% 양의 jitter를 더한다. timeout·rate limit·일시적 의존성 장애만 재시도하며 유효성 오류·지원하지 않는 schema·영구 입력·Safety 검증 오류는 재시도하지 않는다. 격리 Stream과 DLQ에도 원본 의료 데이터가 아닌 envelope와 오류 코드만 남긴다.

publish가 완료된 Outbox·quarantine·DLQ 메타데이터의 30일 보존은 Privacy 승인 대상 기본안이다. 미발행 `PENDING|CLAIMED` DLQ Outbox와 연결된 `MESSAGE_QUARANTINE`은 TTL로 삭제하지 않는다. legal hold 또는 더 엄격한 감사 정책이 있으면 해당 정책을 적용한다.
