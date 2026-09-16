# Track C 일정 변경·외출 상황 선택 — #194

- 상태: Proposed / 구현·리뷰 대상. 병합된 runtime 또는 외부 공개 승인 아님.
- 구현 담당: 권가빈. 단일 책임 리뷰어: 김지혜.
- 영향: Backend API/DTO·Support 선택·멱등성, Frontend #139의 선택·채택·완료 흐름.
- 근거: [PD-194-3](../../governance/decisions/2026-09-16-track-c-travel-situation-194.md).

## GET 및 Plan 생성

`GET /api/v1/barrier-responses/{id}/supports` query와 `POST /api/v1/support-action-plans` body에
선택적 `travel_situation`을 받는다. POST는 null도 허용하며 생략과 동일하다. GET은 query 생략이 기본이다.

| 값 | 조건 | 제안 |
| --- | --- | --- |
| SCHEDULE_CHANGED | ANSWERED + SCHEDULE_OR_TRAVEL | REMINDER_SETUP |
| MEDICATION_NOT_WITH_ME | ANSWERED + SCHEDULE_OR_TRAVEL | ROUTINE_OR_TRAVEL_PLAN |
| 생략/null (POST) | 기존 Barrier 조건 | 기존 priority ASC, support_code ASC의 첫 1개 |

알 수 없는 값과 다른 Barrier에서의 선택은 `422 VALIDATION_FAILED`다.
SELF 404와 최신 Safety·Check-in·Barrier 검증은 기존 순서를 유지한다.
후보를 먼저 제한한 뒤 정렬한다. 후보가 없으면 기존 `NO_ELIGIBLE_SUPPORT`와 빈 목록이다.
새 route, Barrier enum, 오류 code, response 필드, DB migration은 없다.

Plan 생성은 같은 선택으로 후보를 재계산하며 다른 support_code는 `409 SUPPORT_NOT_OFFERED`다.
GET은 저장하지 않으므로 사전 조회 토큰이 아니다. Plan 생성에서 선택과 제안을 함께 재검증한다.
선택은 멱등 fingerprint에 포함하고 null/생략은 제외하여 기존 성공 요청의 재시도를 보존한다.
같은 key의 선택 변경은 `409 IDEMPOTENCY_KEY_CONFLICT`다. 기존 잠금·원자 snapshot 저장을 유지한다.

## 저장 및 과거 데이터

선택에 대응하는 support_code와 rule/copy/config snapshot을 기존 Plan에 저장한다.
Barrier 응답을 추가 정정하거나 선택을 별도 column에 중복 저장하지 않는다.
Rule·Copy 2026-09-16.1을 사용하며 기존 2026-09-15.1 파일과 과거 복원 allowlist를 보존한다.
기존 6개 support_code·priority·static parameters는 유지하고 약 챙기기 Copy만 새 의미를 구체화한다.

## Frontend 흐름과 완료

#629 기반 개발 전용 화면에서 일정 변경·외출 선택 후 두 상황을 radio로 선택한다.
기본 선택은 없으며 선택 전 Offer 조회·Plan 생성은 없다. 서버가 반환한 Offer 하나를 표시하고 별도 확인 후 채택한다.
GET 실패 재시도는 같은 상황을 사용하며 Barrier를 다시 저장하지 않는다.

- 생활 일정 변경: Plan의 서버 결속 약 ID로 기존 일정 화면을 연다. 확인/저장 뒤 명시 완료.
- 약 미지참: ‘다음 외출 전 약 챙기기’ 계획. ACTIVE 생성·조회만으로 완료하지 않는다.
  실제 약을 챙겼다는 사용자 확인 뒤 기존 COMPLETED PATCH를 사용한다. 물리적 준비 여부의 서버 자동 검증은 없다.
- 기존 Copy 버전의 과거 계획에는 새 완료 문구를 소급 적용하지 않는다.
- 취소·Safety 차단·정정 무효화와 Frontend 개발 전용 공개 경계를 유지한다.

검증: `backend/app/tests/track_c/test_track_c_travel_support.py`,
`frontend/tests/TrackCPage.test.tsx`의 상황 선택·재시도·완료 확인 사례.
최종 화면 소비 검토·책임 리뷰 승인과 공개 승인은 별도다.
