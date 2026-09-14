# RAG Evaluation Schema Set 1.4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the two strict Evaluation projection artifacts and immutable 23-member Schema Set `1.4.0` required by Issues #160 and #161.

**Architecture:** A focused `grounding_v1` schema module owns the observation and signal wire contracts without importing Runtime dataclasses. The existing registry and deterministic exporter expose those models only through a new Schema Set `1.4.0`, while all 21 members inherited from `1.3.0` remain byte-for-byte identical. Documentation records the generated set hash as a review-required Candidate and does not claim scorer, Runtime, HOLDOUT, or Release completion.

**Tech Stack:** Python 3.13, Pydantic v2 strict models, canonical JSON/SHA-256 helpers, JSON Schema Draft 2020-12, pytest, Ruff, Mypy.

**Spec:** `docs/superpowers/specs/2026-09-15-rag-evaluation-schema-set-1-4-design.md`

## Global Constraints

- Add exactly `rag-eval.claim-citation-observation@1.0.0` and `rag-eval.grounding-signal@1.0.0`.
- Schema Set `1.4.0` contains 23 unique members and reuses every `1.3.0` member byte-for-byte.
- Keep the exporter default at `1.0.0`; do not mutate `1.0.0` through `1.3.0` outputs or hashes.
- Store identifiers, bounded enums, immutable references, and hashes only; no query, answer, Claim, Source, Provider, credential, or patient body fields.
- Do not read or modify frozen HOLDOUT/SAFETY_REGRESSION Case contents, Gold, Evidence Mapping, or Rubric.
- Do not implement projection builders, metric kernels, Runtime adapters, policies, thresholds, baseline freeze, Release, or publication behavior.
- Responsible reviewer remains 김지혜 (`@Jye-rookie`) for this Evaluation/Source-provenance/Safety contract candidate.

---

### Task 1: Strict Grounding Projection Models

**Files:**
- Create: `ai_worker/tasks/evaluation/schemas/grounding_v1.py`
- Create: `ai_worker/tests/evaluation/test_grounding_v1_schemas.py`
- Modify: `ai_worker/tasks/evaluation/schemas/__init__.py`

**Interfaces:**
- Consumes: `StrictContractModel`, `CanonicalUuid`, `StableId`, `SemanticVersion`, `Sha256Hex`, `ImmutableReference`, `NonEmptyText`, `TaskType`, `canonical_sha256`, and the established byte-parser error path.
- Produces: `ClaimCitationObservation`, `GroundingSignal`, `CLAIM_CITATION_OBSERVATION_ADAPTER`, `GROUNDING_SIGNAL_ADAPTER`, `parse_claim_citation_observation_bytes(bytes)`, and `parse_grounding_signal_bytes(bytes)`.
- Produces bounded Evaluation enums whose wire values exactly match #180 Claim kind, support, source type, validation, and authorization values.

- [ ] **Step 1: Write failing valid-payload and enum tests**

Create literal observation and signal fixtures with canonical UUIDs, hashes, references, sorted Claims/Citations, and independently calculated self-hashes. Assert parsing returns the expected typed enum members. Parameterize every enum's complete literal set and one unsupported value; unsupported values must raise the established schema-validation error.

```python
observation = parse_claim_citation_observation_bytes(canonical_json_bytes(payload))
assert observation.task_type is TaskType.ANSWER_GROUNDING
assert observation.claims[0].claim_kind is ClaimKind.MEDICAL

signal = parse_grounding_signal_bytes(canonical_json_bytes(signal_payload))
assert signal.status is GroundingSignalStatus.EVALUATED
```

- [ ] **Step 2: Run the new tests and verify RED**

```bash
UV_CACHE_DIR=/private/tmp/ah05_issue160_uv_cache uv run pytest \
  ai_worker/tests/evaluation/test_grounding_v1_schemas.py -q
```

Expected: collection/import failure because `grounding_v1` does not exist.

- [ ] **Step 3: Implement the minimal strict models and parsers**

Implement frozen, extra-forbidden nested models with these public fields:

