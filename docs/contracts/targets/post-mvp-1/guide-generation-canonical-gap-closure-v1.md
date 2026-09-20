# Guide Generation Canonical Gap Closure v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Target audit artifact · #180 |
| audit 기준 | `origin/develop` `b42c97bcbbd0bf1ab2b7aae26c5a073e5ac80612` |
| 대상 graph | `rag-runtime-v1.md` Guide Graph |
| 구현 범위 | approved topology freeze와 retained post-composition safety callable audit |
| 결론 | `THIN_LANGGRAPH_BLOCKED_BY_CANONICAL_CALLABLE_GAPS` |

이 문서는 target topology와 현 production implementation을 의미 단위로 대조한 단일
정본이다. node 이름을 맞추기 위한 pass-through, policy-free fake callable, legacy
`EvidenceGateOutcome` 재구성은 만들지 않는다. 이 audit은 LangGraph, retrieval, B1/B2/B5,
Citation Authorization, release, persistence 및 public runtime을 변경하지 않는다.

## 판정 규칙

- `RETAIN_STANDALONE`은 canonical 의미와 독립된 fail-closed 실행 위치를 유지한다.
- `MERGE_INTO_EXISTING_BOUNDARY`는 이미 소유된 authority 경계로 의미를 귀속하며 별도 node나
  wrapper를 만들지 않는다.
- `RETIRE_FROM_CURRENT_MVP_TOPOLOGY`는 현재 MVP에서 모델·authority가 없는 node를 제거한다.
- `RETAIN_STANDALONE_BUT_REPOSITION_AFTER_COMPOSITION`은 generation 후 Card 제한 경계로만
  남긴다.

### `topology-decision-matrix`

