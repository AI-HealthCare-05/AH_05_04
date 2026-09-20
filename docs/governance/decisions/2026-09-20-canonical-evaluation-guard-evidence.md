# Product Decision Candidate: Canonical Evaluation Guard Evidence and Authority Binding (#162)

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-162-20260920` |
| 상태 | Proposed · Freeze Choices Resolved · Review Required · Issue #162 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 단일 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — PM / Product Acceptance / Evaluation & Safety |
| 교차 리뷰 (FYI) | 송은영 (`@phina-io`) — Backend·Data·Security / DB schema · migration · Repository 쓰기 경계<br>김지혜 (`@Jye-rookie`) — Source·Snapshot·Catalog 경계 / Worker |
| 추적 Issue | [#162](https://github.com/AI-HealthCare-05/AH_05_04/issues/162) |
| 조사 기준 | `origin/develop` @ `b3a92f32f0cfd6420d55de0ccf0b3c86216feb74` (PR #849 merge commit `f02f8f06`) |
| 상위·관련 결정 | [`PD-125-20260831`](./2026-08-31-rag-p0-contract-freeze.md), [`PD-216-20260902`](./2026-09-02-rag-evaluation-schema-set-1-1-freeze.md), [`PD-241-20260903`](./2026-09-03-rag-evaluation-schema-set-1-2-freeze.md), [`PD-799-20260918`](./2026-09-18-citation-authorization-production-authority-boundary.md), [`PD-833-20260919`](./2026-09-13-rag-answer-quality-metrics.md) |
| 관련 Issue / PR | #162, #163 (PR #849), #806 (PR #828), #853 (PR #857) |

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

본 작업은 재작업과 안전성 누수를 방지하기 위해 다음 4단계로 분리하여 진행한다:

```text
[지금] Phase A1: Contract Freeze / Authority Coordinate 확정 (Proposed Contract + Decision 문서)
        ↓ 책임 리뷰어(@hazelnutflavoured) 승인
Phase A2: Canonical Guard Producer + Evidence Bridge 구현 (feat/162-evaluation-guard-authority)
        ↓
Phase B: Paired Evaluation Artifact 구현 (ANS-BASE / ANS-RAG / ANS-FINAL 대조 및 95% CI)
        ↓
#163 Follow-up: Actual Release Gate integration (release_gate_loader.py 차단 해제 및 연결)
```

Phase A1 완료 시점의 상태는 `#162_GUARD_CONTRACT_PROPOSED`이며, `release_gate_loader.py`는 여전히 fail-closed(`ACTUAL_RELEASE_GATE_AUTHORIZATION_BLOCKED`) 상태를 유지한다.

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
- `required_partitions`: 평가 대상 필수 파티션 튜플 (`tuple[Partition, ...]`)
- `required_case_set_hash`: 필수 케이스 집합의 canonical hash (`evaluation-required-case-set-v1`)
- `environment`: `RuntimeEnvironment.LOCAL` 고정
- `candidate_guard_decision_id`: Candidate Guard 결정 식별자 (`CanonicalUuid`, per-run UUID string)
- `candidate_guard_decision`: `"PASS"` 고정
- `candidate_guard_ref`: canonical Candidate Guard reference (`GenericImmutableArtifactRef`: `artifact_code="evaluation_candidate_guard"`, `version="1.0"`, `content_sha256="<sha256>"`)
- `runtime_execution_manifest_id`: 대상 번들에 결속된 실행 매니페스트 ID (`RagRuntimeReleaseBundle.execution_manifest_id`, `CanonicalUuid`)
- `runtime_execution_manifest_hash`: 실행 매니페스트 해시 (`RagRuntimeExecutionManifest.manifest_hash`, `Sha256Hex`)
- `governance_revision_ref`: 대상 번들에 결속된 거버넌스 리비전 식별자 (`RagRuntimeReleaseBundle.governance_revision_ref`, non-empty string)
- `safety_epoch`: 대상 실행 환경의 안전성 에포크 (`RagRuntimeEnvironment.safety_epoch >= 1`, positive integer)
- *주의*: `environment_revision`은 가변 환경 상태 전이 카운터이므로 불변 권위 좌표 및 해시 레시피에서 명시적으로 제외한다.

### 3.2 Case-level Authority Binding 좌표
각 Required Case에 대한 `EVALUATION_REQUEST / PASS` Evidence는 정확히 1건씩 존재해야 하며, 다음 좌표에 exact-bind되어야 한다:
- `case_id`: Dataset에 정의된 필수 케이스 ID (`StableId`)
- `evaluation_run_id`: 부모 Run과 동일한 평가 실행 ID (`CanonicalUuid`)
- `candidate_bundle_id`: 부모 Run과 동일한 번들 ID (`CanonicalUuidString`)
- `candidate_bundle_manifest_hash`: 부모 Run과 동일한 번들 매니페스트 해시 (`Sha256Hex`)
- `case_guard_decision_id`: 케이스 단위 Guard 결정 ID (`CanonicalUuid`, per-case UUID string)
- `decision`: `"PASS"` 고정
- `request_operation_code`: 승인된 canonical 오퍼레이션 코드 (`EVALUATION_REQUEST` 단일 대문자 고정)
- `request_scope_codes`: UTF-8 byte sorted unique non-blank 튜플
- `scope_manifest_hash`: `canonical_scope_manifest_hash(request_scope_codes)`와 exact 일치 (`Sha256Hex`)
- `case_guard_ref`: canonical Case Guard reference (`GenericImmutableArtifactRef`: `artifact_code="evaluation_request_guard"`, `version="1.0"`, `content_sha256="<sha256>"`)

