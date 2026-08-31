# Track A migration·rollback 계획 제안 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Proposed — 승인 전 제안 |
| 구현 상태 | Not implemented in `develop` |
| 관련 Issue | #75 |
| 선행 제안 | `profile-self-ownership-v1.md` |
| 적용 범위 | Post-MVP-1 Track A, Backend/API, Database, AI Worker 공통, OCR·Guide·Chat 연결 경계 |
| 작성일 | 2026-08-31 |

이 문서는 Post-MVP-1 Track A의 `AI_JOB`, Outbox, Idempotency, Prescription Version을 실제 migration PR로 나누기 전에 대상 테이블, 적용 순서, rollback 경계, PR 분리 기준을 정리하는 제안이다.

현재 `develop`에는 이 문서의 신규 테이블과 전환 로직이 구현되어 있지 않다. 승인 전까지 이 문서는 Current 계약 또는 구현 완료로 해석하지 않는다.

## 1. 목적

Track A는 OCR·Guide·Chat이 같은 비동기 작업 기준을 사용하도록 공통 실행 기반을 고정한다.

현재 기능들은 도메인별로 동기 처리와 상태 저장 방식이 다르다. Post-MVP-1에서 OCR·Guide·Chat을 비동기로 전환하면 Frontend polling, Worker 재시도, 실패 저장, 처방 version 변경에 따른 STALE 처리까지 같은 기준으로 움직여야 한다. 그래서 구현 전에 DB migration 순서와 rollback 경계를 먼저 고정한다.

이 문서의 목적은 다음과 같다.

- 승인된 Post-MVP-1 목표 계약을 현재 Backend DB 구조에 적용할 순서를 정한다.
- `Expand → Dual-write → Backfill → Dual compatibility → Verify → Read cutover → Contract` 단계를 migration PR 기준으로 나눈다.
- rollback 가능한 지점과 forward-fix가 필요한 지점을 구분한다.
- feature flag 기반 신규 접수 rollback과 기존 Job drain 기준을 구분한다.
- PROFILE 전환, `AI_JOB`, Outbox, Idempotency, Prescription Version, OCR 기존 행 mapping의 선후 관계를 명확히 한다.
- Track A가 다른 트랙의 개발 착수 전체를 막지 않도록 병렬 개발 가능한 경계를 정한다.

## 2. 기준 문서

| 문서 | 기준 |
| --- | --- |
| `docs/contracts/targets/post-mvp-1/async-job-v1.md` | Job 6상태, 상태 조회 응답, lease, retry, 오류 의미 |
| `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md` | Outbox, Redis Stream envelope, ACK, quarantine, DLQ |
| `docs/contracts/targets/post-mvp-1/idempotency-v1.md` | 비동기·동기 멱등성 scope, request hash, 저장 필드 |
| `docs/contracts/targets/post-mvp-1/prescription-version-v1.md` | Prescription Version 불변 snapshot, 활성화, backfill, rollback |
| `docs/contracts/proposed/profile-self-ownership-v1.md` | PROFILE SELF와 `profile_id` 소유권 선행 전환 제안 |
| `docs/governance/post-mvp-1-document-authority.md` | 계약 상태와 승인 원본 우선순위 |

위 문서와 이 제안이 다르면 구현 착수 전에 차이를 먼저 정리한다. 값을 추정하거나 서로 다른 문서의 기준을 섞어 구현하지 않는다.

기준 문서를 먼저 고정하는 이유는 Track A가 여러 영역을 가로지르기 때문이다. DB schema, Backend API, Worker, Frontend polling이 서로 다른 문서를 기준으로 구현하면 PR은 각각 통과해도 통합 단계에서 Job 상태나 응답 형식이 맞지 않을 수 있다.

## 3. Blocking 선행 조건

다음 항목은 Track A PR 1, 즉 Expand migration을 시작하기 전에 승인되어야 한다.

