# Guide Multi-run Evidence Aggregation v1 (P0-B)

| Item | Value |
| --- | --- |
| Status | Proposed Post-MVP-1 target; pure carrier implementation present |
| Upstream boundary | Per-medication, verified #760 `VerifiedGuideEvidenceHandoff` |
| Downstream boundary | Existing `GuidelineGenerationRequest` / Generator port |
| Out of scope | Retrieval, hydration, #760 assembly, evidence-key persistence, DB, release, API |

## Carrier

`assemble_guide_aggregate_evidence(entries)` accepts a non-empty caller-ordered
tuple of `GuideAggregateEvidenceEntry`:

```text
MedicationIdentityRef
retrieval_run_id: UUID
VerifiedGuideEvidenceHandoff
```

It returns `GuideAggregateEvidence(entries=...)`, whose `GuideAggregateEvidenceRun`
keeps the original child handoff object and its existing #774
`ProductionGuidelineEvidenceSet` projection. The carrier has no aggregate receipt,
handoff, or hash. In particular, every child `handoff_sha256` remains the #760 child
authority anchor and is never substituted by an aggregate value.

## Ordering and duplicate semantics

The caller's validated entry order is output order. The carrier does not rank,
compare relevance, sort runs, select first/last, or deduplicate cross-run evidence.
Repeated immutable evidence remains a member of every child run in which it appears.

The persisted identity anchor is `(source_snapshot_id, evidence_key)`. Repetition of
that anchor is allowed only when `(source_snapshot_member_id, source_code,
source_version, locator, content_sha256)` agrees exactly. A disagreement rejects the
whole aggregate before generation. Receipt references are retained by their child
selection as provenance; no new citation namespace is created.

## Generation boundary

`GuidelineGenerationRequest.evidence` accepts either the existing single
`ProductionGuidelineEvidenceSet` or `GuideAggregateEvidence`. The existing generator
port and `compose_personalized_guide()` callable are reused. Provider projection
iterates child runs structurally without changing the carrier. A successful aggregate
therefore permits one existing generator invocation; a rejected aggregate exposes no
generation-compatible input. `N=1` remains the existing single-set representation.

This contract does not construct the P0-C callable that sequences #919, #711, #760,
aggregation, generation, and release.
