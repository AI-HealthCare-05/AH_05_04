# Track B 명시적 식후 시간 후보 v1 — #670

상태: **Local 구현 검토안 / 담당·전문 리뷰 대기**. 현재 공개 계약으로 승격하지 않는다.
근거: [PD-670](../../governance/decisions/2026-09-17-explicit-schedule-recommendation-670.md).
구현 담당 권가빈, 단일 책임 리뷰어 송은영 (Backend/API·데이터·Frontend 소비·안전).
정현우·남한솔 전문 검토 증빙은 별도로 필요하다.

## 후보 계산

`POST /api/v1/prescription-version-medications/{prescription_version_medication_id}/schedule-recommendation`

인증 및 SELF 소유 확인. `ENV=local` 이외에는 `404 NOT_FOUND`. 멱등 키 없이 읽기 계산만 수행한다.
ID는 확정된 PrescriptionVersionMedication이며 요청 본문에 OCR·처방 문구를 받지 않는다.
활성 처방 version·SELF 소유권·snapshot 무결성을 행 잠금 없는 SELECT로 확인한다.
조회 뒤 처방이 바뀔 수 있으므로 실제 저장 시 기존 mutation 잠금 안에서 다시 확인한다.
새 일정·occurrence·audit·idempotency 행을 만들지 않는다.
기존 일정이 있는 약도 계산 자체는 가능하지만 추천을 이용한 덮어쓰기는 허용하지 않는다.

요청 (추가 필드 금지):

```json
{"meal_end_times":{"DINNER":"19:30"},"same_times_every_day":true}
```

- `meal_end_times`: 필수 object, 최대 3개. key는 BREAKFAST/LUNCH/DINNER, 값은 24시간 `HH:MM`.
  필요한 식사가 누락되면 추천 불가 응답. 사용하지 않은 유효 식사 key는 계산에 영향 없음.
- `same_times_every_day`: 필수 true. 불규칙·요일별 입력은 미지원.
- 기준은 KST의 같은 일자 식사 종료 시각. 생활 시간 window를 대용하지 않는다.

200 응답:

```json
{"data":{"prescription_version_medication_id":"22222222-2222-4222-8222-222222222222","prescription_version_id":"33333333-3333-4333-8333-333333333333","rule_version":"explicit-after-meal-v1","timing_text":"저녁 식후 30분","local_times":["20:00"],"reason":"EXPLICIT_AFTER_MEAL"}}
```

모든 응답 필드는 필수이며 timing_text만 nullable. 추천 불가 시 local_times=[]이고 이유를 반환한다.
시각은 중복 없이 오름차순이다. 공통 no-store·trace-id 적용.

| reason | 의미 |
| --- | --- |
| EXPLICIT_AFTER_MEAL | 완전 일치하는 확정 문구의 간격과 식사 종료 시각으로 계산 |
| UNSUPPORTED_INSTRUCTION | 원문 없음·지원 문법 밖·동일 식사 중복 |
| FREQUENCY_MISMATCH | 식사 개수와 확정 하루 횟수 불일치 또는 횟수 없음 |
| MISSING_MEAL_END | 해당 식사 종료 시각 누락 |
| DAY_BOUNDARY | 계산이 다음 날 00:00 이후로 넘어감 |
| DUPLICATE_TIME | 서로 다른 식사로 계산한 최종 시각 중복 |

문법: trim 후 `(아침|점심|저녁)` 목록(구분자 `,` 또는 `·`) + 공백 + `식후` + 공백 + `N분` 전체 일치.
N은 1~999이며 앞에 0을 붙이지 않는다. parser 한계이지 의료 권장 간격이 아니다.
원문에 없는 간격·횟수·식사 종류를 추론하지 않고 추가 조건이 있으면 거부한다.
한 식사라도 실패하면 부분 후보를 제공하지 않는다.

## 명시적 저장

기존 `PUT /api/v1/prescription-version-medications/{id}/schedule`에 선택 nullable
`recommendation_context`를 추가한다. 기존 필드·응답·멱등 키 요구는 유지한다.
context는 위 요청 필드 + 필수 `rule_version`(길이 1~80)이며 추가 필드는 금지한다.

- 없거나 null이면 기존 수동 저장. 기존 멱등 fingerprint에도 context를 넣지 않아 호환성을 유지한다.
- 있으면 Local guard → SELF 조회 → 기존 멱등 성공 replay → 재계산 검증 → 기존 mutation transaction.
- rule_version이 현재 `explicit-after-meal-v1`과 다르거나 후보 없음·최종 local_times 불일치·
  expected_revision != 0이면 `409 SCHEDULE_RECOMMENDATION_CONFLICT`.
- 입력 순서는 기존 DTO처럼 정렬한다. 최종 값만 같으면 순서는 무관하다.
- 처방 활성 version·schedule revision 검사는 기존 mutation의 소유권 잠금 안에서 시행하며
  `409 PRESCRIPTION_VERSION_CONFLICT` / `409 SCHEDULE_REVISION_CONFLICT`를 유지한다.
- 소유 아님·존재하지 않음은 `404 PRESCRIPTION_MEDICATION_NOT_FOUND`, 인증 실패 401,
  형식 오류는 기존 `422 VALIDATION_FAILED`. 처방·입력 원문을 오류 message에 넣지 않는다.
- 동일 성공 멱등 키 replay는 재저장하지 않는다. context를 바꾸면 새 논리적 요청·키를 쓴다.
- 추천 저장으로 기존 일정·횟수·용량·지시를 변경하지 않는다. 기존 수동 편집 경로는 유지한다.

후보·입력은 별도 DB에 저장하지 않는다. 최종 일정은 기존 USER_CONFIRMED source와 audit를 사용한다.
context는 기존 HMAC 멱등 fingerprint에 포함되지만 응답 snapshot이나 일정 audit에 원문 추가 저장하지 않는다.
DB schema·migration 변경은 없다. 재계산은 약학적 검증이나 Source 기반 상호작용 검사를 대체하지 않는다.

## Frontend

DEV 일정 생성에서만 사용. 원문·이유·필수 조건을 표시하고 입력 → 계산 → 적용 → 사용자 저장을 구분한다.
식사 시각/매일 동일 확인 변경 시 후보와 적용된 시각을 지운다. 늦게 도착한 이전 응답은 무시한다.
직접 수정 시 context를 유지해 서버가 검증하며, 명시적으로 직접 입력으로 전환한 경우에만 context를 제거한다.
기존 일정 편집에는 후보 UI를 제공하지 않는다. 저장 실패 시 입력·동일 멱등 키를 유지한다.
Production build는 UI를 제거하고 Backend도 non-local 요청을 차단한다.
