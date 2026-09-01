# PROFILE SELF 소유권 전환 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Current — #117 구현 PR 기준 |
| 구현 상태 | #117 병합 시 `develop` 실행 계약 |
| 관련 Issue | #75, #117 |
| 승인 기록 | [`PD-117`](../../governance/decisions/2026-09-01-profile-self-ownership-current.md) |
| 적용 범위 | Backend/API, Database, 소유권 확인, Post-MVP-1 Track A 선행 조건 |
| 작성일 | 2026-08-31 |

이 문서는 Post-MVP-1 Track A migration을 시작하기 전에 본인 단일 `PROFILE`과 `profile_id` 기반 소유권 기준을 현재 Backend 실행 계약으로 고정한다.

#117 구현 PR은 `profile` 테이블, 기존 사용자 SELF profile backfill, 리소스 `profile_id` backfill, read cutover, 부모·자식 composite FK와 교차 사용자 테스트를 함께 포함한다. [`PD-117`](../../governance/decisions/2026-09-01-profile-self-ownership-current.md)에 따라 이 문서는 #117 병합 시 `docs/contracts/current/`의 실행 계약으로 해석한다.

## 1. 목적

Track A의 `AI_JOB`, Outbox, Idempotency, Prescription Version을 도입하기 전에 사용자 리소스의 소유권 기준을 `user_id` 직접 비교에서 본인 단일 `PROFILE(profile_type='SELF')`의 `profile_id` 기준으로 전환하기 위한 순서와 검증 기준을 확정한다. 실제 `profile_id` 소유권 조회는 PR 0의 backfill, 일관성 검증, read cutover 배포가 완료된 뒤에만 사용한다.

이 전환을 먼저 고정해야 하는 이유는 다음과 같다.

- Post-MVP-1 목표 ERD는 최종 소유권 기준을 `profile_id`로 둔다.
- 현재 Current 계약과 구현은 `user_id` 또는 부모 리소스 chain을 따라 소유권을 확인한다.
- `AI_JOB`, Prescription Version, Guide, Chat, OCR 결과가 서로 다른 소유권 기준을 섞어 쓰면 이후 migration과 권한 검증이 반복 수정될 수 있다.
- 보호자·멀티 프로필은 후속 범위지만, 본인 단일 SELF profile 기준은 이후 확장 지점이 된다.

즉 이 문서는 멀티 프로필 기능을 지금 구현하자는 의미가 아니다. 현재는 사용자 1명당 SELF profile 1개만 만들고, 이후 보호자·환자 profile이 들어와도 기존 의료 리소스의 소유권 컬럼을 다시 바꾸지 않도록 선행 기준을 맞추려는 목적이다.

## 2. 기존 저장소 계약과의 관계

| 문서 | 현재 기준 |
| --- | --- |
| `docs/contracts/current/backend-common-patterns.md` | 사용자 리소스는 소유권을 확인하고, 존재하지 않거나 소유하지 않은 리소스는 `404`를 반환한다. |
| `docs/privacy-safety.md` | #117 이후 새 Backend 리소스는 SELF `profile_id` 또는 부모 chain의 `profile_id`를 기준으로 소유권을 확인한다. |
| `docs/data-schema.md` | 본인 단일 SELF profile은 현재 구현 테이블이며, 보호자·멀티 프로필·위임 권한은 후속 범위로 둔다. |
| `docs/contracts/targets/post-mvp-1/prescription-version-v1.md` | 소유권·출처·감사 시각을 위한 물리 컬럼과 이름은 구현 PR에서 확정한다고 되어 있다. |

따라서 #117 이후 현재 Backend의 사용자 의료 리소스 소유권 기준은 `user_id` 직접 비교가 아니라 SELF `profile_id` 또는 부모 chain의 `profile_id` 확인이다. Track A의 `AI_JOB`, Outbox, Idempotency, Prescription Version은 이 기준 위에서 설계한다.

## 3. 전환 대상

