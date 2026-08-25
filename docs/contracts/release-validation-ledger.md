# Staging Release Validation Ledger 계약

## 상태와 범위

- 상태: **Proposed / 미구현**
- 계약 버전: `release-validation-ledger-v1`
- 적용 환경: 전용 staging control DB와 AI one-cycle validation에만 적용
- Production application DB·API 계약에는 적용하지 않음

이 계약은 `release-validator`, `release-cleanup`과 staging migration process가 공유하는 run 원장, 상태 전이와 상호 배제 규칙을 정의합니다. 현재 저장소에는 control DB, service와 validation CLI가 없으므로 이 문서의 존재를 구현 완료로 간주하지 않습니다.

## 소유자와 권한

| 주체 | application DB | control DB | 허용 동작 |
| --- | --- | --- | --- |
| FastAPI application | 기존 staging application 권한 | 접근 금지 | 실제 HTTP one-cycle 처리 |
| Host wrapper | 접근 금지 | 접근 금지 | run ID 생성, image inspect, 검증된 입력으로 Compose 명령 실행 |
| Validation CLI — normal | 합성 fixture 생성·조회·삭제 최소권한 | record 생성·조회·상태 전이, advisory lock | normal smoke와 finally cleanup |
| Validation CLI — cleanup | 합성 run 조회·삭제 최소권한 | 기존 record 조회·상태 전이, advisory lock | `--cleanup-only` 복구 |
| Ledger resolver | 접근 금지 | run ID 기준 provenance·schema read-only | cleanup image를 시작하기 전 strict JSON 조회 |
| Staging migration | migration 전용 권한 | unresolved 조회, advisory lock | unresolved 0건 확인 후 migration |
| Ledger retention | 접근 금지 | 만료된 `RESOLVED` record·event 삭제만 허용 | 90일 보존 만료 후 정리 |

Validation CLI는 동일 코드가 mode별 credential로 실행되는 유일한 ledger transition owner입니다. FastAPI process, application service account와 host wrapper는 ledger record를 읽거나 변경하지 않습니다. Resolver는 별도 digest로 고정된 staging ops image와 read-only credential을 사용하며 상태를 전이할 수 없습니다. OpenAI Key는 FastAPI에만 주입하며 validation·cleanup·resolver·migration role에는 주입하지 않습니다.

## Schema v1

`release_validation_runs`는 다음 필드를 가집니다. 실제 SQL type과 길이는 구현 PR에서 MySQL migration과 이 문서를 함께 확정합니다.

| 필드 | 규칙 |
| --- | --- |
| `run_id` | UUID 기본키, 변경 금지 |
| `contract_version` | `release-validation-ledger-v1` |
| `state` | 아래 상태 enum 중 하나 |
| `repo_digest` | pull 가능한 `repository@sha256:<digest>` full reference |
| `local_image_id` | 실행 host에서 관찰한 보조 provenance |
| `oci_revision` | image의 `org.opencontainers.image.revision` |
| `app_schema_revision` | run 시작 시 Alembic revision |
| `anchor_email`, `anchor_phone`, `anchor_object_key` | 비식별 합성 root의 exact identity |
| `descendant_identity_hash` | fixture commit 후 확정한 후손 ID·개수의 canonical hash. `STARTED`에서는 null 가능 |
| `failure_stage` | 허용된 비민감 단계명 또는 null |
| `created_at`, `updated_at`, `resolved_at` | UTC timestamp. `resolved_at`은 `RESOLVED`에서만 non-null |

질문·가이드·답변 본문, token, API Key, DB password, 실제 환자·처방 데이터는 ledger에 저장하지 않습니다.

상태 변경 이력은 append-only `release_validation_run_events`에 보존합니다.

| 필드 | 규칙 |
| --- | --- |
| `event_id` | 순서가 보존되는 기본키 |
| `run_id` | `release_validation_runs.run_id` 참조 |
| `from_state`, `to_state` | 생성 event의 `from_state`만 null, 그 외 허용 상태 enum |
| `actor_role` | `NORMAL`, `CLEANUP` 중 하나 |
| `failure_stage` | 허용된 비민감 단계명 또는 null |
| `occurred_at` | immutable UTC timestamp |

상태 update와 event insert는 같은 control DB transaction에서 commit합니다. 어떤 role도 event row를 update할 수 없습니다. Retention role만 90일 보존 조건을 충족한 `RESOLVED` record에 연결된 event를 해당 record와 같은 transaction에서 delete할 수 있습니다.

## 상태와 전이

```text
STARTED
→ SETUP_IN_PROGRESS
→ ANCHOR_COMMITTED
→ RESOLUTION_PENDING
→ CLEANUP_IN_PROGRESS
→ RESOLVED
```

허용 전이는 다음과 같습니다.

| 현재 상태 | 다음 상태 | 선행 조건 |
| --- | --- | --- |
| 없음 | `STARTED` | 같은 run ID 없음, provenance·schema 검증 성공 |
| `STARTED` | `SETUP_IN_PROGRESS` | exact 예상 anchor 기록 완료 |
| `STARTED` | `RESOLVED` | app DB write 전 중단, fresh session에서 관련 row 0건 |
| `SETUP_IN_PROGRESS` | `ANCHOR_COMMITTED` | exact anchor·후손 확인과 identity hash 기록 완료 |
| `SETUP_IN_PROGRESS` | `CLEANUP_IN_PROGRESS` | crash recovery에서 exact 예상 anchor를 채택하고 삭제 대상 확정 |
| `SETUP_IN_PROGRESS` | `RESOLVED` | fixture commit 전 중단, fresh session에서 관련 row 0건 |
| `ANCHOR_COMMITTED` | `RESOLUTION_PENDING` | 실행 결과 불명확 또는 cleanup 대기 |
| `ANCHOR_COMMITTED` | `CLEANUP_IN_PROGRESS` | 삭제 대상 identity·개수 확정 |
| `RESOLUTION_PENDING` | `CLEANUP_IN_PROGRESS` | 진행 중 server 작업이 terminal이고 삭제 대상 확정 |
| `CLEANUP_IN_PROGRESS` | `RESOLVED` | cleanup commit 후 fresh session에서 관련 row 0건 |

