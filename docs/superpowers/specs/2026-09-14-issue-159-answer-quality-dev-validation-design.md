# Issue #159 Answer Quality DEV Validation Design

## Status

- Scope: Issue #159, RAG-EVAL-004 Phase B
- Owner: 정현우 (`@ceohwj`)
- Responsible reviewer: 권가빈 (`@hazelnutflavoured`)
- Decision basis: `PD-159-20260913` and the approved review on PR #475
- Runtime status: DEV diagnostic only; no HOLDOUT execution, baseline freeze, Release decision, or public activation

## Goal

Implement and verify the deterministic Answer Quality metric kernel against synthetic DEV fixtures so a later pre-RAG
`ANS-BASE` run can be scored without changing the frozen 153-case HOLDOUT·SAFETY_REGRESSION Dataset or observing its
results.

The implementation proves the #159 scoring and fail-closed boundaries. It does not call an external provider and does
not claim that an actual pre-RAG runtime baseline has been frozen.

## Existing Boundary

The repository already provides:

- `ANSWER_QUALITY` routing in the Evaluation runner;
- Answer Case Result fields for `actual_claim_ids`, `actual_sections`, `omitted_sections`, and `answer_sha256`;
- Dataset, Case, Evidence Mapping, and Critical Claim Rubric hash validation before adapter execution;
- generic Metric Result and Run Bundle schemas;
- the frozen `rag-holdout-safety@1.0.0` Dataset, which must not be used during Phase B;
- approved formulas and state semantics in the #159 proposed contract and `PD-159-20260913`.

The current manifest builder calculates Retrieval metrics only. Non-Retrieval scopes are emitted as
`NOT_IMPLEMENTED/null`. The current config also accepts any stable Answer variant ID instead of the approved
`ANS-BASE | ANS-RAG | ANS-FINAL` set.

## Scope

### Included

1. Add an Answer-only variant ID contract with exactly `ANS-BASE`, `ANS-RAG`, and `ANS-FINAL`.
2. Reject invalid Answer variant IDs during execution-config preflight without narrowing Retrieval variant IDs.
3. Implement deterministic `REQUIRED_CLAIM_RECALL` and `COMPLETENESS` observations and aggregation.
4. Preserve `ANSWER_CORRECTNESS` and `RELEVANCE` as `NOT_EVALUATED/null` when no approved human judgment artifact is
   supplied.
5. Emit numerator, denominator, canonical metric value, Case count, independent-group count, status, decision, and
   reason for implemented Answer metrics.
6. Reuse the approved fixed-seed cluster-bootstrap behavior through a small statistics helper only where required by
   the #159 policy signature.
7. Route `ANSWER_QUALITY` scopes through the new builder while preserving Retrieval behavior byte-for-byte at its
   public interfaces.
8. Verify behavior with hand-calculated synthetic fixtures and the existing `dev-foundation-v1` Answer Quality Case.
9. Verify Rubric mismatch fails before adapter execution and before any Run Bundle is published.

### Excluded

