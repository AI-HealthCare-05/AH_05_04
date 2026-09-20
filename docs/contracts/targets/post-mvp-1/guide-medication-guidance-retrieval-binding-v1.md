# Guide Medication Guidance Retrieval Binding v1

| Item | Value |
| --- | --- |
| Status | B1/B2/B3/B4/B5 authority seams ready · #180 |
| Re-audited base | `origin/develop` `e03ae267` + B1 branch implementation |
| Canonical node | `retrieve_medication_guidance` in `rag-runtime-v1.md` Guide Graph |
| Result | `THIN_LANGGRAPH_BLOCKED_BY_CANONICAL_CALLABLE_GAPS` |

## 1. Purpose

This document freezes the exact prerequisites that prevent a production
`retrieve_medication_guidance` callable. Production retrieval and the B1-B5
authority seams exist; final callable composition remains incomplete.

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
[B1 verified request binding + B2 query authority]
        ↓
compose_guide_authority_with_production_retrieval()
        ↓
hydrate_guide_retrieval_content()
        ↓
assemble_authoritative_guide_evidence_handoff()
```

The downstream kernels and B1/B2 authoritative inputs are available. The final
composition is not.

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
| `job_id` | `HybridRetrieveRequest` | Backend Sync pinned job/execution persistence | `GuideRuntimeRequestCarrier` exact read (#902) | Yes | None | Backend Sync runtime boundary | Consume unchanged |
| `execution_context_id` | `HybridRetrieveRequest` | Backend Sync execution-context persistence | `GuideRuntimeRequestCarrier` exact read (#902) | Yes | None | Backend Sync runtime boundary | Consume unchanged |
| `prescription_version_id` | `HybridRetrieveRequest` | Backend Sync execution-context persistence | `GuideRuntimeRequestCarrier` exact read (#902) | Yes | None | Backend Sync runtime boundary | Consume unchanged |
| `runtime_release_bundle_id` | `HybridRetrieveRequest` | Backend persisted runtime bundle | `GuideRuntimeRequestCarrier` after canonical bundle verification (#902) | Yes | None | Backend Sync runtime boundary | Consume unchanged |
| `runtime_release_bundle_manifest_hash` | `HybridRetrieveRequest` | Backend persisted runtime bundle | `GuideRuntimeRequestCarrier` after canonical bundle verification (#902) | Yes | None | Backend Sync runtime boundary | Consume unchanged |
| `runtime_execution_manifest_id` | `HybridRetrieveRequest` | Backend runtime execution persistence | `GuideRuntimeRequestCarrier` exact read (#902) | Yes | None | Backend Sync runtime boundary | Consume unchanged |
| `runtime_execution_manifest_hash` | `HybridRetrieveRequest` | Backend runtime execution persistence | `GuideRuntimeRequestCarrier` exact read (#902) | Yes | None | Backend Sync runtime boundary | Consume unchanged |
| `runtime_guard_decision_ref` | `HybridRetrieveRequest` | Pinned execution context | `GuideRuntimeRequestCarrier` exact read (#902) | Yes | None | Backend Sync runtime boundary | Consume unchanged |
| `knowledge_index_id` | `EvidenceSearchExecutionBinding` | Frozen `rag_knowledge_index` identity | `GuideRuntimeRequestCarrier.retrieval_binding.knowledge_index_id` | Yes | None | Backend Sync runtime boundary | Consume the exact persisted UUID binding |
| `allowed_source_snapshot_ids` | `EvidenceSearchExecutionBinding` | Pinned bundle source scope | `GuideRuntimeRequestCarrier.bundle_sources` (#902) | Yes | None | Backend Sync runtime boundary | Consume selected pinned snapshot IDs unchanged |
| `allowed_source_snapshot_member_ids` | `EvidenceSearchExecutionBinding` | Frozen knowledge-index member scope | `GuideRuntimeRequestCarrier.retrieval_binding.member_bindings` | Yes | None | Backend Sync runtime boundary | Consume exact persisted snapshot/member pairs; do not enumerate current members |
| retrieval configuration | `EvidenceSearchExecutionBinding` | Approved `VersionedEvidenceRetrievalConfiguration` supplied at Freeze | `GuideRuntimeRequestCarrier.retrieval_binding.retrieval_configuration` after nested canonical hash verification | Yes | None | Backend Sync runtime boundary | Restore the typed configuration from the verified projection |
| filter snapshot ref | `EvidenceSearchExecutionBinding` | Canonical projection of exact index and member scope at Freeze | `GuideRuntimeRequestCarrier.retrieval_binding.filter_snapshot_ref` | Yes | None | Backend Sync runtime boundary | Consume the immutable ref unchanged |
| source manifest hash | `HybridRetrieveRequest` | Canonical projection of pinned Runtime Bundle Source rows | `GuideRuntimeRequestCarrier.retrieval_binding.source_manifest_hash` | Yes | None | Backend Sync runtime boundary | Consume the dedicated hash unchanged; it remains distinct from `source_scope_manifest_hash` |
| medication identity/context | Guide query projection | Backend execution identification persistence | `GuideRuntimeRequestCarrier.identifications` (#902) | Yes | None | Backend Sync runtime boundary | Consume verified pinned identifications unchanged |
| `normalized_query` | `EvidenceSearchRequest` | #180 B2 pinned snapshot projection | B2 typed producer at the Backend composition root | Yes | None | AI/RAG / Backend Sync | Inject only at the retrieval application boundary |
| `query_fingerprint` | `EvidenceSearchRequest` | B2 typed HMAC producer | Backend composition-root provider plus matching verifier | Yes | None | AI/RAG | Consume the typed result; never expose key material |
| selected `source_snapshot_id` | #697/#672 authority scope | First execution `EvidenceGateSuccess.selected_hits` | `PersistedTerminalReplayPayload.ordered_selected_hits[].provenance` | Yes, exact historical readback | None in B5 | #178 retrieval persistence/readback | Consume the restored coordinate unchanged |
| selected `source_snapshot_member_id` | #697/#672 authority scope | First execution `EvidenceGateSuccess.selected_hits` | `PersistedTerminalReplayPayload.ordered_selected_hits[].provenance` | Yes, exact historical readback | None in B5 | #178 retrieval persistence/readback | Consume the restored coordinate unchanged |
| `source_code` / `source_version` | #697/#672 authority scope | First execution `EvidenceGateSuccess.selected_hits` | `PersistedTerminalReplayPayload.ordered_selected_hits[].provenance` | Yes, exact historical readback | None in B5 | #178 retrieval persistence/readback | Consume the restored coordinate unchanged |
| `member_identity` | #672 authority selection | Pinned first-run selection or B5 replay coordinate | `GuideRequestAuthorityLookupCoordinate` | Lookup ready when supplied | Selected-hit producer/B5 replay | REQUEST authority / Backend persistence | Pass the complete pinned identity unchanged |
| expected Source/Member Decision outcomes | #713 canonical Decision identities | Pinned first-run selection or B5 replay coordinate | `GuideRequestAuthorityLookupCoordinate` | Lookup ready when supplied | Selected-hit producer/B5 replay | REQUEST authority / Backend persistence | Pass both expected outcomes explicitly; Guide retrieval requires `PASS` / `PASS` |
| `request_source_decision_ref` | #672 authority selection | REQUEST authority persistence | `lookup_request_decision_refs()` exact historical read | Yes when exact chain exists | None in B3 | REQUEST authority / Backend persistence | Consume the persisted ref without derivation |
| `request_member_decision_ref` | #672 authority selection | REQUEST authority persistence | `lookup_request_decision_refs()` exact historical read | Yes when exact chain exists | None in B3 | REQUEST authority / Backend persistence | Consume the persisted ref without derivation |
| `ProductionSearchReceipt` | #697 and #760 | First execution #178 retrieval | `PersistedTerminalReplayPayload.search_receipt` exact readback; #180 B4 projection consumes the restored typed receipt | Yes | None in B5 | AI/RAG; #178 retrieval persistence/readback | Preserve the canonical artifact identity and receipt projection |
| `PersistedRetrievalRunReceipt` | #760 | #178 retrieval-run persistence | `HybridRetrieveOutcome.persisted_receipt` | Yes | None by itself | #178 retrieval persistence/readback | Preserve with the future aggregate |
| ordered selected hits / provenance | #697, #711, #760 | First execution `EvidenceGateSuccess.selected_hits` | `PersistedTerminalReplayPayload.ordered_selected_hits` with canonical payload/hash verification | Yes | None in B5 | #178 retrieval persistence/readback | Consume without re-search, re-ranking, or current Source reconstruction |

## 5. Blockers

### B1 — `GUIDE_RUNTIME_REQUEST_CARRIER_READY`

#902's verified exact-read foundation now includes one immutable, versioned
`GuideRetrievalBindingManifest` pinned by `AiJobExecutionContext`. The manifest
binds the exact Runtime Release Bundle and Runtime Execution Manifest to:

- `rag_knowledge_index.id` and the index code/version/configuration-hash identity;
- UUID-byte-sorted Source Snapshot/Member pairs from that exact knowledge index;
- the complete approved `VersionedEvidenceRetrievalConfiguration` projection;
- a canonical filter snapshot ref derived at Freeze from the exact index/member scope;
- a dedicated source manifest hash derived from pinned Runtime Bundle Source rows.

Creation verifies the persisted Runtime Bundle canonical manifest before
accepting the Freeze, requires each supplied member pair to exist in the pinned
knowledge index and selected bundle snapshot scope, and never enumerates members
to infer the requested scope. Readback repeats those checks, validates nested
retrieval configuration hashes, recomputes filter/source/manifest hashes, and
fails closed on any mismatch. Legacy Guide execution contexts without a binding
pin remain unreadable through this carrier.

The implementation is split deliberately:

```text
Runtime Release Bundle
        ↓ exact bundle/manifest verification
