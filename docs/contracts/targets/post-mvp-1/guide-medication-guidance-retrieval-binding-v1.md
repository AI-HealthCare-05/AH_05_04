# Guide Medication Guidance Retrieval Binding v1

| Item | Value |
| --- | --- |
| Status | Blocker freeze · #180 · not implemented |
| Re-audited base | `origin/develop` `948a21dd861807c7b6fa5118bb9af36d6b35f6a2` |
| Canonical node | `retrieve_medication_guidance` in `rag-runtime-v1.md` Guide Graph |
| Result | `GUIDE_MEDICATION_GUIDANCE_RETRIEVAL_BLOCKERS_FROZEN` |

## 1. Purpose

This document freezes the exact prerequisites that prevent a production
`retrieve_medication_guidance` callable. Production retrieval exists; the
incomplete boundary is the Guide-specific authoritative binding from pinned
Sync inputs to the existing retrieval and downstream Guide kernels.

This is a target blocker artifact. It neither implements the canonical node nor
promotes any behavior to the current runtime contract.

## 2. Available production components

The following kernels already exist and are not reimplemented by this slice.

| Scope | Existing component |
| --- | --- |
| #178 production retrieval | `ai_worker.tasks.rag.retrieval_runtime.execute_hybrid_retrieve` |
| #672 Sync Guide authority assembly | `ai_worker.tasks.rag.guide_evidence_authority.assemble_sync_guide_evidence_authority` |
| #697 authority × retrieval exact join | `ai_worker.tasks.rag.guide_retrieval_composition.compose_guide_authority_with_production_retrieval` |
| #711 content hydration | `ai_worker.tasks.rag.knowledge_chunk_content_hydration.hydrate_guide_retrieval_content` |
| #760 authoritative evidence handoff | `ai_worker.tasks.rag.authoritative_guide_evidence_handoff.assemble_authoritative_guide_evidence_handoff` |

The currently available chain is:

```text
execute_hybrid_retrieve()
        ↓
[Guide binding blockers]
        ↓
compose_guide_authority_with_production_retrieval()
        ↓
hydrate_guide_retrieval_content()
        ↓
assemble_authoritative_guide_evidence_handoff()
```

The downstream kernels are available. The intermediate authoritative binding is
not.

## 3. Required canonical-node semantics

`retrieve_medication_guidance` must consume pinned medication context and
authority-bound inputs, invoke the existing #178 production retrieval once, and
preserve the resulting authoritative selection for the next canonical node. It
does not select medication guidelines, apply the medication safety filter,
resolve conflicts, compose a Guide, or assemble a final evidence handoff.

The #697 caller-scope invariant remains mandatory:

```text
set(authority binding member keys)
==
set(member keys represented by selected_hits)
```

Loading all bundle authority and silently filtering unused bindings after
retrieval is prohibited. #697 rejects that input as `EXTRA_BINDING`; this slice
does not change #697 to accommodate a broader authority set.

## 4. Authoritative input matrix

