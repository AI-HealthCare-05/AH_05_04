# Canonical Evaluation Guard Evidence 계약 v1 (#162)

| 항목 | 값 |
| --- | --- |
| 상태 | Proposed / Phase A1 Contract Freeze Candidate · 담당 리뷰 대기 |
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
| `required_partitions` | `tuple[Partition, ...]` | 평가 대상 필수 파티션 튜플 |
| `required_case_set_hash` | `Sha256Hex` | 필수 케이스 집합의 canonical 해시 (`evaluation-required-case-set-v1`) |
| `environment` | `Literal["LOCAL"]` | 실행 환경 (`LOCAL` 고정) |
| `candidate_guard_decision_id` | `PENDING PRODUCER FREEZE` | Candidate Guard 결정 식별자 (Q1 확정 전까지 물리 타입 유보) |
| `candidate_guard_decision` | `Literal["PASS"]` | Candidate Guard 판정 (`PASS` 고정) |
| `candidate_guard_ref` | `GenericImmutableArtifactRef` | Candidate Guard 불변 참조 (`artifact_code`, `version`, `content_sha256`) |
| `runtime_execution_manifest_id` | `PENDING Q4` | 실행 매니페스트 식별자 (Q4 확정 결과 반영) |
| `runtime_execution_manifest_hash` | `Sha256Hex` | 실행 매니페스트 해시 (Q4 확정 결과 반영) |
| `environment_revision` | `PENDING Q4` | 환경 리비전 (Q4 확정 결과 반영) |
| `governance_revision_ref` | `PENDING Q4` | 거버넌스 리비전 참조 (Q4 확정 결과 반영) |
| `safety_epoch` | `PENDING Q4` | 안전성 에포크 (Q4 확정 결과 반영) |
| `case_guard_bindings` | `tuple[EvaluationCaseGuardBinding, ...]` | 필수 케이스별 Guard 바인딩 튜플 |
| `guard_coverage_manifest_hash` | `Sha256Hex` | 전수 결속 커버리지 매니페스트 SHA-256 |

### 2.2 Case-level Evidence Coordinates (`EvaluationCaseGuardBinding`)
각 Required Case에 결속되는 Guard Evidence는 다음 좌표를 만족해야 한다:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `case_id` | `StableId` | Dataset에 정의된 필수 케이스 ID |
| `evaluation_run_id` | `CanonicalUuid` | 부모 Run과 동일한 평가 실행 ID |
| `candidate_bundle_id` | `CanonicalUuidString` | 부모 Run과 동일한 번들 ID |
| `candidate_bundle_manifest_hash` | `Sha256Hex` | 부모 Run과 동일한 번들 매니페스트 해시 |
| `request_guard_decision_id` | `PENDING PRODUCER FREEZE` | 케이스 Guard 결정 식별자 |
| `decision` | `Literal["PASS"]` | 케이스 Guard 판정 (`PASS` 고정) |
| `request_operation_code` | `Literal["EVALUATION_REQUEST"]` | 승인된 canonical 오퍼레이션 코드 (lowercase/alias 금지) |
| `request_scope_codes` | `tuple[str, ...]` | non-empty, unique, NFC, UTF-8 byte sorted 스코프 코드 튜플 |
| `scope_manifest_hash` | `Sha256Hex` | `canonical_scope_manifest_hash(request_scope_codes)`와 exact 일치 |
| `case_guard_ref` | `GenericImmutableArtifactRef` | Case Guard 불변 참조 (`artifact_code`, `version`, `content_sha256`) |

---

## 3. 정본 해시 및 사영 레시피 (Canonical Hash Recipes)

### 3.1 Required Case Set Recipe (`evaluation-required-case-set-v1`)
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

### 3.2 Guard Coverage Recipe (`evaluation-guard-coverage-v1`)
전체 Case Coverage의 암호학적 결속을 위해 다음 preimage를 직렬화하여 해시를 산출한다:

