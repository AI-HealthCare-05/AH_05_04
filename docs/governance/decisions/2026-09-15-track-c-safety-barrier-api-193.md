# PD-193 — Safety·Barrier HTTP 구체화 v1

- 날짜: 2026-09-15
- 상태: Proposed / 담당 리뷰 전. Approved Freeze v4를 재승인한 문서가 아니다.
- 구현 소유자 @Jye-rookie, 책임 리뷰어 @phina-io (Backend·Transaction·Security).
- Frontend 소비 의견 @solia142, 제품 수용 @hazelnutflavoured.

## 범위

사용자 요청에 따라 #580 병합 develop (`58aa7286`) 위에서 #193을 구현한다.
기존 두 target route·Safety 필드·enum·잠금 순서를 유지한다. 신규 migration은 필요하지 않다.
HTTP status/envelope, Barrier 응답 필드 및 revision 충돌 오류의 구체화,
증상 코드 문법·크기 제한은 [제안 계약](../../contracts/proposed/track-c-safety-barrier-api-193.md)에서
명시적으로 리뷰한다. 기존 승인 target에 새 세부 조건을 승인된 것처럼 추가하지 않는다.

## 정책 자료

빈 증상 목록의 ROUTINE은 기존 target에 정의돼 있다. 비어 있지 않은 증상 판정표는
아직 없으므로 현재 구현은 UNKNOWN 차단에 한정한다. 사용자가 NHS를 참고 자료로 제시했다.
[NHS 아나필락시스](https://www.nhs.uk/conditions/anaphylaxis/)와
[응급 연락 안내](https://www.nhs.uk/nhs-services/urgent-and-emergency-care-services/when-to-call-999/)를
2026-09-15 확인했다. 이 자료를 바탕으로 앱 전용 판정표·고정 문구·버전·국내 경로를
별도 확정해야 한다. NHS 참고 링크는 제품 규칙 승인이나 외부 승인 증빙이 아니다.

API 기반 구현·합성 테스트와 임상 Safety 정책 완성·배포 검증을 구분한다.
Track F Safety Intake, #194 지원 선택, #195 Check-in 정정 adapter 및 publication 조건은 바꾸지 않는다.