| 종류 | 대상 | 변경 |
| --- | --- | --- |
| 신규 테이블 | `profile` | 본인 단일 SELF profile 저장 |
| 컬럼 추가 | `medical_document.profile_id` | 의료문서 소유 profile |
| 컬럼 의미 변경 | `medical_document.user_id` → `medical_document.uploaded_by` | 소유권 기준이 아니라 업로드 행위자 의미로 격하 |
| 컬럼 추가 | `prescription.profile_id` | 확정 처방 소유 profile |
| 컬럼 추가 | `guide.profile_id` | 가이드 소유 profile |
| 컬럼 추가 | `chat_session.profile_id` | 채팅 세션 소유 profile |

`prescription`, `guide`, `chat_session`은 현재 직접 소유권 컬럼이 없고 부모 chain을 따라 사용자를 확인한다. PR 0의 backfill, 일관성 검증, read cutover 배포가 완료된 뒤에는 각 도메인 row에 저장된 `profile_id`를 소유권 확인 기준으로 사용한다.

이렇게 바꾸면 cutover 이후 각 API가 매번 여러 부모 테이블을 따라가며 `user_id`를 확인하지 않아도 된다. 특히 Job 상태 조회, Guide 조회, Chat 조회처럼 서로 다른 도메인 결과가 공통 Job과 연결될 때 같은 `profile_id` 기준을 재사용할 수 있다.

OCR 작업 자체는 이번 전환의 소유권 확인 범위에 포함한다. 다만 `ocr_job.profile_id` 직접 컬럼은 추가하지 않는다. 현재 OCR 작업은 `ocr_job.document_id`로 `medical_document`에 연결되어 있고, PR 0의 read cutover 이후에는 `medical_document.profile_id`를 통해 다음 chain으로 소유권을 확인할 수 있기 때문이다.

```text
ocr_job → medical_document → profile_id
```

따라서 OCR 소유권 확인은 포함하지만, `ocr_job`에 `profile_id`를 중복 저장하지 않는다. OCR의 `ai_job_id` 연결과 기존 OCR 행 mapping은 #75의 Track A 후속 범위에서 별도 확정한다.

`ocr_job.profile_id`를 만들지 않는 이유는 같은 소유권 값이 `medical_document.profile_id`와 `ocr_job.profile_id` 두 곳에 중복 저장되면 두 값이 어긋나는 상태를 막기 위한 추가 제약과 backfill 검증이 필요해지기 때문이다. OCR은 문서에 종속된 작업이므로 문서의 profile 소유권을 따라가는 쪽이 더 단순하다.

## 4. `profile` 테이블

| 컬럼 | 타입 | Nullable | 설명 |
| --- | --- | ---: | --- |
| `id` | `CHAR(36)` / `UUIDChar` | No | PK |
| `user_id` | `CHAR(36)` / `UUIDChar` | No | `user.id` FK |
| `profile_type` | `VARCHAR(30)` | No | MVP/Post-MVP-1 선행 전환에서는 `SELF`만 허용 |
| `display_name` | `VARCHAR(100)` | No | 기본값은 사용자 이름 또는 SELF 표시명 |
| `created_at` | timezone datetime | No | 생성 시각 |
| `updated_at` | timezone datetime | No | 수정 시각 |

DB 제약:

- `(user_id, profile_type)` unique
- `profile_type = 'SELF'` CHECK

이 제약으로 사용자마다 본인 SELF profile이 1개만 생성되도록 한다.

SELF profile이 중복되면 같은 사용자 리소스가 어느 profile에 귀속되는지 모호해진다. 따라서 애플리케이션 로직뿐 아니라 DB unique 제약으로도 중복 생성을 차단한다.

## 5. 소유권 확인 기준

전환 후 사용자 리소스 조회·수정·삭제는 다음 기준을 따른다.

