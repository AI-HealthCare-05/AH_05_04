# Track C ActionPlan Follow-up API v1 — #194 후속

- 상태: Proposed / 구현·책임 리뷰 대상. Current 승격·#194 전체 완료·Production 공개 승인 아님.
- 구현 담당: 권가빈 (@hazelnutflavoured).
- 단일 책임 리뷰어: 김지혜 (@Jye-rookie) — Backend·API/DTO·Transaction·Security·Frontend 소비 계약.
- 제품 결정: [PD-194-2](../../governance/decisions/2026-09-16-track-c-followup-194.md).
- 기반: [저장 계약](track-c-storage-v1.md), [Plan lifecycle](../current/track-c-plan-lifecycle-617.md), [멱등성](../targets/post-mvp-1/idempotency-v1.md).

## 의미와 범위

Follow-up은 완료한 지원 계획에 대한 사용자 의견이다. 약의 효과나 복용 여부를 판정하지 않는다.
`HELPED`, `NOT_HELPED`, `NOT_SURE`만 허용하며 나중에는 호출·저장하지 않는다.
Plan당 현재 응답 하나와 정정 이력을 보존한다. 기존 Plan·일정·Check-in·Safety·Barrier를 변경하지 않는다.
완료 뒤 부모 흐름이 정정돼도 평가·정정을 허용한다. 일반 Support 실행 허가로 사용하지 않는다.
새 Rule·Copy·의료 정책·Provider 호출, 자동 추천, 삭제, audit 목록 API, Frontend는 범위 밖이다.

## 공통 응답

인증과 Plan → Barrier → Check-in → occurrence → schedule → prescription의 SELF 소유권을 검증한다.
미존재·타인 Plan은 모두 `404 ACTION_PLAN_NOT_FOUND`다. 응답은 항상 `Cache-Control: no-store`다.
성공은 `data` envelope, 오류는 `code/message/details/trace_id` 형식이다.

평가 data의 필수 필드:

| 필드 | 타입·의미 |
| --- | --- |
| followup_id | UUID; 최초 생성 후 유지 |
| support_action_plan_id | UUID; 원래 계획 ID, 변경 불가 |
| response | HELPED / NOT_HELPED / NOT_SURE |
| revision | 양의 정수; 최초 1 |
| created_at | UTC timestamp; 최초 저장 시각, 변경 불가 |
| updated_at | UTC timestamp; 최근 제출 시각 |

## GET /api/v1/support-action-plans/{id}/followups

- operationId: `support-action-plan.followup.get`.
- 200: 현재 평가를 반환한다. 소유 Plan에 평가가 없으면 `data=null`이며 ACTIVE/CANCELLED도 동일하다.
- 부모 소유권과 현재 평가를 하나의 SQL snapshot에서 읽는다. 행 잠금·mutation·Idempotency-Key는 없다.
- Check-in 정정, 최신 Safety, 운영 Rule·Copy 설정에 의존하지 않는다.

## POST /api/v1/support-action-plans/{id}/followups

- operationId: `support-action-plan.followup.submit`.
- 최초 제출·정정 모두 200과 평가 data를 반환한다.
- `Idempotency-Key` 필수, 기존 16~255 ASCII 형식을 따른다.
- body는 아래 두 필드만 허용하며 모두 필수다. 자유 입력과 사용자 ID·시각·revision 주입을 허용하지 않는다.

| 필드 | 타입·의미 |
| --- | --- |
| response | HELPED / NOT_HELPED / NOT_SURE; 사용자가 직접 선택한 응답 |
| expected_revision | strict JSON integer >= 0; 미응답 0, 정정은 현재 follow-up revision |

### transaction과 오류 우선순위

1. SELF Plan 소유권을 멱등 replay보다 먼저 검증한다.
2. 같은 `(user_id, operation_id, plan_id, key_hmac)`의 성공은 최초 암호화 snapshot을 반환한다.
   같은 key의 다른 body는 `409 IDEMPOTENCY_KEY_CONFLICT`다.
