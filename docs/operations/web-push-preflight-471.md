# #471 Web Push 실기기 사전 검증 준비

상태: **PREPARATION_ONLY · 환경/기기/최소 수신 화면 확인 대기 · 실제 전송 미실행**.
2026-09-14 준비 기준이다. 이 문서는 기존 구현을 소비하는 실행 계획이며 새 API·DTO·DB·
전송 정책이나 공개 조건을 정의하지 않는다. #471 전체 완료 또는 Production 승인 증빙이 아니다.

## 담당과 기준 소스

- 준비·실행·증빙 취합: 권가빈 (`hazelnutflavoured`).
- #471에 기록된 협업·리뷰 범위: 남한솔 (`solia142`) — 설치·권한·Service Worker·기기 동작;
  송은영 (`phina-io`) — Backend·Security·설정·전송 증빙.
- 이번 사전 준비 문서 PR의 단일 책임 리뷰어는 남한솔 (`solia142`)이다.
  2026-09-14 구현 담당자의 지정에 따라 설치·실기기 절차와 Backend 실행 절차의 연결을
  검토한다. Backend·Security 설정/전송의 전문 검토 증빙은 송은영 범위로 별도 남긴다.
  #471 전체의 기존 역할을 변경하거나 전문 검토 완료를 의미하지 않는다.
- 구현 확인 기준: `develop` commit `488a01843faa9a47986a32473e0ed52fea64cee3`.
  #481 merge commit은 `af2fa746`이다. 실제 실행 때는 배포한 Backend/Frontend SHA를 따로 기록한다.