```python
class CitationEdgeObservation(StrictContractModel):
    citation_key: StableId
    claim_key: StableId
    source_type: CitationSourceTypeValue
    evidence_ref_id: StableId
    source_version: SemanticVersion
    locator: NonEmptyText
    content_sha256: Sha256Hex
    accepted: StrictBool
    validation_reason_code: CandidateValidationReasonValue | None
    authorized: StrictBool
    authorization_reason_code: AuthorizationReasonValue | None
    authorization_selection_sha256: Sha256Hex | None
    gold_source_matched: StrictBool


class ClaimObservation(StrictContractModel):
    claim_key: StableId
    claim_kind: ClaimKindValue
    criticality: ClaimCriticalityValue
    criticality_source: CriticalitySourceValue
    criticality_review_ref: ImmutableReference | None
    support_status: ClaimSupportStatusValue
    support_receipt_sha256: Sha256Hex
    citations: tuple[CitationEdgeObservation, ...]


class ClaimCitationObservation(StrictContractModel):
    schema_id: Literal["rag-eval.claim-citation-observation"]
    schema_version: Literal["1.0.0"]
    observation_sha256: Sha256Hex
    run_id: CanonicalUuid
    case_id: StableId
    task_type: AnswerGroundingTaskTypeValue
    dataset_code: StableId
    dataset_version: SemanticVersion
    input_sha256: Sha256Hex
    answer_sha256: Sha256Hex
    answer_variant_manifest_hash: Sha256Hex
    validation_execution_status: CandidateValidationExecutionStatusValue
    validation_decision: CandidateValidationDecisionValue
    validation_reason_codes: tuple[CandidateValidationReasonValue, ...]
    validated_selection_sha256: Sha256Hex | None
    authorization_decision: AuthorizationDecisionValue | None
    authorization_reason_codes: tuple[AuthorizationReasonValue, ...]
    authorization_receipt_ref: ImmutableReference | None
    authorization_receipt_sha256: Sha256Hex | None
    claims: tuple[ClaimObservation, ...]
```

`AnswerGroundingTaskTypeValue` accepts only `ANSWER_GROUNDING | SAFETY | END_TO_END_RAG`. Require unique UTF-16-sorted Claim keys, globally unique UTF-16-sorted Citation keys, each Citation's `claim_key` equal to its containing Claim, unique sorted reason tuples, correct criticality-reference pairing, consistent accepted/authorized reason/hash pairing, and the canonical self-hash excluding `observation_sha256`.

Implement the signal with these public fields:

```python
class GroundingSignal(StrictContractModel):
    schema_id: Literal["rag-eval.grounding-signal"]
    schema_version: Literal["1.0.0"]
    signal_sha256: Sha256Hex
    run_id: CanonicalUuid
    case_id: StableId
    task_type: SafetyTaskTypeValue
    dataset_code: StableId
    dataset_version: SemanticVersion
    input_sha256: Sha256Hex
    answer_sha256: Sha256Hex | None
    status: GroundingSignalStatusValue
    observation_ref: ImmutableReference | None
    observation_sha256: Sha256Hex | None
    critical_unsupported_claim: StrictBool
    uncited_medical_claim: StrictBool
    source_binding_misuse: StrictBool
```

`SafetyTaskTypeValue` accepts only `SAFETY | END_TO_END_RAG`. `EVALUATED` requires answer and both observation bindings. `NOT_APPLICABLE_NO_CLAIMS` permits a null answer hash or an approved-fallback answer hash, requires both observation bindings to be null, and requires all failure booleans false. Validate `signal_sha256` over the payload excluding that field. Byte parsers must follow the existing hashed-artifact path: parse a JSON object, validate the strict model, compare the canonical hash excluding its self-hash field, apply `validate_privacy_boundary`, and translate failures to the established `EvaluationValidationError` codes.

- [ ] **Step 4: Run valid-payload tests and verify GREEN**

Run the Task 1 command. Expected: valid-payload and enum tests pass.

- [ ] **Step 5: Write failing invariant and privacy tests**

Parameterize literal mutations for duplicate/unsorted Claim and Citation keys, orphan Citation, self-hash mismatch, criticality-reference mismatch, accepted/authorized reason mismatch, invalid authorization hash tuple, and every invalid no-claims tuple. Add a field-name sentinel rejecting `query`, `question`, `answer_text`, `claim_text`, `source_body`, `provider_payload`, `credential`, and `patient`.

- [ ] **Step 6: Run invariant tests and verify RED**

Run the Task 1 command. Expected: each newly introduced mutation fails until its validator exists.

- [ ] **Step 7: Add only the missing validators and package exports**

Complete the validators required by Step 5 and export the public types and parsers from `schemas/__init__.py`.

- [ ] **Step 8: Run Task 1 and existing schema tests**

```bash
UV_CACHE_DIR=/private/tmp/ah05_issue160_uv_cache uv run pytest \
  ai_worker/tests/evaluation/test_grounding_v1_schemas.py \
  ai_worker/tests/evaluation/test_schema_exports.py -q
```

- [ ] **Step 9: Commit Task 1**

```bash
git add ai_worker/tasks/evaluation/schemas/grounding_v1.py \
  ai_worker/tasks/evaluation/schemas/__init__.py \
  ai_worker/tests/evaluation/test_grounding_v1_schemas.py
git commit -m "✨ feat: #160 grounding projection 스키마 추가"
```

