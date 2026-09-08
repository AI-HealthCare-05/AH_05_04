# Issue #273 Phase B DEV Dataset Approval Validation Report

> Phase B · DEV Dataset Approved — Dataset-approved, unfrozen, and not a Release decision.

- Phase: `PHASE_B_DATASET_APPROVAL`
- Schema Set Status: `REVIEW_REQUIRED`
- Dataset: `rag-natural-language-retrieval-dev@1.0.0` (`DRAFT`)
- Dataset Manifest SHA-256: `b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2`
- Schema Set: `rag-eval.schema-set@1.3.0` `ca1f324c701dd5e86d811a4430ddbf2d394bd3aa0e7eb0e32dabcb8b63d1e325`
- Candidate Decision: [`docs/governance/decisions/2026-09-05-rag-evaluation-schema-set-1-3-candidate.md`](../../../governance/decisions/2026-09-05-rag-evaluation-schema-set-1-3-candidate.md)
- Gold Review Evidence: `github-pr-341-review-5137833200@1.0.0` `6dd83d9c258499fb0d543870e5a99a913abb0b2dcb3c11e4b72855e43c235776`
- Dataset Approval Evidence: `github-pr-354-review-5139907268@1.0.0` `3b1a90ba0f9a6c06162ce953bdb7e0d504f76074d415a807611812d16ac29896`
- Approval Transition: `DATASET_CUSTODIAN_APPROVAL_RECORDED`; the verified actor is `@phina-io` (`DATASET_CUSTODIAN`).
- Release Eligible: `false`
- Production remains closed.

## Authored DEV Scope

- Planned DEV questions: `60`; created: `60`
- Planned HOLDOUT questions: `40`; created: `0`
- Topics: planned `5`; created `5`
- Expression types: planned `6`; created `6`
- Independent transform-origin groups: planned `20`; created `20`
- Gold records created: `20`; review: `APPROVED`
- Study-wide synthetic corpus records created: `100`
- HOLDOUT Freeze: `NOT_STARTED`
- Actual Adapter: `NOT_IMPLEMENTED`
- Actual Run Artifact: `NOT_CREATED`

한국어 자연어 합성 DEV 질문 60개와 합성 Gold/corpus authoring graph가 저장소에 존재하며, 실제 환자 발화나 실제 제품 데이터가 아니다.
DEV Dataset approval is recorded as APPROVED for the 60 Cases, Evidence Mapping, and Dataset Manifest.
Dataset remains DRAFT and unfrozen; approval does not create or Freeze HOLDOUT content.
Actual retrieval was not run because the actual Adapter is NOT_IMPLEMENTED.
No baseline Metric exists, and no Metric fields are recorded in the machine status.
DEV cannot produce a Release PASS; Production remains closed.

## Blocking Codes

- `BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER`
- `BLOCKED_BY_RAG_14_ADAPTER`
- `WAITING_FOR_HOLDOUT_FREEZE`

## Verification Evidence

| Check | Command | Exit | Result |
| --- | --- | ---: | --- |
| `PHASE_A_DEV_FIXTURE` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py -q` | `0` | 26 passed |
| `PHASE_A_LOADER` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_authoring_identity_loader.py ai_worker/tests/evaluation/test_loaders.py -q` | `0` | 132 passed |
| `PHASE_A_REPORT_PROJECTION` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_validation_report.py -q` | `0` | 50 passed |
| `PHASE_A_SCHEMA_EXPORT` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_schema_exports.py ai_worker/tests/evaluation/test_external_schema_parity.py ai_worker/tests/evaluation/test_provenance_v1_schemas.py -q` | `0` | 94 passed, 7 skipped |
| `PHASE_B_DATASET_APPROVAL_PROVENANCE` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py::test_issue_273_graph_records_the_actual_dataset_custodian_approval_event -q` | `0` | 1 passed |
| `PHASE_B_GOLD_REVIEW_PROVENANCE` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py::test_issue_273_graph_records_only_the_actual_gold_review_event -q` | `0` | 1 passed |

## Boundaries

- Issue [#278](https://github.com/AI-HealthCare-05/AH_05_04/issues/278) is separate and non-blocking for #273.
- No Dataset Freeze, HOLDOUT Freeze, actual baseline completion, Release PASS, or Production readiness is claimed.
- HOLDOUT question content is absent from the repository and remains future protected work.
- The protected runner, actual Adapter, and HOLDOUT Freeze remain future blockers.
- The #158 replay uses a different Dataset and is `NOT_COMPARABLE_DIFFERENT_DATASET`.

Status updated at `2026-09-08T09:30:09.000000Z`. Canonical status SHA-256: `46e4e8821348052ac0f44d30267dea36fbcd8f835b3c21ab42b8c16aa8bf5dea`.
