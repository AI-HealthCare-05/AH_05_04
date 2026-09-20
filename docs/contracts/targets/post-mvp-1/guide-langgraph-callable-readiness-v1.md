# Guide LangGraph Canonical Callable Readiness v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Target audit artifact · #180 |
| 기준 commit | `origin/develop` `6e24b2b092edf149410f4766d8c3a9d22e427518` |
| 정본 graph | `rag-runtime-v1.md` Guide Graph |
| 범위 | canonical node와 실제 production callable의 의미 단위 대조 |
| 결론 | `THIN_LANGGRAPH_BLOCKED_BY_CANONICAL_CALLABLE_GAPS` |

## 판정 규칙

- `EXACT_CALLABLE`은 하나의 기존 callable이 node 전체 의미를 소유할 때만 사용한다.
- `THIN_ADAPTER_NEEDED`는 의미가 이미 완전하지만 graph carrier만 다른 경우다.
- `MISSING_SEMANTIC_CALLABLE`은 가까운 구현이 있어도 canonical node의 일부가 분리되어 있지 않은 경우다.
- `EXTERNAL_SYNC_BACKEND_BOUNDARY`는 현 Sync Backend service/persistence가 소유하며 AI Worker가 직접 ORM·repository·session을 도입할 수 없는 경계다.
- `actual_symbol`의 `—`는 현재 해당 의미의 callable이 없음을 뜻한다. `nearest:`는 이름이 비슷하지만 exact mapping이 아닌 조사 대상이다.

`claim_citation_validator` 뒤의 Citation Authorization Guard·issuer·finalization은 Application Service Citation Finalizer boundary다. 별도 graph node가 아니며, 이 matrix는 `citation_authorization_guard`를 추가하지 않는다.

## Topology decisions

| topology item | decision | owner | canonical effect |
| --- | --- | --- | --- |
| `validate_bundle_and_source_freshness` | `RETAIN_STANDALONE` | AI/RAG runtime policy with Backend source state input | independent fail-closed validation node remains required |
| `product_safety_overlay_gate_if_bundle_capability_enabled` | `RETIRE_FROM_CURRENT_MVP_TOPOLOGY` | AI/RAG product-safety policy | no modeled capability or approved overlay authority exists; no node or pass-through wrapper is retained |
| `select_medication_guidelines` | `MERGE_INTO_EXISTING_BOUNDARY` | authoritative retrieval / Evidence selection | selection belongs to the retrieval/evidence boundary; #774 remains projection-only and is not relabeled |
| `medication_guideline_safety_filter` | `RETAIN_STANDALONE_BUT_REPOSITION_AFTER_COMPOSITION` | AI/RAG medication safety policy | post-composition Card exclusion/limitation node; it must not generate new medical guidance |
| `conflict_gate` | `MERGE_INTO_EXISTING_BOUNDARY` | P0 request/source/evidence authority | P0 conflict authority stays in the existing boundary; advanced semantic/NLI detection remains out of scope |

## Canonical callable matrix

### `load_pinned_execution_context`

| field | value |
| --- | --- |
| contract_semantics | 이미 접수 transaction에서 고정된 Guide Full Execution Context만 읽는다. 현재 상태를 재선택하거나 새 context를 만들지 않는다. |
| actual_symbol | `app.repositories.rag_runtime_repository:RagRuntimeRepository.get_execution_context_by_job` |
| input_type | Guide `ai_job_id`와 Backend-owned session/repository |
| output_type | `AiJobExecutionContext \| None` |
| side_effect | READ |
| authority_owner | Backend Sync persistence |
| status | `EXTERNAL_SYNC_BACKEND_BOUNDARY` |
| reason | Repository read는 존재하지만 Worker가 호출할 typed sync boundary/port가 없고, graph state carrier와 worker ownership도 정해지지 않았다. |
| required_next_action | Backend owner가 sync Guide runtime에 제공할 read-only execution-context seam과 carrier를 확정한다. |

### `load_pinned_runtime_release_bundle`

| field | value |
| --- | --- |
| contract_semantics | 고정된 bundle ID와 manifest hash에 정확히 대응하는 persisted runtime release bundle을 읽는다. |
| actual_symbol | `app.services.rag_runtime_bundle_build:load_persisted_bundle_configuration` |
| input_type | Backend `AsyncSession`, bundle identity |
| output_type | persisted bundle configuration |
| side_effect | READ |
| authority_owner | Backend Runtime Bundle persistence |
| status | `EXTERNAL_SYNC_BACKEND_BOUNDARY` |
| reason | Existing service is a Backend session boundary; it is not an AI Worker Guide node callable or graph carrier adapter. |
| required_next_action | Backend owner defines the read-only pinned-bundle projection available to the sync Guide execution seam. |

### `load_verified_medication_identifications`

