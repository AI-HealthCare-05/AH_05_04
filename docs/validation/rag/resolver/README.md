# Issue #170 Resolver Draft/DEV Fixtures

`tests/fixtures/rag/resolver/draft_decision_matrix.json` is a synthetic-only review aid for the unresolved
Issue #170 medication Candidate Resolver. It is `DRAFT_DEV_ONLY`, is not an executable production expectation,
and is not a release or approval Receipt.

The policy envelope is deliberately closed with `environment=LOCAL` and `release_eligible=false`. The legacy
G0 `BLOCKED` and G1–G7 `TBC` labels apply to production/integration policy and receipts; they do not block the
separately approved pure/local Protocol and unit-test slice. Every threshold, mapping version, policy version,
failure taxonomy, and unresolved reason code in these files remains `TBC`. Tests must not load these files as
runtime configuration and must instead construct explicit inline non-release policies. The outcome and DB
projection fields express a non-authoritative draft matrix for review and must not be copied into runtime defaults,
migrations, DTOs, public contracts, or production evaluations.

All Product and Ingredient names and identities use the explicit `SYNTH_*` namespace. The fixture contains no
patient data, prescription data, raw OCR values, Source IDs, credentials, or real medication data.

Each `synthetic_input` contains exactly the Resolver direct-input allowlist: `medication_name` and nullable
`strength_text`. Candidate form, version mismatch, input validation relation, and permutation details are separate
draft evidence/condition metadata with `direct_input=false`; they are not additional Resolver input fields.

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

`internal_candidates`는 Resolver의 평가·진단 evidence이고 `persisted_count`와 동일하지 않다. 승인 Target에 맞춰
`NO_CANDIDATE | INGREDIENT_ONLY | INVALID_INPUT`의 `persisted_count`와 `db_projection.result_rows`는 0으로
고정한다. 후속 #171 Finalizer가 내부 후보를 자동 전량 저장하는 계약으로 해석하면 안 된다.

Resolver business success is written as `SINGLE_CANDIDATE`, never as lifecycle `READY`. A corresponding
`db_projection.search_status=READY` appears only as an explicitly `NON_AUTHORITATIVE_DRAFT` projection into the
unresolved #171 lifecycle boundary.
