# PD-867 — Track C ActionPlan 목록·재진입 API

- 상태: Current 계약의 구현 Decision — PR #873에서 코드·계약·증빙을 함께 반영한다.
- 범위·배정 근거: #867 실천 계획 목록·재진입 기능. 구현 담당 송은영, 책임 리뷰어 권가빈.
- Issue: [#867](https://github.com/AI-HealthCare-05/AH_05_04/issues/867). 기반 계약: [ActionPlan lifecycle v1](../../contracts/current/track-c-plan-lifecycle-617.md).

## 문제와 결정

최신 런타임에는 ActionPlan 생성·상세 조회·완료/취소가 이미 존재하지만, 사용자가 앱을 다시 열었을 때 저장된 plan id를 서버에서 다시 찾는 목록 API가 없다. Track C Frontend는 건강정보와 mutation key를 브라우저 저장소에 보존하지 않으므로, 재진입은 서버 목록 API로 해결한다.

`GET /api/v1/support-action-plans`를 추가한다. 이 API는 SELF 소유 ActionPlan만 최신 생성순으로 반환하며, 목록에 필요한 최소 필드만 노출한다. 상세 snapshot, parent barrier id, rule/copy version, action config는 기존 `GET /api/v1/support-action-plans/{id}`에서만 조회한다.

## 영향과 검토

Backend API/DTO/Repository·Frontend 소비 계약이 영향 범위다. 이 PR은 Backend 목록 API 1단계이며 Frontend 목록 화면과 메뉴 진입점은 후속으로 남긴다. 기존 생성, 단건 상세 조회, 완료/취소, follow-up, DB schema, idempotency, 공개 게이트는 변경하지 않는다.

검증은 OpenAPI operation id, 필드 required/nullable, 정렬, SELF 소유권, 타 사용자 미노출, no-store, idempotency row 미증가를 자동 테스트로 고정한다.
