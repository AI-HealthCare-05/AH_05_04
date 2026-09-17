# Issue #273 Actual Retrieval Experiment Logs

This directory contains historical DEV Actual Retrieval experiment evidence.

- [2026-09-16 — DEV Actual Retrieval RET-L / RET-D / RET-H](2026-09-16-dev-actual-retrieval-ret-l-d-h.md)

Experiments are preserved as dated historical records and are not overwritten by later runs.

DEV EVIDENCE ONLY — NOT RELEASE PASS.

## Adding a new experiment

New experiments must be added as new dated Markdown files. Existing historical
experiment logs must not be overwritten.

1. Add `<YYYY-MM-DD>-<experiment-name>.md` to this directory.
2. Add one line to the list above.

`report.md` links to this index, not to any individual log, so no renderer
change is needed when an experiment is added.

## Scope

각 실험의 metric·Run ID·provenance·재현성 관찰값의 source of truth는 **날짜별
experiment Markdown 파일**이다. 이 index에는 상세 수치를 복사하지 않는다.

| File | Responsibility |
| --- | --- |
| `../status.json` | machine-readable current state |
| `../report.md` | current deterministic status projection + link to this index |
| `README.md` (this file) | historical experiment catalog |
| `<YYYY-MM-DD>-*.md` | immutable historical experiment evidence |

These logs are diagnostic DEV observations on a `DRAFT`, unfrozen synthetic
dataset. They are not a Release PASS, not Production readiness, and not clinical
validity. HOLDOUT was not authored, accessed, or evaluated.
