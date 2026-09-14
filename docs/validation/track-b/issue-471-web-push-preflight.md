# #471 실기기 Web Push 사전 검증 기록

상태: **준비 문서 · NOT_RUN**. 2026-09-14에는 코드·이슈·절차만 확인했다.
HTTPS 환경 기동, 키 발급/주입, 구독 등록, 외부 Push 전송, 실기기 관찰은 실행하지 않았다.
실제 환자 정보는 사용하지 않는다. [실행 절차](../../operations/web-push-preflight-471.md).

## 준비 근거

| 항목 | 기록 |
| --- | --- |
| 코드 확인 기준 | `488a01843faa9a47986a32473e0ed52fea64cee3` (`develop`) |
| Backend 병합 | #481, `af2fa746`; #469 CLOSED 확인 |
| Frontend | #470 OPEN, 기준 코드에 manifest·Service Worker 없음 |
| 표시 fixture | `push-471-synthetic-v1`, [합성 JSON](issue-471-push-payload.synthetic.json) |
| fixture 용도 | 고정 문구·4개 payload 필드 형식 확인용, 가짜 UUID; 발송용 endpoint/키/DB seed 없음 |
| 환경·기기·host | 미정, 실행자 확인 대기 |
| 담당·검토 | 권가빈 준비; 이번 문서 PR 책임 리뷰어 남한솔, Backend·Security 전문 검토 증빙 송은영 |
| 외부/Production 승인 | 이 기록으로 부여하지 않음 |

## 실행별 기록 양식

아래 표를 실행마다 복사한다. 미실행은 NOT_RUN, 필수 조건이 없어 진행하지 못한 경우
BLOCKED와 원인을 기록한다. 실행 후 기대를 만족하면 PASS, 만족하지 않으면 FAIL이다.
문서용 판정어이며 앱/DB 상태 enum이 아니다.

| 항목 | 실행값 |
| --- | --- |
| 실행 ID / 실행자 / 관찰자 / 일시·시간대 | 미기입 |
| 시나리오 ID / fixture ID | 미기입 / push-471-synthetic-v1 |
| 기기 별칭·모델 / OS·브라우저 정확한 버전 | 미기입 |
| 설치 방식 / 일반 탭·홈 화면·전경·배경·잠금 | 미기입 |
| 권한 전→후 / 집중 모드·알림 요약·절전 | 미기입 |
| 네트워크 종류 / 연결·오프라인 상태 | 미기입 |
| HTTPS origin / 접근 통제 / 환경 책임자·사용 기간 | 미기입 |
| Backend SHA·이미지 digest / Frontend SHA | 미기입 |
| DB migration head / Runtime 권한 확인 근거 | 미기입 |
| SW script 경로·버전·scope / manifest 경로·실행 모드 | 미기입 |
| config 점검 / API와 발송 프로세스 동일 설정 | 미기입; 값 원문 제외 |
| provider hostname / Security 검토 근거 | 미기입; endpoint 전체 제외 |
| 합성 계정 별칭 / 새 occurrence 준비 방식 | 미기입; 실제 식별자 제외 |
| 구독 수 / due 알림 수 / scheduler 중지 확인 | 미기입; 단일 기기 실행은 1 / 1 |
| config GET / 구독 PUT·DELETE HTTP 상태 | 미기입 |
| 구독 활성화 / 알림 게시 시각 (UTC) | 미기입 |
| 전송 명령 시작·종료 시각 / exit code | 미기입 |
| 서비스 접수 결과 / 안전한 reason·집계 | NOT_RUN |
| 접수 관찰 시각·관찰 방법 | 미기입; 정밀한 서버 접수시각과 수집시각 구분 |
| 관찰 종료 예정 시각 / 실제 종료 시각 | 미기입 |
| 기기 표시 여부·시각 / 전송 시작부터 관찰 지연 | NOT_RUN; 미표시를 0초로 기록하지 않음 |
| 제목·본문 / 민감 내용 부재 | NOT_RUN |
| 클릭 여부·시각 / 도착한 same-origin 경로 | NOT_RUN |
| 게시·읽음·attempt·Check-in 전후 불변 | NOT_RUN; 값 대신 비교 결과 기록 |
| 해제·unsubscribe·generation 제거·기능 OFF | NOT_RUN |
| 가린 화면 증빙 / 접근 통제된 증빙 위치 | 미기입; HAR·키·구독 JSON·개인 알림 첨부 금지 |
| 최종 PASS/FAIL/BLOCKED / 원인·다음 조치 | NOT_RUN |
| 늦은 도착 등 추가 관찰 / 검토자·검토 일시 | 미기입 |