| node_id | canonical_meaning | current_actual_owner | current_input | current_output | execution_position | side_effect | authority_owner | decision | reason | implementation_required | exact_next_action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| validate_bundle_and_source_freshness | pinned bundle/source member의 freshness·scope·revocation을 fail-closed validation한다. | bundle build eligibility / no execution kernel | pinned execution carrier와 evaluated policy facts 없음 | canonical validation outcome 없음 | retrieval 전 | PURE | AI/RAG runtime policy with Backend source state input | RETAIN_STANDALONE | build-time `freshness_eligible` / `scope_allowed` is not execution-time policy evaluation. | NO — `FRESHNESS_RUNTIME_EVALUATOR_MISSING` | Approve the pinned-member policy evaluators and Worker-readable carrier, then implement one pure kernel. |
| product_safety_overlay_gate_if_bundle_capability_enabled | bundle capability enabled일 때 approved product-safety overlay를 적용한다. | none | no modeled capability | no approved overlay outcome | removed | PURE | AI/RAG product-safety policy | RETIRE_FROM_CURRENT_MVP_TOPOLOGY | No modeled capability or approved overlay authority exists. | NO — do not create a disabled pass-through wrapper | Reintroduce only with a separately approved capability and overlay contract. |
| select_medication_guidelines | retrieved guidance의 generation 대상 selection authority를 적용한다. | authoritative retrieval / Evidence selection | retrieval/evidence authority boundary | authoritative selection within that boundary | retrieval boundary | PURE | authoritative retrieval / Evidence selection | MERGE_INTO_EXISTING_BOUNDARY | `project_guideline_evidence_from_handoff` (#774) remains projection only and MUST NOT be relabeled as selection. | NO — no standalone node | Resolve the retained retrieval callable gaps without duplicating its authority. |
| medication_guideline_safety_filter | approved minimal Patient Context로 generated Guideline Card content만 제외·제한한다. | no exact callable | `GuidelineCardDraft` plus typed minimal Patient Context carrier | post-composition exclusion/limitation outcome | after `compose_personalized_guide` | PURE | AI/RAG medication safety policy | RETAIN_STANDALONE_BUT_REPOSITION_AFTER_COMPOSITION | Patient Context must never generate new medical guidance; `_is_valid_draft_shape()` is not this filter. | NO — `PATIENT_CONTEXT_TYPED_CARRIER_MISSING` | Supply the typed pinned carrier, then implement one pure post-composition callable. |
| conflict_gate | P0 unresolved conflict를 fail closed한다. | existing request/source/evidence authority | request/source/evidence authority | its existing fail-closed authority outcome | retrieval/evidence boundary | PURE | P0 request/source/evidence authority | MERGE_INTO_EXISTING_BOUNDARY | `EVIDENCE_CONFLICTED` vocabulary and legacy `evaluate_evidence_gate()` are not new standalone production detectors. Advanced semantic/NLI detection is out of scope. | NO — no standalone node | Preserve the existing authority boundary when the retained retrieval callable is wired. |

`project_guideline_evidence_from_handoff != select_medication_guidelines`: #774는 verified
handoff projection-only boundary이며 selection으로 relabel하지 않는다.
`EVIDENCE_CONFLICTED != conflict_gate`: result vocabulary와 legacy detector는 P0 authority
boundary 밖의 새 standalone node를 정당화하지 않는다.

### `compose_personalized_guide`

`orchestrate_guide_generation_card != compose_personalized_guide`.

`compose_personalized_guide()`는 `GuidelineGenerationRequest`와 generator만 받고
`generator.generate()`를 정확히 한 번 await한 결과를 재해석 없이 반환한다. request 구성은
#787에 남고, dynamic binding, fallback mapping, Card finalization 및 post-composition safety
filter는
추출된 callable에 없다. #787은 이 callable을 실제로 소비한다.

| field | value |
| --- | --- |
| classification | `THIN_EXTRACTION` |
| canonical meaning | authoritative retrieval/evidence boundary가 공급한 medication guidance로 personalized Guide generation outcome을 compose한다. |
| actual symbol | `ai_worker.tasks.rag.guide_personalized_composition:compose_personalized_guide` |
| input | `GuidelineGenerationRequest`, `GuidelineGeneratorPort` |
| output | `GuidelineCardDraft \| GuidelineGenerationFailure` |
| execution position | retrieval/evidence boundary 뒤, post-composition safety filter 전 |
| authority owner | AI/RAG Guide generation |
| reason | existing direct provider invocation is the exact composition-only semantic core; #787 전체는 upstream orchestration과 authority/finalization도 수행하므로 compose node가 아니다. |
| safe action | retained retrieval input이 준비된 미래 runtime에서 extracted callable을 재사용한다. |

### `medication_guideline_safety_filter`

`post-generation Card validation != medication_guideline_safety_filter`.

`_is_valid_draft_shape()` 및 `_FORBIDDEN_ACTION_RES`, `_SCOPE_PATTERNS`,
`_ACTION_CLASS_BY_SCOPE`, `_ACTION_TEXT_BY_CLASS`는 Card draft shape/text validation이며 이
filter로 재사용하지 않는다. 이 retained node는 `compose_personalized_guide` 뒤에서 승인된
minimal Patient Context에 따라 이미 생성된 Card를 제외하거나 제한할 뿐, guideline을 선택하거나
새 의료 guidance를 생성하지 않는다.

현재 `GuideRuntimeRequestCarrier.patient_context_digest`와
`AiJobExecutionContext.patient_context_digest`만 존재한다. `patient_context_digest` is only a SHA-256 identity; it cannot materialize confirmed condition codes, exact allergy targets, pregnancy
status, or the required snapshot schema version. 따라서 유일한 implementation blocker는
`PATIENT_CONTEXT_TYPED_CARRIER_MISSING`이며 Backend schema, persistence, or worker adapter를
이 slice에서 새로 만들지 않는다.

## Execution-order alignment

Target order is `validate_bundle_and_source_freshness → retrieve_medication_guidance →
compose_personalized_guide → medication_guideline_safety_filter →
claim_citation_validator → release_gate → persist_guide` (preceded by the existing three pinned
load nodes). `select_medication_guidelines`와 `conflict_gate`는 retrieval/evidence boundary에
merge되고, product-safety overlay node는 retire된다. Removed nodes are not preserved as wrappers.

## Exact next actions

1. Approve and supply the exact typed, pinned minimal Patient Context carrier to the Guide runtime
   seam; then implement the pure post-composition safety callable with deterministic exclusion or
   limitation and fail-closed malformed-input behavior.
2. Keep freshness as a retained standalone blocker until its policy evaluator and carrier exist.
3. Keep retrieval blocked until B1, B2, and B5 are resolved; selection and P0 conflict authority
   merge there without #774 relabeling or an advanced conflict detector.
