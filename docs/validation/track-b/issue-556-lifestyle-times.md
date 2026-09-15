# #556 생활 시간 저장 API 검증 기록

## 범위

식사 시간대, 반복 행동 시각, 복용 곤란 구간을 인증 사용자의 SELF profile별 현재 한 세트로
저장하고 조회한다. 저장만으로 처방, 일정, occurrence, 알림 또는 Check-in을 변경하지 않으며
추천 계산과 의료 규칙은 포함하지 않는다.

## 구현 경계

- `GET /api/v1/lifestyle-times`: 미저장은 revision 0과 빈 days, 저장 후에는 현재 정규화 payload를 반환한다.
- `PUT /api/v1/lifestyle-times`: `expected_revision`과 필수 `Idempotency-Key`로 전체 요일을 교체한다.
- SELF profile row를 잠가 최초 생성과 수정을 직렬화하고 stale revision을 409로 거부한다.
- 기존 `SYNC_MUTATION` 암호화 snapshot을 재사용하며 같은 key·같은 정규 요청은 최초 200을 재현한다.
- `lifestyle_times`는 profile PK/FK, 양수 revision, JSON 배열 days와 갱신 시각만 저장한다.
- Runtime에는 SELECT·INSERT·UPDATE만 허용한다. Source Writer를 포함한 다른 역할에는 권한을 열지 않는다.

## 합성 인수 자료

[요청·응답 fixture](./issue-556-lifestyle-times-fixtures.json)는 정확한 실제 사용자 정보를 포함하지
않는 합성 데이터다. Frontend는 전체 교체, 자정 경계, 미입력·불규칙·평소 식사하지 않음의 구분,
초기화 후 최신 GET 재조회 및 409 복구 흐름을 이 shape로 연결할 수 있다.

## 자동 검증

- 엄격한 필드·enum·시간·요일·상한 검증과 알려지지 않은 필드 거부
- 미저장 GET, 최초 저장, 정규 정렬, 변경, 빈 days 초기화, replay와 key/revision 충돌
- 겹치는 구간 보존, 완전히 같은 복용 곤란 구간 거부, 일요일 자정 넘김
- SELF 미존재·인증·no-store·OpenAPI 응답 및 오류 shape
- snapshot 저장 실패 시 생활 row와 멱등 row 동시 rollback
- 같은 key의 동시 최초 요청은 한 snapshot으로 수렴하고, 같은 revision의 서로 다른 동시 수정은 하나만 commit
- DB logic/RLS/Trigger 금지 검사와 보호 테이블 쓰기 경계 검사

Production 공개와 후속 추천에서의 외부 전송·의료 판정은 이 검증 범위가 아니다.