| 선행 조건 | 이유 | 완료 기준 |
| --- | --- | --- |
| PROFILE SELF 소유권 전환 기준 승인 | 현재 Current 계약은 `user_id` 기반이고 목표 ERD는 `profile_id` 기반이다. 소유권 기준이 흔들리면 Job·결과·처방 version 권한 검사가 반복 수정된다. | `profile` 테이블, SELF unique, 기존 사용자 신규 write 시 SELF profile 멱등 생성, 신규 리소스 dual-write, 기존 리소스 `profile_id` backfill, 도메인별 `profile_id` composite FK·일관성 검증, OCR 소유권 chain 기준 승인 |
| `AI_JOB.domain_type/domain_id` 물리 저장 여부 확정 | 목표 계약은 응답 구성값으로 설명하지만 ERD와 구현안이 물리 컬럼으로 해석될 수 있다. | 최종 DDL에서 물리 컬럼으로 둘지, 도메인 row의 `ai_job_id` FK에서 응답을 구성할지 결정 |
| Idempotency 단일 테이블 구조 정렬 | #99 이후 목표 계약은 단일 `idempotency_record`와 `record_type=ASYNC_JOB|SYNC_MUTATION`을 사용한다. 별도 `sync_idempotency_record`를 만들면 최신 계약과 충돌한다. | ERD·계약·migration 계획에서 단일 테이블, `record_type`, 타입별 CHECK 제약으로 정렬 |
| `MESSAGE_QUARANTINE`, `DLQ_OUTBOX_EVENT` Expand 포함 확정 | poison message를 durable하게 기록한 뒤 ACK하려면 quarantine과 DLQ Outbox가 필요하다. | PR 1 범위와 계약 테스트에 두 테이블 포함 |
| Backfill과 rollback 용어 분리 | 재실행 가능한 batch는 rollback이 아니라 resume/recovery다. | 코드 rollback, forward-fix, batch 재개 기준을 문서에 구분 |

위 항목을 blocking으로 둔 이유는 PR 1의 DDL이 한번 병합되면 이후 PR들이 그 구조를 전제로 개발되기 때문이다. `profile_id`, `domain_type/domain_id`, 멱등성 저장 구조, quarantine/DLQ 범위가 나중에 바뀌면 migration뿐 아니라 API 응답, Worker 메시지, 테스트 fixture까지 다시 바뀐다.

## 4. 대상 테이블

### 4.1 신규 테이블

| 영역 | 테이블 | 목적 |
| --- | --- | --- |
| 공통 Job | `ai_job` | OCR·Guide·Chat 실행 상태와 lease·retry·failure metadata 저장 |
| Outbox | `outbox_event` | DB commit 이후 Worker 실행 이벤트 발행 |
| Quarantine | `message_quarantine` | 필수 필드 오류, 미지원 schema, event mismatch 등 poison message 격리 기록 |
| DLQ Outbox | `dlq_outbox_event` | quarantine 기록을 dead-letter Stream으로 발행하기 위한 별도 Outbox |
| 멱등성 | `idempotency_record` | 비동기 Job 접수와 Track B·C·F 동기 상태 변경을 `record_type=ASYNC_JOB|SYNC_MUTATION`으로 구분 |
| 처방 버전 | `prescription_version` | 확정 처방의 불변 snapshot |
| 버전별 약물 snapshot | `prescription_version_medication` | version에 귀속된 확정 약물 snapshot |

### 4.2 기존 테이블 변경

| 테이블 | 변경 | 기준 |
| --- | --- | --- |
| `prescription` | `active_version_id` nullable FK 추가 후 backfill·검증 뒤 NOT NULL | 활성 version pointer |
| `ocr_job` | `ai_job_id` 적용 방식 별도 확정 | 목표 ERD상 NOT NULL이면 기존 OCR 행 mapping·검증 기준 필요 |
| `guide` | 신규 비동기 생성부터 nullable `ai_job_id` 연결 | 기존 Guide에는 synthetic Job 생성 금지 |
| `chat_message` | 신규 ASSISTANT 비동기 메시지부터 nullable `ai_job_id` 연결 | 기존 메시지에는 synthetic Job 생성 금지 |

PR 0의 PROFILE backfill, 일관성 검증, read cutover 배포가 완료되면 기존 리소스 소유권은 `profile_id` 기준으로 정렬한다. OCR은 `ocr_job.profile_id`를 직접 만들지 않고 `ocr_job → medical_document → profile_id` chain으로 확인한다.

대상 테이블을 이 범위로 나누는 이유는 실제 상태의 기준 원본을 분리하기 위해서다. `ai_job`은 실행 상태만, 도메인 row는 결과와 placeholder만, Outbox는 실행 요청 전달만 담당해야 재시도·중복 전달·rollback 상황에서 어느 row를 기준으로 판단할지 명확해진다.

## 5. `AI_JOB`과 도메인 row 관계

