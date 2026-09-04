# Product Decision `PD-206`: 계정 생명주기(로그아웃·비밀번호 재설정·회원탈퇴) 계약

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-206-20260902` |
| 상태 | 결정 기록 — 로그아웃·`token_version` 재검증 구현 반영, 비밀번호 재설정·회원탈퇴 후속 구현 대기 |
| 결정일 | 2026-09-02 |
| 결정자(제안) | 송은영 (Backend/DB) |
| 추적 Issue | [#206](https://github.com/AI-HealthCare-05/AH_05_04/issues/206) |
| 근거 문서 | 계정 기능 범위 확정(남한솔, 권가빈 리뷰) — 팀 공용 Notion 문서, 저장소 미포함. 동의·외부 처리 범위 정리 §6/§8/§10(권가빈, 송은영 리뷰) — 팀 공용 Notion 문서, 저장소 미포함. [[공통-S1] Account·Security·Privacy 계약·inventory](https://app.notion.com/p/S1-Account-Security-Privacy-inventory-3c6233603e278184ba03e3b231d8cf13?pvs=21). `요구사항_정의서.xlsx` `CH02_회원_동의` 시트 REQ-USR-007/008/009/010/020. |
| 적용 범위 | User 계정 상태, 로그아웃 구현 기준, 비밀번호 재설정/회원탈퇴 API·transaction 경계 |

## 결정 1: 계정 상태 표현 + 세션 무효화 카운터

`User`에 다음 컬럼을 추가한다.

- `account_status`: `ACTIVE` | `WITHDRAWAL_REQUESTED` | `WITHDRAWN`
- `withdrawal_requested_at`, `withdrawn_at`: nullable timestamp
- `token_version`: integer, not null, 기본값 `0`. 로그아웃·비밀번호 재설정·탈퇴마다 원자적으로 `+1`. 발급된 access/refresh token 중 `token_version` 클레임이 이 값과 **정확히 일치하지 않는** 토큰은 만료 전이라도 전부 무효로 취급한다(아래 "이 방식을 쓰려면..." 문단의 정확한 일치 비교와 동일한 규칙이며, 클레임 값이 작든 크든 다르면 무효다).

기존 `is_active`(현재 `services/auth.py`의 로그인 차단에 이미 사용 중)는 의미를 바꾸지 않는다. `account_status`가 `ACTIVE`가 아니게 되는 시점에 `is_active`도 함께 `false`로 설정해, 기존 로그인 차단 경로를 그대로 재사용한다.

`동의·외부 처리 범위 정리` §6의 "계정이 활성 상태다" Gate 체크는 `account_status == ACTIVE`로 연결한다.

`account_status`가 `ACTIVE`가 아닌 계정에 `is_active=true`를 다시 세팅하는 코드 경로는 없어야 한다 — 두 컬럼의 불일치(예: 탈퇴 요청 후 다른 플로우가 `is_active`만 되돌리는 것)를 막기 위해, `is_active`를 직접 대입하는 대신 계정 상태 전이 시점에만 두 컬럼을 함께 갱신하는 단일 지점(예: 서비스 계층의 상태 전이 헬퍼)을 통과하도록 구현 PR에서 강제한다.

**세션 무효화는 jti 개별 폐기 목록도, 타임스탬프 비교도 아니라 `token_version` 단조 증가 카운터로 처리한다.** jti 방식(초안 v1)은 두 가지 실제 결함이 있어 폐기했다:
1. `core/jwt/tokens.py`의 `Token.set_jti()`는 access token과 refresh token 각각에 독립적인 `uuid4`를 발급하고, `RefreshToken.access_token` 프로퍼티는 `no_copy_claims`에 `jti`를 포함해 access token에 복사하지 않는다 — 즉 한 로그인에서 나온 access/refresh token은 jti가 서로 다르다. "refresh token의 jti를 폐기 목록에 기록"하고 "access token 검증 시 jti로 조회"하는 방식은 서로 다른 값을 비교하게 되어 절대 매치되지 않는다.
2. 비밀번호 재설정·회원탈퇴처럼 "해당 사용자의 모든 세션 무효화"가 필요한 경우, 서버는 그 사용자에게 지금까지 발급한 jti 전체를 알 방법이 없다(발급 시점에 별도로 전수 기록해 둔 적이 없음). "모든 jti를 일괄 기록"은 실행 불가능한 문장이었다.

**타임스탬프 비교(`tokens_valid_after` + `iat`/`iat_ms`, 초안 v2)도 폐기했다.** Frontend/UX 리뷰에서 두 차례에 걸쳐 지적받았듯, "무효화 시각과 발급 시각을 같은 정밀도로 내림해서 비교"하는 접근은 그 정밀도 단위(초→밀리초→...) 안에서 발급된 토큰의 실제 선후관계를 원천적으로 구분하지 못한다 — 정밀도를 아무리 올려도 경계 자체가 없어지지 않고 작아질 뿐이라, 같은 종류의 지적이 마이크로초·나노초 단위로 반복될 뿐 근본적으로 해소되지 않는 접근이었다.

**`token_version`은 시간 비교가 아니라 정수 일치 비교라 이 문제 자체가 존재하지 않는다.** 무효화 시 `token_version`을 원자적으로 `+1`하고, 토큰 검증은 `token.token_version == user.token_version` 정확히 일치하는지만 확인한다. 발급과 무효화가 물리적으로 아무리 가깝게 일어나도, 둘 다 같은 `user` row에 대한 쓰기라 DB가 순서를 보장하며 — 어느 한쪽이 먼저 commit되고 그 이후 읽는 값만 유효하다. "같은 순간에 걸치면 어느 쪽이 이기는가"라는 질문 자체가 성립하지 않는다.

구현은 토큰 payload에 발급 시점의 `user.token_version` 값을 명시적으로 포함한다. 기존 초안에서 논의됐던 `iat`/`iat_ms` 클레임은 이 Decision에서는 더 이상 필요하지 않다(로깅 목적으로 표준 `iat`를 남겨도 무방하나 무효화 판정과는 무관하다).

**노출·변조 위험 검토.** JWT는 서명만 하고 암호화하지 않으므로 `user_id`처럼 `token_version`도 토큰을 가진 누구나 읽을 수 있지만, 이 값은 세션 무효화 횟수를 나타내는 정수일 뿐 계정 식별·의료정보와 무관해 노출돼도 추가 위험이 없다. 서명 검증을 통과하지 못하면 클레임을 변조할 수 없는 것도 기존 `exp`/`jti`와 동일하다.

**모든 인증된 요청의 재검증(REQ-USR-010, REQ-USR-020 AC-03 대응).** `dependencies/security.py`의 `get_request_user()`는 요청마다 `repository.get_user(user_id)`로 DB를 조회한 뒤 다음 두 체크를 수행한다. 실패 시 계약된 401을 반환한다.
- `account_status == ACTIVE`(또는 동등하게 `is_active`) — 계정 자체가 살아있는지.
- 토큰의 `token_version` `==` `user.token_version` — 이 토큰이 마지막 전체 세션 무효화 이후에 발급됐는지.

REQ-USR-010("서버가 화면 표시 여부와 별개로 모든 접근 권한을 재검증한다")과 REQ-USR-020 AC-03("이미 종료된 session으로 API를 호출하면 계약된 401/재인증 응답이 반환된다")이 이 동작을 요구한다.

**`GET /auth/token/refresh`도 같은 재검증을 거친다.** `token_refresh`는 refresh token의 서명·만료만으로 새 access token을 발급하지 않고 DB의 사용자 상태를 다시 조회해 다음을 확인한다.
- `account_status == ACTIVE`
- **refresh token 자체의 `token_version`** `==` `user.token_version` — 새로 발급하는 access token의 `token_version`이 아니라, 지금 제시된 refresh token이 원래 발급될 때 담겼던 값을 기준으로 판단한다. access token 쪽 값으로 대신 검사하면 refresh할 때마다 값이 최신으로 갱신되어 무효화가 무력화된다.
- 두 조건 중 하나라도 실패하면 `get_request_user()`와 동일한 401을 반환하고 새 access token을 발급하지 않는다.

## 결정 2: 로그아웃 — `token_version` 증가 + refresh 쿠키 종료

`apis/v1/auth_routers.py`의 `login`이 이미 refresh token을 **httponly 쿠키**(`refresh_token`)로 내려주고 있음을 확인했다 — 로그아웃은 이 쿠키의 존재를 전제로 설계한다.

- 로그아웃 API는 (a) 해당 사용자의 `token_version`을 원자적으로 `+1`하고, (b) 응답에서 `refresh_token` 쿠키를 명시적으로 만료·삭제(`delete_cookie` 또는 과거 시각의 `set_cookie`)한 뒤, (c) 로컬 세션 종료 안내를 함께 응답한다. **(b)는 REQ-USR-020 AC-01("access token과 refresh cookie가 함께 종료된다")이 요구하는 항목으로, 기존 초안에는 빠져 있었다.**
- **동작 결과 명시:** 이 방식은 세션·기기 단위로 구분되지 않으므로, 한 기기에서 로그아웃하면 같은 사용자의 다른 기기 세션도 함께 즉시 무효화된다. 현재 저장소에는 기기/세션을 구분해 추적하는 테이블이 없고, `계정 기능 범위 확정.md`·REQ-USR-020 어디에도 "다른 기기 세션 유지"를 요구하는 내용이 없어 이 전체-무효화 동작을 채택한다. 추후 기기별 선택적 로그아웃이 요구되면 별도 Decision에서 세션 테이블을 도입해야 한다.
- 서버 요청 실패 시에도 클라이언트는 로컬 자격증명을 우선 제거한다(계정 기능 범위 확정 표1 기준).

## 결정 3: 비밀번호 재설정 — 토큰

REQ-USR-009 설계메모는 "본인 확인 방식과 기존 세션 무효화 범위는 인증 계약에서 확정한다"고 명시한다 — 이 Decision이 그 인증 계약 역할을 하며, 아래로 확정한다.

`password_reset_token` 테이블을 추가한다.

- `id`, `user_id`, `token_hash`(원문 미저장 — 재설정 링크의 원본 토큰 값은 DB에 저장하지 않고 해시만 저장해, DB 유출 시에도 토큰이 재사용되지 않도록 한다), `created_at`, `expires_at`, `used_at`(nullable)
- 존재하지 않는 계정 요청도 존재하는 계정과 동일한 응답 형태·유사 처리시간을 반환한다(계정 존재 여부 비노출).
- 재설정 성공 시 해당 사용자의 `token_version`을 원자적으로 `+1`해 기존 세션을 전부 무효화한다(결정 1 재사용) — 이것이 REQ-USR-009가 말하는 "기존 세션 무효화 범위"의 확정이다: 재설정 시점 이전에 발급된 모든 access/refresh token을 무효화하며, 재설정을 요청한 기기만 예외로 두지 않는다.
- **재설정 성공 후 인증 상태(Frontend/UX 리뷰 반영, 2026-09-02): 재로그인을 요구한다.** 재설정 성공 응답은 새 access/refresh token을 발급하지 않고 성공 안내만 반환하며, Frontend는 로그인 화면으로 이동시킨다. 의료정보를 다루는 서비스 특성상 재설정 직후 자동 세션 발급으로 매끄러운 진입을 주는 것보다, 사용자가 새 비밀번호로 다시 로그인해 본인이 그 비밀번호를 정확히 인지하고 있는지 즉시 재확인하게 하는 쪽을 우선한다. 재설정 성공 응답 body에는 토큰 등 세션 관련 정보를 포함하지 않는다.
- rate limit 기준(횟수/기간)은 이 Decision 범위에서 확정하지 않고 구현 PR에서 Backend/Security 리뷰로 정한다.
- **재설정 성공 자체(토큰 검증 후 새 비밀번호 저장)에는 anti-enumeration 고려가 필요 없다** — 이 시점의 인증 요소는 계정 존재 여부가 아니라 `password_reset_token`이므로, 유효하지 않은/만료된/사용된 토큰은 그냥 실패 응답으로 처리한다.
- **토큰 소비는 원자적 일회성 소비로 구현한다.** 토큰 소비(`used_at` 조건부 갱신), 비밀번호 변경, `token_version` 증가를 단일 transaction에서 처리하며, 토큰 소비는 결정 4의 조건부 UPDATE와 같은 패턴(`UPDATE password_reset_token SET used_at = now() WHERE id = ? AND used_at IS NULL AND expires_at > now()`)으로 구현한다. 영향받은 row가 0건이면 이미 사용됐거나 만료된 토큰으로 간주해 실패 응답을 반환하고 비밀번호를 변경하지 않는다 — read-then-write로 구현하면 같은 토큰의 동시 요청이 둘 다 성공해 비밀번호가 두 번 바뀌거나 경쟁 상태로 이어질 수 있다.
- **같은 사용자의 나머지 미사용 토큰도 같은 transaction에서 함께 무효화한다.** 한 사용자가 재설정을 여러 번 요청해 유효한 `password_reset_token`이 동시에 여러 개 존재할 수 있다. 위 조건부 UPDATE는 지금 소비하는 토큰 하나(`id = ?`)만 `used_at`을 채우므로, 그대로 두면 같은 사용자의 다른 유효 토큰으로 비밀번호를 다시 바꿀 수 있다. 그래서 토큰 소비와 같은 transaction에서 `UPDATE password_reset_token SET used_at = now() WHERE user_id = ? AND used_at IS NULL AND expires_at > now()`로 해당 사용자의 나머지 미사용·미만료 토큰도 함께 소비 처리한다.

## 결정 4: 회원탈퇴 — Transaction 경계

0. **대상과 재인증의 정의.** 탈퇴 대상 계정은 요청 URL/body의 별도 파라미터가 아니라 **인증된 요청의 `get_request_user()` 결과(`user_id`)로만** 결정한다 — 다른 사용자의 계정 ID를 지정해 탈퇴시킬 수 있는 경로를 두지 않는다. "재인증"은 세션이 살아있다는 사실만으로 충족되지 않고, `services/auth.py`의 `authenticate()`와 동일한 경로로 **비밀번호 재입력을 검증**하는 것을 의미한다. 이 재인증 엔드포인트는 로그인(`/auth/login`)과 별개의 "비밀번호 맞춰보기" 경로가 되므로, 동일한 수준의 rate limit/lockout을 적용한다(정확한 수치는 결정 3과 같이 구현 PR에서 확정).
1. 재인증 성공 → 최종확인 → **단일 transaction**으로 `account_status=WITHDRAWAL_REQUESTED`, `is_active=false`, `withdrawal_requested_at=now()` 커밋. 이 시점부터 즉시 재로그인 차단. 이 UPDATE는 `WHERE account_status = 'ACTIVE'` 조건부 원자적 전이로 구현하고, 영향받은 row가 0이면 이미 처리된 것으로 간주한다 — read-then-write로 구현하면 동시 중복 요청이 둘 다 커밋될 수 있다([#101](https://github.com/AI-HealthCare-05/AH_05_04/issues/101)과 동일한 클래스의 동시성 결함).
2. 같은 요청은 멱등 처리한다 — 이미 `WITHDRAWAL_REQUESTED`/`WITHDRAWN`인 계정에 중복 탈퇴 요청이 오면(위 조건부 UPDATE의 영향 row 0건으로 식별) 새 transaction 없이 현재 상태를 반환한다. **이 멱등 처리는 재인증에 성공한 여러 요청이 거의 동시에 도착하는 좁은 경쟁 구간에만 적용된다.** 1번에서 `token_version`이 증가하고 나면 그 시점 이전에 발급된 access token은 결정 1의 재검증 로직에 의해 즉시 무효화되므로, 탈퇴가 실제로 반영된 뒤 도착하는 재요청은 이 멱등 로직에 도달하기 전에 `get_request_user()` 단계에서 표준 401로 차단된다. 즉 탈퇴 완료 후 상태를 조회하기 위한 별도 idempotency key나 인증 없는 상태 조회 경로는 두지 않는다 — 탈퇴 확정 API의 마지막 성공 응답을 화면에 보존해 안내하는 것으로 충분하며, 그 UX 처리는 #206 Frontend 리뷰 범위에서 다룬다.
3. 실제 건강정보 삭제·보존 처리는 **비동기**로 진행하고, 완료 시 `account_status=WITHDRAWN`, `withdrawn_at=now()`로 전환한다. 부분 실패는 상태로 식별 가능해야 하며 재시도 가능해야 한다.

   **초안(멘토 자문 후 확정) — 전용 `account_deletion_request` 테이블.** Track A `AI_JOB` 상태 머신을 재사용하지 않고, 삭제 처리 전용 테이블을 별도로 둔다.
   - `account_deletion_request(id, user_id, status, requested_at, completed_at, failed_at, retry_count, last_error_code)`
   - `status`: `PENDING` → `IN_PROGRESS` → `COMPLETED` | `FAILED`(재시도 가능)
   - 1번의 `WITHDRAWAL_REQUESTED` 전이와 **같은 transaction**에서 `status=PENDING` row를 생성한다.
   - `account_status`는 로그인 가능 여부를 판단하는 게이트로만 유지하고, 삭제 진행 상태·재시도·실패 사유는 이 테이블이 전담한다 — AI_JOB은 단일 실행 단위(Provider 호출 1회) 기준으로 설계돼 있어 DB·백업·로그·외부 Provider에 걸친 다단계 삭제 작업과 도메인이 맞지 않는다.
   - 이 테이블은 `EXT-PRIV-001`(외부 Privacy 승인) 관련 삭제 요청·완료 시각의 감사 기록 역할도 겸한다.
   - **이 방향은 아직 확정이 아니다.** 멘토 자문 후 이 초안을 유지·수정할지 결정하고, 이 문단을 갱신한다.
4. 실제 데이터 물리 삭제 실행은 `EXT-PRIV-001` 외부 Privacy 승인 전까지 수행하지 않는다 — 이 Decision은 상태 추적까지만 다루고 물리 삭제는 별도 승인 후 별도 PR로 분리한다.
5. 탈퇴 확정(1번) 시점에 결정 1을 재사용해 해당 사용자의 `token_version`을 원자적으로 `+1`하고 `refresh_token` 쿠키를 종료한다 — REQ-USR-008의 "즉시 로그인 차단"은 `is_active=false`뿐 아니라 이미 발급된 access token의 즉시 무효화(결정 1의 재검증 로직)까지 포함한다.

## 제외

- 보호자·멀티 프로필 계정 상태 (해당 없음, 본인 단일 계정 기준)
- 동의 상태(`GRANTED`/`WITHDRAWN`) 자체의 모델링 — 별도 Decision
- 실제 이메일 발송 Provider 연동
- 비밀번호 재설정 rate limit 정확한 수치
- 건강정보 실제 물리 삭제 실행 (`EXT-PRIV-001` 승인 후)
- 목적별 동의 상태(`GRANTED`/`WITHDRAWN`) 자체 모델링 — [#207](https://github.com/AI-HealthCare-05/AH_05_04/issues/207)로 분리
- 계정 이벤트(로그아웃·비밀번호 재설정·회원탈퇴) 감사 로그 저장 여부 — 이 Decision 범위에서 확정하지 않는다

## 후속

- Frontend는 이 Decision과 실제 구현 PR의 API/DTO가 확정된 뒤 연결한다(계정 기능 범위 확정 표2 "다음 조치" 참고). 현재 `frontend/src`에는 로그아웃·비밀번호 재설정·회원탈퇴 관련 실제 구현이 없음을 확인했다(`DesignPrototypePage.tsx`의 로그아웃 항목은 디자인 프로토타입 목업이며 실제 세션·API 연동이 아니다).
- `password_reset_token`의 만료분 정리 배치는 구현 PR에서 별도 확정한다(`token_version`은 `User` 컬럼 값이라 별도 정리 배치가 필요 없다).
- 목적별 동의 상태 모델링은 [#207](https://github.com/AI-HealthCare-05/AH_05_04/issues/207)에서 별도 Decision으로 진행한다(담당 송은영/권가빈, `동의·외부 처리 범위 정리.md` §10 P0 근거).
- 계정 이벤트 감사 로그 저장 여부는 이슈 [#206](https://github.com/AI-HealthCare-05/AH_05_04/issues/206) "후속 작업"에서 별도로 결정한다.
