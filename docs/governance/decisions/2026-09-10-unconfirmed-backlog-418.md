# PD-418 — UNCONFIRMED backlog 조회 제안

- 상태: Proposed · 미등록 후보 승인 확인, 등록 변경 Backend/Frontend 재승인 대기
- 구현 상태: #426의 조회 구현을 실제 v1 router에 연결한 리뷰용 변경. 최신 #202(#456)와 실제 앱 HTTP/OpenAPI 통합 검증 대상.
- 구현: 권가빈 (`hazelnutflavoured`), #418의 Backend 담당 변경 기준
- 책임 리뷰: 송은영 (`phina-io`, Backend/API/SELF ownership), 남한솔 (`solia142`, Frontend 소비 계약)
- 이슈: [#418](https://github.com/AI-HealthCare-05/AH_05_04/issues/418)
- 정규 계약: [UNCONFIRMED backlog v1](../../contracts/proposed/unconfirmed-backlog-v1.md)

## 문제와 제안

B3의 `list_unconfirmed_owned`는 내부 조회 경계만 제공한다. B4의 URL·DTO·pagination은 미정이므로 Approved v4에서 정해진 것으로 취급하지 않는다. 본 Decision은 별도 필터 없는 SELF 소유 UNCONFIRMED 목록, 기본 20/최대 100개, 예정 시각과 Check-in ID 오름차순 keyset pagination을 제안한다.

Cursor는 마지막으로 반환된 Check-in UUID다. 소유권을 확인한 저장 행의 불변 예정 시각과 ID로 위치를 복원한다. Cursor 행이 보완되어 TAKEN/NOT_TAKEN으로 바뀌어도 위치를 유지하므로 offset 방식에서 발생하는 건너뛰기를 피한다. 없는 cursor와 타인 cursor는 같은 404 `CHECKIN_CURSOR_NOT_FOUND`로 처리한다. 이 오류 코드는 본 제안의 신규 코드다.

이전 처방 버전의 snapshot과 occurrence를 반환한다. active version, 활성 schedule, 최근 14일로 제한하지 않는다. GET은 Check-in 생성·보완·Audit·로그인 상태를 변경하지 않는다. 보완은 기존 Check-in PUT과 revision 검사를 재사용하며 별도의 batch mutation을 추가하지 않는다.

## 범위와 승인 전 경계

새 DB schema, 상태, scheduler, 의료 판단, Frontend 구현은 없다. #202 PUT, #203 알림, #417 schedule contract 파일을 변경하지 않는다. 새 조회 메서드는 기존 repository에 추가하고 새 DTO/service/router를 분리한다.

### 승인 증빙과 등록 변경의 병합 조건

#426은 미등록 상태로 병합됐다. `564dfb0a`의
[Frontend 승인](https://github.com/AI-HealthCare-05/AH_05_04/pull/426#pullrequestreview-5168490711)과
[Backend 승인](https://github.com/AI-HealthCare-05/AH_05_04/pull/426#pullrequestreview-5168848890)은
미등록 후보 구현·DTO·cursor 복구에 대한 승인이다. 실제 API 등록이나 Current 승격,
Production 공개 승인으로 확대하지 않는다. 과거 등록 시도의 승인·철회 이력은
[#426 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/426/files)에 남아 있다.

2026-09-11 사용자 요청으로 최신 `develop`의 #456을 반영하여 실제 v1 등록과
실제 앱 GET→PUT→목록 제외·페이지 이동·날짜별 Check-in 재조회 검증을 리뷰 가능한 변경으로
준비한다. #426 Backend blocker의 “등록을 포함한 HEAD에서 두 책임 리뷰어 승인 전 병합 금지”
조건을 적용하며, 이전 승인을 등록 변경 승인으로 간주하지 않는다.

등록 변경 PR은 Draft로 두고 송은영·남한솔이 등록 HEAD와 계약을 재승인해야 한다.
승인 후 같은 구현 PR에서 이 Decision의 승인 증빙과 계약 상태·인덱스·참조를 정렬하고
`docs/contracts/current/`로 이동한다. Proposed 복제본을 남기지 않는다. 이후 필수 CI·blocking
comment 해소를 확인하고 병합한다. 승인 전 Current 승격·#418 종료를 하지 않는다.
#138 Frontend 화면/소비 검증과 Privacy·Track C/F Production gate는 별도로 유지한다.

## Pagination 한계

전체 페이지는 시점 고정 snapshot이 아니다. 후속 GET은 그 시점의 UNCONFIRMED만 반환하며, cursor 앞에 나중에 생성된 과거 기록은 처음부터 재조회해야 보인다. `404 CHECKIN_CURSOR_NOT_FOUND`에서 Backend는 자동 fallback하지 않는다. Frontend가 cursor와 페이지 누적 상태를 초기화하고 `cursor` query parameter를 생략해 같은 `limit`으로 최초 페이지부터 재조회한다. 성공 응답으로 목록을 교체하며, 오류를 빈 목록이나 보완 완료로 처리하지 않는다. 미존재·삭제·소유권 불일치 모두 동일한 [Frontend 복구 절차](../../contracts/proposed/unconfirmed-backlog-v1.md#cursor-404의-frontend-복구)를 따른다. 처방과 계정의 기존 삭제 정책을 변경하지 않는다.
