# 회원가입·사용자 정보 Backend 계약

## 목적

회원가입과 내 정보 조회·수정 API에서 MVP 범위의 입력 필드와 개인정보 nullable 상태를 Frontend와 공유하는 기준을 기록합니다.

## 회원가입

- Endpoint: `POST /api/v1/auth/signup`
- 요청 body는 `name`, `email`, `password` 세 필드만 허용합니다(`extra="forbid"`).
- `gender`, `birthday`, `phone_number` 등 가입 후 추가 정보 입력 대상 필드는 회원가입 요청에서 받지 않습니다.
- MVP 범위 밖 필드가 포함되면 공통 `422 VALIDATION_FAILED` 응답을 반환합니다.
- `email` 중복 시 `409 CONFLICT`을 반환합니다. `phone_number` 중복 체크는 Post-MVP에서 가입 요청에 `phone_number`가 추가될 때 함께 적용됩니다.

| 필드 | 기준 |
| --- | --- |
| `name` | 필수, 1~20자 |
| `email` | 필수, `EmailStr`, 최대 40자. Backend에서 소문자로 정규화 |
| `password` | 필수, 8~72자, 대문자·소문자·숫자·특수문자 각 1개 이상 포함 |

### 이메일 정규화·저장·중복 기준

- 회원가입과 `PATCH /api/v1/users/me` 요청의 `email`은 Backend에서 소문자로 정규화한 뒤 저장합니다.
- 로그인 요청의 `email`도 `EmailStr`, 최대 40자 기준으로 검증한 뒤 소문자로 정규화해 계정을 조회합니다.
- 이메일 중복 여부는 소문자로 정규화된 값을 기준으로 판단합니다. 따라서 대소문자만 다른 이메일은 같은 계정 식별자로 취급하며 중복 가입을 허용하지 않습니다.
- `GET /api/v1/users/me`와 `PATCH /api/v1/users/me` 응답의 `email`에는 DB에 저장된 소문자 정규화 값이 반환됩니다.

## 내 정보 조회·수정

- Endpoint: `GET /api/v1/users/me`, `PATCH /api/v1/users/me`
- 가입 직후 `gender`, `birthday`, `phone_number`는 `null`일 수 있습니다(`USER` 테이블 nullable).
- MVP의 `PATCH /api/v1/users/me`는 `name`, `email`만 수정 대상으로 받습니다(`extra="forbid"`).
- `gender`, `birthday`, `phone_number` 수정은 Post-MVP의 가입 후 추가 개인정보·건강정보 입력 기능에서 다룹니다.

## 인증 세션 무효화

이 절은 [`PD-206`](../../governance/decisions/2026-09-02-account-lifecycle-contract.md) 중 현재 구현된 로그아웃·refresh token rotation·비밀번호 재설정 범위를 기록합니다. 회원탈퇴 API는 아직 현재 실행 계약이 아니며 후속 구현 범위입니다.

- `User.account_status`는 `ACTIVE`, `WITHDRAWAL_REQUESTED`, `WITHDRAWN` 중 하나입니다.
- `User.token_version`은 access/refresh token 무효화 판정에 사용하는 정수 카운터이며 기본값은 `0`입니다.
- access token과 refresh token payload에는 발급 시점의 `token_version`을 포함합니다.
- 모든 인증된 요청은 `get_request_user()`에서 DB의 사용자 상태를 다시 조회합니다.
- `account_status != ACTIVE`, `is_active=false`, 또는 토큰의 `token_version != user.token_version`이면 `401 INVALID_TOKEN`을 반환합니다.
- `POST /api/v1/auth/logout`은 현재 사용자의 `token_version`을 DB에서 원자적으로 `+1`하고, 응답에서 `refresh_token` httponly 쿠키를 만료·삭제합니다.
- 로그아웃 후 기존 access token으로 보호 API에 접근하거나 기존 refresh token으로 토큰 갱신을 시도하면 `401 INVALID_TOKEN`을 반환합니다.
- 현재 구현은 기기·세션 단위 로그아웃을 구분하지 않습니다. 한 기기에서 로그아웃하면 같은 사용자의 기존 access/refresh token이 함께 무효화됩니다.

### Refresh Token Rotation(#206)

