# Canonical Evaluation Guard Evidence 계약 v1 (#162)

| 항목 | 값 |
| --- | --- |
| 상태 | Approved Contract Freeze · Phase A2 implementation in progress — PR #868 |
| 추적 Issue | [#162](https://github.com/AI-HealthCare-05/AH_05_04/issues/162) |
| 선행·관련 | [`PD-162-20260920`](../../../governance/decisions/2026-09-20-canonical-evaluation-guard-evidence.md), PR #849 ([#163](../../targets/post-mvp-1/rag-evaluation-v1.md)), [#806](./request-guard-runtime-binding-v1.md) |
| 구현 owner | `@ceohwj` (정현우) — AI/RAG |
| required reviewer | `@hazelnutflavoured` (권가빈) — PM / Product Acceptance / Evaluation & Safety |

---

## 1. 목적과 범위 (Purpose & Scope)

이 계약은 PR #849에서 protected Release Gate loader에 명시적으로 fail-closed 처리한 upstream blocker(`_require_canonical_release_guard_authority()`, `STATE_COMBINATION_INVALID`)를 해소하기 위해, **#162 소유의 canonical Evaluation Guard Evidence artifact 및 검증 가능한 Bridge 규격**을 정의한다.

본 계약은 다음 요소를 하나의 불변(immutable), 버전화(versioned), fail-closed authority graph로 결속한다:
1. **Run 수준**: `EVALUATION_CANDIDATE / PASS` Guard 권위
2. **Case 수준**: 각 Required Case별 `EVALUATION_REQUEST / PASS` Guard 권위
3. **후보 번들**: Candidate Bundle Identity (`bundle_id`, `bundle_manifest_hash`)
4. **전수 결속**: FROZEN Dataset의 Required Case 전수 일치(exact-set coverage)

### 제외 범위 (Out of Scope)
본 계약은 actual HOLDOUT 실행, Provider 호출, ANS-BASE/ANS-RAG/ANS-FINAL 실제 대조(Phase B), paired delta/CI 계산, Baseline Freeze 실행, Release Gate PASS 판정, Bundle READY/Active 전이, `PUBLIC_TRACK_F`를 포함하지 않는다.

---

## 2. 권위 좌표와 식별자 (Authority Coordinates & Identity)

### 2.1 Run-level Evidence Coordinates
Run 수준의 Guard Evidence는 `RagEvaluationRun`과 독립적으로 검증 가능한 artifact로서 최소 다음 불변 좌표에 결속된다:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `schema_id` | `Literal["rag-eval.evaluation-guard-evidence"]` | 고유 스키마 식별자 |
| `schema_version` | `Literal["1.0.0"]` | 스키마 시맨틱 버전 |
| `evaluation_run_id` | `CanonicalUuid` | 대상 `RagEvaluationRun.run_id`와 exact match |
| `candidate_bundle_id` | `CanonicalUuidString` | 대상 Candidate 번들 ID (lowercase UUID string) |
| `candidate_bundle_manifest_hash` | `Sha256Hex` | 대상 Candidate 번들 매니페스트 SHA-256 |
| `dataset_code` | `StableId` | FROZEN Dataset 코드 |
| `dataset_version` | `SemanticVersion` | FROZEN Dataset 시맨틱 버전 |
| `dataset_manifest_sha256` | `Sha256Hex` | FROZEN Dataset 매니페스트 SHA-256 |
| `required_partitions` | `tuple[Partition, ...]` | 평가 대상 필수 파티션 튜플 (UTF-16 BE ascending sorted) |
| `required_case_set_hash` | `Sha256Hex` | 필수 케이스 집합의 canonical 해시 (`evaluation-required-case-set-v1`) |
| `environment` | `Literal["LOCAL"]` | 실행 환경 (`LOCAL` 고정) |
| `environment_revision_fence` | `int` | 평가 실행 시작 시점의 환경 리비전 동시성 펜스 양의 정수 (`RagRuntimeEnvironment.environment_revision >= 1`) |
| `governance_revision_ref` | `str` | 거버넌스 리비전 식별자 (`RagRuntimeReleaseBundle.governance_revision_ref`, 환경과 exact match) |
| `safety_epoch` | `int` | 평가 실행 시작 시점의 환경 안전성 에포크 스냅샷 양의 정수 (`RagRuntimeEnvironment.safety_epoch >= 1`) |
| `candidate_guard_decision_id` | `CanonicalUuid` | Candidate Guard 결정 식별자 (canonical UUID string) |
| `candidate_guard_decision` | `Literal["PASS"]` | Candidate Guard 판정 (`PASS` 고정) |
| `candidate_guard_ref` | `GenericImmutableArtifactRef` | Candidate Guard 불변 참조 (`artifact_code="evaluation_candidate_guard"`, `version="1.0"`, `content_sha256`: `evaluation-candidate-guard-v1` 재계산) |
| `runtime_execution_manifest_id` | `CanonicalUuid` | 실행 매니페스트 식별자 (`RagRuntimeReleaseBundle.execution_manifest_id`) |
| `runtime_execution_manifest_hash` | `Sha256Hex` | 실행 매니페스트 해시 (`RagRuntimeExecutionManifest.manifest_hash`) |
| `case_guard_bindings` | `tuple[EvaluationCaseGuardBinding, ...]` | 필수 케이스별 Guard 바인딩 튜플 (UTF-16 BE case_id canonical order 강제) |
| `guard_coverage_manifest_hash` | `Sha256Hex` | 전수 결속 커버리지 매니페스트 SHA-256 |

*권위 원천 및 동시성 펜스 원칙*:
- `safety_epoch`는 런타임 번들의 역사적 빌드 시점 provenance가 아니다. Evaluation Candidate Guard 시작 시점에 `bundle.environment_code`로 exact lookup한 `RagRuntimeEnvironment.safety_epoch`를 시간적 안전성 권위 스냅샷(Evaluation-start temporal safety authority snapshot)으로 관찰한다.
- `environment_revision`은 번들 시맨틱 식별자가 아니며, 평가 실행 중 환경 상태 변경을 방지하기 위한 동시성 펜스(`environment_revision_fence`)로 기록한다.

### 2.2 Case-level Evidence Coordinates (`EvaluationCaseGuardBinding`)
각 Required Case에 결속되는 Guard Evidence는 다음 좌표를 만족해야 한다:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `case_id` | `StableId` | Dataset에 정의된 필수 케이스 ID |
| `evaluation_run_id` | `CanonicalUuid` | 부모 Run과 동일한 평가 실행 ID |
| `candidate_guard_ref` | `GenericImmutableArtifactRef` | 부모 Candidate Guard의 정본 불변 참조와 exact-bind |
| `candidate_bundle_id` | `CanonicalUuidString` | 부모 Run과 동일한 번들 ID |
| `candidate_bundle_manifest_hash` | `Sha256Hex` | 부모 Run과 동일한 번들 매니페스트 해시 |
| `runtime_execution_manifest_id` | `CanonicalUuid` | 부모 Run과 동일한 실행 매니페스트 ID |
| `runtime_execution_manifest_hash` | `Sha256Hex` | 부모 Run과 동일한 실행 매니페스트 해시 |
| `required_case_set_hash` | `Sha256Hex` | 부모 Run과 동일한 필수 케이스 집합 해시 |
| `environment` | `Literal["LOCAL"]` | 실행 환경 (`LOCAL` 고정) |
| `environment_revision_fence` | `int` | 부모 Candidate Guard에 기록된 시작 펜스와 exact-match (`positive integer`) |
| `governance_revision_ref` | `str` | 부모 Candidate Guard에 기록된 거버넌스 리비전과 exact-match |
| `safety_epoch` | `int` | 부모 Candidate Guard에 기록된 안전성 에포크 스냅샷과 exact-match (`positive integer`) |
| `case_guard_decision_id` | `CanonicalUuid` | 케이스 Guard 결정 식별자 (canonical UUID string) |
| `decision` | `Literal["PASS"]` | 케이스 Guard 판정 (`PASS` 고정) |
| `request_operation_code` | `Literal["EVALUATION_REQUEST"]` | 승인된 canonical 오퍼레이션 코드 (lowercase/alias 금지) |
| `request_scope_codes` | `tuple[str, ...]` | non-empty, unique, NFC, UTF-8 byte sorted 스코프 코드 튜플 |
| `scope_manifest_hash` | `Sha256Hex` | `canonical_scope_manifest_hash(request_scope_codes)`와 exact 일치 |
| `case_guard_ref` | `GenericImmutableArtifactRef` | Case Guard 불변 참조 (`artifact_code="evaluation_request_guard"`, `version="1.0"`, `content_sha256`: `evaluation-request-guard-v1` 재계산) |

*단일 스냅샷 재사용 원칙*:
- 모든 Case Guard는 부모 Candidate Guard의 시작 스냅샷(`environment_revision_fence`, `governance_revision_ref`, `safety_epoch`)을 그대로 재사용하여 exact-bind한다.
- 케이스 실행 중 환경을 개별 재조회하여 서로 다른 epoch를 기록하는 것은 엄격히 금지된다 (`Candidate epoch == Case1 epoch == ... == CaseN epoch` 필수).

---

## 3. 정본 해시 및 사영 레시피 (Canonical Hash Recipes)

### 3.1 Candidate Guard Ref 사영 레시피 (`evaluation-candidate-guard-v1`)
Candidate Guard의 불변 식별자(`candidate_guard_ref.content_sha256`)는 다음 정본 사영을 직렬화하여 산출한다:

```json
{
  "projection_version": "evaluation-candidate-guard-v1",
  "candidate_guard_decision_id": "<canonical_uuid>",
  "operation": "EVALUATION_CANDIDATE",
  "decision": "PASS",

  "evaluation_run_id": "<canonical_uuid>",

  "environment": "LOCAL",
  "environment_revision_fence": 1,
  "governance_revision_ref": "<nonblank_nfc_ref>",
  "safety_epoch": 1,

  "candidate_bundle_id": "<canonical_lowercase_uuid>",
  "candidate_bundle_manifest_hash": "<sha256>",

  "runtime_execution_manifest_id": "<canonical_uuid>",
  "runtime_execution_manifest_hash": "<sha256>",

  "dataset_code": "<stable_id>",
  "dataset_version": "<semantic_version>",
  "dataset_manifest_sha256": "<sha256>",

  "required_partitions": [
    "HOLDOUT",
    "SAFETY_REGRESSION"
  ],

  "required_case_set_hash": "<sha256>"
}
```
- **직렬화 및 해시**: 저장소 정본 `canonical_sha256()` 함수를 단일 source로 사용한다.
  `candidate_guard_content_sha256 = canonical_sha256(candidate_guard_projection)`
- **불변 참조 (`candidate_guard_ref`)**:
  ```json
  {
    "artifact_code": "evaluation_candidate_guard",
    "version": "1.0",
    "content_sha256": "<candidate_guard_content_sha256>"
  }
  ```
- **검증기 규칙**: 검증기는 artifact에 기록된 `candidate_guard_ref.content_sha256` 문자열을 신뢰하지 않으며, 필드에서 사영을 재구성한 뒤 `canonical_sha256()`을 재계산하여 exact match를 검증한다. 불일치 시 즉시 fail-closed한다.
- **파티션 정렬**: `required_partitions`는 enum wire 문자열을 **UTF-16 BE** 오름차순으로 정렬한다. 중복 파티션은 즉시 거부한다.

### 3.2 Case Guard Ref 사영 레시피 (`evaluation-request-guard-v1`)
각 Case Guard의 불변 식별자(`case_guard_ref.content_sha256`)는 다음 정본 사영을 직렬화하여 산출한다:

```json
{
  "projection_version": "evaluation-request-guard-v1",
  "case_guard_decision_id": "<canonical_uuid>",
  "operation": "EVALUATION_REQUEST",
  "decision": "PASS",

  "evaluation_run_id": "<canonical_uuid>",
  "case_id": "<stable_case_id>",

  "candidate_guard_ref": {
    "artifact_code": "evaluation_candidate_guard",
    "version": "1.0",
    "content_sha256": "<candidate_guard_content_sha256>"
  },

  "environment": "LOCAL",
  "environment_revision_fence": 1,
  "governance_revision_ref": "<nonblank_nfc_ref>",
  "safety_epoch": 1,

  "candidate_bundle_id": "<canonical_lowercase_uuid>",
  "candidate_bundle_manifest_hash": "<sha256>",

  "runtime_execution_manifest_id": "<canonical_uuid>",
  "runtime_execution_manifest_hash": "<sha256>",

  "required_case_set_hash": "<sha256>",

  "request_scope_codes": [
    "<canonical_scope_code>"
  ],

  "scope_manifest_hash": "<sha256>"
}
```
- **Candidate Ref 직접 결속**: Case Guard 사영 내에 부모의 `candidate_guard_ref`를 직접 포함함으로써, 다른 Candidate Guard에서 발행된 Case Guard가 동일 Run 커버리지에 혼입되는 것을 원천 차단한다.
- **스코프 규칙**: #806 순수 시맨틱에 따라 `request_scope_codes`는 non-empty, unique, NFC, UTF-8 byte sorted여야 하며, `scope_manifest_hash == canonical_scope_manifest_hash(request_scope_codes)`를 필수 검증한다.
- **직렬화 및 해시**: `case_guard_content_sha256 = canonical_sha256(case_guard_projection)`
- **불변 참조 (`case_guard_ref`)**:
  ```json
  {
    "artifact_code": "evaluation_request_guard",
    "version": "1.0",
    "content_sha256": "<case_guard_content_sha256>"
  }
  ```
- **검증기 규칙**: 검증기는 사영을 재구성하여 ref의 `content_sha256`을 재계산 검증한다.

### 3.3 Required Case Set Recipe (`evaluation-required-case-set-v1`)
Required Case 집합의 정본성은 단순 ID 목록이 아니라 승인된 FROZEN Dataset의 manifest 및 partition과 결속되어야 한다.

```json
{
  "projection_version": "evaluation-required-case-set-v1",
  "dataset_manifest_sha256": "<FROZEN Dataset manifest_sha256>",
  "required_partitions": ["HOLDOUT", "SAFETY_REGRESSION"],
  "case_ids": ["rag-case-001", "rag-case-002", "..."]
}
```
- **정렬 규칙**: `case_ids`는 저장소의 canonical ordering 기준인 **UTF-16 BE** 바이트 순서(`sorted(case_ids, key=lambda s: s.encode("utf-16-be"))`)로 정렬한다.
- **중복 검사**: 해시 계산 전 중복된 case ID가 발견되면 즉시 거부(`CASE_DUPLICATE`)한다.
- **해시 계산**: `required_case_set_hash = canonical_sha256(projection)`

### 3.4 Guard Coverage Recipe (`evaluation-guard-coverage-v1`)
전체 Case Coverage의 암호학적 결속을 위해 다음 preimage를 직렬화하여 해시를 산출한다:

```json
{
  "projection_version": "evaluation-guard-coverage-v1",
  "evaluation_run_id": "<canonical_uuid_string>",
  "candidate_bundle_id": "<canonical_lowercase_uuid>",
  "candidate_bundle_manifest_hash": "<sha256_hex>",
  "runtime_execution_manifest_id": "<canonical_uuid_string>",
  "runtime_execution_manifest_hash": "<sha256_hex>",
  "environment": "LOCAL",
  "environment_revision_fence": 1,
  "governance_revision_ref": "<nonblank_nfc_ref>",
  "safety_epoch": 1,
  "candidate_guard_ref": {
    "artifact_code": "evaluation_candidate_guard",
    "version": "1.0",
    "content_sha256": "<recomputed_sha256>"
  },
  "dataset_manifest_sha256": "<sha256_hex>",
  "required_case_set_hash": "<evaluation-required-case-set-v1_sha256>",
  "case_guard_bindings": [
    {
      "case_id": "rag-case-001",
      "case_guard_decision_id": "<canonical_uuid_string>",
      "case_guard_ref": {
        "artifact_code": "evaluation_request_guard",
        "version": "1.0",
        "content_sha256": "<recomputed_sha256>"
      },
      "scope_manifest_hash": "<canonical_scope_manifest_hash>"
    }
  ]
}
```
- **해시 계산**: `guard_coverage_manifest_hash = canonical_sha256(preimage)`
- **해시 레시피 정본 동결**:
  - `evaluation-guard-coverage-v1`의 모든 권위 좌표와 Envelope 구조가 확정되었다.
  - Candidate Guard ref와 Case Guard ref는 self-asserted 값이 아니라 검증기가 사영에서 독립 재계산한 `content_sha256`만을 사용한다.
  - `environment_revision_fence`는 동시성 펜스로서 바인딩된다.
- **불변 속성**:
  - 동일 케이스 집합이라도 `candidate_bundle_id` 또는 `candidate_bundle_manifest_hash`가 다르면 다른 해시 생성
  - 단 1개의 필수 케이스가 누락되거나 바인딩 순서가 뒤바뀌면 다른 해시 생성 또는 검증 거부
  - 케이스의 Guard ref 또는 스코프 해시가 변조되면 다른 해시 생성

### 3.5 커버리지 계산 순서 (Coverage Calculation Order)
커버리지 계산과 검증은 다음 13단계 고정 순서로 진행되어야 하며, 자의적 ref를 주입할 수 없다:
1. FROZEN Dataset validation (`validate_release_dataset_authority`)
2. required case set 계산 (`derive_required_case_ids`)
3. `required_case_set_hash` 계산 (`compute_required_case_set_hash`)
4. Environment start snapshot 확보 (`environment_code`, `environment_revision`, `governance_revision_ref`, `safety_epoch`)
5. Candidate Guard projection 구성 (`evaluation-candidate-guard-v1`)
6. `candidate_guard_ref` 재계산 (`canonical_sha256`)
7. 모든 Case Guard projection 검증 (`evaluation-request-guard-v1`)
8. 모든 `case_guard_ref` 재계산 (`canonical_sha256`)
9. duplicate / missing / extra exact-set 검증
10. `case_guard_bindings` UTF-16 BE `case_id` 순 canonicalization
11. coverage projection 구성 (`evaluation-guard-coverage-v1`)
12. `canonical_sha256(preimage)` 계산
13. `guard_coverage_manifest_hash` 생성

### 3.6 Run Finalization Concurrency Fence 순서
- Evaluation 실행 시:
  ```text
  Candidate start environment snapshot
          ↓
  Candidate Guard 발행
          ↓
  모든 Case Guard 실행 및 바인딩
          ↓
  Environment exact re-read (bundle.environment_code)
          ↓
  revision == fence AND governance == gov AND epoch == epoch ?
          ↓
  YES → coverage finalization 허용 (COMPLETED/PASS 후보)
  NO  → Run authority INVALID, decision_status = null, 권위 사용 금지
  ```
- coverage hash finalization은 반드시 end-fence 검증이 PASS된 이후에만 허용된다. 실행 도중 환경이 변경되었을 경우 valid coverage receipt를 발행하는 것은 엄격히 금지된다.

---

## 4. 전수 결속 의미와 오류 규격 (Exact-Set Coverage Semantics)

`case_guard_bindings`의 검증은 단순 개수(count) 일치 검사가 아니며, Dataset authority에서 도출된 `expected_case_ids`와의 **exact-set** 일치여야 한다.

| 위반 조건 | 검증 판정 | 오류 코드 |
| --- | --- | --- |
| 필수 케이스 누락 (missing case) | 즉시 거부 (Fail closed) | `BASELINE_ARTIFACT_INVALID` |
| 동일 케이스 중복 바인딩 (duplicate case) | 즉시 거부 (Fail closed) | `CASE_DUPLICATE` |
| Dataset에 없는 케이스 포함 (extra case) | 즉시 거부 (Fail closed) | `BASELINE_ARTIFACT_INVALID` |
| 비정규 와이어 순서 (non-canonical wire order) | 즉시 거부 (Fail closed) | `BASELINE_ARTIFACT_INVALID` |
| 케이스 순서 불일치 (order mismatch in hash) | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| `evaluation_run_id` 불일치 | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| `candidate_bundle_id` 불일치 | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| `candidate_bundle_manifest_hash` 불일치 | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| `runtime_execution_manifest` / `governance_revision` / `epoch` 불일치 | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| `scope_manifest_hash` 불일치 | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| Guard ref 변조 또는 무효 (tampered / invalid Guard ref) | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| 동시성 펜스 위반 (end-fence concurrency failure) | 즉시 거부 (Fail closed) | `BASELINE_ARTIFACT_INVALID` |
| non-PASS decision (FAIL 등) | 즉시 거부 (Fail closed) | `BASELINE_ARTIFACT_INVALID` |
| 허용되지 않은 operation code | 즉시 거부 (Fail closed) | `BASELINE_ARTIFACT_INVALID` |

### 4.1 Canonical Ordering Rules (`case_guard_bindings`)
- `case_guard_bindings`의 정본 정렬 키는 저장소 표준인 **UTF-16 BE** 바이트 순서(`sorted(case_guard_bindings, key=lambda binding: binding.case_id.encode("utf-16-be"))`)로 확정한다.
- **와이어 아티팩트 순서 규칙 (Wire Order Enforcement)**:
  - 아티팩트 파일 또는 JSON payload에 직렬화된 `case_guard_bindings` 배열의 실제 순서 또한 반드시 이 canonical 순서와 일치해야 한다.
  - 와이어 아티팩트의 배열 순서가 비정규 순서(non-canonical order)인 경우, 검증기는 정렬을 대신 보정해 주지 않고 즉시 `BASELINE_ARTIFACT_INVALID`로 거부(fail closed)한다.

### 4.2 재계산 기반 Guard Reference 무결성 (Recomputed Guard Ref Integrity)
- 검증기는 증거 아티팩트에 자가 주장된(self-asserted) `candidate_guard_ref`나 `case_guard_ref`의 `content_sha256` 값을 맹신하지 않는다.
- `evaluation-candidate-guard-v1` 및 `evaluation-request-guard-v1`의 원본 사영 필드들을 재구성하여 `canonical_sha256`을 직접 재계산한다.
- 커버리지 해시(`evaluation-guard-coverage-v1`) 계산 및 검증에는 오직 재계산된 정본 ref만을 사용하며, 주입된 ref와 불일치 시 `HASH_MISMATCH`로 fail closed한다.

### 4.3 Run Finalization Concurrency Fence 검증
- 평가 실행 완료 및 커버리지 해시 확정 직전에 `bundle.environment_code`에 해당하는 `RagRuntimeEnvironment` row를 exact re-read한다:
  - `end.environment_revision == candidate.environment_revision_fence`
  - `end.governance_revision_ref == candidate.governance_revision_ref`
  - `end.safety_epoch == candidate.safety_epoch`
- 위 3개 조건 중 단 하나라도 불일치하면 평가 실행 도중 환경 권위가 전이된 것으로 판정하여 즉시 `BASELINE_ARTIFACT_INVALID` 처리하고, 해당 Run의 `decision_status = null` 처리하여 어떠한 Release Gate 권위로도 사용하지 못하게 차단한다.

---

## 5. 데이터 타입 및 와이어 규칙 (Data Types & Wire Rules)

1. **Runtime Bundle ID Wire Rule**:
   - Runtime의 물리 bundle ID는 PostgreSQL의 `UUID`다.
   - `RagEvaluationRun.candidate_bundle_id`는 현재 스키마상 `StableId`다.
   - 본 계약에서는 `RagEvaluationRun` 스키마를 변경하지 않고, canonical lowercase UUID string(예: `123e4567-e89b-12d3-a456-426614174000`)을 표준 와이어 포맷으로 규정한다.
   - 검증기는 `str(runtime_bundle_uuid) == run.candidate_bundle_id`를 exact-match하고, 대문자 또는 비정규 포맷은 fail closed한다.
2. **Candidate Guard Decision ID 물리 타입**:
   - `RagEvaluationRun.candidate_guard_decision_id`의 wire 포맷은 `CanonicalUuid` 문자열로 확정한다.
   - Evaluator는 run-scoped의 고유 `UUID`를 발행하여 감사 추적성을 보장하며, 비정규 UUID 문자열은 검증기에서 fail-closed 처리한다.
3. **Case Guard Decision ID 물리 타입**:
   - `EvaluationCaseGuardBinding.case_guard_decision_id`의 wire 포맷은 `CanonicalUuid` 문자열로 확정한다.
   - 라이브 요청과 명확히 구분하기 위해 `case_guard_decision_id`를 정규 필드로 사용한다.
4. **Generic Immutable Artifact Reference**:
   - `#806`의 `RequestGuardRuntimeBindingRef`와 동일한 `(artifact_code, version, content_sha256)` 3요소 형식을 기본으로 사용한다.
   - 평가용 `ImmutableReference`(id/version/hash)로 임의 필드명 변환을 수행하지 않는다.
5. **오퍼레이션 코드 엄격성**:
   - `EVALUATION_REQUEST` 단일 canonical 대문자 값만 허용한다.
   - `"evaluation_request"`, `"Evaluation_Request"` 등 임의의 lowercase 또는 alias 허용 집합은 금지한다.
6. **Environment Concurrency Fence (`environment_revision_fence`)**:
   - `RagRuntimeEnvironment.environment_revision`은 Candidate 번들 자체의 provenance identity가 아니라, 평가 실행 도중 환경 전이를 감지하기 위한 **동시성 펜스 (`environment_revision_fence: int >= 1`)**로 바인딩된다.
   - 평가 시작 시점에 관찰되어 Candidate Guard, Case Guard, Coverage에 포함되며, Run 종료 시점에 환경이 변경되지 않았음을 재확인하는 펜스로 동작한다.
7. **#806 권위 원천 분리 및 무영속**:
   - #806의 `request_guard_runtime_binding` 테이블에는 일체 쓰거나 조회하지 않으며, 순수 시맨틱(UTF-8 정렬 스코프 코드, `canonical_scope_manifest_hash`, 불변 참조 구조 등)만 재사용하고 평가는 독립 결과 아티팩트로 저장한다.
8. **Standalone Versioned Artifact 상태**:
   - 기존 Evaluation Schema Set 1.0~1.5(`schema_registry.py`)는 변경하지 않으며, `rag-eval.evaluation-guard-evidence@1.0.0`은 독립 버전화 권위 아티팩트로 취급한다.
9. **Safety Epoch (`safety_epoch`)의 시간적 권위 스냅샷**:
   - `safety_epoch`는 Bundle build-time 속성이 아니라, Candidate 평가 시작 시점에 관찰된 시간적 안전성 권위 스냅샷(`int >= 1`)이다.
   - 모든 Case Guard와 Coverage는 이 동일 snapshot을 바인딩하며 (`Candidate epoch == Case1 epoch == ... == CaseN epoch`), 과거 번들에 대한 DB 마이그레이션이나 백필 없이 동작한다.
   - Run 종료 시점의 re-read에서 epoch가 변경되었으면 해당 평가는 fail closed된다.
10. **Candidate Guard Ref의 직접 결속**:
    - 모든 Case Guard projection (`evaluation-request-guard-v1`)은 부모 `candidate_guard_ref`를 직접 포함한다.
    - 이를 통해 서로 다른 Candidate Guard에서 발행된 Case Guard가 단일 Run coverage에 교차 혼입(splicing)되는 것을 암호학적으로 차단한다.

---

## 6. 개인정보 보호 경계 (Privacy Boundary)

Evaluation Guard Evidence artifact는 평가의 무결성과 권위만을 증명하는 메타데이터 계층이다:
- 원문 환자 질문(query text), 응답(answer text), 환자 식별자(patient ID), Provider 원문 응답 payload, 시스템 자격증명(credential)은 artifact 필드, 로그, 오류 메시지에 일체 포함되어서는 안 된다.
- 검증 시 비민감 식별자(`case_id`, `run_id`, `bundle_id`, `hash`)만을 대상으로 처리한다.

---

## 7. 소비 경계와 Release Gate 연동 (Consumer Boundary)

### 7.1 `RagEvaluationRun` Exact Binding
canonical Guard evidence 검증을 통과한 후, 대상 `RagEvaluationRun`과의 exact match를 검증한다:
- `run.run_id == evidence.evaluation_run_id`
- `run.candidate_bundle_id == evidence.candidate_bundle_id`
- `run.candidate_bundle_manifest_hash == evidence.candidate_bundle_manifest_hash`
- `run.candidate_guard_decision_id == evidence.candidate_guard_decision_id`
- `run.required_case_guard_coverage_manifest_hash == evidence.guard_coverage_manifest_hash`
하나라도 불일치할 경우 `HASH_MISMATCH`로 fail closed한다.

### 7.2 Release Gate Loader 경계 (#163)
- `release_gate_loader.py`의 `_require_canonical_release_guard_authority()`는 본 Phase A1 및 A2 PR에서 수정하지 않는다.
- 본 계약(Phase A1) 승인 후, Phase A2(Guard producer/bridge) 및 Phase B(Paired comparison)가 완전히 병합된 후속 `#163` PR에서만 loader를 연결한다.
- 이로써 도메인 간 책임 경계를 분리하고 조기 승인 누수를 방지한다.

---

## 8. Phase A2 공유 파라미터 테스트 계획 (Shared Parameter Test Plan)

리뷰어 요청에 따라 Phase A2 구현 시 필수로 만족해야 하는 테스트 매트릭스를 계약에 동결한다 (실제 구현은 Phase A2에서 진행):

### 8.1 Candidate Projection 필드 변조 매트릭스 (Field Mutation Matrix)
다음 17개 필드를 각 1개씩 단독 변조(one-at-a-time mutation)하여 재계산된 `content_sha256`이 변경되거나 검증기에서 거부됨을 확인:
- `candidate_guard_decision_id`
- `operation`
- `decision`
- `evaluation_run_id`
- `environment`
- `environment_revision_fence`
- `governance_revision_ref`
- `safety_epoch`
- `candidate_bundle_id`
- `candidate_bundle_manifest_hash`
- `runtime_execution_manifest_id`
- `runtime_execution_manifest_hash`
- `dataset_code`
- `dataset_version`
- `dataset_manifest_sha256`
- `required_partitions`
- `required_case_set_hash`

### 8.2 Case Projection 필드 변조 매트릭스 (Field Mutation Matrix)
다음 17개 필드를 각 1개씩 단독 변조하여 재계산된 `content_sha256` 불일치 또는 검증 거부를 확인:
- `case_guard_decision_id`
- `operation`
- `decision`
- `evaluation_run_id`
- `case_id`
- `candidate_guard_ref`
- `environment`
- `environment_revision_fence`
- `governance_revision_ref`
- `safety_epoch`
- `candidate_bundle_id`
- `candidate_bundle_manifest_hash`
- `runtime_execution_manifest_id`
- `runtime_execution_manifest_hash`
- `required_case_set_hash`
- `request_scope_codes`
- `scope_manifest_hash`

### 8.3 필수 네거티브 테스트 (Mandatory Negative Tests)
- Candidate `operation != "EVALUATION_CANDIDATE"` → reject
- Candidate `decision != "PASS"` → reject
- Case `operation != "EVALUATION_REQUEST"` → reject
- Case `decision != "PASS"` → reject
- Lowercase / mixed-case operation alias (예: `"evaluation_request"`) → reject
- 변조된(tampered) `candidate_guard_ref` → reject
- 변조된(tampered) `case_guard_ref` → reject
- `request_scope_codes`가 변경되었으나 과거 `scope_manifest_hash` 유지 → reject
- Case에 부모 Run과 다른 Candidate Guard ref가 바인딩됨 → reject

### 8.4 Case-set 테스트 매트릭스 (Case-set Test Matrix)
- 필수 케이스 1개 누락 (missing case) → reject (`BASELINE_ARTIFACT_INVALID`)
- 동일 케이스 중복 바인딩 (duplicate case) → reject (`CASE_DUPLICATE`)
- Dataset에 없는 초과 케이스 (extra case) → reject (`BASELINE_ARTIFACT_INVALID`)
- 케이스 수는 일치하나 잘못된 케이스 ID 포함 → reject
- `case_guard_bindings` 순서 반전 (reversed wire ordering) → non-canonical artifact로 reject
- 단 1개 케이스의 Guard ref 변경 → coverage hash mismatch
- 단 1개 케이스의 decision ID 변경 → coverage hash mismatch

### 8.5 Canonical Ordering 테스트 (Canonical Ordering Tests)
- Canonical 정렬(`case_id.encode("utf-16-be")`)된 바인딩 → 정상 수락
- 역순 또는 임의 순서의 wire 바인딩 → reject
- 임의 입력 순서로부터 빌더 실행 시 항상 canonical order로 출력 생성
- UTF-16 BE 경계 문자열 fixture 검증으로 일관된 결정론적 정렬 보장 (새 JCS 라이브러리 도입 금지, 저장소 정본 재사용)

### 8.6 Safety Epoch 시간적 상태 전이 테스트 (Temporal Transition Tests)
- **정상 완료 (Stable)**:
  - 시작 snapshot: `revision_fence=10, governance="GOV-A", epoch=4`
  - 전 Case 바인딩: `10 / "GOV-A" / 4`
  - 종료 re-read: `10 / "GOV-A" / 4`
  - 판정: coverage finalization 정상 허용
- **Epoch 변경 발생 (Epoch Change)**:
  - 종료 re-read: `revision=11, governance="GOV-A", epoch=5`
  - 판정: reject (INVALID)
- **Governance 변경 발생 (Governance Change)**:
  - 종료 re-read: `revision=11, governance="GOV-B", epoch=4`
  - 판정: reject (INVALID)
- **무관한 환경 리비전 증가 발생 (Unrelated Revision Change)**:
  - 종료 re-read: `revision=11, governance="GOV-A", epoch=4`
  - 판정: reject (동시성 펜스 위반에 따른 보수적 거부)
