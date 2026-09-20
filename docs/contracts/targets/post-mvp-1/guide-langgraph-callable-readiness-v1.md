# Guide LangGraph Canonical Callable Readiness v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Target audit artifact · #180 |
| 기준 commit | `origin/develop` `24c25ecac5056cc01a1190a71aea8e69fbd74065` |
| 정본 graph | `rag-runtime-v1.md` Guide Graph |
| 범위 | canonical node와 실제 production callable의 의미 단위 대조 |
| 결론 | `THIN_LANGGRAPH_BLOCKED_BY_CANONICAL_CALLABLE_GAPS` |

## 판정 규칙

- `EXACT_CALLABLE`은 하나의 기존 callable이 node 전체 의미를 소유할 때만 사용한다.
- `THIN_ADAPTER_NEEDED`는 의미가 이미 완전하지만 graph carrier만 다른 경우다. 이 audit에는 해당 항목이 없다.
- `MISSING_SEMANTIC_CALLABLE`은 가까운 구현이 있어도 canonical node의 일부가 분리되어 있지 않은 경우다.
- `EXTERNAL_SYNC_BACKEND_BOUNDARY`는 현 Sync Backend service/persistence가 소유하며 AI Worker가 직접 ORM·repository·session을 도입할 수 없는 경계다.
- `actual_symbol`의 `—`는 현재 해당 의미의 callable이 없음을 뜻한다. `nearest:`는 이름이 비슷하지만 exact mapping이 아닌 조사 대상이다.

`claim_citation_validator` 뒤의 Citation Authorization Guard·issuer·finalization은 Application Service Citation Finalizer boundary다. 별도 graph node가 아니며, 이 matrix는 `citation_authorization_guard`를 추가하지 않는다.

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
| reason | `preflight_guide_runtime` validates a RAG-15 approval pack and generator provenance, not the pinned runtime bundle and source freshness required by this node. |
| required_next_action | Define and implement the exact pinned bundle/source freshness kernel only after its input carrier and ownership are approved. |

### `product_safety_overlay_gate_if_bundle_capability_enabled`

| field | value |
| --- | --- |
| contract_semantics | bundle capability가 enable일 때 승인된 product safety overlay authority를 적용하고 fail-closed outcome을 낸다. |
| actual_symbol | `—` |
| input_type | capability flag and approved overlay authority carrier are absent |
| output_type | canonical node outcome is absent |
| side_effect | PURE |
| authority_owner | AI/RAG product-safety policy |
| status | `MISSING_SEMANTIC_CALLABLE` |
| reason | No approved capability authority or production callable exists. Disabled-path pass-through would be a fake node. |
| required_next_action | Product-safety owner must approve capability authority, input/output contract, and failure semantics before implementation. |

### `retrieve_medication_guidance`

| field | value |
| --- | --- |
| contract_semantics | Guide의 pinned medication context와 authority-bound query/evidence selection으로 medication guidance retrieval을 실행한다. |
| actual_symbol | `nearest: ai_worker.tasks.rag.retrieval_runtime:execute_hybrid_retrieve` |
| input_type | canonical Guide query construction and authority-bound retrieval request are absent |
| output_type | canonical Guide retrieval outcome is absent |
| side_effect | READ/WRITE |
| authority_owner | AI/RAG retrieval runtime |
| status | `MISSING_SEMANTIC_CALLABLE` |
| reason | `execute_hybrid_retrieve` owns generic search plus retrieval-run persistence and requires a prebuilt `HybridRetrieveRequest`; it does not construct or own the Guide node's pinned medication query and selection semantics. |
| required_next_action | Define the Guide-specific retrieval request/selection seam without duplicating existing retrieval execution or persistence. |

### `select_medication_guidelines`

| field | value |
| --- | --- |
| contract_semantics | retrieved medication guidance에서 Guide generation에 사용할 guideline set을 canonical safety/authority criteria로 선택한다. |
| actual_symbol | `nearest: ai_worker.tasks.rag.guideline_production_evidence:project_guideline_evidence_from_handoff` |
| input_type | canonical retrieved guidance selection carrier is absent |
| output_type | canonical selected guideline set is absent |
| side_effect | PURE |
| authority_owner | AI/RAG guideline selection policy |
| status | `MISSING_SEMANTIC_CALLABLE` |
| reason | Existing projection consumes an already verified handoff; it neither selects guidelines from retrieval nor owns this node's selection policy. |
| required_next_action | Approve and implement the separated guideline-selection semantic callable. |

### `medication_guideline_safety_filter`

| field | value |
| --- | --- |
| contract_semantics | selected medication guidelines를 medication-specific safety policy로 filter/reject한다. |
| actual_symbol | `—` |
| input_type | canonical selected-guideline carrier is absent |
| output_type | canonical safety-filter outcome is absent |
| side_effect | PURE |
| authority_owner | AI/RAG medication safety policy |
| status | `MISSING_SEMANTIC_CALLABLE` |
| reason | Existing Card finalization validates a completed generation result; it is not a standalone pre-composition medication guideline filter. |
| required_next_action | Approve filter authority and exact input/output before implementing a callable. |

### `conflict_gate`

| field | value |
| --- | --- |
| contract_semantics | safety-filtered guideline/evidence set의 unresolved conflict를 fail-closed로 gate한다. |
| actual_symbol | `—` |
| input_type | canonical filtered guideline/evidence carrier is absent |
| output_type | canonical conflict-gate outcome is absent |
| side_effect | PURE |
| authority_owner | AI/RAG evidence conflict policy |
| status | `MISSING_SEMANTIC_CALLABLE` |
| reason | Existing fallback projections may represent an already-determined conflict but do not perform this distinct canonical gate. |
| required_next_action | Approve conflict criteria and carrier, then implement the smallest dedicated callable. |

### `compose_personalized_guide`

| field | value |
| --- | --- |
| contract_semantics | conflict-gated medication guidance를 사용해 personalized Guide/Card를 compose하고 canonical generation outcome을 낸다. |
| actual_symbol | `nearest: ai_worker.tasks.rag.guide_generation_card_orchestration:orchestrate_guide_generation_card` |
| input_type | canonical conflict-gated guide composition input is absent |
| output_type | canonical composition-only outcome is absent |
| side_effect | EXTERNAL_PROVIDER |
| authority_owner | AI/RAG Guide generation |
| status | `MISSING_SEMANTIC_CALLABLE` |
| reason | Existing orchestration also runs preflight, evidence handoff, production projection, dynamic binding, and Card finalization. Reusing it for this node would re-execute multiple canonical phases. |
| required_next_action | Define a composition-only callable only if the prior canonical node carriers are actually implemented. |

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

The canonical node set is fixed at the 13 entries above. There are no adapter implementations in this slice because no item truthfully qualifies as `THIN_ADAPTER_NEEDED`.

`StateGraph`, `langgraph` dependency, `uv.lock`, fake/no-op/pass-through nodes, Worker/Outbox/ACK changes, Backend DTO/OpenAPI changes, and direct AI Worker persistence are therefore out of scope. The minimum handoffs are: Backend sync read/projection seams for the four external nodes; approved AI/RAG semantic callables for bundle freshness, product safety overlay, retrieval, selection, safety filter, conflict gate, and composition.