| Input / fact | Required by | Current authoritative producer | Current carrier / lookup | Available? | Missing authority | Owner | Minimum follow-up |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `job_id` | `HybridRetrieveRequest` | Backend Sync pinned job/execution persistence | Backend-owned execution-context read | No Guide carrier | Single Sync retrieval carrier | Backend Sync runtime boundary | Project it with the other pinned coordinates |
| `execution_context_id` | `HybridRetrieveRequest` | Backend Sync execution-context persistence | Backend-owned execution-context read | No Guide carrier | Single Sync retrieval carrier | Backend Sync runtime boundary | Project it with the other pinned coordinates |
| `prescription_version_id` | `HybridRetrieveRequest` | Backend Sync execution-context persistence | Backend-owned execution-context read | No Guide carrier | Single Sync retrieval carrier | Backend Sync runtime boundary | Project it with the other pinned coordinates |
| `runtime_release_bundle_id` | `HybridRetrieveRequest` | Backend persisted runtime bundle | Backend bundle configuration read | No Guide carrier | Single Sync retrieval carrier | Backend Sync runtime boundary | Project exact pinned bundle identity |
| `runtime_release_bundle_manifest_hash` | `HybridRetrieveRequest` | Backend persisted runtime bundle | Backend bundle configuration read | No Guide carrier | Single Sync retrieval carrier | Backend Sync runtime boundary | Project exact pinned bundle manifest hash |
| `runtime_execution_manifest_id` | `HybridRetrieveRequest` | Backend runtime execution persistence | No Guide retrieval carrier | No | Single Sync retrieval carrier | Backend Sync runtime boundary | Project exact pinned execution manifest identity |
| `runtime_execution_manifest_hash` | `HybridRetrieveRequest` | Backend runtime execution persistence | No Guide retrieval carrier | No | Single Sync retrieval carrier | Backend Sync runtime boundary | Project exact pinned execution manifest hash |
| `runtime_guard_decision_ref` | `HybridRetrieveRequest` | Pinned execution context | Backend-owned execution-context read | No Guide carrier | Single Sync retrieval carrier | Backend Sync runtime boundary | Project the stored exact reference unchanged |
| `knowledge_index_id` | `EvidenceSearchExecutionBinding` | Pinned runtime/bundle configuration | No Guide retrieval carrier | No | Sync request carrier | Backend Sync runtime boundary | Include it in the canonical projection |
| `allowed_source_snapshot_ids` | `EvidenceSearchExecutionBinding` | Pinned source scope | No Guide retrieval carrier | No | Sync request carrier | Backend Sync runtime boundary | Include the exact stored scope |
| `allowed_source_snapshot_member_ids` | `EvidenceSearchExecutionBinding` | Pinned source scope | No Guide retrieval carrier | No | Sync request carrier | Backend Sync runtime boundary | Include the exact stored member scope |
| retrieval configuration | `EvidenceSearchExecutionBinding` | Pinned runtime/bundle configuration | No Guide retrieval carrier | No | Sync request carrier | Backend Sync runtime boundary | Include the exact versioned configuration |
| filter snapshot ref | `EvidenceSearchExecutionBinding` | Pinned runtime/bundle configuration | No Guide retrieval carrier | No | Sync request carrier | Backend Sync runtime boundary | Include its immutable reference |
| source manifest hash | `HybridRetrieveRequest` | Pinned source/runtime configuration | No Guide retrieval carrier | No | Sync request carrier | Backend Sync runtime boundary | Include the exact stored hash |
| medication identity/context | Guide query projection | Backend execution identification persistence | Backend membership read; no worker-facing projection | No | Guide medication carrier | Backend Sync runtime boundary | Project verified, pinned medication context |
| `normalized_query` | `EvidenceSearchRequest` | No approved deterministic producer | None | No | Query construction rule | AI/RAG | Freeze exact medication-query projection |
| `query_fingerprint` | `EvidenceSearchRequest` | No approved deterministic producer | None | No | Query construction rule | AI/RAG | Freeze fingerprint algorithm and key/version |
| selected `source_snapshot_id` | #697/#672 authority scope | First execution `EvidenceGateSuccess.selected_hits` | `ProductionSearchHit.provenance`; no replay aggregate | First execution only | Replay readback | #178 retrieval persistence/readback | Restore selected-hit provenance in order |
| selected `source_snapshot_member_id` | #697/#672 authority scope | First execution `EvidenceGateSuccess.selected_hits` | `ProductionSearchHit.provenance`; no replay aggregate | First execution only | Replay readback | #178 retrieval persistence/readback | Restore selected-hit provenance in order |
| `source_code` / `source_version` | #697/#672 authority scope | First execution `EvidenceGateSuccess.selected_hits` | `ProductionSearchHit.provenance`; no replay aggregate | First execution only | Replay readback | #178 retrieval persistence/readback | Restore selected-hit provenance in order |
| `member_identity` | #672 authority selection | No selected-hit producer | None | No | REQUEST authority lookup coordinate | REQUEST authority / Backend persistence | Freeze selected member to identity lookup |
| `request_source_decision_ref` | #672 authority selection | REQUEST authority persistence | Exact-ref reader only; no selected-member lookup coordinate | No | REQUEST authority lookup coordinate | REQUEST authority / Backend persistence | Freeze exact immutable ref lookup |
| `request_member_decision_ref` | #672 authority selection | REQUEST authority persistence | Exact-ref reader only; no selected-member lookup coordinate | No | REQUEST authority lookup coordinate | REQUEST authority / Backend persistence | Freeze exact immutable ref lookup |
| `ProductionSearchReceipt` | #697 and #760 | First execution #178 retrieval | `HybridRetrieveOutcome.search_receipt`; no approved downstream binding or replay result | First execution only | Outcome binding and replay | AI/RAG; #178 retrieval persistence/readback | Approve binding and readback |
| `PersistedRetrievalRunReceipt` | #760 | #178 retrieval-run persistence | `HybridRetrieveOutcome.persisted_receipt` | Yes | None by itself | #178 retrieval persistence/readback | Preserve with the future aggregate |
| ordered selected hits / provenance | #697, #711, #760 | First execution `EvidenceGateSuccess.selected_hits` | In-memory result only; no terminal aggregate readback | First execution only | Terminal replay payload | #178 retrieval persistence/readback | Restore full immutable ordered selection |

## 5. Blockers

### B1 — `GUIDE_RUNTIME_REQUEST_CARRIER_MISSING`

`HybridRetrieveRequest` requires the pinned runtime coordinates listed above,
including `search_request` and, when required, `filter_snapshot` and
`source_manifest_hash`. A canonical Sync application carrier/port that provides
them authority-preservingly to the Guide retrieval seam does not exist.

