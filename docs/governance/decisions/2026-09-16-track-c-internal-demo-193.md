# PD-193 revision 3 — Local 7일 합성 데모 정책

- 상태: 사용자 구현 요청 수용 / Proposed 구현 검토 대상. 의료 승인·Current 승격·배포 승인 아님.
- 날짜: 2026-09-16
- 구현 담당: 권가빈 (@hazelnutflavoured), #193 기존 인계 범위.
- 단일 책임 리뷰어: @phina-io — Backend 설정·API 오류·Transaction·Security.
- 영향 영역: Backend Safety 분기와 Frontend가 소비하는 message/copy/source 값.
  Frontend 소비 확인은 @solia142, 의료·Source 공개 증빙은 별도이며 현재 미확보다.

## 요청과 결정

사용자는 의료 검토 완료를 이번 7일 데모 구현의 선행조건에서 제외하고,
합성 데이터를 사용하는 내부 데모로 구현하도록 요청했다. 기존 외부 승인 게이트는
이미 미승인 상태의 비공개 synthetic 구현을 허용한다. 공개 조건은 바꾸지 않는다.

기존 API·DB shape·enum·revision·잠금 순서를 유지한다. Local에서 명시적으로 설정한
합성 계정과 최대 7일 시간창에만 별도 정책을 적용한다. 런타임 Source 승인을 위조하거나
NHS가 앱을 승인했다고 표현하지 않는다. 위험을 낮추는 ROUTINE 매핑은 추가하지 않는다.

- 빈 목록은 기존 foundation 결과·버전을 그대로 사용한다. 최근 24시간 확인으로 의미를 확장하지 않는다.
- 비어 있지 않은 목록의 E01~E11/U01/X01/X02 분기는 기존 정책 초안에서 가져온다.
- 응급 우선, 다음 긴급, 나머지 UNKNOWN. 모든 non-ROUTINE에서 기존 Barrier·Support 차단과 Plan 취소를 유지한다.
- 데모 결과는 `DEMO_` message code와 전용 copy/source version으로 구분한다.
- 새 오류 `503 SAFETY_DEMO_UNAVAILABLE`: 활성화된 데모의 계정·환경·기간·artifact 검증 실패.
  실패는 저장 전 rollback하며 오류에 계정·증상·파일 경로를 포함하지 않는다.
- 이미 성공한 같은 key/fingerprint의 요청은 기존 암호화 snapshot을 재현한다.
  만료나 계정 allowlist 변경은 과거 성공 결과를 재판정하지 않는다. 새로운 mutation은 거부한다.
- 일반 공개 모드, 새로운 환자용 API, Frontend 화면 변경 및 #631 평가는 범위 밖이다.

Rule·Copy·Source를 하나의 hash-pinned artifact로 결속한다. DB의 copy/source 쌍이 유일하게
해당 rule version을 가리킨다. 과거 artifact와 assessment는 새 버전으로 덮어쓰지 않는다.
증상 코드와 한국어 안내는 [API 제안 계약](../../contracts/proposed/track-c-safety-barrier-api-193.md)의
데모 절과 [정책 검토안](../../contracts/proposed/track-c-safety-policy-copy-193.md)을 따른다.

#196은 별도 소유 범위이며 [준비 상태 확인](../../testing/track-c-rag-readiness-196.md)에 기록한다.
공용 RAG 계약의 미확정 부분을 이 Decision으로 승인하거나 대체하지 않는다.