- External Provider calls or current Guide/Chat runtime wiring
- Creation or observation of HOLDOUT or SAFETY_REGRESSION results
- `baseline-freeze-receipt.json`
- Answer pair comparison and CLI `--baseline-run-id` support
- Citation Precision, Citation Coverage, Citation Entailment, or unsupported-claim scoring (#160)
- Safety, Rule-first, Scope, fallback, or critical-failure gates (#161)
- Human-judgment authoring or approval workflow
- Active thresholds, `PASS | FAIL`, Production release, or `PUBLIC_TRACK_F` changes
- Changes to the frozen 153-case Dataset, Gold, Evidence Mapping, Rubric, or leakage allocation

## Components

### Answer variant validation

`ai_worker/tasks/evaluation/config.py` introduces an Answer-specific enum or literal type. Only `DevVariant` instances
whose `kind` is `ANSWER` are restricted to the three approved values. Retrieval variants continue to accept the
existing stable IDs.

Validation occurs before repository-state and referenced-resource resolution. An invalid Answer ID is reported through
the existing schema/state error boundary and cannot reach an adapter.

### Answer metric kernel

`ai_worker/tasks/evaluation/answer_metrics.py` owns pure observation and aggregation logic. It accepts validated
`ANSWER_QUALITY` Cases, matching completed Case Results, and Comparison Policy scopes. It does not read files, invoke
providers, publish artifacts, or infer natural-language meaning.

For each Case:

- `REQUIRED_CLAIM_RECALL` contributes the number of required Gold claim IDs present in `actual_claim_ids` over the
  number of required Gold claim IDs.
- `COMPLETENESS` contributes the number of expected section IDs present in `actual_sections` over the number of
  expected section IDs.

Aggregation uses a micro ratio: sum of Case numerators divided by sum of Case denominators. It never averages Case
ratios. The builder derives independent group IDs only from the Policy-selected `cluster_dimension` and records their
distinct count separately from Case count and metric denominator.

The kernel rejects duplicate, missing, or extra Case Results and mismatched task types. Incomplete Case Results preserve
the existing execution-status priority and never receive a decision.

### Human metrics

This slice does not introduce an unapproved substitute for human judgment. When no approved judgment artifact is
present, `ANSWER_CORRECTNESS` and `RELEVANCE` are emitted as `NOT_EVALUATED/null`, with all numeric fields null. Empty
judgment input is not converted into zero, `FAIL`, or `INCONCLUSIVE`.

### Manifest routing

`ai_worker/tasks/evaluation/manifest.py` keeps the existing Retrieval route. For
`ANSWER_GROUNDING_SAFETY` experiments it delegates only Answer Quality scopes to the Answer metric builder. Grounding
and Safety scopes remain `NOT_IMPLEMENTED/null`, making the Run-level incomplete state explicit until #160 and #161
are implemented.

## State Semantics

| Condition | Execution status | Decision status | Numeric result |
| --- | --- | --- | --- |
| Valid non-required DEV diagnostic with sufficient sample | `COMPLETED` | `N/A` | Present |
| Denominator zero | `COMPLETED` | `INCONCLUSIVE` | Null |
| Minimum Case count not met | `COMPLETED` | `INCONCLUSIVE` | Point estimate retained only if the existing Metric schema permits it |
| Minimum independent group count not met | `COMPLETED` | `INCONCLUSIVE` | Point estimate retained only if the existing Metric schema permits it |
| Missing human judgments | `NOT_EVALUATED` | Null | Null |
| Case/Result set or binding mismatch | `INVALID` | Null | Null |
| Incomplete selected Case Result | Existing blocking priority | Null | Null |

No Phase B result may emit `PASS` or `FAIL`.

## Data and Privacy

Tests use only existing synthetic fixtures or newly created minimal synthetic objects. The implementation does not
persist queries, answer text, Provider payloads, patient data, reasoning, or source bodies. Metrics consume stable IDs
and hashes only. Failure evidence contains Case IDs and allowlisted non-sensitive reason codes.

## Test Strategy

Development follows red-green-refactor. Tests cover:

- accepted and rejected Answer variant IDs without restricting Retrieval IDs;
- hand-calculated numerator and denominator for both deterministic metrics;
- micro ratio rather than mean-of-means;
- zero denominator;
- minimum Case and independent-group counts;
- duplicate, missing, extra, wrong-task, and incomplete Case Results;
- missing human judgment producing `NOT_EVALUATED/null`;
- fixed-seed deterministic CI where the approved Policy requests it;
- manifest routing that calculates Answer Quality while preserving Grounding/Safety `NOT_IMPLEMENTED` states;
- Rubric mismatch causing zero adapter calls and zero published Run files;
- existing Retrieval metric, config, runner, artifact, loader, and comparison regressions.

Required verification is the targeted Answer tests first, then the full Evaluation test directory, Ruff check and format
check, mypy for `ai_worker/tasks/evaluation`, `git diff --check`, and complete diff inspection.

## Completion Boundary

Phase B is complete when the implemented deterministic Answer metrics produce schema-valid, reproducible DEV results
and every excluded capability remains unavailable or explicitly incomplete. The next separate slice may add a
pre-RAG runtime adapter and historical output capture. Actual `ANS-BASE` freeze on the 153-case Dataset begins only
after #159–#161 scorers, runtime adapters, and an authorized HOLDOUT Comparison/Evaluation Policy are ready.