| field | value |
| --- | --- |
| contract_semantics | pinned execution context의 identification member를 읽고, Guide에 사용 가능한 verified medication identity로 materialize한다. |
| actual_symbol | `app.repositories.rag_runtime_repository:RagRuntimeRepository.list_execution_identifications` |
| input_type | `execution_context_id`와 Backend-owned session/repository |
| output_type | `list[AiJobExecutionIdentification]` |
| side_effect | READ |
| authority_owner | Backend medication-identification persistence |
| status | `EXTERNAL_SYNC_BACKEND_BOUNDARY` |
| reason | Existing method returns membership rows only; a worker-facing verified medication identity projection does not exist. |
| required_next_action | Backend owner supplies one pinned, verified medication identity read projection for sync Guide runtime. |

### `validate_bundle_and_source_freshness`

| field | value |
| --- | --- |
| contract_semantics | pinned bundle·source member·freshness·scope/revocation state를 canonical execution input으로 fail-closed validation한다. |
| actual_symbol | `nearest: ai_worker.tasks.rag.guide_runtime_preflight:preflight_guide_runtime` |
| input_type | required pinned Guide context and bundle/source freshness carrier are absent |
| output_type | canonical node outcome is absent |
| side_effect | PURE |
| authority_owner | AI/RAG runtime policy with Backend source state input |
| status | `MISSING_SEMANTIC_CALLABLE` |
| alignment_decision | `RETAIN_STANDALONE` |
| reason | `preflight_guide_runtime` validates a RAG-15 approval pack and generator provenance, not the pinned runtime bundle and source freshness required by this node. `runtime_bundle_builder` is build-time only: it accepts externally decided `freshness_eligible` and `scope_allowed`; `evaluate_snapshot_use_eligibility()` merely combines those inputs and cannot derive either from `freshness_policy_hash` or `scope_policy_hash`. The exact SourceUsePurpose mapping, policy content/evaluators, and a Worker-readable pinned runtime source-state carrier are absent. |
| required_next_action | `FRESHNESS_RUNTIME_EVALUATOR_MISSING`: approve the exact pinned-member policy identities/content, freshness and scope evaluators, SourceUsePurpose mapping, and Backend-to-Worker carrier; then implement one pure validation kernel that reads no current/latest replacement Source. |

### `retrieve_medication_guidance`

| field | value |
| --- | --- |
| contract_semantics | Guide의 pinned medication context로 authoritative retrieval/Evidence selection과 P0 request/source/evidence conflict authority를 실행한다. |
| actual_symbol | `nearest: ai_worker.tasks.rag.guide_retrieval_outcome_binding:project_hybrid_retrieval_for_guide_composition`; B3 lookup: `SqlAlchemyGuideEvidenceAuthorityReader.lookup_request_decision_refs` |
| input_type | pinned Sync runtime request carrier and production query fingerprint/binding verifier are absent; exact REQUEST decision lookup and terminal replay aggregate are available |
| output_type | canonical Guide retrieval outcome is absent |
| side_effect | READ/WRITE |
| authority_owner | AI/RAG retrieval runtime |
| status | `MISSING_SEMANTIC_CALLABLE` |
| reason | Production `execute_hybrid_retrieve`, the B3 exact historical REQUEST decision lookup, the B4 projection, and B5 exact terminal replay readback exist, but canonical Guide binding remains blocked by `GUIDE_RUNTIME_REQUEST_CARRIER_MISSING` and `GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_BLOCKED_BY_ALGORITHM_AUTHORITY_MISSING`. |
| required_next_action | Do not add a production callable until B1 and B2 fingerprint authority are resolved; then compose the approved B3/B4 seams and B5 replay payload without duplicating retrieval execution or persistence. |

Retrieval binding re-audited at `origin/develop` `dde2f3db90e589ad1958f00ecd6512303fc57baa`.

### `compose_personalized_guide`

| field | value |
| --- | --- |
| contract_semantics | retrieval/evidence-authority-bound medication guidance를 사용해 personalized Guide/Card를 compose하고 canonical generation outcome을 낸다. |
| actual_symbol | `ai_worker.tasks.rag.guide_personalized_composition:compose_personalized_guide` |
| input_type | `GuidelineGenerationRequest`, `GuidelineGeneratorPort` |
| output_type | `GuidelineGenerationResult` (`GuidelineCardDraft \| GuidelineGenerationFailure`) |
| side_effect | EXTERNAL_PROVIDER |
| authority_owner | AI/RAG Guide generation |
| status | `THIN_ADAPTER_NEEDED` |
| reason | #787의 direct provider invocation을 composition-only callable로 최소 추출했다. #787 전체는 preflight, evidence projection, dynamic binding, Card finalization을 함께 수행하므로 여전히 canonical compose node가 아니다. |
| required_next_action | Future graph state가 approved `GuidelineGenerationRequest`를 소유할 때만 이 callable을 재사용한다. The retained safety filter runs after this callable. |

