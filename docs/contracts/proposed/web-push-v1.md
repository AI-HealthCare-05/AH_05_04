# Web Push v1 — #469 검토안

상태: **Proposed · 구현 PR 검토 중 · 책임 리뷰 승인 대기**.
[PD-469](../../governance/decisions/2026-09-13-web-push-469.md)가 결정 출처다.
아래 경로·필드·숫자·오류·상태는 이 작업 브랜치에서 구현한 검토안이다.
사용자가 2026-09-13 구현·테스트 후 PR에서 계약 검토를 받도록 지시했다.
병합된 Current 계약이나 Production 공개 승인으로 취급하지 않는다.
구현 권가빈, Backend·DB·Security 리뷰 송은영, Frontend 소비 계약 리뷰 남한솔.

## 기존 동작과 연결

- `NotificationRecord`의 `DELIVERED`, `attempt`, `delivered_at`, `read_at`은
  앱 내부 게시·읽음 전용이다. Push 성공·실패로 변경하지 않는다.
- occurrence parent chain의 SELF profile 소유권을 검증한다.
- 최초 알림·재알림 생성 로직과 Check-in의 의미를 변경하지 않는다.
- #470은 설치·권한·Service Worker·클릭 흐름, #471은 실제 기기 합성 수신 검증이다.
  Backend 단위 테스트를 기기 수신 증빙으로 대체하지 않는다.

## API·OpenAPI 구현

모두 인증 필요, `Cache-Control: no-store`, 공통 오류 envelope 사용.
endpoint·키·요청 body는 오류 details와 로그에 반사하지 않는다.

| Method / 경로 | 요청 | 성공 응답 |
| --- | --- | --- |
| `GET /api/v1/push/config` | 없음 | `200 {data: {public_key: string}}` |
| `PUT /api/v1/push/subscriptions` | `endpoint: string`, `keys: {p256dh: string, auth: string}` | `200 {data: {id: UUID, generation: UUID}}` |
| `DELETE /api/v1/push/subscriptions/{subscription_id}` | 없음 | `204`, body 없음 |

operation ID는 각각 `push.config`, `push-subscription.upsert`,
`push-subscription.delete`다. body의 추가 필드는 금지한다. 키는 base64url을
decode하여 P-256 uncompressed public key 65 bytes/유효 curve point,
auth secret 16 bytes로 검증한다. endpoint 상한은 2048자다.

같은 사용자·endpoint·키·token_version의 중복 PUT은 같은 id/generation을
반환한다. 키 또는 인증 세대가 바뀌면 새 generation으로 교체하고 기존 미접수
전달을 종료한다. DELETE 재호출은 소유권을 확인할 수 있는 해제 행에 한해 204다.
다른 사용자 및 존재하지 않는 id는 `404 PUSH_SUBSCRIPTION_NOT_FOUND`로 통일한다.
다른 사용자에게 결속된 endpoint의 PUT은 `409 PUSH_SUBSCRIPTION_CONFLICT`로
거절하며 소유자 정보는 반환하지 않는다. Frontend는 unsubscribe/resubscribe한다.
입력 오류는 `422 VALIDATION_FAILED`, 인증 오류는 기존 인증 계약을 사용한다.
SELF profile이 없는 사용자는 `404 PROFILE_NOT_FOUND`다. DNS 조회 실패/5초
초과도 `503 PUSH_UNAVAILABLE`이다. 이 PUT/DELETE는 리소스 상태 자체의 멱등성을
사용하며 `Idempotency-Key` snapshot 재생은 추가하지 않는다. 해제 후 재등록은
새 generation이며 오래된 성공 응답을 재생하지 않는다. 이 예외는 PD-469 리뷰 대상이다.
전송 기능이 꺼져 있으면 config/PUT은 `503 PUSH_UNAVAILABLE`; DELETE는 허용한다.

## 저장·계정 경계

- `push_subscription`: UUID id, SELF `profile_id` FK, 등록 당시 `token_version`,
  UUID generation, 전역 unique endpoint HMAC, 암호화한 endpoint/p256dh/auth,
  암호화 key version(`key_id`), created/activated/revoked 시각.
- `push_delivery`: UUID id, Notification FK, subscription FK, generation snapshot,
  status, attempt_count, next_attempt_at, claim token/expiry, accepted_at,
  expires_at, 고정 실패 분류 및 created/updated 시각.
  `(notification_id, subscription_id, generation)` UNIQUE로 중복 생성을 막는다.
- 암호화는 cryptography Fernet 인증 암호화를 사용한다. VAPID private key,
  저장 암호화 키, endpoint HMAC 키는 용도를 분리하고 저장소 밖에서 주입한다.
  key_id별 복호화 키를 유지하고 active_key_id로 신규 암호화한다. 동일 구독의
  재등록 시 기존 키로 읽고 활성 키로 다시 암호화하며 generation은 보존한다.
  HMAC 키는 unique scope 전체에서 하나로 고정한다. 구독/tombstone이 남아 있는
  동안 변경하지 않는다. 상세 키 운영과 보존은 Security·Privacy 검토 대상이다.
