# Issue #273 Phase B4 DEV Actual Retrieval Evaluation Validation Report

> Phase B4 · DEV Actual Retrieval Evaluation Completed — DEV retrieval execution completed and verified,
> but protected Runner infrastructure enforcement, authorization, Freeze, and Release remain incomplete.

- Phase: `PHASE_B4_DEV_ACTUAL_RUN_COMPLETED`
- Schema Set Status: `REVIEW_REQUIRED`
- Dataset: `rag-natural-language-retrieval-dev@1.0.0` (`DRAFT`)
- Dataset Manifest SHA-256: `b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2`
- Schema Set: `rag-eval.schema-set@1.3.0` `ca1f324c701dd5e86d811a4430ddbf2d394bd3aa0e7eb0e32dabcb8b63d1e325`
- Candidate Decision: [`docs/governance/decisions/2026-09-05-rag-evaluation-schema-set-1-3-candidate.md`](../../../governance/decisions/2026-09-05-rag-evaluation-schema-set-1-3-candidate.md)
- Gold Review Evidence: `github-pr-341-review-5137833200@1.0.0` `6dd83d9c258499fb0d543870e5a99a913abb0b2dcb3c11e4b72855e43c235776`
- Dataset Approval Evidence: `github-pr-354-review-5139907268@1.0.0` `3b1a90ba0f9a6c06162ce953bdb7e0d504f76074d415a807611812d16ac29896`
- HOLDOUT Preparation: `issue-273-holdout-freeze-preparation@1.0.0`
- Preparation raw SHA-256: `40ea344c378298d99c14c372c27296322854d8e9b055fa179592568ca88bc192`
- Preparation self SHA-256: `b4a0a113d9efce867a434875f18ee259431d226a9cf1e4dcaed28152920600b6`
- Protected Runner Foundation: `issue-273-protected-runner-foundation@1.0.0`
- Foundation raw SHA-256: `03d6c7613f2429d0ce972857c493bea7b37fec6b69f7c2d7f467a426159f2e24`
- Foundation self SHA-256: `edb69fea41af1ee2aec34e54ef44d7d0dc8273ab27135b26829c096ed842b3b2`
- Phase B3 Product·Privacy·Safety·Evaluation Reviewer: `@hazelnutflavoured`
- Phase B3 Dataset Custodian·Backend·Security Reviewer: `@phina-io`
- Prior DEV Approval Transition: `DEV_DATASET_CUSTODIAN_APPROVAL_RECORDED`; the verified actor was `@phina-io` (`DATASET_CUSTODIAN`). This is not HOLDOUT access authorization.
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
- HOLDOUT Preparation: `PREPARATION_READY`
- Protected Runner Issue: `CREATED`
- Policy Foundation: `IMPLEMENTED`
- Effective Enforcement: `NOT_IMPLEMENTED`
- Infrastructure Adapter: `PARTIALLY_IMPLEMENTED`
- Reconciliation Adapter: `NOT_IMPLEMENTED`
- HOLDOUT Freeze: `NOT_STARTED`
- Actual Adapter: `IMPLEMENTED`
- Actual Run Artifact: `rag-eval.run@1.0.0` `228ca8ad3ad47d12888801e8a114a93484c80ebc41ca4a7a32127fce075e4f79`

한국어 자연어 합성 DEV 질문 60개와 합성 Gold/corpus authoring graph가 저장소에 존재하며, 실제 환자 발화나 실제 제품 데이터가 아니다.
DEV Dataset approval is recorded as APPROVED for the 60 Cases, Evidence Mapping, and Dataset Manifest.
Dataset remains DRAFT and unfrozen; preparation does not create or Freeze HOLDOUT content.
The protected Runner policy foundation and data-plane adapter are partially implemented and verified.
Control-plane services and the actual protected loader/CLI remain NOT_IMPLEMENTED.
Access authorization is not recorded, and HOLDOUT authoring has not started.
Actual DEV retrieval evaluation was executed and verified.
DEV Metric values are DIAGNOSTIC_ONLY observations in the experiment log; no baseline Metric is approved and no Metric fields are recorded in the machine status.
DEV cannot produce a Release PASS; Production remains closed.

Actual DEV Retrieval experiment evidence: [DEV Actual Retrieval RET-L/D/H experiment](experiments/2026-09-16-dev-actual-retrieval-ret-l-d-h.md)
Later re-runs are preserved as additional dated logs under `docs/validation/rag/issue-273/experiments/` and do not overwrite this one.

## Blocking Codes

- `BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER`
- `WAITING_FOR_HOLDOUT_ACCESS_AUTHORIZATION`
- `WAITING_FOR_HOLDOUT_FREEZE`

## Verification Evidence

| Check | Command | Exit | Result |
| --- | --- | ---: | --- |
| `PHASE_A_DEV_FIXTURE` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py -q` | `0` | 26 passed |
| `PHASE_A_LOADER` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_authoring_identity_loader.py ai_worker/tests/evaluation/test_loaders.py -q` | `0` | 132 passed |
| `PHASE_A_REPORT_PROJECTION` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_validation_report.py -q` | `0` | 55 passed |
| `PHASE_A_SCHEMA_EXPORT` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_schema_exports.py ai_worker/tests/evaluation/test_external_schema_parity.py ai_worker/tests/evaluation/test_provenance_v1_schemas.py -q` | `0` | 158 passed |
| `PHASE_B3_PROTECTED_RUNNER_FOUNDATION` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_protected_runner_foundation.py ai_worker/tests/evaluation/test_protected_retrieval.py -q` | `0` | 85 passed |
| `PHASE_B_DATASET_APPROVAL_PROVENANCE` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py::test_issue_273_graph_records_the_actual_dataset_custodian_approval_event -q` | `0` | 1 passed |
| `PHASE_B_GOLD_REVIEW_PROVENANCE` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py::test_issue_273_graph_records_only_the_actual_gold_review_event -q` | `0` | 1 passed |
| `PHASE_B_HOLDOUT_FREEZE_PREPARATION` | `UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_holdout_preparation.py -q` | `0` | 27 passed |

## Boundaries

- Issue [#278](https://github.com/AI-HealthCare-05/AH_05_04/issues/278) is separate and non-blocking for #273.
- No Dataset Freeze, HOLDOUT Freeze, actual baseline completion, Release PASS, or Production readiness is claimed.
- HOLDOUT question content is absent from the repository and remains future protected work.
- Control-plane services, actual protected environment, access authorization, loader/CLI, and HOLDOUT Freeze remain blockers.
- `@phina-io` must approve the database/schema, roles, protected credential environment, audit retention,
  backup, revoke, and incident-response evidence before adapter completion or activation.
- HOLDOUT authoring may start only after an independent Dataset Custodian authorization event is recorded.
- The #158 replay uses a different Dataset and is `NOT_COMPARABLE_DIFFERENT_DATASET`.

Status updated at `2026-09-16T15:00:00.000000Z`. Canonical status SHA-256: `693b2ef93fecf5292a0a93910f242865fccece857b95cbd6204957e978c08c9f`.
