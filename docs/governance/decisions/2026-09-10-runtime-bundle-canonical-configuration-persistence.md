# Product Decision: Runtime Bundle canonical 구성 영속화와 `bundle_manifest_hash` 검증 계약

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-175-20260910` |
| 상태 | **Proposed** — 지정 책임 리뷰어 승인 대기 |
| 제안일 | 2026-09-10 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG 구현 담당 |
| 책임 리뷰 | 송은영 (`@phina-io`) — persistence·FK·transaction / 권가빈 (`@hazelnutflavoured`) — Product·Safety |
| 추적 Issue·PR | [#175](https://github.com/AI-HealthCare-05/AH_05_04/issues/175) · [PR #416](https://github.com/AI-HealthCare-05/AH_05_04/pull/416) |
| 관련 계약 | [`rag-runtime-v1.md`](../../contracts/targets/post-mvp-1/rag-runtime-v1.md) — `Approved Target · Not implemented` |
| Migration | `175a1b2c3d4e` (`201a1b2c3d4e` 후속) |
| 선행 Decision | 없음. #164 저장 구조를 추정 없이 확장한다. |

## 목적

`bundle_manifest_hash`가 **저장값으로 재계산·검증 가능한 identity**가 되도록, 해시에 들어가는 전체 canonical 구성을 영속화한다. 그리고 해시 입력과 저장 컬럼의 정합을 계약으로 고정한다.

## 배경 — 확인된 결함

PR #416 리뷰에서 확인된 사실이다.

`bundle_manifest_hash` 입력 중 다음 값은 **어디에도 저장되지 않았다.**

| 해시 입력 | #164 저장 상태 |
| --- | --- |
| `approval_version` | 컬럼 없음 |
| `scope_policy_hash` | 컬럼 없음 |
| `freshness_policy_hash` | 컬럼 없음 |
| artifact `version` | 컬럼 없음 |
| 대상 환경 | 컬럼 없음 |
| Medication Catalog `version`·`manifest_hash` | 컬럼 없음 |

결과:

1. **재검증 불가.** `rag-runtime-v1.md` 「활성화·Rollback·Resume Guard」는 포인터 교체 직전 Bundle Manifest 재검증을 요구한다. 저장값으로 해시를 재계산할 수 없으면 이 요구를 충족할 수 없고, `bundle_manifest_hash`는 불투명 토큰에 그친다.
2. **저장 구성 충돌.** artifact version만 다른 두 구성이 서로 다른 해시를 갖지만 저장 행은 바이트 동일해진다. 평가 대상과 실행 대상이 같음을 저장값으로 증명할 수 없다. 이 Issue가 막으려는 drift가 반대 방향으로 발생한다.
3. **member set 불변 미보장.** 생성 후 member를 추가해도 저장 상태만으로는 탐지할 수 없다.

## 결정

### 1. 해시 입력 = 저장 컬럼 (정합 원칙)

`bundle_manifest_hash`의 입력은 `RuntimeBundleCanonicalConfiguration` **하나뿐**이며, 그 모든 필드는 영속화된다. 해시에 값을 추가하려면 같은 변경에서 컬럼도 추가한다. 이 원칙을 깨는 변경은 계약 위반으로 취급한다.

### 2. 추가 컬럼 (Migration `175a1b2c3d4e`, 순수 additive)

`rag_runtime_bundle_source`:

| 컬럼 | 타입 | 근거 |
| --- | --- | --- |
| `source_version` | `String(255)` NOT NULL | 해시 입력. 복합 FK로 pinning |
| `canonical_checksum` | `String(64)` NOT NULL | 해시 입력 |
| `approval_version` | `String(80)` NOT NULL | 해시 입력 |
| `scope_policy_hash` | `String(64)` NOT NULL | 해시 입력 |
| `freshness_policy_hash` | `String(64)` NOT NULL | 해시 입력 |

복합 FK `fk_rag_runtime_bundle_source_snapshot_version` `(source_snapshot_id, source_version)` → `rag_source_snapshot(id, source_version)`를 추가한다. #164가 만든 `uq_rag_source_snapshot_id_version`이 정확히 이 용도의 unique이며, 복사한 문자열을 신뢰하는 대신 **참조로 version을 고정**한다. member가 snapshot에 없는 version을 주장할 수 없다. #164의 단일 컬럼 FK `fk_rag_runtime_bundle_source_snapshot`은 **유지한다** — 복합 FK가 이를 포섭하지만, 머지된 constraint를 제거하는 것은 이 Issue 범위보다 넓고 `tests/migration`이 그 존재를 단정한다.

`rag_runtime_release_bundle`:

| 컬럼 | 타입 | 근거 |
| --- | --- | --- |
| `environment_code` | `String(50)` NOT NULL | 해시 입력. bundle 행에 환경 결속 지점이 없었다 |
| `catalog_version` | `String(80)` NOT NULL | 해시 입력. Catalog 승인 결속 |
| `catalog_manifest_hash` | `String(64)` NOT NULL | 해시 입력. Catalog 승인 결속 |
| `candidate_index_version` 외 4종 `*_version` | `String(80)` NULL | 해시 입력. artifact identity는 ref+version 쌍이다 |

artifact 5종에 `({kind}_ref IS NULL) = ({kind}_version IS NULL)` CHECK를 추가한다. ref만, 또는 version만 저장된 절반 상태는 identity가 아니므로 저장 불가로 만든다.

### 3. 기존 행 backfill 금지

Migration은 `rag_runtime_release_bundle`·`rag_runtime_bundle_source`에 행이 있으면 **실패한다.** `rag-runtime-v1.md`가 Post-MVP-1 RAG Runtime을 Local 전용·`PUBLIC_TRACK_F=false`·Development/Staging 없음으로 고정했으므로 마이그레이션할 배포 Bundle이 없다. 기존 행에 placeholder 승인·정책 hash를 채우면 검증되지 않은 내용에 provenance를 조작해 넣는 것이며, 이 변경이 막으려는 바로 그 일이다. 추정하지 않고 실패한다.

### 4. Medication Catalog 승인은 Snapshot 존재로 대체하지 않는다

`CATALOG` purpose source member가 있다는 사실은 그 Catalog가 승인·완성되었다는 증거가 아니다. Bundle build는 `CandidateCatalogExport`(#166/#167)의 `verification_status`·`freshness_status`·`is_complete`를 필수 입력으로 받아 검증하고, 다음을 결속한다.

- 고정된 `CATALOG` member snapshot ⊆ Catalog의 `source_refs`
- Candidate Index의 `catalog_version`·`catalog_manifest_hash` exact-match

### 5. 승인 관측값의 기본값 금지 (fail-closed)

`RuntimeBundleSourceMemberInput`·`RuntimeBundleArtifactMemberInput`의 승인·freshness·revocation·scope 필드는 **기본값을 갖지 않는다.** 생략을 허용으로 읽으면 최소 인자 생성이 곧 통과가 된다. 기본값은 pinning 설정(`required`, `selected_for_operation`)에만 둔다.

### 6. member set 불변은 검증으로 보장한다

`CONTRIBUTING.md`가 Trigger 도입을 금지하므로 DB가 물리적으로 INSERT를 막지는 않는다. 대신 `verify_persisted_bundle_manifest_hash`가 저장 행에서 해시를 재계산해 저장값과 비교한다. member 추가·변경은 재계산 해시 불일치로 **탐지된다.** 이 성질을 통합 테스트로 고정한다.

## 검토한 대안

| 대안 | 판단 |
| --- | --- |
| **해시를 저장 가능한 범위로 축소** (승인·scope·freshness policy hash·artifact version·환경을 해시에서 제거) | 기각. `rag-runtime-v1.md`가 Guard에 「Bundle 전체 Source·Snapshot Member의 승인·Freshness·Scope Policy 무결성」 검사를 요구한다. 이를 identity에서 빼면 계약이 요구하는 결속이 약해진다. |
| **정규화된 `rag_runtime_bundle_artifact` 테이블 도입** | 보류. `rag_runtime_bundle_source`와 대칭이고 가변 cardinality에 적합하지만, #164가 머지한 bundle 행 컬럼 7개를 폐기해야 해 변경 폭이 커진다. artifact 종류는 계약이 5종으로 고정하므로 additive 컬럼으로 현재 요구를 충족한다. 종류가 늘거나 artifact별 `required`/`selected_for_operation`이 필요해지면 재검토한다. |
| **DB Trigger로 member 불변 강제** | 기각. `CONTRIBUTING.md`가 요구사항·승인 계약에 없는 Trigger 도입을 금지한다. 재계산 검증으로 탐지 가능하다. |
| **`backend` 대신 `ai_worker/adapters` Protocol+adapter로 build port 구현** | 기각. `sqlalchemy_source_snapshot_repository.py` 방식은 `table()` 리터럴로 스키마를 재선언하는데, Bundle 6개 테이블의 model·migration·리뷰어가 모두 `backend`에 있어 스키마 정본이 이중화된다. |

## 함께 결정할 사항 (리뷰 필요)

### `backend` → `ai_worker` production 의존

`backend/app/services/rag_runtime_bundle_build.py`는 **`backend`가 `ai_worker`를 production 코드에서 import하는 첫 사례**다. 추가되는 의존은 I/O·시계·session이 없는 순수 kernel 모듈 하나이며, `backend`가 이미 `provider_contracts`를 import하는 것과 같은 형태다. 역방향(`ai_worker` → `backend`)은 계속 금지이며 Worker 테스트 lane이 이를 강제한다(확인: `backend` 제외 PYTHONPATH에서 kernel 테스트 통과).

이 경계를 승인할지, 아니면 kernel을 최상위 `rag_runtime/` 공유 패키지로 옮길지 결정이 필요하다. 후자는 kernel이 의존하는 `ai_worker.tasks.rag.catalog`·`source_ingestion` 모듈까지 함께 옮겨야 하므로 이 Issue 범위를 넘는다.

## 미해소로 남기는 항목

이 Decision은 다음을 해소하지 않는다.

- **Worker–Bundle 호환성 검사.** [`post-mvp-1-document-authority.md`](../post-mvp-1-document-authority.md) 「Runtime Bundle과 Worker 배포」 미정 충돌이 유효하다. `worker_artifact_ref`는 Manifest에 고정만 하고 호환성 판정은 `deferred_checks`에 `BLOCKED_BY_RUNTIME_BUNDLE_WORKER_DEPLOYMENT_DECISION`으로 기록한다.
- **Graph·Validator version 고정.** `rag-runtime-v1.md`는 Bundle이 Graph·Validator를 고정한다고 적었으나 manifest 테이블에 `graph_ref`·`validator_ref`가 없다. 컬럼 추가와 계약 문장 개정 중 어느 쪽인지 미결이다.
- **`current/` 승격.** 위 두 항목과 외부 승인 게이트가 남아 있어 `rag-runtime-v1.md`는 `targets/`에 유지한다.

## 승인 및 적용 조건

1. `@phina-io`가 migration·복합 FK·CHECK·transaction 경계와 「해시 입력 = 저장 컬럼」 원칙을 승인한다.
2. `@hazelnutflavoured`가 backfill 금지와 fail-closed 기본값 금지를 승인한다.
3. 두 승인 모두 PR #416 최신 HEAD 기준으로 기록한다. 승인 전에는 이 Decision을 `Approved`로 전이하지 않으며, PR 병합만으로 승인을 대체하지 않는다.
4. `backend` → `ai_worker` 경계 결정을 함께 기록한다.
