# Product Decision Candidate: Canonical Evaluation Guard Evidence and Authority Binding (#162)

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-162-20260920` |
| 상태 | Phase A1 Approved (PR #868) · Phase A2 Merged (PR #883) · Phase A3 Proposal Merged (PR #895) · Phase A3.5 Freshness·Scope Policy Authority Freeze Proposed · Authority Resolution Required |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 단일 책임 리뷰 | 송은영 (`@phina-io`) — Backend·Data·Security / DB schema · migration · Repository 쓰기 경계 (Phase A3.5 Authority Formal Resolution) |
| 선행 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — PM / Product Acceptance / Evaluation & Safety (Phase A1 APPROVED 2026-09-20T04:11:20Z / Phase A3 APPROVED 2026-09-20T08:52:13Z) |
| 승인 근거 | Phase A1: PR #868 책임 리뷰 `APPROVED` (`@hazelnutflavoured`) · Merge commit `896ef7c5`<br>Phase A2: PR #883 머지 (`a19ec7e7`)<br>Phase A3: PR #895 머지 (`4ab7bf68`)<br>Phase A3.5: Proposed · Authority Resolution Required (`@phina-io`) |
| 교차 리뷰 (FYI) | 김지혜 (`@Jye-rookie`) — Source·Snapshot·Catalog 경계 / Worker |
| 추적 Issue | [#162](https://github.com/AI-HealthCare-05/AH_05_04/issues/162) |
| 조사 기준 | `origin/develop` @ `b3a92f32f0cfd6420d55de0ccf0b3c86216feb74` (PR #849 merge commit `f02f8f06`) |
| 상위·관련 결정 | [`PD-125-20260831`](./2026-08-31-rag-p0-contract-freeze.md), [`PD-216-20260902`](./2026-09-02-rag-evaluation-schema-set-1-1-freeze.md), [`PD-241-20260903`](./2026-09-03-rag-evaluation-schema-set-1-2-freeze.md), [`PD-799-20260918`](./2026-09-18-citation-authorization-production-authority-boundary.md), [`PD-833-20260919`](./2026-09-13-rag-answer-quality-metrics.md) |
| 관련 Issue / PR | #162, #163 (PR #849), #806 (PR #828), #853 (PR #857), PR #868, PR #883, PR #895 |

---

## 1. 배경과 문제 정의 (Context & Problem)

PR #849 병합으로 `#163` Protected Release Gate CLI 및 Loader(`ai_worker/tasks/evaluation/release_gate_loader.py`)는 보호된 평가 실행 시 `#162` canonical Guard authority binding이 제공되지 않으면 명시적으로 fail-closed(`_require_canonical_release_guard_authority()`, `STATE_COMBINATION_INVALID`, exit code 2)하도록 차단 경계가 고정되었다.

현재 `RagEvaluationRun` artifact에는 다음과 같은 런타임 Guard 필드가 존재한다.
- `candidate_bundle_id: StableId | None`
- `candidate_bundle_manifest_hash: Sha256Hex | None`
- `candidate_guard_decision_id: StableId | None`
- `candidate_guard_decision: CandidateGuardDecisionValue | None`
- `required_case_guard_coverage_manifest_hash: Sha256Hex | None`

그러나 이 필드들은 단지 Run artifact에 기록된 **미검증 주장(unverified claims)**일 뿐이며, 그 자체로 Release Candidate authority를 입증하는 증거(proof)가 될 수 없다. 실제 Release Candidate 승인을 위해서는 Run-level Candidate Guard, Case-level Request Guard, Candidate Bundle Identity, 그리고 FROZEN Dataset의 Required Case Coverage가 암호학적·구조적으로 상호 결속된 **정본 Evaluation Guard Evidence**가 필수적이다.

현재 develop의 구현 상태를 감사한 결과 다음과 같은 명확한 사실이 확인되었다:

1. **#806 REQUEST authority 자동 승격 불가**:
   - `RequestGuardRuntimeBindingObservation`은 단일 사용자 라이브 쿼리에 대한 `decision_stage = REQUEST` 수준의 per-request authority를 소유한다.
   - #806은 Evaluation Run ID, Candidate Bundle, FROZEN Dataset Manifest, Evaluated Partition, Required Case ID exact set, Candidate Manifest에 대한 바인딩을 소유하지 않는다.
   - 따라서 `#806 REQUEST/PASS`를 `#162 EVALUATION_REQUEST/PASS`로 자동 승격하는 것은 금지된다.
2. **EVALUATION Guard Canonical Producer 부재**:
   - Source Governance 어휘 집합에는 `EVALUATION_CANDIDATE` 및 `EVALUATION_REQUEST`가 존재하지만, 현재 실행 evaluator는 해당 오퍼레이션에 대해 `OPERATION_CONTEXT_NOT_MODELED`로 차단한다.
   - 따라서 현재 develop에는 `EVALUATION_CANDIDATE / PASS`를 실제 발행하는 canonical producer가 존재하지 않는다.
   - 이를 임의의 synthetic PASS로 날조(fabrication)하여 조기 통과시키는 것은 안전성 통제를 위반한다.

따라서 본 결정(Phase A1)은 코드 구현에 앞서 authority producer, immutable identity, runtime coordinate, required case exact coverage, 그리고 hash semantics를 먼저 정본 계약으로 동결(Contract Freeze Candidate)하는 것을 목적으로 한다.

---

## 2. 작업 단계 구분 (Phased Roadmap)

본 작업은 재작업과 안전성 누수를 방지하기 위해 다음 단계로 분리하여 진행한다:

```text
Phase A1: Contract Freeze / Authority Coordinate 확정 (Approved · PR #868)
        ↓
Phase A2: Canonical Guard Evidence Core (Merged · PR #883)
        ↓
Phase A3: Candidate Source Governance Authority Freeze 제안 (Merged · PR #895)
        ↓
[지금] Phase A3.5: Freshness·Scope Policy Authority Freeze (Proposed · Authority Resolution Required)
        ↓ 공식 승인자 송은영(@phina-io) Q2/Q3 policy content 및 freshness anchor/max_age/scope matrix 확정
Phase A4: Candidate Source Governance Bridge 구현
        ↓
Phase B: Paired Evaluation Artifact 구현 (ANS-BASE / ANS-RAG / ANS-FINAL 대조 및 95% CI)
        ↓
#163 Follow-up: Actual Release Gate integration (release_gate_loader.py 차단 해제 및 연결)
```

Phase A3.5 제안 시점의 authority 상태는 다음과 같다:
- `PURPOSE_MAPPING_AUTHORITY_RESOLVED` (은영님 확정 사항 정본 문서화 완료)
- `CANDIDATE_SOURCE_FRESHNESS_AUTHORITY_BLOCKED` (정책 규격 제안 상태, 내용/앵커/TTL 미확정)
- `CANDIDATE_SOURCE_SCOPE_AUTHORITY_BLOCKED` (정책 규격 제안 상태, 내용/허용 매트릭스 미확정)
- `CANDIDATE_SOURCE_AUTHORITY_BLOCKED` (Candidate PASS 불가)

`release_gate_loader.py`는 여전히 fail-closed(`ACTUAL_RELEASE_GATE_AUTHORIZATION_BLOCKED`) 상태를 유지한다.

---

## 3. Phase A1 동결 의미 (Frozen Semantics)

다음 항목은 기존 #162 정본 및 Freeze 계약(`PD-125`, `PD-216`, `PD-241`)에 의해 충분히 지지되므로 본 결정에서 동결한다:

### 3.1 Run-level Authority Binding 좌표
Run 수준 `EVALUATION_CANDIDATE / PASS` Evidence는 최소 다음 불변 좌표에 exact-bind되어야 한다:
- `evaluation_run_id`: 평가 실행 식별자 (`CanonicalUuid`)
- `candidate_bundle_id`: 대상 런타임 번들 식별자 (physical UUID의 canonical lowercase string)
- `candidate_bundle_manifest_hash`: 런타임 번들 매니페스트 SHA-256 (`Sha256Hex`)
- `dataset_code`: 대상 Dataset 코드 (`StableId`)
- `dataset_version`: 대상 Dataset 시맨틱 버전 (`SemanticVersion`)
- `dataset_manifest_sha256`: FROZEN Dataset 매니페스트 SHA-256 (`Sha256Hex`)
- `required_partitions`: 평가 대상 필수 파티션 튜플 (`tuple[Partition, ...]`, UTF-16 BE ascending canonical sorted)
- `required_case_set_hash`: 필수 케이스 집합의 canonical hash (`evaluation-required-case-set-v1`)
- `environment`: `RuntimeEnvironment.LOCAL` 고정
- `environment_revision_fence`: 평가 실행 시작 시점의 환경 리비전 동시성 펜스 (`RagRuntimeEnvironment.environment_revision >= 1`, positive integer)
- `governance_revision_ref`: 대상 번들에 결속된 거버넌스 리비전 식별자 (`RagRuntimeReleaseBundle.governance_revision_ref`, non-empty string, 환경과 exact match)
- `safety_epoch`: 평가 실행 시작 시점 환경 스냅샷의 안전성 에포크 (`RagRuntimeEnvironment.safety_epoch >= 1`, positive integer)
- `candidate_guard_decision_id`: Candidate Guard 결정 식별자 (`CanonicalUuid`, per-run UUID string)
- `candidate_guard_decision`: `"PASS"` 고정
- `candidate_guard_ref`: canonical Candidate Guard reference (`GenericImmutableArtifactRef`: `artifact_code="evaluation_candidate_guard"`, `version="1.0"`, `content_sha256="<candidate_guard_content_sha256>"`)
- `runtime_execution_manifest_id`: 대상 번들에 결속된 실행 매니페스트 ID (`RagRuntimeReleaseBundle.execution_manifest_id`, `CanonicalUuid`)
- `runtime_execution_manifest_hash`: 실행 매니페스트 해시 (`RagRuntimeExecutionManifest.manifest_hash`, `Sha256Hex`)

*권위 원천 및 동시성 펜스 원칙*:
- `safety_epoch`는 런타임 번들의 역사적 빌드 시점 provenance가 아니다. Evaluation Candidate Guard 시작 시점에 `bundle.environment_code`로 exact lookup한 `RagRuntimeEnvironment.safety_epoch`를 시간적 안전성 권위 스냅샷(Evaluation-start temporal safety authority snapshot)으로 관찰한다.
- `environment_revision`은 번들 시맨틱 식별자가 아니며, 평가 실행 중 환경 상태 변경을 방지하기 위한 동시성 펜스(`environment_revision_fence`)로 기록한다.

### 3.2 Case-level Authority Binding 좌표
각 Required Case에 대한 `EVALUATION_REQUEST / PASS` Evidence는 정확히 1건씩 존재해야 하며, 다음 좌표에 exact-bind되어야 한다:
- `case_id`: Dataset에 정의된 필수 케이스 ID (`StableId`)
- `evaluation_run_id`: 부모 Run과 동일한 평가 실행 ID (`CanonicalUuid`)
- `candidate_guard_ref`: 부모 Candidate Guard의 정본 불변 참조 (`GenericImmutableArtifactRef`: `artifact_code="evaluation_candidate_guard"`, `version="1.0"`, `content_sha256="<candidate_guard_content_sha256>"`)
- `candidate_bundle_id`: 부모 Run과 동일한 번들 ID (`CanonicalUuidString`)
- `candidate_bundle_manifest_hash`: 부모 Run과 동일한 번들 매니페스트 해시 (`Sha256Hex`)
- `runtime_execution_manifest_id`: 부모 Run과 동일한 실행 매니페스트 ID (`CanonicalUuid`)
- `runtime_execution_manifest_hash`: 부모 Run과 동일한 실행 매니페스트 해시 (`Sha256Hex`)
- `required_case_set_hash`: 부모 Run과 동일한 필수 케이스 집합 해시 (`Sha256Hex`)
- `environment`: `RuntimeEnvironment.LOCAL` 고정
- `environment_revision_fence`: 부모 Candidate Guard에 기록된 시작 펜스와 exact-match (`positive integer`)
- `governance_revision_ref`: 부모 Candidate Guard에 기록된 거버넌스 리비전과 exact-match (`str`)
- `safety_epoch`: 부모 Candidate Guard에 기록된 안전성 에포크 스냅샷과 exact-match (`positive integer`)
- `case_guard_decision_id`: 케이스 단위 Guard 결정 ID (`CanonicalUuid`, per-case UUID string)
- `decision`: `"PASS"` 고정
- `request_operation_code`: 승인된 canonical 오퍼레이션 코드 (`EVALUATION_REQUEST` 단일 대문자 고정)
- `request_scope_codes`: UTF-8 byte sorted unique non-blank 튜플
- `scope_manifest_hash`: `canonical_scope_manifest_hash(request_scope_codes)`와 exact 일치 (`Sha256Hex`)
- `case_guard_ref`: canonical Case Guard reference (`GenericImmutableArtifactRef`: `artifact_code="evaluation_request_guard"`, `version="1.0"`, `content_sha256="<case_guard_content_sha256>"`)

*단일 스냅샷 재사용 원칙*:
- 모든 Case Guard는 부모 Candidate Guard의 시작 스냅샷(`environment_revision_fence`, `governance_revision_ref`, `safety_epoch`)을 그대로 재사용하여 exact-bind한다.
- 케이스 실행 중 환경을 개별 재조회하여 서로 다른 epoch를 기록하는 것은 엄격히 금지된다 (`Candidate epoch == Case1 epoch == ... == CaseN epoch` 필수).

### 3.3 Candidate Guard 정본 사영 및 해시 레시피 (`evaluation-candidate-guard-v1`)
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

### 3.4 Case Guard 정본 사영 및 해시 레시피 (`evaluation-request-guard-v1`)
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

### 3.5 Required Case Set Canonical Hash Recipe
단순 케이스 ID 목록 해시가 아닌 Dataset authority와 결속된 사영 레시피를 동결한다:

```json
{
  "projection_version": "evaluation-required-case-set-v1",
  "dataset_manifest_sha256": "<exact validated FROZEN Dataset manifest hash>",
  "required_partitions": ["HOLDOUT", "SAFETY_REGRESSION"],
  "case_ids": ["case-001", "case-002", "..."]
}
```
- `case_ids`는 저장소의 canonical ordering 기준인 **UTF-16 BE** 바이트 순서(`sorted(case_ids, key=lambda s: s.encode("utf-16-be"))`)로 정렬한다.
- 해시 계산 전에 중복 케이스 ID가 발견되면 즉시 거부(`CASE_DUPLICATE`)한다.
- `required_case_set_hash = canonical_sha256(projection)`

### 3.6 Guard Coverage Hash Recipe (`evaluation-guard-coverage-v1`)
Required Case Coverage는 단순 개수(count) 일치 검사가 아니며, 다음 preimage를 결속한다:

```json
{
  "projection_version": "evaluation-guard-coverage-v1",
  "evaluation_run_id": "<canonical_uuid>",
  "candidate_bundle_id": "<canonical_lowercase_uuid>",
  "candidate_bundle_manifest_hash": "<sha256_hex>",
  "runtime_execution_manifest_id": "<canonical_uuid>",
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
      "case_id": "case-001",
      "case_guard_decision_id": "<canonical_uuid>",
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
- `guard_coverage_manifest_hash = canonical_sha256(preimage)`
- **해시 레시피 정본 동결**:
  - `evaluation-guard-coverage-v1`의 모든 권위 좌표와 Envelope 구조가 확정되었다.
  - Candidate Guard ref와 Case Guard ref는 self-asserted 값이 아니라 검증기가 사영에서 독립 재계산한 `content_sha256`만을 사용한다.
  - `environment_revision_fence`는 동시성 펜스로서 바인딩된다.
- **불변 속성**:
  - 동일 케이스 집합이라도 번들이 다르면 다른 해시가 생성된다.
  - 케이스 바인딩 순서가 뒤바뀌거나 케이스가 누락되면 다른 해시가 생성되거나 검증에서 거부된다.

### 3.7 커버리지 계산 순서 및 Run Finalization Fence
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

*Run Finalization Concurrency Fence 순서*:
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

### 3.8 Exact-Set Coverage Semantics 및 case_guard_bindings 정본 정렬 규칙
`case_guard_bindings`의 검증은 단순 count 일치가 아닌 **exact-set** 일치여야 하며, 정렬 규칙을 강제한다:
- **Canonical Ordering 동결**:
  모든 Case Guard 검증 후, 중복 `case_id`를 선거부(`CASE_DUPLICATE`)하고 다음 순서로 정렬한다:
  ```python
  sorted(
      case_guard_bindings,
      key=lambda binding: binding.case_id.encode("utf-16-be"),
  )
  ```
  Canonical key는 `case_id` 단 하나이며, exact-set에서 unique하므로 보조 키는 불필요하다.
- **Wire Artifact Order 강제**:
  저장되는 wire artifact의 `case_guard_bindings` 튜플 순서는 반드시 canonical sorted order와 동일해야 한다 (`actual tuple order == canonical sorted order`). 순서 반전이나 임의 순서는 non-canonical artifact로 판정되어 즉시 fail-closed(`BASELINE_ARTIFACT_INVALID`) 처리된다.
- **위반 조건별 실패 규격**:
  - `missing case` → reject (`BASELINE_ARTIFACT_INVALID`)
  - `duplicate case` → reject (`CASE_DUPLICATE`)
  - `extra case` (Dataset에 없는 케이스) → reject (`BASELINE_ARTIFACT_INVALID`)
  - `wrong run_id` → reject (`HASH_MISMATCH`)
  - `wrong bundle_id` 또는 `wrong bundle_manifest_hash` → reject (`HASH_MISMATCH`)
  - `wrong execution manifest` / `wrong revision` / `wrong epoch` → reject (`HASH_MISMATCH`)
  - `wrong scope hash` → reject (`HASH_MISMATCH`)
  - `wrong Guard ref` → reject (`HASH_MISMATCH`)
  - `non-canonical wire order` → reject (`BASELINE_ARTIFACT_INVALID`)
  - `non-PASS decision` → reject (`BASELINE_ARTIFACT_INVALID`)
  - 단순 count 일치만으로 성공 처리하는 것은 엄격히 금지된다.

### 3.9 Runtime Bundle ID Wire Rule
- Runtime의 물리 번들 ID는 PostgreSQL DB의 `UUID`이다.
- 반면 `RagEvaluationRun.candidate_bundle_id`는 현재 스키마상 `StableId`이다.
- 본 Phase A1에서는 `RagEvaluationRun` 스키마를 변경하지 않으며, canonical lowercase UUID 문자열(`str(runtime_bundle_uuid)`)을 wire 포맷으로 동결한다.
- 향후 A2 검증기는 `str(runtime_bundle_uuid) == run.candidate_bundle_id`를 exact-match하고 non-canonical 포맷은 fail-closed 처리한다.

### 3.10 Candidate Guard Decision ID 물리 타입 확정
- `RagEvaluationRun.candidate_guard_decision_id`의 wire 포맷은 `CanonicalUuid` 문자열로 확정한다.
- Evaluator는 run-scoped의 고유/결정론적 `UUID`를 발행하여 감사 추적성(audit traceability)을 보장하며, 비정규 UUID 문자열은 검증기에서 fail-closed 처리한다.

### 3.11 #806 Ref의 Generic Immutable Identity 보존
- `#806`의 `RequestGuardRuntimeBindingRef`는 `(artifact_code, version, content_sha256)` 3요소 형태다.
- Evaluation의 `ImmutableReference`는 `(id, version, hash)` 형태다.
- 단순 필드명 변경으로 권위를 왜곡하지 않으며, Phase A1 계약에서는 generic immutable artifact identity로 `(artifact_code, version, content_sha256)`를 기술한다.

### 3.12 Privacy Boundary
- 원문 환자 질문(query text), 답변(answer text), 환자 식별자(patient ID), Provider 원문 응답 payload, 시스템 자격증명(credential)은 Evaluation Guard Evidence artifact, 로그, 오류 메시지에 절대 포함하지 않는다.

### 3.13 #163 Consumer Boundary
- `release_gate_loader.py`의 `_require_canonical_release_guard_authority()`는 본 PR에서 수정하지 않는다.
- Phase A2(Guard evidence 구현)와 Phase B(Paired comparison 구현)가 완료된 후, 별도 `#163` 후속 PR에서 loader를 실제 연결한다.

---

## 4. 동결된 핵심 결정 사항 (Frozen Authority Decisions: Q1~Q4 Resolved)

본 결정에서는 `origin/develop` 감사 및 도메인 책임 원칙에 기반하여 4개 핵심 권위 결정을 다음과 같이 확정·동결한다:

### Q1. EVALUATION_CANDIDATE Canonical Producer 확정
- **결정**: Protected Local Evaluation Runner 소유의 `EvaluationCandidateGuardEvaluator`(`ai_worker/tasks/evaluation/`)가 공식 권위 발행자(authority producer)를 담당한다 (Phase A2 구현).
- **근거 및 원칙**:
  - `ai_worker/tasks/rag/source_governance.py`는 Source/Worker 도메인에 속하며, 현재 `EVALUATION_CANDIDATE` 오퍼레이션에 대해 `SyntheticGovernanceReason.OPERATION_CONTEXT_NOT_MODELED`로 엄격히 fail-closed 차단하고 있다.
  - `source_governance.py`를 Evaluation 런타임 검증용으로 억지 확장하는 것은 도메인 쓰기 경계를 오염시키므로, 기존 `source_governance.py`는 변경 없이 차단 상태를 유지한다.
  - Evaluation 도메인의 전용 Evaluator는 이미 영속화된 권위(BUILDING 상태의 `RagRuntimeReleaseBundle`, 번들 매니페스트, `RagRuntimeExecutionManifest`, 번들에 핀된 `governance_revision_ref`, 환경의 `safety_epoch`, FROZEN Dataset 권위)를 exact-read 및 재검증한 후 Evaluation-specific `EVALUATION_CANDIDATE / PASS` 권위를 공식 발행한다.