`AI_JOB`은 공통 실행 상태만 관리한다. OCR·Guide·Chat의 결과와 placeholder는 각 도메인 row가 관리한다.

| Job 유형 | 도메인 row | 연결 기준 |
| --- | --- | --- |
| `OCR` | `ocr_job` | `ocr_job.ai_job_id` |
| `GUIDE` | `guide` | `guide.ai_job_id` |
| `CHAT` | ASSISTANT `chat_message` | `chat_message.ai_job_id` |

`domain_type`과 `domain_id`는 외부 `JobStatusResponse`를 만들 때 구성하는 값으로 둔다.

```json
{
  "domain_type": "OCR_JOB | GUIDE | CHAT_MESSAGE",
  "domain_id": "uuid"
}
```

최종 DDL에서 `AI_JOB.domain_type/domain_id`를 물리 컬럼으로 둘지 여부는 PR 1 전에 확정한다. 이 결정이 끝나기 전에는 `ai_job` DDL을 병합하지 않는다.

이 결정을 미루지 않는 이유는 두 방식의 제약과 조회 방식이 다르기 때문이다. 물리 컬럼으로 저장하면 Job row만으로 응답을 만들 수 있지만 도메인 row와 값 불일치를 막아야 한다. 도메인 row의 `ai_job_id` FK에서 구성하면 중복 저장은 줄지만 조회 시 도메인별 join 또는 lookup이 필요하다.

## 6. Job 조회와 결과 조회 소유권 기준

`GET /api/v1/jobs/{job_id}`와 `result_url`이 가리키는 도메인 결과 조회는 같은 소유권 기준을 사용한다. Job은 존재하지만 인증 사용자의 리소스가 아니거나, Job과 도메인 결과의 소유권이 서로 맞지 않으면 fail-closed로 `404`를 반환한다.

| 단계 | Job 조회 기준 | 결과 조회 기준 |
| --- | --- | --- |
| PROFILE 전환 승인 전 | 기존 target 계약의 `user_id` 기준을 유지한다. | 결과 도메인 row도 기존 `user_id` 또는 parent chain 기준을 유지한다. |
| PR 0 backfill·검증·read cutover 배포 완료 후 | `ai_job.profile_id` 직접 저장 여부를 PR 1 전에 확정한다. 직접 저장하지 않으면 도메인 row의 `ai_job_id`를 역조회해 SELF `profile_id`를 확인한다. | OCR은 `ocr_job → medical_document → profile_id`, Guide는 `guide.profile_id`, Chat은 `chat_message → chat_session.profile_id` 기준으로 확인한다. |

`ai_job.profile_id`를 직접 저장할지 여부는 `AI_JOB.domain_type/domain_id` 물리 저장 여부와 함께 PR 1의 blocking 결정으로 둔다. 직접 저장하면 Job 단독 조회가 단순하지만 도메인 row와 `profile_id` 불일치를 막아야 한다. 직접 저장하지 않으면 중복 저장은 줄지만 Job 조회 시 도메인별 역조회가 필요하다.

구현 PR에서는 다음 테스트를 포함한다.

- 본인 Job 상태 조회 성공
- 타 사용자 Job 상태 조회 `404`
- 본인 Job이지만 도메인 결과의 `profile_id`가 다른 경우 `404`
- `result_url` 결과 조회 시 Job 조회와 같은 소유권 기준 적용
- `domain_id`를 클라이언트가 임의 조합해도 타 사용자 결과를 조회할 수 없음

## 7. Idempotency 구조

Track A는 비동기 Job 접수와 동기 상태 변경을 단일 `idempotency_record` 테이블에 저장하고 `record_type`으로 구분한다.

| 구분 | `record_type` | 저장 방식 |
| --- | --- | --- |
| 비동기 OCR·Guide·Chat 접수 | `ASYNC_JOB` | `user_id`, `operation_id`, `key_hmac`, `request_hash`, `job_id`, `created_at`, `expires_at`을 저장한다. 응답 body snapshot은 저장하지 않는다. |
| Track B·C·F 동기 상태 변경 | `SYNC_MUTATION` | `user_id`, `operation_id`, `parent_resource_id`, `key_hmac`, `request_hash`, 최초 2xx status와 암호화된 `response_body_snapshot`, `created_at`, `expires_at`을 저장한다. |

