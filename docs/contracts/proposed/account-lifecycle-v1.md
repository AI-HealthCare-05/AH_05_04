# 계정 생명주기(로그아웃·비밀번호 재설정·회원탈퇴) 계약 v1

**문서 성격**: Draft 공유 계약 (제안)
**담당자**: 송은영
**리뷰어**: 권가빈, 남한솔
**우선순위**: P0
**관련 트랙**: Account/Auth
**근거 Decision**: [Product Decision `PD-206-20260902`](../../governance/decisions/2026-09-02-account-lifecycle-contract.md) — 결정 배경·대안 검토·거부된 설계(jti 폐기 목록, 타임스탬프 비교)의 근거는 이 Decision 문서를 참고한다. 이 문서는 그 결정의 계약 부분만 요약한다.

## 1) 계정 상태

`User`는 다음 상태를 갖는다.

- `account_status`: `ACTIVE` | `WITHDRAWAL_REQUESTED` | `WITHDRAWN`
- `withdrawal_requested_at`, `withdrawn_at`: nullable timestamp
- 기존 `is_active`는 `account_status`가 `ACTIVE`가 아니게 되는 시점에 함께 `false`로 전환되며, 두 컬럼은 항상 같은 상태 전이 지점에서만 함께 갱신된다(개별 대입 금지).

회원탈퇴의 실제 건강정보 삭제·보존 처리 중 발생하는 부분 실패를 상태로 구분하는 방식(대기/처리 중/실패 등 하위 상태 도입 여부)은 아직 확정하지 않았다 — 구현 PR 착수 전 별도 확정한다.

## 2) 세션 무효화 — `token_version`

- `User.token_version`: integer, not null, 기본값 `0`.
- 로그아웃·비밀번호 재설정·회원탈퇴마다 원자적으로 `+1`.
- 토큰 payload에 발급 시점의 `token_version` 클레임을 포함한다.
- 검증 규칙: `token.token_version == user.token_version`. 일치하지 않으면(크든 작든) 만료 전이라도 무효.
- 모든 인증된 요청(`get_request_user()`)과 `GET /auth/token/refresh`가 이 규칙을 재검증한다. `token_refresh`는 새로 발급할 access token이 아니라 **제시된 refresh token 자체**의 `token_version`을 기준으로 판단한다.
- 세션 무효화는 기기·세션 단위로 구분되지 않는다 — 한 기기의 로그아웃/재설정/탈퇴가 같은 사용자의 다른 모든 기기 세션을 함께 무효화한다.
- `token_version`은 정수 카운터일 뿐 PII가 아니며, JWT 서명으로 변조가 불가능해 노출에 따른 추가 위험이 없다.

## 3) 로그아웃

- `token_version` 원자적 `+1`.
- 응답에서 `refresh_token` httponly 쿠키를 명시적으로 만료·삭제한다.
- 서버 요청 실패 시에도 클라이언트는 로컬 자격증명을 우선 제거한다.

## 4) 비밀번호 재설정

- `password_reset_token(id, user_id, token_hash, created_at, expires_at, used_at)` — 원문 토큰은 저장하지 않고 해시만 저장한다.
- 존재하지 않는 계정 요청도 존재하는 계정과 동일한 응답 형태·유사 처리시간을 반환한다(계정 존재 여부 비노출). 재설정 성공 자체는 `password_reset_token`만으로 인증되므로 anti-enumeration을 적용하지 않는다.
- 토큰 소비는 원자적 일회성 소비다: 토큰 소비(`used_at` 조건부 갱신), 비밀번호 변경, `token_version` 증가를 단일 transaction에서 처리한다. 영향받은 row가 0건이면 이미 사용됐거나 만료된 토큰으로 간주해 실패 응답을 반환한다.
- **같은 transaction에서 해당 사용자의 나머지 미사용·미만료 `password_reset_token`도 함께 소비 처리한다** — 한 사용자에게 유효한 재설정 토큰이 여러 개 있어도 재설정 성공 이후에는 전부 무효가 된다.
- 재설정 성공 후 재로그인을 요구한다. 성공 응답은 새 access/refresh token을 발급하지 않고, 토큰 등 세션 관련 정보를 포함하지 않는다.
- rate limit 기준(횟수/기간)은 구현 PR에서 확정한다.

## 5) 회원탈퇴

- 탈퇴 대상은 `get_request_user()` 결과(`user_id`)로만 결정하며, 재인증은 세션 유효성이 아니라 `authenticate()`와 동일한 경로의 **비밀번호 재입력 검증**을 의미한다.
- 재인증 성공 → 최종확인 → 단일 transaction으로 `account_status=WITHDRAWAL_REQUESTED`, `is_active=false`, `withdrawal_requested_at=now()` 커밋. 조건부 원자적 전이(`WHERE account_status = 'ACTIVE'`)로 구현하며, 영향받은 row가 0이면 이미 처리된 것으로 간주해 새 transaction 없이 현재 상태를 반환한다(멱등 처리, 재인증 성공 후 좁은 경쟁 구간에만 적용).
- 같은 transaction에서 `token_version`을 원자적으로 `+1`하고 `refresh_token` 쿠키를 종료한다.
- 실제 건강정보 삭제·보존 처리는 비동기로 진행하고, 완료 시 `account_status=WITHDRAWN`, `withdrawn_at=now()`로 전환한다. 부분 실패는 상태로 식별 가능해야 하며 재시도 가능해야 한다.
  - **초안(멘토 자문 후 확정)**: Track A `AI_JOB`을 재사용하지 않고 전용 `account_deletion_request(id, user_id, status, requested_at, completed_at, failed_at, retry_count, last_error_code)` 테이블을 둔다. `status`는 `PENDING → IN_PROGRESS → COMPLETED | FAILED`이며, 위 `WITHDRAWAL_REQUESTED` 전이와 같은 transaction에서 `PENDING` row를 생성한다. `account_status`는 로그인 가능 여부 게이트로만 유지한다. 아직 확정이 아니며 방향이 바뀔 수 있다.
- 실제 데이터 물리 삭제 실행은 `EXT-PRIV-001` 외부 Privacy 승인 전까지 수행하지 않는다.

## 제외 (이 계약이 다루지 않는 것)

- 보호자·멀티 프로필 계정 상태
- 동의 상태(`GRANTED`/`WITHDRAWN`) 모델링 — [#207](https://github.com/AI-HealthCare-05/AH_05_04/issues/207)
- 실제 이메일 발송 Provider 연동
- 비밀번호 재설정 rate limit 정확한 수치
- 건강정보 실제 물리 삭제 실행 (`EXT-PRIV-001` 승인 후)
- 계정 이벤트 감사 로그 저장 여부