3. 새 mutation은 부모를 재조회한 뒤 Check-in → 현재 revision의 최신 Safety → 최신 Barrier → 대상 Plan
   순서로 잠근다. `populate_existing`으로 잠금 대기 뒤 변경을 다시 읽는다.
4. Plan이 COMPLETED가 아니면 `409 ACTION_PLAN_STATE_CONFLICT`. ACTIVE 및 사용자/자동 CANCELLED에 응답하지 않는다.
   완료 이력에 대한 평가이므로 현재 Check-in/최신 Safety/Barrier의 일반 Support 조건은 재검증하지 않는다.
5. Plan 잠금 뒤 Follow-up을 잠근다. 현재 응답 없으면 기대 revision은 0이다. 불일치는
   `409 ACTION_PLAN_FOLLOWUP_REVISION_CONFLICT`다.
6. 최초는 row 1개/revision 1, 정정은 동일 row/revision +1과 audit를 기록한다.
   새 key와 정확한 revision으로 같은 응답을 다시 제출해도 revision +1과 audit를 기록한다.
7. audit는 from/to response·revision, 인증 사용자 changed_by와 서버 UTC changed_at을 저장한다.
   평가 변경·audit·암호화 멱등 snapshot을 같은 transaction에 저장한다. 실패 시 전부 rollback한다.

Check-in/Safety 정정과 같은 Check-in lock으로 직렬화한다. 완료 Plan은 자동 취소하지 않는 기존 기준을
유지한다. 동일 key 동시 요청은 최초 결과 하나를 재현하고, 서로 다른 key의 동일 expected_revision 경쟁은
하나만 성공한다. 첫 제출의 row 없음 경쟁은 부모 Plan lock으로 보호한다.

실패 응답은 멱등 snapshot으로 남기지 않는다. 성공 replay는 과거 제출 결과이며, 이후 정정된 현재값은 GET으로
조회한다. 요청 key·의료 원문을 일반 로그에 남기지 않는다. snapshot 크기 초과는
`503 IDEMPOTENCY_RESPONSE_TOO_LARGE`이며 domain 변경도 rollback한다.

## 오류

| HTTP | code |
| --- | --- |
| 400 | IDEMPOTENCY_KEY_REQUIRED / IDEMPOTENCY_KEY_INVALID (POST) |
| 401 | 기존 인증 오류 |
| 404 | ACTION_PLAN_NOT_FOUND |
| 409 | ACTION_PLAN_STATE_CONFLICT / ACTION_PLAN_FOLLOWUP_REVISION_CONFLICT / IDEMPOTENCY_KEY_CONFLICT (POST) |
| 422 | VALIDATION_FAILED |
| 503 | IDEMPOTENCY_RESPONSE_TOO_LARGE (POST) |

## #139 소비 인계

- Plan GET의 COMPLETED 확인 후 평가 UI를 제공한다. 실제 제출 가능 여부는 POST에서 다시 검증한다.
- GET의 `data=null`은 미응답이고 expected_revision=0이다. 응답이 있으면 그 revision을 사용한다.
- 평가 선택·정정은 명시적 사용자 행동으로만 제출한다. 나중에/화면 진입/조회는 mutation을 호출하지 않는다.
- 동일 요청 재시도는 같은 key/body, 응답을 수정한 새 제출은 새 key를 사용한다.
- revision 409는 GET 재조회 후 사용자가 다시 확인하도록 한다. 이전 선택을 자동 재제출하지 않는다.
- 응답이 NOT_HELPED여도 복약 상태·지원 계획·새 Barrier를 자동 생성/변경하지 않는다.
- 이 PR에는 Frontend 코드 변경이나 실제 브라우저 인수가 없다.

합성 예시: [fixture](../../../tests/fixtures/post_mvp_1/track_c/followup-v1.json).
검증 기록: [follow-up 검증](../../testing/track-c-followup-194.md).
