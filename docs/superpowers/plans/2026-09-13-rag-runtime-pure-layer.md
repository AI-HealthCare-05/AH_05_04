# RAG-16 Claim·Citation Pure Layer Implementation Plan

> **For Codex:** Execute this plan task-by-task with strict red-green-refactor TDD. Do not add Graph, persistence, Backend imports, public DTOs, or production activation.

**Goal:** Implement Issue #180's persistence-free Claim–Citation validation, Citation Authorization request/Receipt verification, and finalization boundary without creating work that must be rewritten when #174 supplies async persistence and currentness transactions.

**Architecture:** The pure layer is a three-phase pipeline: validate detached candidates against exact-bound support Receipts, build an immutable authorization request, then finalize only after an externally observed authorization Receipt exact-matches that request. I/O remains outside the pure modules; a later Worker Application Service may define an async persistence port and pass the observed Receipt back to these functions. Success is only an input to the later Runtime Release Gate, never public-release authority.

**Tech Stack:** Python 3.13 frozen dataclasses and `StrEnum`, standard-library canonical JSON/SHA-256/NFC validation, pytest, Ruff, Mypy.

**Design source:** `docs/designs/ceohwj/issue-180-rag-runtime-pure-layer-design.md`

---

## Scope guardrails

- Modify only the RAG pure modules, their focused unit/contract tests and synthetic fixture, and Issue #180 design/plan documentation.
- Reuse `ImmutableArtifactRef`; do not import SQLAlchemy, FastAPI, LangGraph, network, clock, filesystem, `backend.app`, or synthetic runtime authority.
- Do not define a sync/async authorization Protocol until the #174 persistence consumer exists.
- Do not reinterpret `ai_worker.tasks.rag.source_governance` synthetic verdicts as Runtime authorization.
- Do not emit `release_decision`, `ai_job.status`, fallback body text, or a public Citation DTO.
- Preserve Target contract vocabulary exactly: five Citation source types and four Claim support statuses.

## Task 1: Lock candidate validation behavior

**Files:**

- Create: `ai_worker/tests/rag/test_claim_citation_validator.py`
- Create: `ai_worker/tasks/rag/claim_citation_validator.py`

### Steps

- [ ] Write focused tests that fail because the module does not exist. Each test names a behavioral regression: a valid medical Claim passes only with a complete exact-bound support Receipt; unsupported or partially supported medical Claims fail; missing Citation fails; five tagged Evidence Ref variants accept only their matching `source_type`; duplicate keys/orders, order gaps, missing provenance, non-NFC text, uppercase/short hashes, and bool-as-int fail closed.
- [ ] Use literal expected reason codes and at least one hand-computed canonical selection hash; do not compute expected values with production helpers.
- [ ] Run `uv run pytest ai_worker/tests/rag/test_claim_citation_validator.py -q` and record the expected import/collection failure.
- [ ] Implement frozen, slotted input/output dataclasses and internal enums. Reuse `ImmutableArtifactRef`. Keep source-backed provenance explicit, distinguish tagged Endpoint/Operation from Artifact Member identity, and allow nullable Source execution provenance only for `PRESCRIPTION`.
- [ ] Implement strict shape validation before semantic validation. Require exact enum/tuple/dataclass types, NFC strings, lowercase SHA-256, positive non-bool contiguous display orders, unique Claim/Citation keys, existing Claim references, and exact tagged-ref matching.
- [ ] Canonicalize validated Claim/Citation projections with versioned canonical JSON sorted by UTF-8 bytes. Reject duplicates rather than silently normalizing them.
- [ ] Verify one `ClaimSupportVerificationReceipt` per Claim against the recomputed projection, artifact refs, verifier ref, support status, Claim digest, and complete sorted Citation evidence projection. Reject the whole candidate set on any failure.
- [ ] Run the focused test until green, then run Ruff format/check on the two files.

## Task 2: Lock authorization request and Receipt verification

**Files:**

- Create: `ai_worker/tests/rag/test_citation_authorization.py`
- Create: `ai_worker/tasks/rag/citation_authorization.py`

### Steps

- [ ] Write tests that fail because the authorization module does not exist. Cover deterministic request projection; canonical scope ordering; origin `REQUEST/PASS`; exact environment, bundle, manifest, scope and scope-hash matching; `CITATION_AUTHORIZATION`; `PATIENT_CITATION`; selected flag; complete Source/Member `PASS`; and missing, extra, duplicated or altered selection entries.
- [ ] Include one literal canonical request/selection hash expectation and mutation cases for bundle, scope, operation, purpose and one selection entry.
- [ ] Run `uv run pytest ai_worker/tests/rag/test_citation_authorization.py -q` and record the expected import/collection failure.
- [ ] Implement immutable origin/runtime bindings, authorization selection entries, request, Receipt, and verification outcome types. Requests contain Source/Member identity only; newly created Citation Authorization Decision refs appear only in the observed Receipt. Keep all reason codes internal and stable; exclude external exception strings and sensitive text.
- [ ] Implement `build_citation_authorization_request(validated_selection, runtime_binding, origin_guard)` as a pure function. It must validate the origin and binding before returning a request, build selections only from validated Citation provenance, produce deterministic versioned hashes, and fail closed on an empty Source/Member Selection until #174 defines the prescription-only Guard representation.
- [ ] Implement `verify_citation_authorization_receipt(request, receipt)` as a pure exact-match. A bare `PASS`, partial selection, wrong purpose, wrong operation, or Source/Member failure rejects the entire authorization.
- [ ] Run the focused test until green, then run Ruff format/check on the two files.