### 3.3 Required Case Set Canonical Hash Recipe
단순 케이스 ID 목록 해시가 아닌 Dataset authority와 결속된 사영 레시피를 동결한다:

```json
{
  "projection_version": "evaluation-required-case-set-v1",
  "dataset_manifest_sha256": "<exact validated FROZEN Dataset manifest hash>",
  "required_partitions": ["HOLDOUT", "SAFETY_REGRESSION"],
  "case_ids": ["case-001", "case-002", "..."]
}
```
- `case_ids`는 저장소의 canonical ordering 기준인 **UTF-16 BE** 바이트 순서로 정렬한다.
- 해시 계산 전에 중복 케이스 ID가 발견되면 즉시 거부(`CASE_DUPLICATE`)한다.
- `required_case_set_hash = canonical_sha256(projection)`

### 3.4 Guard Coverage Hash Recipe
Required Case Coverage는 단순 개수(count) 일치 검사가 아니며, 다음 preimage를 결속한다:

```json
{
  "projection_version": "evaluation-guard-coverage-v1",
  "evaluation_run_id": "<canonical_uuid>",
  "candidate_bundle_id": "<canonical_lowercase_uuid>",
  "candidate_bundle_manifest_hash": "<sha256_hex>",
  "candidate_guard_decision_id": "<canonical_uuid>",
  "candidate_guard_ref": {
    "artifact_code": "evaluation_candidate_guard",
    "version": "1.0",
    "content_sha256": "<sha256_hex>"
  },
  "dataset_manifest_sha256": "<sha256_hex>",
  "required_case_set_hash": "<evaluation-required-case-set-v1_sha256>",
  "runtime_execution_manifest_id": "<canonical_uuid>",
  "runtime_execution_manifest_hash": "<sha256_hex>",
  "governance_revision_ref": "<governance_revision_string>",
  "safety_epoch": 1,
  "case_guard_bindings": [
    {
      "case_id": "case-001",
      "case_guard_decision_id": "<canonical_uuid>",
      "case_guard_ref": {
        "artifact_code": "evaluation_request_guard",
        "version": "1.0",
        "content_sha256": "<sha256_hex>"
      },
      "scope_manifest_hash": "<canonical_scope_manifest_hash>"
    }
  ]
}
```
- `guard_coverage_manifest_hash = canonical_sha256(preimage)`
- **해시 레시피 정본 동결**:
  - `evaluation-guard-coverage-v1`의 모든 권위 좌표와 Envelope 구조가 확정되었다.
  - Q4 판정에 따라 `runtime_execution_manifest_id/hash`, `governance_revision_ref`, `safety_epoch`가 정본 좌표로 바인딩되었으며, `environment_revision`은 가변 동시성 필드로서 해시 preimage에서 영구 제외되었다.
- **불변 속성**:
  - 동일 케이스 집합이라도 번들이 다르면 다른 해시가 생성된다.
  - 케이스 바인딩 순서가 뒤바뀌거나 케이스가 누락되면 다른 해시가 생성되거나 검증에서 거부된다.

### 3.5 Exact-Set Coverage Semantics
Required Case 검증은 반드시 exact-set이어야 한다:
- `missing case` → reject (`BASELINE_ARTIFACT_INVALID`)
- `duplicate case` → reject (`CASE_DUPLICATE`)
- `extra case` (Dataset에 없는 케이스) → reject (`BASELINE_ARTIFACT_INVALID`)
- `wrong run_id` → reject (`HASH_MISMATCH`)
- `wrong bundle_id` 또는 `wrong bundle_manifest_hash` → reject (`HASH_MISMATCH`)
- `wrong execution manifest` / `wrong revision` / `wrong epoch` → reject (`HASH_MISMATCH`)
- `wrong scope hash` → reject (`HASH_MISMATCH`)
- `wrong Guard ref` → reject (`HASH_MISMATCH`)
- `non-PASS decision` → reject (`BASELINE_ARTIFACT_INVALID`)
- 단순 count 일치만으로 성공 처리하는 것은 엄격히 금지된다.

### 3.6 Runtime Bundle ID Wire Rule
- Runtime의 물리 번들 ID는 PostgreSQL DB의 `UUID`이다.
- 반면 `RagEvaluationRun.candidate_bundle_id`는 현재 스키마상 `StableId`이다.
- 본 Phase A1에서는 `RagEvaluationRun` 스키마를 변경하지 않으며, canonical lowercase UUID 문자열(`str(runtime_bundle_uuid)`)을 wire 포맷으로 동결한다.
- 향후 A2 검증기는 `str(runtime_bundle_uuid) == run.candidate_bundle_id`를 exact-match하고 non-canonical 포맷은 fail-closed 처리한다.

