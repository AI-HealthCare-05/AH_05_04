# 계정 생명주기 후속 계약 v1

**문서 성격**: 후속 구현 계약 (제안)
**담당자**: 송은영
**우선순위**: P0
**관련 트랙**: Account/Auth
**근거 Decision**: [Product Decision `PD-206-20260902`](../../governance/decisions/2026-09-02-account-lifecycle-contract.md) — 결정 배경·대안 검토·거부된 설계(jti 폐기 목록, 타임스탬프 비교)의 근거는 이 Decision 문서를 참고한다.

로그아웃과 `token_version` 기반 access/refresh token 재검증은 현재 구현 계약인 [회원가입·사용자 정보 계약](../current/user-account.md)에 반영되어 있다. 이 문서는 아직 구현되지 않은 비밀번호 재설정과 회원탈퇴의 후속 transaction 경계만 관리한다.

이 문서의 `P0`는 구현 릴리즈 순서가 아니라 구현 전에 먼저 고정해야 하는 계약 확정 우선순위를 뜻한다. 요구사항정의서 기준으로 비밀번호 재설정은 Post-MVP-1, 회원탈퇴는 Post-MVP-2 범위이며, 실제 API·DB 구현이 완료되기 전에는 current 계약으로 승격하지 않는다.

## 1) 계정 상태

`User`는 다음 상태를 갖는다.

- `account_status`: `ACTIVE` | `WITHDRAWAL_REQUESTED` | `WITHDRAWN`
- `withdrawal_requested_at`, `withdrawn_at`: nullable timestamp
- 기존 `is_active`는 `account_status`가 `ACTIVE`가 아니게 되는 시점에 함께 `false`로 전환되며, 두 컬럼은 항상 같은 상태 전이 지점에서만 함께 갱신된다(개별 대입 금지).

회원탈퇴의 실제 개인정보·건강정보 삭제·보존 처리 상태는 `account_status`에 섞지 않고 Account 전용 `account_deletion_request` 테이블에서 관리한다. `account_status`는 로그인 가능 여부를 판단하는 큰 상태로만 사용한다.

| `account_status` | 의미 | 로그인·보호 API 접근 | 시각 컬럼 |
| --- | --- | --- | --- |
| `ACTIVE` | 정상 이용 가능한 계정 | 허용 | `withdrawal_requested_at=NULL`, `withdrawn_at=NULL` |
| `WITHDRAWAL_REQUESTED` | 탈퇴 요청이 접수되어 계정 이용을 즉시 종료한 상태 | 차단 | `withdrawal_requested_at` 필수, `withdrawn_at=NULL` |
| `WITHDRAWN` | 개인정보·건강정보 삭제·보존 처리가 완료된 탈퇴 계정 | 차단 | `withdrawal_requested_at` 필수, `withdrawn_at` 필수 |

허용되는 `account_status` 전이는 아래로 제한한다.

| 현재 상태 | 다음 상태 | 전이 조건 | 함께 처리할 값 |
| --- | --- | --- | --- |
| `ACTIVE` | `WITHDRAWAL_REQUESTED` | 비밀번호 재입력 재인증과 최종 확인 성공 | `is_active=false`, `withdrawal_requested_at=now()`, `token_version + 1`, `account_deletion_request.status=PENDING` 생성 |
| `WITHDRAWAL_REQUESTED` | `WITHDRAWN` | PM/Privacy 정책에 맞춘 삭제·보존 처리 완료 | `withdrawn_at=now()`, `account_deletion_request.status=COMPLETED` |

`WITHDRAWAL_REQUESTED` 또는 `WITHDRAWN`에서 `ACTIVE`로 되돌리는 전이는 이 계약에 없다. 실패나 rollback이 발생하면 성공 상태처럼 보이는 부분 전이를 남기지 않고, 이미 커밋된 탈퇴 요청의 삭제·보존 처리 실패는 `account_deletion_request.status=FAILED`로 남겨 운영 재시도 또는 확인 대상으로 관리한다.