---

### Task 2: Schema Set 1.4 Registry and Canonical Export

**Files:**
- Modify: `ai_worker/tasks/evaluation/schema_registry.py`
- Modify: `ai_worker/tasks/evaluation/schema_exports.py`
- Modify: `ai_worker/tests/evaluation/test_schema_exports.py`
- Create: `evals/schemas/1.4.0/` with 23 canonical JSON Schema files

**Interfaces:**
- Consumes: Task 1 adapters and `SCHEMA_REGISTRY_V1_3`.
- Produces: `SCHEMA_REGISTRY_V1_4`, `SCHEMA_REGISTRIES["1.4.0"]`, and CLI support for `--schema-set-version 1.4.0`.

- [ ] **Step 1: Write failing registry and inherited-byte tests**

Assert `1.4.0` has exactly 23 unique paths and IDs, adds only the two `1.0.0` members, and keeps every inherited document byte-identical:

```python
for path, document in schema_documents("1.3.0").items():
    assert canonical_json_bytes(schema_documents("1.4.0")[path]) == canonical_json_bytes(document)
```

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
UV_CACHE_DIR=/private/tmp/ah05_issue160_uv_cache uv run pytest \
  ai_worker/tests/evaluation/test_schema_exports.py -k 'schema_set_1_4' -q
```

Expected: failure because registry version `1.4.0` is absent.

- [ ] **Step 3: Register the two members**

Build `SCHEMA_REGISTRY_V1_4` from the complete `SCHEMA_REGISTRY_V1_3` plus the two artifact adapters, add it to `SCHEMA_REGISTRIES`, and set `_SCHEMA_SET_MEMBER_COUNTS["1.4.0"] = 23`. Do not change earlier tuples or `SCHEMA_VERSION`.

- [ ] **Step 4: Run registry and inherited-byte tests and verify GREEN**

Run the Step 2 command.

- [ ] **Step 5: Write failing portable conditional-schema tests**

Validate the same invalid authorization and no-claims payload mutations against the exported Draft 2020-12 documents. Use the existing conditional `jsonschema` skip convention when that optional package is unavailable.

- [ ] **Step 6: Run conditional tests and verify RED**

Expected: invalid tuples remain portable-schema-valid until exporter conditions exist.

- [ ] **Step 7: Add two focused exporter condition helpers**

Extend `_schema_document()` only for observation validation/authorization nullable-field relationships and signal `EVALUATED` versus `NOT_APPLICABLE_NO_CLAIMS` relationships. Do not change normalization or existing schema behavior.

- [ ] **Step 8: Generate the committed schema directory**

```bash
UV_CACHE_DIR=/private/tmp/ah05_issue160_uv_cache uv run python \
  -m ai_worker.tasks.evaluation.schema_exports \
  --output evals/schemas/1.4.0 --schema-set-version 1.4.0
```

- [ ] **Step 9: Add committed-export and strictness tests**

Assert a fresh temporary export equals `evals/schemas/1.4.0/` byte-for-byte, both new schemas use Draft 2020-12, reject extra properties, expose the exact required field sets, and contain none of the forbidden body-field names.

- [ ] **Step 10: Run Task 1 and Task 2 tests**

```bash
UV_CACHE_DIR=/private/tmp/ah05_issue160_uv_cache uv run pytest \
  ai_worker/tests/evaluation/test_grounding_v1_schemas.py \
  ai_worker/tests/evaluation/test_schema_exports.py -q
```

- [ ] **Step 11: Commit Task 2**

```bash
git add ai_worker/tasks/evaluation/schema_registry.py \
  ai_worker/tasks/evaluation/schema_exports.py \
  ai_worker/tests/evaluation/test_schema_exports.py evals/schemas/1.4.0
git commit -m "✨ feat: #160 평가 Schema Set 1.4 추가"
```

---

### Task 3: Candidate Decision and Contract Alignment

**Files:**
- Create: `docs/governance/decisions/2026-09-15-rag-evaluation-schema-set-1-4-candidate.md`
- Modify: `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`
- Modify: `docs/contracts/targets/post-mvp-1/rag-grounding-citation-metrics-v1.md`
- Modify: `docs/contracts/targets/post-mvp-1/rag-safety-rule-first-metrics-v1.md`
- Modify: `docs/contracts/README.md`
- Modify: `evals/README.md`
- Modify: `ai_worker/tests/evaluation/test_schema_exports.py`

**Interfaces:**
- Consumes: committed `evals/schemas/1.4.0/` and loader `_schema_set_hash`.
- Produces: one exact Schema Set ID/version/hash/count statement across the Decision, authoritative contract, index, and Evaluation README.

- [ ] **Step 1: Calculate the canonical set hash**

```bash
UV_CACHE_DIR=/private/tmp/ah05_issue160_uv_cache uv run python -c \
  'from ai_worker.tasks.evaluation.loaders import _SnapshotReader, _schema_set_hash; from pathlib import Path; print(_schema_set_hash(_SnapshotReader(Path("evals")), "1.4.0"))'
