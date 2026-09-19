# #166 Catalog 승인·철회·감사 저장소 계약

- 상태: **Current / Implemented** (`#526` Phase 2)
- 구현 근거: migrations `166f50617283`, `526c1d2e3f4a`; ORM, 운영 명령, 역할 정책, 저장·조회 통합 테스트
- 승인 근거: Issue #526 comment #5740158236 @phina-io 기술·감사 승인 및 PR #852 지정 리뷰
- 범위 주의: Current는 코드·migration·자동화 테스트가 뒷받침하는 실행 계약이라는 뜻이다. actual approval issuance와 actual Catalog materialization은 **NOT RUN**이며, `PUBLIC_TRACK_F`는 변경되지 않는다. `EXT-SOURCE-001`과 `LIST_PATIENT_MEDICATION_GUIDES` 자연키 blocker도 미해결 상태다.

## 1. 책임 경계

Catalog 승인은 Runtime Source Use Approval(`#807`), Source 관리 권한, Runtime Bundle 평가 승인을 대체하지 않는다. Source freshness와 Catalog 사용 승인은 독립적으로 판정한다. 승인 payload·기간·Source 집합은 발급 후 불변이고, 철회 컬럼만 단방향으로 갱신한다. 기간 연장이나 재승인은 새 approval ID를 발급한다.

업무 판정과 transaction은 Python Service/Repository에서 수행한다. 이 계약은 신규 Trigger, RLS, 업무용 DB 함수 또는 범용 인증 framework를 도입하지 않는다.

## 2. Permission control과 bootstrap authority

Dedicated `CATALOG_APPROVAL_*` database principal은 `grant-permission`과 `revoke-permission`의 bootstrap 실행 권위다. permission state는 처음에 비어 있을 수 있으므로 두 permission-control 작업은 기존 `catalog_approval_permission` row를 요구하지 않는다. `actor_id`와 `subject_user_id`는 감사 identity이며 DB authentication을 대신하지 않는다.

Permission control 작업은 다음을 모두 요구한다.

- dedicated least-privilege Catalog Approval DB principal
- 유효한 actor·subject user identity
- permission current-state 변경과 별도의 append-only audit event를 같은 transaction에서 커밋

Content approval 작업인 `issue-product-catalog`, `revoke-source`, `revoke-catalog`는 다음을 모두 요구한다.

- dedicated Catalog Approval DB principal
- actor의 enabled `catalog_approval_permission`

Approval role의 permission 권한은 `SELECT`, `INSERT (user_id, enabled, evidence_ref, revision, updated_at)`, `UPDATE (enabled, evidence_ref, revision, updated_at)`로 제한된다. `user_id` UPDATE와 `DELETE`/`TRUNCATE`는 허용하지 않는다. Audit은 `SELECT`/`INSERT`만 허용하며 `UPDATE`/`DELETE`/`TRUNCATE`는 허용하지 않는다. Catalog business table 쓰기와 Catalog Writer의 approval table 쓰기는 금지한다.

## 3. 물리 저장 구조

### `catalog_approval_permission`

현재 운영자 권한 상태만 보관한다.

- `user_id` — PK, `user.id` RESTRICT FK
- `enabled`
- `evidence_ref`
- `revision`
- `updated_at`

### `catalog_source_approval`

하나의 Snapshot·version·purpose에 대한 불변 승인 사실이다.

- `id`
- `source_snapshot_id`
- `source_version`
- `purpose`
- `actor_id`
- `evidence_ref`
- `valid_from`
- `expires_at`
- `revoked_at`
- `revoked_by`
- `revoked_reason`
- `issued_revision`
- `created_at`

`(source_snapshot_id, source_version)`은 `rag_source_snapshot`에 결속되고, `purpose`는 `PRODUCT_IDENTIFICATION`으로 exact binding한다.

### `catalog_build_approval`

Set 생성 전 정확한 export 구성에 대한 불변 승인 사실이다.

- `id`
- `catalog_version`
- `export_checksum`
- `schema_version`
- `manifest_spec_version`
- `approved_export_bytes`
- `is_complete`
- `actor_id`
- `evidence_ref`
- `valid_from`
- `expires_at`
- `revoked_at`
- `revoked_by`
- `revoked_reason`
- `issued_revision`
- `created_at`

### `catalog_build_approval_source`

Build approval과 전체 Source approval 집합을 결속한다.

- `id`
- `build_approval_id`
- `source_approval_id`
- `source_snapshot_id`
- `source_version`
- `created_at`

같은 build 안에서 Snapshot과 Source approval의 중복을 각각 금지하고, 복합 FK로 Source approval의 Snapshot·version을 함께 고정한다.

### `catalog_approval_audit`

권한·발급·철회 event identity와 target coordinates를 보존하는 append-only 감사다.