## 2) 세션 무효화 — `token_version`

- `User.token_version`: integer, not null, 기본값 `0`.
- 비밀번호 재설정·회원탈퇴마다 원자적으로 `+1`.
- 토큰 payload와 인증 재검증 규칙은 현재 구현 계약인 [회원가입·사용자 정보 계약](../current/user-account.md)을 따른다.
- 세션 무효화는 기기·세션 단위로 구분되지 않는다 — 비밀번호 재설정·회원탈퇴가 같은 사용자의 모든 기기 세션을 함께 무효화한다.
- `token_version`은 정수 카운터일 뿐 PII가 아니며, JWT 서명으로 변조가 불가능해 노출에 따른 추가 위험이 없다.

세션 무효화는 토큰의 개별 `jti` 폐기 목록이나 발급 시각 비교가 아니라 `token_version`의 정확한 일치 비교로 처리한다.

| 상황 | 처리 |
| --- | --- |
| 로그인 성공 | 현재 DB의 `user.token_version` 값을 access token과 refresh token payload에 포함한다. |
| 보호 API 요청 | token payload의 `token_version`과 DB의 `user.token_version`이 정확히 일치하는지 매 요청마다 확인한다. |
| 토큰 갱신 | refresh token payload의 `token_version`과 DB의 `user.token_version`이 정확히 일치할 때만 새 access token을 발급한다. |
| 로그아웃 | 현재 구현처럼 `user.token_version`을 원자적으로 `+1`하고 refresh cookie를 만료한다. |
| 비밀번호 재설정 성공 | 비밀번호 변경 transaction 안에서 `user.token_version`을 원자적으로 `+1`한다. |
| 회원탈퇴 확정 | `WITHDRAWAL_REQUESTED` 전이 transaction 안에서 `user.token_version`을 원자적으로 `+1`한다. |

`account_status != ACTIVE`, `is_active=false`, 또는 token payload의 `token_version != user.token_version`이면 인증 실패로 처리한다. 보호 API와 refresh token 재발급 모두 기존 공통 오류 계약의 `401 INVALID_TOKEN`을 반환하며, 계정 상태·탈퇴 여부·토큰 버전 차이 같은 내부 사유를 사용자 응답에 세분화해 노출하지 않는다.

`token_version`은 응답 body, 로그, 오류 `details`에 디버깅 목적으로 출력하지 않는다. JWT payload 안의 값은 서명으로 보호되는 무효화 카운터일 뿐 개인정보나 의료정보가 아니지만, 운영 로그와 오류 응답에는 인증 판단에 필요한 최소 정보만 남긴다.

## 3) 비밀번호 재설정

- `password_reset_token(id, user_id, token_hash, created_at, expires_at, used_at)` — 원문 토큰은 저장하지 않고 해시만 저장한다.
- 존재하지 않는 계정 요청도 존재하는 계정과 동일한 응답 형태·유사 처리시간을 반환한다(계정 존재 여부 비노출). 재설정 성공 자체는 `password_reset_token`만으로 인증되므로 anti-enumeration을 적용하지 않는다.
- 토큰 소비는 원자적 일회성 소비다: 토큰 소비(`used_at` 조건부 갱신), 비밀번호 변경, `token_version` 증가를 단일 transaction에서 처리한다. 영향받은 row가 0건이면 이미 사용됐거나 만료된 토큰으로 간주해 실패 응답을 반환한다.
- **같은 transaction에서 해당 사용자의 나머지 미사용·미만료 `password_reset_token`도 함께 소비 처리한다** — 한 사용자에게 유효한 재설정 토큰이 여러 개 있어도 재설정 성공 이후에는 전부 무효가 된다.
- 재설정 성공 후 재로그인을 요구한다. 성공 응답은 새 access/refresh token을 발급하지 않고, 토큰 등 세션 관련 정보를 포함하지 않는다.
- rate limit 기준(횟수/기간)은 구현 PR에서 확정한다.

비밀번호 재설정은 요청 단계와 완료 단계를 분리한다.

