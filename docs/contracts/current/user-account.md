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

이 절은 [`PD-206`](../../governance/decisions/2026-09-02-account-lifecycle-contract.md) 중 현재 구현된 로그아웃과 토큰 재검증 범위를 기록합니다. 비밀번호 재설정과 회원탈퇴 API는 아직 현재 실행 계약이 아니며 후속 구현 범위입니다.

- `User.account_status`는 `ACTIVE`, `WITHDRAWAL_REQUESTED`, `WITHDRAWN` 중 하나입니다.
- `User.token_version`은 access/refresh token 무효화 판정에 사용하는 정수 카운터이며 기본값은 `0`입니다.
- access token과 refresh token payload에는 발급 시점의 `token_version`을 포함합니다.
- 모든 인증된 요청은 `get_request_user()`에서 DB의 사용자 상태를 다시 조회합니다.
- `account_status != ACTIVE`, `is_active=false`, 또는 토큰의 `token_version != user.token_version`이면 `401 INVALID_TOKEN`을 반환합니다.
- `GET /api/v1/auth/token/refresh`도 DB를 조회해 refresh token 자체의 `token_version`을 `user.token_version`과 비교합니다. 일치하지 않으면 새 access token을 발급하지 않고 `401 INVALID_TOKEN`을 반환합니다.
- `POST /api/v1/auth/logout`은 현재 사용자의 `token_version`을 DB에서 원자적으로 `+1`하고, 응답에서 `refresh_token` httponly 쿠키를 만료·삭제합니다.
- 로그아웃 후 기존 access token으로 보호 API에 접근하거나 기존 refresh token으로 토큰 갱신을 시도하면 `401 INVALID_TOKEN`을 반환합니다.
- 현재 구현은 기기·세션 단위 로그아웃을 구분하지 않습니다. 한 기기에서 로그아웃하면 같은 사용자의 기존 access/refresh token이 함께 무효화됩니다.

## Post-MVP 이관

- 가입 후 `gender`, `birthday`, `phone_number` 등 추가 개인정보·건강정보 입력 및 저장
- `PATCH /api/v1/users/me`에서 위 필드를 수정 대상으로 확장
- 비밀번호 재설정 API와 회원탈퇴 API의 세부 transaction 구현

## 검증과 변경 규칙

구현 계약은 `backend/app/tests/auth_apis`, `backend/app/tests/user_apis`에서 검증합니다.

다음 변경은 이 문서, 구현, API 문서와 관련 테스트를 같은 PR에서 갱신해야 합니다.

- 회원가입·내 정보 수정 요청에서 허용하는 필드의 추가·삭제·필수 여부 변경
- `USER` 테이블의 nullable 필드 범위 변경
- 이메일 정규화, 저장, 조회 및 중복 비교 기준 변경
- 인증 토큰 payload, `token_version` 재검증, 로그아웃 세션 무효화 기준 변경
