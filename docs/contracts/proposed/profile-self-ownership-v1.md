# PROFILE SELF 소유권 전환 제안 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Proposed — 승인 전 제안 |
| 구현 상태 | Not implemented in `develop` |
| 관련 Issue | #75, #117 |
| 적용 범위 | Backend/API, Database, 소유권 확인, Post-MVP-1 Track A 선행 조건 |
| 작성일 | 2026-08-31 |

이 문서는 Post-MVP-1 Track A migration을 시작하기 전에 본인 단일 `PROFILE`과 `profile_id` 기반 소유권 기준을 먼저 확정하기 위한 제안이다.

현재 `develop`의 실행 계약은 아직 `user_id` 기반 소유권을 사용한다. 이 문서는 승인 전까지 `current` 또는 `targets/post-mvp-1` 계약으로 해석하지 않으며, 실제 API·DB 동작으로 간주하지 않는다.

## 1. 목적

Track A의 `AI_JOB`, Outbox, Idempotency, Prescription Version을 도입하기 전에 사용자 리소스의 소유권 기준을 `user_id` 직접 비교에서 본인 단일 `PROFILE(profile_type='SELF')`의 `profile_id` 기준으로 전환한다.

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
| `docs/privacy-safety.md` | Post-MVP-1 Job·결과와 Track B·C·D 직접 API는 parent resource를 따라 동일한 `user_id` 소유권을 검사한다고 되어 있다. |
| `docs/data-schema.md` | 멀티 프로필, 환자·보호자 권한은 현재 미구현 후속 범위이며, Post-MVP-1 목표 스키마는 실제 구현으로 간주하지 않는다. |
| `docs/contracts/targets/post-mvp-1/prescription-version-v1.md` | 소유권·출처·감사 시각을 위한 물리 컬럼과 이름은 구현 PR에서 확정한다고 되어 있다. |

따라서 이 문서는 기존 Current 계약을 즉시 변경하지 않는다. 승인 후 구현 PR에서 migration, 모델, repository, API 테스트가 함께 제출되면 해당 기준을 Current 문서와 데이터 구조 문서에 반영한다.

이 단계를 분리하는 이유는 문서 승인과 코드 병합 상태를 섞지 않기 위해서다. `proposed` 문서에서 방향을 먼저 합의하고, 구현 PR에서 실제 migration·테스트가 통과한 뒤에만 Current 계약을 갱신한다.

## 3. 전환 대상

| 종류 | 대상 | 제안 변경 |
| --- | --- | --- |
| 신규 테이블 | `profile` | 본인 단일 SELF profile 저장 |
| 컬럼 추가 | `medical_document.profile_id` | 의료문서 소유 profile |
| 컬럼 의미 변경 | `medical_document.user_id` | 소유권 기준이 아니라 업로드 행위자 의미로 격하. 구현 시 `uploaded_by` rename 검토 |
| 컬럼 추가 | `prescription.profile_id` | 확정 처방 소유 profile |
| 컬럼 추가 | `guide.profile_id` | 가이드 소유 profile |
| 컬럼 추가 | `chat_session.profile_id` | 채팅 세션 소유 profile |

`prescription`, `guide`, `chat_session`은 현재 직접 소유권 컬럼이 없고 부모 chain을 따라 사용자를 확인한다. 전환 후에는 각 도메인 row에 `profile_id`를 직접 저장해 소유권 확인 기준을 단순화한다.

이렇게 바꾸면 각 API가 매번 여러 부모 테이블을 따라가며 `user_id`를 확인하지 않아도 된다. 특히 Job 상태 조회, Guide 조회, Chat 조회처럼 서로 다른 도메인 결과가 공통 Job과 연결될 때 같은 `profile_id` 기준을 재사용할 수 있다.

OCR 작업 자체는 이번 전환의 소유권 확인 범위에 포함한다. 다만 `ocr_job.profile_id` 직접 컬럼은 추가하지 않는다. 현재 OCR 작업은 `ocr_job.document_id`로 `medical_document`에 연결되어 있고, 전환 후에는 `medical_document.profile_id`를 통해 다음 chain으로 소유권을 확인할 수 있기 때문이다.

```text
ocr_job → medical_document → profile_id
```

따라서 OCR 소유권 확인은 포함하지만, `ocr_job`에 `profile_id`를 중복 저장하지 않는다. OCR의 `ai_job_id` 연결과 기존 OCR 행 mapping은 #75의 Track A 후속 범위에서 별도 확정한다.

`ocr_job.profile_id`를 만들지 않는 이유는 같은 소유권 값이 `medical_document.profile_id`와 `ocr_job.profile_id` 두 곳에 중복 저장되면 두 값이 어긋나는 상태를 막기 위한 추가 제약과 backfill 검증이 필요해지기 때문이다. OCR은 문서에 종속된 작업이므로 문서의 profile 소유권을 따라가는 쪽이 더 단순하다.

## 4. `profile` 테이블 제안

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

## 6. Migration 순서

PROFILE 전환은 Track A의 `AI_JOB`·Outbox Expand보다 먼저 수행한다.

