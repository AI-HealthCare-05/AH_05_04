# Check-in과 Barrier 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Contract Freeze v4 target — 2026-08-27 검증 |
| 구현·리뷰 | Not implemented · 구현 동기화와 관련 지정 리뷰어 검토 대기 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-b-adherence-v1.md`, `track-c-support-v1.md` |
| Last verified | 2026-08-27 |

## 소유권 경계

모든 Track B·C 직접 조회·쓰기 API는 인증 사용자가 직접 소유한 resource만 허용한다. 본인 단일 `SELF` profile과 도메인 리소스의 `user_id → profile_id` 소유권 전환은 Post-MVP-1 현재 범위다. occurrence → schedule·prescription version → prescription → profile, Check-in → occurrence, Safety·Barrier·ActionPlan → Check-in의 parent chain을 따라 `PROFILE.user_id`가 인증 사용자와 같은지 검증한다. 존재하지 않거나 소유하지 않은 ID는 존재 여부를 숨기기 위해 모두 `404`다. 이 문서는 새 권한 역할이나 잠금 순서를 추가하지 않는다.

## 일정 occurrence와 Check-in 분리

복약 일정 occurrence는 응답 전 `PENDING`일 수 있지만, 이는 Check-in 저장 상태가 아니다. 사용자가 제출하거나 응답 기한이 지난 뒤 생성되는 Check-in 결과는 세 가지다.

| status | 의미 | 생성 주체 |
|---|---|---|
| `TAKEN` | 복용했다고 명시 | 사용자 |
| `NOT_TAKEN` | 복용하지 않았다고 명시 | 사용자 |
| `UNCONFIRMED` | 기한까지 응답 없음 | Scheduler |

- “건너뛰기” 액션과 별도 `SKIPPED` 상태는 두지 않는다.
- `NOT_TAKEN`과 무응답 `UNCONFIRMED`를 합치지 않는다.
- 늦은 복용은 `TAKEN`과 실제 `taken_at`으로 표현하고 별도 상태를 추가하지 않는다.

Timed occurrence는 사용자가 일정 설정 API에서 시작일·종료 결정·정확한 시각을 확인한 `medication_schedule`이 있을 때만 생성한다. 처방에 정확한 시작일·시각이 있어도 명시적 확인이 필요하며, `timing_text`, `frequency_per_day`, 처방 확정일만으로 값을 추정하지 않는다. 미설정 약은 `schedule_item_status=SETUP_REQUIRED`와 `MISSING_START_DATE|MISSING_EXACT_TIME|MISSING_DURATION_DECISION|UNSUPPORTED_SCHEDULE_PATTERN` 중 하나를 반환하고 occurrence·알림을 만들지 않는다. 전체 `schedule_status`는 `READY|PARTIAL|SETUP_REQUIRED|INACTIVE|NO_ACTIVE_PRESCRIPTION`이다.

`medication_schedule`은 `prescription_version_medication_id`를 unique로 참조하고 `end_mode=DATE|OPEN_ENDED`, `source=PRESCRIPTION_EXACT|USER_CONFIRMED`, `status=ACTIVE|CANCELLED|ENDED`, revision을 가진다. 시각은 별도 `medication_schedule_time` row에 revision별로 보존하고 `(medication_schedule_id, schedule_revision, local_time)`을 unique로 둔다. occurrence 상태는 `PENDING|CANCELLED|CLOSED`이며 Check-in 생성 시 `CLOSED`가 된다. 일정 `PUT`은 최초 생성·변경과 `CANCELLED|ENDED`의 명시적 재활성화를 담당하고, `PATCH`는 사용자 `CANCELLED`만 허용하며 `ENDED`는 Scheduler만 설정한다.

Post-MVP-1은 매일 동일한 시각 반복만 지원하고 Scheduler는 앞으로 14일 rolling horizon만 생성한다. `confirmation_deadline_at`은 `max(Asia/Seoul 기준 예정일 다음 날 00:00, scheduled_at + 4시간)`을 UTC instant로 계산해 snapshot한다. 모든 DB timestamp는 UTC로 저장한다. 배포 설정의 서비스 시간대가 누락되거나 `Asia/Seoul`이 아니면 Scheduler 시작을 거부한다. 사용자별 IANA time zone은 후속 계약이다.

Scheduler는 `confirmation_deadline_at <= now`이고 결과가 없는 occurrence만 처리한다. `medication_checkin.occurrence_id` unique 제약과 조건부 insert로 중복 실행에도 `UNCONFIRMED`를 하나만 만든다. deadline 뒤 사용자가 복용 또는 미복용을 명시하면 현재 결과를 `TAKEN` 또는 `NOT_TAKEN`으로 정정하고 이전 `UNCONFIRMED`는 감사 이력에 남긴다.

## 수정과 감사

Post-MVP-1에서는 사용자가 과거 결과를 횟수 제한 없이 수정할 수 있다. 현재값과 별도로 `checkin_audit`에 `checkin_id`, `from_status`, `to_status`, `from_revision`, `to_revision`, `changed_by`, nullable `reason_code`, `changed_at`을 append-only로 저장한다.

Check-in `PUT` 요청은 `Idempotency-Key` 헤더와 `expected_revision`을 요구한다. 동기 멱등 레코드의 unique scope는 `(user_id, API operation, occurrence_id, key_hmac)`이며 원문 키를 저장하지 않는다. 같은 키·같은 request hash는 revision 검사보다 먼저 같은 transaction에서 저장한 최초 성공 HTTP status와 canonical body snapshot을 재현하고, 같은 키·다른 hash는 `409 IDEMPOTENCY_KEY_CONFLICT`다. 신규 키에서 현재 revision과 다른 `expected_revision`은 payload가 현재 값과 같아도 `409 CHECKIN_REVISION_CONFLICT`다. snapshot은 최대 1MiB이며 암호화 저장·일반 로그 금지이고, 초과 시 mutation 전 `503 IDEMPOTENCY_RESPONSE_TOO_LARGE`로 실패한다.

처방 버전이 바뀌면 effective 시각 이후 이전 버전의 `PENDING` occurrence와 미전달 알림만 취소한다. 이전 일정·시각을 새 버전에 복사·재귀속하거나 새 occurrence를 자동 생성하지 않는다. 새 버전의 모든 대상 약은 사용자가 일정 설정 API로 다시 확인하기 전 `SETUP_REQUIRED`다. 이미 마감되었거나 사용자가 응답한 Check-in과 감사 이력은 당시 `prescription_version_id`에 남긴다.

자정 경계, UTC 변환, Scheduler 중복 실행과 처방 변경을 계약 fixture로 고정한다. 사용자별 time zone과 DST fixture는 해당 기능을 도입하는 후속 version에서 추가한다.

## Barrier 응답

Barrier는 Check-in status와 별도 축이다.

- 사용자가 장벽을 선택: `response_status=ANSWERED`, 선택한 `barrier_code` 저장
- “응답하지 않음/넘어가기”를 명시: `response_status=DECLINED`, `barrier_code=null`
- Barrier 단계 자체를 열지 않거나 제출하지 않음: 응답 row를 생성하지 않음

v1 `barrier_code`는 `FORGOT`, `SCHEDULE_OR_TRAVEL`, `INSTRUCTIONS_UNCLEAR`, `NEED_DOUBT`, `MEDICATION_CONCERN`, `ACCESS_OR_COST`로 고정한다. `UNKNOWN`, `USER_DECLINED`, `uncertain`을 건너뛰기 값으로 사용하지 않는다.

## 목표 API와 고정 오류

Track B의 목표 API는 다음으로 고정한다.

- `GET /api/v1/medication-occurrences?date=YYYY-MM-DD`
- `PUT /api/v1/prescription-version-medications/{prescription_version_medication_id}/schedule`
- `PATCH /api/v1/prescription-version-medications/{prescription_version_medication_id}/schedule`
- `PUT /api/v1/medication-occurrences/{occurrence_id}/check-in`
- `GET /api/v1/medication-occurrences/{occurrence_id}/check-in-history`
- `POST /api/v1/medication-occurrences/{occurrence_id}/reminders`

Track C의 목표 API는 다음으로 고정한다.

- `POST /api/v1/safety-assessments`
- `PUT /api/v1/medication-checkins/{checkin_id}/barrier-response`
- `GET /api/v1/barrier-responses/{id}/supports`
- `POST /api/v1/support-action-plans`
- `PATCH /api/v1/support-action-plans/{id}`
- `POST /api/v1/support-action-plans/{id}/followups`

### 목표 DTO 요약

- 일정 조회 응답은 `schedule_status`, 약별 `schedule_items[]`, 날짜별 `occurrences[]`, 현재 Check-in, `revision`, `corrected`, `prescription_version_id`를 포함한다. 약별 항목은 `schedule_item_status`, `prescription_version_medication_id`, nullable `schedule_id`, nullable `revision`, nullable `setup_reason`을 포함한다. 전체 상태는 활성 처방 없음 → `NO_ACTIVE_PRESCRIPTION`, READY와 SETUP_REQUIRED 혼합 → `PARTIAL`, SETUP_REQUIRED만 존재 → `SETUP_REQUIRED`, setup 대상 없이 READY 존재 → `READY`, 나머지가 모두 INACTIVE이고 pending occurrence 없음 → `INACTIVE` 순으로 판정한다. 원본에서 occurrence 정렬은 별도 고정하지 않았다.
- 일정 `PUT` body는 `start_local_date`, `end_mode`, nullable `end_local_date`, `local_times[]`, `expected_revision`; 취소 `PATCH` body는 `status=CANCELLED`, `expected_revision`이다.
- Check-in `PUT` body는 `status=TAKEN|NOT_TAKEN`, nullable `taken_at`, nullable `reason_code`, `expected_revision`이다. `taken_at`은 `TAKEN`에서만 허용한다.
- Safety assessment 요청은 `medication_checkin_id`, `checkin_revision`, `symptom_codes[]`, `expected_revision`; 응답은 `assessment_id`, `medication_checkin_id`, `checkin_revision`, `response_level`, `safety_disposition`, `message_code`, `copy_version`, `source_version`, `revision`이다.
- Barrier 요청은 `response_status`, nullable `barrier_code`, `checkin_revision`, `expected_revision`이다. Support 응답은 `support_code`, `copy_version`, `priority`, `rationale_code`, 허용 action config를 포함하고 `priority ASC, support_code ASC`로 최대 2개를 반환한다. ActionPlan은 선택한 rule/copy version을 snapshot한다.

이 요약은 승인 원본의 최소 필드와 순서만 옮긴 것이다. 구현 PR에서 새 필수 필드, enum, 정렬 또는 오류를 추가하려면 계약 version을 갱신해야 한다.

목표 오류 의미는 다음과 같다.

- 일정 생성·변경의 `expected_revision` 불일치: `409 SCHEDULE_REVISION_CONFLICT`
- Check-in에 사용자가 `UNCONFIRMED` 제출: `422 CHECKIN_STATUS_NOT_USER_SETTABLE`
- 취소된 미래 occurrence에 Check-in 제출: `409 OCCURRENCE_CANCELLED`
- 구조화 목록이 아닌 자유 텍스트 증상 제출: `422 FREE_TEXT_SYMPTOM_NOT_SUPPORTED`
- Safety assessment의 오래된 revision: `409 SAFETY_ASSESSMENT_REVISION_CONFLICT`
- 최신 Safety assessment가 없거나 `ROUTINE`이 아닌 상태에서 Barrier 진행: `409 SAFETY_FLOW_PRECEDES_BARRIER`
- Check-in이 `NOT_TAKEN`이 아니거나 assessment revision이 현재 Check-in revision과 다름: `409 CHECKIN_FLOW_STALE`
- 같은 occurrence의 활성 재알림이 이미 존재: `409 REMINDER_ALREADY_SCHEDULED`

모든 쓰기 API는 `Idempotency-Key`와 동기 응답 snapshot 계약을 적용한다. 상세 요청 DTO, revision 처리와 공개 범위는 구현 OpenAPI 및 계약 테스트로 이 목록과 함께 검증한다.

## Safety assessment lifecycle

- Safety·Barrier·일반 Support는 현재 Check-in이 `NOT_TAKEN`일 때만 허용한다. `UNCONFIRMED`는 사용자가 먼저 `NOT_TAKEN`으로 정정해야 하며 `TAKEN`의 현재 증상 처리는 v1 adherence flow 범위가 아니다.
- Barrier 전에 증상 선택 여부와 무관하게 assessment를 저장한다. 빈 `symptom_codes=[]`는 “증상 없음 확인”이며 `ROUTINE` 결과를 만든다.
- 최신 assessment가 없거나 `ROUTINE`이 아니면 Barrier로 진행할 수 없다.
- Check-in에는 revision별 assessment를 append-only로 저장하고 가장 높은 최신 revision 하나만 현재 흐름을 결정한다.
- 요청은 `expected_revision`을 사용하며 최초 `0`, 오래된 revision은 `409 SAFETY_ASSESSMENT_REVISION_CONFLICT`다.
- 최신 결과가 `URGENT`, `EMERGENCY`, `UNKNOWN`이면 Barrier·일반 Support를 생성하지 않는다.
- 기존 Barrier 뒤 non-`ROUTINE` 정정이 생기면 이력은 보존하고 활성 ActionPlan을 같은 transaction에서 `CANCELLED` 처리한다.
- 잠금 순서는 `MEDICATION_CHECKIN → SAFETY_ASSESSMENT → BARRIER_RESPONSE → SUPPORT_ACTION_PLAN`이다.
- Check-in write transaction의 owner는 Track B다. 현재 `NOT_TAKEN` revision이 `TAKEN` 또는 새 `NOT_TAKEN` revision으로 바뀌면 B가 Track C의 동기 `invalidate_for_checkin_revision` port를 같은 transaction에서 호출해 이전 revision의 Safety·Barrier 이력을 보존하고 활성 ActionPlan을 취소한다. 새 상태가 `NOT_TAKEN`이면 Safety부터 다시 시작하며 과거 revision 결과를 현재 흐름에 사용하지 않는다.

## Support와 실행계획

- 표시 문구는 enum과 분리해 `copy_version`으로 관리한다.
- Support rule은 다음 안정적인 `support_code`, 대상 barrier 목록, eligibility, exclusion과 priority를 가진다.

| support_code | 대상 Barrier | priority | 추가 조건 |
|---|---|---:|---|
| `REMINDER_SETUP` | `FORGOT`, `SCHEDULE_OR_TRAVEL` | 10 | 없음 |
| `ROUTINE_OR_TRAVEL_PLAN` | `SCHEDULE_OR_TRAVEL`, `FORGOT` | 20 | 없음 |
| `INSTRUCTION_REVIEW` | `INSTRUCTIONS_UNCLEAR` | 10 | 없음 |
| `PURPOSE_REVIEW` | `NEED_DOUBT` | 10 | 없음 |
| `MEDICATION_CONCERN_GUIDANCE` | `MEDICATION_CONCERN` | 10 | Safety Router가 `ROUTINE` |
| `ACCESS_SUPPORT` | `ACCESS_OR_COST` | 10 | 없음 |

- Track C Safety Router 결과는 `ROUTINE`, `URGENT`, `EMERGENCY`, `UNKNOWN`이다.
- eligible Support는 `priority ASC, support_code ASC` 순으로 최대 2개 반환하고, 0개면 `NO_ELIGIBLE_SUPPORT`를 반환한다.
- Safety Router가 `URGENT`, `EMERGENCY`, `UNKNOWN`으로 분기한 경우 일반 Support를 반환하지 않는다.
- 실행계획 상태는 `ACTIVE`, `COMPLETED`, `CANCELLED`다.
- follow-up 응답은 `HELPED`, `NOT_HELPED`, `NOT_SURE`다.
- Post-MVP-1 재알림은 앱 내부 사용자 요청 1회만 지원하며 외부 Push·SMS provider 연동은 범위 밖이다.