### 3.7 Candidate Guard Decision ID 물리 타입 확정
- `RagEvaluationRun.candidate_guard_decision_id`의 wire 포맷은 `CanonicalUuid` 문자열로 확정한다.
- Evaluator는 run-scoped의 고유/결정론적 `UUID`를 발행하여 감사 추적성(audit traceability)을 보장하며, 비정규 UUID 문자열은 검증기에서 fail-closed 처리한다.

### 3.8 #806 Ref의 Generic Immutable Identity 보존
- `#806`의 `RequestGuardRuntimeBindingRef`는 `(artifact_code, version, content_sha256)` 3요소 형태다.
- Evaluation의 `ImmutableReference`는 `(id, version, hash)` 형태다.
- 단순 필드명 변경으로 권위를 왜곡하지 않으며, Phase A1 계약에서는 generic immutable artifact identity로 `(artifact_code, version, content_sha256)`를 우선 기술한다.

### 3.9 Privacy Boundary
- 원문 환자 질문(query text), 답변(answer text), 환자 식별자(patient ID), Provider 원문 응답 payload, 시스템 자격증명(credential)은 Evaluation Guard Evidence artifact, 로그, 오류 메시지에 절대 포함하지 않는다.

### 3.10 #163 Consumer Boundary
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

### Q4. BUILDING Candidate Runtime Coordinates 확정
- **결정**: Candidate 번들의 정본 불변 런타임 좌표는 다음 4개 축으로 확정하며, `environment_revision`은 권위 좌표에서 영구 제외한다.
- **확정 좌표**:
  1. **Execution Manifest**:
     - `RagRuntimeReleaseBundle.execution_manifest_id: CanonicalUuid`
     - `RagRuntimeExecutionManifest.manifest_hash: Sha256Hex`
     - Evidence 좌표명: `runtime_execution_manifest_id`, `runtime_execution_manifest_hash`
  2. **Governance Revision**:
     - `RagRuntimeReleaseBundle.governance_revision_ref: str` (번들 빌드 시점에 핀된 거버넌스 리비전 식별자, `bundle.governance_revision_ref == env.governance_revision_ref` exact match 검증 필수, `None` 불가)
     - Evidence 좌표명: `governance_revision_ref`
  3. **Safety Epoch**:
     - `RagRuntimeEnvironment.safety_epoch: int` (`>= 1`, 번들 발행 시점의 환경 안전성 에포크를 캡처하여 모든 Case Guard와 exact-bind)
     - Evidence 좌표명: `safety_epoch`
  4. **`environment_revision` 영구 제외**:
     - `RagRuntimeEnvironment.environment_revision`은 활성 번들 포인터 전이 및 환경 상태 변경 시 증가하는 가변 동시성 제어 카운터일 뿐, Candidate 번들이나 실행 매니페스트 자체의 불변 빌드 좌표가 아니다.
     - 따라서 권위 식별자 및 `evaluation-guard-coverage-v1` 해시 preimage에서 완전히 제외한다. (Phase A2 실행 시 로컬 읽기 낙관적 동시성 펜스로만 활용 가능)
- **Active 번들 포인터 엄격 금지**:
  - 평가 대상은 `BUILDING` 상태의 후보 번들이므로, `RagRuntimeEnvironment.active_bundle_id` 및 `active_bundle_manifest_hash`를 평가 대상으로 참조하는 것은 엄격히 금지된다.

---

## 5. Phase A1 완료 검증

본 PR은 문서 전용(docs-only) 변경이므로 불필요한 전체 구현 테스트를 실행하지 않으며 다음 검증을 수행한다:
1. `git diff --check`: 포맷 및 공백 무결성 확인
2. `git grep -n "evaluation-guard-evidence" docs`: 문서 간 상호 참조 링크 정합성 확인
3. `git grep -n "EVALUATION_CANDIDATE" docs/contracts docs/governance` 및 `git grep -n "EVALUATION_REQUEST" docs/contracts docs/governance`: 용어 및 식별자 일관성 확인
4. `uv run ruff check .` 및 `uv run ruff format . --check`: 린트/포맷 정합성 확인

---

## 6. 결론 및 다음 단계

본 Phase A1 결정과 Proposed Contract(`docs/contracts/proposed/post-mvp-1/evaluation-guard-evidence-v1.md`)에서 Q1~Q4 권위 결정이 완전히 동결되었으므로:
1. 단일 책임 리뷰어(`@hazelnutflavoured`)의 승인 후,
2. `feat/162-evaluation-guard-authority` 브랜치에서 Phase A2(actual Guard producer, evidence bridge, exact-set validator, synthetic/negative tests)를 구현한다.
3. 그 전까지 저장소의 Protected Release Gate는 안전하게 fail-closed 상태를 유지한다.
