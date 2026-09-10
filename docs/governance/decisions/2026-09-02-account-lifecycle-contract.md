# Product Decision `PD-206`: 계정 생명주기(로그아웃·비밀번호 재설정·회원탈퇴) 계약

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-206-20260902` |
| 상태 | 결정 기록 — 로그아웃·`token_version` 재검증·비밀번호 재설정 구현 반영. refresh token rotation과 비밀번호 재설정 응답시간 padding은 PR #404 리뷰 반영 결과를 결정 5·6으로 사후 승인(2026-09-10). 회원탈퇴 후속 구현 대기 |
| 결정일 | 2026-09-02 |
| 결정자(제안) | 송은영 (Backend/DB) |
| 추적 Issue | [#206](https://github.com/AI-HealthCare-05/AH_05_04/issues/206) |
| 근거 문서 | 계정 기능 범위 확정(남한솔, 권가빈 리뷰) — 팀 공용 Notion 문서, 저장소 미포함. 동의·외부 처리 범위 정리 §6/§8/§10(권가빈, 송은영 리뷰) — 팀 공용 Notion 문서, 저장소 미포함. [[공통-S1] Account·Security·Privacy 계약·inventory](https://app.notion.com/p/S1-Account-Security-Privacy-inventory-3c6233603e278184ba03e3b231d8cf13?pvs=21). 요구사항정의서 `CH02_회원_동의` 시트 REQ-USR-007/008/009/010/020. |
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
- **토큰 소비는 원자적 일회성 소비로 구현하고 잠금 순서를 고정한다.** `token_hash`로 candidate의 `user_id`를 잠금 없이 조회한 뒤 user row를 `FOR UPDATE`로 먼저 잠근다. 그 다음 제출 token의 미사용·미만료 조건을 다시 확인하고, 같은 사용자의 미사용·미만료 token row를 `id` 오름차순으로 잠가 함께 소비한 뒤 비밀번호 변경과 `token_version` 증가를 같은 transaction에서 처리한다. candidate 조회 뒤 상태가 바뀔 수 있으므로 user lock 획득 후 유효성 재확인은 필수다. token row를 먼저 갱신하거나 잠근 뒤 user row를 기다리는 경로는 두지 않는다.
- **같은 사용자의 나머지 미사용 토큰도 같은 transaction에서 함께 무효화한다.** 한 사용자가 재설정을 여러 번 요청해 유효한 `password_reset_token`이 동시에 여러 개 존재할 수 있다. 제출 token 하나만 소비하면 다른 유효 token으로 비밀번호를 다시 바꿀 수 있으므로, user row lock을 보유한 상태에서 같은 사용자의 나머지 미사용·미만료 token도 함께 소비 처리한다. 서로 다른 유효 token이 동시에 제출되어도 user row → token row의 단일 lock order로 직렬화하고 deadlock 없이 한 요청만 성공해야 한다. token 발급·만료 정리처럼 두 종류의 row를 함께 잠그는 경로도 같은 순서를 따른다.
- **Frontend 오류 복구 분기는 공통 오류 `details`로 고정한다.** 공개 코드는 새로 추가하지 않고 모두 `422 VALIDATION_FAILED`를 사용한다. 새 비밀번호 정책 오류는 `details[].field=new_password`, `reason=PASSWORD_POLICY_VIOLATION`, 만료·사용됨·존재하지 않는 token은 `details[].field=token`, `reason=RESET_TOKEN_INVALID`로 반환한다. token 내부 사유는 더 세분화하지 않고 두 경우 모두 `rejected_value=null`로 민감값을 숨긴다. Frontend는 message 문자열이 아니라 이 field/reason으로 입력 수정과 재설정 링크 다시 받기를 구분한다.

## 결정 4: 회원탈퇴 — Transaction 경계

0. **대상과 재인증의 정의.** 탈퇴 대상 계정은 요청 URL/body의 별도 파라미터가 아니라 **인증된 요청의 `get_request_user()` 결과(`user_id`)로만** 결정한다 — 다른 사용자의 계정 ID를 지정해 탈퇴시킬 수 있는 경로를 두지 않는다. "재인증"은 세션이 살아있다는 사실만으로 충족되지 않고, `services/auth.py`의 `authenticate()`와 동일한 경로로 **비밀번호 재입력을 검증**하는 것을 의미한다. 이 재인증 엔드포인트는 로그인(`/auth/login`)과 별개의 "비밀번호 맞춰보기" 경로가 되므로, 동일한 수준의 rate limit/lockout을 적용한다(정확한 수치는 결정 3과 같이 구현 PR에서 확정).
1. 재인증 성공 → 최종확인 → **단일 transaction**으로 `account_status=WITHDRAWAL_REQUESTED`, `is_active=false`, `withdrawal_requested_at=now()` 커밋. 이 시점부터 즉시 재로그인 차단. 이 UPDATE는 `WHERE account_status = 'ACTIVE'` 조건부 원자적 전이로 구현하고, 영향받은 row가 0이면 이미 처리된 것으로 간주한다 — read-then-write로 구현하면 동시 중복 요청이 둘 다 커밋될 수 있다([#101](https://github.com/AI-HealthCare-05/AH_05_04/issues/101)과 동일한 클래스의 동시성 결함).
2. 같은 요청은 멱등 처리한다 — 이미 `WITHDRAWAL_REQUESTED`/`WITHDRAWN`인 계정에 중복 탈퇴 요청이 오면(위 조건부 UPDATE의 영향 row 0건으로 식별) 새 transaction 없이 동일한 계정 이용 종료·탈퇴 요청 접수 완료 응답을 반환한다. 이 응답은 개인정보·건강정보의 물리 삭제 완료를 뜻하지 않는다. **이 멱등 처리는 재인증에 성공한 여러 요청이 거의 동시에 도착하는 좁은 경쟁 구간에만 적용된다.** 1번에서 `token_version`이 증가하고 나면 그 시점 이전에 발급된 access token은 결정 1의 재검증 로직에 의해 즉시 무효화되므로, 탈퇴가 실제로 반영된 뒤 도착하는 재요청은 이 멱등 로직에 도달하기 전에 `get_request_user()` 단계에서 표준 401로 차단된다. 즉 탈퇴 완료 후 상태를 조회하기 위한 별도 idempotency key나 인증 없는 상태 조회 경로는 두지 않는다 — 탈퇴 확정 API의 마지막 성공 응답을 화면에 보존해 안내하는 것으로 충분하며, 그 UX 처리는 #206 Frontend 리뷰 범위에서 다룬다.
3. 실제 개인정보·건강정보 삭제·보존 처리는 사용자에게 별도 상태 조회 API를 제공하지 않고 Backend 내부 처리로 진행한다. 탈퇴 기능을 실제 사용자에게 제공하는 구현 PR은 단순히 `PENDING` row만 만들고 종료하지 않으며, PM/Privacy가 확정한 삭제·보존 정책에 맞춰 삭제·보존 처리와 최종 계정 상태 전이를 함께 포함한다. 다만 `EXT-PRIV-001` 승인 전에는 Production 물리 삭제·보존 job, `IN_PROGRESS`/`COMPLETED` 전이와 `account_status=WITHDRAWN` 기록을 실행하지 않고 비식별 합성 fixture를 사용한 Local/Test 구현·검증만 허용한다. 실제 사용자 대상 기능도 승인 전에 `PENDING` 요청만 쌓는 형태로 공개하지 않는다. 승인 후 처리가 완료되면 `account_status=WITHDRAWN`, `withdrawn_at=now()`로 전환한다. 부분 실패는 사용자 화면에 노출하지 않더라도 운영·감사 관점에서 상태로 식별 가능해야 하며 재시도 가능해야 한다.

   **전용 `account_deletion_request` 테이블.** Track A `AI_JOB` 상태 머신을 재사용하지 않고, 삭제 처리 전용 테이블을 별도로 둔다.
   - 최소 컬럼과 상태 전이 상세는 [계정 생명주기 후속 계약 v1](../../contracts/proposed/account-lifecycle-v1.md)의 `account_deletion_request` 테이블 계약을 따른다.
   - `status`: `PENDING` → `IN_PROGRESS` → `COMPLETED` | `FAILED`(재시도 가능). `COMPLETED`는 terminal 상태다.
   - 1번의 `WITHDRAWAL_REQUESTED` 전이와 **같은 transaction**에서 `status=PENDING` row를 생성한다.
   - `account_status`는 로그인 가능 여부를 판단하는 게이트로만 유지하고, 삭제 진행 상태·재시도·실패 사유는 이 테이블이 전담한다 — AI_JOB은 단일 실행 단위(Provider 호출 1회) 기준으로 설계돼 있어 DB·백업·로그·외부 Provider에 걸친 다단계 삭제 작업과 도메인이 맞지 않는다.
   - 이 테이블은 PM/Privacy 정책에 따른 삭제 요청·처리·완료·실패 시각의 감사 기록 역할도 겸한다.
   - 실패 사유는 새 사용자 노출 오류 코드를 늘리지 않고, 필요 시 `TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `INTERNAL_ERROR` 같은 공통 내부 실패 사유를 재사용한다.