1. 인증된 사용자의 SELF profile을 조회한다.
2. 대상 리소스의 `profile_id`가 해당 SELF profile id와 같은지 확인한다.
3. 리소스가 없거나 SELF profile과 일치하지 않으면 동일하게 `404`를 반환한다.
4. 오류 응답에는 다른 사용자의 리소스 존재 여부, 의료정보, 소유권 판단 상세 사유를 포함하지 않는다.

Repository 공통 패턴은 아래 형태를 기준으로 한다.

```python
owned_by_self(Resource.profile_id, user_id)
```

이 패턴은 구현 PR에서 실제 helper와 테스트로 고정한다.

공통 helper를 두는 이유는 각 repository가 소유권 조건을 직접 조립하다가 어떤 API는 `user_id`, 어떤 API는 `profile_id`, 어떤 API는 부모 chain을 쓰는 식으로 갈라지는 것을 막기 위해서다.

## 6. 도메인별 `profile_id` 일관성 기준

`profile_id`를 여러 도메인 row에 직접 저장하면 조회는 단순해지지만, 부모·자식 row의 `profile_id`가 달라지는 불일치가 생길 수 있다. 따라서 직접 컬럼을 두는 도메인은 기준 원본과 write 검증 규칙을 함께 둔다.

| 도메인 | 기준 원본 | 직접 `profile_id` 저장 | write 시 검증 |
| --- | --- | --- | --- |
| `medical_document` | 인증 사용자의 SELF profile | Yes | 업로드 시 SELF profile id를 저장한다. |
| `ocr_job` | `medical_document.profile_id` | No | `ocr_job.document_id`의 문서 소유권 chain으로 확인한다. |
| `prescription` | `medical_document.profile_id` | Yes | 처방 생성 시 `prescription.document_id`가 가리키는 문서의 `profile_id`와 처방의 `profile_id`가 일치해야 한다. |
| `guide` | 연결된 `prescription.profile_id` | Yes | 가이드 생성 시 처방의 `profile_id`와 가이드의 `profile_id`가 일치해야 한다. |
| `chat_session` | 연결된 `prescription.profile_id` | Yes | 처방 기반 세션 생성 시 처방의 `profile_id`와 세션의 `profile_id`가 일치해야 한다. |
| `chat_message` | `chat_session.profile_id` | No | 메시지는 세션의 소유권을 따른다. |

기준 원본을 위처럼 두는 이유는 소유권 판단의 출처를 하나로 고정하기 위해서다. OCR은 의료문서에 종속된 작업이므로 `medical_document.profile_id`를 기준으로 삼고, Guide는 처방 결과에 종속되므로 `prescription.profile_id`를 기준으로 삼는다. Chat message는 세션 안의 하위 row이므로 별도 `profile_id`를 반복 저장하지 않는다.

구현 PR에서는 다음 검증을 함께 포함한다.

- write 시 부모 row의 `profile_id`와 자식 row에 저장할 `profile_id`가 일치하지 않으면 저장하지 않는다.
- 부모·자식 소유권 관계는 단순 composite index가 아니라 실제 DB 제약으로 강제한다. 예를 들어 `prescription(document_id, profile_id) → medical_document(id, profile_id)`, `guide(prescription_id, profile_id) → prescription(id, profile_id)`, `chat_session(prescription_id, profile_id) → prescription(id, profile_id)` 형태의 composite FK를 사용한다.
- composite FK를 걸기 위해 부모 테이블에는 `(id, profile_id)` unique 또는 동등한 참조 가능 제약을 둔다.
- backfill 후 부모·자식 `profile_id` 불일치 검증 SQL을 실행한다.
- 정상 사용자 접근, 타 사용자 접근 차단, 부모·자식 소유권 불일치 방어 테스트를 추가한다.
- 불일치가 발견되면 read cutover와 NOT NULL 전환을 진행하지 않는다.

## 7. Migration 순서

PROFILE 전환은 Track A의 `AI_JOB`·Outbox Expand보다 먼저 수행한다.

