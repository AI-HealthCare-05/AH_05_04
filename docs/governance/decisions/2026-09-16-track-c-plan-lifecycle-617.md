# PD-617 — Track C ActionPlan 조회·완료·취소

- 상태: Current 계약의 구현 Decision — PR #618에서 코드·계약·증빙을 함께 반영. 최종 책임 리뷰 승인·병합 대기.
- 범위·배정 근거: 2026-09-16 사용자 요청. 권가빈 구현, 김지혜 단일 책임 리뷰.
- Issue: [#617](https://github.com/AI-HealthCare-05/AH_05_04/issues/617), 선행 PR #608 / 상위 #194.

## 문제와 결정

Plan 생성 후 저장 상태를 다시 읽거나 사용자 완료·취소를 기록할 API가 없다.
단건 GET과 기존 목표 PATCH를 추가한다. 계약의 필드·오류·잠금·소비 경계는
[ActionPlan lifecycle v1](../../contracts/current/track-c-plan-lifecycle-617.md)에 정의한다.

상태는 ACTIVE → COMPLETED/CANCELLED만 허용한다. 설정 수정·재활성화가 없어 Plan revision을
새로 추가하지 않고 기존 Check-in 잠금과 Plan ACTIVE 검사로 단일 전환을 보장한다.
동일 key 성공 replay는 최초 응답을 보존하며 다른 key로 종료 상태를 다시 변경하면 409다.
완료는 최신 Flow 검증과 명시 사용자 확인을 요구하고, 취소는 stale 흐름에서도 허용한다.
REMINDER_SETUP은 일정 확인/저장 뒤 사용자 완료 확인이며 일정 변경을 대행하지 않는다.

## 영향과 검토

Backend·API/DTO·Transaction·Security·Frontend 소비 계약이 영향 범위다. 김지혜가 단일 책임 리뷰를 맡는다.
Frontend 구현은 포함하지 않으며 #139 소비자는 GET을 실행 허가로 사용하거나 PATCH를 자동 호출하면 안 된다.
기존 생성 응답·DB schema·#195 취소 adapter·의료 규칙·외부 Provider 및 공개 조건은 변경하지 않는다.
단일 상태 전환·동시 완료/취소·정정 경합·rollback·재전송·SELF 404를 통합 테스트한다.
Follow-up과 Plan 설정 수정은 별도 계약 범위로 남긴다.

## 책임 리뷰 반영 — 2026-09-16

김지혜의 [변경 요청](https://github.com/AI-HealthCare-05/AH_05_04/pull/618#pullrequestreview-5216913531)을 반영해
이 PR의 구현된 GET/PATCH 계약을 `docs/contracts/current/`로 이동하고 API·DB 문서·인덱스·#194 참조를 정렬한다.
구현 HEAD `f8e4986f`의 [CI](https://github.com/AI-HealthCare-05/AH_05_04/actions/runs/34991259236)에서 test·lint가 통과했다.
리뷰의 상태 정렬 요청을 최종 APPROVED로 기록하지 않는다. 병합 전 develop의 현재 동작이나 외부 공개 승인도 아니다.
이번 문서 변경은 API·DTO·enum·잠금 순서·공개 조건을 변경하지 않는다.
