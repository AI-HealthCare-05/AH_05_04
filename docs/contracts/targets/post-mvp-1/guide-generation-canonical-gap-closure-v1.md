# Guide Generation Canonical Gap Closure v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Target audit artifact · #180 |
| audit 기준 | `origin/develop` `37ca993b3b09df242da13a9c1a40a4cadf73137a` |
| 대상 graph | `rag-runtime-v1.md` Guide Graph의 연속된 네 canonical node |
| 구현 범위 | composition-only boundary 하나와 기존 #787 호출 교체 |
| 결론 | `THIN_LANGGRAPH_BLOCKED_BY_CANONICAL_CALLABLE_GAPS` |

이 문서는 target topology와 현 production implementation을 의미 단위로 대조한 단일
정본이다. node 이름을 맞추기 위한 pass-through, policy-free fake callable, legacy
`EvidenceGateOutcome` 재구성은 만들지 않는다. 이 audit은 LangGraph, retrieval, B1/B2/B5,
Citation Authorization, release, persistence 및 public runtime을 변경하지 않는다.

## 판정 규칙

- `EXACT_EXISTING`은 callable 하나가 node의 전체 의미, 입력·출력 경계 및 실행 위치를
  이미 소유할 때만 가능하다.
- `THIN_EXTRACTION`은 production 의미가 이미 존재하며 provider·authority·fallback·Card
  finalization의 관측 가능한 동작을 바꾸지 않는 최소 추출일 때만 가능하다.
- `CONTRACT_ALIGNMENT_REQUIRED`는 target node 순서 또는 carrier와 현재 승인된 runtime
  architecture가 다르거나 필요한 policy authority가 없는 경우다.

### `four-node-semantic-matrix`

| canonical_node_id | target_contract_semantics | current_actual_semantics | nearest_symbol | current_input | current_output | current_position_in_execution_order | side_effect | authority_owner | classification | reason | safe_action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| select_medication_guidelines | retrieved medication guidance에서 canonical safety/authority criteria로 generation guideline set을 선택한다. | #760 verified handoff의 이미 승인된 selection을 `ProductionGuidelineEvidenceSet`으로 최소 projection·결정적 정렬한다. | `ai_worker.tasks.rag.guideline_production_evidence:project_guideline_evidence_from_handoff` | `VerifiedGuideEvidenceHandoff` | `ProductionGuidelineEvidenceSet` | #787 preflight 뒤, provider 호출 전 | PURE | #760 handoff / #774 projection | CONTRACT_ALIGNMENT_REQUIRED | retrieval outcome에서 guideline을 고르는 policy·carrier가 없고 #774는 selection·filtering·ranking·freshness/conflict 재판정을 하지 않는다. | selection criteria와 typed carrier를 승인한 뒤 별도 pure kernel을 설계한다. |
| medication_guideline_safety_filter | selected guideline을 medication-specific safety policy로 filter/reject한다. | Card finalizer가 생성된 `GuidelineCardDraft`의 shape, Korean-safe text, forbidden action, scope/action template, citation binding 및 medication membership을 검증한다. | `ai_worker.tasks.rag.guideline_card:_is_valid_draft_shape` | `GuidelineCardDraft`, `VersionedGuidelinePolicy` | Card validation result / fallback outcome | provider 호출·dynamic authority 뒤, `finalize_guideline_card()` 내부 | PURE | RAG-15 Guideline Card safety policy | CONTRACT_ALIGNMENT_REQUIRED | existing safety semantics are post-generation draft validation, not a pre-composition selected-guideline filter. | target/current order와 filter input/output authority를 freeze하고 Safety 승인 전에는 validator를 이동·복사하지 않는다. |
| conflict_gate | safety-filtered guideline/evidence set의 unresolved conflict를 fail-closed로 gate한다. | `EVIDENCE_CONFLICTED`와 `CONFLICTING_EVIDENCE`는 이미 판정된 conflict를 Card fallback vocabulary로 표현할 수 있으나 production detector는 아니다. | `ai_worker.tasks.rag.guideline_card:GuidelineCardReason.EVIDENCE_CONFLICTED` | existing Card outcome/fallback vocabulary | existing Card outcome/fallback vocabulary | no standalone production conflict detection position | PURE | AI/RAG evidence conflict policy | CONTRACT_ALIGNMENT_REQUIRED | approved conflict criteria와 filtered carrier가 없으며 legacy Evidence Gate를 #774 production input으로 복원할 수 없다. | conflict criteria, owner, typed input/output을 승인한 뒤 fail-closed kernel을 설계한다. |
| compose_personalized_guide | conflict-gated medication guidance를 이용해 personalized Guide generation outcome을 compose한다. | `GuidelineGenerationRequest`를 provider에 전달하고 결과를 그대로 반환한다. | `ai_worker.tasks.rag.guide_personalized_composition:compose_personalized_guide` | `GuidelineGenerationRequest`, `GuidelineGeneratorPort` | `GuidelineCardDraft \| GuidelineGenerationFailure` | #787 projection·request construction 뒤, dynamic authority·Card finalization 전 | EXTERNAL_PROVIDER | AI/RAG Guide generation | THIN_EXTRACTION | existing direct provider invocation is the exact composition-only semantic core; #787 전체는 upstream orchestration과 authority/finalization도 수행하므로 compose node가 아니다. | #787 direct invocation을 extracted callable 한 번으로 교체한다. |