- [#471](https://github.com/AI-HealthCare-05/AH_05_04/issues/471),
  [#470](https://github.com/AI-HealthCare-05/AH_05_04/issues/470),
  [Web Push 운영](web-push.md), [PD-469](../governance/decisions/2026-09-13-web-push-469.md),
  [Web Push 계약](../contracts/proposed/web-push-v1.md),
  [#469 검증](../validation/track-b/issue-469-web-push.md)을 함께 읽는다.

#481 병합과 #469 종료는 확인했으나 기준 SHA의 계약·운영 문서에는 Proposed/검토 중 표기가
남아 있다. 여기서 current로 승격하거나 외부 승인을 추정하지 않는다. 아래 필드와 명령은
병합된 코드·DTO와 대조한 실행 참고다. #504/#418 문서 정리와 이슈 종료는 별도 후속 작업이다.

## 오늘 준비한 것과 실행 전 확인할 것

| 항목 | 현재 상태 | 실행 전 채울 값 / 담당 |
| --- | --- | --- |
| 실행 절차·판정·기록 양식 | 준비 | 이 문서와 [실행 기록](../validation/track-b/issue-471-web-push-preflight.md) |
| 합성 표시 fixture | 준비, 미전송 | [payload 예시](../validation/track-b/issue-471-push-payload.synthetic.json) |
| HTTPS 테스트 환경 | 미정 | 가빈: 주소, 접근 통제, 운영자, 사용 기간, 독립 DB/배포 식별자 |
| iPhone / Android | 미정 | 가빈·한솔: 기기 별칭, 모델, OS·브라우저 버전, 설치 방식 |
| 최소 설치·수신 화면 | 미확인 | 한솔: manifest·SW 경로/scope·commit, 권한 버튼·구독·해제·표시·클릭 |
| Backend 설정·migration | 실제 환경 미확인 | 가빈·은영: non-production, DB head, Runtime 권한, secret 주입 여부 |
| Provider exact host | 미정 | 기기에서 hostname만 확인 → 은영 검토 → allowlist 적용 |
| 실제 수신 | NOT_RUN | 사전 조건 완료 후 기기별 한 건씩 순차 실행 |

기준 SHA의 `frontend/public`에는 manifest·Service Worker가 없고, 수신 구현은 #470 범위다.
Frontend 전체 완성은 필요하지 않지만 아래 최소 기능이 있는 내부 prototype을 한솔님과
연결해야 한다. 이번 준비에서는 다른 담당자의 Frontend 구현을 수정하지 않았다.

## HTTPS·환경·설정 점검

1. 실제 기기가 접근 가능한 **접근 통제된 내부 HTTPS origin**을 선택한다. 유효한 인증서와
   인증서 체인을 기기에서 확인하고 mixed content가 없어야 한다. 휴대폰의 localhost는
   개발 PC가 아니다. Production 주소의 환경 이름을 local로 바꿔 차단을 우회하지 않는다.
2. Frontend와 `/api`를 같은 origin으로 제공하는 구성을 우선 검토한다. 별도 origin이면
   실제 CORS·인증 동작을 먼저 확인한다. 테스트 origin은 운영 origin/SW scope와 분리한다.
3. 테스트 전용 DB·합성 계정·전용 키를 사용한다. migration `469a1b2c3d4e`를 포함한 배포
   head와 Runtime 역할의 Push DML 권한을 확인한다. 전체 head 적용 절차와 role provisioning은
   [기존 운영 문서](web-push.md)의 Migration·롤백·검증 절을 따른다.
4. 첫 단발 실험 중 notification scheduler는 중지한다. 아래 명령은 전체 후보를 처리하므로
   공유 DB에서 “한 건”을 보장하지 않는다. 현재 실행 기기 구독 1개와 due 알림 1개만 있는
   격리 환경인지 확인하고, 다른 실행 프로세스가 없는지 확인한다.
5. API와 발송 프로세스에 아래 설정을 **환경변수로** 주입한다. `PushSettings`는 자체적으로
   `.env` 파일을 읽도록 설정되어 있지 않다. 파일만 만들어 두는 것으로 준비 완료가 아니다.

| 변수 | 확인 내용; 값 자체는 기록하지 않음 |
| --- | --- |
| `WEB_PUSH_ENABLED` | 설정 준비 전 false, 해당 내부 검증 실행 때 true |
| `WEB_PUSH_ALLOWED_HOSTS` | 기기에서 관찰·검토한 정확한 hostname의 JSON 배열; wildcard 금지 |
| `WEB_PUSH_VAPID_PRIVATE_KEY` | 전용 P-256 PEM private key, 저장소 밖 secret 주입 |
| `WEB_PUSH_VAPID_PUBLIC_KEY` | 같은 키 쌍의 uncompressed public key, base64url no padding |
| `WEB_PUSH_VAPID_SUBJECT` | 담당 서비스의 유효한 `mailto:` 연락처 |
| `WEB_PUSH_ENCRYPTION_KEYS` | key_id → Fernet key JSON 객체, 잔존 데이터 복호화 키 유지 |
| `WEB_PUSH_ACTIVE_KEY_ID` | 위 객체에 있는 최대 40자 key_id |
| `WEB_PUSH_ENDPOINT_HMAC_KEY` | 암호화/VAPID와 별개인 무작위 32자 이상 secret |

설정 교체 후 API와 발송 프로세스를 함께 새 설정으로 기동한다. 비밀값·구독 JSON·전체 환경·
Docker config·HAR·요청 body를 출력하거나 이슈에 첨부하지 않는다. DB SQL echo도 끈다.
인증된 `GET /api/v1/push/config`의 200과 `data.public_key` 유무만 기록한다.
503이면 feature OFF·설정 누락·키 불일치·Production 차단을 확인한다.

초기 allowlist는 추측으로 확장하지 않는다. 한솔님이 사용하는 동일 전용 VAPID public key로
브라우저 구독을 만들고 **endpoint의 hostname만** 확인한다. 은영님 검토 후 exact host를
주입하고 Backend config와 공개키 일치를 확인한 다음 등록한다. 최초 config를 열 수 없는
경우 테스트 공개키의 안전한 제공 경로를 협업하여 정한다. 비밀키를 브라우저에 주지 않는다.
새 host가 필요할 때 기존 SSRF·공인 DNS·TLS·redirect 제한을 완화하지 않는다.

## 한솔님과 연결할 최소 화면 조건

- iPhone은 일반 탭에서 설치 안내를 확인하고 홈 화면에서 별도로 실행한다. iOS/iPadOS
  16.4 이상 홈 화면 웹앱의 사용자 직접 동작에 따른 권한 요청을 기준으로 한다.
  manifest의 `display`, `start_url`, `scope`, 이름·아이콘과 실제 실행 모드를 확인한다.
  [WebKit 근거](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/).
- Android Chrome은 secure context와 `serviceWorker`, `PushManager`, `Notification` 지원을
  확인한다. 자동 prompt 대신 사용자가 누른 알림 켜기 버튼으로 권한을 요청한다.
  [Push 흐름 근거](https://web.dev/articles/push-notifications-overview).
- 활성 SW·scope와 페이지 제어 상태를 확인한다. 기존 SW와 중복 등록하지 않는다.
  SW의 `push` 처리가 알림을 실제 표시해야 한다. 개발자 도구의 모의 Push나 로컬
  `showNotification()` 성공은 외부 Push 수신 PASS가 아니다.
- 인증된 `PUT /api/v1/push/subscriptions`에는 `endpoint`, `keys.p256dh`, `keys.auth`만
  보낸다. `PushSubscription.toJSON()` 전체의 `expirationTime` 등을 그대로 보내면 추가 필드
  금지 DTO와 맞지 않는다. 성공 응답은 `data.id`, `data.generation`이다.
- SW가 현재 구독 generation과 수신 generation을 비교할 수 있어야 한다. 이전 generation의
  표시·클릭은 폐기한다. 인증 token·API 응답·의료정보를 offline cache에 저장하지 않는다.
- 사전 prototype의 클릭은 허용한 same-origin 테스트 화면으로 돌아오는 것까지만 판정할 수
  있다. 로그인 후 원래 복약 기록 복구와 계정 전환 안전성은 별도 통합 시나리오로 남긴다.
- 해제 버튼에서 인증된 DELETE와 브라우저 unsubscribe·로컬 generation 제거를 확인한다.
  권한 거절 시 반복 prompt 없이 설정 안내와 기존 앱 내부 알림 경로를 제공한다.

## 합성 알림 한 건 준비

fixture ID는 `push-471-synthetic-v1`이다. JSON의 UUID는 표시 형식 확인용 가짜 값이며,
실제 발송 명령의 입력이나 운영 DB seed가 아니다. Backend가 실제 테스트 구독의 generation과
테스트 Notification ID를 채우도록 한다. 임의 payload 발송 API는 추가하지 않는다.

실험 데이터는 전용 합성 계정 A의 SELF profile·현재 확정 처방 version·합성 약 항목 1개,
구독 1개, 최초 알림 대상 occurrence 1개다. 합성 약은 `frequency_per_day=1` 조건으로
준비한다. 기존 합성 처방이 없으면 승인된 합성 fixture를 기존 확정 흐름으로 준비하고,
실제 환자 자료나 자동 테스트의 `push.example.test` endpoint를 대체 사용하지 않는다.

1. 계정 A로 로그인 → 기기에서 권한 허용 → 구독 PUT 성공을 먼저 확인한다.
2. 기존 일정 PUT으로 당일 실행 시각에서 수 분 뒤의 시각 하나를 설정한다.
   `start_local_date`·`end_local_date`는 그 시각의 KST 날짜, `end_mode=DATE`,
   `local_times`는 해당 `HH:mm` 하나, `expected_revision`은 조회한 revision
   (신규 일정이면 0)을 사용한다. 날짜 경계 부근에서는 다음 날로 명시적으로 맞춘다.
   기존 Idempotency-Key가 필요하며 [일정 계약](../contracts/proposed/track-b-schedule-api-v1.md)을 따른다.
3. `GET /api/v1/medication-occurrences?date=YYYY-MM-DD`로 대상이 `PENDING`,
   `checkin=null`, 확인 기한 전임을 확인한다. 민감 식별자는 결과 문서에 옮기지 않는다.
4. 구독 활성화 이후 알림이 게시되어야 한다. 기존 알림을 재사용하려고 `delivered_at`,
   attempt, 상태, deadline을 DB에서 바꾸지 않는다. 재실험에는 새 합성 실행을 준비한다.

## 실행 순서 — 환경·기기 준비 후에만

아래는 **배포한 동일 Backend 코드의 Python 환경, 저장소 루트**에서 실행한다.
DB·인증·Push 설정은 앞 절대로 이미 주입되어 있어야 한다. 활성 Python 환경은 해당 commit의
잠긴 의존성을 사용한다. 운영 서버에서 이 명령을 실행하지 않는다.

1. [결과 양식](../validation/track-b/issue-471-web-push-preflight.md)을 실행별로 복사하고
   기기·commit·설정 점검·합성 데이터·관찰 종료 시각을 먼저 기록한다. 폰/서버 시계를 맞춘다.
2. iPhone 홈 화면 앱 또는 Android Chrome에서 구독 준비를 확인한 뒤, 계획한 전경/배경/잠금
   상태로 전환한다. 집중 모드·알림 요약·배터리 절약·네트워크 조건도 기록한다.
3. 예정 시각이 지난 후 앱 내부 알림을 단발 생성·게시한다.

   ```bash
   PYTHONPATH=backend:. python -m app.commands.process_notifications
   ```

4. 앱 내부 게시 1건과 미읽음·미체크인 상태를 확인한다. 게시 시각부터 300초와 확인 기한
   중 먼저 도래하는 시각 안에 다음 명령을 실행한다. 코드의 TTL은 남은 시간으로 계산한다.

   ```bash
   PYTHONPATH=backend:. python -m app.commands.process_push
   ```

5. 명령 exit code, `outcome`, `reason`, `count`, `accepted_count`와 관찰 시각을 기록한다.
   exit 0만으로 전송을 판정하지 않는다(OFF 또는 후보 0개일 수도 있다).
   `ACCEPTED`/`accepted_count=1`은 Push 서비스 접수 증거다. 기기 표시와 별도 칸에 쓴다.
6. 전송 시작·서비스 접수 관찰·기기 표시·클릭 시각을 분리해 기록한다. 로그 수집 시각만
   있으면 접수 시각으로 단정하지 않는다. 관찰창은 실행 전에 예를 들어 5분으로 정하되
   이는 SLA가 아니다. 관찰창 내 미표시는 해당 수신 시도 FAIL, 원인 미확정으로 기록하고
   이후 늦게 도착하면 추가 관찰을 남긴다.
7. 표시 제목 `복약 기록 알림`, 본문 `앱에서 기록을 확인해 주세요.`와 민감 내용 부재를
   확인한다. 화면 증거는 합성 알림만 남기고 다른 개인 알림을 제외한다.
8. 클릭 전후 앱 내부 Notification의 게시·읽음·attempt 및 Check-in 상태가 Push 때문에
   바뀌지 않았는지 확인한다. 명시적 읽음 동작을 따로 수행했다면 그 사건을 구분한다.
9. 수신·클릭 결과를 기록한 뒤 구독 DELETE → unsubscribe·generation 제거 → 테스트 알림 닫기,
   필요 시 기존 일정 취소 API로 남은 합성 일정을 정리한다. 다음 기기는 새 실행으로 진행한다.

## 실패·중지·복구

| 관찰 | 다음 조치 |
| --- | --- |
| config/PUT 503 | Production 여부, enabled, 설정·키 쌍, DNS 확인; 오류 원문 수집 금지 |
| PUT 422 | DTO 추가 필드·키 형식·exact host·공인 DNS 확인; SSRF 검사 유지 |
| PUT 409 | 기존 계정 결속 여부 확인, 적절한 해제 후 unsubscribe/resubscribe; 강제 소유권 이전 금지 |
| 후보 0 / 접수 0 | 구독 활성 시각, 게시 여부·시각, PENDING·checkin·기한, 기존 delivery 결과 확인 |
| ACCEPTED인데 미표시 | 기기 권한·설치 모드·SW scope/generation·집중 모드·네트워크 확인 |
| UNKNOWN | 결과 불명으로 기록, 같은 전달 자동/강제 재전송 금지 |
| 429/5xx | 기존 총 3회·backoff·기한만 사용; 즉시 반복 호출이나 deadline 연장 금지 |
| 404/410 | 기존 구독 해제 결과 확인 후 명시적 사용자 동작으로 재구독 |
| 민감정보 노출/계정 혼선 | 실행 중지, 기존 Security 보고 경로 사용; 해당 검증 FAIL |

중지는 scheduler 정지와 `WEB_PUSH_ENABLED=false` 적용 후 API/발송 프로세스 재기동으로
확인한다. DELETE는 OFF에서도 가능하다. 이미 접수된 메시지는 회수를 보장하지 못한다.
브라우저 권한 해제만으로 서버 키 삭제가 즉시 완료되었다고 판단하지 않는다.
키·tombstone·ledger 보존과 rollback은 기존 운영 문서를 따른다. 이력 삭제나 HMAC 변경으로
재발송하지 않는다. 재개는 문제 수정·새 설정 적용·새 실행 ID·새 합성 대상 준비 후 진행한다.

사전 수신 PASS는 해당 기기/설정의 한 번의 관찰에만 적용한다. #470/#138/#421 화면 연결 후
원래 날짜 복구·오프라인·기한 경과·계정 전환을, #434 연결 후 반복/장애 복구를 검증한다.
전체 시나리오·책임 검토·데모 판단이 남아 있으므로 사전 수신만으로 #471을 닫지 않는다.