## Task 3: Lock finalization ordering and fail-closed output

**Files:**

- Create: `ai_worker/tests/rag/test_citation_finalizer.py`
- Create: `ai_worker/tasks/rag/citation_finalizer.py`

### Steps

- [ ] Write tests that fail because the finalizer module does not exist. Cover an exact-bound pass producing `AuthorizedCitationSelection`; selection/request mismatch, missing or rejected Receipt, malformed success, and authorization mismatch producing `DiscardGeneratedContent`; and prove no generated/source raw sentinel appears in outcome or `repr`.
- [ ] Run `uv run pytest ai_worker/tests/rag/test_citation_finalizer.py -q` and record the expected import/collection failure.
- [ ] Implement `finalize_citations(validated_selection, authorization_request, authorization_receipt)`. Recompute and exact-match the request selection before verifying the Receipt; never accept a caller-constructed request for a different validated selection.
- [ ] Return only frozen `AuthorizedCitationSelection` or `DiscardGeneratedContent`. State in the success type docstring that it is Release Gate input, not public authorization.
- [ ] Run the focused test until green, then run Ruff format/check on the two files.

## Task 4: Add synthetic contract matrix and drift coverage

**Files:**

- Create: `tests/fixtures/rag/citation/finalization_cases.json`
- Create: `tests/contract/rag/test_claim_citation_contract.py`

### Steps

- [ ] Add de-identified synthetic PASS/failure fixture cases with no patient text, prescription image content, provider body, credentials, or re-identifiable identifiers.
- [ ] Write a parameterized contract test that executes the public pure functions against the fixture. It must prove the five source types, four support statuses, whole-candidate rejection, and authorization exact-match behavior rather than grepping source or documentation.
- [ ] Run the new contract test first and observe failure before adding any missing fixture parser/behavior.
- [ ] Add only the smallest production changes required by the failing contract cases; do not add test-only methods to production classes.
- [ ] Run all four focused files together until green.

## Task 5: Integrated verification and review

**Files:**

- Modify only if evidence reveals a defect: files created in Tasks 1–4 and the Issue #180 design document.

### Steps

- [ ] Run `uv run pytest ai_worker/tests/rag/test_claim_citation_validator.py ai_worker/tests/rag/test_citation_authorization.py ai_worker/tests/rag/test_citation_finalizer.py tests/contract/rag/test_claim_citation_contract.py -q`.
- [ ] Run `uv run pytest ai_worker/tests/rag -q` and `uv run pytest ai_worker/tests/evaluation -q`.
- [ ] Run `uv run ruff check ai_worker/tasks/rag ai_worker/tests/rag tests/contract/rag`.
- [ ] Run `uv run ruff format ai_worker/tasks/rag ai_worker/tests/rag tests/contract/rag --check`.
- [ ] Run `uv run mypy ai_worker/tasks/rag`.
- [ ] Run `git diff --check` and inspect the complete diff for unrelated, generated, secret, patient-data, Backend, Graph, persistence, or public-contract changes.
- [ ] Run `bash scripts/ci/run_test.sh`. If infrastructure prevents it, report the exact gap and retain focused green evidence.
- [ ] Perform a fresh solo design/code review because this task does not authorize subagent delegation. Check async extension compatibility, trust-boundary wording, fail-closed behavior, privacy, Target vocabulary, and mutation coverage; fix any blocking finding and rerun affected checks.
- [ ] Commit the focused implementation with the repository commit convention. Do not push, open a PR, merge, or enable Track F without an explicit user request and required reviewer/external approvals.

## Plan self-review

- [x] Every implementation task starts with an observable failing test and names the regression it prevents.
- [x] Expected hashes include literal independently derived values rather than production helper reuse.
- [x] The future async persistence boundary is outside the pure functions, avoiding a sync-to-async rewrite.
- [x] Worker integration does not import `backend.app`; transaction ownership remains with the Consumer/ResultStore boundary.
- [x] The plan does not implement blocked Graph, persistence, currentness, public DTO, Runtime Bundle completion, or production activation.
- [x] The final checks cover focused behavior, RAG/evaluation regressions, lint, formatting, types, diff hygiene, and repository CI.