같은 비동기 key와 같은 request hash가 다시 들어오면 새 Job, 새 도메인 placeholder, 새 Outbox, 새 Provider 호출을 만들지 않고 저장된 `job_id`로 최신 `202 Accepted` 상태 응답을 반환한다.

같은 key지만 request hash가 다르면 `409 IDEMPOTENCY_KEY_CONFLICT`를 반환한다.

비동기와 동기 멱등성을 `record_type`으로 구분하는 이유는 재응답 방식이 다르기 때문이다. 비동기 Job은 시간이 지나며 상태가 바뀌므로 최초 응답 body snapshot을 그대로 저장하지 않고 `job_id`로 최신 상태를 다시 만든다. 반면 동기 상태 변경은 이미 완료된 2xx mutation의 결과를 같은 body로 재현해야 하므로 snapshot 저장이 필요하다.

단일 테이블을 쓰는 이유는 #99 이후 최신 목표 계약이 `idempotency_record` 하나를 정본으로 삼기 때문이다. `ASYNC_JOB`은 `job_id`가 non-null이고 snapshot 관련 필드는 null이어야 하며, `SYNC_MUTATION`은 `parent_resource_id`, `response_status`, `response_body_snapshot`이 non-null이고 `job_id`는 null이어야 한다. 이 타입별 nullability는 DB CHECK 제약으로 강제한다.

PR 0의 PROFILE backfill, 일관성 검증, read cutover 배포 완료 후 멱등성 scope까지 `profile_id`로 바꿀지는 PR 1 전에 별도 확정한다. PR 0 완료 전에는 target 계약의 `user_id` scope를 임의로 바꾸지 않는다.

## 8. Migration 단계

| 단계 | 내용 | rollback·복구 기준 |
| --- | --- | --- |
| 1. PROFILE 선행 | 본인 단일 SELF profile 도입, 기존 사용자 신규 write 시 SELF profile 멱등 생성, 신규 리소스 dual-write, 기존 리소스 `profile_id` backfill, composite FK 기반 소유권 일관성 검증, 소유권 조회 전환 | Contract 전에는 기존 read 경로 rollback 가능. Contract 후에는 forward-fix 우선 |
| 2. Expand | `ai_job`, `outbox_event`, `message_quarantine`, `dlq_outbox_event`, 단일 `idempotency_record`, `prescription_version`, `prescription_version_medication` 생성. 기존 테이블에 nullable FK 추가 | 신규 경로 미사용 시 nullable 컬럼·신규 테이블 제거 가능 |
| 3. Dual-write | 신규 처방 write 시 기존 read 호환 필드와 version snapshot을 함께 기록. 신규 비동기 접수는 feature flag가 켜진 경로에서만 생성 | flag off 시 신규 접수는 legacy 경로로 되돌리고, 이미 생성된 Job은 drain |
| 4. Backfill | 기존 처방마다 version 1 생성, 약물 snapshot 복사, `active_version_id` 채움 | 재실행 가능한 batch는 rollback이 아니라 resume/recovery로 처리 |
| 5. Dual compatibility | version FK가 있으면 신경로, 없으면 기존 경로 fallback. 신규 write는 version snapshot과 기존 read 호환 필드 유지 | 애플리케이션 코드 rollback 가능 |
| 6. Verify | orphan, 중복 version, 무효 active pointer, snapshot 수와 핵심 값 불일치 검증 | 실패 시 cutover 금지. backfill 재개 또는 forward-fix |
| 7. Read cutover | 조회와 하위 결과 생성을 `prescription_version` 기준으로 전환 | 한 배포 구간 관찰. 문제 시 contract 전 코드 rollback |
| 8. Contract | 임시 nullable 제거, NOT NULL·FK·unique·CHECK 최종화 | 이후 destructive downgrade 금지, forward-fix 원칙 |

이 순서를 쓰는 이유는 기존 사용자 데이터와 운영 중인 API를 동시에 보호하기 위해서다. 먼저 새 구조를 옆에 추가하고, 기존 데이터를 채우고, 양쪽 구조를 함께 읽을 수 있게 만든 뒤, 검증이 끝난 다음에만 최종 제약을 잠근다.

## 9. Backfill, rollback, resume/recovery 구분

Backfill batch는 같은 입력에 대해 여러 번 실행되어도 중복 row를 만들지 않아야 한다. 이는 rollback이 아니라 부분 실패 후 이어서 처리하는 resume/recovery 기준이다.