운영 DB에 적용하기 전에는 복구와 원인 추적을 위해 migration 전 backup과 적용 전후 Alembic revision·row count snapshot을 남긴다. 최소 기록 대상은 `user`, `profile`, `medical_document`, `prescription`, `guide`, `chat_session`이다. DB dump와 검증 증빙은 의료·개인정보 포함 가능성을 고려해 소유자만 접근할 수 있는 권한으로 생성한다.

아래 표는 rolling deploy나 장기 migration으로 분리할 때의 논리적 전환 순서다. #117 구현 PR의 실제 운영 적용은 이 단계를 하나의 중단 배포 안에서 수행하며, 절차는 `서비스 중단 → backup·적용 전 snapshot → migration·backfill·적용 후 snapshot·검증 → 호환 코드 재시작` 순서로 고정한다.

| 단계 | 내용 | 검증 |
| --- | --- | --- |
| 1. Expand | `profile` 테이블 생성, 기존 리소스 테이블에 nullable `profile_id` FK 추가 | migration 적용 가능 여부 |
| 2. SELF profile write 보장 | 회원가입·사용자 생성 시 SELF profile 자동 생성. 기존 사용자가 신규 리소스를 생성하는 경우에도 리소스 write transaction 안에서 SELF profile을 멱등적으로 조회·생성 | 신규 사용자와 기존 사용자의 신규 write 모두 SELF profile을 갖는지 확인 |
| 3. SELF profile backfill | 기존 `user`마다 `profile_type='SELF'` profile 1개 생성 | `user` 수와 SELF profile 수 일치 |
| 4. Resource dual-write 활성화 | 신규 `medical_document`, `prescription`, `guide`, `chat_session` 생성 시 `profile_id` 기록. 부모가 있는 리소스는 부모의 `profile_id`와 같은 값만 저장하며, 부모의 `profile_id`가 NULL이면 자식 생성 전에 부모 chain을 먼저 보정한다. | 신규 리소스의 `profile_id IS NULL` 0건, 부모·자식 불일치 0건 |
| 5. Resource backfill | 기존 `medical_document`, `prescription`, `guide`, `chat_session`에 `profile_id` 채움 | 대상 테이블별 `profile_id IS NULL` 0건 |
| 6. Consistency verify | 부모·자식 `profile_id` 불일치, orphan, 중복 SELF profile 확인 | 불일치 0건 |
| 7. Read cutover | repository 소유권 조회를 `profile_id` 기준으로 전환 | 정상 조회와 타 사용자 접근 차단 테스트 |
| 8. Contract | 검증 후 `profile_id NOT NULL`, FK·composite FK·index·unique 최종화 | NOT NULL 적용 전 null 잔존 재검증 |

데이터 규모가 작은 MVP 상태에서는 backfill을 별도 장기 batch로 나누지 않고 migration 안에서 수행할 수 있다. 다만 운영 데이터가 증가했거나 검증 시간이 길어질 경우에는 재실행 가능한 batch와 cutover PR을 분리한다.

SELF profile write 보장과 Resource dual-write를 backfill보다 먼저 두는 이유는 backfill 이후 read cutover 전까지 새로 생성되는 사용자와 리소스를 보호하기 위해서다. 기존 사용자에게 아직 SELF profile backfill이 완료되지 않았더라도, 신규 리소스 write transaction 안에서 SELF profile을 멱등적으로 생성하면 `profile_id = NULL` 리소스가 새로 생기지 않는다.

Resource dual-write 시 부모 row의 `profile_id`가 아직 NULL이면 그 NULL을 자식 row에 복사하지 않는다. 예를 들어 기존 Prescription에서 Guide 또는 Chat session을 생성할 때 `prescription.profile_id`가 NULL이면, 같은 write transaction 안에서 `prescription.document_id → medical_document.profile_id → SELF profile` 순서로 부모 chain을 먼저 보정한 뒤 자식의 `profile_id`를 저장한다. 부모 chain을 보정할 수 없거나 부모·자식 `profile_id`가 일치하지 않으면 자식 리소스를 생성하지 않는다.

