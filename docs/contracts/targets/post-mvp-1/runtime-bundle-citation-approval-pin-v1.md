# Runtime Bundle PATIENT_CITATION Approval Pin v1 (#853)

Status: Target — local implementation and PostgreSQL evidence complete; CI and responsible reviewer approval pending.

Owner: `@ceohwj`
Responsible reviewer: `@hazelnutflavoured`
Specialist FYI: `@Jye-rookie`

## Authority and persistence

`rag_runtime_bundle_citation_approval` is an immutable child of one exact
`(bundle_id, bundle_manifest_hash)`. Each row pins one historical #807
`PATIENT_CITATION` Source Use Approval using `source_snapshot_id`, `source_use_approval_id`,
`source_code`, `source_version`, `approval_version`, `environment`, and `purpose`.
`(bundle_id, source_snapshot_id)` is unique. Snapshot and approval deletion are `RESTRICT`;
Bundle lifecycle follows the existing child-row `CASCADE` policy.

Before persistence, the repository reads the identified #807 row and exact-matches every pinned
field plus `Bundle.environment_code`. `RETRIEVAL` is never promoted to `PATIENT_CITATION`.
The repository owns neither commit nor rollback and exposes no semantic update/delete API.

## Canonical manifest

Projection `rag-runtime-bundle-manifest-v2` adds the complete, canonically sorted citation pin set
to `bundle_manifest_hash`. Persisted Bundle, Execution Manifest, Source, Artifact, and citation pin
rows reconstruct the same configuration for hash verification. Changing an approval ID/version,
snapshot, or pin membership changes the Bundle hash; input ordering does not.

## Worker seam and future consumer rule

The AI Worker reader uses equality-only `(bundle_id, bundle_manifest_hash)` lookup, returns the
canonical pin set, and fails closed on database failure, corruption, or ambiguous snapshot rows.
It has no latest/current/newest/MAX selector and does not evaluate approval usability.

Future NEW C must perform:

```text
exact Bundle pair pin read
→ #807 read_exact(identity)
→ ABSENT check
→ observation.is_usable_at(evaluation_time)
```

This contract does not implement Citation Source/Member Decisions, CitationAuthorizationReceipt,
finalizer wiring, Release Gate, or `PUBLIC_TRACK_F`.
