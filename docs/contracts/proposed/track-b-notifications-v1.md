# Track B Notification 계약 v1 — 제안

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Proposed · 승인 전 구현 금지 |
| 구현 상태 | Not implemented |
| Decision | [PD-203](../../governance/decisions/2026-09-10-track-b-notifications.md) |
| 구현 담당 | 권가빈 (`hazelnutflavoured`) |
| 기술 리뷰 | 송은영 (`phina-io`) — Backend·DB·Security |
| 소비 계약 리뷰 | 남한솔 (`solia142`) — Frontend |

## 기존 기준과 이번 제안

[Check-in 목표 계약](../targets/post-mvp-1/checkin-v1.md), [처방 버전 목표 계약](../targets/post-mvp-1/prescription-version-v1.md), [멱등성 목표 계약](../targets/post-mvp-1/idempotency-v1.md)을 따른다. 아래 enum·추가 필드·상세 API·조건은 이번 제안이며 Approved v4나 현재 API로 해석하지 않는다.

외부 Push·SMS·Email·Calendar, 사용자 알림 ON/OFF 정책, Track C Safety·Barrier 구현은 제외한다. 알림은 사용자가 확정한 occurrence에서만 만들며 복용 여부를 추정하지 않는다. 원문 처방, 약명이나 의료 안내 문구를 별도 알림 본문으로 복제하지 않는다.

## 저장 구조 제안

`notification_record`는 다음 컬럼을 가진다. 시각은 timezone-aware UTC, 식별자는 UUID다.

| 필드 | 제안 타입·조건 |
| --- | --- |
| `id` | UUID PK |
| `occurrence_id` | non-null FK → medication_occurrence.id |
| `kind` | non-null `SCHEDULED` 또는 `REMINDER` |
| `scheduled_at` | non-null timestamptz, 앱 내부 게시 예정 시각 |
| `status` | non-null `PENDING`, `DELIVERED`, `CANCELLED` |
| `attempt` | non-null integer, 0 또는 1; 앱 내부 게시 전 0, 게시 성공 1 |
| `cancelled_at` | nullable timestamptz |
| `delivered_at` | nullable timestamptz, 이번 제안에서 추가 |
| `read_at` | nullable timestamptz, 이번 제안에서 추가 |
| `created_at` | non-null timestamptz, 이번 제안에서 추가 |

`(occurrence_id, kind)` unique로 최초 알림 1개·사용자 요청 재알림 1개를 보장한다. 취소·읽음·멱등 레코드 만료 후에도 동일 occurrence의 재알림을 추가 생성하지 않는다. 원본의 ‘1회’를 lifetime 1회로 해석하는 것이 적절한지 제품·기술 리뷰에서 확인한다.

DB check는 `PENDING`에서 attempt=0이고 cancelled_at·delivered_at·read_at이 null, `DELIVERED`에서 attempt=1이고 delivered_at이 non-null·cancelled_at이 null, `CANCELLED`에서 attempt=0이고 cancelled_at이 non-null·delivered_at·read_at이 null임을 보장한다. read_at은 delivered_at 이상이다. 취소·전달 이력은 물리 삭제하지 않으며 계정 삭제는 기존 parent chain 보존·삭제 정책과 FK 정합성을 검증한다.

## 생성·게시·취소

