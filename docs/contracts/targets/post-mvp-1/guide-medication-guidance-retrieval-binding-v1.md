# Guide Medication Guidance Retrieval Binding v1

| Item | Value |
| --- | --- |
| Status | B2 partial authority freeze + B4 pure binding implementation · #180 |
| Re-audited base | `origin/develop` `61bbb579ff195e06a0d28bed7ac8d062410d1ea4` |
| Canonical node | `retrieve_medication_guidance` in `rag-runtime-v1.md` Guide Graph |
| Result | `THIN_LANGGRAPH_BLOCKED_BY_CANONICAL_CALLABLE_GAPS` |

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
| `normalized_query` | `EvidenceSearchRequest` | #180 B2 pinned snapshot projection | B1 carrier still absent | Text policy frozen | Fingerprint and B1 carrier | AI/RAG / Backend Sync | Do not implement before production fingerprint authority exists |
| `query_fingerprint` | `EvidenceSearchRequest` | No approved production producer | None | No | Exact algorithm/key/version/secret/verifier authority | AI/RAG | Keep B2 fail closed at its audited algorithm-authority blocker |
| selected `source_snapshot_id` | #697/#672 authority scope | First execution `EvidenceGateSuccess.selected_hits` | `ProductionSearchHit.provenance`; no replay aggregate | First execution only | Replay readback | #178 retrieval persistence/readback | Restore selected-hit provenance in order |
| selected `source_snapshot_member_id` | #697/#672 authority scope | First execution `EvidenceGateSuccess.selected_hits` | `ProductionSearchHit.provenance`; no replay aggregate | First execution only | Replay readback | #178 retrieval persistence/readback | Restore selected-hit provenance in order |
| `source_code` / `source_version` | #697/#672 authority scope | First execution `EvidenceGateSuccess.selected_hits` | `ProductionSearchHit.provenance`; no replay aggregate | First execution only | Replay readback | #178 retrieval persistence/readback | Restore selected-hit provenance in order |
| `member_identity` | #672 authority selection | Pinned first-run selection or B5 replay coordinate | `GuideRequestAuthorityLookupCoordinate` | Lookup ready when supplied | Selected-hit producer/B5 replay | REQUEST authority / Backend persistence | Pass the complete pinned identity unchanged |
| expected Source/Member Decision outcomes | #713 canonical Decision identities | Pinned first-run selection or B5 replay coordinate | `GuideRequestAuthorityLookupCoordinate` | Lookup ready when supplied | Selected-hit producer/B5 replay | REQUEST authority / Backend persistence | Pass both expected outcomes explicitly; Guide retrieval requires `PASS` / `PASS` |
| `request_source_decision_ref` | #672 authority selection | REQUEST authority persistence | `lookup_request_decision_refs()` exact historical read | Yes when exact chain exists | None in B3 | REQUEST authority / Backend persistence | Consume the persisted ref without derivation |
| `request_member_decision_ref` | #672 authority selection | REQUEST authority persistence | `lookup_request_decision_refs()` exact historical read | Yes when exact chain exists | None in B3 | REQUEST authority / Backend persistence | Consume the persisted ref without derivation |
| `ProductionSearchReceipt` | #697 and #760 | First execution #178 retrieval | #180 B4 approved narrow projection from `HybridRetrieveOutcome.search_receipt` | First execution only | Replay readback | AI/RAG; #178 retrieval persistence/readback | B5 must restore it for replay |
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

### B2 — `GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_BLOCKED_BY_ALGORITHM_AUTHORITY_MISSING`

The exact pinned snapshot to `SensitiveText normalized_query` rule is frozen
in `guide-medication-retrieval-query-authority-v1.md`. The B2-2 audit in
`guide-retrieval-query-fingerprint-authority-v1.md` found no exact approved
production algorithm identifier. Key/version/secret ownership, Worker key
injection, production `QueryBindingVerifierPort`, and verifier artifact
identity are also absent. The generic approved HMAC direction and design-only
examples are insufficient.

Owner: AI/RAG.

Minimum follow-up: provide the B1 carrier and approve/implement the exact
production algorithm, key/version, secret owner/injection, verifier artifact
identity, and query-binding verifier.

### B3 — `GUIDE_RETRIEVAL_REQUEST_AUTHORITY_LOOKUP_READY`

`GuideRequestAuthorityLookupCoordinate` binds the original request guard,
owner, operation, REQUEST stage, selected Source/Snapshot/Member coordinates,
complete member identity, and the independently expected Source and Member
Decision outcomes. The Guide retrieval path supplies `PASS` for both. The
read-only `lookup_request_decision_refs()` adapter resolves that exact historical chain
to the persisted immutable `request_source_decision_ref` and
`request_member_decision_ref` in one repeatable-read transaction.

Owner: REQUEST authority / Backend persistence boundary.

No B3 lookup follow-up is required. The caller must still receive the selected
coordinate from the first-run result or B5 replay payload; this lookup does not
reconstruct a missing selection.

### B4 — `GUIDE_RETRIEVAL_OUTCOME_BINDING_READY`

The approved `project_hybrid_retrieval_for_guide_composition()` preserves the
first-run status, exact search receipt, and exact production gate outcome for
#697 while setting `search_success=None`. Missing `search_receipt` blocks
terminal replay; B5 remains unresolved.

Owner: AI/RAG retrieval and Guide composition boundary.

No B4 follow-up is required. This does not provide the B5 historical payload.

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
- MUST NOT construct an unapproved `HybridRetrieveOutcome` → `ProductionRetrievalOutcome` projection.
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
- [ ] A production Guide medication fingerprint and binding verifier exists.
- [x] Exact selected Source/Member to immutable REQUEST Decision reference lookup exists.
- [x] A `HybridRetrieveOutcome` downstream binding contract is approved.
- [ ] Terminal replay restores the complete canonical retrieval result without re-search.

Partial satisfaction does not permit a READY or callable-status promotion.

## 9. Non-scope

This freeze adds no production retrieval implementation, RRF or Evidence Gate
change, migration, database repository, SQL, Backend DTO/API, Frontend change,
Worker path, LangGraph graph/state, guideline selection, safety filter, conflict
gate, Guide composition, or `PUBLIC_TRACK_F` change.
