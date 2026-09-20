# Product Decision Candidate: Canonical Evaluation Guard Evidence and Authority Binding (#162)

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-162-20260920` |
| 상태 | Proposed · Review Required · Issue #162 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 단일 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — PM / Product Acceptance / Evaluation & Safety |
| 교차 리뷰 (FYI) | 송은영 (`@phina-io`) — Backend·Data·Security / DB schema · migration · Repository 쓰기 경계<br>김지혜 (`@Jye-rookie`) — Source·Snapshot·Catalog 경계 / Worker |
| 추적 Issue | [#162](https://github.com/AI-HealthCare-05/AH_05_04/issues/162) |
| 조사 기준 | `origin/develop` @ `dd400ce4635a4b6697b3f1a7215a8b1b4f12ba8e` (PR #849 merge commit `f02f8f06`) |
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
- `evaluation_run_id`: 평가 실행 식별자
- `candidate_bundle_id`: 대상 런타임 번들 식별자 (physical UUID의 canonical lowercase string)
- `candidate_bundle_manifest_hash`: 런타임 번들 매니페스트 SHA-256
- `dataset_code`: 대상 Dataset 코드
- `dataset_version`: 대상 Dataset 시맨틱 버전
- `dataset_manifest_sha256`: FROZEN Dataset 매니페스트 SHA-256
- `required_partitions`: 평가 대상 필수 파티션 튜플
- `required_case_set_hash`: 필수 케이스 집합의 canonical hash (`evaluation-required-case-set-v1`)
- `environment`: `RuntimeEnvironment.LOCAL` 고정
- 최종 승인(Phase A2) 시 추가 필수 바인딩:
  - `candidate_guard_ref`: canonical Candidate Guard reference
  - `runtime_execution_manifest_id` 및 hash (Q4 확정 결과 반영)
  - `revision` 및 `safety_epoch` (Q4 확정 결과 반영)

### 3.2 Case-level Authority Binding 좌표
각 Required Case에 대한 `EVALUATION_REQUEST / PASS` Evidence는 정확히 1건씩 존재해야 하며, 다음 좌표에 exact-bind되어야 한다:
- `case_id`: Dataset에 정의된 필수 케이스 ID
- `evaluation_run_id`: 부모 Run과 동일한 평가 실행 ID
- `candidate_bundle_id`: 부모 Run과 동일한 번들 ID
- `candidate_bundle_manifest_hash`: 부모 Run과 동일한 번들 매니페스트 해시
- `request_guard_decision_id`: 케이스 단위 Guard 결정 ID
- `decision`: `"PASS"` 고정
- `request_operation_code`: 승인된 canonical 오퍼레이션 코드 (`EVALUATION_REQUEST`)
- `request_scope_codes`: UTF-8 byte sorted unique non-blank 튜플
- `scope_manifest_hash`: `canonical_scope_manifest_hash(request_scope_codes)`와 exact 일치
- `case_guard_ref`: canonical Case Guard reference

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

```text
{
  "projection_version": "evaluation-guard-coverage-v1",
  "evaluation_run_id": "<canonical_uuid>",
  "candidate_bundle_id": "<canonical_lowercase_uuid>",
  "candidate_bundle_manifest_hash": "<sha256_hex>",
  "candidate_guard_ref": {
    "artifact_code": "<code_or_id>",
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
      "case_id": "case-001",
      "case_guard_ref": { ... },
      "scope_manifest_hash": "<sha256_hex>"
    },
    ...
  ]
}
```
- `guard_coverage_manifest_hash = canonical_sha256(preimage)`
- **해시 레시피 상태 구분**:
  - `evaluation-guard-coverage-v1`은 아직 **final canonical recipe로 동결된 상태가 아니며**, 본 Phase A1에서는 Envelope structure 및 필수 의미축만을 **Proposed Candidate**로 제안한다.
  - Execution Manifest / Revision / Governance Revision / Safety Epoch의 exact physical source는 **Q4 판정 전까지 미확정(Pending Q4)** 상태로 유지되며, Q4 승인 완료 후에만 final hash recipe로 최종 동결한다.
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

### 3.7 Candidate Guard Decision ID 물리 타입 유보
- 현재 `RagEvaluationRun.candidate_guard_decision_id`는 `StableId`이다.
- canonical producer가 확정되지 않은 상태에서 이를 `CanonicalUuid`로 섣불리 좁히지 않는다.
- 본 결정에서는 `Candidate Guard identity physical type: PENDING PRODUCER FREEZE`로 유보하고 producer 확정 후 타입을 narrowing한다.

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

## 4. 미해결 핵심 결정 사항 (4 Unresolved Authority Questions)

다음 4개 항목은 단일 책임 리뷰어(`@hazelnutflavoured`) 및 교차 리뷰어의 명시적 승인을 거쳐 확정해야 하므로 Phase A1에서 임의로 결정하지 않는다:

### Q1. EVALUATION_CANDIDATE Canonical Producer
- **질문**: 어떤 evaluator/service가 `EVALUATION_CANDIDATE / PASS` 권위를 공식 발행하는가?
- **현황**: 현재 `ai_worker/tasks/rag/source_governance.py`는 `EVALUATION_CANDIDATE` 오퍼레이션에 대해 `OPERATION_CONTEXT_NOT_MODELED`로 거부한다.
- **선택지**:
  - **방안 1**: 기존 Source Governance evaluator를 확장하여 Candidate Evaluation에 필요한 컨텍스트(Building bundle manifest, snapshot completeness, approval closure)를 모델링하고 발행 권위를 부여.
  - **방안 2**: Evaluation 전용의 독립 Guard Evaluator 모듈을 신설하여 번들 무결성 및 거버넌스 승인 상태를 직접 검증하고 발행.
- **판정 기준**: Source 거버넌스 쓰기 책임(@phina-io / @Jye-rookie)과 Evaluation 실행 책임(@ceohwj) 사이의 권한 경계 분리 수준.

### Q2. EVALUATION_REQUEST Authority Source
- **질문**: Case 수준의 `EVALUATION_REQUEST` Guard 권위는 어디서 획득하는가?
- **선택지**:
  - **방안 A**: 기존 #806 `RequestGuardRuntimeBindingObservation`의 물리 레코드를 재사용하되, #162 Evaluation binding metadata(`evaluation_run_id`, `case_id`)와 composition하여 검증.
  - **방안 B**: #806과 분리된 별도의 `EvaluationRequestGuardObservation` 발행자 및 저장소를 구축.
  - **방안 C**: 공통 하위 Guard authority를 도입하고, `REQUEST`와 `EVALUATION_REQUEST`가 각자의 projection을 생성하도록 분리.
- **판정 기준**: 불필요한 테이블 증설 방지(DB 최소화) vs 런타임 환자 요청과 평가 요청 간의 데이터 평면 격리.

### Q3. Schema Set Membership
- **질문**: `rag-eval.evaluation-guard-evidence@1.0.0` artifact를 기존 Evaluation Schema Set에 포함할 것인가?
- **선택지**:
  - **방안 1 (권장 기본안)**: Standalone versioned authority artifact로 유지. 기존 Schema Set 1.0~1.5(`schema_registry.py`, `schema_exports.py`)를 일체 수정하지 않고 독립 검증.
  - **방안 2**: 정식 Schema Set member로 편입하기 위해 신규 `Schema Set 1.6` (총 27개 멤버) 거버넌스 결정을 추진하고, registry 및 schema export 동기화.
- **판정 기준**: 기존 불변 Schema Set 역사 보존 및 PR 변경 반경 최소화.

### Q4. Revision / Epoch Exact Physical Source
- **질문**: #162 본문이 요구하는 `Execution Manifest`, `Revision`, `Governance Revision`, `Safety Epoch`의 exact physical source는 무엇인가?
- **후보 소스**:
  - `RagRuntimeReleaseBundle.execution_manifest_id` vs `RagRuntimeExecutionManifest.manifest_hash`
  - `RagRuntimeEnvironment.environment_revision` vs `RagRuntimeEnvironment.governance_revision_ref` vs `RagRuntimeEnvironment.safety_epoch`
- **확정 필요 사항**: #162의 "Revision"이 환경 리비전(`environment_revision`)인지 거버넌스 리비전(`governance_revision_ref`)인지, 혹은 둘 다 필수인지 확정.

---

## 5. Phase A1 완료 검증

본 PR은 문서 전용(docs-only) 변경이므로 불필요한 전체 구현 테스트를 실행하지 않으며 다음 검증을 수행한다:
1. `git diff --check`: 포맷 및 공백 무결성 확인
2. `git grep -n "evaluation-guard-evidence" docs`: 문서 간 상호 참조 링크 정합성 확인
3. `git grep -n "EVALUATION_CANDIDATE" docs/contracts docs/governance` 및 `git grep -n "EVALUATION_REQUEST" docs/contracts docs/governance`: 용어 및 식별자 일관성 확인
4. `uv run ruff check .` 및 `uv run ruff format . --check`: 린트/포맷 정합성 확인

---

## 6. 결론 및 다음 단계

본 Phase A1 결정과 Proposed Contract(`docs/contracts/proposed/post-mvp-1/evaluation-guard-evidence-v1.md`)가 단일 책임 리뷰어의 승인을 획득하면:
1. Q1~Q4에 대한 최종 확정안이 반영된다.
2. `feat/162-evaluation-guard-authority` 브랜치에서 Phase A2(actual Guard producer, evidence bridge, exact-set validator, synthetic/negative tests)를 구현한다.
3. 그 전까지 저장소의 Protected Release Gate는 안전하게 fail-closed 상태를 유지한다.