### `medication_guideline_safety_filter`

| field | value |
| --- | --- |
| contract_semantics | generated Guideline Card content에 approved minimal Patient Context를 적용해 제외·제한하고 fail closed한다. 새 의료 guidance를 생성하거나 selection을 재판정하지 않는다. |
| actual_symbol | `—` |
| input_type | `GuidelineCardDraft` plus an approved typed minimal Patient Context carrier are required; the carrier is absent |
| output_type | canonical post-composition Card exclusion/limitation outcome is absent |
| side_effect | PURE |
| authority_owner | AI/RAG medication safety policy |
| status | `MISSING_SEMANTIC_CALLABLE` |
| alignment_decision | `RETAIN_STANDALONE_BUT_REPOSITION_AFTER_COMPOSITION` |
| reason | Patient Context is only an exclusion/caution safety filter for an approved Card, never a Guideline selection input. The existing Guide carrier exposes `patient_context_digest` only; `patient_context_digest` is only a SHA-256 identity and cannot supply confirmed condition, exact allergy target, or pregnancy state. `_is_valid_draft_shape()` is Card shape validation, not this filter. |
| required_next_action | `PATIENT_CONTEXT_TYPED_CARRIER_MISSING`: Backend Sync must supply the pinned, approved minimal Patient Context snapshot and schema version to the Guide runtime seam; then implement this pure callable after `compose_personalized_guide`. |

### `claim_citation_validator`

| field | value |
| --- | --- |
| contract_semantics | generated Guide Card와 authoritative evidence handoff를 exact-match로 project하고 existing claim/citation validator를 실행해 validated selection만 낸다. |
| actual_symbol | `ai_worker.tasks.rag.guide_claim_citation_validation:run_guide_claim_citation_validation` |
| input_type | `GuideClaimCitationValidationRequest` |
| output_type | `GuideClaimCitationValidationOutcome` |
| side_effect | PURE |
| authority_owner | AI/RAG claim/citation validation |
| status | `EXACT_CALLABLE` |
| reason | The callable is one fail-closed validation boundary, calls the underlying kernel once, and explicitly stops before Citation Authorization and release. |
| required_next_action | Reuse directly when a future graph state owns the exact request carrier; keep Citation Finalizer outside the graph. |

### `release_gate`

| field | value |
| --- | --- |
| contract_semantics | authoritative Guide citation-runtime outcome을 safety-result-v2 release/fallback result로 결정적으로 project한다. |
| actual_symbol | `ai_worker.tasks.rag.guide_runtime_release:finalize_guide_runtime_release` |
| input_type | `GuideCitationRuntimeOrchestrationOutcome` |
| output_type | `GuideRuntimeReleaseResult` |
| side_effect | PURE |
| authority_owner | AI/RAG runtime release |
| status | `EXACT_CALLABLE` |
| reason | #893's pure kernel is the stated final patient-release decision and neither rechecks citation authority nor performs persistence. |
| required_next_action | Reuse directly; do not substitute `ai_worker.tasks.evaluation.release_gate`. |

### `persist_guide`

| field | value |
| --- | --- |
| contract_semantics | released runtime result를 current synchronous Backend Guide persistence/public DTO flow에 materialize한다. |
| actual_symbol | `app.services.guides:GuideService.create_guide` |
| input_type | `User`, `CreateGuideRequest`, Backend-owned repository/generator/consent service |
| output_type | `GuideData` |
| side_effect | WRITE/EXTERNAL_PROVIDER |
| authority_owner | Backend Sync Guide service/persistence |
| status | `EXTERNAL_SYNC_BACKEND_BOUNDARY` |
| reason | Current sync service owns persistence but accepts the legacy request and runs its own generation; it has no graph-safe adapter for `GuideRuntimeReleaseResult`. AI Worker DB writes or direct repository use would cross the protected Backend boundary. |
| required_next_action | Backend owner completes the sync projection seam that accepts a released runtime result and persists it without re-execution. |

## Readiness decision

The canonical node set is fixed at the 10 entries above. `compose_personalized_guide` alone truthfully qualifies as `THIN_ADAPTER_NEEDED`. The product-safety overlay is retired, while selection and P0 conflict authority are merged into the retrieval/evidence boundary without creating wrapper nodes.

`StateGraph`, `langgraph` dependency, `uv.lock`, fake/no-op/pass-through nodes, Worker/Outbox/ACK changes, Backend DTO/OpenAPI changes, and direct AI Worker persistence are therefore out of scope. LangGraph cannot be implemented until the retained freshness node, merged retrieval boundary, and repositioned safety callable have their exact required carriers and semantics. The minimum handoffs are Backend sync read/projection seams for freshness, retrieval, and the typed minimal Patient Context carrier.
