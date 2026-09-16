# Web Push — #469 검토 중 운영 연결

상태: 구현 PR 검토 중. 기본 OFF, Production 등록·전송 차단.
[계약](../contracts/proposed/web-push-v1.md), [Decision](../governance/decisions/2026-09-13-web-push-469.md)
및 [Production 게이트 Decision(#651)](../governance/decisions/2026-09-16-web-push-production-gate-651.md).

`schedule_notifications`는 앱 내부 게시를 먼저 완료하고 별도 `process_push.run()`을
호출한다. 두 작업의 transaction은 분리한다. Push 예외는 고정 분류 로그를 남기고
다음 앱 내부 실행을 유지한다. 기존 60초 fixed delay는 두 실행이 끝난 후 적용된다.
Push가 활성화되면 회당 최대 45초가 추가될 수 있으며 정시 도착을 보장하지 않는다.
기존 one-shot `process_notifications`만 실행하면 외부 전송은 하지 않는다.

## Local 합성 검증 설정

아래 값은 Backend와 notification scheduler 프로세스에 동일하게 주입한다.
실제 secret, endpoint, 브라우저 키를 문서·로그·명령행 인자로 남기지 않는다.

| 환경변수 | 형식/운영 경계 |
| --- | --- |
| `WEB_PUSH_ENABLED` | 기본 false. Production에서는 `WEB_PUSH_PRODUCTION_ENABLED`도 true여야 등록·전송이 열린다 |
| `WEB_PUSH_PRODUCTION_ENABLED` | 기본 false(#651). Production에서 이 값이 false면 `WEB_PUSH_ENABLED` 값과 무관하게 등록·전송을 차단한다. 다른 Production 전용 안전장치(이메일 인증 강제 등)에는 영향을 주지 않는다. 문제가 생기면 이 값만 다시 false로 두고 API/scheduler를 재시작하면 코드 롤백이나 재배포 없이 차단된다. 아래 cache 설명대로 재시작 전까지는 이미 뜬 프로세스가 이전 값을 계속 쓴다 |
| `WEB_PUSH_ALLOWED_HOSTS` | 정확한 provider hostname의 JSON 배열. 기본 빈 배열, wildcard 금지 |
| `WEB_PUSH_VAPID_PRIVATE_KEY` | 저장소 밖 secret에서 주입하는 P-256 PEM private key |
| `WEB_PUSH_VAPID_PUBLIC_KEY` | 해당 키의 uncompressed public key, base64url no padding |
| `WEB_PUSH_VAPID_SUBJECT` | 담당 서비스의 `mailto:` 연락처 |
| `WEB_PUSH_ENCRYPTION_KEYS` | key_id → Fernet key의 JSON 객체. 폐기 전 모든 잔존 ciphertext 복호화 키 유지 |
| `WEB_PUSH_ACTIVE_KEY_ID` | 신규 저장용 key_id, 최대 40자 |
| `WEB_PUSH_ENDPOINT_HMAC_KEY` | 암호화/VAPID와 다른 무작위 32자 이상 secret. 구독·tombstone이 남아 있을 때 변경 금지 |

VAPID private/public key가 맞지 않거나 활성화 필수 설정이 빠지면 fail closed한다.
API는 고정 `503 PUSH_UNAVAILABLE`로 응답하며 DELETE는 secret 설정 오류와 무관하게
사용할 수 있다. 등록 시 DNS 실패도 503, 허용되지 않는 주소는 422다.
설정은 프로세스에서 cache하므로 교체 후 API/scheduler를 함께 재시작한다.

암호화 키 회전은 새 key_id를 추가하고 active_key_id를 바꾼다. 기존 키는 유지한다.
동일 구독 재등록은 새 키로 암호화하되 generation/activated_at을 바꾸지 않는다.
구독·전달 처리 종료와 기존 ciphertext 부재를 확인한 뒤에만 이전 키를 제거한다.
VAPID 변경에는 브라우저 재구독이 필요하며 자동으로 기존 구독을 이전하지 않는다.

## 안전한 전송과 장애 판정

등록 및 전송마다 allowlist·공인 DNS 주소를 검사한다. 전송은 해당 IP에 직접
TLS로 연결하면서 원래 hostname을 검증한다. redirect를 따라가지 않고 proxy 환경변수를
사용하지 않는다. DNS가 호출 제한시간보다 늦게 끝나면 HTTP를 시작하지 않는다.
provider 응답 body/reason/header 원문은 저장하지 않으며 Retry-After만 숫자 대기로 변환한다.

로그의 `outcome`, `reason`, `count`, `accepted_count`는 집계다. endpoint, 키,
payload, notification id, 사용자 id, 예외 원문을 로그에 추가하지 않는다.
`ACCEPTED`는 서비스 접수이며 기기 표시·읽음·실제 복약이 아니다.
`UNKNOWN`은 timeout 또는 선점 만료이며 자동 재전송하지 않는다.
5xx/429도 최대 총 3회, 게시 후 5분/확인 기한 이내만 재시도한다.

logout/reset/refresh 재사용 탐지는 token_version 증가와 같은 transaction에서
모든 구독을 해제하고 키를 지운다. 이미 provider에 접수된 Push는 회수할 수 없다.
#470은 브라우저 generation 확인과 계정 전환 시 unsubscribe, 클릭 후 최신 인증·
원본 알림 조회를 구현해야 한다. 이 연동과 #471의 합성 기기 검증 전에는
계정 전환 안전성이나 실제 수신 완료를 선언하지 않는다.

## Migration·롤백·검증

- revision `469a1b2c3d4e`는 `166f30415263` 다음에 적용한다.
- migration 후 `provision_database_roles.py`를 재실행한다. Runtime(API/scheduler)만
  Push 테이블 SELECT/INSERT/UPDATE/DELETE를 가지며 Source Writer/Management와 PUBLIC에는
  해당 테이블 접근을 부여하지 않는다. TRUNCATE·DDL 권한도 부여하지 않는다.
- `push_subscription` 또는 `push_delivery`가 비어 있지 않으면 downgrade가 거절된다.
  rollback을 위해 사용자 이력을 임의로 삭제하지 않는다. 우선 기능을 OFF한다.
- `process_push`의 정리 단계는 7일 지난 terminal ledger와 해제 tombstone을 제한된
  수량으로 제거한다. 이 7일 정책은 Privacy 책임 리뷰 대상이다.
- 기능 OFF/설정 오류 시 정기 정리는 실행하지 않는다. 로그아웃의 동기 키 삭제와
  전송 비활성화는 유지된다. 잔존 이력 삭제·운영 보존은 책임자 확인이 필요하다.
- 수신 검증에는 합성 계정과 테스트 기기만 사용한다. 자동 테스트는 provider
  전송을 대체해 암호화·VAPID를 검증하며 실제 전송/기기 수신은 #471 증빙이다.
- 검증 결과: [#469 실행 기록](../validation/track-b/issue-469-web-push.md).
