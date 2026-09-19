# #166 실제 승인·철회·감사 저장소 연결 구체안

- 상태: **Proposed / Phase 2 구현 완료(#526)**. 아래 §3의 5개 표(승인 3개 표 + 운영 권한·감사 2개 표)와 발급·철회 one-shot CLI, PostgreSQL transaction advisory lock 기반 동시성 경합 및 fail-closed 검증을 구현했다.
- 근거: Issue #526 comment #5740158236 @phina-io 기술·감사 승인.
- 주의: 본 문서는 계약 승격 전 상태(`proposed/`)를 유지한다. Production 공개 승인, EXT-SOURCE-001 완료, actual Catalog approval 발급 및 actual Catalog materialization은 이 범위에 포함되지 않으며 미완료 상태를 유지한다. 또한 LIST_PATIENT_MEDICATION_GUIDES 자연키 문제는 본 변경으로 해결되지 않는다.

## 구현 상태 (2026-09-20, #526 Phase 2 구현 완료)

| 항목 | 상태 | 구현 내용 |
| --- | --- | --- |
| `catalog_source_approval`·`catalog_build_approval`·`catalog_build_approval_source` | 구현 완료 (Phase 1) | migration `166f50617283`, DDL/ORM, 일반 FK·복합 UK 제약 |
| `SqlAlchemyCatalogApprovalVerifier` (조회 전용 실제 검증기) | 구현 완료 (Phase 1/2) | `PRODUCT_IDENTIFICATION` exact purpose 바인딩, 실시간 유효기간 검증 |
| `catalog_approval_permission` | 구현 완료 (Phase 2) | migration `526c1d2e3f4a`, `user_id` PK/FK, `enabled`, `updated_by`, current-state row 구조 |
| `catalog_approval_audit` | 구현 완료 (Phase 2) | migration `526c1d2e3f4a`, append-only, 6종 audit event, `(actor_id, request_id, event_kind)` 멱등 UK |
| 운영 발급·철회 명령 | 구현 완료 (Phase 2) | `ai_worker/admin/catalog_approval.py`: `grant-permission`, `revoke-permission`, `issue-product-catalog`, `revoke-source`, `revoke-catalog` |
| §4 포트에 receipt_id·schema 추가 및 exact binding | 구현 완료 (Phase 2) | `approval_binding_from_manifest`, exact approval receipt ID 검증, 임의 선택 금지 |
| §5 같은-session 검증·잠금 순서 | 구현 완료 (Phase 2) | `SqlAlchemyCatalogBuildRepository` `save_build`/`load_build` 내 canonical advisory lock + same-session exact revalidation, fail-closed |
| Catalog Writer 권한 격리 | 구현 완료 (Phase 2) | 승인 3개 표 `SELECT` 전용, `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE` 엄격 박탈 |
| Approval 전용 최소권한 Role | 구현 완료 (Phase 2) | `catalog_approval_role_policy.py`, 승인 관리 표 전용 write, 비즈니스 표 쓰기 권한 박탈 |
| Concurrency Race 직렬화 | 검증 완료 (Phase 2) | Case A (Revoke first -> Save fail-closed), Case B (Save first -> Revoke -> Load fail-closed) 검증 |

- 조사 기준: develop `0e6ec2ec` (#526 Phase 2 완료).
- 작성: 김지혜 (Phase 1), 정현우 (Phase 2 구현). 담당 리뷰어: 송은영 (Backend/Security).
- `AGENTS.md`, `CONTRIBUTING.md` 준수: 업무 로직·무결성은 Python Service/Repository와 명시적 transaction으로 관리하며, 일반 FK·UNIQUE·CHECK·최소 권한을 사용하고 신규 RLS·DB Trigger·업무용 DB 함수는 추가하지 않았다.

## 1. 현재 구현과 재사용 범위

| 현재 코드 | 실제 역할 | 연결안 |
| --- | --- | --- |
| `CatalogApprovalVerifier.verify()` | catalog_version·export_checksum·source_refs로 receipt 또는 None 반환하는 포트 | 실제 저장소 adapter 추가. 정확한 기존 receipt 지정 및 transaction 결속은 계약 보완 필요 |
| `CatalogApprovalReceipt` | 승인 ID·Catalog 버전·export checksum·완전성·Source receipt 목록 | 기존 export/envelope의 payload 형태는 유지 |
| `SourceManagementPermission` | Source/Catalog 관리 변경 권한 | 인증·명시적 grant/revoke 패턴 참고. 관리 권한을 승인 권한으로 자동 승격하지 않음 |
| `SourceManagementAudit` | UPDATE/DELETE/GRANT/REVOKE 관리 변경·권한 이력 | 기존 목적 유지. Catalog 승인·검증 실패를 관리 UPDATE로 위장하지 않음 |
| Source/Endpoint/Operation 상태·Snapshot Receipt | 수집 상태·현재성·checksum·출처 검증 | 승인과 함께 검사. acquisition APPROVED나 checksum 일치만으로 Catalog 사용 승인 간주 금지 |
| `RagReleaseEvaluationApproval` | Runtime Bundle 평가 승인 | Catalog 구성 승인으로 대체하지 않음 |
| `RagSourceUseApproval` (#807) | Runtime Source Use Approval authority | Catalog 도메인 승인(`catalog_source_approval`)과 별개이며 상호 대체하지 않음 |

기존 관리 감사는 target revision과 before/after 상태를 중심으로 하며, 승인 유효기간·정확한 Catalog receipt·
실패한 build attempt의 관계를 표현하지 않는다. 승인 의미가 다른 기존 테이블에 범용 JSON을 덧붙이는 대신
아래 전용 구조를 제안한다. 유지보수 비용은 모델·migration·역할 정책·운영 명령·통합 테스트 추가이며,
실제 verifier의 권한·철회·만료 조회를 구현하기 위해 필요한 분리다.

## 2. 승인 대상과 순환 의존 방지

Catalog는 승인 receipt를 받은 뒤 최종 envelope와 Set을 만든다. 따라서 **최초 승인에 Set ID나
최종 envelope hash를 필수로 요구하지 않는다.** 승인 대상은 다음을 정확히 결속하는 안이다.

- catalog_version, export_checksum, export schema/spec, 검증된 export bytes
- `(source_snapshot_id, source_version)`의 정렬된 전체 집합 및 각 Source 승인 ID
- 승인 근거 참조, 용도, 완전성 검증 자료, 유효기간

승인 서비스는 원본 export bytes를 재계산하고 실제 Snapshot Receipt/구성 범위를 대조한다.
클라이언트가 주장한 checksum·APPROVED·is_complete만으로 승인하지 않는다.
D-02 normalization_run이나 D-05 projection hash를 필수 FK로 만들지 않는다.
Source 승인은 수집 자체가 아닌 **해당 Snapshot의 Catalog 사용 용도**로 범위를 구분하는 안이며,
기관·의료·Privacy 승인을 자동 생성하는 기능이 아니다. 실제 승인 권한과 증빙 정책을 먼저 정해야 한다.

## 3. 물리 저장 구조

Phase 1 migration `166f50617283` 및 Phase 2 migration `526c1d2e3f4a`를 통해 아래 5개 물리 테이블이 구현되었다.

| 테이블 | 핵심 필드·관계 | 구현 내용 및 의미 |
| --- | --- | --- |
| `catalog_approval_permission` | `user_id` (PK, users FK), `enabled`, `updated_by` (users FK), `created_at`, `updated_at` | 승인·철회 명령을 수행할 운영자 권한의 **최신 상태(current-state row)**. 자체 권한 부여 금지. 변경 이력과 증빙(evidence_ref)은 `catalog_approval_audit`에 append-only로 보존 |
| `catalog_source_approval` | `id` (PK), `snapshot_id`/`source_version` 복합 FK, `purpose`, `actor_id`, `evidence_ref`, `valid_from`/`expires_at`, `revoked_at`/`revoked_by`/`revoke_reason`, `revision` | 하나의 Snapshot·version·용도에 대한 불변 승인 사실과 단방향 철회. `purpose`는 `PRODUCT_IDENTIFICATION` exact binding |
| `catalog_build_approval` | `id` (PK), `catalog_version`, `export_checksum`, `schema`/`spec`, `export_bytes`, `is_complete`, `actor_id`, `evidence_ref`, `valid_from`/`expires_at`, `revoked_at`/`revoked_by`/`revoke_reason`, `revision` | Set 생성 전 정확한 export 구성에 대한 승인 |
| `catalog_build_approval_source` | `build_approval_id` FK, `source_approval_id` FK, `snapshot_id`/`source_version` | Catalog 승인과 전체 Source 승인 집합 결속. 중복 Snapshot 금지 |
| `catalog_approval_audit` | `id` (PK), `request_id`, `event_kind`, `actor_id`, `target_type`, `target_id`, `reason_code`, `evidence_ref`, `details` (JSON), `created_at` | 권한 부여·철회 및 승인 발급·철회의 감사 이력을 기록하는 **append-only** 감사 테이블. `(actor_id, request_id, event_kind)` 복합 UK로 멱등성 보장 |

- ID는 UUIDString(Char(36)) 규칙을 따른다. 승인/Source link는 RESTRICT FK, 기간은 `expires_at > valid_from` CHECK 제약을 적용했다.
- 승인 payload·기간·출처 목록은 발급 후 수정하지 않는다. 철회 metadata(`revoked_at`, `revoked_by`, `revoke_reason`)와 revision만 명시적으로 갱신한다.
  기간 연장·재승인은 새 ID를 발급한다. 만료는 시각 비교로 판정하며 `EXPIRED`로 바꾸는 scheduler를 두지 않는다.
- Source의 승인 용도(purpose)는 MFDS 제품 허가 기준 `PRODUCT_IDENTIFICATION` exact binding으로 관리된다.
- 감사 이벤트(`event_kind`)는 정확히 6개 이벤트로 한정하여 구현되었다:
  `GRANT_PERMISSION`, `REVOKE_PERMISSION`, `ISSUE_SOURCE`, `ISSUE_CATALOG`, `REVOKE_SOURCE`, `REVOKE_CATALOG`.
- 동일 운영 명령의 `(actor_id, request_id, event_kind)` UNIQUE 제약으로 중복 실행 방지 및 멱등성을 보장한다.
- 권한 모델 정합성: `catalog_approval_permission` 테이블 자체에는 별도 `evidence_ref`나 `revision` 컬럼 없이 현재 활성화 상태(`enabled`)와 최종 수정자(`updated_by`), 시각(`created_at`, `updated_at`)만 관리하는 **current-state row** 구조이다. 모든 권한 부여(grant)와 철회(revoke) 이력 및 운영 증빙(`evidence_ref`), request_id는 `catalog_approval_audit`에 append-only로 분리 보존된다.

## 4. 포트·receipt 연결 및 exact binding

현재 구현된 승인 포트 및 manifest 복원 체계는 승인 ID와 schema를 exact binding한다.

- `approval_binding_from_manifest`: manifest에 기록된 exact approval receipt ID와 export schema를 대조하여 검증하며, 임의로 최신 승인을 자동 선택하지 않는다.
- `SqlAlchemyCatalogApprovalVerifier`:
  - `PRODUCT_IDENTIFICATION` exact purpose 바인딩을 확인한다.
  - 실시간 유효기간 검증: `valid_from <= now < expires_at` 범위 검증.
  - 철회 즉시 거부 (fail-closed): `revoked_at`이 설정되어 있으면 즉시 `CatalogApprovalRevokedError` 반환.
  - build approval과 연결된 전체 source approval 유효성 및 snapshot/version 일치를 대조 검증한다.
- 재승인 시 과거 Set의 receipt나 bytes를 변조하지 않고 새로운 receipt를 담은 export/envelope/Set 경로를 생성한다.
- Source freshness와 승인 유효성은 별도 판정이며, Snapshot 존재 여부와 무관하게 명시적 Catalog 승인이 없는 경우 사용을 거부한다.

## 5. transaction·경합·소비 시점

Phase 2에서 동시성 경합 및 fail-closed 보호를 위해 PostgreSQL transaction advisory lock과 same-session current-state revalidation을 구현했다.

### 잠금 순서 및 동시성 보호

- Advisory Lock: `pg_advisory_xact_lock`을 사용하여 정렬된 승인 자원 잠금(`_catalog_approval_advisory_lock`)을 수행함으로써 동시 실행 시 교착상태(Deadlock)를 방지하고 직렬화한다.
- Same-session Current-state Revalidation: `SqlAlchemyCatalogBuildRepository.save_build()`와 `load_build()`는 단일 DB 세션/트랜잭션 내에서 advisory lock 획득 후 최신 승인 상태(revocation, expiration, exact source linkage)를 즉시 재검증한다.
- Revoke / Expiry Fail-closed: 저장 또는 적재 시점에 승인이 철회되었거나 만료된 경우 `CatalogApprovalRevokedError` 또는 `CatalogApprovalExpiredError`를 발생시키며 트랜잭션을 중단한다.
- Concurrency Race 직렬화 검증 완료 (`tests/integration/rag/test_catalog_approval_command.py`):
  - **Case A (Revoke first -> Save fail-closed)**: 세션 1이 revoke를 커밋한 후, 세션 2의 save가 advisory lock을 얻고 same-session 재검증 단계에서 철회 사실을 감지하여 즉시 fail-closed 됨을 검증.
  - **Case B (Save first -> Revoke -> Load fail-closed)**: 세션 2의 save가 먼저 완료된 후 세션 1이 revoke를 수행한 경우, 후속 `load_build()`가 same-session 재검증에서 철회 상태를 확인하고 즉시 fail-closed 됨을 검증.

## 6. 감사 및 트랜잭션 원자성

- 승인 발급·철회 및 권한 grant·revoke 시 성공 데이터 변경과 감사 기록은 동일 트랜잭션에서 처리된다. 감사 저장 실패 시 전체 작업이 rollback된다.
- 감사 테이블 `catalog_approval_audit`은 append-only INSERT만 허용되며, `(actor_id, request_id, event_kind)` 복합 유니크 제약으로 중복 기록을 방지한다.
- 감사 이벤트는 6종(`GRANT_PERMISSION`, `REVOKE_PERMISSION`, `ISSUE_SOURCE`, `ISSUE_CATALOG`, `REVOKE_SOURCE`, `REVOKE_CATALOG`)의 정형화된 식별자를 사용하며, 원문 예외 문자열이나 SQL 대신 정규화된 `reason_code`와 `evidence_ref`, JSON `details`를 기록한다.

## 7. 최소 권한·역할 분리

데이터베이스 역할 분리 및 최소 권한 원칙을 `catalog_approval_role_policy.py`, `provision_database_roles.py`, migration `526c1d2e3f4a`에 반영했다.

- **Catalog Writer 격리**:
  - `catalog_source_approval`, `catalog_build_approval`, `catalog_build_approval_source`, `catalog_approval_permission`, `catalog_approval_audit`에 대해 `SELECT` 권한만 허용.
  - `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE` 쓰기/수정 권한은 엄격히 박탈.
- **Approval Operator Role 최소 권한**:
  - 승인 관리 및 권한·감사 5개 테이블에 대해 필요한 DML 권한만 부여.
  - 일반 비즈니스 데이터 테이블에 대한 쓰기 권한은 배제.
- **Append-only Audit 보호**:
  - 감사 로그 테이블에 대한 `UPDATE`, `DELETE` 권한을 배제하여 감사 무결성 유지.

## 8. 운영 명령 및 확인 항목

### 구현 완료된 운영 발급·철회 CLI (`ai_worker/admin/catalog_approval.py`)

1. `grant-permission`: 운영자에게 승인 관리 권한 부여 (`user_id`, `enabled=True`, 감사 기록 생성).
2. `revoke-permission`: 운영자의 승인 관리 권한 철회 (`user_id`, `enabled=False`, 감사 기록 생성).
3. `issue-product-catalog`: 검증된 manifest와 source approval에 기반하여 Catalog 빌드 승인 발급 (`ISSUE_SOURCE`, `ISSUE_CATALOG`).
4. `revoke-source`: Source 승인 철회 (`REVOKE_SOURCE`).
5. `revoke-catalog`: Catalog 빌드 승인 철회 (`REVOKE_CATALOG`).

모든 명령은 수행자의 권한 활성화 여부를 사전에 검사하며, 권한이 없거나 비활성화된 경우 거부된다.

### 미해결 과제 및 외부 승인 게이트 (주의사항 유지)

- **문서 상태**: 본 문서는 `proposed/` 상태를 유지하며, 실제 승인 완료 전까지 `current/`로 승격하지 않는다.
- **Production 공개 승인 미완료**: Track C/F 배포 게이트(`docs/release-gates/post-mvp-1-external-approvals.md`)는 계속 닫혀 있다.
- **EXT-SOURCE-001 미완료**: 외부 MFDS 원본 수집기 및 실제 외부 연동 승인은 본 작업의 범위에 포함되지 않는다.
- **실제 운영 승인 및 Materialization 미실행**: 본 PR은 인프라·스토리지·검증 로직 구현이며, actual Catalog approval 발급이나 actual Catalog materialization을 수행한 것이 아니다.
- **자연키 문제 미해결**: `LIST_PATIENT_MEDICATION_GUIDES`의 자연키 문제는 본 변경으로 해결되지 않으며 별도 과제로 유지된다.

## 9. 검증 결과 (Phase 2)

Phase 2에서 다음 자동화 검증을 완료했다:

1. **CLI 및 동시성 경합 테스트** (`tests/integration/rag/test_catalog_approval_command.py`):
   - 17개 테스트 통과 (27.38s)
   - `grant-permission`, `revoke-permission`, `issue-product-catalog`, `revoke-source`, `revoke-catalog` CLI 실행 및 감사 기록 멱등성 검증.
   - Case A (Revoke first -> Save fail-closed) 및 Case B (Save first -> Revoke -> Load fail-closed) advisory lock 동시성 경합 검증 완료.
2. **저장소 및 왕복 테스트**:
   - `test_catalog_approval_storage.py`: 16 passed
   - `test_catalog_storage_roundtrip.py`: 24 passed
3. **데이터베이스 역할 및 최소 권한 배포 테스트**:
   - `test_database_role_deployment.py`: 12 passed
   - `test_database_role_provisioning.py`: 1 passed
4. **회귀 검증**:
   - `ai_worker/tests/rag/catalog`: 322 passed
   - Alembic 마이그레이션 single head 확인 (`526c1d2e3f4a (head)`).
   - 정적 분석 및 린트 통과 (`ruff check`, `ruff format --check`, `mypy`).

## 코드 근거

- [승인 관리 CLI](../../../../ai_worker/admin/catalog_approval.py)
- [승인 Advisory Lock 유틸리티](../../../../ai_worker/adapters/catalog_approval_advisory_lock.py)
- [Catalog Write Support & Same-Session Revalidation](../../../../ai_worker/adapters/sqlalchemy_catalog_write_support.py)
- [SqlAlchemy Catalog Approval Verifier](../../../../ai_worker/adapters/sqlalchemy_catalog_approval_verifier.py)
- [승인 포트·receipt 및 DTO](../../../../ai_worker/tasks/rag/catalog/approval.py)
- [현재 receipt 비교 복원](../../../../ai_worker/tasks/rag/catalog/restore.py)
- [승인 ORM 모델 (Permission/Audit/Approval)](../../../../backend/app/models/catalog_approval.py)
- [Phase 2 Migration (`526c1d2e3f4a`)](../../../../backend/alembic/versions/526c1d2e3f4a_catalog_approval_permission_audit.py)
- [승인 Database Role 최소권한 정책](../../../../infra/python/catalog_approval_role_policy.py)
- [Database Role Provisioning](../../../../infra/python/provision_database_roles.py)
- [동시성 경합 및 CLI 통합 테스트](../../../../tests/integration/rag/test_catalog_approval_command.py)