- **권위 식별자**:
  - `candidate_guard_decision_id`: `CanonicalUuid`
  - `candidate_guard_ref`: `GenericImmutableArtifactRef(artifact_code="evaluation_candidate_guard", version="1.0", content_sha256="...")`
  - Projection: `evaluation-candidate-guard-v1`
- **기각된 대안**:
  - *대안 1 (기존 source_governance.py 확장)*: Source Worker와 Evaluation Runner 간 결합도를 증가시키고 라이브 질의 경로에 사이드이펙트 위험이 있어 기각.
  - *대안 2 (synthetic PASS 임의 생성)*: 실제 권위 검증 없는 조기 통과는 의료 안전성 원칙을 위반하므로 기각.

### Q2. EVALUATION_REQUEST Authority Source 확정
- **결정**: Evaluation 도메인 전용 `EvaluationRequestGuardEvaluator`가 케이스 단위 Guard 권위를 독립 발행하며, 그 결과는 Run Result Bundle의 독립 평가 아티팩트로 저장된다. 라이브 DB 테이블(`request_guard_runtime_binding`)에는 일체 쓰거나 조회하지 않는다.
- **근거 및 원칙**:
  - #806의 `request_guard_runtime_binding` 테이블 및 권위는 라이브 사용자 질의 시점(`decision_stage = REQUEST`)의 per-request 권위이다.
  - #806 레코드에는 `evaluation_run_id`, Dataset manifest, Case ID, Candidate 번들에 대한 exact-set 바인딩이 전혀 존재하지 않으므로, #806 레코드를 #162 권위로 자동 승격하거나 라이브 DB를 평가 권위 원천으로 삼을 수 없다.
  - #806에서는 오직 **순수 시맨틱**(UTF-8 바이트 정렬 스코프 코드, `canonical_scope_manifest_hash`, `(artifact_code, version, content_sha256)` 불변 참조 구조, fail-closed 검증 패턴, no latest/current fallback)만을 엄격히 재사용한다.
- **권위 식별자**:
  - `case_guard_decision_id`: `CanonicalUuid` (라이브 요청과 명확히 구분하기 위해 `request_guard_decision_id`에서 리네임)
  - `case_guard_ref`: `GenericImmutableArtifactRef(artifact_code="evaluation_request_guard", version="1.0", content_sha256="...")`
  - Projection: `evaluation-request-guard-v1`
  - 허용 오퍼레이션 코드: `EVALUATION_REQUEST` 단일 정규 대문자 고정
