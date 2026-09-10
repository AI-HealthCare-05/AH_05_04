# PD-418 — UNCONFIRMED backlog 조회 제안

- 상태: Proposed · Backend/Frontend 승인 대기
- 구현 상태: repository·service·DTO·미등록 router와 테스트 앱의 합성 HTTP 통합 테스트. 계약 승인 대기.
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

API route·DTO·오류는 공유 계약 변경이므로 두 담당 리뷰어의 계약 승인 전에는 `apis/v1/__init__.py`에 등록하지 않는다. `8d2909eb`의 선등록은 2026-09-10 Backend blocker에 따라 되돌렸다. 테스트 앱에서만 후보 backlog router를 등록해 병합된 #413 PUT 구현과 HTTP 통합을 검증하며, 실제 앱의 route 목록·OpenAPI 미노출 및 HTTP 404를 회귀 테스트로 고정한다.

Frontend의 `b213cc01` 승인과 과거 Backend 검토를 등록 변경의 공동 승인으로 간주하지 않는다. 계약 승인 후 등록과 실제 앱 검증을 추가하고 두 담당 리뷰어가 그 등록 HEAD를 승인하기 전에는 병합하지 않는다. Draft를 유지하며 Current 승격·#138 Frontend 소비 완료·#418 종료를 주장하지 않는다.

## Pagination 한계

전체 페이지는 시점 고정 snapshot이 아니다. 후속 GET은 그 시점의 UNCONFIRMED만 반환하며, cursor 앞에 나중에 생성된 과거 기록은 처음부터 재조회해야 보인다. `404 CHECKIN_CURSOR_NOT_FOUND`에서 Backend는 자동 fallback하지 않는다. Frontend가 cursor와 페이지 누적 상태를 초기화하고 `cursor` query parameter를 생략해 같은 `limit`으로 최초 페이지부터 재조회한다. 성공 응답으로 목록을 교체하며, 오류를 빈 목록이나 보완 완료로 처리하지 않는다. 미존재·삭제·소유권 불일치 모두 동일한 [Frontend 복구 절차](../../contracts/proposed/unconfirmed-backlog-v1.md#cursor-404의-frontend-복구)를 따른다. 처방과 계정의 기존 삭제 정책을 변경하지 않는다.