SELF profile 생성은 `(user_id, profile_type)` unique 제약을 기준으로 멱등적으로 처리한다. PostgreSQL에서는 일반 `INSERT`의 unique violation 이후 같은 transaction에서 바로 재조회할 수 없으므로, 구현 PR에서는 `INSERT ... ON CONFLICT DO NOTHING RETURNING` 후 반환 row가 없으면 기존 SELF profile을 조회하는 방식 또는 savepoint로 충돌을 격리하는 방식 중 하나를 사용한다. 동시에 같은 사용자의 SELF profile 생성이 발생해도 하나만 성공해야 하며, 리소스 write transaction은 최종적으로 조회된 같은 SELF profile id를 `profile_id`로 사용한다.

이 순서를 쓰는 이유는 기존 데이터가 있는 상태에서 바로 `profile_id NOT NULL`을 적용하면 migration이 실패하거나 서비스 rollback이 어려워지기 때문이다. 먼저 nullable 컬럼을 열고, 신규 write가 새 기준을 함께 기록하게 만든 뒤, 기존 값을 채우고, 조회 기준을 바꾸고, 마지막에 NOT NULL을 적용해야 중간 실패 시 복구할 수 있다.

### 7.1 적용 전후 검증 기록

운영 적용 전후에는 아래 값을 기록한다.

| 시점 | 기록 |
| --- | --- |
| 적용 전 | DB backup 생성 여부, 대상 테이블 row count, 현재 Alembic revision |
| 적용 후 | 적용된 Alembic revision, 대상 테이블 row count, SELF profile 수, `profile_id IS NULL` 잔존 수, 부모·자식 `profile_id` 불일치 수 |

검증 기준은 다음과 같다.

- 기존 `user` 수와 `SELF` profile 수가 일치한다.
- `medical_document`, `prescription`, `guide`, `chat_session`의 `profile_id IS NULL`이 0건이다.
- `prescription.document_id → medical_document.profile_id`, `guide.prescription_id → prescription.profile_id`, `chat_session.prescription_id → prescription.profile_id` 불일치가 0건이다.
- 검증 실패 시 read cutover와 NOT NULL 전환을 진행하지 않고 backup, row count, 실패 SQL 결과를 기준으로 원인을 확인한다.

## 8. Rollback 기준

#117 구현 PR의 운영 적용은 migration, 코드, 문서가 같은 배포 단위로 움직이는 중단 배포를 기준으로 한다. DB schema 변경 전에 기존 `fastapi`와 `ai-worker`를 멈추고, 처리 중인 요청이 종료된 뒤 migration을 실행한다. 서비스 중단에 실패하거나 중단 상태를 확인하지 못하면 migration을 실행하지 않는다. migration 후에는 `fastapi` 새 이미지를 필수로 재시작하고, `ai-worker`는 실제 Redis Consumer 실행 경로가 연결된 뒤 같은 배포 단위에 포함한다. placeholder `ai-worker`를 강제 재시작해 재시작 루프를 만들지 않으며, 구버전 이미지를 다시 띄우지 않는다. Rolling deploy로 적용하려면 Expand, dual-write, backfill, read cutover, Contract를 분리 PR로 나누고 각 단계별 호환성을 별도로 검증해야 한다.

- `profile_id`가 nullable인 Expand 단계에서는 코드 rollback이 가능해야 한다.
- Contract 단계 전에는 기존 `user_id` 또는 부모 chain 기반 read 경로로 되돌릴 수 있어야 한다.
- Contract 단계에서 `profile_id NOT NULL`을 적용한 뒤에는 destructive downgrade보다 forward-fix를 우선한다.
- 이미 생성한 `profile` row와 backfill된 `profile_id` 값은 사용자 소유권 이력으로 보고 임의 삭제하지 않는다.
- rollback 또는 forward-fix 과정에서도 다른 사용자의 리소스 존재 여부를 노출하지 않는다.