The missing item is a pinned Guide runtime request carrier, not a Worker
callable. #577 Worker handoff is not a prerequisite.

Owner: Backend Sync runtime boundary.

Minimum follow-up: provide one canonical read projection from pinned execution
context, bundle, and identification; the Guide retrieval seam must consume that
carrier rather than caller-supplied raw values.

### B2 — `GUIDE_RETRIEVAL_QUERY_AUTHORITY_MISSING`

There is no approved deterministic rule from pinned medication context to
`SensitiveText normalized_query` and `QueryFingerprint`. `EvidenceSearchRequest`
requires those inputs but does not own their Guide medication meaning.

Owner: AI/RAG.

Minimum follow-up: freeze the input medication carrier, selected fields, query
projection version, NFC and whitespace policy, fingerprint algorithm and
key/version, multi-medication ordering, missing-medication behavior, and
sensitive-text handling.

### B3 — `GUIDE_RETRIEVAL_REQUEST_AUTHORITY_LOOKUP_COORDINATE_MISSING`

`assemble_sync_guide_evidence_authority()` requires selected Source/Member
coordinates, `member_identity`, and exact immutable
`request_source_decision_ref` and `request_member_decision_ref`. A selected hit
can carry Source/Member coordinates, but no canonical lookup coordinate binds
those values to the immutable REQUEST decision references.

Owner: REQUEST authority / Backend persistence boundary.

Minimum follow-up: freeze and provide the exact selected Source/Member
coordinate to immutable request Source/Member Decision reference lookup.

### B4 — `GUIDE_RETRIEVAL_OUTCOME_BINDING_UNRESOLVED`

`execute_hybrid_retrieve()` returns `HybridRetrieveOutcome`; #697 accepts
`ProductionRetrievalOutcome`. Similar field names do not approve a projection.

Owner: AI/RAG retrieval and Guide composition boundary.

Minimum follow-up: separately approve either direct #697 consumption of
`HybridRetrieveOutcome` or a lossless projection contract that owns no new
policy, ranking, or retrieval semantics.

### B5 — `GUIDE_RETRIEVAL_TERMINAL_REPLAY_PAYLOAD_UNAVAILABLE`

On terminal replay, `execute_hybrid_retrieve()` returns the existing persisted
receipt but `search_receipt=None` and `EvidenceGateSuccess(selected_hits=())`.
The downstream Guide kernels require the original `ProductionSearchReceipt` and
ordered selected-hit provenance/coordinates. There is no canonical terminal
aggregate readback that restores them.

Owner: #178 retrieval persistence/readback boundary.

Minimum follow-up: provide an immutable terminal aggregate read model containing
the persisted run receipt, original production search receipt, and ordered
selected `ProductionSearchHit` provenance/coordinates, or an equivalent
canonical projection.

## 6. Forbidden workarounds

- MUST NOT accept caller-asserted raw runtime facts.
- MUST NOT construct a medication query by concatenation, heuristic normalization,
  LLM query generation, or reuse of free-text user input.
- MUST NOT infer the latest or current Source/Member Decision.
- MUST NOT derive immutable Decision references from Source/Member coordinates.
- MUST NOT construct an arbitrary `HybridRetrieveOutcome` → `ProductionRetrievalOutcome` projection.
- MUST NOT load a bundle-authority superset and silently filter unused bindings.
- MUST NOT re-search on terminal replay.
- MUST NOT recompute a search receipt, reconstruct selection from current source
  state, or infer selected hits from a receipt hash.
- MUST NOT treat `selected_hits=()` as a successful selection.
- LangGraph implementation is out of scope.

## 7. #760 boundary

`assemble_authoritative_guide_evidence_handoff()` already exists but is not a
source of the missing upstream values. Its request requires
`persisted_retrieval_receipt`, `retrieval_receipt`, `hydrated_selections`,
`authorities`, `evidence_keys_by_chunk`, and `evaluated_at`; it must not be used
to construct the missing request, query, authority, or replay information.

## 8. Exit criteria

Production `retrieve_medication_guidance` implementation may start only when all
of the following are true:

- [ ] A canonical Sync runtime request carrier exists.
- [ ] A deterministic Guide medication query/fingerprint rule is approved.
- [ ] Exact selected Source/Member to immutable REQUEST Decision reference lookup exists.
- [ ] A `HybridRetrieveOutcome` downstream binding contract is approved.
- [ ] Terminal replay restores the complete canonical retrieval result without re-search.

Partial satisfaction does not permit a READY or callable-status promotion.

## 9. Non-scope

This freeze adds no production retrieval implementation, RRF or Evidence Gate
change, migration, database repository, SQL, Backend DTO/API, Frontend change,
Worker path, LangGraph graph/state, guideline selection, safety filter, conflict
gate, Guide composition, or `PUBLIC_TRACK_F` change.