| 단계 | 내용 | 검증 |
| --- | --- | --- |
| 1. Expand | `profile` 테이블 생성, 기존 리소스 테이블에 nullable `profile_id` FK 추가 | migration 적용 가능 여부 |
| 2. SELF profile backfill | 기존 `user`마다 `profile_type='SELF'` profile 1개 생성 | `user` 수와 SELF profile 수 일치 |
| 3. Resource backfill | 기존 `medical_document`, `prescription`, `guide`, `chat_session`에 `profile_id` 채움 | 대상 테이블별 `profile_id IS NULL` 0건 |
| 4. Read cutover | repository 소유권 조회를 `profile_id` 기준으로 전환 | 정상 조회와 타 사용자 접근 차단 테스트 |
| 5. Contract | 검증 후 `profile_id NOT NULL`, FK·index·unique 최종화 | NOT NULL 적용 전 null 잔존 재검증 |

데이터 규모가 작은 MVP 상태에서는 backfill을 별도 장기 batch로 나누지 않고 migration 안에서 수행할 수 있다. 다만 운영 데이터가 증가했거나 검증 시간이 길어질 경우에는 재실행 가능한 batch와 cutover PR을 분리한다.

이 순서를 쓰는 이유는 기존 데이터가 있는 상태에서 바로 `profile_id NOT NULL`을 적용하면 migration이 실패하거나 서비스 rollback이 어려워지기 때문이다. 먼저 nullable 컬럼을 열고, 값을 채운 뒤, 조회 기준을 바꾸고, 마지막에 NOT NULL을 적용해야 중간 실패 시 복구할 수 있다.

## 7. Rollback 기준

- `profile_id`가 nullable인 Expand 단계에서는 코드 rollback이 가능해야 한다.
- Contract 단계 전에는 기존 `user_id` 또는 부모 chain 기반 read 경로로 되돌릴 수 있어야 한다.
- Contract 단계에서 `profile_id NOT NULL`을 적용한 뒤에는 destructive downgrade보다 forward-fix를 우선한다.
- 이미 생성한 `profile` row와 backfill된 `profile_id` 값은 사용자 소유권 이력으로 보고 임의 삭제하지 않는다.
- rollback 또는 forward-fix 과정에서도 다른 사용자의 리소스 존재 여부를 노출하지 않는다.

rollback 기준을 미리 정하는 이유는 소유권 migration이 실패했을 때 일부 API만 새 기준을 보고 일부 API는 기존 기준을 보는 혼합 상태를 피하기 위해서다. Contract 전에는 되돌릴 수 있어야 하고, Contract 후에는 데이터 삭제보다 보정 migration으로 복구한다.

## 8. 후속 Track A와의 연결

PROFILE 전환이 승인·병합되면 #75의 Track A migration은 다음 기준을 사용한다.

- `AI_JOB`, Outbox, Idempotency, Prescription Version은 `profile_id` 기준 소유권과 충돌하지 않게 설계한다.
- Job 상태 조회와 결과 조회는 해당 Job 또는 도메인 결과가 인증 사용자의 SELF profile에 속하는지 확인한다.
- `domain_type`/`domain_id`는 물리 컬럼으로 단정하지 않고 `JobStatusResponse` 구성값으로 둔다.
- 비동기 `idempotency_record`와 동기 `sync_idempotency_record` 분리 기준은 기존 target 계약과 ERD를 맞춘 뒤 적용한다.
- `MESSAGE_QUARANTINE`, `DLQ_OUTBOX_EVENT` 등 Worker 복구용 테이블은 Outbox/Stream 계약과 함께 Expand 범위에서 재확인한다.

## 9. 제외 범위

이번 제안은 다음을 포함하지 않는다.

- 보호자·멀티 프로필·위임 권한
- 운영자 support role
- Profile 선택 UI
- `ocr_job.profile_id` 직접 컬럼 추가. OCR 소유권은 `ocr_job → medical_document → profile_id` chain으로 확인한다.
- OCR `ai_job_id` 기존 행 mapping
- `AI_JOB`, Outbox, Idempotency, Prescription Version 실제 migration
- RAG·Citation·Safety·OTC 세부 구현

## 10. 승인 후 필요한 문서 갱신

이 제안이 승인되면 구현 PR에서 다음 문서를 함께 갱신한다.

- `docs/contracts/current/backend-common-patterns.md`
- `docs/privacy-safety.md`
- `docs/data-schema.md`
- 필요한 경우 `docs/contracts/targets/post-mvp-1/async-job-v1.md`
- 필요한 경우 `docs/contracts/targets/post-mvp-1/prescription-version-v1.md`

승인 전에는 위 문서의 Current/Approved target 내용을 이 제안 기준으로 임의 변경하지 않는다.

## 11. 완료 조건

- PROFILE 선행 전환이 #75의 Track A PR 1보다 먼저 필요하다는 점이 승인된다.
- `profile` 테이블과 `(user_id, profile_type='SELF')` unique 기준이 승인된다.
- 기존 리소스의 `profile_id` backfill 대상과 순서가 승인된다.
- 리소스 소유권 조회를 SELF `profile_id` 기준으로 전환하는 방향이 승인된다.
- 보호자·멀티 프로필·위임 권한은 후속 범위로 유지한다.
- 구현 PR에서 migration, 모델, repository, API/ownership 테스트, 문서 갱신을 함께 제출한다.

## 12. 참고 자료

- `docs/contracts/README.md`
- `docs/contracts/current/backend-common-patterns.md`
- `docs/privacy-safety.md`
- `docs/data-schema.md`
- `docs/contracts/targets/post-mvp-1/async-job-v1.md`
- `docs/contracts/targets/post-mvp-1/outbox-stream-v1.md`
- `docs/contracts/targets/post-mvp-1/idempotency-v1.md`
- `docs/contracts/targets/post-mvp-1/prescription-version-v1.md`
- `docs/governance/post-mvp-1-document-authority.md`