- **기각된 대안**:
  - *대안 A (#806 DB 테이블에 평가 레코드 직접 쓰기)*: 비임상 테스트 실행 데이터가 운영 DB를 오염시키고 신규 DB 마이그레이션이 필요하므로 기각.
  - *대안 B (#806 기존 라이브 레코드 승격)*: 평가 컨텍스트 및 exact-set 결속 부재로 인한 보안/안전성 위반으로 기각.

### Q3. Schema Set Membership 확정
- **결정**: `rag-eval.evaluation-guard-evidence@1.0.0`은 **독립 버전화 권위 아티팩트(Standalone Versioned Authority Artifact)**로 유지한다.
- **근거 및 원칙**:
  - 기존 Evaluation Schema Set 1.0~1.5(`ai_worker/tasks/evaluation/schema_registry.py`, `schema_exports.py`)는 이미 동결된 불변 계약이다.
  - 신규 아티팩트를 위해 `Schema Set 1.6`을 선언하거나 레지스트리를 수정하지 않는다.
  - `guard_evidence.py`의 순수 검증기(`validate_evaluation_guard_evidence`)를 통해 독립적으로 검증을 수행함으로써 불필요한 스키마 레지스트리 변경 및 PR 영향 범위를 최소화한다.
- **기각된 대안**:
  - *대안 2 (Schema Set 1.6 선언 및 레지스트리 수정)*: 기존 동결 계약의 불필요한 개정과 export 재생성이 요구되므로 기각.

### Q4. BUILDING Candidate Runtime Coordinates 및 Safety Epoch / Concurrency Fence 확정
- **결정**: Candidate 번들의 정본 불변 런타임 좌표는 다음 축으로 확정하며, `safety_epoch`의 의미를 바로잡고 `environment_revision_fence`를 동시성 펜스로 동결한다.
- **핵심 원칙**:
  - **Safety Epoch는 Runtime Bundle build provenance가 아니다.**
  - `RagRuntimeReleaseBundle`에는 `safety_epoch` 컬럼이나 `environment` FK가 없으며, Runtime Bundle 빌드 경로는 Environment row를 읽어 epoch를 핀하지 않는다. 따라서 과거 BUILDING 번들의 "발행 시점 epoch"를 복원하는 것은 물리적으로 불가능하며, 이를 위해 DB 마이그레이션이나 백필을 추가하지 않는다.
  - 대신, `EVALUATION_CANDIDATE` Guard 평가 시작 시점에 `bundle.environment_code`로 exact lookup한 `RagRuntimeEnvironment` row에서 `environment_revision`, `governance_revision_ref`, `safety_epoch`를 하나의 **evaluation-start snapshot**으로 관찰한다.
  - `environment_revision`은 번들 시맨틱 식별자가 아니라 **Evaluation execution concurrency fence**(`environment_revision_fence`, positive integer)로 동작한다.
  - 모든 Required Case 실행과 Run finalization 동안 동일 snapshot의 `environment_revision_fence`, `governance_revision_ref`, `safety_epoch`가 유지되어야 한다 (`Candidate epoch == Case1 epoch == ... == CaseN epoch` 필수).
  - Run finalization 직전 Environment를 exact re-read하여 `end.environment_revision == candidate.environment_revision_fence`, `end.governance_revision_ref == candidate.governance_revision_ref`, `end.safety_epoch == candidate.safety_epoch` 중 하나라도 달라졌으면 해당 Evaluation authority는 즉시 **INVALID** 처리된다 (decision_status = null, coverage authority 사용 금지).
- **확정 좌표 세부**:
  1. **Execution Manifest**:
     - `RagRuntimeReleaseBundle.execution_manifest_id: CanonicalUuid`
     - `RagRuntimeExecutionManifest.manifest_hash: Sha256Hex`
     - Evidence 좌표명: `runtime_execution_manifest_id`, `runtime_execution_manifest_hash`
  2. **Governance Revision**:
     - `RagRuntimeReleaseBundle.governance_revision_ref: str` (번들 빌드 시점에 핀된 거버넌스 리비전 식별자, `bundle.governance_revision_ref == env.governance_revision_ref` exact match 검증 필수, `None` 불가)
     - Evidence 좌표명: `governance_revision_ref`
  3. **Safety Epoch (Evaluation Start Snapshot)**:
     - `RagRuntimeEnvironment.safety_epoch: int >= 1` (Candidate Guard 시작 시점에 관찰된 시간적 안전성 권위 스냅샷, 전 Case exact 동일값 바인딩)
     - Evidence 좌표명: `safety_epoch`
  4. **Environment Concurrency Fence**:
     - `RagRuntimeEnvironment.environment_revision: int >= 1` (평가 시작 시점의 동시성 펜스)
     - Evidence 좌표명: `environment_revision_fence`
  5. **기존 번들 및 마이그레이션 불필요**:
     - 기존 BUILDING 번들은 평가 시작 시점의 Environment 스냅샷을 새로 관찰하며, 과거 빌드 시점 epoch를 날조(fabricate)하지 않는다. DB 컬럼 추가나 forward migration, silent recompute는 일체 없다.
- **Active 번들 포인터 엄격 금지**:
  - 평가 대상은 `BUILDING` 상태의 후보 번들이므로, `RagRuntimeEnvironment.active_bundle_id` 및 `active_bundle_manifest_hash`를 평가 대상으로 참조하는 것은 엄격히 금지된다.

---

## 5. Phase A2 공유 파라미터 테스트 계획 (Shared Parameter Test Plan)

리뷰어 요청에 따라 Phase A2 구현 시 필수로 만족해야 하는 테스트 매트릭스를 계약에 기록한다 (실제 구현은 Phase A2에서 진행):

### 5.1 Candidate Projection 필드 변조 매트릭스 (Field Mutation Matrix)
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

### 5.2 Case Projection 필드 변조 매트릭스 (Field Mutation Matrix)
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

### 5.3 필수 네거티브 테스트 (Mandatory Negative Tests)
- Candidate `operation != "EVALUATION_CANDIDATE"` → reject
- Candidate `decision != "PASS"` → reject
- Case `operation != "EVALUATION_REQUEST"` → reject
- Case `decision != "PASS"` → reject
- Lowercase / mixed-case operation alias (예: `"evaluation_request"`) → reject
- 변조된(tampered) `candidate_guard_ref` → reject
- 변조된(tampered) `case_guard_ref` → reject
- `request_scope_codes`가 변경되었으나 과거 `scope_manifest_hash` 유지 → reject
- Case에 부모 Run과 다른 Candidate Guard ref가 바인딩됨 → reject

### 5.4 Case-set 테스트 매트릭스 (Case-set Test Matrix)
- 필수 케이스 1개 누락 (missing case) → reject (`BASELINE_ARTIFACT_INVALID`)
- 동일 케이스 중복 바인딩 (duplicate case) → reject (`CASE_DUPLICATE`)
- Dataset에 없는 초과 케이스 (extra case) → reject (`BASELINE_ARTIFACT_INVALID`)
- 케이스 수는 일치하나 잘못된 케이스 ID 포함 → reject
- `case_guard_bindings` 순서 반전 (reversed wire ordering) → non-canonical artifact로 reject
- 단 1개 케이스의 Guard ref 변경 → coverage hash mismatch
- 단 1개 케이스의 decision ID 변경 → coverage hash mismatch

### 5.5 Canonical Ordering 테스트 (Canonical Ordering Tests)
- Canonical 정렬(`case_id.encode("utf-16-be")`)된 바인딩 → 정상 수락
- 역순 또는 임의 순서의 wire 바인딩 → reject
- 임의 입력 순서로부터 빌더 실행 시 항상 canonical order로 출력 생성
- UTF-16 BE 경계 문자열 fixture 검증으로 일관된 결정론적 정렬 보장 (새 JCS 라이브러리 도입 금지, 저장소 정본 재사용)

### 5.6 Safety Epoch 시간적 상태 전이 테스트 (Temporal Transition Tests)
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

---

## 6. Phase A1 완료 검증

본 PR은 문서 전용(docs-only) 변경이므로 불필요한 전체 구현 테스트를 실행하지 않으며 다음 검증을 수행한다:
1. `git diff --check`: 포맷 및 공백 무결성 확인
2. `git grep -n "evaluation-guard-evidence" docs`: 문서 간 상호 참조 링크 정합성 확인
3. `git grep -n "EVALUATION_CANDIDATE" docs/contracts docs/governance` 및 `git grep -n "EVALUATION_REQUEST" docs/contracts docs/governance`: 용어 및 식별자 일관성 확인
4. `uv run ruff check .` 및 `uv run ruff format . --check`: 린트/포맷 정합성 확인

---

## 7. Phase A1·A2 완료 현황 및 전환

1. **Phase A1 (Contract Freeze)**: PR #868에서 단일 책임 리뷰어(`@hazelnutflavoured`) 승인 및 머지 완료. Guard ref 정본 사영, 해시 레시피, `case_guard_bindings` canonical ordering, Safety Epoch 시작 스냅샷 및 Concurrency Fence 규격 동결.
2. **Phase A2 (Guard Evidence Core)**: PR #883에서 Candidate Guard evidence core, 해시/envelope 재계산 검증기, `SqlAlchemyEvaluationGuardAuthorityReader` 및 PostgreSQL 통합 테스트 구현 완료 및 머지(`a19ec7e7`).
3. **Phase A3 전환**: Candidate 번들 런타임 소스 구성원의 Source Governance 판정을 위한 3개 핵심 시맨틱 권위가 미확정 상태이므로, 코드 구현에 앞서 본 문서 Section 8에서 정본 의미를 동결 제안(Proposed · Authority Resolution Required)한다.

---

## 8. Candidate Source Governance 권위 동결 (Phase A3 Proposed · Authority Resolution Required)

PR #883 병합으로 Candidate Guard의 암호학적/구조적 Evidence Core(`evaluation-candidate-guard-v1`, `evaluation-request-guard-v1`, `evaluation-guard-coverage-v1`, Concurrency Fence, `SqlAlchemyEvaluationGuardAuthorityReader`)는 준비 완료되었다. 그러나 Candidate 번들의 런타임 소스 구성원에 대한 실제 Source Governance 판정(`EvaluationCandidateGuardEvaluator.evaluate_candidate_start`)은 아래 3개 시맨틱 권위가 미확정 상태이므로, fail-closed 원칙에 따라 `CANDIDATE_SOURCE_AUTHORITY_BLOCKED` 상태를 유지하고 있다.

본 절은 코드를 추가 작성하기 전에 도메인 소유자 간 합의를 통해 3개 권위의 정본 의미를 동결(Proposed Freeze)하기 위한 규격이다. 본 단계에서는 새 Source Governance 엔진 신설, 새 DB 테이블/마이그레이션 생성, 임의 매핑이나 synthetic fallback을 일체 배제한다.

### 8.1 재사용 확정된 기존 권위 (Unchanged Resolved Authorities — 재논의 제외)
다음 권위 축은 이미 기존 정본 모델 및 PR #883 어댑터에 의해 완전히 지지되므로 본 동결에서 재논의하지 않고 그대로 재사용한다:

1. **Runtime 번들 구조적 무결성**:
   - Source: `ai_worker/adapters/sqlalchemy_evaluation_guard_authority.py` (`SqlAlchemyEvaluationGuardAuthorityReader.read_candidate_start`)
   - Recomputation: `canonical_runtime_bundle_manifest_hash` 재계산 일치, Candidate Index 필수 바인딩, Environment 3-축 펜스
2. **Source 생명주기**:
   - Source: `backend/app/models/rag_source.py` (`RagSource.lifecycle_status`)
   - 기준: `RagSource.lifecycle_status == RagSourceLifecycleStatus.ACTIVE`
3. **Endpoint 적격성**:
   - Source: `backend/app/models/rag_source.py` (`RagSourceEndpoint`)
   - 기준: `lifecycle_status == RagSourceEndpointLifecycleStatus.VERIFIED`, `runtime_status == RagSourceUsageStatus.ENABLED`, `acquisition_status == RagSourceApprovalStatus.APPROVED`
4. **Operation 적격성**:
   - Source: `backend/app/models/rag_source.py` (`RagSourceOperation`)
   - 기준: `runtime_status == RagSourceUsageStatus.ENABLED`, `acquisition_status == RagSourceApprovalStatus.APPROVED`
5. **Snapshot 최신 검증 상태**:
   - Source: `backend/app/models/rag_source.py` (`RagSourceSnapshot.verification_status`)
   - 기준: `verification_status == RagSnapshotVerificationStatus.CURRENT`
6. **Snapshot Provenance 영수증 무결성**:
   - Source: `ai_worker/adapters/sqlalchemy_source_snapshot_repository.py` (`SqlAlchemySourceSnapshotRepository.get_snapshot_receipt`) 및 `ai_worker/tasks/rag/source_ingestion/snapshot_lifecycle.py` (`SnapshotProvenanceReceipt.validate_provenance()`)
   - 기준: 번들 핀(`source_snapshot_id`, `source_version`, `canonical_checksum`)과의 exact match 및 영수증 provenance 검증 통과
7. **Snapshot 발행 승인 (Publication Approval)**:
   - Source: `SqlAlchemySourceSnapshotRepository.get_snapshot_receipt`
   - 기준: `SnapshotProvenanceReceipt.publication_verification_id is not None` (`rag_source_snapshot_verification`의 `check_name == "SNAPSHOT_PUBLICATION_APPROVAL"`, `verification_result == "PASSED"`, `verified_by` nonblank)
8. **Snapshot 적격성 커널**:
   - Source: `ai_worker/tasks/rag/source_ingestion/snapshot_lifecycle.py` (`evaluate_snapshot_use_eligibility`)
   - 기준: `decision == SnapshotUseDecision.USABLE`
9. **Source Use Approval 유효성 및 철회 여부**:
   - Source: `ai_worker/adapters/sqlalchemy_source_use_approval.py` (`SqlAlchemySourceUseApprovalReader.read_usable_exact`) 및 `rag_runtime/source_use_approval.py`
   - 기준: `valid_from <= evaluation_time < expires_at`, `revoked_at is None`, exact 6-field equality lookup

---

### 8.2 Q1. Purpose Mapping Authority (RESOLVED)

#### 1) 도메인 소유자(@phina-io) 공식 확정 사항 정본 반영
런타임 번들 소스 멤버 테이블(`rag_runtime_bundle_source`)의 `RuntimeBundleMemberPurpose` 6종과 소스 사용 승인 테이블(`rag_source_use_approval`)의 `SourceUsePurpose` 5종 간의 정본 매핑은 Backend/Data 책임자 송은영(@phina-io)님의 공식 확정(Formal Resolution)에 따라 다음과 같이 결정되었다:

| RuntimeBundleMemberPurpose | SourceUsePurpose | Cardinality | 확정 상태 |
| --- | --- | --- | --- |
| `CATALOG` | `PRODUCT_IDENTIFICATION` | 1:1 | RESOLVED |
| `KNOWLEDGE` | `RETRIEVAL` | 1:1 | RESOLVED |
| `CANDIDATE_INDEX_INPUT` | `PRODUCT_IDENTIFICATION` | 1:1 | RESOLVED |
| `RULE` | `RULE_DERIVATION` | 1:1 | RESOLVED |
| `GUIDELINE` | `BLOCKED_BY_UNMODELED_PURPOSE` | N/A | RESOLVED (차단) |
| `SAFETY_POLICY` | `SAFETY_ROUTING` | 1:1 | RESOLVED |

#### 2) 정본 경계 원칙 (Confirmed Boundary Rules)
1. **`PATIENT_CITATION` 경계**:
   - `PATIENT_CITATION`은 런타임 번들 소스 멤버(`rag_runtime_bundle_source`)의 `source_purpose` 매핑에 일체 사용되지 않는다.
   - 이는 Citation approval pin 및 환자 라이브 쿼리 인용 승인(`SourceUseApproval` identity) 전용 목적이다.
2. **`GUIDELINE` 멤버 존재 시 즉시 Fail-Closed**:
   - 번들에 `source_purpose == GUIDELINE`인 소스 멤버가 포함되어 있을 경우, 대응하는 `SourceUsePurpose`가 모델링되어 있지 않으므로 `CANDIDATE_SOURCE_PURPOSE_MAPPING_BLOCKED`로 판정된다.
   - 따라서 번들에 `GUIDELINE` 멤버가 존재하는 한 `EVALUATION_CANDIDATE / PASS`는 절대 발행되지 않는다 (Fail-closed).
3. **재논의 금지**:
   - 본 매핑은 formal resolution으로 확정되었으므로 새 매핑을 추가하거나 변경하지 않는다 (`PURPOSE_MAPPING_AUTHORITY_RESOLVED`).

---

### 8.3 Q2. Freshness Authority (Proposed · Resolution Required)

#### 1) 기존 사실 및 불변 식별자
- **Identity source**: `rag_runtime_bundle_source.freshness_policy_hash` (64-char SHA-256 hex string).
- **Policy content source**: 현재 develop에는 승인된 정책 내용 및 evaluator가 부재함 (`CANDIDATE_SOURCE_FRESHNESS_AUTHORITY_BLOCKED`).
- **엄격 금지된 바로가기 (Prohibited Shortcuts)**:
  - `verification_status == CURRENT` 판정을 Freshness 통과로 간주하는 것: **엄격 금지** (`PD-362-20260909` 라인 135-136에 명시된 바와 같이, `CURRENT`는 파이프라인 수집 시점의 최신성 검증일 뿐이며, 특정 Freshness Policy와 평가 시점 기준의 만료 여부 판정이 아님).
  - `freshness_policy_hash` 컬럼 존재만으로 PASS 간주: **엄격 금지** (정책 해시의 존재는 정책 내용의 충족을 뜻하지 않음).
  - 번들 빌드 당시 freshness 판정 재사용: **엄격 금지** (Candidate 평가는 반드시 `evaluation_time` 기준 current-state 재검증이어야 함).
  - 기본값 `freshness_eligible = True`: **엄격 금지**.
  - 기존 `24h` Evidence Assessment Validity Policy TTL 복사: **엄격 금지** (도메인이 다른 별개 정책이므로 Source snapshot freshness TTL로 전용 불가).

#### 2) Freshness 정책에서 이번에 확정할 4가지 축
1. **Proposed canonical shape**:
   최소 제안 스키마 (`evaluation-source-freshness-policy-v1`):
   ```json
   {
     "projection_version": "evaluation-source-freshness-policy-v1",
     "policy_code": "<approved-code>",
     "policy_version": "1",
     "freshness_anchor": "<approved-anchor>",
     "max_age_seconds": "<approved-positive-int>"
   }
   ```
   - `<approved-code>`, `<approved-anchor>`, `<approved-positive-int>` 값은 임의로 채우지 않으며 `@phina-io`의 공식 승인을 받아야 한다.

2. **Freshness Clock Anchor 후보 감사 (Physical Ownership Audit)**:
   DB 모델(`backend/app/models/rag_source.py`) 및 Alembic 마이그레이션/트리거 감사 결과:

   | 후보 필드 | Physical Owner / Model / Table | Nullability | Write/Seal 시점 | Mutation 가능 여부 | Verification Lifecycle 의미 | Evaluation Freshness Anchor 적합성 |
   | --- | --- | --- | --- | --- | --- | --- |
   | `collected_at` | `RagSourceSnapshot.collected_at`<br>(`rag_source_snapshot`) | NOT NULL | 스냅샷 생성/적재 시점 (`RagSourceCatalogRepository.create_snapshot`) | **불변 (Immutable)**<br>DB 트리거 `prevent_rag_source_snapshot_mutation`에 의해 수정 시 예외 발생 | 외부 엔드포인트로부터 원시 데이터를 물리적으로 수집한 기준 시각. `PENDING`부터 전 생명주기 불변 보존 | **높음**<br>데이터 원천의 실제 물리적 수집 경과 시간을 측정하기에 가장 직접적이며 결측/변조 불가 |
   | `verified_at` | `RagSourceSnapshot.verified_at`<br>(`rag_source_snapshot`), `RagSourceSnapshotVerification.verified_at` | Nullable (`PENDING` 시 NULL) | 스냅샷 검증 통과 시점 (`transition_rag_source_snapshot`) | **설정 후 불변**<br>`guard_rag_snapshot_state_write` 및 DB 함수에 의해 사후 수정 차단 | 스냅샷 구조/스키마/체크섬 검증이 완료되어 Seal된 시각 | **중간**<br>플랫폼 검증 통과 시각을 기준으로 경과 시간을 측정하고자 할 때 유효. `PENDING` 중에는 NULL |
   | `effective_at` | `RagSourceSnapshot.effective_at`<br>(`rag_source_snapshot`) | Nullable (`CURRENT` 전이 시 필수) | `CURRENT` 상태 전이 시점 (`transition_rag_source_snapshot`) | **조건부 갱신 가능**<br>`CURRENT` 재선택 전이 시 `COALESCE(p_effective_at, effective_at)` 처리 | 해당 스냅샷이 운영용 최신 검증본(`CURRENT`)으로 승격/선택된 시각 | **낮음/주의**<br>데이터 자체의 신선도가 아닌 `CURRENT` 선택 시점이며, 재전이 시 갱신될 가능성이 존재함 |

   *감사 결론*:
   - 세 후보의 physical ownership과 생명주기 의미가 상이하므로 엔지니어링 임의 선택을 금지하고, `@phina-io`에게 위 감사 표를 제시하여 단 1개의 anchor를 공식 선택받는다.

3. **Max Age / Deadline Rule (TTL)**:
   - `max_age_seconds: OWNER_RESOLUTION_REQUIRED`
   - 임의 TTL(24h, 7d, 30d, 무기한 등) 추정 금지.
   - Pure evaluator 판정 공식:
     `(snapshot freshness anchor, evaluation_time, approved freshness policy) -> freshness_eligible: bool`
     수식: `0 <= (evaluation_time - anchor_time).total_seconds() <= max_age_seconds`
   - Evaluator 경계: Snapshot CURRENT, publication approval, Source Use Approval, revocation, scope 등 타 권위와 중복되지 않으며 오직 시간 경과에 따른 적격성만 소유한다.

4. **Canonical Hash Recipe**:
   - `freshness_policy_hash = canonical_sha256(evaluation-source-freshness-policy-v1 projection)`
   - Phase A4 순서:
     `policy content` → `canonical hash recompute` → `Bundle member freshness_policy_hash exact-match` → `freshness evaluation`
   - Fail-closed: 정책 부재, 해시 불일치, anchor NULL/invalid, evaluation_time invalid, age > max_age_seconds 시 즉시 `freshness_eligible = False` (예외로 PASS 불가).

---

### 8.4 Q3. Scope Authority (Proposed · Resolution Required)

#### 1) 기존 사실 및 불변 식별자
- **Identity source**: `rag_runtime_bundle_source.scope_policy_hash` (64-char SHA-256 hex string).
- **Policy content source**: 현재 develop에는 승인된 정책 내용 및 evaluator가 부재함 (`CANDIDATE_SOURCE_SCOPE_AUTHORITY_BLOCKED`).
- **Operation**: `EVALUATION_CANDIDATE` 단일 오퍼레이션으로 한정.
- **#806 혼동 금지**:
  - #806의 per-request `request_scope_codes`를 Candidate 소스 스코프로 자동 매핑하거나 재사용하는 것은 엄격히 금지된다.
  - 기본값 `scope_allowed = True` 금지.
- **Q1 Purpose Mapping과의 명확한 분리**:
  - Purpose Mapping(`KNOWLEDGE -> RETRIEVAL` 등)은 `SourceUseApproval` 조회 목적을 결정하는 1:1 매핑이다.
  - Scope Policy는 `EVALUATION_CANDIDATE` 오퍼레이션 컨텍스트에서 특정 번들 멤버의 사용이 허용되는지(`ALLOWED | BLOCKED`)를 결정하는 별개의 권위이다. 둘을 단일 딕셔너리로 합치지 않는다.

#### 2) 정본 Scope 경계 유지 및 Proposed Canonical Shape
정본 계약의 Scope 판정 경계는 다음과 같다:
`(scope_policy_hash, EVALUATION_CANDIDATE, member_context) -> ALLOWED | BLOCKED`

본 freeze 제안에서는 `member_context`를 `RuntimeBundleMemberPurpose` 하나로 선제 축소하지 않고 유지한다.
다만, Phase A4 최소 설계를 위해 `member_context`의 최소 semantic dimension이 `RuntimeBundleMemberPurpose`만으로 충분한지를 `@phina-io`의 최종 resolution 항목으로 상정한다.

Proposed canonical shape (`evaluation-source-scope-policy-v1`):
```json
{
  "projection_version": "evaluation-source-scope-policy-v1",
  "policy_code": "<approved-code>",
  "policy_version": "1",
  "operation": "EVALUATION_CANDIDATE",
  "allowed_bundle_member_purposes": [
    "<approved-purpose>"
  ]
}
```
*주의*: 위 스키마는 `@phina-io`가 purpose-only context를 공식 승인한 뒤에만 final canonical shape로 동결된다.

#### 3) EVALUATION_CANDIDATE Scope Allowed Matrix (승인 대기)
각 번들 멤버 목적에 대한 허용 여부는 `@phina-io`의 명시적 승인을 받아야 한다:

| RuntimeBundleMemberPurpose | EVALUATION_CANDIDATE 허용 여부 | 상태 |
| --- | --- | --- |
| `CATALOG` | ALLOWED \| BLOCKED | Proposed · Resolution Required |
| `KNOWLEDGE` | ALLOWED \| BLOCKED | Proposed · Resolution Required |
| `CANDIDATE_INDEX_INPUT` | ALLOWED \| BLOCKED | Proposed · Resolution Required |
| `RULE` | ALLOWED \| BLOCKED | Proposed · Resolution Required |
| `GUIDELINE` | ALLOWED \| BLOCKED (참고: scope가 ALLOWED더라도 Q1에 의해 Candidate PASS 불가) | Proposed · Resolution Required |
| `SAFETY_POLICY` | ALLOWED \| BLOCKED | Proposed · Resolution Required |

#### 4) Scope Canonical Hash Recipe
- `scope_policy_hash = canonical_sha256(evaluation-source-scope-policy-v1 projection)`
- Phase A4 순서:
  `policy content` → `recompute scope_policy_hash` → `Bundle pin exact-match` → `scope evaluator`
- Fail-closed: 알 수 없는 해시, 해시 불일치, 목적 미허용 시 즉시 `BLOCKED`.

---

### 8.5 Policy Content Source & Exact Lookup Contract (No New DB Priority)

#### 1) Code-owned Immutable Policy Registry 우선 제안
- 새 정책 DB 테이블이나 마이그레이션을 신설하지 않는다.
- 정책 수가 극소수이고 운영 중 동적 편집 요구가 없으며, 번들에 이미 해시가 핀되어 있으므로 **versioned, immutable, code-owned pure policy + content-addressed canonical hash** 방식의 code-owned policy registry를 우선 제안한다.
- 단, 본 PR은 문서 전용(docs-only)이므로 Python 구현은 포함하지 않으며, 승인 후 Phase A4 구현 PR에서 작성한다.

#### 2) Policy Exact Lookup Contract
- 해시 조회의 단일 규격:
  `resolve_freshness_policy(hash) -> exact one policy OR BLOCKED`
  `resolve_scope_policy(hash) -> exact one policy OR BLOCKED`
- **엄격 금지**: `latest policy`, `current policy`, `default policy`, `fallback policy`, `closest version`. 오직 hash exact lookup만 허용한다.
- 알 수 없는 해시: `unknown freshness_policy_hash -> BLOCKED`, `unknown scope_policy_hash -> BLOCKED`.
- 무결성: 동일 해시에 대해 내용이 상이한 경우 `Configuration Integrity Error`로 fail-closed 중단.

---

### 8.6 Candidate Source Governance 최종 판정 방정식 (Final Governance Equation)

위 3개 권위가 확정된 후, Phase A4 Bridge가 구현할 `Candidate Source Governance PASS`의 수학적/논리적 최종 방정식은 다음과 같이 동결된다:

```text
Candidate Source Governance PASS
⟺
ALL pinned Bundle source members (rag_runtime_bundle_source) satisfy:

  1. Bundle Pin Exact-Match:
     (source_snapshot_id, source_version, canonical_checksum) exact match with rag_source_snapshot
  2. Source ACTIVE:
     rag_source.lifecycle_status == ACTIVE
  3. Endpoint VERIFIED / ENABLED / APPROVED:
     rag_source_endpoint.lifecycle_status == VERIFIED
     ∧ rag_source_endpoint.runtime_status == ENABLED
     ∧ rag_source_endpoint.acquisition_status == APPROVED
  4. Operation ENABLED / APPROVED:
     rag_source_operation.runtime_status == ENABLED
     ∧ rag_source_operation.acquisition_status == APPROVED
  5. Snapshot CURRENT:
     rag_source_snapshot.verification_status == CURRENT
  6. Snapshot Provenance Valid:
     SnapshotProvenanceReceipt.validate_provenance() == PASS
  7. Publication Approval Valid:
     publication_verification_id is not None
  8. Freshness Policy PASS:
     resolve_freshness_policy(pin.freshness_policy_hash) IS NOT NULL
     ∧ canonical_sha256(policy) == pin.freshness_policy_hash
     ∧ approved freshness evaluator(snapshot, pin.freshness_policy_hash, evaluation_time) == TRUE
  9. Scope Policy PASS:
     resolve_scope_policy(pin.scope_policy_hash) IS NOT NULL
     ∧ canonical_sha256(policy) == pin.scope_policy_hash
     ∧ approved scope evaluator(pin.scope_policy_hash, EVALUATION_CANDIDATE, member_context) == ALLOWED
 10. Usable Source Use Approval Exact Match:
     SqlAlchemySourceUseApprovalReader.read_usable_exact(
         identity=SourceUseApprovalIdentity(
             source_snapshot_id=pin.source_snapshot_id,
             source_code=source.source_code,
             source_version=pin.source_version,
             environment=RuntimeEnvironmentCode.LOCAL,
             purpose=approved_purpose_mapping(pin.source_purpose),
             approval_version=pin.approval_version,
         ),
         evaluation_time=evaluation_time,
     ) is not None
     (valid_from <= evaluation_time < expires_at, revoked_at is None)

AND Exact Member Coverage:
  번들에 핀된 소스 멤버 집합과 검증 대상 소스 멤버 집합 간 누락(missing) 또는 초과(extra)가 전혀 없어야 함.
```

---

### 8.7 Fail-Closed 결측 권위 시맨틱 (Fail-Closed Semantics)

다음 조건 중 **단 하나라도 발생하면** `EVALUATION_CANDIDATE / PASS`는 절대 발행되지 않으며 즉시 거부(Fail-closed)된다:
- 권위 정본 부재 (missing authority)
- 목적 매핑 모호 또는 미정의 (ambiguous / unmodeled purpose mapping, 특히 `GUIDELINE`)
- 정책 원천 부재 (missing freshness / scope policy content)
- 정책 해시 불일치 또는 미등록 해시
- 정책 만료 또는 위반 (stale freshness / blocked scope)
- 소스 사용 승인 만료 (expired approval)
- 소스 사용 승인 철회 (revoked approval)
- 목적 불일치 (wrong purpose)
- 환경 불일치 (wrong environment: `LOCAL` 외)
- 최신 스냅샷 아님 (`verification_status != CURRENT`)
- 소스/엔드포인트/오퍼레이션 비활성 또는 미승인 상태

결과:
- `candidate_guard_decision = null`
- `candidate_guard_ref = null`
- Release Gate loader 차단 지속 (`ACTUAL_RELEASE_GATE_AUTHORIZATION_BLOCKED`)
- 어떠한 synthetic/default PASS도 허용되지 않음

---

### 8.8 Phase A4 구현 진입 조건 (Entry Criteria for Implementation)

본 제안에 대해 다음 사항이 모두 만족되어 정본 문서가 머지될 때까지 **Python 구현 코드 작성을 엄격히 금지**한다:
1. `Q1 exact purpose mapping documented` (RESOLVED by @phina-io)
2. `Q2 freshness content source approved` (@phina-io)
3. `Q2 freshness anchor approved` (@phina-io)
4. `Q2 max_age approved` (@phina-io)
5. `Q2 canonical hash recipe approved` (@phina-io)
6. `Q3 scope content source approved` (@phina-io)
7. `Q3 EVALUATION_CANDIDATE allowed matrix approved` (@phina-io)
8. `Q3 member_context semantic dimension confirmed` (@phina-io)
9. `Q3 canonical hash recipe approved` (@phina-io)
10. `formal approval: @phina-io`
11. `docs PR: MERGED`

위 진입 조건이 완료된 뒤에만 `CANDIDATE_SOURCE_AUTHORITY_FREEZE_READY` 상태로 전이하고 Phase A4 어댑터 구현을 진행한다.

---

### 8.9 송은영(@phina-io) 공식 승인 요청 템플릿

```text
Q2 Freshness Final Resolution

Policy content source:
code-owned immutable content-addressed registry
| OTHER: ______

freshness_anchor:
COLLECTED_AT
| VERIFIED_AT
| EFFECTIVE_AT
| OTHER: ______

max_age_seconds:
________

hash:
canonical_sha256(evaluation-source-freshness-policy-v1 projection)

unknown/mismatch:
BLOCKED
```

```text
Q3 Scope Final Resolution

Policy content source:
code-owned immutable content-addressed registry
| OTHER: ______

Operation:
EVALUATION_CANDIDATE

member_context dimension:
RuntimeBundleMemberPurpose only is sufficient: YES | NO (if NO, specify additional required fields: ______)

Allowed RuntimeBundleMemberPurpose:
- CATALOG: ALLOWED | BLOCKED
- KNOWLEDGE: ALLOWED | BLOCKED
- CANDIDATE_INDEX_INPUT: ALLOWED | BLOCKED
- RULE: ALLOWED | BLOCKED
- GUIDELINE: ALLOWED | BLOCKED
- SAFETY_POLICY: ALLOWED | BLOCKED

hash:
canonical_sha256(evaluation-source-scope-policy-v1 projection)

unknown/mismatch:
BLOCKED
```
