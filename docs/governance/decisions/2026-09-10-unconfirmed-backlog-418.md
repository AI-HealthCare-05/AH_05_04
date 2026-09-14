# PD-418 — UNCONFIRMED backlog 조회 제안

- 상태: Proposed · #462 등록 구현 병합·승인 확인, Current 승격은 별도 검토 (2026-09-14 정리)
- 구현 상태: #426 후보 구현과 #462 실제 v1 등록·보완·페이지 이동·날짜별 revision 검증 병합 완료.
- 구현: 권가빈 (`hazelnutflavoured`), #418의 Backend 담당 변경 기준
- 상태·참조 정리 책임 리뷰어: 남한솔 (`solia142`) — Backend/API 등록 근거와 Frontend DTO·cursor·보완 소비 계약의 문서 정합성. 과거 Backend 검토 근거는 아래 별도 기록.
- 이슈: [#418](https://github.com/AI-HealthCare-05/AH_05_04/issues/418)
- 정규 계약: [UNCONFIRMED backlog v1](../../contracts/proposed/unconfirmed-backlog-v1.md)

## 최초 문제와 제안 (2026-09-10)

B3의 `list_unconfirmed_owned`는 내부 조회 경계만 제공한다. B4의 URL·DTO·pagination은 미정이므로 Approved v4에서 정해진 것으로 취급하지 않는다. 본 Decision은 별도 필터 없는 SELF 소유 UNCONFIRMED 목록, 기본 20/최대 100개, 예정 시각과 Check-in ID 오름차순 keyset pagination을 제안한다.

Cursor는 마지막으로 반환된 Check-in UUID다. 소유권을 확인한 저장 행의 불변 예정 시각과 ID로 위치를 복원한다. Cursor 행이 보완되어 TAKEN/NOT_TAKEN으로 바뀌어도 위치를 유지하므로 offset 방식에서 발생하는 건너뛰기를 피한다. 없는 cursor와 타인 cursor는 같은 404 `CHECKIN_CURSOR_NOT_FOUND`로 처리한다. 이 오류 코드는 본 제안의 신규 코드다.

이전 처방 버전의 snapshot과 occurrence를 반환한다. active version, 활성 schedule, 최근 14일로 제한하지 않는다. GET은 Check-in 생성·보완·Audit·로그인 상태를 변경하지 않는다. 보완은 기존 Check-in PUT과 revision 검사를 재사용하며 별도의 batch mutation을 추가하지 않는다.

## 구현 범위와 승인 이력

새 DB schema, 상태, scheduler, 의료 판단, Frontend 구현은 없다. #202 PUT, #203 알림, #417 schedule contract 파일을 변경하지 않는다. 새 조회 메서드는 기존 repository에 추가하고 새 DTO/service/router를 분리한다.

### 승인 증빙과 등록 변경의 병합 조건

#426은 미등록 상태로 병합됐다. `564dfb0a`의
[Frontend 승인](https://github.com/AI-HealthCare-05/AH_05_04/pull/426#pullrequestreview-5168490711)과
[Backend 승인](https://github.com/AI-HealthCare-05/AH_05_04/pull/426#pullrequestreview-5168848890)은
미등록 후보 구현·DTO·cursor 복구에 대한 승인이다. 실제 API 등록이나 Current 승격,
Production 공개 승인으로 확대하지 않는다. 과거 등록 시도의 승인·철회 이력은
[#426 리뷰](https://github.com/AI-HealthCare-05/AH_05_04/pull/426/files)에 남아 있다.

2026-09-11 등록 준비 당시에는 Draft·두 리뷰어 재승인·같은 구현 PR 내 Current 이동을
병합 조건으로 기록했다. 아래는 그 계획과 구분한 2026-09-14 실제 상태다.

| 근거 | 실제 상태와 적용 범위 |
| --- | --- |
| [#426](https://github.com/AI-HealthCare-05/AH_05_04/pull/426) | 2026-09-10 병합, merge `d4ad39c1585cde35e19cc9001683eee184ce7f2f`. 위 Backend·Frontend 승인은 미등록 후보 범위다. |
| [#462](https://github.com/AI-HealthCare-05/AH_05_04/pull/462) | 2026-09-13 07:31:35 UTC 병합, merge `36890a43b6399ce58c0fc01b8619d0f6f73c6cf5`. 실제 v1 등록·PUT 보완 제외·페이지 이동·날짜별 revision 일치·OpenAPI/DTO/합성 fixture 검증을 포함한다. |
| [남한솔 APPROVED](https://github.com/AI-HealthCare-05/AH_05_04/pull/462#pullrequestreview-5189961883) | 2026-09-13 07:11:11 UTC. 실제 등록 이후 DTO·SELF 소유권·keyset/cursor 복구·보완 제외·날짜별 revision/status·빈 목록 종료의 Frontend 소비 계약을 확인했고 추가 blocker 없음으로 기록했다. |
| 승인 commit | GitHub review의 `commit_id`는 최종 HEAD `7c4252d9095ca8fb357cb2cd22e32d4a297d42fd`다. 리뷰 본문은 `33ac5dbd`를 언급하므로 본문 표기와 API 메타데이터를 구분해 보존한다. |
| [최종 HEAD CI](https://github.com/AI-HealthCare-05/AH_05_04/actions/runs/34745052686) | test-inventory·lint·test-migration·test-backend·test-worker·frontend·test 모두 SUCCESS, 7/7. |

#462 본문에는 송은영·남한솔 두 리뷰어가 남아 있지만 실제 등록 변경의 APPROVED는
남한솔 1건이다. 송은영의 #426 승인을 #462 Backend 재승인으로 확대하지 않는다.
현재 저장소의 책임 리뷰어 1명 기준과 #418의 2026-09-14 종료 조건에 맞춰 이 정리의 책임
리뷰어는 남한솔로 명시한다. 이는 과거 두 리뷰어 조건을 충족했다는 소급 판정이 아니다.
새 API·DTO·오류·상태·DB·publication condition 변경은 없다.

#462 리뷰의 WATCH는 Draft·두 리뷰어 문구와 문서 상태 정렬이었다. 본 문서 정리는
병합·승인 근거와 인덱스를 정렬하지만 **Current 승격 승인을 뜻하지 않는다**. #462가
문서 이동 없이 이미 병합됐으므로 Proposed 경로를 유지하며,
[문서 권위·승격 규칙](../post-mvp-1-document-authority.md#충돌과-승격-규칙)에 따른 후속 검토가 필요하다.
정리 HEAD는 지정 책임 리뷰어의 승인을 받아야 하며 기존 #462 승인을 새 HEAD 승인으로
재사용하지 않는다. 상태·참조 정리 승인·병합과 체크리스트 갱신 후 #418을 종료한다.
#138 Frontend 화면·실제 브라우저 E2E와 Privacy·Track C/F Production gate는 별도로 유지한다.

## Pagination 한계

전체 페이지는 시점 고정 snapshot이 아니다. 후속 GET은 그 시점의 UNCONFIRMED만 반환하며, cursor 앞에 나중에 생성된 과거 기록은 처음부터 재조회해야 보인다. `404 CHECKIN_CURSOR_NOT_FOUND`에서 Backend는 자동 fallback하지 않는다. Frontend가 cursor와 페이지 누적 상태를 초기화하고 `cursor` query parameter를 생략해 같은 `limit`으로 최초 페이지부터 재조회한다. 성공 응답으로 목록을 교체하며, 오류를 빈 목록이나 보완 완료로 처리하지 않는다. 미존재·삭제·소유권 불일치 모두 동일한 [Frontend 복구 절차](../../contracts/proposed/unconfirmed-backlog-v1.md#cursor-404의-frontend-복구)를 따른다. 처방과 계정의 기존 삭제 정책을 변경하지 않는다.