4. 탈퇴 처리의 내부 상세 상태는 사용자에게 조회 API나 앱 내부 알림으로 제공하지 않는다. Frontend는 탈퇴 확정 API의 마지막 성공 응답을 화면에 보존하고 로컬 인증 정보를 제거한다. 완료 화면에는 REQ-USR-008 AC-04의 사용자-facing 처리 상태 "삭제 요청 접수됨"을 표시하며, 계정 이용 종료와 요청 접수 완료이지 물리 삭제 완료가 아님을 구분한다. 즉시 삭제 정보, 보존 정보·기간·근거와 최종 완료 통지 여부는 PM/Privacy 정책에서 별도 확정한다.
5. 탈퇴 확정(1번) 시점에 결정 1을 재사용해 해당 사용자의 `token_version`을 원자적으로 `+1`하고 `refresh_token` 쿠키를 종료한다 — REQ-USR-008의 "즉시 로그인 차단"은 `is_active=false`뿐 아니라 이미 발급된 access token의 즉시 무효화(결정 1의 재검증 로직)까지 포함한다.

## 결정 5 (추가, 2026-09-10): Refresh Token Rotation — 범위 외 보안 강화

구현 PR #404 리뷰(권가빈, 남한솔)에서 refresh token 탈취 대응을 위해 이 Decision의 원래 적용 범위(비밀번호 재설정 token) 밖에서 추가로 구현했다. 리뷰 과정에서 최초 설계(`User.active_refresh_jti` 단일 컬럼)의 결함이 발견돼 재설계했고, 그 최종 동작과 기본값을 이 Decision에 사후 기록해 명시적으로 승인한다.

