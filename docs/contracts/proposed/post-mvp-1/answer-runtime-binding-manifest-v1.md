# Answer Runtime Authority Binding Manifest 계약 v1 (#159)

> **상태**: Proposed · Review Required (`PD-159-20260913` Phase B Proposal - Revised)  
> **책임 작성자**: 정현우 (AI/RAG Implementation Owner)  
> **책임 리뷰어**: 권가빈 (`@hazelnutflavoured`, PM / Track F Acceptance)  
> **상류 권위**: RFC 8785 (JCS Canonical JSON), PR #808 (`AnswerComparisonRunInput` Seam), PR #828 / #806 (`RequestGuardRuntimeBindingObservation`)  
> **연결 이슈**: #159 (Answer Quality Metrics & Comparison), #808 (Pure Typed Comparison Seam), #828 / #806 (Request Guard Runtime Binding), #180 (Track F Runtime Orchestration), #807 (PATIENT_CITATION Source Authority), #799 (Citation Authorization)

---

## 1. 목적과 배경

본 문서는 Issue #159 Answer 3-Pair Comparison(`ANS-BASE--ANS-RAG`, `ANS-RAG--ANS-FINAL`, `ANS-BASE--ANS-FINAL`) 실행 시 요구되는 15개 Authority Binding(7개 Supplemental Controlled Variables + 8개 Allowed Delta Axes) 중, 현재 `develop` 정본에 실재하는 11개 Authority를 표준화된 Canonical Projection 및 Hash Recipe로 결속하는 **Answer Runtime Authority Binding Manifest**의 계약을 정의한다.

### 핵심 설계 원칙
1. **Schema Set 1.5 불변 원칙**: 기존 `RagEvaluationRun` (Schema Set 1.5) 스키마를 임의 확장하거나 수정하지 않는다 (`SEPARATE_BINDING_MANIFEST_PREFERRED`).
2. **기존 Artifact 무결성 원칙**: 3개 pair comparison artifact를 묶는 `answer-comparison-set-manifest`와 본 `answer-runtime-binding-manifest`를 엄격히 분리하여 단일 책임 원칙을 유지한다.
3. **순수 Seam 결속**: PR #808에서 기합의된 `AnswerComparisonSupplementalControls` 및 `AnswerComparisonDeltaBindings` typed seam과 1:1 정합 매핑한다.
4. **결정론적 Content Identity**: 임의 난수 ID(`manifest_id`), 벽시계 타임스탬프(`created_at`), 외부 GitHub 워크플로 메타데이터(`blocker_issue`)를 manifest payload 및 해시 프리이미지에서 배제하여 동일한 authority 입력에 대해 언제나 동일한 self-hash를 보장한다.
5. **기존 Immutable Hash 우선 재사용**: 이미 content-addressed SHA-256을 보유한 불변 아티팩트 참조(`prompt_ref`, `parser_ref`, `retrieval_config_ref`, `knowledge_index_ref`)는 불필요한 래핑 재해싱 없이 기존 불변 해시를 직접 결속한다.
6. **실제 Runtime Invocation 정합**: Provider 호출 시 실제 전달되지 않는 암묵적 기본값(implicit defaults)을 authority로 승격하지 않는다.
7. **명시적 `NOT_APPLIED` 상태**: 특정 Variant에서 해당 축이 실행되지 않은 비적용 상태는 임의의 fake sentinel(`"none"`, `"LOCAL"`)이나 `null`이 아닌 결정론적 typed state로 표현한다.

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
    "run_id": { "type": "string", "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$" },
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

