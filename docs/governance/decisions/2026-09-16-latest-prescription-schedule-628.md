# PD-628-20260916: 일정 설정의 최신 처방 범위

- 상태: 요청자 범위 확인 (2026-09-16), 구현 리뷰 대기. 담당 리뷰어 승인·운영 배포 증거 아님.
- 추적: [#628](https://github.com/AI-HealthCare-05/AH_05_04/issues/628)
- 구현 담당: 권가빈 (`hazelnutflavoured`). 단일 책임 리뷰어: 송은영 (`phina-io`).
- 영향 영역: Backend 조회·SELF 소유권·Frontend 소비 계약. Frontend 전문 확인은 남한솔 영역이며 추가 필수 PR 리뷰어가 아니다.
- 정본: [Track B 일정 API v1](../../contracts/proposed/track-b-schedule-api-v1.md).

## 문제와 선택

latest는 최신 처방 한 건을 반환하지만 일정 조회는 모든 처방의 활성 version 약을
반환하여 Frontend의 ID 기반 연결이 차단된다. current는 처방별 version 상태이다.
보고된 운영 응답의 추가 3개 ID 소속은 확인하지 않았으며, 합성 데이터로 같은
조회 범위 불일치를 재현한다.

요청자는 두 대안 중 **최신 처방 한 건으로 제한**을 선택했다. schedule_items와
schedule_status의 입력을 latest와 같은 SELF 처방 선택으로 제한한다. 생성 시각
동률은 id 내림차순으로 결정한다. 같은 처방의 비활성 version은 계속 제외한다.

이전 처방의 schedule을 취소하거나 occurrence·Check-in 이력을 삭제하지 않는다.
기존 PUT/PATCH version 유효성, scheduler, 알림 규칙, 과거 occurrence의 약 snapshot
조회도 유지한다. 최신 목록에서 제외됐다는 사실이 과거 처방의 의학적 중단 판단이나
일정 취소를 의미하지 않는다. 추가 생명주기 변경은 이 Decision 범위 밖이다.

## 검증 및 승인 경계

이전 별도 처방 3개 + 최신 처방 2개, 동일 생성 시각의 ID 선택, 다른 사용자의 더
최신 처방 제외, 같은 처방의 비활성 version 제외를 검증한다. 최신 일정 저장과
occurrence 생성, 이전/최신 occurrence의 원래 약 조회와 Check-in을 함께 확인한다.

공유 DTO 필드·enum·오류·DB schema·쓰기 transaction·공개 게이트는 바꾸지 않는다.
요청자의 제품 범위 확인은 책임 리뷰어의 코드 승인이나 Production 검증을 대체하지 않는다.