| 상황 | 처리 |
| --- | --- |
| Backfill 중 일부 batch 실패 | 완료된 row를 삭제하지 않고 미완료 범위를 식별해 재개 |
| 코드 rollback 필요 | Contract 전에는 legacy read 경로로 복귀. 생성된 version row는 보존 |
| Contract 후 문제 발견 | destructive downgrade 대신 후속 migration 또는 데이터 보정으로 forward-fix |
| version row 생성 후 | 처방 이력으로 보고 임의 삭제하지 않음 |

검증 쿼리는 migration PR의 필수 산출물이다.

검증 쿼리를 필수로 두는 이유는 backfill 성공을 코드 리뷰만으로 판단할 수 없기 때문이다. row 수, orphan, 중복 version, snapshot 누락을 실제 SQL로 확인해야 Contract 단계에서 NOT NULL과 FK를 안전하게 적용할 수 있다.

- 기존 `prescription` 수와 `prescription_version` version 1 수 일치
- `prescription.active_version_id` orphan 0건
- `(prescription_id, version_number)` 중복 0건
- version medication snapshot 수와 기존 `medication` 수 대조
- 하위 결과의 `prescription_version_id` 누락 여부 확인

## 10. Feature flag와 기존 Job drain

비동기 전환 rollback은 신규 접수와 이미 생성된 Job을 분리해서 처리한다.

| 구분 | 기준 |
| --- | --- |
| `ASYNC_OCR` | OCR 신규 접수를 공통 Job·Outbox 경로로 보낼지 결정 |
| `ASYNC_GUIDE` | Guide 신규 접수를 공통 Job·Outbox 경로로 보낼지 결정 |
| `ASYNC_CHAT` | Chat 신규 접수를 공통 Job·Outbox 경로로 보낼지 결정 |

feature flag는 신규 접수 경로에만 적용한다. flag를 끄면 이후 들어오는 신규 요청은 legacy 경로로 보내지만, 이미 생성된 `AI_JOB`과 `outbox_event`는 생성 당시 경로로 완료하거나 실패 상태로 종결한다.

이미 생성된 Job을 legacy 동기 경로로 재라우팅하지 않는 이유는 같은 요청이 두 실행 경로에서 중복 처리될 수 있기 때문이다. Job과 Outbox가 만들어진 뒤에는 해당 Job의 lifecycle을 끝까지 drain해야 `ACK`, retry, failure 기록, idempotency 상태가 서로 어긋나지 않는다.

mixed-version 배포 중에는 다음 기준을 따른다.

- 신규 접수는 각 feature flag 값에 따라 async 또는 legacy 경로 중 하나만 탄다.
- 기존 non-terminal Job은 flag 변경과 무관하게 기존 Job lifecycle에서 `COMPLETED`, `FAILED`, `STALE` 중 하나로 종결한다.
- 같은 `Idempotency-Key` 재요청은 flag 현재값이 아니라 기존 `idempotency_record.job_id`를 기준으로 같은 Job의 최신 상태를 반환한다.
- rollback 중에도 새 Job, 새 Outbox, 새 Provider 호출이 중복 생성되면 안 된다.
- drain 완료 전에는 관련 nullable FK를 제거하거나 `AI_JOB`·Outbox 테이블을 drop하지 않는다.

drain 완료 조건은 아래 항목을 모두 충족해야 한다.

- rollback 대상 window 안의 모든 non-terminal Job이 `COMPLETED`, `FAILED`, `STALE` 중 하나가 된다.
- `outbox_event`에 발행 대기 또는 처리 중인 이벤트가 남아 있지 않다.
- Redis pending message가 없거나, 남은 pending message가 quarantine/DLQ 기준으로 durable 기록된 뒤 ACK된다.
- 같은 요청을 재시도해도 기존 Job 상태 조회 또는 legacy 접수 중 하나로만 처리된다.

PR 단계에서는 feature flag 기본값, flag off 시 접수 경로, drain 검증 SQL 또는 운영 확인 방법을 함께 제출한다.

## 11. 도메인별 기준

### 11.1 OCR

- OCR은 Track A 적용 대상이다.
- OCR 소유권은 PROFILE 전환 후 `ocr_job → medical_document → profile_id` chain으로 확인한다.
- `ocr_job.profile_id` 직접 컬럼은 만들지 않는다.
- 목표 ERD에서 `ocr_job.ai_job_id NOT NULL`을 유지하려면 기존 OCR 행을 어떤 Job으로 mapping할지 별도 기준이 필요하다.
- 기존 OCR 실행 사실을 검증 없이 일괄 synthetic `AI_JOB(COMPLETED)`로 만들지 않는다.
- OCR `ai_job_id` mapping은 OCR 담당자와 별도 이슈·PR에서 확정한다.

