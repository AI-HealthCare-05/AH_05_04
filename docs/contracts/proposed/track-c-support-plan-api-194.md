# Track C Support Offer·ActionPlan 생성 v1 — #194 부분 구현

- 상태: Proposed / 구현·기술 리뷰 대상. Current 승격 및 #194 전체 완료 아님.
- 구현: 권가빈 @hazelnutflavoured. 책임 리뷰어: @phina-io (Backend·Transaction·Security·Frontend 소비 경계).
- 소비 의견: @solia142 (#139).
- 근거: [PD-194](../../governance/decisions/2026-09-15-track-c-support-plan-api-194.md),
  [Check-in target](../targets/post-mvp-1/checkin-v1.md), [HandlerConfig](track-c-handler-config-192.md).

## 이번 범위

`GET /api/v1/barrier-responses/{id}/supports`와 `POST /api/v1/support-action-plans`만 연결한다.
Plan 조회·완료·취소·follow-up API와 Plan revision column은 추가하지 않는다.
기존 #193/#195의 안전 정정·Check-in 정정 취소 책임을 변경하지 않는다.

## 공통

인증 및 SELF 부모 chain 소유권을 요구한다. 미존재·타인 Barrier는 동일한
`404 BARRIER_RESPONSE_NOT_FOUND`, 응답은 `Cache-Control: no-store`, 성공 `200` + `data` envelope다.
오류는 기존 `code/message/details/trace_id` 형식이다.

POST만 Check-in → 최신 Safety → 최신 Barrier → ACTIVE Plan 순서로 잠근다.
GET은 단일 SQL statement의 MVCC snapshot에서 소유권·현재 Check-in·최신 Safety/Barrier를
읽고 검증한다. FOR UPDATE/행 잠금과 row·멱등 레코드 생성/변경은 없다.
GET 응답 이후 상태가 바뀔 수 있으므로 POST는 서버에서 현재성을 반드시 다시 검증한다.

리뷰 반영 revision 2: 신규 POST는 소유권/멱등 replay 확인 후 **행 잠금 전에** 승인 Rule·Copy를
한 쌍으로 로딩·검증한다. 각 파일을 한 번씩 읽고 동기 파일 I/O는 스레드로 분리한다.
캐시는 추가하지 않으며 파일 누락·손상 차단을 유지한다. 설정 장애와 stale 상태가 동시에 있으면
신규 POST는 설정 단계의 503이 먼저 반환될 수 있다. 기존 성공 replay는 파일을 읽지 않는다.

- 현재 Check-in이 NOT_TAKEN이 아니거나 Barrier의 checkin_revision과 다르면 `409 CHECKIN_FLOW_STALE`.
- 최신 Safety가 없거나 ROUTINE/NORMAL이 아니면 `409 SAFETY_FLOW_PRECEDES_SUPPORT`.
  BLOCKED_ACTION을 일반 지원 허가로 해석하지 않는다.
- Barrier가 최신이 아니거나 최신 Safety에 귀속되지 않으면 `409 BARRIER_FLOW_STALE`.
  새로운 ROUTINE Safety도 과거 Barrier의 재사용을 허용하지 않는다.

## Support 조회

`data` 필수 필드: `barrier_response_id`, `medication_checkin_id`, `checkin_revision`,
`safety_assessment_id`, `supports`, nullable `reason_code`.

`supports`는 승인 설정에서 Barrier에 대응하는 eligible 후보를
`priority ASC, support_code ASC`로 정렬한 **첫 1개**다. 후보 없음·DECLINED는 빈 배열과
`reason_code=NO_ELIGIBLE_SUPPORT`; 1개면 reason_code=null이다.
non-ROUTINE·오래된 흐름·설정 장애를 정상적인 지원 없음으로 숨기지 않는다.

item 필수 필드:

| 필드 | 내용 |
|---|---|
| support_code | 기존 6개 enum 중 하나 |
| rule_version / copy_version | 승인된 불변 설정·문구 버전 |
| priority / rationale_code | 설정에 저장된 우선순위·사유 코드 |
| action_config | schema_version, rationale_code, parameters |
| support_copy | title, body, confirmation_prompt, primary_label, secondary_label |

action_config의 schema_version은 `track-c-handler-config-v1`이다. REMINDER_SETUP parameters는
`destination=MEDICATION_SCHEDULE_SETUP`, 서버 부모 chain에서 얻은 `prescription_version_medication_id`다.
나머지는 해당 지원 enum과 같은 `content_key`만 있다. 환자별 자유 입력·임의 URL·시간을 추가하지 않는다.
Copy는 #192의 버전 파일을 그대로 사용한다. 잘못된 Rule·Copy는 `503 SUPPORT_CONFIG_UNAVAILABLE`이며
원문 설정·파일 경로를 오류에 담지 않는다.

6개 설정 모두 현재는 최소 정적 안내형이다. 고급 Constraint/RAG/LLM 라우팅을 임의 구현하지 않는다.
FORGOT·SCHEDULE_OR_TRAVEL에서 ROUTINE_OR_TRAVEL_PLAN도 후보지만 priority 10의 REMINDER_SETUP만 제안된다.
두 번째 후보를 직접 POST로 선택하는 것은 허용되지 않는다.

## Plan 생성

`Idempotency-Key` 필수. body는 다음 다섯 필드만 허용하며 모두 필수다.

| 필드 | 타입 |
|---|---|
| barrier_response_id | UUID |
| support_code | 기존 SupportCode |
| rule_version | 1~100자 문자열 |
| copy_version | 1~100자 문자열 |
| confirmed | boolean true만 허용; false·1·문자열·누락 거부 |

사용자 확정 후 클라이언트가 confirmed=true를 제출한다. 서버가 사용자의 실제 화면 클릭을 증명하는
기능은 아니며 Frontend는 자동 제출하지 않는다. GET 결과를 받은 사실을 저장하는 별도 Offer row는 없다.
POST에서 동일한 결정적 제안·활성 버전·현재성을 다시 검증한다. GET 응답은 실행 허가 토큰이 아니다.

활성 버전이 다르면 `409 SUPPORT_VERSION_CONFLICT`, 현재 첫 번째 제안이 아닌 지원은
`409 SUPPORT_NOT_OFFERED`, 같은 Barrier에 ACTIVE Plan이 있으면 `409 ACTION_PLAN_ALREADY_ACTIVE`.
클라이언트 config JSON·rationale·priority·약 ID 주입은 `422 VALIDATION_FAILED`다.

서버가 #192 snapshot helper로 Plan을 ACTIVE로 저장한다. data 필드:
`support_action_plan_id`, `barrier_response_id`, `support_code`, `rule_version`, `copy_version`,
`action_config_snapshot`, `status`, `created_at`, nullable `completed_at`, nullable `cancelled_at`.
snapshot은 제안의 action_config와 같은 형태이며 과거 버전을 최신 설정으로 덮어쓰지 않는다.
생성만으로 일정·알림을 변경하거나 Check-in 복용 결과·Plan 완료를 기록하지 않는다.

## 멱등성·원자성

scope: `(user_id, support-action-plan.create, barrier_response_id, key_hmac)`.
SELF 소유권 확인 후 동일 키·동일 body는 최초 성공 HTTP status/body를 그대로 재현한다.
현재 Safety·Check-in·설정 또는 Plan 상태가 바뀌어도 성공 응답을 재계산하지 않는다.
이는 과거 접수 결과의 재현이지 현재 Plan 실행 허가가 아니다.
같은 키·다른 body는 `409 IDEMPOTENCY_KEY_CONFLICT`다.

신규 mutation만 현재 흐름·제안·버전·ACTIVE Plan을 다시 검사한다.
Plan과 암호화된 응답 snapshot을 같은 transaction에 저장하고 오류면 모두 rollback한다.
4xx/5xx는 저장하지 않고 cap 초과는 `503 IDEMPOTENCY_RESPONSE_TOO_LARGE`다.
동일/상이 key의 동시 요청은 Check-in 잠금 및 기존 Barrier별 ACTIVE unique 제약으로 직렬화한다.

## #139 소비·후속

- 0/1개만 표시하며 두 번째 지원·자유 입력·RAG 설명을 Frontend에서 만들지 않는다.
- 신규 흐름·Safety 정정 후 이전 제안으로 POST하지 않는다. 409는 최신 Safety→Barrier 흐름으로 복구한다.
- REMINDER_SETUP 확정 후 기존 일정 화면으로 연결하되 Track B API의 확인·검증을 그대로 사용한다.
- 일정 확인/저장 뒤 명시적인 Plan 완료는 PM 후속 결정이며 이번에는 endpoint가 없다.
- 전용 Plan 조회·완료·취소·follow-up 및 revision 계약은 후속 구현까지 미완료다.
- 이번 부분 구현만으로 #194/#139를 닫거나 외부 공개 게이트를 열지 않는다.

검증: [#194 지원·생성 검증 기록](../../testing/track-c-support-194.md).

## #617 / PR #618 조회·완료·취소 구현

[Current 조회·완료·취소 계약](../current/track-c-plan-lifecycle-617.md)이 GET/PATCH를 추가한다. 위의 “이번 범위”는 #608의 역사적 범위다. 생성 응답·DB revision은 유지하며 책임 리뷰어는 김지혜다. #617 계약은 책임 리뷰의 상태 정렬 요청에 따라 같은 구현 PR에서 Current 경로로 이동한다. 김지혜 승인 후 PR #618이 `a542bcc2`로 병합됐다. 생성 계약 전체나 Follow-up·공개 승인으로 범위를 확대하지 않는다.

## #194 Follow-up 후속

[Follow-up API v1](../current/track-c-followup-api-194.md)은 완료한 계획의 평가 조회·제출·정정을 별도 계약으로 구체화한다.
기존 제안·생성·Plan 응답은 변경하지 않는다. 권가빈 구현·김지혜 단일 책임 리뷰로 PR #631에서 Current 경로에 반영하며 최종 승인·병합은 대기 중이다.