- **`GET /auth/token/refresh`는 매 호출마다 access token과 refresh token을 함께 재발급한다(rotation).** 재사용(rotation으로 이미 교체된 refresh token의 재제출)이 탐지되면 결정 1의 `token_version` 전체 무효화를 그대로 재사용해 해당 사용자의 모든 세션을 강제 로그아웃시킨다 — 결정 2가 명시한 "세션·기기 단위로 구분하지 않는다"는 전역 무효화 원칙과 동일하며, per-device 선택적 로그아웃을 도입하는 것이 **아니다**.
- **`refresh_session` 테이블(로그인별 row) + 로그인 시 발급하는 `session_id` JWT claim으로 재사용 판정을 세션 단위로 범위를 좁힌다.** 최초 설계는 사용자당 단일 `active_refresh_jti` 컬럼으로 CAS를 검사해, 같은 사용자가 다른 기기에서 로그인하면 그 기기의 jti가 이전 로그인의 jti를 덮어써 이전 기기의 아직 유효한 refresh token이 다음 사용 시 "재사용"으로 오판되는 결함이 있었다(다중 기기 로그인이 표준 사용 흐름이므로 회귀). `session_id`는 로그인 시 한 번 발급돼 `rotate()`의 `no_copy_claims`에 포함되지 않아 rotation 내내 유지되며, CAS 비교 기준을 `user_id`가 아니라 `session_id`로 바꿔 서로 다른 로그인이 서로의 rotation을 방해하지 않게 한다. 이 테이블은 결정 2가 언급한 "기기별 선택적 로그아웃"을 위한 세션 테이블이 **아니며**, 오직 rotation 재사용 판정의 정확성을 위해서만 존재한다 — 로그아웃·탈퇴는 여전히 결정 1·2·4의 전역 `token_version` 무효화만 사용한다.
- **`REFRESH_TOKEN_EXPIRE_MINUTES`를 14일에서 7일로 단축한다.** `ACCESS_TOKEN_EXPIRE_MINUTES`는 초안에서 10분으로 단축을 검토했으나, Frontend에 refresh/retry 흐름(+동시 401 요청에 대한 single-flight refresh)이 아직 없어 리뷰(남한솔)에서 기존 60분 유지로 반려됐다 — Frontend가 그 흐름을 구현·병합한 뒤 별도 PR에서 단축한다.
- rotation이 반복돼도 refresh token의 절대 만료(`REFRESH_TOKEN_EXPIRE_MINUTES`, 최초 로그인 기준)는 그대로 유지된다 — `rotate()`는 매 사용마다 `jti`만 교체하고 `exp`는 복사해 유지하므로, 계속 활동해도 세션이 무기한 연장되지 않는다.
- 이 migration 배포 전 발급된 refresh token에는 `session_id` claim이 없어 첫 `/token/refresh` 호출 시 `401 INVALID_TOKEN`으로 거부되고 재로그인이 필요하다 — 하위 호환 마이그레이션은 이 Decision 범위에서 별도로 두지 않는다(실사용자가 있는 환경이면 배포 공지 필요).

## 결정 6 (추가, 2026-09-10): 비밀번호 재설정 요청의 처리시간 anti-enumeration 구체화