### `select_medication_guidelines`

`project_guideline_evidence_from_handoff != select_medication_guidelines`.

#774는 verified handoff의 전체 production evidence를 projection할 뿐 generation 전 일부
guideline을 선택하지 않는다. Generator의 citation/draft output과 #781의 request-scoped
binding도 selection policy가 아니다. 임의 Top-K, retrieval rank 재사용, scope filter 또는
freshness 재판정은 승인된 semantics가 아니므로 구현하지 않는다.

### `medication_guideline_safety_filter`

`post-generation Card validation != medication_guideline_safety_filter`.

`_is_valid_draft_shape()` 및 `_FORBIDDEN_ACTION_RES`, `_SCOPE_PATTERNS`,
`_ACTION_CLASS_BY_SCOPE`, `_ACTION_TEXT_BY_CLASS`는 draft/claim text를 검사한다. 이들은
selected evidence를 filter하지 않으며 current execution order에서는 provider generation 뒤다.
따라서 같은 regex를 pre-generation filter로 복사하거나 finalizer를 앞으로 이동하지 않는다.

### `conflict_gate`

`EVIDENCE_CONFLICTED != conflict_gate`.

`GuidelineCardReason.EVIDENCE_CONFLICTED`와
`GuidelineFallbackCode.CONFLICTING_EVIDENCE`는 result vocabulary다. #774 이후 production
request에는 legacy `EvidenceGateOutcome`가 없으며 #760 rejection은 upstream stop으로
끝난다. conflict detector input, criteria, outcome owner가 승인되기 전에는 callable을
만들지 않는다.

### `compose_personalized_guide`

`orchestrate_guide_generation_card != compose_personalized_guide`.

`compose_personalized_guide()`는 `GuidelineGenerationRequest`와 generator만 받고
`generator.generate()`를 정확히 한 번 await한 결과를 재해석 없이 반환한다. request 구성은
#787에 남고, dynamic binding, fallback mapping, Card finalization 및 policy selection은
추출된 callable에 없다. #787은 이 callable을 실제로 소비한다.

## Execution-order alignment

Target order is `select_medication_guidelines → medication_guideline_safety_filter →
conflict_gate → compose_personalized_guide`. Current approved path is
`#774 projection → GuidelineGenerationRequest → compose_personalized_guide → #781 dynamic
binding → finalize_guideline_card`, with draft safety validation inside finalization. The
first three target phases therefore remain contract-alignment blockers; this PR does not
rewrite the approved order to make the graph look complete.

## Exact next actions

1. Approve retrieval-to-selected-guideline carrier and selection authority before adding
   `select_medication_guidelines`.
2. Approve the pre-composition safety-filter input, outcomes, owner, and order before
   moving or reusing Card validation.
3. Approve conflict evidence criteria and its fail-closed outcome before adding
   `conflict_gate`.
4. Reuse the extracted `compose_personalized_guide` only where a future runtime owns the
   exact approved `GuidelineGenerationRequest`; it does not unblock the first three nodes.
