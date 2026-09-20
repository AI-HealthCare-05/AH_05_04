# Citation Authorization Authority v1 (#869)

Status: Target — local implementation evidence in progress; CI and responsible reviewer approval pending.

Owner: `@ceohwj`
Responsible reviewer: `@hazelnutflavoured`
Specialist FYI: `@Jye-rookie`

## Authority flow

The Worker issuer accepts only `ValidatedCitationSelection`, its exact
`CitationAuthorizationRequest`, and an explicit timezone-aware `evaluation_time`. It exact-reads the
#806 request-bound authority, rebuilds the pure request, and requires equality before consuming the
selection provenance. Existing REQUEST Source/Member Decisions provide binding coordinates only.

For every request selection the issuer requires the exact #853 Bundle pin and exact #807
`PATIENT_CITATION` approval. Missing authority produces no Decision and no Receipt. A present but
expired/revoked approval, inactive Source, non-CURRENT Snapshot, or ineligible exact member produces
historical FAIL Decisions and a complete Receipt that the existing pure verifier rejects only with
`SELECTION_NOT_AUTHORIZED`.

## Canonical artifacts and persistence

`rag_runtime.citation_authorization_authority` owns deterministic v1 projections for Citation Source
Decision, Citation Member Decision, and Receipt refs. Outcome and observed evaluation facts are part
of the projection; database row IDs and insertion timestamps are not.

The append-only aggregate consists of:

- `rag_citation_authorization_source_decision`
- `rag_citation_authorization_member_decision`
- `rag_citation_authorization_receipt`
- `rag_citation_authorization_receipt_selection`

`request_sha256` is unique on Receipt. Selection order is unique within Receipt. Composite
FK/UNIQUE bindings couple Member Decision to the exact Source Decision `(id, request_sha256,
artifact_content_sha256)` and each Receipt Selection to the exact Receipt, Source Decision, and
Member Decision request/ref coordinates. Receipt's `(bundle_id, bundle_manifest_hash)` is a composite
FK to the exact Runtime Release Bundle identity. The SQLAlchemy Core store uses the caller-owned
transaction and never commits.

## Exact read and idempotency

The first persistence action is an exact Receipt lookup by `request_sha256`. A complete aggregate is
reconstructed, every artifact ref is recomputed, and Receipt/Selection/Source/Member request, FK, ref,
outcome, and copied selection facts are checked for exact equality before the pure Receipt is returned
without reevaluating current Source state. Partial or incompatible persisted content is
`EXISTING_RECEIPT_CORRUPT` and fails closed. No latest/current/newest/version-max selector exists.

## Non-scope

This contract does not change the pure Citation kernel, #806/#807/#853 semantics, finalizer runtime
wiring, Release/Safety Gates, Frontend/Guide API, or `PUBLIC_TRACK_F`.
