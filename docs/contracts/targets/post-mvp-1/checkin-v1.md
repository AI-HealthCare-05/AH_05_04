# Check-in과 Barrier 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Contract Freeze v4 target — 2026-08-27 검증 |
| 구현·리뷰 | Not implemented · 구현 동기화와 관련 지정 리뷰어 검토 대기 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-b-adherence-v1.md`, `track-c-support-v1.md` |
| Proposed delta | 아래 `setup_reason` 신규 값·우선순위는 Decision/Contract Freeze 승인 전 TBD이며 Approved v4에 포함되지 않음 |
| Last verified | 2026-08-27 |

## 소유권 경계

모든 Track B·C 직접 조회·쓰기 API는 인증 사용자가 직접 소유한 resource만 허용한다. #117 병합 이후 occurrence → schedule·prescription version → prescription, Check-in → occurrence, Safety·Barrier·ActionPlan → Check-in의 parent chain은 SELF `profile_id` 또는 부모 chain의 `profile_id`를 기준으로 소유권을 확인한다. 존재하지 않거나 소유하지 않은 ID는 존재 여부를 숨기기 위해 모두 `404`다. 이 문서는 새 권한 역할이나 잠금 순서를 추가하지 않는다.

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

Timed occurrence는 사용자가 일정 설정 API에서 시작일·종료 결정·정확한 시각을 확인한 `medication_schedule`이 있을 때만 생성한다. 처방에 정확한 시작일·시각이 있어도 명시적 확인이 필요하며, `timing_text`, `frequency_per_day`, 처방 확정일만으로 값을 추정하지 않는다. Approved v4에서 미설정 약은 `schedule_item_status=SETUP_REQUIRED`와 `MISSING_START_DATE|MISSING_EXACT_TIME|MISSING_DURATION_DECISION|UNSUPPORTED_SCHEDULE_PATTERN` 중 하나를 반환하고 occurrence·알림을 만들지 않는다. 여러 사유가 동시에 있을 때의 단일 값 선택 우선순위와 `USER_CONFIRMATION_REQUIRED` 추가는 Approved v4에 포함되지 않은 Proposed/TBD delta이며 아래 별도 절에 격리한다. 전체 `schedule_status`는 `READY|PARTIAL|SETUP_REQUIRED|INACTIVE|NO_ACTIVE_PRESCRIPTION`이다.

`medication_schedule`은 `prescription_version_medication_id`를 unique로 참조하고 `end_mode=DATE|OPEN_ENDED`, `source=PRESCRIPTION_EXACT|USER_CONFIRMED`, `status=ACTIVE|CANCELLED|ENDED`, revision을 가진다. 시각은 별도 `medication_schedule_time` row에 revision별로 보존하고 `(medication_schedule_id, schedule_revision, local_time)`을 unique로 둔다. occurrence 상태는 `PENDING|CANCELLED|CLOSED`이며 Check-in 생성 시 `CLOSED`가 된다. 일정 `PUT`은 최초 생성·변경과 `CANCELLED|ENDED`의 명시적 재활성화를 담당하고, `PATCH`는 사용자 `CANCELLED`만 허용하며 `ENDED`는 Scheduler만 설정한다.

처방에 `frequency_per_day`가 존재하면 일정 생성·변경 요청의 `local_times.length`와 반드시 일치해야 한다. Frontend는 저장 전 같은 기준으로 사전 검증하고, Backend는 동일 기준으로 다시 검증한다. 불일치하면 `422 VALIDATION_FAILED` 또는 Track B에서 정의한 검증 오류를 반환하며 `medication_schedule`과 `medication_schedule_time` row를 저장하지 않는다. 처방의 `frequency_per_day`가 없으면 사용자가 입력한 `local_times.length`를 일정 생성 기준으로 사용한다.

Post-MVP-1은 매일 동일한 시각 반복만 지원하고 Scheduler는 앞으로 14일 rolling horizon만 생성한다. `confirmation_deadline_at`은 `max(Asia/Seoul 기준 예정일 다음 날 00:00, scheduled_at + 4시간)`을 UTC instant로 계산해 snapshot한다. 모든 DB timestamp는 UTC로 저장한다. 배포 설정의 서비스 시간대가 누락되거나 `Asia/Seoul`이 아니면 Scheduler 시작을 거부한다. 사용자별 IANA time zone은 후속 계약이다.

Scheduler는 `confirmation_deadline_at <= now`이고 결과가 없는 occurrence만 처리한다. Scheduler의 deadline 처리와 사용자 `PUT`의 최초 Check-in 생성은 모두 `medication_checkin.occurrence_id` unique 제약을 직렬화 기준으로 삼고 조건부 insert와 insert 충돌 처리를 사용한다. 따라서 deadline 부근에 두 경로가 경쟁해도 occurrence마다 현재 Check-in row는 하나만 생성하며, 충돌 뒤 처리는 아래 `expected_revision`과 정정 계약을 따른다. deadline 뒤 사용자가 복용 또는 미복용을 명시하면 현재 결과를 `TAKEN` 또는 `NOT_TAKEN`으로 정정하고 이전 `UNCONFIRMED`는 감사 이력에 남긴다.

## 수정과 감사

Post-MVP-1에서는 사용자가 과거 결과를 횟수 제한 없이 수정할 수 있다. 현재값과 별도로 `checkin_audit`에 `checkin_id`, `from_status`, `to_status`, `from_revision`, `to_revision`, `changed_by`, `changed_at`을 append-only로 저장한다. `reason_code`는 enum이 확정되기 전까지 Check-in 생성·정정 요청, OpenAPI request schema와 DB enum에서 제외하며 `checkin_audit`에도 해당 컬럼을 만들지 않는다. 이력 응답 예시에서 필요하면 확정되지 않은 값을 쓰지 않고 `reason_code=null` 또는 필드 생략으로 표시한다.

Check-in `PUT` 요청은 `Idempotency-Key` 헤더와 `expected_revision`을 요구하며 최초 생성은 `expected_revision=0`이다. 동기 멱등 레코드의 unique scope는 `(user_id, API operation, occurrence_id, key_hmac)`이며 원문 키를 저장하지 않는다. 같은 키·같은 request hash는 revision 검사보다 먼저 같은 transaction에서 저장한 최초 성공 HTTP status와 canonical body snapshot을 재현하고, 같은 키·다른 hash는 `409 IDEMPOTENCY_KEY_CONFLICT`다. 신규 키에서 현재 revision과 다른 `expected_revision`은 payload가 현재 값과 같아도 `409 CHECKIN_REVISION_CONFLICT`다. snapshot은 최대 1MiB이며 암호화 저장·일반 로그 금지이고, 초과 시 mutation 전 `503 IDEMPOTENCY_RESPONSE_TOO_LARGE`로 실패한다.

처방 version이 바뀌면 `effective_at` 이후에 예정된 이전 version의 `PENDING` occurrence와 미전달 알림만 취소한다. 이전 일정·시각을 새 version에 복사·재귀속하거나 참고 후보로 자동 제공하지 않고, 새 occurrence도 자동 생성하지 않는다. 새 version의 모든 `prescription_version_medication`은 이전 version과 약명·용량·횟수가 같더라도 사용자가 해당 version의 일정을 다시 확인하기 전까지 `SETUP_REQUIRED`다.

처방 version 확정과 이전 version의 `PENDING` occurrence·미전달 알림 취소를 하나의 DB transaction, Outbox 또는 다른 비동기 경계 중 어떤 방식으로 결합할지는 이 문서에서 고정하지 않는다. Track A 비동기 인프라가 확정된 뒤 후속 Issue와 별도 Decision에서 transaction 경계, 실패 복구와 재처리 방식을 정한다.

이전 version의 schedule·time revision, `effective_at` 이전 occurrence, 이미 생성된 Check-in과 Check-in audit은 생성 당시 `prescription_version_id`에 그대로 보존하고 새 version으로 재귀속하지 않는다. `effective_at` 이전에 예정되었지만 아직 결과가 없는 occurrence도 취소하지 않으며, deadline이 지났다면 Scheduler가 기존 기준에 따라 `UNCONFIRMED`를 생성한다.

`UNCONFIRMED` backlog를 조회하고 다음 로그인에서 보완하는 기능은 Track B 완료 범위에 유지한다. 전용 API의 URL·pagination 등 상세 계약은 별도 Issue에서 고정할 수 있지만, 이 기능을 구현·검증하기 전에 Track B를 완료로 판정하지 않는다. 사용자 알림 ON/OFF preference와 외부 Push·SMS 등 전달 채널 정책은 후속 Issue로 분리할 수 있다. 단, 앱 내부 알림의 생성·중복 방지·version 변경 시 취소와 알림 상태로 복용 결과를 추정하지 않는 기준은 Track B 범위에 유지한다.

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
- Check-in `PUT` body는 `status=TAKEN|NOT_TAKEN`, nullable `taken_at`, `expected_revision`이다. 최초 생성의 `expected_revision`은 `0`이며 `taken_at`은 `TAKEN`에서만 허용한다. `reason_code`는 enum 확정 전까지 요청 body에 포함하지 않는다.
- Safety assessment 요청은 `medication_checkin_id`, `checkin_revision`, `symptom_codes[]`, `expected_revision`; 응답은 `assessment_id`, `medication_checkin_id`, `checkin_revision`, `response_level`, `safety_disposition`, `message_code`, `copy_version`, `source_version`, `revision`이다.
- Barrier 요청은 `response_status`, nullable `barrier_code`, `checkin_revision`, `expected_revision`이다. Support 응답은 `support_code`, `copy_version`, `priority`, `rationale_code`, 허용 action config를 포함하고 `priority ASC, support_code ASC`로 최대 2개를 반환한다. ActionPlan은 선택한 rule/copy version을 snapshot한다.

이 요약은 승인 원본의 최소 필드와 순서만 옮긴 것이다. 구현 PR에서 새 필수 필드, enum, 정렬 또는 오류를 추가하려면 계약 version을 갱신해야 한다.

### Proposed/TBD — `setup_reason` 신규 값·우선순위

이 절은 Approved Contract Freeze v4의 일부가 아니며 구현 근거로 사용할 수 없다. 다음 신규 값·우선순위는 별도 Decision 또는 Contract Freeze version에서 승인될 때까지 Proposed/TBD다. 승인 시 Backend가 고정 우선순위를 계산해 `schedule_item_status=SETUP_REQUIRED`인 약품별 항목에 nullable 단일 `setup_reason`을 반환한다. Frontend는 반환값을 표시·분기에만 사용하고 동일 우선순위를 재계산하지 않는다. `NO_ACTIVE_PRESCRIPTION`은 약품별 `setup_reason`이 아니라 전체 `schedule_status`로만 반환한다. `NEW_PRESCRIPTION_VERSION`과 `NEW_MEDICATION`은 중복·경계가 불명확하므로 제안 enum에 포함하지 않는다.

| 우선순위 | `setup_reason` | 의미 |
| ---: | --- | --- |
| 1 | `UNSUPPORTED_SCHEDULE_PATTERN` | v1의 매일 동일 시각 반복으로 표현할 수 없음 |
| 2 | `MISSING_START_DATE` | 시작일 확인이 필요함 |
| 3 | `MISSING_EXACT_TIME` | 정확한 복용 시각 확인이 필요함 |
| 4 | `MISSING_DURATION_DECISION` | 종료일 또는 계속 복용 여부 확인이 필요함 |
| 5 | `USER_CONFIRMATION_REQUIRED` | 필요한 값은 모두 있지만 사용자가 해당 version의 일정을 아직 확인하지 않음 |

제안안에서 `USER_CONFIRMATION_REQUIRED`는 위의 1~4 사유가 없고 active schedule도 없을 때만 사용한다. 이 enum 추가와 우선순위 고정은 공유 계약 변경이므로 구현 전 Decision 또는 Contract Freeze version에 반영하고 OpenAPI·DTO·Frontend fixture·계약 테스트를 같이 동기화한다. 승인 전에는 이 표를 확정 enum 또는 테스트 기대값으로 사용하지 않는다.

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
- Check-in write transaction의 owner는 Track B다. 현재 `NOT_TAKEN` revision이 `TAKEN` 또는 새 `NOT_TAKEN` revision으로 바뀌면 B가 Track C의 `invalidate_for_checkin_revision` port를 별도 queue·Outbox를 거치지 않는 in-process 동기 함수로 같은 transaction에서 호출한다. 이 호출도 `MEDICATION_CHECKIN → SAFETY_ASSESSMENT → BARRIER_RESPONSE → SUPPORT_ACTION_PLAN` 잠금 순서를 그대로 따르며, 이전 revision의 Safety·Barrier 이력을 보존하고 활성 ActionPlan을 취소한다. 새 상태가 `NOT_TAKEN`이면 Safety부터 다시 시작하며 과거 revision 결과를 현재 흐름에 사용하지 않는다.

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