OCR을 별도 mapping으로 분리하는 이유는 기존 OCR 행의 실행 사실과 결과 상태를 공통 `AI_JOB`으로 되살리는 기준이 필요하기 때문이다. 근거 없이 기존 행마다 synthetic completed Job을 만들면 실제 실행 시각, attempt, provider 실패 여부가 왜곡될 수 있다.

### 11.2 Guide

- 기존 Guide 행에는 synthetic Job을 만들지 않는다.
- 기존 행의 `ai_job_id`는 nullable을 허용한다.
- 신규 비동기 Guide 생성부터 `AI_JOB`과 연결한다.
- Guide 상태 enum과 의미는 기존 승인된 목표 계약을 따른다.

Guide는 기존 행을 사용자에게 보여줄 수 있는 결과 데이터로 보존해야 하므로, 과거 행을 공통 Job으로 억지 연결하지 않는다. 신규 비동기 생성부터만 `AI_JOB`을 연결하면 기존 데이터의 의미를 바꾸지 않고 전환할 수 있다.

### 11.3 Chat

- 기존 Chat message에는 synthetic Job을 만들지 않는다.
- Job으로 생성되는 신규 ASSISTANT 메시지만 `ai_job_id`를 연결한다.
- 기존/migration 메시지는 nullable을 허용한다.
- 같은 Chat session에는 non-terminal Job을 하나만 허용한다.
- Track F의 RAG·Citation·Safety·OTC 세부 구현은 별도 계약을 따른다.

Chat도 같은 이유로 기존 메시지를 synthetic Job에 연결하지 않는다. 특히 Chat은 사용자 메시지와 ASSISTANT 메시지가 대화 이력으로 남기 때문에, 과거 메시지의 생성 상태를 새 Job lifecycle로 재해석하면 화면 복구와 감사 의미가 달라질 수 있다.

## 12. Outbox, quarantine, DLQ

PR 1 Expand에는 정상 실행 Outbox뿐 아니라 poison message 격리용 테이블도 포함한다.

| 테이블 | 필요 이유 |
| --- | --- |
| `outbox_event` | Job 실행 요청을 DB commit 이후 Redis Stream으로 발행 |
| `message_quarantine` | 필수 필드 오류, 지원하지 않는 schema, 파싱 불가 메시지를 원문 없이 durable 기록 |
| `dlq_outbox_event` | quarantine 기록을 `oryak:jobs:dead-letter`로 발행 |

Worker는 Provider 호출 전에 message schema, `event_id`, `job_id`, attempt, `expected_event_id`, lease를 검증한다. poison message는 Provider를 호출하지 않고 `MESSAGE_QUARANTINE`과 `DLQ_OUTBOX_EVENT`를 같은 DB transaction에서 commit한 뒤 ACK한다.

Stream envelope, DLQ envelope, ACK 순서는 `outbox-stream-v1.md`를 따른다. 의료 원문, 질문, 답변, Provider 원문 오류, 원문 Idempotency Key는 Outbox·Stream·quarantine·DLQ에 저장하지 않는다.

quarantine과 DLQ를 Expand에 포함하는 이유는 Worker가 처리할 수 없는 메시지를 만났을 때도 ACK 전에 장애 사실을 DB에 남겨야 하기 때문이다. 이 테이블이 없으면 poison message를 계속 재전달받거나, 반대로 원인 기록 없이 ACK해서 장애 추적이 어려워진다.

## 13. PR 분리안

최종 담당은 송은영이며, 각 PR에서 영향받는 영역은 담당 리뷰어에게 확인한다.