| Variant | 4개 Retrieval Axes (`RETRIEVAL_PIPELINE`, `SOURCE_INDEX`, `RUNTIME_BUNDLE`, `RETRIEVED_EVIDENCE`) | 4개 Finalization Axes (`FINAL_VALIDATOR`, `CITATION_GATE`, `SAFETY_GATE`, `RELEASE_GATE`) |
| :--- | :--- | :--- |
| **`ANS-BASE`** | Canonical `NOT_APPLIED` 해시 4개 바인딩 | Canonical `NOT_APPLIED` 해시 4개 바인딩 |
| **`ANS-RAG`** | 실제 런타임 정본 Authoritative 해시 4개 바인딩 | Canonical `NOT_APPLIED` 해시 4개 바인딩 |
| **`ANS-FINAL`** | 실제 런타임 정본 Authoritative 해시 4개 바인딩 (`ANS-RAG`와 일치) | 상류 정본(#180, #807, #799) 미완료 시 `null` (Blocked); 완료 시 정본 해시 바인딩 |

### 2) 비교 쌍별 Readiness 및 Fail-Closed 작동 원리

기존 PR #808의 `_check_delta_bindings()` 커널 로직을 무수정으로 수용한다:

1. **`ANS-BASE -> ANS-RAG` (RAG 도입 효과 비교)**:
   - **Allowed Deltas**: `RETRIEVAL_PIPELINE`, `SOURCE_INDEX`, `RUNTIME_BUNDLE`, `RETRIEVED_EVIDENCE`
   - **Non-Allowed Deltas**: `FINAL_VALIDATOR`, `CITATION_GATE`, `SAFETY_GATE`, `RELEASE_GATE`
   - **판정**:
     - Baseline(`ANS-BASE`)과 Candidate(`ANS-RAG`) 모두 4개 finalization 축에 동일한 canonical `NOT_APPLIED` 해시를 보유함.
     - 8개 delta 필드 전수가 `None`이 아니므로 `delta_binding_missing = False`.
     - Non-allowed deltas에서 baseline과 candidate 해시가 정확히 일치(`b_val == c_val`)하므로 `unauthorized_delta_changed = False`.
     - **결과**: 상류 finalization authority(#180, #807, #799)가 아직 완성되지 않았더라도, **`ANS-BASE -> ANS-RAG` 비교는 불필요한 차단 없이 정상 실행(Ready) 가능**하다.

2. **`ANS-RAG -> ANS-FINAL` (최종 게이트/공개 효과 비교)**:
   - **Allowed Deltas**: `FINAL_VALIDATOR`, `CITATION_GATE`, `SAFETY_GATE`, `RELEASE_GATE`
   - **Non-Allowed Deltas**: `RETRIEVAL_PIPELINE`, `SOURCE_INDEX`, `RUNTIME_BUNDLE`, `RETRIEVED_EVIDENCE`
   - **판정**:
     - `ANS-FINAL`의 finalization 축은 상류 이슈(#180, #807, #799) 미완료로 인해 `None` (`null`) 상태임.
     - `_check_delta_bindings()`에서 `getattr(candidate.delta_bindings, attr) is None` 감지.
     - **결과**: `delta_binding_missing = True`가 발동하여 `execution_status = INVALID`, `decision_status = None`으로 자동 fail-closed 처리된다.

3. **`ANS-BASE -> ANS-FINAL` (전체 효과 요약 비교)**:
   - `ANS-FINAL`의 finalization 축 누락으로 인해 `ANS-RAG -> ANS-FINAL`과 동일하게 자동 fail-closed 처리된다.

---

## 5. 11 Canonical Recipes 최종 규격

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
| `RETRIEVED_EVIDENCE` | `delta_bindings.retrieved_evidence_hash` | `ProductionGuidelineEvidenceSet` vs `CaseResult.selected_evidence_ids` | #159 / #180 (#760) | • `ANS-RAG`/`ANS-FINAL`: **미확정 (Recipe Unresolved 유지)**. 이유: `ProductionGuidelineEvidenceSet`에 `case_id`가 없고, `CaseResult.selected_evidence_ids`는 문자열 ID 튜플만 보유하여 증거 정본 content hash를 온전히 증명하지 못함. 향후 carrier 확정 필요.<br>• `ANS-BASE`: `{"axis": "RETRIEVED_EVIDENCE", "binding_state": "NOT_APPLIED", "projection_version": "answer-authority-binding-v1"}` | • `ANS-RAG`/`ANS-FINAL`: Carrier 및 Stage 확정 전까지 미생성<br>• `ANS-BASE`: `canonical_sha256(NOT_APPLIED_projection)` | Allowed delta (`ANS-BASE` vs `ANS-RAG`); Controlled match (`ANS-RAG` vs `ANS-FINAL`) |

---

## 6. Manifest Self-Hash 계산 규칙

Manifest 자체의 무결성은 RFC 8785 Canonical JSON 및 SHA-256을 통해 검증된다:
1. `manifest_sha256` 계산 시 자기 참조 필드(`manifest_sha256`)는 preimage에서 엄격히 제외한다 (`excluded_top_level_keys=frozenset({"manifest_sha256"})`).
2. 계산식:
   ```python
   manifest_payload["manifest_sha256"] = canonical_sha256(
       manifest_payload,
       excluded_top_level_keys=frozenset({"manifest_sha256"}),
   )
   ```
3. 저장소의 기존 표준 함수 `ai_worker.tasks.evaluation.canonical.canonical_sha256`을 재사용한다.
4. Payload에 임의 UUID, 타임스탬프, GitHub 이슈 번호가 포함되지 않으므로, 동일 입력에 대해 항상 100% 동일한 바이트와 해시가 생성된다.

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

1. 본 계약 문서는 책임 리뷰어 권가빈(`@hazelnutflavoured`)의 승인 전까지 `Proposed` 상태를 유지한다.
2. 책임 리뷰어의 승인 완료 후:
   - 본 계약은 `targets/post-mvp-1/answer-runtime-binding-manifest-v1.md`로 이동한다.
   - Pydantic DTO, Manifest Extractor, Validator 구현 및 단위/회귀 테스트를 착수한다.
3. 상류 4개 차단 이슈(#180, #807, #799)가 완료되어 정본 영수증이 도입될 때까지 프로덕션 release gate 통과 및 `PUBLIC_TRACK_F` 해제는 엄격히 금지된다.