- `PENDING` occurrence마다 `SCHEDULED`를 기존 `scheduled_at`에 맞춰 생성한다. 반복 실행·경합 시 unique constraint로 하나만 저장한다. 과거 이력 전체를 backfill하지 않고 생성 시점이 확인 기한 전인 대상만 취급한다.
- 앱 내부 게시 명령은 알림 예정 시각이 도래하고 occurrence가 `PENDING`이며 `now < confirmation_deadline_at`인 대상만 `DELIVERED`로 전환한다. 목록 조회 자체는 게시하지 않는다. 게시 명령 재시작은 이미 전달된 row를 다시 변경하지 않는다.
- occurrence가 `CANCELLED` 또는 `CLOSED`이거나 확인 기한에 도달했다면 미전달 알림은 `CANCELLED`로 처리한다. `TAKEN`·`NOT_TAKEN`·`UNCONFIRMED`를 읽음·전달 상태에서 생성하지 않는다.
- 처방 version 변경과 일정 수정·취소의 미전달 알림 취소는 승인 원본대로 occurrence 변경과 같은 transaction에서 처리한다. `DELIVERED` 이력과 이미 저장한 read_at은 유지한다.
- Check-in 완료 후 미전달 알림은 게시 명령에서 상태 재검증으로 취소한다. 게시와 Check-in 생성은 occurrence row 잠금으로 직렬화한다. 이미 전달된 알림은 복약 결과를 나타내지 않는 이력으로 남는다. 별도 Check-in 상태·revision을 알림에 snapshot하지 않는다.
- 기존 일정·처방 쓰기 잠금 순서를 유지하고 Notification 처리에서 occurrence나 prescription을 역순으로 잠그지 않는다. 취소 port는 caller의 AsyncSession을 사용하며 commit하지 않는다. 실패는 상위 transaction rollback으로 전파한다.

## 소유권과 공통 API

인증 사용자 → SELF profile → prescription → version → schedule → occurrence → notification의 소유권을 검증한다. 존재하지 않거나 타인 소유인 ID는 동일한 공통 404이며 원문·식별자 상세를 오류에 포함하지 않는다. 모든 응답은 `Cache-Control: no-store`, 성공은 `data` envelope, 오류는 기존 `{code, message, details, trace_id}`를 따른다.

### 목록 — 신규 경로 제안

`GET /api/v1/notifications?limit=20&offset=0`

`limit`은 1~100, `offset`은 0 이상이다. 자신의 `DELIVERED` 알림만 `scheduled_at DESC, id DESC`로 반환한다. 응답은 `200 {data: {items: NotificationResponse[], next_offset: integer|null}}`다. 변경 중인 목록에서 offset pagination은 snapshot 일관성을 보장하지 않는다. 새로고침은 offset=0부터 수행한다.

`NotificationResponse`의 필수 필드는 `id`, `occurrence_id`, `kind`, `scheduled_at`, `status`, `delivered_at`, `read_at`이며 read_at은 nullable이다. API 시각은 UTC RFC3339, status는 목록·읽음 응답에서 `DELIVERED`다. 약 상세와 현재 복약 결과는 #202 occurrence 계약을 소비한다.

### 읽음 — 신규 경로 제안

`PATCH /api/v1/notifications/{notification_id}/read`, body `{}`.

처음 요청에 서버 시각을 read_at에 저장하고 이미 읽은 경우 최초 read_at을 유지한다. 자기 소유 `DELIVERED`만 허용하며 그 외에는 공통 404다. 성공은 `200 {data: NotificationResponse}`다. `Idempotency-Key`를 요구하고 동기 snapshot을 저장한다. parent resource를 notification_id로 추가하는 것은 [동기 멱등 계약](../targets/post-mvp-1/idempotency-v1.md)에 반영할 제안이다.

### 재알림 — 기존 경로의 상세 DTO 제안

`POST /api/v1/medication-occurrences/{occurrence_id}/reminders`

body는 `{scheduled_at: UTC RFC3339 timestamp}`이며 필수다. 서버는 시각을 추정하거나 복용 시각을 변경하지 않는다. 신규 요청은 occurrence가 `PENDING`이고 `max(now, occurrence.scheduled_at) < requested scheduled_at < confirmation_deadline_at`인 경우만 허용한다.

