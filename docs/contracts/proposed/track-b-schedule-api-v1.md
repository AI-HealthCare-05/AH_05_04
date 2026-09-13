# Track B 일정 API v1

| 항목 | 값 |
| --- | --- |
| 상태 | Proposed HTTP 구체화 — 작업 브랜치 구현, 지정 리뷰어 승인 대기 |
| Decision | [PD-202-20260911](../../governance/decisions/2026-09-11-schedule-api-202.md) |
| 승인된 의미 | [PD-417 일정 정합화 v1](../targets/post-mvp-1/track-b-schedule-reconciliation-v1.md) |
| 담당 / 리뷰 | 권가빈 / 송은영(Backend·Security), 남한솔(Frontend) |

기존 target의 상태 전이·reason 규칙을 중복 정의하지 않고 HTTP 표현만 구체화한다.
OpenAPI는 실제 앱 `/openapi.json`으로 검증한다. 날짜는 KST local date, instant는 UTC다.

## 일정 PUT/PATCH

`/api/v1/prescription-version-medications/{prescription_version_medication_id}/schedule`

- PUT 필수: `start_local_date`, `end_mode=DATE|OPEN_ENDED`, `local_times`, `expected_revision`.
- `end_local_date`는 생략/null 가능하되 DATE면 필수, OPEN_ENDED면 null이다. 종료일은 시작일 이상이다.
- local_times는 비어 있지 않은 중복 없는 HH:mm 집합이다. 입력 순서는 정렬하여 지문·응답에 사용한다.
- frequency_per_day가 non-null이면 시각 개수와 일치해야 한다. 불일치는 422 VALIDATION_FAILED,
  details의 field=local_times, reason=FREQUENCY_MISMATCH, rejected_value=null이며 저장하지 않는다.
- PATCH 필수: `status=CANCELLED`, `expected_revision`. 직접 ENDED 제출은 422다.
- expected_revision은 0 이상의 엄격한 정수다. string/bool과 알려지지 않은 필드는 거부한다.
- 필수 Idempotency-Key는 기존 공통 형식·암호화 snapshot·충돌 의미를 따른다.
- 두 method 모두 200 `data`: `schedule_id`, `prescription_version_medication_id`, `revision`,
  `status`, `start_local_date`, `end_mode`, nullable `end_local_date`, `local_times`.
- 취소/종료의 응답 시각은 마지막 설정 시각이며 새 occurrence 생성용 현재 time이 아니다.
- 현재 USER_CONFIRMED 설정만 PUT으로 저장한다. source·audit·actor는 요청에서 받지 않는다.

SELF 소유권 확인 → 성공 snapshot 재현 → 신규 요청 잠금·version/revision 검증 → 설정·audit·
미래 occurrence 취소 → 같은 session의 NotificationRepository 미전달 취소 → PUT horizon 생성 →
전체 성공 응답 암호화 저장 순이다. 상위 목표의 저장 서비스가 내부 commit하지 않고 기존
SYNC_MUTATION이 snapshot을 저장한 뒤 요청 DB 의존성이 transaction을 commit한다. 이미 전달된 미읽음 알림은 보존한다.

## 날짜별 조회

`GET /api/v1/medication-occurrences?date=YYYY-MM-DD` → 200 `data`:

| 필드 | 표현 |
| --- | --- |
| schedule_status | READY, PARTIAL, SETUP_REQUIRED, INACTIVE, NO_ACTIVE_PRESCRIPTION |
| schedule_items | 현재 활성 처방 version의 약별 항목 배열 |
| occurrences | 지정한 원래 KST 날짜의 본인 occurrence 배열; 과거 version·취소·현재 Check-in 포함 |

schedule_items의 각 항목은 `prescription_version_medication_id`, `schedule_item_status`,
nullable `schedule_id`, nullable `revision`, nullable `setup_reason`이다. reason은 PD-417의
5값이고 READY/INACTIVE에서는 null이다. 현재 snapshot에 정확한 입력 필드가 없으므로
일정이 없는 약은 MISSING_START_DATE다. 값을 추정하지 않는다.

occurrences의 각 항목은 `occurrence_id`, `prescription_version_id`,
`prescription_version_medication_id`, `scheduled_local_date`, `scheduled_at`,
`confirmation_deadline_at`, `status=PENDING|CANCELLED|CLOSED`, nullable `checkin`이다.
checkin은 기존 Check-in PUT data와 같은 `checkin_id`, `occurrence_id`,
`status=TAKEN|NOT_TAKEN|UNCONFIRMED`, nullable `taken_at`, `revision`, `corrected`다.

PARTIAL은 READY occurrence를 숨기지 않고 INACTIVE도 과거 pending을 반환한다.
알림의 occurrence_local_date를 date로 보내면 원래 날짜를 조회한다. 배열 순서·pagination·
추가 상태 filter는 이번 계약에 도입하지 않는다. unknown date와 인증 오류는 공통 경계를 따른다.

## 오류·노출 경계

- 400: IDEMPOTENCY_KEY_REQUIRED / IDEMPOTENCY_KEY_INVALID.
- 401: 기존 인증 오류.
- 404: PRESCRIPTION_MEDICATION_NOT_FOUND; 없는 약과 타 사용자 약이 같은 표현이다.
- 409: SCHEDULE_REVISION_CONFLICT / PRESCRIPTION_VERSION_CONFLICT / IDEMPOTENCY_KEY_CONFLICT.
  저장 서비스의 기존 처방 fingerprint 검증 실패는 PRESCRIPTION_VERSION_UNAVAILABLE로 유지한다.
- 422: VALIDATION_FAILED; DTO 오류와 frequency mismatch 모두 미저장.
- 503: IDEMPOTENCY_RESPONSE_TOO_LARGE; audit·occurrence·알림·snapshot 모두 rollback.

모든 오류는 `{code,message,details,trace_id}`, 모든 응답은 no-store와 X-Trace-Id를 적용한다.
실제 환자 fixture·외부 AI/Push 호출은 없다. 대표 예시는
[합성 Frontend fixture](../../validation/track-b/issue-202-schedule-fixtures.json)와
[검증 기록](../../validation/track-b/issue-202-schedule-api.md)을 참조한다.