Runtime Execution Manifest
        ↓ exact immutable references
Guide Retrieval Binding Manifest
        ↓ verified exact-read
GuideRuntimeRequestCarrier.retrieval_binding
```

This resolves the five audited B1 authority gaps without using a completed
retrieval run as first-run authority, reusing `source_scope_manifest_hash`, or
deriving current/latest Source state. The shared projection is an internal
Backend/runtime carrier only; it does not add a public API or DTO.

Owner: Backend Sync runtime boundary.

No B1 persistence/readback follow-up is required. Final callable composition
remains separate work. #577 Worker handoff is not a prerequisite for B1 readback.

### B2 — `GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_READY`

The exact pinned snapshot to `SensitiveText normalized_query` rule is frozen
in `guide-medication-retrieval-query-authority-v1.md`. B2 now supplies the
approved HMAC producer, existing `QueryBindingVerifierPort` result semantics,
secret-free verifier artifact identity, and Backend composition-root typed key
provider. No caller or domain code reads an environment variable or receives
raw key material.

Owner: AI/RAG.

No B2 authority follow-up is required. The final callable must consume the typed
producer and matching verifier without receiving raw key material.

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
#697 while setting `search_success=None`. B5 now restores the exact typed
`search_receipt` and gate outcome before this projection is consumed.

Owner: AI/RAG retrieval and Guide composition boundary.

No B4 follow-up is required. B5 owns the historical payload readback.

### B5 — `GUIDE_RETRIEVAL_TERMINAL_REPLAY_PAYLOAD_READY`

`PersistedTerminalReplayPayload` stores the original canonical
`ProductionSearchReceipt` projection, ordered selected `ProductionSearchHit`
values with immutable Source/Snapshot/Member provenance and coordinates, and
the terminal Evidence Gate status/reason. `SqlAlchemyRetrievalRunStore`
persists that payload and its canonical hash in the same transaction as the
run receipt. Terminal replay verifies the payload hash, the receipt hash, the
search-receipt artifact identity, and the selection manifest before restoring
the typed receipt and gate outcome. Missing or invalid payloads fail closed;
the replay path does not call search, reconstruct from current Source state, or
recompute ranking/RRF.

Owner: #178 retrieval persistence/readback boundary.

No B5 persistence/readback follow-up is required. This does not assemble the
final `retrieve_medication_guidance` callable.

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

- [x] A verified Sync runtime request carrier foundation exists (#902).
- [x] The five B1 exact retrieval authorities are frozen, persisted, integrity-bound, and exact-readable.
- [x] A production Guide medication fingerprint and binding verifier exists.
- [x] Exact selected Source/Member to immutable REQUEST Decision reference lookup exists.
- [x] A `HybridRetrieveOutcome` downstream binding contract is approved.
- [x] Terminal replay restores the complete canonical retrieval result without re-search.

The five authority seams are READY. Final callable assembly remains owned by the
AI/RAG runtime work and is not implemented by this Backend persistence slice.

## 9. Non-scope

The B1/B5 follow-ups add only internal immutable persistence and exact readback.
They add no new search, RRF or Evidence Gate semantics, Backend public DTO/API,
Frontend change, LangGraph graph/state, Guide downstream mapping, guideline
selection, safety filter, conflict gate, Guide composition, or `PUBLIC_TRACK_F`
change.
