# Product Decision `PD-117`: PROFILE SELF 소유권 Current 전환

| 항목 | 값 |
| --- | --- |
| 상태 | Pending acceptance — PR #133 승인 및 병합 시 Accepted |
| 결정일 | 2026-09-01 |
| 관련 Issue | #75, #117 |
| 관련 PR | #133 |
| 승인자 | 권가빈(PR #133 담당 리뷰어 승인 시 확정) |
| 승인 근거 | PR #133의 구현, migration, 계약 문서, migration/API/repository 테스트와 담당 리뷰어 승인 및 병합 |

## 결정

PR #133이 담당 리뷰어 승인을 받고 병합되면, 사용자 의료 리소스의 현재 Backend 소유권 기준을 본인 단일 `SELF` Profile의 `profile_id` 기준으로 전환한다.

이 결정은 보호자, 멀티 프로필, 위임 권한을 승인하지 않는다. 현재 범위는 사용자 1명당 `SELF` Profile 1개를 보장하고, 의료문서·처방·가이드·채팅 세션을 해당 `profile_id`에 연결해 이후 Track A의 `AI_JOB`, Outbox, Idempotency, Prescription Version migration이 같은 소유권 기준을 사용하도록 하는 것이다.

## Current 승격 조건

`docs/contracts/current/profile-self-ownership-v1.md`는 아래 조건이 PR #133에서 함께 충족될 때 Current 실행 계약으로 본다.

- `profile` 테이블과 `(user_id, profile_type)` unique 제약 구현
- 기존 사용자별 `SELF` Profile backfill
- 기존 의료문서·처방·가이드·채팅 세션의 `profile_id` backfill
- 신규 사용자와 기존 사용자의 신규 리소스 write에서 `SELF` Profile 멱등 생성
- 부모·자식 `profile_id` 일관성 composite FK 적용
- 다른 사용자 리소스 접근 404 처리와 교차 사용자 테스트
- migration 전후 row count, `profile_id` null, 부모·자식 불일치 검증
- 운영 적용 시 구버전 애플리케이션을 멈춘 뒤 migration을 실행하는 중단 배포 기준 반영

## 제외

- 보호자·멀티 프로필·위임 권한
- `AI_JOB`, Outbox, Idempotency 실제 migration
- OCR 기존 행 `ai_job_id` mapping
- Guide·Chat 비동기 접수

## 후속

Track A의 `AI_JOB`, Outbox, Idempotency, Job 조회 API는 #117 병합 이후 `SELF profile_id` 또는 부모 chain의 `profile_id` 소유권 기준 위에서 설계·구현한다.