| 단계 | 입력 | 처리 | 응답 원칙 |
| --- | --- | --- | --- |
| 재설정 요청 | 이메일 등 확정된 본인 확인 입력 | 계정이 존재하고 정책상 발송 가능하면 `password_reset_token`을 생성하고 원문 토큰은 사용자 전달 경로로만 사용한다. DB에는 `token_hash`만 저장한다. | 계정 존재 여부를 노출하지 않도록 존재/미존재 모두 같은 형태의 응답을 반환한다. |
| 재설정 완료 | 원문 재설정 토큰, 새 비밀번호 | `token_hash`, `used_at IS NULL`, `expires_at > now()` 조건으로 토큰을 원자적으로 소비하고, 같은 transaction에서 비밀번호 해시 저장, `token_version + 1`, 같은 사용자의 나머지 미사용·미만료 토큰 소비를 함께 처리한다. | 성공 시 새 access/refresh token을 발급하지 않고 재로그인을 요구한다. |

재설정 완료 transaction은 아래 순서를 하나의 commit 단위로 처리한다.

1. 원문 토큰을 서버에서 hash로 변환한다.
2. `password_reset_token`을 조건부로 소비한다(`used_at IS NULL`, `expires_at > now()`).
3. 조건에 맞는 row가 없으면 비밀번호를 변경하지 않고 실패 응답을 반환한다.
4. 대상 사용자의 비밀번호 hash를 새 값으로 저장한다.
5. 대상 사용자의 `token_version`을 원자적으로 `+1`한다.
6. 같은 사용자의 나머지 미사용·미만료 `password_reset_token`을 모두 소비 처리한다.
7. transaction을 commit한다.

보안 규칙은 다음과 같다.

| 항목 | 기준 |
| --- | --- |
| 원문 토큰 저장 | 금지. DB에는 `token_hash`만 저장한다. |
| 계정 존재 여부 | 재설정 요청 응답과 오류 문구에서 노출하지 않는다. |
| 토큰 재사용 | `used_at` 조건부 갱신으로 차단한다. |
| 만료 토큰 | 비밀번호를 변경하지 않는다. |
| 기존 세션 | `token_version + 1`로 전부 무효화한다. |
| 성공 후 세션 | 새 토큰을 발급하지 않고 재로그인을 요구한다. |
| 로그·오류 응답 | 원문 토큰, 새 비밀번호, 비밀번호 hash, 계정 존재 여부 추론 정보를 남기지 않는다. |

## 4) 회원탈퇴

탈퇴 대상은 `get_request_user()` 결과의 `user_id`로만 결정한다. 요청 body나 path parameter로 받은 `user_id`를 기준으로 다른 계정을 탈퇴 처리하지 않는다.

재인증은 현재 access token이 유효한지만 확인하는 것이 아니라, `authenticate()`와 동일한 비밀번호 검증 경로로 현재 비밀번호를 다시 확인하는 것을 의미한다. 최종 확인 신호의 구체적인 필드명은 구현 PR의 OpenAPI에서 확정하되, 재인증과 최종 확인이 모두 성공하기 전에는 어떤 계정 상태도 변경하지 않는다.

탈퇴 요청 접수 transaction은 아래 순서로 처리한다.

| 순서 | 처리 | 계약 |
| --- | --- | --- |
| 1 | 탈퇴 대상 확인 | 현재 인증 사용자만 대상으로 삼고, 별도 `user_id` 입력은 받지 않는다. |
| 2 | 비밀번호 재입력 재인증 | 실패하면 계정 상태, token, deletion request를 변경하지 않는다. |
| 3 | 최종 확인 신호 검증 | 누락 또는 불일치 시 저장하지 않고 validation 오류로 종료한다. |
| 4 | 사용자 row 잠금과 상태 확인 | `account_status='ACTIVE'`, `is_active=true`인 계정만 탈퇴 요청을 새로 접수한다. |
| 5 | 계정 이용 종료 처리 | `account_status=WITHDRAWAL_REQUESTED`, `is_active=false`, `withdrawal_requested_at=now()`를 같은 transaction에서 저장한다. |
| 6 | 세션 무효화 | 같은 transaction에서 `token_version`을 원자적으로 `+1`한다. |
| 7 | 삭제 요청 기록 생성 | 같은 transaction에서 `account_deletion_request.status=PENDING` row를 생성한다. |
| 8 | commit 이후 응답 | refresh token cookie를 만료시키고 "탈퇴되었습니다." 수준의 단순 완료 응답을 반환한다. |

