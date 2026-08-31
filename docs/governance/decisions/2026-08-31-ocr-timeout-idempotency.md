# Product Decision: OCR 실행 상한과 멱등성 저장 구조

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-91-20260831` |
| 상태 | Approved |
| 결정일 | 2026-08-31 |
| 결정자 | 권가빈 — PM·Product acceptance (`@hazelnutflavoured`) |
| 추적 Issue | [#91](https://github.com/AI-HealthCare-05/AH_05_04/issues/91) |
| 적용 범위 | Post-MVP-1 Approved target 계약 |

## 결정 1: OCR 실행 상한

OCR Job의 실행 상한은 다음으로 확정한다.

- hard timeout: 60초
- Worker lease: 75초
- heartbeat: 10초
- 최대 시도 횟수: 최초 실행 포함 3회

현재 Provider 상한인 CLOVA OCR 20초와 LLM 구조화 30초의 순차 실행,
종료 처리 여유와 hard timeout보다 15초 긴 lease 원칙을 반영한 값이다.
이 결정은 기존 Contract Freeze v4의 OCR hard timeout 30초와 lease 45초를
Post-MVP-1 OCR 목표 계약에 한해 대체한다. Guide와 Chat 값은 변경하지 않는다.

## 결정 2: 멱등성 저장 구조

비동기 Job 접수와 동기 상태 변경의 멱등 레코드는 PostgreSQL 단일
`idempotency_record` 테이블에 저장하고
`record_type=ASYNC_JOB|SYNC_MUTATION`으로 구분한다. 별도
`sync_idempotency_record` 테이블은 만들지 않는다.

- `ASYNC_JOB`은 `job_id`를 저장하고 응답 snapshot은 저장하지 않는다.
- `SYNC_MUTATION`은 `parent_resource_id`, 최초 성공 `response_status`,
  암호화된 canonical `response_body_snapshot`을 저장한다.
- snapshot의 PostgreSQL 물리 타입은 `BYTEA`다.
- 타입별 nullability는 DB CHECK 제약으로 강제한다.

이 결정은 Contract Freeze v4의 분리 테이블 서술을 대체하고 Approved
Architecture/ERD의 단일 테이블과 `record_type` 구조를 채택한다. `BYTEA`는
PostgreSQL 기준 정합화이며 `MEDIUMBLOB`은 사용하지 않는다.

## 적용과 검증

- 이 결정은 Approved target이며 Current runtime 구현 완료를 뜻하지 않는다.
- 구현 PR에서 migration, OpenAPI/DTO, 계약·통합 테스트와 담당 리뷰어 승인을
  함께 제출한다.
- 외부 Privacy·의료·약학·Source 공개 승인을 대신하지 않으며 기존 공개
  게이트를 유지한다.
