# Guide REQUEST Authority Coordinate Lookup v1

| Item | Value |
| --- | --- |
| Status | Approved Target · exact read implementation added · reviewer/CI pending |
| Issue | #180 B3 |
| Result | `GUIDE_RETRIEVAL_REQUEST_AUTHORITY_LOOKUP_READY` |
| Implementation | `SqlAlchemyGuideEvidenceAuthorityReader.lookup_request_decision_refs()` |

## 1. Purpose

This contract defines the read-only historical lookup that binds one original
Guide request and one selected Source/Snapshot/Member coordinate to the exact
persisted `request_source_decision_ref` and `request_member_decision_ref`.
It does not issue, recompute, or re-judge a REQUEST Decision.

## 2. Exact input coordinate

The lookup requires all of the following pinned facts:

- `request_guard_ref`
- `user_id`
- `request_operation_code`
- `decision_stage=REQUEST`
- `source_snapshot_id`
- `source_snapshot_member_id`
- `source_code`
- `source_version`
- `expected_source_decision_outcome`
- `expected_member_decision_outcome`
- the complete `SourceMemberIdentity`

`SourceMemberIdentity` includes its exact member kind and either the persisted
endpoint/operation coordinate or the persisted artifact code/version coordinate.
Nullable identity fields use exact `IS NULL` matching rather than wildcard
matching.

## 3. Read and verification rules

The guard, Source Decision, and Member Decision rows are read in one
`REPEATABLE READ, READ ONLY` transaction. The lookup uses equality predicates
only and contains no `CURRENT`, latest/newest selection, ordering, limit, or
fallback lookup. The expected Source and Member outcomes are independent,
required equality predicates because each outcome participates in its Decision
artifact's canonical identity. For the Guide retrieval PASS path, both expected
outcomes are explicitly `PASS`.

Before returning references, the existing canonical REQUEST authority identity
functions recompute and verify every persisted artifact identity. The returned
values are the artifact references stored on the matched Source and Member
Decision rows; they are not derived from the caller's coordinate.

## 4. Fail-closed behavior

- Invalid input raises the existing `GuideEvidenceAuthorityReaderError` before DB access.
- A missing guard, Source Decision, or Member Decision returns `None` with no partial result.
- Multiple rows for any exact coordinate raise `GuideEvidenceAuthorityReaderError`.
- Corrupt rows or canonical identity mismatch raise `GuideEvidenceAuthorityReaderError`.
- Database dependency failures raise `GuideEvidenceAuthorityReaderError` without logging row data or connection details.

The lookup does not require a migration. Existing request-guard reference
indexes narrow the historical candidate set, and ambiguity is rejected rather
than resolved by timestamp or row order.

## 5. Non-scope

This contract does not implement B1 carrier completion, B2 production query
fingerprinting, B4 retrieval outcome projection, B5 terminal replay readback,
`retrieve_medication_guidance`, LangGraph, Worker/#577, public DTO/API,
Frontend, or `PUBLIC_TRACK_F` activation.