rollback 기준을 미리 정하는 이유는 소유권 migration이 실패했을 때 일부 API만 새 기준을 보고 일부 API는 기존 기준을 보는 혼합 상태를 피하기 위해서다. Contract 전에는 되돌릴 수 있어야 하고, Contract 후에는 데이터 삭제보다 보정 migration으로 복구한다.

## 9. 후속 Track A와의 연결

PR 0의 PROFILE backfill, 일관성 검증, read cutover 배포가 완료되면 #75의 Track A migration은 다음 기준을 사용한다.

- `AI_JOB`, Outbox, Idempotency, Prescription Version은 `profile_id` 기준 소유권과 충돌하지 않게 설계한다.
- Job 상태 조회와 결과 조회는 해당 Job 또는 도메인 결과가 인증 사용자의 SELF profile에 속하는지 확인한다.
- `domain_type`/`domain_id`는 물리 컬럼으로 단정하지 않고 `JobStatusResponse` 구성값으로 둔다.
- 단일 `idempotency_record`와 `record_type=ASYNC_JOB|SYNC_MUTATION` 기준은 기존 target 계약과 ERD를 맞춘 뒤 적용한다.
- `MESSAGE_QUARANTINE`, `DLQ_OUTBOX_EVENT` 등 Worker 복구용 테이블은 Outbox/Stream 계약과 함께 Expand 범위에서 재확인한다.

## 10. 제외 범위

이번 계약은 다음을 포함하지 않는다.

- 보호자·멀티 프로필·위임 권한
- 운영자 support role
- Profile 선택 UI
- `ocr_job.profile_id` 직접 컬럼 추가. OCR 소유권은 `ocr_job → medical_document → profile_id` chain으로 확인한다.
- OCR `ai_job_id` 기존 행 mapping
- `AI_JOB`, Outbox, Idempotency, Prescription Version 실제 migration
- RAG·Citation·Safety·OTC 세부 구현

## 11. 함께 갱신한 문서

#117 구현 PR에서는 이 계약과 함께 다음 문서를 갱신한다.

- `docs/contracts/current/backend-common-patterns.md`
- `docs/privacy-safety.md`
- `docs/data-schema.md`
- 필요한 경우 `docs/contracts/targets/post-mvp-1/async-job-v1.md`
- 필요한 경우 `docs/contracts/targets/post-mvp-1/prescription-version-v1.md`

## 12. 완료 조건

- PROFILE 선행 전환이 #75의 Track A PR 1보다 먼저 적용된다.
- `profile` 테이블과 `(user_id, profile_type='SELF')` unique 기준이 구현된다.
- 기존 사용자와 신규 사용자 모두 backfill 전후 신규 리소스 생성 시 `profile_id = NULL`로 남지 않도록 SELF profile 멱등 생성과 dual-write 순서가 구현된다.
- 기존 리소스의 `profile_id` backfill 대상과 순서가 구현된다.
- 도메인별 `profile_id` 기준 원본과 부모·자식 composite FK·일관성 검증 기준이 구현된다.
- PR 0의 backfill, 일관성 검증, read cutover 배포 완료 후 리소스 소유권 조회를 SELF `profile_id` 기준으로 전환한다.
- 운영 적용 전 DB backup, 적용 전후 Alembic revision·row count, `profile_id` null·불일치 검증 결과를 기록한다.
- 보호자·멀티 프로필·위임 권한은 후속 범위로 유지한다.
- migration, 모델, repository, API/ownership 테스트, 문서 갱신을 같은 PR에 포함한다.

## 13. 참고 자료

- `docs/contracts/README.md`
- `docs/contracts/current/backend-common-patterns.md`
- `docs/privacy-safety.md`
- `docs/data-schema.md`
- `docs/contracts/targets/post-mvp-1/async-job-v1.md`
- `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md`
- `docs/contracts/targets/post-mvp-1/idempotency-v1.md`
- `docs/contracts/targets/post-mvp-1/prescription-version-v1.md`
- `docs/governance/post-mvp-1-document-authority.md`