- `refresh_session(id, user_id, active_jti, created_at, updated_at)` 테이블이 로그인 1회(=refresh token 1개 계보)마다 독립된 row로 현재 유효한 jti를 추적합니다. `id`는 refresh token payload의 `session_id` 클레임과 같습니다. **로그인마다 새 row가 생기므로 같은 사용자의 여러 기기 로그인이 서로 간섭하지 않습니다**(PR #404 리뷰 — `User` 컬럼 하나로 두면 두 번째 로그인이 첫 번째 로그인의 refresh를 재사용 공격으로 오판했습니다).
- `GET /api/v1/auth/token/refresh`는 매 호출마다 access token과 **새 refresh token을 함께 재발급**하고, 응답에서 `refresh_token` 쿠키를 새 값으로 교체합니다. 이때도 refresh token 자체의 `token_version`을 `user.token_version`과 먼저 비교하며, 일치하지 않으면 `401 INVALID_TOKEN`을 반환합니다.
- 새 refresh token의 `exp`는 새로 계산하지 않고 직전 refresh token의 값을 그대로 옮깁니다 — rotation을 반복해도 `REFRESH_TOKEN_EXPIRE_MINUTES`(최초 로그인 기준 절대 상한, 기본 7일)가 늘어나지 않습니다.
- 이미 rotation으로 교체돼 무효해진 refresh token이 다시 제출되면(해당 `refresh_session.active_jti` 불일치) 탈취 의심 신호로 간주해 그 사용자의 `token_version`을 즉시 `+1`해 모든 access/refresh token을 무효화하고 `401 INVALID_TOKEN`을 반환합니다 — `token_version`은 사용자 전체를 무효화하므로 다른 기기의 세션도 함께 끊깁니다(기존 로그아웃과 동일한 전체 무효화 정책, 세션 단위 무효화는 범위 밖).
- `ACCESS_TOKEN_EXPIRE_MINUTES` 기본값은 60분입니다(Frontend refresh/retry 흐름이 구현되기 전까지 유지 — PR #404 리뷰).

### 회원가입 이메일 인증(#431)

- `POST /api/v1/auth/email-verification/request`는 회원가입 전 이메일 소유 확인 token을 발급합니다. 별도 공개 이메일 중복 확인 API를 만들지 않으며, 이미 가입된 이메일이어도 같은 성공 응답 형태를 반환하고 token을 만들거나 발송하지 않습니다. 최종 중복 방어는 기존 `POST /api/v1/auth/signup`의 `409 CONFLICT`가 담당합니다.
- `email_verification_token(id, email, purpose, token_hash, created_at, expires_at, verified_at)` — 아직 User row가 없을 수 있는 단계이므로 `user_id` FK를 두지 않습니다. 원문 token은 저장하지 않고 SHA-256 해시만 저장합니다.
- `purpose`는 현재 `SIGNUP`만 사용합니다. 같은 이메일·목적에 대해 `EMAIL_VERIFICATION_REQUEST_COOLDOWN_SECONDS`(기본 60초) 안에 다시 요청하면 새 token을 만들지 않고 같은 성공 응답을 반환합니다.
- `verification_token`은 `LOCAL` 환경에서만 응답에 채워집니다. 그 외 환경에서는 이메일 존재 여부 추론을 줄이기 위해 비웁니다. 실제 외부 Email Provider 연결은 `EmailSender` adapter 뒤에 두며, 기본값 `EMAIL_PROVIDER=noop`은 발송하지 않습니다. 배포에서 `EMAIL_PROVIDER=smtp`와 `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_USE_TLS`, `SMTP_TIMEOUT_SECONDS`를 설정하면 SMTP Provider로 발송합니다. 실제 Provider 선택·계정·비용 정책은 배포 설정 단계에서 확정합니다.
- `POST /api/v1/auth/email-verification/confirm`은 이메일과 원문 token을 받아 `token_hash`, `verified_at IS NULL`, `expires_at > now()` 조건으로 검증합니다. 성공하면 같은 이메일·목적의 미인증·미만료 token 전체를 인증 완료 처리합니다.
- 유효하지 않거나, 만료됐거나, 이미 사용됐거나, 다른 이메일에 발급된 token이면 `422 VALIDATION_FAILED`, `details[].field=token`, `reason=EMAIL_VERIFICATION_TOKEN_INVALID`를 반환합니다.
- 로그·오류 응답에는 원문 token, token hash, 이메일 존재 여부 추론 정보를 남기지 않습니다.

### 비밀번호 재설정(#206, `PD-206` 결정 3)

- `password_reset_token(id, user_id, token_hash, created_at, expires_at, used_at)` — 원문 토큰은 저장하지 않고 해시(SHA-256)만 저장합니다.
- 새 비밀번호는 [회원가입 비밀번호 기준](#회원가입)과 동일하게 필수, 8~72자, 대문자·소문자·숫자·특수문자 각 1개 이상 포함을 적용합니다. 회원가입 비밀번호 정책이 바뀌면 재설정 정책도 같은 변경에서 함께 갱신합니다.
- `POST /api/v1/auth/password-reset/request`는 계정 존재 여부와 무관하게 항상 같은 성공 응답(`detail`)을 반환합니다(anti-enumeration). 원문 token은 `EmailSender` adapter 호출 경계까지만 전달하고 DB에는 저장하지 않습니다. `reset_token`은 `LOCAL` 환경에서만 채워지며, 그 외 환경에서는 항상 비웁니다. 같은 사용자가 `PASSWORD_RESET_REQUEST_COOLDOWN_SECONDS`(기본 60초) 안에 다시 요청하면 새 token을 발급하지 않고 같은 성공 응답만 반환합니다. 이메일 발송 Provider는 회원가입 이메일 인증과 같은 `EMAIL_PROVIDER`/`SMTP_*` 설정을 사용합니다.
- **처리시간 기반 anti-enumeration(PR #404 리뷰)**: 계정이 없어도 있는 경우와 같은 수의 DB 조회·해싱 연산을 수행하지만, 존재하는 계정만 수행하는 `password_reset_token` INSERT 때문에 남는 처리시간 차이가 있습니다. 이 차이를 없애기 위해 실제 쓰기(있다면)를 마치고 commit까지 끝낸 뒤, 요청 진입 시각 기준 `PASSWORD_RESET_RESPONSE_TARGET_SECONDS`(기본 0.03초)까지 응답을 지연시킵니다. 이 값은 `scripts/measure_password_reset_timing.py`로 CI(Linux 러너, 격리된 컨테이너) 기준 측정한 가장 느린 경로의 최대 관측치(약 14ms)에 여유를 둔 것입니다. **잔존 리스크**: 동시 요청이 많아 DB 커넥션 풀 대기가 지배적인 상황에서는 응답 시간이 이 목표치를 넘을 수 있고, 그 구간에서는 계정 존재 여부에 따른 미세한 시간차가 다시 드러날 수 있습니다 — 이는 설계된 방어가 아니라 알려진 한계로 남겨둡니다. 전역 요청 빈도 제한(IP 기준 등)은 이번 범위에 포함하지 않으며 별도 후속 이슈로 다룹니다.
- 재설정 완료(`POST /api/v1/auth/password-reset/confirm`)는 `token`·`new_password`를 받아 원자적 일회성 소비로 처리합니다. **재설정 성공 자체는 `password_reset_token` 소지만으로 인증되므로 anti-enumeration을 적용하지 않습니다.**

| 단계 | 입력 | 처리 | 응답 원칙 |
| --- | --- | --- | --- |
| 재설정 요청 | 이메일 | 계정이 존재하고 쿨다운이 아니면 `password_reset_token`을 생성하고 원문 토큰은 사용자 전달 경로로만 사용합니다. DB에는 `token_hash`만 저장합니다. | 계정 존재 여부를 노출하지 않도록 존재/미존재 모두 같은 형태로 응답하고, `PASSWORD_RESET_RESPONSE_TARGET_SECONDS`까지 응답을 지연시켜 처리시간도 맞춥니다(부하가 심한 구간은 잔존 리스크로 남음). |
| 재설정 완료 | 원문 재설정 토큰, 새 비밀번호 | `token_hash`, `used_at IS NULL`, `expires_at > now()` 조건으로 토큰을 원자적으로 소비하고, 같은 transaction에서 비밀번호 해시 저장, `token_version + 1`, 같은 사용자의 나머지 미사용·미만료 토큰 소비를 함께 처리합니다. | 성공 시 새 access/refresh token을 발급하지 않고 재로그인을 요구합니다. |

재설정 완료 transaction은 아래 순서를 하나의 commit 단위로 처리합니다. 같은 사용자의 서로 다른 유효 token이 동시에 제출되어도 모든 완료 transaction이 **user row → password reset token row** 순서로 잠금을 획득합니다.

1. 요청 schema와 새 비밀번호 정책을 검증합니다. 실패하면 token, 비밀번호, `token_version`을 변경하지 않습니다(`422 VALIDATION_FAILED`, `details[].field=new_password`, `reason=PASSWORD_POLICY_VIOLATION`).
2. 원문 토큰을 서버에서 hash로 변환합니다.
3. `token_hash`로 candidate token의 `user_id`를 조회합니다. 이 조회에서는 token row를 잠그거나 소비하지 않습니다. candidate가 없으면 실패 응답을 반환합니다.
4. 대상 user row를 `FOR UPDATE`로 먼저 잠급니다.
5. user lock을 보유한 상태에서 제출된 token이 `used_at IS NULL`, `expires_at > now()`를 충족하는지 다시 확인합니다.
6. 같은 사용자의 미사용·미만료 `password_reset_token` row를 `id` 오름차순으로 잠그고 모두 소비 처리합니다. 제출된 token도 이 집합에 포함됩니다.
7. 대상 사용자의 비밀번호 hash를 새 값으로 저장합니다.
8. 대상 사용자의 `token_version`을 원자적으로 `+1`합니다.
9. transaction을 commit합니다.

3~6단계 중 하나라도 조건에 맞지 않으면(candidate 없음, 이미 사용됨, 만료됨, 제출된 token이 6단계에서 잠근 집합에 없음) 비밀번호와 `token_version`을 변경하지 않고 `422 VALIDATION_FAILED`, `details[].field=token`, `reason=RESET_TOKEN_INVALID`를 반환합니다(사유를 세분화하지 않습니다).

| 항목 | 기준 |
| --- | --- |
| 원문 토큰 저장 | 금지. DB에는 `token_hash`만 저장합니다. |
| 계정 존재 여부 | 재설정 요청 응답과 오류 문구에서 노출하지 않습니다. |
| 토큰 재사용 | `used_at` 조건부 갱신으로 차단합니다. |
| 만료 토큰 | 비밀번호를 변경하지 않습니다. |
| 기존 세션 | `token_version + 1`로 전부 무효화합니다. |
| 성공 후 세션 | 새 토큰을 발급하지 않고 재로그인을 요구합니다. |
| 로그·오류 응답 | 원문 토큰, 새 비밀번호, 비밀번호 hash, 계정 존재 여부 추론 정보를 남기지 않습니다. |

## Post-MVP 이관

- 가입 후 `gender`, `birthday`, `phone_number` 등 추가 개인정보·건강정보 입력 및 저장
- `PATCH /api/v1/users/me`에서 위 필드를 수정 대상으로 확장
- 회원탈퇴 API의 세부 transaction 구현
- 실제 외부 Email Provider 연동, 정교한 rate limit

## 검증과 변경 규칙

구현 계약은 `backend/app/tests/auth_apis`, `backend/app/tests/user_apis`, `backend/app/tests/services/test_auth_service.py`(로그인·refresh rotation·비밀번호 재설정 동시성), `tests/migration/test_refresh_rotation_password_reset_migration.py`, `tests/migration/test_email_verification_token_migration.py`에서 검증합니다.

다음 변경은 이 문서, 구현, API 문서와 관련 테스트를 같은 PR에서 갱신해야 합니다.

- 회원가입·내 정보 수정 요청에서 허용하는 필드의 추가·삭제·필수 여부 변경
- `USER` 테이블의 nullable 필드 범위 변경
- 이메일 정규화, 저장, 조회 및 중복 비교 기준 변경
- 인증 토큰 payload, `token_version` 재검증, 로그아웃 세션 무효화 기준 변경
- refresh token rotation·재사용 탐지 기준, 비밀번호 재설정 lock 순서·오류 코드 변경
- 회원가입 이메일 인증 목적·만료·쿨다운·오류 코드 변경

## #398 통합 권한 (PR #429 리뷰 대상)

병합된 #404의 로그인·refresh rotation·비밀번호 재설정은 Python Service/Repository를 유지한다. `refresh_session`, `password_reset_token`, `email_verification_token`에는 Runtime SELECT/INSERT를 허용하며 UPDATE는 각각 `refresh_session(active_jti, updated_at)`, `password_reset_token(used_at)`, `email_verification_token(verified_at)` 컬럼으로 제한한다. token identity·유효기간 덮어쓰기와 DELETE/TRUNCATE는 허용하지 않는다. Source Writer·관리 Writer에는 인증 테이블 접근 권한이 없다. 새 테이블의 권한을 포괄 GRANT나 DB Trigger로 해결하지 않는다. migration→head 검증→역할 provisioning이 완료된 뒤 서비스를 시작한다. 실제 운영 DB 적용 증빙은 별도다.
