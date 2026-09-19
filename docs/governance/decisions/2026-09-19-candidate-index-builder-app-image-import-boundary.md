# Decision: Candidate Index Builder App Image Execution and Import Boundary

- **Decision ID**: `PD-800-20260919`
- **Date**: 2026-09-19
- **Status**: Accepted
- **Approval Date**: 2026-09-19
- **Implementation Owner**: 정현우 (AI/RAG implementation owner)
- **Responsible Reviewers**: @phina-io (송은영 — Backend / Security technical controls), @hazelnutflavoured (권가빈 — PM / Acceptance)
- **Precedent Decisions**:
  - `PD-175-20260910`: `backend` → `ai_worker` import boundary pure-kernel allowlist baseline.
  - `PD-168-20260915`: RAG-07B Candidate Index build entrypoint and parameter kernel authorization.

---

## 1. Context and Problem Statement

Issue #800 Phase A requires establishing the production execution boundary for the Candidate Index Builder one-shot operator command without actual Novasc data ingestion.

Under `PD-175-20260910` and `PD-168-20260915`, the production `backend` codebase is strictly limited to pure kernel modules in `ai_worker` (`ai_worker.tasks.rag.runtime_bundle_builder`, `ai_worker.tasks.rag.candidate_index`, `ai_worker.tasks.rag.catalog.export`), with zero SQLAlchemy adapters permitted.

However, the Candidate Index Builder operator command requires:
1. Verifying upstream Catalog build approvals before building (`SqlAlchemyCatalogApprovalVerifier`).
2. Materializing candidate index records via isolated database session support (`SqlAlchemyCatalogWriteSupport`).
3. Running with the dedicated `candidate-index-admin` Docker Compose profile inside the `app-${APP_VERSION}` container image where database connectivity and schema definitions reside.

Placing these adapter imports into `backend/app` would violate the pure-kernel boundary established by `PD-175`. Conversely, running this one-shot administrative process in the long-running `ai_worker` Celery worker container would violate least privilege, as `ai_worker` has no database administrative access or migration context.

---

## 2. Decision

### 2.1 Pure Kernel Allowlist Invariance for Backend Application Runtime
The import allowlist for production backend application roots (`backend/app/**` and `backend/alembic/**`) remains strictly invariant and pure-kernel only:
- `ai_worker.tasks.rag.runtime_bundle_builder`
- `ai_worker.tasks.rag.candidate_index`
- `ai_worker.tasks.rag.catalog.export`

No SQLAlchemy adapter or database I/O module from `ai_worker` is permitted in `backend/app` or `backend/alembic`.

### 2.2 Authorized Operator Script Boundary in `app-${APP_VERSION}`
The one-shot operator script `scripts/candidate_index_builder.py` is authorized to exist in the repository root and be packaged into the `app-${APP_VERSION}` container image via explicit Dockerfile `COPY`:
- `COPY ./scripts/__init__.py ./scripts/__init__.py`
- `COPY ./scripts/candidate_index_builder.py ./scripts/candidate_index_builder.py`

This script is authorized to directly import exactly the following three `ai_worker` modules:
1. `ai_worker.adapters.sqlalchemy_catalog_approval_verifier`
2. `ai_worker.adapters.sqlalchemy_catalog_write_support`
3. `ai_worker.tasks.rag.candidate_index`

No other `ai_worker` modules are permitted.

### 2.3 Automated Anti-Evasion Contract Testing
Contract test `tests/contract/test_backend_ai_worker_import_boundary.py` automatically binds `backend/app/Dockerfile` script COPY directives with static AST import audits:
1. Extracts all `COPY ./scripts/...py` targets fail-closed.
2. Checks that every copied script is governed by an explicit per-file allowlist (`APP_IMAGE_SCRIPT_ALLOWED_AI_WORKER_MODULES`), defaulting to `frozenset()` (zero `ai_worker` imports).
3. Verifies that `scripts/candidate_index_builder.py` matches its exact authorized import set.
4. Asserts that no other script copied into the image can import catalog approval or write support adapters.

### 2.4 Least-Privilege DB Role Isolation
`CANDIDATE_INDEX_CATALOG_READ_TABLES` in `infra/python/candidate_index_role_policy.py` is decoupled from `CATALOG_READ_TABLES` as an independent literal `frozenset` containing exactly 18 verified tables:
- 10 Catalog persistence tables
- 5 Source provenance tables
- 3 Catalog approval tables

Future widenings of `CATALOG_READ_TABLES` will not widen Candidate Index Builder grants.

---

## 3. Alternatives Considered and Rejected

1. **Move Candidate Index Builder into `ai_worker`**:
   - *Rejected*: The Celery worker runs with runtime worker privileges and lacks one-shot operator isolation, network database maintenance routing, and configuration parity with Compose administrative profiles.
2. **Move script into `backend/app/commands`**:
   - *Rejected*: Violates `PD-175-20260910`. Any adapter import inside `backend/app` weakens the pure-kernel boundary for the main web service.
3. **Broaden `BACKEND_ALLOWED_AI_WORKER_MODULES` globally**:
   - *Rejected*: Allows any API router or service in `backend/app` to import adapters, destroying boundary enforcement.
4. **Copy entire `scripts/` directory in Dockerfile (`COPY ./scripts ./scripts`)**:
   - *Rejected*: Violates fail-closed image surface auditing. All copied scripts must be explicitly enumerated and audited by contract tests.

---

## 4. Consequences and Compliance

- Strict import isolation is preserved for the web application runtime.
- The Candidate Index Builder is executable as a one-shot container under the `candidate-index-admin` profile.
- All boundaries are guarded by automated contract tests (`test_backend_ai_worker_import_boundary.py`, `test_database_role_deployment.py`).
- Approval Gate: SATISFIED — @phina-io (송은영) Backend / Security approval and @hazelnutflavoured (권가빈) PM / Acceptance approval recorded on 2026-09-19. See [§5 Approval Evidence](#5-approval-evidence).

---

## 5. Approval Evidence

Both responsible reviewers recorded explicit approval of this Decision. The pull request merge event itself is **not** treated as approval evidence; only the two reviewer approvals below are.

| Reviewer | Role | State | Submitted | Evidence | Target |
| --- | --- | --- | --- | --- | --- |
| `@phina-io` | Backend / Security technical controls | `APPROVED` | `2026-09-19T02:09:54Z` | [PR #824 review](https://github.com/AI-HealthCare-05/AH_05_04/pull/824#pullrequestreview-5254127846) `PRR_kwDOT3EWNs8AAAABOSug5g` | `f896ab81becb227b837f58f2c33b886b927910a9` |
| `@hazelnutflavoured` | PM / Acceptance | `APPROVED` | `2026-09-19T03:56:21Z` | [PR #824 comment](https://github.com/AI-HealthCare-05/AH_05_04/pull/824#issuecomment-5739178810) `5739178810` | post-merge PD-800 approval; merge `e8ed7d7b1427d4fadfcbe2fc372fb6b87a6f5e38` |

### 5.1 Post-Merge Governance Chronology

PR #824 carried the Phase A implementation together with the Backend / Security approval from `@phina-io`, and was merged through the merge queue as `e8ed7d7b1427d4fadfcbe2fc372fb6b87a6f5e38` before the PM / Acceptance review concluded.

The explicit PD-800 approval from the PM / Acceptance responsible reviewer `@hazelnutflavoured` was recorded after that merge, on 2026-09-19.

Accordingly, this status transition from `Proposed · Review pending` to `Accepted` is a **post-merge governance closeout**, not an implementation change. No boundary defined in §2 is altered, broadened, or re-designed by the transition.
