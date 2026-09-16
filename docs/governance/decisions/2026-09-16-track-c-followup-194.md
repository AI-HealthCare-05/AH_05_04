# PD-194-2 — 완료한 ActionPlan의 Follow-up 저장·조회·정정

- 상태: 2026-09-16 사용자 명시 제품 결정 반영. HTTP/DTO·오류·잠금 구체화는 구현 및 책임 리뷰 대상.
- 구현 담당: 권가빈 (@hazelnutflavoured).
- 단일 책임 리뷰어: 김지혜 (@Jye-rookie).
- 영향 영역: Track C Backend·API/DTO·Transaction·Security·Frontend 소비 계약.
- 배정 근거: 사용자가 #194 후속 구현과 김지혜 책임 리뷰를 지정하고 아래 상태·이력 정책에 동의했다.
- 원본 Issue: [#194](https://github.com/AI-HealthCare-05/AH_05_04/issues/194). #139 Frontend는 별도 진행한다.

## 문제와 제품 결정

지원 계획의 생성·완료·취소 뒤 사용자가 그 계획이 도움이 됐는지 기록하는 API가 없었다.
기존 `action_plan_followup`과 `action_plan_followup_audit` 테이블을 사용해 응답과 정정 이력을 연결한다.

1. `COMPLETED` 계획에만 `HELPED`, `NOT_HELPED`, `NOT_SURE` 응답 제출·정정을 허용한다.
2. 완료 후 Check-in·Safety·Barrier가 바뀌어도 완료 계획의 과거 평가와 정정을 허용한다.
3. 평가가 치료 효과·인과관계·새 Barrier를 뜻하지 않는다. 복약 기록·일정·Plan 상태를 바꾸지 않는다.
4. 나중에 응답하기는 쓰기 없음이다. `LATER` enum이나 미응답 row를 만들지 않는다.
5. 현재 응답과 revision을 조회해 화면 재진입·충돌 복구·정정에 사용한다. 자동 응답·추천·Provider 호출은 없다.

## 계약 구체화

기존 target의 `POST /api/v1/support-action-plans/{id}/followups`에 제출·정정을 연결하고,
같은 경로에 `GET`을 추가한다. 기존 Plan GET/PATCH 응답은 바꾸지 않는다.

POST는 `response`, strict nonnegative integer `expected_revision`만 받는다. 처음에는 0,
이후에는 조회한 follow-up revision을 제출한다. 최초 revision은 1이며 성공한 정정은 1씩 증가한다.
같은 답을 새 key와 현재 revision으로 명시 재제출해도 하나의 제출로 기록하고 audit를 남긴다.
네트워크 재전송은 기존 key를 사용해 최초 성공 응답을 재현한다.

GET은 소유 Plan에 평가가 없으면 `200 {"data": null}`을 반환한다. Plan이 없거나 타인 소유이면
동일한 404다. POST의 상태 충돌은 `ACTION_PLAN_STATE_CONFLICT`, 평가 revision 충돌은
`ACTION_PLAN_FOLLOWUP_REVISION_CONFLICT`다. 세부 필드·순서는 [후속 계약](../../contracts/proposed/track-c-followup-api-194.md)을 따른다.

기존 Check-in → Safety → Barrier → Plan 잠금 뒤 Follow-up을 잠근다. Plan 잠금은 첫 제출의
빈 row 경쟁도 직렬화한다. 평가 수정·audit·암호화 멱등 snapshot은 같은 transaction이며
실패하면 함께 rollback한다. 별도 migration·DB trigger·RLS·queue는 추가하지 않는다.

## 범위와 승인 경계

이 결정은 #194 전체 완료, Constraint/RAG Handler 확대, #139 화면 인수 또는 Production 승인이 아니다.
계약은 책임 리뷰 승인·구현/검증 증빙을 갖추기 전 `proposed/`에 둔다.
Track C/F 및 공통 Privacy Production 공개 게이트는 유지한다. 기존 승인 최소 문구·의료 규칙을 수정하지 않는다.
GitHub #194 상단의 Follow-up 후속 구현 항목에 이번 범위와 담당·책임 리뷰어를 기록했다.
이전 구현의 담당·리뷰 이력은 보존하며 이번 후속 배정과 구분한다.