정의되지 않은 전이는 거부합니다. `ANCHOR_COMMITTED`·`RESOLUTION_PENDING`인데 anchor가 없거나, 관찰한 identity가 ledger와 다르면 자동 채택·삭제하지 않고 `FAIL`입니다. `RESOLVED + row 0건`만 반복 cleanup의 성공 no-op입니다.

## Transaction과 crash recovery

Control DB와 application DB commit은 원자적이라고 가정하지 않습니다.

- Application fixture write 전에 control DB를 `SETUP_IN_PROGRESS`로 commit합니다.
- Application cleanup write 전에 대상 hash와 `CLEANUP_IN_PROGRESS`를 control DB에 commit합니다.
- `SETUP_IN_PROGRESS + exact anchor`는 해당 anchor를 채택해 cleanup을 계속합니다.
- `SETUP_IN_PROGRESS + row 0건`은 fixture commit 전 중단으로 확인하고 `RESOLVED`로 전이합니다.
- `CLEANUP_IN_PROGRESS + matching rows`는 cleanup을 재개합니다.
- `CLEANUP_IN_PROGRESS + row 0건`은 fresh session 재확인 후 `RESOLVED`로 전이합니다.

각 상태 전이와 application write 사이 process kill을 fault-injection test로 검증합니다.

## Advisory lock과 migration

Normal·cleanup validation CLI와 staging migration process는 전용 control DB connection에서 `ah_staging_release_validation` advisory lock을 획득하고 전체 작업 동안 유지합니다.

- Lock 획득 timeout·실패는 application DB write 전 non-zero 종료입니다.
- Connection loss 또는 lock 소유 확인 실패 후에는 다음 application DB write를 수행하지 않습니다.
- Migration은 같은 lock 안에서 `state != RESOLVED` record가 0건인지 확인하고 migration 완료까지 lock을 유지합니다.
- Unresolved record가 있으면 해당 record의 원래 `repo_digest` image로 cleanup을 완료하기 전 migration하지 않습니다.

## Image와 schema 호환성

- Cleanup은 ledger의 full `repo_digest`를 Compose image와 CLI 입력에 동일하게 사용합니다.
- Cleanup 전 host wrapper는 고정된 `release-ledger-resolver` service에 run ID만 전달합니다. Resolver는 `{contract_version, run_id, repo_digest, oci_revision, app_schema_revision}`만 strict JSON 한 건으로 반환합니다.
- Wrapper는 run ID 일치, contract version, full RepoDigest 형식과 non-empty revision을 검증한 뒤 그 값을 Compose interpolation과 cleanup CLI 입력에 동일하게 전달합니다. Unknown run, 중복 출력, 추가 필드, 형식 오류는 cleanup container 시작 전 실패입니다.
- Cleanup CLI는 시작 후 같은 ledger record를 다시 조회해 wrapper 입력과 비교하고 하나라도 다르면 application DB를 읽거나 쓰기 전에 거부합니다.
- Local image ID는 pull reference가 아니며 cleanup image를 선택하는 데 사용할 수 없습니다.
- Registry에서 원래 RepoDigest를 보존하는 기간은 ledger retention보다 길어야 합니다.
- 각 `contract_version`을 해석하는 pinned resolver ops image는 해당 version의 unresolved record가 0건이고 마지막 resolved record의 90일 보존이 끝날 때까지 삭제하지 않습니다.
- 다른 image로 cleanup하려면 ledger의 `app_schema_revision`을 지원한다는 별도 호환성 리뷰와 증거가 필요합니다.
- Ledger schema 변경은 `contract_version` 변경, migration, 이전 unresolved record 호환성 계획과 테스트를 포함합니다.

## 보존과 감사

`RESOLVED` record와 event는 `resolved_at`부터 최소 90일 보존합니다. 전용 `release-ledger-retention` role만 90일이 지난 `RESOLVED` record와 연결 event를 같은 transaction에서 삭제할 수 있습니다. 삭제 실행 시각·대상 run ID·삭제 개수는 본문이나 비밀정보 없이 staging 운영 감사 로그에 남깁니다. Unresolved record와 event는 자동 만료·삭제하지 않습니다.

## 필수 검증

- 허용·금지 상태 전이와 role별 권한
- Unknown run ID, 중복 run ID와 identity mismatch 거부
- Setup·cleanup의 모든 cross-DB crash window 재진입
- Lock 획득 timeout·connection loss와 migration 상호 배제
- 원래 RepoDigest·OCI revision·schema 불일치 차단
- Resolver의 unknown run, 출력 변조·형식 오류, credential read-only 범위와 wrong-image cleanup 거부
- 상태 update와 append-only event의 동일 transaction, 90일 이전 삭제 거부와 unresolved 삭제 거부
- Retention credential의 모든 event `UPDATE` 거부와 eligible event·record 동시 삭제
- `RESOLVED` 이후 fresh-session zero-row 확인과 idempotent cleanup

이 계약을 구현하거나 변경하는 PR은 Backend `@phina-io`, 배포·아키텍처 `@hazelnutflavoured`와 기본 소유자 `@ceohwj`의 리뷰를 받아야 합니다.