```json
{
  "projection_version": "evaluation-guard-coverage-v1",
  "evaluation_run_id": "<canonical_uuid_string>",
  "candidate_bundle_id": "<canonical_lowercase_uuid>",
  "candidate_bundle_manifest_hash": "<sha256_hex>",
  "candidate_guard_ref": {
    "artifact_code": "<artifact_code>",
    "version": "<version>",
    "content_sha256": "<sha256_hex>"
  },
  "required_case_set_hash": "<evaluation-required-case-set-v1_sha256>",
  "runtime_execution_manifest_id": "<id_pending_q4>",
  "runtime_execution_manifest_hash": "<hash_pending_q4>",
  "environment_revision": "<revision_pending_q4>",
  "governance_revision_ref": "<ref_pending_q4>",
  "safety_epoch": "<epoch_pending_q4>",
  "case_guard_bindings": [
    {
      "case_id": "rag-case-001",
      "case_guard_ref": {
        "artifact_code": "<artifact_code>",
        "version": "<version>",
        "content_sha256": "<sha256_hex>"
      },
      "scope_manifest_hash": "<canonical_scope_manifest_hash>"
    }
  ]
}
```
- **해시 계산**: `guard_coverage_manifest_hash = canonical_sha256(preimage)`
- **해시 레시피 상태 구분**:
  - `evaluation-guard-coverage-v1`은 아직 **final canonical recipe로 동결된 상태가 아니며**, 본 계약에서는 Envelope structure 및 필수 의미축만을 **Proposed Candidate**로 제안한다.
  - Execution Manifest / Revision / Governance Revision / Safety Epoch의 exact physical source는 **Q4 판정 전까지 미확정(Pending Q4)** 상태로 유지되며, Q4 승인 완료 후에만 final hash recipe로 최종 동결한다.
- **불변 속성**:
  - 동일 케이스 집합이라도 `candidate_bundle_id` 또는 `candidate_bundle_manifest_hash`가 다르면 다른 해시 생성
  - 단 1개의 필수 케이스가 누락되거나 바인딩 순서가 뒤바뀌면 다른 해시 생성 또는 검증 거부
  - 케이스의 Guard ref 또는 스코프 해시가 변조되면 다른 해시 생성

---

## 4. 전수 결속 의미와 오류 규격 (Exact-Set Coverage Semantics)

`case_guard_bindings`의 검증은 단순 개수(count) 일치 검사가 아니며, Dataset authority에서 도출된 `expected_case_ids`와의 **exact-set** 일치여야 한다.

| 위반 조건 | 검증 판정 | 오류 코드 |
| --- | --- | --- |
| 필수 케이스 누락 (missing case) | 즉시 거부 (Fail closed) | `BASELINE_ARTIFACT_INVALID` |
| 동일 케이스 중복 바인딩 (duplicate case) | 즉시 거부 (Fail closed) | `CASE_DUPLICATE` |
| Dataset에 없는 케이스 포함 (extra case) | 즉시 거부 (Fail closed) | `BASELINE_ARTIFACT_INVALID` |
| 케이스 순서 불일치 (order mismatch) | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| `evaluation_run_id` 불일치 | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| `candidate_bundle_id` 불일치 | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| `candidate_bundle_manifest_hash` 불일치 | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| `execution_manifest` / `revision` / `epoch` 불일치 | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| `scope_manifest_hash` 불일치 | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| Guard ref 변조 또는 무효 | 즉시 거부 (Fail closed) | `HASH_MISMATCH` |
| non-PASS decision (FAIL 등) | 즉시 거부 (Fail closed) | `BASELINE_ARTIFACT_INVALID` |
| 허용되지 않은 operation code | 즉시 거부 (Fail closed) | `BASELINE_ARTIFACT_INVALID` |

---

## 5. 데이터 타입 및 와이어 규칙 (Data Types & Wire Rules)

1. **Runtime Bundle ID Wire Rule**:
   - Runtime의 물리 bundle ID는 PostgreSQL의 `UUID`다.
   - `RagEvaluationRun.candidate_bundle_id`는 현재 스키마상 `StableId`다.
   - 본 계약에서는 `RagEvaluationRun` 스키마를 변경하지 않고, canonical lowercase UUID string(예: `123e4567-e89b-12d3-a456-426614174000`)을 표준 와이어 포맷으로 규정한다.
   - 검증기는 `str(runtime_bundle_uuid) == run.candidate_bundle_id`를 exact-match하고, 대문자 또는 비정규 포맷은 fail closed한다.
2. **Candidate Guard Decision ID 물리 타입**:
   - canonical producer 확정 전까지 `PENDING PRODUCER FREEZE`로 유보한다.
   - `CanonicalUuid`로 섣불리 좁히지 않으며, producer 구현 시 실제 권위 식별자 타입을 반영한다.
3. **Generic Immutable Artifact Reference**:
   - `#806`의 `RequestGuardRuntimeBindingRef`와 동일한 `(artifact_code, version, content_sha256)` 3요소 형식을 기본으로 사용한다.
   - 평가용 `ImmutableReference`(id/version/hash)로 임의 필드명 변환을 수행하지 않는다.
4. **오퍼레이션 코드 엄격성**:
   - `EVALUATION_REQUEST` 단일 canonical 대문자 값만 허용한다.
   - `"evaluation_request"`, `"Evaluation_Request"` 등 임의의 lowercase 또는 alias 허용 집합은 금지한다.

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