`ACCEPTED`는 서비스 접수, 화면 증거는 기기 표시, 클릭은 사용자 동작이다.
앱 내부 `delivered_at`·`read_at`·실제 복약을 대신하지 않는다. 로그 집계만으로 표시 PASS를
선언하지 않으며 자동 테스트나 로컬 알림 표시도 실기기 수신 증거로 쓰지 않는다.

## 사전 수신 시나리오

각 수신 시도는 별도 합성 알림 한 건으로 진행한다. 첫 기기 한 건 성공 후 범위를 넓힌다.

| ID | 조건·관찰할 동작 | 기대 | 상태 / 증빙 |
| --- | --- | --- | --- |
| P01 | iPhone 일반 탭 | 홈 화면 설치 안내, 자동 권한 prompt 없음 | NOT_RUN |
| P02 | iPhone 홈 화면 앱·명시적 버튼·잠금 | 구독 성공, 일반 문구 실제 표시, 클릭 시 허용 화면 | NOT_RUN |
| P03 | Android Chrome 전경 | 구독 성공, 일반 문구 실제 표시 | NOT_RUN |
| P04 | Android Chrome 배경/잠금 | 실제 표시·클릭·수신 지연 기록 | NOT_RUN |
| P05 | 권한 거절 | 반복 prompt 없음, 앱 내부 알림 fallback | NOT_RUN |
| P06 | 권한 철회·해제·재구독 | 서버/브라우저 해제 확인, 새 generation으로 새 알림 수신 | NOT_RUN |
| P07 | 전송/표시/클릭 전후 | Push가 게시 의미·읽음·Check-in을 임의 변경하지 않음 | NOT_RUN |
| P08 | 중지·설정 복구 | OFF에서 등록·전송 차단, DELETE 가능, 복구 후 새 합성 실행 | NOT_RUN |

## 화면 연결·정기 실행 후 통합 검증

| ID | 조건 | 기대 | 상태 / 선행 |
| --- | --- | --- | --- |
| I01 | 클릭 → 로그인 → 원래 기록 | 알림 목록의 원래 날짜·소유권을 최신 조회, 없으면 목록 복구 | NOT_RUN / #470·#138·#421 |
| I02 | 오프라인·재연결·기한 경과 | 지연을 기록하고 오래된 알림을 새 복약 지시로 사용하지 않음 | NOT_RUN / 화면 연결 |
| I03 | 이미 보완한 기록·취소 일정 | 최신 상태 표시, TAKEN 자동 변경 없음 | NOT_RUN / 화면 연결 |
| I04 | 여러 기기·중복 실행 | 동일 delivery 중복 전송 억제 확인, 기기별 표시 수 기록 | NOT_RUN / #434 연결 |
| I05 | 로그아웃 → 계정 B 로그인·늦은 이전 Push | 옛 generation 표시·딥링크 폐기, A의 내용/기록 비노출 | NOT_RUN / #470 |
| I06 | 주기 반복·실패·재시작 | 앱 내부 알림 유지, 기존 bounded retry·UNKNOWN 처리, 안전한 중지·복구 | NOT_RUN / #434 연결 |

## 검토와 데모 판단

- Frontend 실기기 검토 증빙: 대기.
- Backend·Security 전송/설정 검토 증빙: 대기.
- 이번 문서 PR 책임 리뷰어: 남한솔 (`solia142`), 2026-09-14 담당자 지정. 승인 대기.
- 데모 포함 판단: **미결정**. 기기 부족·미실행·환경 미준비는 PASS가 아니다.
- #471 종료: **미충족**. 사전 수신 성공 이후에도 통합 시나리오와 책임 검토가 필요하다.

## 준비 자료 자체 점검 — 실기기 결과와 별개

- 두 문서를 Markdown HTML로 렌더링하고 표 7개의 생성 및 상대 링크 대상 존재를 확인했다.
- 합성 JSON의 네 필드·UUID 형식·고정 제목/본문을 병합된 발송 코드와 대조했다.
- 신규 파일을 포함한 전체 diff와 공백을 검토하고 `git diff --check`를 통과했다.
- 브라우저 화면 검토는 로컬 파일 URL 접근 정책으로 수행하지 못했다. 시각 검토 PASS로 기록하지 않는다.
- 런타임 변경이 없는 문서·표시 fixture 준비이므로 Backend 전체 CI·외부 전송·실기기 테스트는 실행하지 않았다.