위 transaction은 조건부 원자적 전이(`WHERE account_status='ACTIVE'`)로 구현한다. 영향받은 user row가 0이면 이미 탈퇴 요청이 접수되었거나 탈퇴 완료된 계정으로 보고 새 `account_deletion_request`를 만들지 않으며, 사용자에게는 동일한 탈퇴 완료 응답을 반환한다. 재인증 성공 후 아주 좁은 경쟁 구간에서 중복 요청이 들어와도 계정 상태와 삭제 요청 row가 중복 생성되면 안 된다.

개인정보·건강정보 삭제·보존 처리는 사용자에게 별도 상태 조회 API를 제공하지 않고 Backend 내부 처리로 진행한다. 탈퇴 기능을 실제 사용자에게 제공하는 구현 PR은 `account_deletion_request.status=PENDING` row만 만들고 종료하지 않으며, PM/Privacy가 확정한 삭제·보존 정책에 맞춰 삭제·보존 처리와 최종 계정 상태 전이를 함께 포함한다.

삭제·보존 처리 상태는 Track A `AI_JOB` 상태 머신을 재사용하지 않고 Account 전용 `account_deletion_request`가 관리한다. 상태값과 계정 상태 정합성은 [5) account_deletion_request 테이블](#5-account_deletion_request-테이블)을 따른다.

삭제·보존 처리 중 rollback이 발생하면 성공 상태처럼 보이는 부분 전이를 남기지 않는다. 탈퇴 요청 transaction 자체가 commit되기 전 실패하면 `account_status`는 `ACTIVE`로 유지되고 `account_deletion_request`도 생성되지 않는다. 탈퇴 요청은 commit됐지만 후속 삭제·보존 처리에서 실패하면 `account_status=WITHDRAWAL_REQUESTED`를 유지하고 `account_deletion_request.status=FAILED`로 남겨 운영·감사 기준으로 확인한다.

실패 사유는 새 사용자 노출 오류 코드를 늘리지 않고, 필요 시 `TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `INTERNAL_ERROR` 같은 공통 내부 실패 사유를 재사용한다. 단, 이 값은 사용자 응답이나 화면에 내부 오류 상세로 노출하지 않는다.

사용자-facing 범위는 탈퇴 요청 성공 응답까지로 제한한다. 탈퇴 처리 상태는 사용자에게 조회 API나 앱 내부 알림으로 제공하지 않는다. Frontend는 탈퇴 성공 응답을 받으면 로컬 인증 정보를 제거하고 완료 화면을 보여준다. 완료 화면에 필요한 삭제·보존 안내 문구, 재가입 제한, 법정 보존 기간, 즉시 폐기 대상, 이메일 안내 여부는 이 계약에서 임의로 정하지 않고 PM/Privacy 정책을 기준으로 표시한다.

요구사항정의서 REQ-USR-008의 "삭제 요청 처리 상태 안내"는 탈퇴 후 별도 상태 조회 API를 제공한다는 뜻이 아니라, 탈퇴 성공 응답과 완료 화면에서 계정 이용 종료 및 삭제·보존 처리 기준을 사용자에게 안내하는 것으로 해석한다. 내부 처리의 세부 상태(`PENDING`, `IN_PROGRESS`, `FAILED`, `COMPLETED`)는 운영·감사·재처리용으로만 사용한다.

## 5) account_deletion_request 테이블

`account_deletion_request`는 회원탈퇴 요청 이후 개인정보·건강정보 삭제·보존 처리의 감사 기준 테이블이다. 사용자 로그인 가능 여부는 `user.account_status`가 판단하고, 삭제·보존 처리의 대기·진행·완료·실패 상태와 재처리 근거는 이 테이블이 관리한다.

### 5.1 최소 컬럼

| 컬럼 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `id` | UUID | O | 탈퇴 처리 요청 ID |
| `user_id` | UUID FK | O | 탈퇴 요청 사용자 |
| `status` | enum/string | O | `PENDING`, `IN_PROGRESS`, `COMPLETED`, `FAILED` 중 하나 |
| `requested_at` | datetime | O | 탈퇴 요청 transaction이 커밋된 시각 |
| `started_at` | datetime nullable | X | 삭제·보존 처리 시작 시각 |
| `completed_at` | datetime nullable | X | 삭제·보존 처리 완료 시각 |
| `failed_at` | datetime nullable | X | 마지막 실패 시각 |
| `retry_count` | integer | O | 내부 재시도 횟수. 기본값은 `0` |
| `last_error_code` | string nullable | X | 운영 확인용 내부 실패 사유 |
| `created_at` | datetime | O | row 생성 시각 |
| `updated_at` | datetime | O | 마지막 갱신 시각 |

`last_error_code`에는 원문 예외 메시지, 이메일, 전화번호, 이름, 의료문서 원문, OCR 원문, access/refresh token, reset token, provider 응답 원문을 저장하지 않는다.

### 5.2 상태 전이

| 현재 상태 | 다음 상태 | 조건 |
| --- | --- | --- |
| 없음 | `PENDING` | `ACTIVE → WITHDRAWAL_REQUESTED` 전이 transaction 안에서 생성 |
| `PENDING` | `IN_PROGRESS` | 삭제·보존 처리 시작 |
| `IN_PROGRESS` | `COMPLETED` | PM/Privacy 정책에 따른 삭제·보존 처리 완료 |
| `IN_PROGRESS` | `FAILED` | 삭제·보존 처리 실패 또는 재시도 필요 |
| `FAILED` | `IN_PROGRESS` | 운영 재시도 또는 자동 재처리 시작 |

`COMPLETED`는 terminal 상태다. `COMPLETED` 이후 `PENDING`, `IN_PROGRESS`, `FAILED`로 되돌리지 않는다.

### 5.3 계정 상태와의 정합성

| `account_deletion_request.status` | 허용되는 `user.account_status` | 정합성 규칙 |
| --- | --- | --- |
| `PENDING` | `WITHDRAWAL_REQUESTED` | 계정 접근은 이미 차단되어 있어야 한다. |
| `IN_PROGRESS` | `WITHDRAWAL_REQUESTED` | 처리 중에도 로그인과 보호 API 접근은 차단한다. |
| `FAILED` | `WITHDRAWAL_REQUESTED` | 삭제·보존 처리는 실패했지만 계정 접근 차단은 유지한다. |
| `COMPLETED` | `WITHDRAWN` | 완료 시 `withdrawn_at`도 함께 저장되어야 한다. |

`account_deletion_request.status=COMPLETED`인데 `user.account_status != WITHDRAWN`이거나, `user.account_status=WITHDRAWN`인데 완료된 deletion request가 없으면 데이터 정합성 오류로 본다.

### 5.4 중복 생성 방지

한 사용자에게 동시에 활성 탈퇴 요청 row가 여러 개 생기면 안 된다. 구현 PR에서는 terminal 전 요청이 사용자별로 1개만 존재하도록 DB 제약을 먼저 둔다.

- `user_id` 기준으로 `PENDING`/`IN_PROGRESS`/`FAILED` 요청이 1개만 존재하도록 partial unique index를 적용한다.
- 탈퇴 요청 생성 시 user row lock과 조건부 update를 함께 사용해 application transaction 안에서도 중복 전이를 막는다.

애플리케이션에서 먼저 조회한 뒤 insert하는 방식만으로는 동시 요청을 막을 수 없으므로 DB 제약 또는 row lock 기반 원자적 처리가 필요하다.

### 5.5 내부 운영 상태와 사용자-facing 정보 분리

이 테이블의 상세 상태는 Backend 운영·감사·재처리용 정보이며 사용자-facing API로 제공하지 않는다. 사용자는 탈퇴 요청 성공 시점에 계정 이용이 종료되었다는 완료 안내만 받는다.

운영자는 이 테이블을 통해 아래 항목을 확인할 수 있어야 한다.

- 탈퇴 요청 접수 시각
- 삭제·보존 처리 시작·완료·실패 시각
- 실패 여부와 내부 실패 사유
- 재시도 횟수
- 삭제·보존 처리가 최종 완료되었는지 여부

운영 확인 화면이나 로그를 만들더라도 민감정보 원문을 노출하지 않고, 사용자 식별자는 필요한 범위에서만 사용한다.

## 6) PM/Privacy 정책 경계

이 계약은 계정 상태, token 무효화, 비밀번호 재설정 token, 회원탈퇴 transaction, `account_deletion_request`의 저장·상태 기준을 정한다. 개인정보·건강정보를 어떤 기준으로 삭제하거나 보존할지는 PM/Privacy 정책을 기준으로 구현한다.

| 구분 | 이 계약에서 정하는 것 | PM/Privacy 정책에서 정할 것 |
| --- | --- | --- |
| 탈퇴 요청 접수 | 재인증, 최종 확인, `WITHDRAWAL_REQUESTED`, `token_version + 1`, `account_deletion_request.status=PENDING` 생성 | 탈퇴 전 사용자에게 고지할 문구와 법적 안내 |
| 계정 접근 차단 | 탈퇴 요청 commit 이후 로그인·보호 API 접근 차단 | 탈퇴 후 재가입 제한이 필요한지 여부 |
| 삭제·보존 처리 | 처리 상태를 `account_deletion_request`에 남기고 완료 시 `WITHDRAWN`으로 전환 | 즉시 폐기 대상, 법정 보존 대상, 보존 기간, 삭제 예외 사유 |
| 사용자 안내 | 탈퇴 성공 응답과 Frontend 로컬 인증 정보 제거. 내부 처리 상태 조회 API는 제공하지 않음 | 완료 화면의 삭제·보존 안내 문구, 완료 이후 이메일 등 별도 통지 필요 여부 |
| 운영·감사 | 요청·시작·완료·실패 시각, 재시도 횟수, 내부 실패 사유 저장 | 감사 증빙에 필요한 보존 항목과 접근 권한 |
| 오류·로그 | 사용자 응답과 로그에 민감정보 원문을 남기지 않음 | 개인정보처리방침·이용약관·내부 운영 정책 문구 |

PM/Privacy 정책이 확정되지 않은 항목을 Backend에서 임의로 정하지 않는다. 특히 완료 화면 고지 문구, 재가입 제한 기간, 법정 보존 기간, 이메일 통지 여부, 삭제 제외 대상은 구현 PR에서 하드코딩하지 않고 승인된 정책 문서나 이슈를 근거로 연결한다.

## 7) 오류 응답 기준

계정 생명주기 API는 기존 [Backend 공통 오류 응답 계약](../current/backend-error-response.md)의 `{code, message, details, trace_id}` 형식과 오류 코드를 재사용한다. 이 계약만을 위해 새 사용자 노출 오류 코드를 추가하지 않는다.

| 상황 | HTTP | code | 기준 |
| --- | ---: | --- | --- |
| access token 없음 | 401 | `UNAUTHORIZED` | 보호 API 공통 인증 누락 기준을 따른다. |
| access token 형식 오류, 서명 오류, token type 오류 | 401 | `INVALID_TOKEN` | 토큰이 유효하지 않으므로 재로그인이 필요하다. |
| access token 만료 | 401 | `EXPIRED_TOKEN` | refresh fallback이 없는 보호 API 요청에서는 만료 오류를 반환한다. |
| `account_status != ACTIVE` | 401 | `INVALID_TOKEN` | 탈퇴 요청·탈퇴 완료 계정임을 사용자 응답에서 세분화하지 않는다. |
| `is_active=false` | 401 | `INVALID_TOKEN` | 비활성 사유를 사용자 응답에서 세분화하지 않는다. |
| token payload의 `token_version`과 DB 값 불일치 | 401 | `INVALID_TOKEN` | 로그아웃·비밀번호 재설정·회원탈퇴로 무효화된 토큰이다. |
| 비밀번호 재설정 요청의 이메일 형식 오류 | 422 | `VALIDATION_FAILED` | Pydantic 또는 Service validation 기준을 따른다. |
| 비밀번호 재설정 완료의 token 누락·형식 오류 또는 새 비밀번호 형식 오류 | 422 | `VALIDATION_FAILED` | 원문 token과 새 비밀번호는 `details[].rejected_value`에 넣지 않는다. |
| 비밀번호 재설정 token이 만료·사용됨·존재하지 않음 | 422 | `VALIDATION_FAILED` | 비밀번호를 변경하지 않는다. 계정 존재 여부나 token 존재 여부를 세분화해 노출하지 않는다. |
| 회원탈퇴 재인증 비밀번호 불일치 | 401 | `UNAUTHORIZED` | 로그인 실패와 같은 수준의 메시지를 사용하고 계정 상태는 변경하지 않는다. |
| 회원탈퇴 최종 확인 신호 누락·불일치 | 422 | `VALIDATION_FAILED` | 계정 상태, token, deletion request를 변경하지 않는다. |
| 예상하지 못한 서버 오류 | 500 | `INTERNAL_SERVER_ERROR` | 공통 500 fallback 기준을 따른다. 민감정보 원문을 message/details/log에 남기지 않는다. |

비밀번호 재설정 요청 단계에서는 계정 존재 여부를 노출하지 않는다. 존재하지 않는 이메일이어도 가능한 한 존재하는 계정과 같은 형태의 성공 응답을 반환하며, 계정 없음 여부를 `USER_NOT_FOUND` 같은 별도 오류 코드로 노출하지 않는다.

회원탈퇴는 요청 URL이나 body로 다른 사용자의 ID를 받지 않으므로 다른 사용자 리소스 접근을 의미하는 `404 *_NOT_FOUND` 오류를 새로 만들지 않는다. 탈퇴 대상은 항상 현재 인증 사용자이며, 인증에 실패하면 위 401 계열 오류를 따른다.

계정 생명주기 구현 중 새 오류 코드가 필요해지면 먼저 [Backend 공통 오류 응답 계약](../current/backend-error-response.md)의 "새 오류 코드 추가 기준"을 따른다. 승인된 Decision 또는 Contract Freeze 없이 `ACCOUNT_*`, `PASSWORD_RESET_*`, `WITHDRAWAL_*` 같은 새 공개 오류 코드를 추가하지 않는다.

## 8) 검증 기준

계정 생명주기 구현 PR은 아래 항목을 자동 테스트 또는 명시적인 수동 검증으로 확인한다. 문서만 갱신하는 PR에서는 구현 테스트를 요구하지 않지만, 후속 구현 PR은 이 기준을 완료 조건으로 삼는다.

| 영역 | 검증 항목 | 성공 기준 |
| --- | --- | --- |
| 현재 구현 회귀 | 로그인, 토큰 갱신, 로그아웃, 내 정보 조회 | 기존 정상 흐름이 유지되고 `token_version` 재검증이 깨지지 않는다. |
| `token_version` 무효화 | 로그아웃, 비밀번호 재설정 성공, 회원탈퇴 요청 | 각 transaction 이후 기존 access/refresh token으로 보호 API 또는 token refresh를 호출하면 `401 INVALID_TOKEN`을 반환한다. |
| 비밀번호 재설정 요청 | 존재하는 이메일과 존재하지 않는 이메일 | 계정 존재 여부를 응답 message, status, details로 구분할 수 없다. |
| 비밀번호 재설정 완료 | 유효 token, 만료 token, 이미 사용된 token, 잘못된 token | 유효 token만 비밀번호 변경과 `token_version + 1`을 수행하고, 실패 케이스는 비밀번호와 세션 상태를 변경하지 않는다. |
| 비밀번호 재설정 token 보안 | DB 저장값과 로그 | 원문 token과 새 비밀번호가 DB, 오류 응답, 로그에 남지 않는다. |
| 회원탈퇴 재인증 | 올바른 비밀번호와 잘못된 비밀번호 | 올바른 비밀번호만 탈퇴 transaction을 시작하고, 실패 시 계정 상태·token·deletion request를 변경하지 않는다. |
| 회원탈퇴 transaction | 성공 요청 | `account_status=WITHDRAWAL_REQUESTED`, `is_active=false`, `withdrawal_requested_at`, `token_version + 1`, `account_deletion_request.status=PENDING`이 같은 commit 단위로 반영된다. |
| 회원탈퇴 중복 요청 | 거의 동시에 들어온 동일 사용자 탈퇴 요청 | 활성 `account_deletion_request`가 사용자별 1개만 생성되고, 계정 상태와 `token_version`이 중복으로 증가하지 않는다. |
| 삭제·보존 처리 완료 | PM/Privacy 정책에 따른 처리 성공 | `account_deletion_request.status=COMPLETED`, `completed_at`, `user.account_status=WITHDRAWN`, `withdrawn_at`이 정합성을 유지한다. |
| 삭제·보존 처리 실패 | 처리 중 예외 또는 rollback | 성공 상태처럼 보이는 부분 전이를 남기지 않고, 이미 접수된 탈퇴 요청은 `WITHDRAWAL_REQUESTED` 접근 차단 상태와 `account_deletion_request.status=FAILED`로 운영 확인 가능해야 한다. |
| 사용자-facing 응답 | 회원탈퇴 성공 후 화면/API 응답 | 사용자는 탈퇴 완료 안내와 PM/Privacy 정책에 따른 삭제·보존 안내만 받고, `account_deletion_request.status`, `retry_count`, `last_error_code`를 응답으로 받지 않는다. |
| 오류 응답 | 인증 오류, validation 오류, 서버 오류 | 기존 `{code, message, details, trace_id}` 형식을 따르고 새 `ACCOUNT_*`, `PASSWORD_RESET_*`, `WITHDRAWAL_*` 공개 오류 코드를 만들지 않는다. |
| 민감정보 비노출 | 오류 응답과 로그 | 이메일 존재 여부, 원문 token, 비밀번호, refresh token, 의료문서/OCR 원문, provider 응답 원문이 `message`, `details[].rejected_value`, 로그에 남지 않는다. |
| PM/Privacy 경계 | 정책 미확정 항목 | 재가입 제한, 법정 보존 기간, 이메일 통지, 삭제 제외 대상은 승인된 정책 문서나 이슈 없이 하드코딩하지 않는다. |

구현 PR에서는 최소한 관련 단위 테스트, API 테스트, migration 테스트를 포함한다. 실제 이메일 Provider 연동이나 PM/Privacy 정책 확정 전에는 이메일 발송 성공 여부와 세부 보존 기간 검증을 완료 조건으로 두지 않는다.

## 제외 (이 계약이 다루지 않는 것)

- 보호자·멀티 프로필 계정 상태
- 동의 상태(`GRANTED`/`WITHDRAWN`) 모델링 — [#207](https://github.com/AI-HealthCare-05/AH_05_04/issues/207)
- 실제 이메일 발송 Provider 연동
- 비밀번호 재설정 rate limit 정확한 수치
- 개인정보·건강정보의 세부 보존 기간, 즉시 폐기 대상, 법정 보존 대상과 재가입 제한 여부의 정책 확정 — PM/Privacy 범위
- 탈퇴 후 사용자-facing 상태 조회 API와 앱 내부 완료·실패 알림 — 제공하지 않음
- 계정 이벤트 감사 로그 저장 여부