```

- [ ] **Step 2: Write failing documentation-hash tests**

Add a parameterized test for the new Decision, `rag-evaluation-v1.md`, and `evals/README.md`, matching each literal `rag-eval.schema-set@1.4.0` hash to `_schema_set_hash(..., "1.4.0")`.

```bash
UV_CACHE_DIR=/private/tmp/ah05_issue160_uv_cache uv run pytest \
  ai_worker/tests/evaluation/test_schema_exports.py -k 'documented_schema_set_1_4' -q
```

Expected: failure because the documents do not yet contain the Candidate reference.

- [ ] **Step 3: Add the Candidate Decision and align contracts**

Record `Candidate · Review Required`, Issues #160/#161, owner `@ceohwj`, responsible reviewer `@Jye-rookie`, exact set ID/version/hash/root/member count, 21 inherited members plus two new members, and every excluded boundary. Update #160/#161 wording only from “no schema exists” to “Schema Set 1.4 Candidate exists but remains review-required”; do not mark metric implementation complete.

- [ ] **Step 4: Run documentation-hash tests and verify GREEN**

Run the Step 2 command.

- [ ] **Step 5: Validate documentation and scope**

```bash
git diff --check
git diff --stat origin/develop...HEAD
git diff origin/develop...HEAD -- docs/governance/decisions \
  docs/contracts/targets/post-mvp-1 docs/contracts/README.md evals/README.md
```

Expected: no whitespace errors and no claim that scorers, Runtime, HOLDOUT, baseline freeze, Release, or publication are implemented or approved.

- [ ] **Step 6: Commit Task 3**

```bash
git add docs/governance/decisions/2026-09-15-rag-evaluation-schema-set-1-4-candidate.md \
  docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md \
  docs/contracts/targets/post-mvp-1/rag-grounding-citation-metrics-v1.md \
  docs/contracts/targets/post-mvp-1/rag-safety-rule-first-metrics-v1.md \
  docs/contracts/README.md evals/README.md ai_worker/tests/evaluation/test_schema_exports.py
git commit -m "📝 docs: #160 평가 Schema Set 1.4 후보 고정"
```

---

### Task 4: Integrated Verification and Review

**Files:**
- Verify all files changed by Tasks 1–3.

**Interfaces:**
- Consumes: completed model, export, registry, generated schemas, and Candidate documentation.
- Produces: fresh verification evidence and a review-ready branch; no new feature surface.

- [ ] **Step 1: Run focused tests**

```bash
UV_CACHE_DIR=/private/tmp/ah05_issue160_uv_cache uv run pytest \
  ai_worker/tests/evaluation/test_grounding_v1_schemas.py \
  ai_worker/tests/evaluation/test_schema_exports.py -q
```

- [ ] **Step 2: Run the complete Evaluation suite**

```bash
UV_CACHE_DIR=/private/tmp/ah05_issue160_uv_cache uv run pytest ai_worker/tests/evaluation -q
```

- [ ] **Step 3: Run static checks**

```bash
UV_CACHE_DIR=/private/tmp/ah05_issue160_uv_cache uv run ruff check \
  ai_worker/tasks/evaluation ai_worker/tests/evaluation
UV_CACHE_DIR=/private/tmp/ah05_issue160_uv_cache uv run ruff format \
  ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
UV_CACHE_DIR=/private/tmp/ah05_issue160_uv_cache uv run mypy ai_worker/tasks/evaluation
```

- [ ] **Step 4: Verify diff and generated bytes**

```bash
git diff --check origin/develop...HEAD
git status --short --branch
```

Re-run the fresh temporary export comparison through `test_schema_exports.py`; never edit generated JSON manually.

- [ ] **Step 5: Request independent code review**

Provide base `6b1dee6e`, current HEAD, this plan, the design spec, and the explicit excluded scope. Resolve every Critical or Important finding and rerun the smallest proving test plus Steps 1–4.

- [ ] **Step 6: Prepare the Pull Request**

Use `.github/pull_request_template.md`, link Issues #160 and #161 without claiming they are closed, name exactly one responsible reviewer (`@Jye-rookie`), report every executed/skipped check, and state that the PR is a Candidate schema prerequisite rather than scorer, Runtime, HOLDOUT, baseline, Release, or Production completion.