- `id`
- `request_id`
- `event_kind`
- `actor_id`
- `subject_user_id`
- `source_approval_id`
- `build_approval_id`
- `source_snapshot_id`
- `source_version`
- `purpose`
- `catalog_version`
- `export_checksum`
- `request_fingerprint`
- `created_at`

`event_kind`는 `GRANT_PERMISSION`, `REVOKE_PERMISSION`, `ISSUE_SOURCE`, `ISSUE_CATALOG`, `REVOKE_SOURCE`, `REVOKE_CATALOG`만 허용한다. `(actor_id, request_id, event_kind)`가 멱등 key다.

증빙 위치는 다음과 같다.

- permission grant/revoke: current state의 `catalog_approval_permission.evidence_ref`; audit의 immutable request identity, fingerprint, actor, subject, event
- Source/Catalog approval: `catalog_source_approval.evidence_ref`, `catalog_build_approval.evidence_ref`
- audit: event identity, target coordinates, request fingerprint

## 4. Exact binding과 transaction 순서

`approval_binding_from_manifest`는 manifest에 기록된 exact build/source approval ID, Catalog version, checksum, schema와 Source Snapshot/version 집합을 복원한다. 최신 승인을 임의로 선택하지 않는다.

`SqlAlchemyCatalogBuildRepository.save_build()`와 `load_build()`는 같은 session/transaction에서 다음 순서를 지킨다.

1. manifest exact binding 검증
2. 정렬된 approval ID에 대한 PostgreSQL transaction advisory lock 획득
3. lock 획득 뒤 현재 시각 계산
4. exact build/source approval의 철회·유효기간·purpose·전체 link 집합 재검증
5. 검증 성공 시에만 저장 또는 소비

따라서 lock 대기 중 approval이 만료되면 과거 시각으로 통과하지 않는다. 승인 검증과 저장/소비 사이에 revoke가 끼어들 수 없으며, 저장된 Set이 있어도 load 시 현재 exact approval이 유효하지 않으면 소비하지 않는다.

## 5. 오류 의미

- `SqlAlchemyCatalogApprovalVerifier.verify()`는 승인 없음, 만료, 철회, purpose 불일치 또는 target 불일치에 `None`을 반환한다.
- 승인 조회 ambiguity는 `CatalogApprovalAmbiguityError`다.
- 저장소/조회 실패는 sanitized `CatalogApprovalStorageError`다.
- save/load exact revalidation의 invalid, revoked, expired 또는 exact-binding 실패는 `CatalogDatabaseBindingError`로 fail-closed한다.
- 운영 명령의 실패는 sanitized `CatalogApprovalCommandError(code)`다.

별도 `CatalogApprovalRevokedError`나 `CatalogApprovalExpiredError`는 이 구현 계약에 존재하지 않는다.

## 6. 감사와 원자성

Permission current state 변경, approval 발급·철회와 대응 audit INSERT는 각각 동일 transaction에서 수행한다. Audit 저장이 실패하면 상태 변경도 rollback한다. Audit row는 발급 후 갱신하거나 삭제하지 않는다.

만료는 `valid_from <= checked_at < expires_at` 비교로 판정하며 상태 전환 scheduler나 grace period를 두지 않는다.

## 7. 운영 및 공개 상태

구현된 one-shot 명령은 `grant-permission`, `revoke-permission`, `issue-product-catalog`, `revoke-source`, `revoke-catalog`다. 구현 계약 확정은 실제 운영 승인을 발급했다는 뜻이 아니다.

- actual approval issuance: **NOT RUN**
- actual Catalog materialization: **NOT RUN**
- `PUBLIC_TRACK_F`: unchanged
- `EXT-SOURCE-001`: not completed
- `LIST_PATIENT_MEDICATION_GUIDES` blocker: unresolved

## 코드 근거

- [승인 관리 CLI](../../../ai_worker/admin/catalog_approval.py)
- [승인 Advisory Lock](../../../ai_worker/adapters/catalog_approval_advisory_lock.py)
- [Catalog 저장·same-session revalidation](../../../ai_worker/adapters/sqlalchemy_catalog_write_support.py)
- [Catalog Approval verifier](../../../ai_worker/adapters/sqlalchemy_catalog_approval_verifier.py)
- [승인 ORM 모델](../../../backend/app/models/catalog_approval.py)
- [Phase 1 migration](../../../backend/alembic/versions/166f50617283_catalog_approval_storage.py)
- [Phase 2 migration](../../../backend/alembic/versions/526c1d2e3f4a_catalog_approval_permission_audit.py)
- [승인 DB role 정책](../../../infra/python/catalog_approval_role_policy.py)
- [동시성·운영 명령 통합 테스트](../../../tests/integration/rag/test_catalog_approval_command.py)
- [실제 role provisioning 통합 테스트](../../../tests/integration/rag/test_database_role_provisioning.py)
