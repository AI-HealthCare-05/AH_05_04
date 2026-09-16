# PD-193 — Safety·Barrier HTTP 구체화 v1

- 날짜: 2026-09-15
- 상태: Proposed / 담당 리뷰 전. Approved Freeze v4를 재승인한 문서가 아니다.
- 구현 소유자 권가빈 @hazelnutflavoured, 책임 리뷰어 @phina-io (Backend·Transaction·Security).
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

## 2026-09-15 정책·문구 초안 구체화

사용자가 NHS 기반 표와 한국어 문구의 검토안 작성을 요청했다.
[정본 초안](../../contracts/proposed/track-c-safety-policy-copy-193.md)에 증상 코드·선택 문구·근거,
응급 우선 규칙·고정 안내·국내 연락 경로·후보 버전·미실행 합성 검증 명세를 모았다.
최근 24시간 신경학적 신호와 증상 없음 확인의 의미 확장, 성인 SELF 데모 대상 제안은
기존 승인 계약에 포함된 것으로 간주하지 않는다. 제품·소비 계약·전문 안전 검토 항목이다.
초안 작성 요청은 판정표 승인이나 구현 요청으로 기록하지 않으며 API·설정은 변경하지 않았다.

## PD-193 revision 2 — 리뷰 반영

2026-09-15 @Jye-rookie의 PR #592 리뷰와 PM의 수정 요청을 반영한다.
Barrier expected_revision 충돌을 `409 BARRIER_RESPONSE_REVISION_CONFLICT`로 분리한다.
`CHECKIN_FLOW_STALE`은 Check-in 상태·revision 불일치에 유지한다. HTTP·DB 스키마 변경은 없다.
이는 기존 승인 Freeze에 있었던 오류가 아니라 이 구현 PR에서 제안·리뷰하는 delta이며,
API 계약·OpenAPI 설명·동시성 및 API 회귀 검증을 함께 갱신한다.
POST Safety의 200/data 응답은 저장소 선례와 기존 멱등 snapshot 의미에 맞춰 유지한다.
구현 담당은 실제 인계받은 권가빈으로 정정하고 책임 리뷰어는 @phina-io로 유지한다.
Draft 사유는 임상 판정표·환자용 문구의 검토 및 연결 대기이며 CI 미실행 때문이 아니다.

## 2026-09-16 후속 범위

사용자의 내부 합성 7일 데모 구현 요청은 [revision 3](2026-09-16-track-c-internal-demo-193.md)에
별도로 기록한다. 의료 검토 대기는 실제 사용자 공개에 유지하며, 기본 OFF인 Local 데모만
계정·시간창 제한과 별도 버전으로 구현한다. 이 문서의 과거 검토·승인 기록을 소급 변경하지 않는다.
