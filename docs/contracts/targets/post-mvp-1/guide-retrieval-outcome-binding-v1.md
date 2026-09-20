# Guide Retrieval Outcome Binding v1

| Item | Value |
| --- | --- |
| Status | Approved Target · pure implementation and contract tests added |
| Issue | #180 B4 |
| Implementation | `ai_worker.tasks.rag.guide_retrieval_outcome_binding` |
| Result | `GUIDE_RETRIEVAL_OUTCOME_BINDING_READY` |

## 1. Purpose

This is the narrow, pure boundary from a complete first-run
`HybridRetrieveOutcome` to the semantic surface consumed by unchanged #697
`compose_guide_authority_with_production_retrieval()`. It issues no new
authority and does not convert an arbitrary retrieval result.

## 2. Ready input

The input is READY only when all conditions hold:

- its exact type is `HybridRetrieveOutcome`;
- its status is `SUCCEEDED`;
- `search_receipt` is an exact `ProductionSearchReceipt` with
  `retrieval_execution_status=SUCCEEDED`;
- `gate_outcome` is an exact production `EvidenceGateSuccess` with
  `status=SUCCEEDED`, `reason=ELIGIBLE`, and exact
  `ProductionSearchHit` members in non-empty selected_hits.

Any foreign or malformed input, non-succeeded outcome, missing/invalid receipt,
an empty success gate, or malformed gate is BLOCKED with one typed reason and
no partial projection. Production `evaluate_evidence_gate()` emits
`NO_RESULT/INSUFFICIENT` for zero eligible hits, so an empty success gate is
not a complete first-run production observation.

## 3. Exact field projection

| Source | Target | Rule |
| --- | --- | --- |
| `HybridRetrieveOutcome.status` | `ProductionRetrievalOutcome.status` | Preserve exactly |
| `HybridRetrieveOutcome.search_receipt` | `ProductionRetrievalOutcome.receipt` | Preserve the identical receipt object |
| `HybridRetrieveOutcome.gate_outcome` | `ProductionRetrievalOutcome.gate_outcome` | Preserve the identical gate object |
| absent Hybrid search result | `ProductionRetrievalOutcome.search_success` | `None`; do not fabricate it |
| raw Hybrid message | `ProductionRetrievalOutcome.message` | Empty fixed value; do not propagate provider/dependency diagnostics |

`HybridRetrieveOutcome.status` → `ProductionRetrievalOutcome.status`;
`HybridRetrieveOutcome.search_receipt` → `ProductionRetrievalOutcome.receipt`;
`HybridRetrieveOutcome.gate_outcome` → `ProductionRetrievalOutcome.gate_outcome`;
`ProductionRetrievalOutcome.search_success` → `None` is semantically safe
only because #697 consumes status, receipt, and gate outcome—not
`search_success`. The focused regression test supplies the projected result
to `compose_guide_authority_with_production_retrieval` and proves the existing
successful #697 composition remains unchanged.

## 4. Terminal replay fails closed

A legacy or incomplete replay row without a verified terminal payload is not a
successful Guide projection. B5 now restores the original typed
`search_receipt` and gate outcome only after payload, receipt, artifact, and
selection-manifest verification. Missing or invalid B5 payloads fail before
this projection. If an incomplete outcome nevertheless reaches this binding,
it returns `SEARCH_RECEIPT_REQUIRED`; it MUST NOT reconstruct a receipt, treat
an empty selection as valid, or re-search.

B5 owns historical payload recovery. This B4 contract itself does not define
the readback repository, migration, or persistence semantics.

## 5. Prohibitions and state

The projection MUST NOT fabricate `search_success`. It MUST NOT recompute a receipt
or recompute manifests. It MUST NOT rank, filter, or alter selected hits, execute search,
issue authority, or copy raw diagnostic/provider messages.
It MUST NOT re-search.

#697 remains unchanged. The approved adapter is
`project_hybrid_retrieval_for_guide_composition()`, which returns typed
`READY`/`BLOCKED` outcomes and no partial projected result. The B4 state is
`GUIDE_RETRIEVAL_OUTCOME_BINDING_READY`; it does not resolve B1, B2
fingerprinting, B3, B5, or make `retrieve_medication_guidance` callable.
