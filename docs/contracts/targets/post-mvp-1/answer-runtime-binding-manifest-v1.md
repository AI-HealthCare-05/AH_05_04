# Answer Runtime Authority Binding Manifest 계약 v1 (#159)

| 항목 | 값 |
| --- | --- |
| 상태 | Approved Target |
| 구현 | Partially implemented — DTO/schema, 10 canonical recipe helpers, manifest/run validation, #808 typed seam projection |
| Decision | [`PD-159-20260913`](../../../governance/decisions/2026-09-13-rag-answer-quality-metrics.md) |
| 추적 Issue | [#159](https://github.com/AI-HealthCare-05/AH_05_04/issues/159) |
| 구현 담당 | 정현우 (`@ceohwj`, AI/RAG Implementation Owner) |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`, PM / Track F Acceptance) — `APPROVED` |
| 승인 Evidence | [PR #833 comment `5740823309`](https://github.com/AI-HealthCare-05/AH_05_04/pull/833#issuecomment-5740823309) · Final HEAD `ef8a78c8f6ad1c037d203d1b376d899e0dbffbcd` · Merge commit `5128cfdee8d9791a76fe6331cea2314420184cc9` |
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

### 2) 구현 착수 Gate (Implementation Gate)
Phase B contract approval completed.

`RECIPE_APPROVED_BINDING_IMPLEMENTATION_PENDING` 10개 항목에 대해 Python implementation may now begin for:
- approved manifest DTO/schema (`rag-eval.answer-runtime-binding-manifest@1.0.0`)
- approved 10 canonical recipe helpers
- authoritative extractor/binding validation (`CanonicalUuid`, 64-hex hash pattern, self-hash integrity)
- approved #808 typed seam projection (`AnswerComparisonSupplementalControls`, `AnswerComparisonDeltaBindings`)
- fail-closed handling for unresolved bindings (`RETRIEVED_EVIDENCE=null`, upstream finalization blocked deltas=`null`)

단 다음은 여전히 구현하지 않는다:
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
