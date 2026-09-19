# Decision: MFDS Product Approval Catalog Status Mapping

- **Decision ID**: `PD-800-PHASE-B-STATUS-20260919`
- **Date**: 2026-09-19
- **Status**: Accepted for #800 Phase B implementation
- **Decision Owner**: 정현우 (AI/RAG implementation owner)

## Scope

This decision applies only to the exact authority:

- Source: `MFDS_PRODUCT_APPROVAL`
- Endpoint: `MFDS_PRODUCT_APPROVAL_API`
- Operation: `LIST_APPROVED_PRODUCTS`

## Decision

A Product record is mapped to `CandidateRecordStatus.ACTIVE` only after the
authoritative CURRENT Product Snapshot passes all of these preconditions:

- Snapshot status is `CURRENT`.
- `rejected_record_count` is zero.
- Snapshot provenance validation passes.
- The explicitly selected ingestion run is bound to the Snapshot and is `SUCCEEDED`.
- The existing strict MFDS decoder accepts every persisted `RAW_RESPONSE` artifact.
- The requested `ITEM_SEQ` occurs exactly once across that artifact set.

The mapping is independent of `ITEM_NAME`, `ENTP_NAME`, `ITEM_PERMIT_DATE`,
product-name text, collection time, label presence, or any other record field.

## Non-scope

This decision does not infer `INACTIVE`, cancellation, withdrawal, expiry, or
historical reconciliation. It does not apply to another MFDS operation, does
not grant Source or Catalog approval, does not activate Runtime data, and does
not change PUBLIC_TRACK_F.
