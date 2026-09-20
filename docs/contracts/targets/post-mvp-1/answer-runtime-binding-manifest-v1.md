# Answer Runtime Authority Binding Manifest 계약 v1 (#159)

| 항목 | 값 |
| --- | --- |
| 상태 | Approved Target (Phase B) · Proposed / Review Required (Phase C-1) |
| 구현 | Partially implemented — DTO/schema, 10 canonical recipe helpers, manifest self-hash/run/variant delta state validation, #808 typed seam projection and fail-closed tests; Phase C-1 carrier audit & materialization coordinate review (code change: none) |
| Decision | [`PD-159-20260913`](../../../governance/decisions/2026-09-13-rag-answer-quality-metrics.md) |
| 추적 Issue | [#159](https://github.com/AI-HealthCare-05/AH_05_04/issues/159) |
| 구현 담당 | 정현우 (`@ceohwj`, AI/RAG Implementation Owner) |
| 책임 리뷰 | 김지혜 (`@Jye-rookie`, Worker / Track A·C·E) — `Review Required` |
| 수용 증거 (Acceptance) | 권가빈 (`@hazelnutflavoured`, PM / Track F Acceptance) |
| 기술 통제 (Backend/DB) | 송은영 (`@phina-io`, Backend / Data & Security) |
| 이전 Phase B 승인 | [PR #833 comment `5740823309`](https://github.com/AI-HealthCare-05/AH_05_04/pull/833#issuecomment-5740823309) · Final HEAD `ef8a78c8f6ad1c037d203d1b376d899e0dbffbcd` · Merge commit `5128cfdee8d9791a76fe6331cea2314420184cc9` |
| 상류 권위 | RFC 8785 (JCS Canonical JSON), PR #808 (`AnswerComparisonRunInput` Seam), PR #828 / #806 (`RequestGuardRuntimeBindingObservation`) |
| 연결 이슈 | #159, #808, #828 / #806, #180, #807, #799 |

> [!NOTE]
> **승인 범위**:
> 이번 승인은 **Phase B DEV contract implementation approval**이다.
> 다음을 의미하지 않는다:
> - Runtime integration approved
> - HOLDOUT approved
> - Baseline Freeze approved
> - Release PASS approved
> - PUBLIC_TRACK_F approved

구현 이후에도 authoritative 3-variant carrier materialization, `RETRIEVED_EVIDENCE`, 4개 finalization
authority, actual pair execution은 미구현 상태이며 위 승인 범위를 확장하지 않는다.

---

## 1. 목적과 배경

본 문서는 Issue #159 Answer 3-Pair Comparison(`ANS-BASE--ANS-RAG`, `ANS-RAG--ANS-FINAL`, `ANS-BASE--ANS-FINAL`) 실행 시 요구되는 15개 Authority Binding(7개 Supplemental Controlled Variables + 8개 Allowed Delta Axes) 중, 10개 실재 Authority의 표준화된 Canonical Projection 및 Hash Recipe, 1개 명시적 미해결 바인딩(`RETRIEVED_EVIDENCE`), 그리고 상류 차단 4개 항목을 관리하는 **Answer Runtime Authority Binding Manifest**의 계약을 정의한다.

### 핵심 설계 원칙
1. **Schema Set 1.5 불변 원칙**: 기존 `RagEvaluationRun` (Schema Set 1.5) 스키마를 임의 확장하거나 수정하지 않는다 (`SEPARATE_BINDING_MANIFEST_PREFERRED`).
2. **기존 Artifact 무결성 원칙**: 3개 pair comparison artifact를 묶는 `answer-comparison-set-manifest`와 본 `answer-runtime-binding-manifest`를 엄격히 분리하여 단일 책임 원칙을 유지한다.
3. **순수 Seam 결속**: PR #808에서 기합의된 `AnswerComparisonSupplementalControls` 및 `AnswerComparisonDeltaBindings` typed seam과 1:1 정합 매핑한다.
4. **Run-Bound Deterministic Integrity**: 임의 난수 ID(`manifest_id`), 벽시계 타임스탬프(`created_at`), 외부 GitHub 워크플로 메타데이터(`blocker_issue`)를 manifest payload 및 해시 프리이미지에서 배제하고, 동일한 run-scoped manifest payload(`schema_id`, `schema_version`, `experiment_id`, `run_id`, `variant_id`, `supplemental_controls`, `delta_bindings`) 재계산 시 100% 동일한 결정론적 artifact integrity hash(`manifest_sha256`)를 생성한다. Cross-run semantic equality는 개별 binding hash로 수행하며 본 해시를 교차 비교용으로 쓰지 않는다.
5. **기존 Immutable Hash 우선 재사용**: 이미 content-addressed SHA-256을 보유한 불변 아티팩트 참조(`prompt_ref`, `parser_ref`, `retrieval_config_ref`, `knowledge_index_ref`)는 불필요한 래핑 재해싱 없이 기존 불변 해시를 직접 결속한다.
6. **실제 Runtime Invocation 정합**: Provider 호출 시 실제 전달되지 않는 암묵적 기본값(implicit defaults)을 authority로 승격하지 않는다.
7. **명시적 `NOT_APPLIED` 상태**: 특정 Variant에서 해당 축이 실행되지 않은 비적용 상태는 임의의 fake sentinel(`"none"`, `"LOCAL"`)이나 `null`이 아닌 결정론적 typed state로 표현한다.
8. **계층 분리 원칙 및 Phase B 상태 어휘 (Status Vocabulary)**:
   $$\text{Authority Recipe Status} \neq \text{Authoritative Runtime Carrier Status} \neq \text{Pair Execution Readiness}$$
   $$\text{Recipe Approved} \neq \text{Binding Implemented} \neq \text{Carrier Ready} \neq \text{Pair Executable}$$
   - **Phase B Status Vocabulary**:
     - `READY`: 0개 (실제 3-variant pair execution ready 항목 없음)
     - `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING`: 10개 (Canonical projection 및 hash recipe는 승인되었으나, actual execution authority에서 값을 추출·검증·materialize하여 Manifest에 기록하는 production/DEV binding implementation 대기)
     - `SOURCE_EXISTS_RECIPE_UNRESOLVED`: 1개 (`RETRIEVED_EVIDENCE` — canonical source/projection/hash recipe 미확정)
     - `BLOCKED_BY_UPSTREAM_AUTHORITY`: 4개 (`FINAL_VALIDATOR`, `CITATION_GATE`, `SAFETY_GATE`, `RELEASE_GATE` — 상류 런타임 권위 미완료로 차단)
   - Canonical Recipe 승인(`DEFINED / APPROVED`)은 정규 사영 및 해싱 규칙의 승인 정의일 뿐이며, actual variant execution에서 해당 값을 권위 있게 공급하는 runtime carrier의 실재(`Carrier Ready`), binding 구현 완료(`Binding Implemented`), 및 pair 실행 가능성(`Pair Executable`)과는 엄격히 구분된다.

---

## 2. Manifest 식별 및 구조

- **Schema ID**: `rag-eval.answer-runtime-binding-manifest`
- **Schema Version**: `1.0.0`
- **Artifact Code**: `answer_runtime_binding_manifest`

### JSON Schema 정의

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "rag-eval.answer-runtime-binding-manifest@1.0.0",
  "title": "AnswerRuntimeAuthorityBindingManifest",
  "type": "object",
  "required": [
    "schema_id",
    "schema_version",
    "experiment_id",
    "run_id",
    "variant_id",
    "supplemental_controls",
    "delta_bindings",
    "manifest_sha256"
  ],
  "properties": {
    "schema_id": { "const": "rag-eval.answer-runtime-binding-manifest" },
    "schema_version": { "const": "1.0.0" },
    "experiment_id": { "type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$" },
    "run_id": {
      "type": "string",
      "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
      "format": "uuid"
    },
    "variant_id": { "type": "string", "enum": ["ANS-BASE", "ANS-RAG", "ANS-FINAL"] },
    "supplemental_controls": {
      "type": "object",
      "required": [
        "input_context_hash",
        "prompt_structure_hash",
        "parser_hash",
        "seed_hash",
        "sampling_parameters_hash",
        "token_limit_hash",
        "timeout_hash"
      ],
      "properties": {
        "input_context_hash": { "type": "string", "pattern": "^[0-9a-f]{64}$" },
        "prompt_structure_hash": { "type": "string", "pattern": "^[0-9a-f]{64}$" },
        "parser_hash": { "type": "string", "pattern": "^[0-9a-f]{64}$" },
        "seed_hash": { "type": "string", "pattern": "^[0-9a-f]{64}$" },
        "sampling_parameters_hash": { "type": "string", "pattern": "^[0-9a-f]{64}$" },
        "token_limit_hash": { "type": "string", "pattern": "^[0-9a-f]{64}$" },
        "timeout_hash": { "type": "string", "pattern": "^[0-9a-f]{64}$" }
      },
      "additionalProperties": false
    },
    "delta_bindings": {
      "type": "object",
      "required": [
        "retrieval_pipeline_hash",
        "source_index_hash",
        "runtime_bundle_hash",
        "retrieved_evidence_hash",
        "final_validator_hash",
        "citation_gate_hash",
        "safety_gate_hash",
        "release_gate_hash"
      ],
      "properties": {
        "retrieval_pipeline_hash": { "type": ["string", "null"], "pattern": "^[0-9a-f]{64}$" },
        "source_index_hash": { "type": ["string", "null"], "pattern": "^[0-9a-f]{64}$" },
        "runtime_bundle_hash": { "type": ["string", "null"], "pattern": "^[0-9a-f]{64}$" },
        "retrieved_evidence_hash": { "type": ["string", "null"], "pattern": "^[0-9a-f]{64}$" },
        "final_validator_hash": { "type": ["string", "null"], "pattern": "^[0-9a-f]{64}$" },
        "citation_gate_hash": { "type": ["string", "null"], "pattern": "^[0-9a-f]{64}$" },
        "safety_gate_hash": { "type": ["string", "null"], "pattern": "^[0-9a-f]{64}$" },
        "release_gate_hash": { "type": ["string", "null"], "pattern": "^[0-9a-f]{64}$" }
      },
      "additionalProperties": false
    },
    "manifest_sha256": { "type": "string", "pattern": "^[0-9a-f]{64}$" }
  },
  "additionalProperties": false
}
```

### 스키마 정렬 및 유효성 검증 규칙
1. **`run_id` 검증 (CanonicalUuid 계약 정렬)**:
   - JSON Schema 패턴은 Evaluation 공통 정규식 `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` 및 `"format": "uuid"`를 따른다.
   - 향후 Python 구현에서는 저장소 공통 타입인 `ai_worker.tasks.evaluation.schemas.common.CanonicalUuid`를 그대로 재사용하여 Python `UUID(value)` 파싱 및 canonical string 일치 검증을 수행하며, 별도의 중복 UUID validator를 만들지 않는다.
2. **`delta_bindings` 널러빌리티**:
   - 8개 delta 필드는 `"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"`로 정의되어, 상류 미완료나 레시피 미확정 상태인 경우 `null`을 명시적으로 허용하고 바인딩 완료 시에는 엄격한 64자리 hex SHA-256 문자열을 요구한다.

---

## 3. `NOT_APPLIED` Typed State 및 해시 규격

Variant에서 특정 처리 단계가 실행되지 않은 경우(예: `ANS-BASE`의 검색 축, `ANS-BASE` 및 `ANS-RAG`의 최종 검증/게이트 축)는 임의의 fake 값(`"none"`, `"LOCAL"`)을 쓰지 않고 명시적인 Canonical Typed State로 프로젝션한다.

### Canonical Projection

```json
{
  "axis": "<AXIS_KEY>",
  "binding_state": "NOT_APPLIED",
  "projection_version": "answer-authority-binding-v1"
}
```

### Hash Preimage & Hashing Rule

`canonical_sha256({"axis": "<AXIS_KEY>", "binding_state": "NOT_APPLIED", "projection_version": "answer-authority-binding-v1"})`

- **의미론적 엄격성**:
  $$\text{NOT\_APPLIED} \neq \text{missing} \neq \text{blocked} \neq \text{null fallback} \neq \text{synthetic authority}$$
- 각 축별로 고유한 결정론적 64자리 Hex SHA-256을 생성하므로, 해당 단계가 적용되지 않은 Variant 간에는 동일한 해시가 바인딩된다.

---

## 4. Variant별 Delta Binding 의미 및 비교 쌍(Pair) Readiness

### 1) Variant별 바인딩 상태

| 축 구분 | Delta Axis Key | `ANS-BASE` | `ANS-RAG` | `ANS-FINAL` |
| :--- | :--- | :--- | :--- | :--- |
| **Retrieval Axes** | `RETRIEVAL_PIPELINE` | `NOT_APPLIED` 해시 | 실제 정본 해시 ($H_{ret}$) | 실제 정본 해시 ($H_{ret}$) |
| | `SOURCE_INDEX` | `NOT_APPLIED` 해시 | 실제 정본 해시 ($H_{idx}$) | 실제 정본 해시 ($H_{idx}$) |
| | `RUNTIME_BUNDLE` | `NOT_APPLIED` 해시 | 실제 정본 해시 ($H_{bnd}$) | 실제 정본 해시 ($H_{bnd}$) |
| | `RETRIEVED_EVIDENCE` | `NOT_APPLIED` 해시 | `null` (Carrier/Recipe 미확정) | `null` (Carrier/Recipe 미확정) |
| **Finalization Axes** | `FINAL_VALIDATOR` | `NOT_APPLIED` 해시 | `NOT_APPLIED` 해시 | `null` (상류 #180 차단) |
| | `CITATION_GATE` | `NOT_APPLIED` 해시 | `NOT_APPLIED` 해시 | `null` (상류 #807/#799/#180 차단) |
| | `SAFETY_GATE` | `NOT_APPLIED` 해시 | `NOT_APPLIED` 해시 | `null` (상류 #180 차단) |
| | `RELEASE_GATE` | `NOT_APPLIED` 해시 | `NOT_APPLIED` 해시 | `null` (상류 #180 차단) |

### 2) 비교 쌍별 Readiness 및 Fail-Closed 작동 원리

기존 PR #808의 `_check_delta_bindings()` 및 `_check_supplemental_controls()` 커널 로직을 무수정으로 수용한다:

1. **`ANS-BASE -> ANS-RAG` (RAG 도입 효과 비교)**:
   - **Allowed Deltas**: `RETRIEVAL_PIPELINE`, `SOURCE_INDEX`, `RUNTIME_BUNDLE`, `RETRIEVED_EVIDENCE`
   - **Non-Allowed Deltas**: `FINAL_VALIDATOR`, `CITATION_GATE`, `SAFETY_GATE`, `RELEASE_GATE`
   - **구조적 분리 성과**: `NOT_APPLIED` semantics 도입으로 인해 `FINAL_VALIDATOR`, `CITATION_GATE`, `SAFETY_GATE`, `RELEASE_GATE` 4개 finalization authority의 부재(#180, #807, #799)가 `ANS-BASE -> ANS-RAG` 비교를 가로막는 구조적 종속 문제는 완전히 해결되었다 (두 Variant 모두 4개 finalization 축에 동일한 `NOT_APPLIED` 해시를 가지므로 non-allowed deltas가 일치함).
   - **현재 실행 판정**: **`NOT READY` (실행 시 fail-closed / INVALID)**.
     - **차단 사유**:
       1. `RETRIEVED_EVIDENCE` authoritative carrier/recipe 미확정 (`retrieved_evidence_hash = null`)
       2. 7 Supplemental Controls의 3-variant runtime carrier/extractor 미구현
     - **커널 동작**: `_check_delta_bindings()`에서 `getattr(candidate.delta_bindings, "retrieved_evidence_hash") is None`이 감지되어 `delta_binding_missing = True`가 발동하며, `execution_status = INVALID`, `decision_status = None`으로 안전하게 fail-closed 처리된다.
   - **실제 Pair 실행 전제 조건 (Prerequisites)**:
     - **A**: 7개 Supplemental Controls가 `ANS-BASE`와 `ANS-RAG` 양쪽에서 authoritative carrier로 materialize될 것
     - **B**: 두 Variant에서 7개 supplemental bindings가 모두 non-None일 것
     - **C**: 7개 mandatory supplemental hashes가 exact-match할 것
     - **D**: `RETRIEVED_EVIDENCE` authoritative carrier/recipe가 해결되어 `retrieved_evidence_hash`가 non-None으로 제공될 것
     - **E**: 나머지 3개 retrieval delta 축(`RETRIEVAL_PIPELINE`, `SOURCE_INDEX`, `RUNTIME_BUNDLE`)의 authoritative binding이 확보될 것
     - **F**: 4개 finalization 축은 두 Variant 모두 canonical `NOT_APPLIED`로 exact-match할 것
   - **핵심 원칙**:
     `NOT_APPLIED` semantics 해결로 #180/#807/#799 finalization authority가 `ANS-BASE -> ANS-RAG`의 선행조건이 되는 구조적 문제는 제거되었다. 그러나 실제 Pair execution은 `RETRIEVED_EVIDENCE`뿐 아니라 7개 Supplemental Controls의 authoritative runtime carrier가 모두 확보된 후에만 가능하다.

2. **`ANS-RAG -> ANS-FINAL` (최종 게이트/공개 효과 비교)**:
   - **Allowed Deltas**: `FINAL_VALIDATOR`, `CITATION_GATE`, `SAFETY_GATE`, `RELEASE_GATE`
   - **Non-Allowed Deltas**: `RETRIEVAL_PIPELINE`, `SOURCE_INDEX`, `RUNTIME_BUNDLE`, `RETRIEVED_EVIDENCE`
   - **현재 실행 판정**: **`NOT READY` / FAIL-CLOSED**
     - **차단 사유**:
       1. 7 Supplemental Controls의 runtime carrier/extractor 미구현
       2. `RETRIEVED_EVIDENCE` carrier 미해결 (`null`)
       3. `ANS-FINAL`의 4개 finalization authorities 상류 미완료 (`null`)
     - **커널 동작**: `_check_delta_bindings()`에서 `getattr(baseline.delta_bindings, "retrieved_evidence_hash") is None` 및 `getattr(candidate.delta_bindings, final_attr) is None` 감지로 `delta_binding_missing = True`가 발동하여 `execution_status = INVALID`, `decision_status = None`으로 자동 차단된다.

3. **`ANS-BASE -> ANS-FINAL` (전체 효과 요약 비교)**:
   - **현재 실행 판정**: **`NOT READY` / FAIL-CLOSED**
     - **차단 사유**:
       1. 7 Supplemental Controls의 runtime carrier/extractor 미구현
       2. `RETRIEVED_EVIDENCE` carrier 미해결 (`null`)
       3. `ANS-FINAL`의 4개 finalization authorities 상류 미완료 (`null`)
     - **커널 동작**: `delta_binding_missing = True` 발동으로 `execution_status = INVALID`, `decision_status = None`으로 자동 차단된다.

---

## 5. 10 Canonical Recipes 규격 및 Carrier 상태 분리

### 5.1 7 Supplemental Controls의 Runtime Carrier 상태 분리

Authority binding 체계는 다음 세 계층을 엄격히 분리하여 다룬다:
$$\text{Authority Recipe Status} \neq \text{Authoritative Runtime Carrier Status} \neq \text{Pair Execution Readiness}$$
$$\text{Recipe Approved} \neq \text{Binding Implemented} \neq \text{Carrier Ready} \neq \text{Pair Executable}$$

$$\text{source object exists} \neq \text{ANS-BASE / ANS-RAG / ANS-FINAL 실행에서 그 값을 authoritative하게 materialize할 carrier가 이미 존재함}$$

| Supplemental Key | Canonical Recipe | Phase B State | Current Source Object | Variant Execution Carrier Status | 판정 및 세부 사유 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `INPUT_CONTEXT` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `LoadedRunBundle.cases` (`CaseResult.case_id`, `CaseResult.input_sha256`) | `RUN_BUNDLE` (Run 완성 시 실재; 3-Variant actual run 미실행) | 실제 완료된 Run bundle이 존재할 때 carrier 계약은 실재함. 단, `ANS-BASE`/`ANS-RAG`/`ANS-FINAL` 3-Variant actual execution materialization은 미실행 (`NOT YET EXECUTED`). |
| `SEED` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `DevExecutionRequest.seed` (`SafeInteger`) | `UNRESOLVED` (미구현) | `RagEvaluationRun`에는 seed 자체가 저장되지 않음. Persisted Run에서 seed 재구성 불가. Manifest carrier 별도 구현 전까지 `NOT YET IMPLEMENTED` / `UNRESOLVED`. |
| `PROMPT_STRUCTURE` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `GuidelineGenerationProvenance.prompt_ref` (`ImmutableArtifactRef`) | `UNRESOLVED` (미구현) | RAG Generator provenance 소스는 실재하나, `ANS-BASE`/`ANS-RAG`/`ANS-FINAL` 각 Variant의 actual execution carrier 미구현. `ANS-BASE`가 Generator를 호출한다고 가정할 수 없으며, mandatory controlled variable이므로 임의 `NOT_APPLIED` 불가 (3-Variant exact-match 필수). 실제 baseline 실행 모델 확정 전까지 carrier `UNRESOLVED`. |
| `PARSER` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `GuidelineGenerationProvenance.parser_ref` (`ImmutableArtifactRef`) | `UNRESOLVED` (미구현) | `PROMPT_STRUCTURE`와 동일 원칙. 3 Variant 전체에서 동일 parser identity를 authoritative하게 공급하는 execution carrier 미구현. |
| `SAMPLING_PARAMETERS` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `OpenAIGuidelineGeneratorAdapter` 실제 호출 파라미터 (`temperature=0`) | `UNRESOLVED` (미구현) | RAG runtime actual invocation 소스는 실재하나, 3-Variant 공통 carrier로 미구현. |
| `TOKEN_LIMIT` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `DevVariant.parameters["token_limit"]` + Adapter `_max_output_tokens` | `UNRESOLVED` (미구현) | `DevVariant.parameters`는 Variant config일 뿐 actual 3-run runtime carrier가 아님. config ↔ runtime exact-binding rule 승인됨 (PR #833), 3-variant carrier 미구현. |
| `TIMEOUT` | **DEFINED / APPROVED** | `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` | `DevVariant.parameters["timeout"]` + Adapter `_timeout_seconds` | `UNRESOLVED` (미구현) | `TOKEN_LIMIT`와 동일. config ↔ runtime exact-binding rule 승인됨 (PR #833), 3-variant carrier 미구현. |

#### Future Authoritative Manifest Construction & Exact-Match 책임 분리

현재 PR #808 비교 커널은 `AnswerComparisonSupplementalControls`에 non-None 7개 값이 전달되었는지만 확인하며, 그 값이 올바른 upstream source에서 유래했는지는 커널이 판정하지 않는다 (#808 자체가 carrier의 진위성을 보장하지 않음).

따라서 향후 Authoritative Manifest Construction / Extractor의 책임을 다음과 같이 명시한다:
1. 각 Variant execution의 authoritative source에서 7 supplemental binding을 추출한다.
2. caller가 임의 hash를 직접 공급하지 못하도록 엄격히 차단한다.
3. 각 source identity와 projection/hash recipe 무결성을 검증한다.
4. 그 후에만 `AnswerComparisonSupplementalControls`로 투영한다.
5. 세 Variant pair에서 #808 커널이 투영된 hash 값들의 exact-match를 수행한다.

$$\text{Authoritative Carrier / Extractor Validation} \longrightarrow 7\text{ Supplemental Hashes} \longrightarrow \text{\#808 Exact-Match}$$

---

### 5.2 10 Canonical Recipes 규격 및 1 Explicitly Unresolved Binding

| Key | Binding Field | Canonical Source Object | Owner Issue | Canonical Projection 규격 | Hash Preimage / Hashing Rule | Variant Semantics (ANS-BASE 포함) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `INPUT_CONTEXT` | `supplemental_controls.input_context_hash` | `LoadedRunBundle.cases` (`CaseResult.case_id`, `CaseResult.input_sha256`) | #159 | `{"cases": [{"case_id": c.case_id, "input_sha256": c.input_sha256} for c in cases], "projection_version": "answer-input-context-v1"}` (cases는 UTF-16 BE `case_id` 순 정렬, 질문/환자 민감정보 일절 비포함) | `canonical_sha256(projection)` | Controlled variable (`ANS-BASE`, `ANS-RAG`, `ANS-FINAL` 3개 변형 전수 exact-match 필수) |
| `PROMPT_STRUCTURE` | `supplemental_controls.prompt_structure_hash` | `GuidelineGenerationProvenance.prompt_ref` (`ImmutableArtifactRef`) | #159 | 불변 아티팩트 참조 자체 사용 (`artifact_code="guideline-prompt"`, `version=...`, `content_sha256=...`) | `prompt_ref.content_sha256` (기존 불변 아티팩트 SHA-256 직접 재사용) | Controlled variable (프롬프트 템플릿 지침/구조 불변 결속) |
| `PARSER` | `supplemental_controls.parser_hash` | `GuidelineGenerationProvenance.parser_ref` (`ImmutableArtifactRef`) | #159 | 불변 아티팩트 참조 자체 사용 (`artifact_code="guideline-parser"`, `version=...`, `content_sha256=...`) | `parser_ref.content_sha256` (기존 불변 아티팩트 SHA-256 직접 재사용) | Controlled variable (structured parser 정본 불변 결속) |
| `SEED` | `supplemental_controls.seed_hash` | `DevExecutionRequest.seed` (`SafeInteger`) | #159 | `{"projection_version": "answer-seed-v1", "seed": request.seed}` | `canonical_sha256(projection)` | Controlled variable (재현 가능한 난수 시드 불변 결속) |
| `SAMPLING_PARAMETERS` | `supplemental_controls.sampling_parameters_hash` | `OpenAIGuidelineGeneratorAdapter` 실제 호출 파라미터 (`temperature=0`) | #159 | `{"projection_version": "answer-sampling-parameters-v1", "temperature": "0"}` (실제 invocation에 전달되지 않는 `top_p`, `frequency_penalty`, `presence_penalty` 제외) | `canonical_sha256(projection)` (정규 10진 문자열 인코딩) | Controlled variable (실제 runtime invocation 결속 샘플링 파라미터 불변 결속) |
| `TOKEN_LIMIT` | `supplemental_controls.token_limit_hash` | `DevVariant.parameters["token_limit"]` exact-bound to `OpenAIGuidelineGeneratorAdapter._max_output_tokens` | #159 | `{"max_output_tokens": int(max_output_tokens), "projection_version": "answer-token-limit-v1"}` | `canonical_sha256(projection)` | Controlled variable (정수 토큰 상한 불변 결속) |
| `TIMEOUT` | `supplemental_controls.timeout_hash` | `DevVariant.parameters["timeout"]` exact-bound to `OpenAIGuidelineGeneratorAdapter._timeout_seconds` | #159 | `{"projection_version": "answer-timeout-v1", "timeout_seconds": str(Decimal(str(timeout_seconds)))}` (손실성 정수 ms round 제거, canonical decimal seconds 인코딩) | `canonical_sha256(projection)` | Controlled variable (실제 Provider client timeout 불변 결속) |
| `RETRIEVAL_PIPELINE` | `delta_bindings.retrieval_pipeline_hash` | `VersionedEvidenceRetrievalConfiguration.artifact_ref` (`ImmutableArtifactRef`) | #159 | • `ANS-RAG`/`ANS-FINAL`: 불변 아티팩트 참조 사용 (`selection_limit`은 별도 `EvidenceRetrievalKernelRequest` 소스이므로 configuration projection에서 배제)<br>• `ANS-BASE`: `{"axis": "RETRIEVAL_PIPELINE", "binding_state": "NOT_APPLIED", "projection_version": "answer-authority-binding-v1"}` | • `ANS-RAG`/`ANS-FINAL`: `retrieval_config.artifact_ref.content_sha256` 직접 재사용 (`compute_canonical_hash()` 결속값)<br>• `ANS-BASE`: `canonical_sha256(NOT_APPLIED_projection)` | Allowed delta (`ANS-BASE` vs `ANS-RAG`); Controlled match (`ANS-RAG` vs `ANS-FINAL`) |
| `SOURCE_INDEX` | `delta_bindings.source_index_hash` | `ActualRetrievalModelConfig.knowledge_index_ref` (`ImmutableReference`) / `RagRuntimeReleaseBundle.knowledge_index_manifest_hash` | #159 | • `ANS-RAG`/`ANS-FINAL`: 불변 참조 자체 사용<br>• `ANS-BASE`: `{"axis": "SOURCE_INDEX", "binding_state": "NOT_APPLIED", "projection_version": "answer-authority-binding-v1"}` | • `ANS-RAG`/`ANS-FINAL`: `knowledge_index_ref.hash` 직접 재사용<br>• `ANS-BASE`: `canonical_sha256(NOT_APPLIED_projection)` | Allowed delta (`ANS-BASE` vs `ANS-RAG`); Controlled match (`ANS-RAG` vs `ANS-FINAL`) |
| `RUNTIME_BUNDLE` | `delta_bindings.runtime_bundle_hash` | `RequestGuardRuntimeBindingObservation` (`environment`, `bundle_id`, `bundle_manifest_hash` via PR #828 / #806) | #159 / #806 | • `ANS-RAG`/`ANS-FINAL`: `{"bundle_id": str(obs.bundle_id), "bundle_manifest_hash": obs.bundle_manifest_hash, "environment": obs.environment.value, "projection_version": "answer-runtime-bundle-binding-v1"}` (decision_id, user_id, scope 등 요청별 가변 메타데이터 배제)<br>• `ANS-BASE`: `{"axis": "RUNTIME_BUNDLE", "binding_state": "NOT_APPLIED", "projection_version": "answer-authority-binding-v1"}` | • `ANS-RAG`/`ANS-FINAL`: `canonical_sha256(projection)`<br>• `ANS-BASE`: `canonical_sha256(NOT_APPLIED_projection)` | Allowed delta (`ANS-BASE` vs `ANS-RAG`); Controlled match (`ANS-RAG` vs `ANS-FINAL`) |
| `RETRIEVED_EVIDENCE` | `delta_bindings.retrieved_evidence_hash` | `ProductionGuidelineEvidenceSet` vs `CaseResult.selected_evidence_ids` | #159 / #180 (#760) | **Unresolved Binding (Recipe Unresolved)**<br>• `ANS-RAG`/`ANS-FINAL`: 미확정 (이유: `ProductionGuidelineEvidenceSet`에 `case_id`가 없고 `CaseResult.selected_evidence_ids`는 단순 ID 튜플만 보유하여 실제 generator 소비 evidence content hash를 온전히 증명하지 못함. 향후 authoritative carrier 확정 필요)<br>• `ANS-BASE`: `{"axis": "RETRIEVED_EVIDENCE", "binding_state": "NOT_APPLIED", "projection_version": "answer-authority-binding-v1"}` | • `ANS-RAG`/`ANS-FINAL`: Carrier 및 Stage 확정 전까지 `null` 유지<br>• `ANS-BASE`: `canonical_sha256(NOT_APPLIED_projection)` | Allowed delta (`ANS-BASE` vs `ANS-RAG`); Controlled match (`ANS-RAG` vs `ANS-FINAL`) |

---

## 6. Manifest Self-Hash 계산 규칙 및 책임 분리

`manifest_sha256`은 특정 Evaluation Run에 결속된 **run-scoped manifest artifact integrity hash**로 정의된다.

### 1) 계산 규칙 및 Preimage
1. `manifest_sha256` 계산 시 자기 참조 필드(`manifest_sha256`)는 preimage에서 엄격히 제외한다 (`excluded_top_level_keys=frozenset({"manifest_sha256"})`).
2. 계산식:
   ```python
   manifest_payload["manifest_sha256"] = canonical_sha256(
       manifest_payload,
       excluded_top_level_keys=frozenset({"manifest_sha256"}),
   )
   ```
3. 저장소의 기존 표준 함수 `ai_worker.tasks.evaluation.canonical.canonical_sha256`을 재사용한다.
4. Preimage에는 `schema_id`, `schema_version`, `experiment_id`, `run_id`, `variant_id`, `supplemental_controls`, `delta_bindings`가 모두 포함된다. 동일한 run-scoped manifest payload를 재계산할 경우 100% 동일한 deterministic integrity hash가 생성된다.

### 2) Run-Scoped 결속과 Cross-Run Equality의 책임 분리
- **`run_id` 유지**: 본 Manifest는 특정 evaluation Run의 authority 바인딩을 보증하는 아티팩트이므로 `run_id`를 Manifest 및 해시 프리이미지에서 제거하지 않는다.
- **정상적 차이 발생**:
  $$\text{same authority configuration} + \text{different run\_id} \longrightarrow \text{different manifest\_sha256}$$
  이는 Run 단위 아티팩트 무결성 보장을 위한 지극히 정상적인 동작이다.
- **해시 책임 분리**:
  - `manifest_sha256`: run-scoped artifact integrity 및 exact run binding 증명.
  - 개별 바인딩 해시(`input_context_hash`, `prompt_structure_hash`, ...): #808 비교 커널에서의 pairwise cross-run semantic equality 판정.
- 본 제안 단계에서 별도의 `manifest_semantic_sha256` 필드는 추가하지 않으며, cross-run 동등성 비교는 기존 #808 typed seam의 개별 해시 필드로 충분히 수행된다.

---

## 7. PR #808 Typed Seam 연결 규격

본 Manifest는 검증 후 PR #808에서 기정의된 typed dataclass로 1:1 투영된다:

```text
[AnswerRuntimeAuthorityBindingManifest]
        │
        ├── supplemental_controls
        │         ↓
        │   AnswerComparisonSupplementalControls(
        │       input_context_hash=manifest.supplemental_controls.input_context_hash,
        │       prompt_structure_hash=manifest.supplemental_controls.prompt_structure_hash,
        │       parser_hash=manifest.supplemental_controls.parser_hash,
        │       seed_hash=manifest.supplemental_controls.seed_hash,
        │       sampling_parameters_hash=manifest.supplemental_controls.sampling_parameters_hash,
        │       token_limit_hash=manifest.supplemental_controls.token_limit_hash,
        │       timeout_hash=manifest.supplemental_controls.timeout_hash,
        │   )
        │
        └── delta_bindings
                  ↓
            AnswerComparisonDeltaBindings(
                retrieval_pipeline_hash=manifest.delta_bindings.retrieval_pipeline_hash,
                source_index_hash=manifest.delta_bindings.source_index_hash,
                runtime_bundle_hash=manifest.delta_bindings.runtime_bundle_hash,
                retrieved_evidence_hash=manifest.delta_bindings.retrieved_evidence_hash,
                final_validator_hash=manifest.delta_bindings.final_validator_hash,
                citation_gate_hash=manifest.delta_bindings.citation_gate_hash,
                safety_gate_hash=manifest.delta_bindings.safety_gate_hash,
                release_gate_hash=manifest.delta_bindings.release_gate_hash,
            )
```

---

## 8. 승인 및 전환 요건

### 1) 승인 Evidence
- **Responsible Reviewer**: 권가빈 (`@hazelnutflavoured`, PM / Track F Acceptance)
- **Status**: `APPROVED`
- **PR**: #833
- **Reviewed/Approved Final HEAD**: `ef8a78c8f6ad1c037d203d1b376d899e0dbffbcd`
- **Merge Commit**: `5128cfdee8d9791a76fe6331cea2314420184cc9`
- **Approval Comment**: [`5740823309`](https://github.com/AI-HealthCare-05/AH_05_04/pull/833#issuecomment-5740823309) ("PD-159-20260913 Phase B / PR #833 final HEAD ef8a78c8 APPROVED")
- **Approval Date**: 2026-09-19

### 2) Phase B Python 부분 구현 현황
Phase B contract approval에 따라 다음 범위의 Python 구현과 회귀 검증을 완료했다.

- approved manifest DTO/schema (`rag-eval.answer-runtime-binding-manifest@1.0.0`)
- approved 10 canonical recipe helpers
- manifest self-hash, run identity, variant delta state validation
- approved #808 typed seam projection (`AnswerComparisonSupplementalControls`, `AnswerComparisonDeltaBindings`)
- unresolved binding의 fail-closed 회귀 검증 (`RETRIEVED_EVIDENCE=null`, upstream finalization blocked deltas=`null`)

이는 pure helper/parser 수준의 부분 구현이며, `RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` 10개 항목의 authoritative runtime carrier materialization 완료를 의미하지 않는다.

단 다음은 여전히 구현하지 않는다:
- 7개 supplemental authoritative runtime carrier/extractor
- `RETRIEVED_EVIDENCE` fake carrier
- `ANS-BASE` synthetic supplemental authority
- `FINAL_VALIDATOR`
- `CITATION_GATE`
- `SAFETY_GATE`
- `RELEASE_GATE`
- actual 3-variant execution
- HOLDOUT
- Baseline Freeze
- `PUBLIC_TRACK_F`

### 3) 상류 릴리스 차단
상류 4개 차단 이슈(#180, #807, #799)가 완료되어 정본 영수증이 도입될 때까지 프로덕션 release gate 통과 및 `PUBLIC_TRACK_F` 해제는 엄격히 금지된다.

---

## 9. Phase C-1 Answer Runtime Authority Carrier 및 Materialization Coordinate 검토 (Proposed / Review Required)

### 1) 목적 및 감사 원칙
본 절은 PR #851(Phase B)에서 승인·구현된 10개 Canonical Recipe 및 Manifest 검증/사영 커널을 바탕으로, `ANS-BASE` / `ANS-RAG` / `ANS-FINAL` 실제 3-variant 실행 시 각 recipe의 입력 source object가 어떤 authoritative coordinate로 결속되는지 최신 `develop`(`8f29710547c8a405736d4a50dcac41bc10a90ed3`)을 전수 감사하여 그 경계를 제안(Proposed / Review Required)한다. 책임 리뷰어(`@Jye-rookie`)의 정식 승인 전까지 본 절의 설계는 Frozen 또는 Approved로 확정되지 않는다.

- **핵심 분리 원칙**:
  $$\text{Source Object Exists} \neq \text{Run-bound Authoritative Carrier Exists}$$
  $$\text{Carrier Exists} \neq \text{Run Lookup Coordinate Exists}$$
  $$\text{Recipe Implemented} \neq \text{Manifest Materializable}$$
- **Production Extractor 구현 판정 조건**:
  Recipe Implemented AND Authoritative Source Exists AND Run-bound Carrier Exists AND Exact Lookup Coordinate Exists AND Exact Validation Possible AND Materialization Lifecycle Point Defined (6개 조건 전수 충족 시에만 구현 허용; 1개라도 미충족 시 추정 구현 엄격 금지).

### 2) 15 Authority Bindings 전수 감사 매트릭스 (15-Binding Audit Table)

| Binding | Canonical Source | Recipe Implemented | Production Source Exists | Run-bound Carrier Exists | Exact Lookup Coordinate | Exact Validation | Materialization Point | Current Status | Owner / Blocker |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `INPUT_CONTEXT` | `LoadedRunBundle.cases` (`case_id`, `input_sha256`) | YES (`compute_input_context_binding_hash`) | YES | YES (`LoadedRunBundle`) | YES (`run_id`, `experiment_id`, `variant_id`) | YES (`_validate_loaded_bundle` run ↔ case binding 일치 검증) | `ALL_CASES_COMPLETED_PRE_SEAL` (Proposed) | Recipe: `IMPLEMENTED`<br>Carrier: `AVAILABLE`<br>Materialization: `READY` | #159 (정현우) · 기구현 완료, 중복 래퍼 생성 금지 |
| `SEED` | `DevExecutionRequest.seed` (`SafeInteger`) | YES (`compute_seed_binding_hash`) | YES (`DevExecutionRequest.seed`) | EXECUTION_ONLY (`ResolvedDevExecution.request.seed`) | NO / MISSING (Persisted `RagEvaluationRun`에 seed 필드 부재) | NO (Persisted run 기준 검증 불가) | Execution Request Resolution 시점 (In-Memory Only) | Recipe: `IMPLEMENTED`<br>Carrier: `EXECUTION_ONLY`<br>Materialization: `NOT_READY` | #159 (정현우) |
| `PROMPT_STRUCTURE` | `GuidelineGenerationProvenance.prompt_ref` (`ImmutableArtifactRef`) | YES (`compute_prompt_structure_binding_hash`) | YES (Guideline card/generator candidate provenance) | NO / UNRESOLVED (Case/Run 결속 carrier 부재, `ANS-BASE` 공급 모델 미확정) | NO / MISSING (`RagEvaluationRun.prompt_version`은 단순 식별 문자열) | NO (Run 단위 prompt ref 정합 검증 부재) | Case Guideline Generation 시점 (Run 집계 계약 미정) | Recipe: `IMPLEMENTED`<br>Carrier: `UNRESOLVED`<br>Materialization: `NOT_READY` | #159 (정현우) / #180 |
| `PARSER` | `GuidelineGenerationProvenance.parser_ref` (`ImmutableArtifactRef`) | YES (`compute_parser_binding_hash`) | YES (Guideline structured parser provenance) | NO / UNRESOLVED (`PROMPT_STRUCTURE`와 동일) | NO / MISSING (`RagEvaluationRun`에 parser ref 부재) | NO (Run 단위 parser ref 정합 검증 부재) | Case Guideline Generation 시점 (Run 집계 계약 미정) | Recipe: `IMPLEMENTED`<br>Carrier: `UNRESOLVED`<br>Materialization: `NOT_READY` | #159 (정현우) / #180 |
| `SAMPLING_PARAMETERS` | `OpenAIGuidelineGeneratorAdapter` actual invocation (`temperature=0`) | YES (`compute_sampling_parameters_binding_hash`) | YES (Adapter `_invoke_provider_raw` 하드코딩) | NO / UNRESOLVED (코드 상수 != exact run actual invocation carrier; 관측 캐리어 부재) | NO / MISSING (`RagEvaluationRun` / `CaseResult`에 invocation 파라미터 부재) | NO (실제 호출 관측 검증 부재) | Actual Provider Invocation 시점 (평가 러너 결속 부재) | Recipe: `IMPLEMENTED`<br>Carrier: `UNRESOLVED`<br>Materialization: `NOT_READY` | #159 (정현우) |
| `TOKEN_LIMIT` | `DevVariant.parameters["token_limit"]` exact-bound to `_max_output_tokens` | YES (`compute_token_limit_binding_hash`) | YES (Variant config + Adapter internal attribute) | NO / UNRESOLVED (Config ↔ actual invocation ↔ run 3자 결속 carrier 부재) | NO / MISSING (`RagEvaluationRun`에 token_limit 필드 부재) | NO (런타임 호출 제약 결속 검증 부재) | Variant Config Resolution 시점 (In-Memory Only) | Recipe: `IMPLEMENTED`<br>Carrier: `UNRESOLVED`<br>Materialization: `NOT_READY` | #159 (정현우) |
| `TIMEOUT` | `DevVariant.parameters["timeout"]` exact-bound to `_timeout_seconds` | YES (`compute_timeout_binding_hash`) | YES (Variant config + Adapter internal attribute) | NO / UNRESOLVED (`TOKEN_LIMIT`와 동일) | NO / MISSING (`RagEvaluationRun`에 timeout 필드 부재) | NO (런타임 클라이언트 timeout 결속 검증 부재) | Variant Config Resolution 시점 (In-Memory Only) | Recipe: `IMPLEMENTED`<br>Carrier: `UNRESOLVED`<br>Materialization: `NOT_READY` | #159 (정현우) |
| `RETRIEVAL_PIPELINE` | `VersionedEvidenceRetrievalConfiguration.artifact_ref` (`ImmutableArtifactRef`) | YES (`compute_retrieval_pipeline_binding_hash`) | YES (`VersionedEvidenceRetrievalConfiguration`) | • `ANS-BASE`: YES (`NOT_APPLIED`)<br>• `ANS-RAG`/`ANS-FINAL`: NO / LOOKUP_COORDINATE_MISSING | • `ANS-BASE`: YES (Variant ID)<br>• `ANS-RAG`/`ANS-FINAL`: NO (`retrieval_variant_manifest_hash` != config hash) | • `ANS-BASE`: YES<br>• `ANS-RAG`/`ANS-FINAL`: NO | Retrieval Pipeline Build / Seal 시점 | Recipe: `IMPLEMENTED`<br>Carrier: `LOOKUP_COORDINATE_MISSING` (적용 변형)<br>Materialization: `NOT_READY` | #159 (정현우) |
| `SOURCE_INDEX` | `ActualRetrievalModelConfig.knowledge_index_ref` (`ImmutableReference`) | YES (`compute_source_index_binding_hash`) | YES (`ActualRetrievalModelConfig`) | • `ANS-BASE`: YES (`NOT_APPLIED`)<br>• `ANS-RAG`/`ANS-FINAL`: NO / LOOKUP_COORDINATE_MISSING | • `ANS-BASE`: YES (Variant ID)<br>• `ANS-RAG`/`ANS-FINAL`: NO (`model_config_hash` != index hash) | • `ANS-BASE`: YES<br>• `ANS-RAG`/`ANS-FINAL`: NO | Retrieval Model Config Resolution 시점 | Recipe: `IMPLEMENTED`<br>Carrier: `LOOKUP_COORDINATE_MISSING` (적용 변형)<br>Materialization: `NOT_READY` | #159 (정현우) |
| `RUNTIME_BUNDLE` | `RequestGuardRuntimeBindingObservation` (`environment`, `bundle_id`, `bundle_manifest_hash`) | YES (`compute_runtime_bundle_binding_hash`) | YES (#806 Reader/Table/Observation 실재) | • `ANS-BASE`: YES (`NOT_APPLIED`)<br>• `ANS-RAG`/`ANS-FINAL`: NO / LOOKUP_COORDINATE_MISSING | • `ANS-BASE`: YES (Variant ID)<br>• `ANS-RAG`/`ANS-FINAL`: NO (`RagEvaluationRun`에 #806 ref 부재) | • `ANS-BASE`: YES<br>• `ANS-RAG`/`ANS-FINAL`: NO | Per-Request Guard Evaluation 시점 (평가 브리지 부재) | Recipe: `IMPLEMENTED`<br>Carrier: `LOOKUP_COORDINATE_MISSING` (적용 변형)<br>Materialization: `NOT_READY` | #159 (정현우) / #806 / #162 |
| `RETRIEVED_EVIDENCE` | `ProductionGuidelineEvidenceSet` vs `CaseResult.selected_evidence_ids` | NO (`SOURCE_EXISTS_RECIPE_UNRESOLVED`) | PARTIAL (객체 실재하나 generator 소비 evidence exact hash 미확정) | NO / UNRESOLVED | NO / MISSING | NO | Undefined | Recipe: `UNRESOLVED`<br>Carrier: `UNRESOLVED`<br>Materialization: `NOT_READY` | #159 (정현우) / #180 (#760) |
| `FINAL_VALIDATOR` | `GuidelineGenerationProvenance.validator_ref` / Final Validator receipt | NO (`BLOCKED_BY_UPSTREAM_AUTHORITY`) | NO (Production receipt 부재) | NO / BLOCKED_BY_UPSTREAM | NO / MISSING | NO | Undefined | Recipe: `BLOCKED_BY_UPSTREAM`<br>Carrier: `BLOCKED_BY_UPSTREAM`<br>Materialization: `NOT_READY` | Issue #180 (OPEN) |
| `CITATION_GATE` | `CitationAuthorizationReceipt` | NO (`BLOCKED_BY_UPSTREAM_AUTHORITY`) | NO (순수 dataclass만 존재, production receipt authority 부재) | NO / BLOCKED_BY_UPSTREAM | NO / MISSING | NO | Undefined | Recipe: `BLOCKED_BY_UPSTREAM`<br>Carrier: `BLOCKED_BY_UPSTREAM`<br>Materialization: `NOT_READY` | Issue #799 (OPEN) / Issue #807 / Issue #180 |
| `SAFETY_GATE` | Runtime Safety Gate decision / receipt | NO (`BLOCKED_BY_UPSTREAM_AUTHORITY`) | NO (Runtime safety gate receipt 부재; 평가 Safety metric 재사용 금지) | NO / BLOCKED_BY_UPSTREAM | NO / MISSING | NO | Undefined | Recipe: `BLOCKED_BY_UPSTREAM`<br>Carrier: `BLOCKED_BY_UPSTREAM`<br>Materialization: `NOT_READY` | Issue #180 (OPEN) |
| `RELEASE_GATE` | Runtime Release Gate decision / receipt | NO (`BLOCKED_BY_UPSTREAM_AUTHORITY`) | NO (Runtime release gate receipt 부재; `release_gate.py` 평가 게이트 재사용 금지) | NO / BLOCKED_BY_UPSTREAM | NO / MISSING | NO | Undefined | Recipe: `BLOCKED_BY_UPSTREAM`<br>Carrier: `BLOCKED_BY_UPSTREAM`<br>Materialization: `NOT_READY` | Issue #180 (OPEN) |

### 3) 상태 어휘 (Status Vocabulary) 정의
1. **Recipe Status**:
   - `IMPLEMENTED`: 정규 사영 및 SHA-256 해시 계산 헬퍼가 구현 완료됨 (10개).
   - `UNRESOLVED`: 정본 입력 소스 또는 사영 레시피가 확정되지 않음 (`RETRIEVED_EVIDENCE` 1개).
   - `BLOCKED_BY_UPSTREAM`: 상류 런타임 권위 미완료로 레시피 구현이 차단됨 (4개 finalization axes).
2. **Carrier Status**:
   - `AVAILABLE`: 해당 Run에 결속된 정본 캐리어가 실재함 (`INPUT_CONTEXT`, 비적용 `NOT_APPLIED` 축).
   - `EXECUTION_ONLY`: 실행 시점 인메모리 컨텍스트에는 존재하나, 영속화된 Run 아티팩트에서 복원 불가 (`SEED`).
   - `LOOKUP_COORDINATE_MISSING`: 소스 객체와 리더는 실재하나, `RagEvaluationRun`에 해당 캐리어를 정확히 조회/결속할 불변 참조 좌표가 부재함 (`RETRIEVAL_PIPELINE`, `SOURCE_INDEX`, `RUNTIME_BUNDLE`).
   - `UNRESOLVED`: 케이스별 관측치 및 변형 간 공통 캐리어 구조가 미확정됨 (`PROMPT_STRUCTURE`, `PARSER`, `SAMPLING_PARAMETERS`, `TOKEN_LIMIT`, `TIMEOUT`, `RETRIEVED_EVIDENCE`).
   - `BLOCKED_BY_UPSTREAM`: 상류 프로덕션 권위 미발급으로 캐리어 부재 (4개 finalization axes).
3. **Materialization Status**:
   - `READY`: 6대 판정 조건 전수 충족 (`INPUT_CONTEXT` 1개).
   - `NOT_READY`: 판정 조건 중 1개 이상 미충족 (14개 항목).

### 4) Materialization Coordinate 제안 (Proposed / Review Required)
A. **Manifest 생성 시점 (Lifecycle Point — Proposed)**:
   - 제안 시점: **`ALL_CASES_COMPLETED_PRE_SEAL` (Proposed / Review Required)**
   - 모든 필수 Case 실행이 완료되고, 케이스별 실제 관측치 수집 및 Controlled-Variable 불변성 검증이 통과한 직후, `RagEvaluationRun` 봉인(`result_content_manifest_hash` 계산) 직전에 `AnswerRuntimeAuthorityBindingManifest` 생성을 제안한다.
   - 단, 본 생성 시점은 `@Jye-rookie` 승인 전까지 확정(Frozen/Approved)이 아닌 제안(Proposed) 상태다. 실행 전 생성(Pre-execution)은 실제 호출 관측이 불가능하므로 금지한다.
B. **각 Source Object 출처**:
   - `INPUT_CONTEXT`: `LoadedRunBundle.cases` (`case_id`, `input_sha256`)
   - `SEED`: `ResolvedDevExecution.request.seed`
   - `PROMPT_STRUCTURE`: 케이스 실행 관측 `GuidelineGenerationProvenance.prompt_ref`
   - `PARSER`: 케이스 실행 관측 `GuidelineGenerationProvenance.parser_ref`
   - `SAMPLING_PARAMETERS`: Provider 클라이언트 호출 관측 (`temperature=0`)
   - `TOKEN_LIMIT`: `ResolvedDevExecution.request.answer_variant.parameters["token_limit"]` exact-bound to `_max_output_tokens`
   - `TIMEOUT`: `ResolvedDevExecution.request.answer_variant.parameters["timeout"]` exact-bound to `_timeout_seconds`
   - `RETRIEVAL_PIPELINE`: `ANS-BASE`는 `NOT_APPLIED`, `ANS-RAG`/`ANS-FINAL`은 `VersionedEvidenceRetrievalConfiguration.artifact_ref`
   - `SOURCE_INDEX`: `ANS-BASE`는 `NOT_APPLIED`, `ANS-RAG`/`ANS-FINAL`은 `ActualRetrievalModelConfig.knowledge_index_ref`
   - `RUNTIME_BUNDLE`: `ANS-BASE`는 `NOT_APPLIED`, `ANS-RAG`/`ANS-FINAL`은 `RequestGuardRuntimeBindingObservation` (브리지 확정 시)
   - `RETRIEVED_EVIDENCE`: `null` 유지 (Unresolved)
   - `FINAL_VALIDATOR`, `CITATION_GATE`, `SAFETY_GATE`, `RELEASE_GATE`: `ANS-BASE`/`ANS-RAG`는 `NOT_APPLIED`, `ANS-FINAL`은 `null` 유지 (Blocked)
C. **결속 Run Identity**:
   - `(experiment_id, run_id, variant_id)`의 3-tuple에 exact-bind한다.
   - `validate_answer_runtime_binding_manifest_for_run()`을 통과해야 하며, `manifest_sha256` self-hash 프리이미지에 `run_id`가 필수 포함된다.
D. **Case-level Value의 Run-level Controlled Binding 증명 (Case Aggregation Rule — Proposed)**:
   - **Controlled-Variable Invariant (Proposed / Review Required)**:
     $$\forall c \in \text{CompletedRequiredCases}, \quad \text{value}(c) == \text{value}_0$$
   - 모든 필수 Case에서 관측된 controlled value가 단일 동일 값이어야 한다 (`len({value(c)}) == 1`).
   - 단 하나의 Case라도 드리프트/불일치 발생 시 즉시 `fail-closed` (`EvaluationValidationError(STATE_COMBINATION_INVALID)`).
   - 필수 Case 중 관측치 누락 발생 시 즉시 `fail-closed`.
   - **계약 공백(Contract Gap) 명시**: 현재 `develop`에는 위 집계 및 드리프트 검증 커널이 정본 코드로 부재하므로 계약 공백으로 기록하며, `@Jye-rookie` 승인 전까지 단일 케이스 값을 전체 Run 값으로 임의 과승격하지 않는다.
E. **Missing/Mismatch 다단계 Fail-Closed 경계**:
   - Stage 1 (Preflight): Variant config 파라미터 유효성 검증 실패 시 실행 전 차단.
   - Stage 2 (Post-Case Assembly): Case aggregation 불일치/누락 시 manifest 생성 거부 및 Run `INVALID`.
   - Stage 3 (Manifest Self-Validation): Manifest 내부 DTO 유효성 검사 및 `manifest_sha256` 불일치 시 거부.
   - Stage 4 (Seam Ingestion): Run ↔ Manifest 식별자 불일치 시 Seam 투영 전 거부.
   - Stage 5 (Comparison Kernel): #808 비교 시 supplemental mismatch 또는 non-allowed delta 위반 시 `execution_status = INVALID`, `decision_status = None`으로 차단.
F. **Storage Architecture 및 Publication Coordinate 철회/미확정 안내**:
   - **기존 직접 발행 확정 철회**: `<run_dir>/answer_runtime_binding_manifest.json` 직접 발행 안은 철회한다.
   - **철회 근거**: 현재 `ContentArtifactPath`(`Literal["cases.jsonl", "metrics.json", "suite-results.json", "comparison.json", "gate.json", "failures.jsonl", "report.md"]`) 및 `publisher.py`의 번들 화이트리스트(`_REQUIRED_BUNDLE_FILENAMES`, `_OPTIONAL_BUNDLE_FILENAMES`)에 해당 파일명이 존재하지 않으므로, 승인 없이 번들 내 파일명을 직접 확정하는 것은 불가능하다.
   - **현재 상태**:
     - `MATERIALIZATION_TIMING_PROPOSED`
     - `STORAGE_COORDINATE_UNRESOLVED`
   - **엄격 금지 사항**:
     - `ContentArtifactPath` 무승인 확장 금지
     - `publisher` 화이트리스트 무승인 수정 금지
     - Schema Set 변경 금지
     - `RagEvaluationRun` 필드 추가 금지
     - 임시 sidecar 경로 확정 금지

### 5) Storage Architecture 비교

| 평가 기준 | Option A: Execution-Time Manifest Materialization | Option B: RagEvaluationRun Field Expansion | Option C: Separate Execution Binding Carrier |
| :--- | :--- | :--- | :--- |
| **개념** | Runner가 실행 완료 시점에 직접 `AnswerRuntimeAuthorityBindingManifest`를 생성·보관 | `RagEvaluationRun`에 15개 필드 또는 carrier ref 필드 7~15개를 직접 추가 | 별도의 중간 아티팩트(`answer_carrier_observation.json`)를 두고 Manifest를 2차 생성 |
| **Schema Set 1.5 영향** | **완전 불변** (Schema Set 1.5 해시 변경 없음, `SEPARATE_BINDING_MANIFEST_PREFERRED` 원칙 준수) | **Schema Set 파괴적 변경** (`rag-eval.run` v1.0.0 breaking change, Schema Set 1.5 hash invalidation, 모든 downstream 마이그레이션 필요) | **새 스키마 추가 필요** (중간 캐리어 스키마 승인 및 관리 부담 추가) |
| **Backward Compatibility** | **완전 유지** (기존 Retrieval 및 Grounding/Safety run 영향 0) | **하위 호환성 파괴** (기존 생성된 모든 run artifact와 fixture 파괴) | **유지** (별도 파일이므로 기존 파일 불변) |
| **Actual Authority Fidelity** | **우수** (실행 컨텍스트와 case-level 관측치를 직접 검증하여 run-scoped manifest로 즉시 봉인) | **낮음/왜곡** (Run artifact에 단순 문자열/해시를 적는 것은 미검증 주장에 불과, 실제 carrier authority 보증 불가) | **우수** (원시 관측치를 보존하나 2단계 변환 오버헤드) |
| **Duplication** | **최소** (중간 중복 아티팩트 없이 단일 Manifest로 직행) | **중복 발생** (Run에도 필드가 있고 Manifest에도 필드가 있어 이중 관리) | **중복 심각** (Carrier 파일과 Manifest 파일 간 동일 데이터 이중 보관) |
| **Runtime Coupling** | **낮음** (Evaluation runner의 output 단계에서 결속, 런타임 엔티티와 평가 스키마 분리) | **심각** (상류 런타임 도메인의 변경이 Evaluation 핵심 엔티티에 직접 전파) | **보통** |
| **결론 및 상태** | **권장 제안 (Proposed)**: Schema Set 1.5 불변을 유지하는 가장 단순한 경로이나, publication storage coordinate는 `STORAGE_COORDINATE_UNRESOLVED`로 유지. | **기각 (Option B)**: Schema Set 1.5 불변 원칙 위반, 거대한 연쇄 churn 유발. | **보류 (Option C)**: 중간 캐리어가 반드시 필요한 수명 주기 격차(lifetime gap)가 증명되지 않음. |

### 6) 상류 권위 및 인접 이슈 정합 (Reconciliation)
1. **#806 Request Guard Runtime Binding 정합 및 Lookup Coordinate 공백 명시**:
   - `SqlAlchemyRequestGuardRuntimeBindingReader.read_exact()`는 `RequestGuardRuntimeBindingRef(artifact_code, version, content_sha256)`를 필수로 요구한다.
   - `RagEvaluationRun`에는 `RequestGuardRuntimeBindingRef` 필드가 존재하지 않는다.
   - `RagEvaluationRun`의 런타임 필드(`candidate_bundle_id`, `candidate_guard_decision_id` 등)는 `runtime_eligible=True` + `END_TO_END_RAG` + `LOCAL` 전용 필드이므로, 일반 Answer Quality(`ANSWER_GROUNDING_SAFETY`) 실행에서 재사용할 수 없다.
   - `candidate_guard_decision_id`는 UUID일 뿐 `RequestGuardRuntimeBindingRef`가 아니며, bundle_id/hash만으로 ref를 역산하는 것은 엄격히 금지된다.
   - 따라서 현 상태는 **`RUNTIME_BUNDLE_SOURCE_EXISTS BUT EVALUATION_RUN_LOOKUP_COORDINATE_MISSING`**이다.
2. **#807 Source Use Approval 및 #853 Citation Pin 정합**:
   - #807은 `PATIENT_CITATION` Source Use Approval 정본 권위이고, #853은 런타임 번들 매니페스트 v2에 이를 핀하는 권위다.
   - `SourceUseApproval != Citation Authorization != CITATION_GATE binding`이다.
   - #807 reader가 존재한다고 하여 #159 Manifest 15축 스키마에 `SourceUseApproval`을 임의 추가하지 않는다 (15축 불변 유지).
   - #807 및 #853은 향후 #799 Citation Authorization 발급의 상류 전제 조건으로만 격리·관리된다.
3. **#799 및 #180 Blocker 상태 유지**:
   - Issue #799는 현재 OPEN 상태이며, 순수 `CitationAuthorizationReceipt` dataclass 외에 production receipt 권위가 없다 (`CITATION_GATE` 상류 차단 지속).
   - Issue #180은 현재 OPEN 상태이며, `FINAL_VALIDATOR`, `SAFETY_GATE`, `RELEASE_GATE` 상류 권위가 미구현이다.
   - 특히 `ai_worker/tasks/evaluation/release_gate.py` 평가 릴리스 게이트를 런타임 Release Gate authority로 혼용·재사용하는 것은 엄격히 금지된다.
4. **#860 Guide·Chat Backend Public Projection Seam 정합 (develop `8f297105`)**:
   - 최신 develop(`8f297105`)에서 PR #860을 통해 Guide·Chat Backend public projection seam이 추가되었으나, 이는 #180 final Authorized/Release result의 향후 소비 경계만 정의한다.
   - #860은 Answer Runtime authority carrier, Citation Authorization production receipt, `FINAL_VALIDATOR` authority, `CITATION_GATE` authority, `SAFETY_GATE` authority, `RELEASE_GATE` authority를 일체 제공하지 않는다.
   - 따라서 Phase C-1 15-binding audit 결과와 `IMPLEMENTABLE NOW = NONE` 판정에는 변화가 없다.

### 7) 구현 판정, Stop Condition 및 후속 관리
- **판정 결과**:
  - 15개 Binding 중 6대 조건을 전수 충족하는 항목은 `INPUT_CONTEXT` 1개뿐이다.
  - `INPUT_CONTEXT`는 이미 `compute_input_context_binding_hash(bundle: LoadedRunBundle)`로 구현 완료되어 추가 구현이 불필요하다 (중복 래퍼 클래스 생성 금지).
  - 나머지 14개 Binding은 Lookup Coordinate 부재, Case 집계 계약 부재, 상류 이슈 차단으로 인해 구현 판정 조건을 충족하지 못한다.
- **Stop Condition 충족에 따른 결론**:
  - **`CODE CHANGE = NONE`**
  - 확인되지 않은 authority나 조기 extractor 래퍼를 날조(fabrication)하여 작성하지 않고 정상 fail-closed 상태로 유지한다.
- **후속 Issue 관리 원칙**:
  1. **Case-level Provenance Aggregation**: 새 Issue를 생성하지 않고 **#159 Phase C-2**의 작업 범위로 계속 유지한다.
  2. **Evaluation Run ↔ #806 Bridge**: 새 Issue를 자동 생성하지 않으며, **#162**에 discovery comment로 연결하고 도메인 간 별도 ownership 분리가 필요하다는 합의가 생길 때만 Issue 생성을 검토한다.
