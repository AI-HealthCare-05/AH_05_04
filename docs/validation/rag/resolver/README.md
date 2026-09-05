# Issue #170 Resolver Draft/DEV Fixtures

`tests/fixtures/rag/resolver/draft_decision_matrix.json` is a synthetic-only review aid for the unresolved
Issue #170 medication Candidate Resolver. It is `DRAFT_DEV_ONLY`, is not an executable production expectation,
and is not a release or approval Receipt.

The policy envelope is deliberately closed with `environment=LOCAL` and `release_eligible=false`. G0 remains
`BLOCKED`; G1–G7 and every threshold, mapping version, policy version, failure taxonomy, and unresolved reason
code remain `TBC`. The outcome and DB projection fields express a non-authoritative draft matrix for review and
must not be copied into runtime defaults, migrations, DTOs, public contracts, or production evaluations.

All Product and Ingredient names and identities use the explicit `SYNTH_*` namespace. The fixture contains no
patient data, prescription data, raw OCR values, Source IDs, credentials, or real medication data.

Every case belongs to the `DEV` partition and has a `lg-dev-resolver-*` leakage group plus named reviewer
provenance. Future `HOLDOUT` and `SAFETY` partitions are reserved with different namespaces and contain no cases
in this fixture; they must not reuse DEV leakage groups or examples. Reviewer provenance also uses distinct
`review-dev-resolver-*`, `review-holdout-resolver-*`, and `review-safety-resolver-*` namespaces. Future HOLDOUT
and SAFETY reviewers remain `TBC` and must be independently assigned and reviewed rather than inferred from DEV.

The draft matrix covers exact and alias singles, cross-stage dedupe, strength and form gates, relevance and
margin gates, dense-only and ingredient-only branches, invalid input axes, version mismatch, invalid port hits,
and deterministic input permutation. Every row records raw/deduped/eligible/persisted counts, a draft outcome or
typed failure, a non-authoritative/TBC DB projection, public candidate count, internal-evidence retention,
unresolved reason code, partition, leakage group, and reviewer provenance.
