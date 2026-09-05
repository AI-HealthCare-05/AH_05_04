# Worker Pending·DLQ 운영 Runbook

이 문서는 Worker Pending reclaim, retry, quarantine, DLQ 상태를 안전하게 확인하고
복구하는 절차다. 원본 의료정보, Provider 응답, 인증정보는 명령 출력이나 운영 기록에
복사하지 않는다.

## 기본 원칙

- 정상 복구는 Worker의 `RecoveryScheduler`에 맡긴다.
- DB 상태 확인 없이 Redis entry를 직접 `XACK` 또는 `XDEL`하지 않는다.
- poison entry는 `message_quarantine`과 `dlq_outbox_event`가 commit된 뒤에만 ACK한다.
- DLQ 재처리는 원본 Stream payload 재발행이 아니라 승인된 Job 접수 경로에서 새
  Job과 Outbox를 생성하는 방식으로 수행한다.
- `message_digest`, `stream_entry_id`, `failure_code`, `trace_id`만 운영 추적에 사용한다.

## 1. Pending 적체 확인

운영 환경의 secret 관리 절차로 Redis 인증을 완료한 뒤 다음 값을 확인한다.

```text
XPENDING <REDIS_STREAM_NAME> <REDIS_CONSUMER_GROUP>
XPENDING <REDIS_STREAM_NAME> <REDIS_CONSUMER_GROUP> - + 100
```

확인 항목:

- Pending 총수
- consumer별 적체 수
- 가장 오래된 idle 시간
- delivery count가 반복 증가하는 entry

기본 reclaim 기준은 `RECONCILER_MIN_IDLE_MS`, 한 주기 처리량은
`RECONCILER_BATCH_SIZE`, 실행 간격은 `RECONCILER_INTERVAL_SECONDS`에서 확인한다.
유효한 lease가 남아 있다면 수동 reclaim하지 않는다.

## 2. Job·lease 상태 확인

운영 DB에서 대상 식별자만 사용해 다음 상태를 확인한다.

```sql
SELECT id, status, attempt_count, max_attempts,
       expected_event_id, lease_expires_at, heartbeat_at
FROM ai_job
WHERE id = :job_id;
```

- `PROCESSING`이며 lease가 유효하면 Worker 처리를 기다린다.
- lease가 만료되면 Scheduler가 `RETRY_WAIT` 또는 `FAILED`로 전환하는지 확인한다.
- `RETRY_WAIT`의 `available_at`이 지났다면 후속 Outbox가 하나만 만들어졌는지 확인한다.
- 이전 fencing token을 가진 Worker 결과는 commit되면 안 된다.

## 3. Quarantine 확인

```sql
SELECT id, stream_name, stream_entry_id, message_digest,
       failure_code, original_schema_version, trace_id, received_at
FROM message_quarantine
WHERE stream_name = :stream_name
  AND stream_entry_id = :stream_entry_id;
```

`failure_detail`이나 원문 payload를 운영 기록으로 사용하지 않는다. 연결된 DLQ Outbox도
함께 확인한다.

```sql
SELECT event_id, quarantine_id, status, attempt_count,
       available_at, claim_expires_at, last_error_code, published_at
FROM dlq_outbox_event
WHERE quarantine_id = :quarantine_id;
```

- quarantine row가 없으면 원본을 ACK하지 않고 Worker 오류 로그와 DB 연결을 확인한다.
- Outbox가 `PENDING`이면 다음 Publisher 주기를 기다린다.
- `CLAIMED` 상태가 claim TTL을 넘기면 Scheduler가 재선점하는지 확인한다.
- `PUBLISHED`이면 dead-letter Stream의 동일 `event_id`를 확인한다.

## 4. DLQ 발행 실패 대응

DLQ Publisher는 실패한 동일 event를 지수 backoff로 재예약한다. 반복 실패 시
`worker_dlq_publish_alert`와 다음 설정을 확인한다.

- `REDIS_DLQ_STREAM_NAME`
- `DLQ_OUTBOX_CLAIM_TTL_SECONDS`
- `DLQ_PUBLISHER_INTERVAL_SECONDS`
- Redis 연결·인증·용량 상태

Redis 복구 후 Scheduler가 기존 `event_id`를 다시 발행하도록 둔다. 새 DLQ Outbox row를
수동 삽입하거나 기존 claim token을 수정하지 않는다.

## 5. 승인된 재처리

1. quarantine의 `failure_code`, digest, trace metadata로 원인을 확인한다.
2. schema 또는 배포 문제가 해결됐는지 확인한다.
3. 원래 도메인 입력이 아직 유효하고 재처리 승인이 있는지 확인한다.
4. 승인된 Job 접수 경로로 새 Job과 Outbox를 생성한다.
5. 새 `job_id`, `event_id`, 배포 SHA를 운영 기록에 남긴다.
6. 새 Job 완료와 기존 quarantine/DLQ 보존 상태를 확인한다.

현재 별도 수동 재처리 UI는 제공하지 않는다. Redis 원문을 복사해 `XADD`하는 방식은
스키마·멱등성·개인정보 경계를 우회하므로 금지한다.

## 6. 종료 조건과 증빙

- Pending entry가 정상 완료 또는 durable quarantine 이후 ACK됐다.
- 최신 lease와 fencing token을 가진 실행만 결과를 commit했다.
- retry Outbox와 DLQ Outbox가 중복 생성되지 않았다.
- quarantine과 DLQ에 원본 메시지·의료정보·secret이 없다.
- 관련 `job_id`, `event_id`, `stream_entry_id`, 배포 SHA, 발생·복구 시각을 기록했다.

장애가 계속되면 Worker를 무리하게 재시작하지 말고 Redis·DB 상태와 미완료 claim을
보존한 채 담당자에게 escalation한다.
