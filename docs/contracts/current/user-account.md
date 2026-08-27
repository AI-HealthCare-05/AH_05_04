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
- 로그인 요청의 `email`은 소문자로 정규화한 뒤 계정을 조회합니다.
- 이메일 중복 여부는 소문자로 정규화된 값을 기준으로 판단합니다. 따라서 대소문자만 다른 이메일은 같은 계정 식별자로 취급하며 중복 가입을 허용하지 않습니다.
- `GET /api/v1/users/me`와 `PATCH /api/v1/users/me` 응답의 `email`에는 DB에 저장된 소문자 정규화 값이 반환됩니다.

## 내 정보 조회·수정

- Endpoint: `GET /api/v1/users/me`, `PATCH /api/v1/users/me`
- 가입 직후 `gender`, `birthday`, `phone_number`는 `null`일 수 있습니다(`USER` 테이블 nullable).
- MVP의 `PATCH /api/v1/users/me`는 `name`, `email`만 수정 대상으로 받습니다(`extra="forbid"`).
- `gender`, `birthday`, `phone_number` 수정은 Post-MVP의 가입 후 추가 개인정보·건강정보 입력 기능에서 다룹니다.

## Post-MVP 이관

- 가입 후 `gender`, `birthday`, `phone_number` 등 추가 개인정보·건강정보 입력 및 저장
- `PATCH /api/v1/users/me`에서 위 필드를 수정 대상으로 확장

## 검증과 변경 규칙

구현 계약은 `app/tests/auth_apis`, `app/tests/user_apis`에서 검증합니다.

다음 변경은 이 문서, 구현, API 문서와 관련 테스트를 같은 PR에서 갱신해야 합니다.

- 회원가입·내 정보 수정 요청에서 허용하는 필드의 추가·삭제·필수 여부 변경
- `USER` 테이블의 nullable 필드 범위 변경
- 이메일 정규화, 저장, 조회 및 중복 비교 기준 변경