- logout·reset·계정 비활성화 이후 매 전송 검사에서 현재 user 상태와
  token_version 불일치를 차단한다. 기존 `increment_token_version` 호출은 같은
  transaction에서 모든 구독을 해제하고 ciphertext를 삭제한다. logout/reset/
  refresh 재사용 탐지를 공통으로 처리한다. 계정 비활성화 상태를 직접 변경한
  경우에도 다음 전송에서 차단하며 dispatcher 정리 단계에서 ciphertext를 삭제한다.
- 권한 철회는 Frontend가 감지 가능한 시점에 DELETE 및 unsubscribe한다.
  서버는 브라우저 설정 변경을 즉시 알 수 없다. 404/410 수신도 해제 근거다.
- 계정 전환 전에 구독 해제와 로컬 generation 제거를 수행한다. Service Worker는
  수신 generation과 현재 로컬 generation이 다르면 표시·딥링크를 폐기한다.
  이 Frontend 작업 완료 전 계정 전환 안전성은 미검증이다.
- 계정 삭제 시 구독·전달 FK cascade와 암호문 삭제가 필요하다. 현재 탈퇴 API를
  완료로 가정하지 않으며 #469와 계정 생명주기 담당자가 호출 경계를 조율한다.
- 해제 즉시 키 ciphertext를 제거하고, endpoint HMAC tombstone과 terminal
  ledger를 7일 후 정리한다. tombstone은 revoked_at, ledger는 terminal updated_at
  기준이다. 7일 보존·탈퇴 예외는 Privacy 확인 필요다. 전달 ledger가 남아 있는
  tombstone은 먼저 삭제하지 않으며 정리도 회당 최대 limit개다.

## 전송 보안과 payload

HTTPS/443의 승인된 Push provider host만 정확히 허용한다. userinfo, fragment,
IP literal, 비표준 port, 미승인 host는 거절한다. 등록 및 매 전송 시 DNS 결과에
loopback/private/link-local/reserved IP가 하나라도 있으면 거절한다.
DNS 검사 후 재해석되는 TOCTOU를 막을 peer pinning 또는 통제 egress가 필요하다.
redirect와 환경 proxy의 암묵적 사용은 금지한다. 단순 URL validator 또는
라이브러리 기본 POST만으로 SSRF 완료를 주장하지 않는다.
설정의 exact host allowlist는 기본 빈 목록이고 활성화 시 필수다. 실제 provider
host 목록은 #471 합성 구독에서 확인하고 Security 리뷰로 고정한다. 전송 TCP는
검증된 공인 IP로 직접 연결하며 TLS는 원래 hostname을 검증한다. DNS 재해석,
redirect, 환경 proxy를 사용하지 않는 전용 transport로 이를 구현했다.

구현 payload는 고정 title `복약 기록 알림`, body `앱에서 기록을 확인해 주세요.`,
불투명 notification id, generation이다. 약명·용량·진단·사용자 식별자·날짜는
전송하지 않는다. notification id도 민감 metadata로 취급하고 로깅하지 않는다.
클릭 시 인증 후 기존 알림 목록에서 id와 `occurrence_local_date`를 확인해
원래 복약 날짜로 진입한다. 찾을 수 없으면 알림 목록으로 복구하며 임의 날짜를
추정하거나 현재 사용자의 다른 occurrence에 연결하지 않는다.

## 전달·재시도 구현

1. 앱 내부 `DELIVERED`이며 `delivered_at >= subscription.activated_at`인 알림만
   후보로 생성한다. 새 구독으로 과거 알림을 소급 전송하지 않는다.
2. 짧은 DB transaction으로 후보를 선점한다. 잠금 순서는 user → occurrence →
   subscription → delivery로 사용한다. 잠금 획득 후 시간을 다시 읽고 기한을 검사한다.
3. 전송 직전 현재 소유권·user 활성 상태/token_version·구독 generation/해제·
   occurrence `PENDING`·확인 기한·알림 상태를 재검사한다. Check-in row가 생겼거나
   일정/occurrence가 취소되면 종료한다. 과거 version이라는 이유만으로 과거
   occurrence를 취소하지 않고 기존 일정 정합화 취소 결과를 따른다.
4. 전달 expires_at은 `min(confirmation_deadline_at, delivered_at + 300초)`다.
   TTL은 `min(300, floor(expires_at - now))`초이며 1초 미만이면 전송하지 않는다.
   외부 HTTP는 최대 10초, 호출 대기는 12초다. DNS가 늦게 완료되더라도 호출
   deadline이 지나면 새 HTTP를 시작하지 않는다.
5. 선점과 최종 판정 후 HTTP는 DB transaction 밖에서 호출한다. 응답 저장은
   claim token과 generation을 재확인한다. 2xx는 서비스 접수이며 기기 수신이 아니다.