결정 3은 "유사 처리시간"을 원칙으로만 명시했다. PR #404 리뷰(권가빈)에서 존재하는 계정만 수행하는 `password_reset_token` INSERT 때문에 실제로는 처리시간 차이가 남는다는 지적을 받아, 구체적인 완화 방식과 기본값을 이 Decision에 사후 기록한다.

- 실제 쓰기(있다면)를 마치고 commit해 DB 커넥션을 반납한 뒤, 요청 진입 시각 기준 `PASSWORD_RESET_RESPONSE_TARGET_SECONDS`(기본 0.03초)까지 응답을 지연시킨다. commit 전에 대기하면 padding 동안 커넥션·트랜잭션을 붙든 채로 있게 되므로 순서를 고정한다.
- 기본값은 CI(Linux 러너, 격리된 컨테이너 — 로컬 Windows/Docker 환경은 노이즈가 너무 커 기준으로 채택하지 않음) 기준 `scripts/measure_password_reset_timing.py` 측정 결과, 동시성 없는 조건에서 가장 느린 경로(존재 계정 신규 토큰)의 p99 4ms·최대 관측치 14ms에 여유를 둔 것이다.
- **잔존 리스크(승인된 한계):** 동시 요청이 많아 DB 커넥션 풀 대기가 지배적인 상황에서는 이 목표치를 넘는 응답이 생길 수 있고, 그 구간에서는 시간차가 다시 드러날 수 있다. 측정 결과 그 상황에서는 세 카테고리(존재/미존재/쿨다운) 응답시간이 자연히 비슷하게 수렴하는 것도 확인했지만, 이는 설계된 방어가 아니므로 알려진 한계로 승인한다. 전역 요청 빈도 제한(IP 기준 등)은 결정 3의 "rate limit 기준은 구현 PR에서 확정" 문구에 따라 이번에도 확정하지 않고 후속 이슈로 분리한다.
- 같은 사용자에 대한 동시 재설정 요청이 60초 쿨다운을 우회해 중복 토큰을 발급하지 않도록, 대상 user row를 결정 3의 재설정 완료 절차와 동일하게 먼저 `FOR UPDATE`로 잠근 뒤 쿨다운 조회~토큰 생성을 직렬화한다(PR #404 리뷰 반영).

## 제외

- 보호자·멀티 프로필 계정 상태 (해당 없음, 본인 단일 계정 기준)
- 실제 이메일 발송 Provider 연동
- 비밀번호 재설정 rate limit 정확한 수치
- 개인정보·건강정보의 세부 보존 기간, 즉시 폐기 대상, 법정 보존 대상과 재가입 제한 여부의 정책 확정 — PM/Privacy 범위
- 탈퇴 후 사용자-facing 상태 조회 API와 앱 내부 완료·실패 알림 — 제공하지 않음
- `EXT-PRIV-001` 승인 전 Production 물리 삭제·보존 처리와 실제 사용자 공개 — 승인 게이트로 차단
- 목적별 동의 상태(`GRANTED`/`WITHDRAWN`) 자체 모델링 — [#207](https://github.com/AI-HealthCare-05/AH_05_04/issues/207)로 분리
- 계정 이벤트(로그아웃·비밀번호 재설정·회원탈퇴) 감사 로그 저장 여부 — 이 Decision 범위에서 확정하지 않는다

## 후속

- Frontend는 이 Decision과 실제 구현 PR의 API/DTO가 확정된 뒤 연결한다(계정 기능 범위 확정 표2 "다음 조치" 참고). 현재 `frontend/src`에는 로그아웃·비밀번호 재설정·회원탈퇴 관련 실제 구현이 없음을 확인했다(`DesignPrototypePage.tsx`의 로그아웃 항목은 디자인 프로토타입 목업이며 실제 세션·API 연동이 아니다).
- `password_reset_token`의 만료분 정리 배치는 구현 PR(#206)에서 lazy cleanup으로 확정했다 — 별도 배치·스케줄러 없이 조회 시 `expires_at` 조건으로만 거르고, 만료된 row는 삭제하지 않는다. 이 저장소의 `idempotency_record`(같은 만료-필터링 패턴, 별도 정리 배치 없음)와 일관된 선택이다. 테이블이 커지는 게 실제 문제가 되면 그때 배치를 추가한다.
- 목적별 동의 상태 모델링은 [#207](https://github.com/AI-HealthCare-05/AH_05_04/issues/207)에서 별도 Decision으로 진행한다(담당 송은영/권가빈, `동의·외부 처리 범위 정리.md` §10 P0 근거).
- 계정 이벤트 감사 로그 저장 여부는 이슈 [#206](https://github.com/AI-HealthCare-05/AH_05_04/issues/206) "후속 작업"에서 별도로 결정한다.
