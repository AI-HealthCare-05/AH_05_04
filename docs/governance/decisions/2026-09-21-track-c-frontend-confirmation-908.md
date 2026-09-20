# PD-908 — Track C C-03~C-06 Frontend 사용자 확인 흐름

- 상태: PR #908 구현·단일 책임 리뷰 대상. Backend API/DTO 변경 및 외부 공개 승인 아님.
- 구현 담당: 남한솔 (`@solia142`)
- 단일 책임 리뷰어: 권가빈 (`@hazelnutflavoured`)
- 관련: #139, PR #908
- 상위 근거:
  - `docs/contracts/proposed/track-c-support-plan-api-194.md`
  - `docs/contracts/current/track-c-rule-based-personalization-718.md`
  - `docs/governance/decisions/2026-09-17-track-c-rule-based-personalization-718.md`

## 결정

Backend가 승인한 Support·Copy·질문을 그대로 소비하면서 Plan 생성 전 사용자 확인을
C-03~C-06 네 단계로 분리한다. 새 API·DTO·enum·의료 문구는 만들지 않는다.

| 단계 | 의미 | 서버 mutation |
| --- | --- | --- |
| C-03 Offer | 서버가 반환한 도움 후보 선택·확인 | 없음 |
| C-04 Configure | 서버가 허용한 질문 선택 등 local draft 구성 | 없음 |
| C-05 Confirm | 선택한 Support·질문과 승인 confirmation_prompt 재확인 | 없음 |
| C-06 Plan Review | 저장 직전 최종 계획 확인 | 최종 CTA 전 없음 |

`POST /api/v1/support-action-plans`는 C-06의
`이 계획을 저장하고 시작하기`를 사용자가 직접 누른 경우에만 호출하고
기존 `confirmed=true` 계약을 그대로 제출한다.

## 되돌아가기·재확인

- C-04 `이전으로` → C-03.
- C-05 `내용 수정하기` → C-04이며 이전 확인은 무효화한다.
- C-06 `내용 다시 확인하기` → C-05이며 이전 확인은 무효화한다.
- C-03에서 Support를 재선택하면 질문 선택과 확인 상태를 초기화한다.
- 질문을 수정한 경우 C-05와 C-06을 다시 통과해야 한다.
- 어느 되돌아가기 경로에서도 Plan POST를 자동 호출하지 않는다.

서버 `support_copy.confirmation_prompt`는 C-05에서 그대로 표시한다.
`primary_label` 필드는 Backend 계약상 유지하지만 C-03~C-06의 단계 이동/최종 저장 CTA는
Frontend Current Source의 workflow label을 사용한다. 이는 Backend payload나 의료 문구를
변경하는 의미가 아니다.