성공은 `201 {data: {id, occurrence_id, kind: REMINDER, scheduled_at, status: PENDING}}`다. `Idempotency-Key`와 occurrence_id parent scope를 사용하며 같은 키·같은 hash는 현재 상태 검증보다 먼저 최초 성공 snapshot을 재현한다. 소유권·인증 확인은 snapshot 재현에서도 생략하지 않는다. 다른 hash는 기존 `409 IDEMPOTENCY_KEY_CONFLICT`다. 도메인 row와 암호화된 동기 snapshot을 같은 transaction으로 commit하고 기존 1MiB cap과 만료 기준을 따른다.

소유권 확인·멱등 replay 후 신규 요청의 검증 순서는 아래 제안과 같다.

1. 취소된 occurrence: 기존 `409 OCCURRENCE_CANCELLED`.
2. PENDING이 아니거나 확인 기한 경과: 신규 제안 `409 REMINDER_NOT_ALLOWED`.
3. 기존 REMINDER가 PENDING: 기존 `409 REMINDER_ALREADY_SCHEDULED`.
4. 기존 REMINDER가 DELIVERED/CANCELLED: 신규 제안 `409 REMINDER_LIMIT_REACHED`.
5. 시간 범위·timezone 없는 시각·추가 필드 위반: 기존 `422 VALIDATION_FAILED`.

새 오류 코드는 이 Decision 승인과 공통 오류 문서·OpenAPI·테스트 반영 전 확정하지 않는다. 현재 Check-in이 `NOT_TAKEN`일 때 Track C 지원에서 이 endpoint를 재사용할 수 있어야 하는지는 별도 확인 대상이며, 이번 제한을 기존 승인 조건으로 취급하지 않는다.

## #202와 구현 접점

| 접점 | #203 구현·검증 계획 |
| --- | --- |
| Notification 모델·Repository·Service·라우터 | 별도 파일에서 구현, #202 Schedule·Check-in API 변경 제외 |
| `UndeliveredMedicationNotificationCancellationPort` | 기존 signature 유지, 동일 session 구현 주입 |
| `PrescriptionVersionMedicationInvalidationService` | 취소 반환 occurrence_ids의 미전달 알림만 처리 |
| 일정 PUT/PATCH | #202의 취소 접점 확정 후 동일 transaction 연결 |
| Check-in·deadline | 상태 재검증 및 occurrence 잠금으로 게시 경합 검증 |
| OpenAPI·docs/api.md·docs/data-schema.md·docs/testing.md | #202 병합 후 정합화, 서로의 문서 변경 보존 |

## 승인 후 검증할 시나리오

아래는 테스트 계획이며 실행 PASS를 의미하지 않는다.

- migration 단일 head·upgrade·downgrade 및 enum/check/unique/FK 검증, 계정 삭제 경로 확인.
- 합성 두 사용자 fixture에서 목록 격리, 타인·없는 ID의 동일 404, SELF parent chain 우회 차단.
- 최초 알림 반복 생성·동시 생성에서 중복 0건, 게시 반복 실행과 rollback·재실행.
- 게시 전 비노출, 전달 후 목록 표시, 읽음 최초 시각 보존과 재조회, GET 무변경.
- 처방 활성화·일정 취소·변경과 게시 경합, 과거·전달 이력 보존, 취소 실패 시 전체 rollback.
- Check-in TAKEN·NOT_TAKEN·UNCONFIRMED와 게시 경쟁, 정정 후 복약 결과·audit 불변 및 새 알림 자동 생성 금지.
- 재알림 시간 경계, 1회 제한, 동시 요청, 같은 키 replay·상이 hash·만료 후 재생성 금지.
- 동기 snapshot 암호화·1MiB cap·오류 미저장·일반 로그 비노출.
- 실제 FastAPI 요청/응답과 OpenAPI 비교 및 #202와의 API 통합, no-store·공통 오류 검증.
- Ruff·format·Mypy·기본 CI 및 관련 PostgreSQL 계약·통합 검증. AI/Provider 동작 변경이 없어 의료 AI eval 추가 대상은 아님.