| PR | 범위 | 선행 조건 | 확인 필요 |
| --- | --- | --- | --- |
| PR 0 | PROFILE SELF 소유권 전환 | `profile-self-ownership-v1.md` 승인 | ERD·계약·Backend 소유권 |
| PR 1 | Expand: Track A 신규 테이블과 nullable FK 추가 | PR 0 backfill·검증·read cutover 배포 완료, blocking 선행 조건 해소 | DB, Worker, 계약 |
| PR 2 | 신규 write dual-write와 async feature flag 기본값 적용 | PR 1 | Backend/API, rollback 경계 |
| PR 3 | Prescription Version backfill·검증 SQL·테스트 | PR 2 | DB, 계약 |
| PR 4 | 공통 Job 접수·상태 조회 API와 Outbox 연결 | PR 1, PR 2 | Backend/API, Frontend polling, Worker |
| PR 5 | 비동기·동기 Idempotency 로직 적용 | PR 1, PR 4 | Backend/API, Frontend 중복 요청 |
| PR 6 | Prescription Version 활성화 transaction과 기존 Job `STALE` 처리 | PR 3, PR 4 | Backend, OCR, Guide/Chat |
| PR 7 | OCR `ai_job_id` mapping·검증 기준 및 적용 | OCR mapping 기준 승인 | OCR, Worker, DB |
| PR 8 | Guide·Chat 비동기 연결 | PR 4 이후 | Guide/Chat, RAG/Safety, Frontend |
| PR 9 | Read cutover + Contract | 선행 PR 안정화, 기존 Job drain 완료 | 전원 담당 영역 최종 확인 |

PR을 나누는 이유는 Track A가 한 번에 병합하기에는 영향 범위가 크기 때문이다. schema 추가, backfill, API 연결, Worker 실행, idempotency, STALE 처리는 실패 양상이 서로 달라서 작은 PR로 나누어야 각 단계에서 rollback 가능성을 유지할 수 있다.

## 14. 병렬 착수 기준

Track A는 통합 게이트이며 모든 트랙의 개발 착수 게이트가 아니다.

- PR 1~4에서 공통 Job 접수·상태 조회·Outbox 인터페이스가 고정되면 Track B·C·E·F는 mock/fake adapter로 병렬 개발할 수 있다.
- 실제 Worker·Handler 통합 merge는 Track A 계약 테스트 통과 후 수행한다.
- 미구현 Provider가 있어도 OCR 얇은 one-cycle 또는 fake adapter로 `접수 → Job/Outbox → Stream → Worker → 결과 commit → ACK → 상태 조회` 흐름을 검증할 수 있어야 한다.

병렬 착수를 허용하는 이유는 Track A가 모든 기능 개발을 직렬로 막는 병목이 되지 않게 하기 위해서다. 다만 공통 계약을 벗어난 임시 상태값이나 응답 형식을 만들면 통합 비용이 커지므로, mock/fake adapter도 같은 Job·Outbox·상태 조회 계약을 따라야 한다.

## 15. 완료 조건

- PROFILE 선행 전환 문서가 연결되어 있다.
- Track A PR 1 전에 필요한 blocking 조건이 문서화되어 있다.
- 대상 테이블과 기존 테이블 변경 범위가 정리되어 있다.
- PROFILE SELF profile 멱등 생성, dual-write, 도메인별 `profile_id` composite FK·일관성 검증 기준이 선행 조건으로 연결되어 있다.
- Job 조회와 `result_url` 결과 조회가 같은 소유권 기준을 사용하고, 소유권 불일치 시 fail-closed `404`를 반환하도록 명시되어 있다.
- `domain_type/domain_id` 물리 컬럼 여부가 PR 1 차단 조건으로 명시되어 있다.
- 단일 `idempotency_record`와 `record_type=ASYNC_JOB|SYNC_MUTATION` 기준이 최신 계약과 일치한다.
- `MESSAGE_QUARANTINE`, `DLQ_OUTBOX_EVENT`가 Expand 범위에 포함되어 있다.
- async feature flag와 기존 Job drain 기준이 rollback 절차에 포함되어 있고, drain 완료 조건은 모두 충족해야 한다고 명시되어 있다.
- Backfill과 rollback/resume/recovery 의미가 구분되어 있다.
- OCR·Guide·Chat 기존 행의 synthetic Job 처리 기준이 분리되어 있다.
- OCR `ai_job_id` mapping이 별도 확정 대상임을 명시한다.
- 후속 PR 순서와 리뷰 경계가 정리되어 있다.

## 16. 참고 자료

- `docs/contracts/proposed/profile-self-ownership-v1.md`
- `docs/contracts/targets/post-mvp-1/async-job-v1.md`
- `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md`
- `docs/contracts/targets/post-mvp-1/idempotency-v1.md`
- `docs/contracts/targets/post-mvp-1/prescription-version-v1.md`
- `docs/governance/post-mvp-1-document-authority.md`