| 결과 | 처리 |
| --- | --- |
| 2xx | `ACCEPTED`, 재전송 금지 |
| 404/410 | `FAILED`, 해당 generation 구독 해제·다른 대기 전달 차단 |
| 429/5xx | 최대 총 3회, 30초·120초 backoff; Retry-After가 더 늦으면 준수하되 기한 이후는 종료 |
| timeout/connection loss/전송 중 crash | `UNKNOWN`, 자동 재전송 금지 |
| 기타 4xx/3xx | `FAILED`, 재전송 및 redirect 금지 |
| 전송 전 해제/취소/Check-in/기한 경과 | `CANCELLED`, 외부 요청 0건 |

대기·선점은 `PENDING`, `SENDING` 상태다. 선점 유효기간은 30초다. 만료된 SENDING은 UNKNOWN으로
종료한다. 결과 불명에서 유실을 수용하며 exactly-once를 주장하지 않는다.
마지막 검사 직후 취소·logout과 외부 전송이 경합할 수 있고 접수된 메시지는
회수 불가다. TTL도 전송 지연과 기기 표시 시점까지 엄격히 제한하지 못하므로
클릭 시 최신 인증·소유권·상태 조회가 필수다.

기존 `schedule_notifications`가 `process_notifications` 완료 후 별도 bounded
Push 명령을 실행한다. 회당 기본 100개, 최대 500개, 배치 제한 45초다.
생성/게시 transaction에 외부 요청을 추가하지 않는다. opt-in 기본 off,
Push 장애는 기존 앱 내부 목록·Check-in을 막지 않는다. AI Worker 및 새 queue는
범위 밖이며 운영 scheduler 연결은 #434 담당 범위와 함께 리뷰한다. Production에서는
`WEB_PUSH_PRODUCTION_ENABLED`(기본 false, [PD-469-2](../../governance/decisions/2026-09-16-web-push-production-gate-651.md))가
true여야 `WEB_PUSH_ENABLED` 값에 따라 config/등록/전송이 열리며, false면 enabled 값과
무관하게 차단한다. DELETE는 이 두 플래그와 무관하게 유지한다. 단, API/scheduler 프로세스가
설정을 cache하므로 OFF 전환은 env 변경 즉시 완료되는 절차가 아니다. 새 설정을 읽도록
API/scheduler 전 인스턴스를 재시작한 뒤에만 신규 config/등록과 전송 batch 차단 완료로
기록한다.

## 검증 범위

| 계층 | 필수 사례 |
| --- | --- |
| API/OpenAPI | 인증·SELF 소유권·타인 404·중복 PUT·키 변경·해제 재호출·no-store·민감 입력 비반사 |
| PostgreSQL/migration | 단일 Alembic head, upgrade/downgrade 안전성, FK 삭제, UNIQUE 동시 등록/선점, 암호문 보존 |
| 계정/Frontend 통합 | 여러 기기, logout/reset/reuse 무효화, 계정 전환, 권한 철회, 늦은 옛 generation 수신 |
| 전송/경합 | 중복 실행, 기한 직전·취소·Check-in 경합, 늦은 응답 fencing, timeout/crash UNKNOWN, backoff/Retry-After, 404/410 |
| SSRF/비로그 | redirect, 내부/혼합 IP, DNS rebinding, proxy, 유효하지 않은 키, 예외와 DB 로그 sentinel |
| 불변성 | Push 성공·실패 전후 Notification read/delivered/attempt 및 Check-in 값 불변 |
| 기기 합성 | 설치→구독→일반 문구 표시→클릭→인증→원래 날짜, iOS 홈 화면 및 Android Chrome (#471) |

구현 이후 CONTRIBUTING의 Ruff·format·Mypy·전체 CI 명령 결과와 commit,
migration revision, 합성 fixture 및 실기기 환경을 별도 증빙으로 기록한다.
실행 결과는 [#469 검증 기록](../../validation/track-b/issue-469-web-push.md)에 기록한다.
실제 Push 서비스 접수·실기기 수신과 계정 전환 Service Worker 통합은 #471에서
검증해야 하며 Backend 암호화 roundtrip 성공을 해당 증빙으로 사용하지 않는다.

## 기술 근거

- [pywebpush 공식 저장소](https://github.com/web-push-libs/pywebpush): Python Web Push/VAPID 후보. `pywebpush==2.3.0`으로 잠금. 전용 pinned transport 및 합성 VAPID/복호화 roundtrip 검증.
- [RFC 8030](https://www.rfc-editor.org/rfc/rfc8030.html): TTL 및 Push 서비스 접수·만료 경계. 위 300초는 표준값이 아닌 제품 제안이다.
- [기존 계정 계약](../current/user-account.md): token_version 및 탈퇴 미구현 경계.
- [기존 Notification 계약](./track-b-notifications-v1.md): 앱 내부 게시·읽음 의미.
