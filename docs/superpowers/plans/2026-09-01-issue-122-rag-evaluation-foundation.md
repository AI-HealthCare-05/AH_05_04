# Issue #122 RAG Evaluation Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the versioned RAG evaluation schemas, deterministic manifests, synthetic DEV dataset, fail-closed loader, and validation-only CLI required by Issue #122.

**Architecture:** Commit Draft 2020-12 JSON Schema as the machine contract and maintain strict Pydantic models as the executable mirror. A constrained RFC 8785 canonicalization layer owns deterministic hashing; loaders validate bytes, structure, paths, hashes, cross-resource invariants, leakage, provenance, and privacy before returning immutable aggregates. The CLI only validates and emits a separate non-release validation receipt using atomic no-clobber publication.

**Tech Stack:** Python 3.13, Pydantic 2.12, pytest, Ruff, mypy, standard-library `json`, `hashlib`, `pathlib`, `os`, and `argparse`; no new dependency.

**Spec:** `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`

## Global Constraints

- Scope is Issue #122 only: schemas, manifests, loaders, deterministic hash, synthetic fixtures, and validation-only CLI. Do not implement Runner, Metric calculation, Reporter, Provider calls, DB, API, Frontend, or release flag changes.
- Result Artifact schema IDs are exactly `rag-eval.run`, `rag-eval.case-result`, `rag-eval.metrics`, `rag-eval.suite-results`, `rag-eval.comparison`, `rag-eval.gate`, `rag-eval.failure`, and `rag-eval.content-manifest`, all at schema version `1.0.0`.
- Evaluation execution states are exactly `NOT_IMPLEMENTED`, `NOT_EVALUATED`, `INVALID`, `ERROR`, `COMPLETED`; decision is null unless execution is `COMPLETED`, when it is one of `PASS`, `FAIL`, `INCONCLUSIVE`, `N/A`. Required members reject `N/A`.
- Partitions are exactly `AUTHORING`, `DEV`, `HOLDOUT`, `SAFETY_REGRESSION`; experiment types are exactly `KNOWLEDGE_RETRIEVAL`, `ANSWER_GROUNDING_SAFETY`, `END_TO_END_RAG`. Never store `END_TO_END_FINAL` or `NOT_RUN`.
- Leakage axes are exactly `question_template`, `source_segment`, `medication_family`, `transform_origin`, and a value shared across partitions is invalid.
- Hash wire values are raw lowercase 64-character SHA-256 hex. Canonical object hashing uses constrained RFC 8785 over I-JSON: UTF-16 key order, no float, safe integers only, lone-surrogate rejection, no Unicode normalization except NFC resource paths.
- Every Pydantic contract uses strict validation and `extra="forbid"`. Every committed JSON Schema uses Draft 2020-12, stable `urn:ah05:rag-eval:schema:<logical-name>:1.0.0`, and `additionalProperties=false`.
- Dataset content is only `SYNTHETIC` or externally approved `APPROVED_DEIDENTIFIED`. Do not store real patient, prescription, OCR raw/normalized/draft, insurance code, internal identifier digest, provider payload, credential, or secret values.
- Foundation data is one Dataset with exactly five synthetic `DEV` cases: one each for `RETRIEVAL`, `ANSWER_QUALITY`, `ANSWER_GROUNDING`, `SAFETY`, `END_TO_END_RAG`. Negative scenarios mutate these fixtures in tests instead of adding committed datasets.
- JSON is the machine source of truth. Foundation does not generate Run, Metric, Gate, Markdown report, PASS/FAIL, or public release evidence.
- Follow TDD for every production behavior: write a focused real-behavior test, run it and observe the expected failure, implement minimally, then run it green.

---

### Task 1: Canonical JSON, scalar contracts, and stable errors

**Files:**
- Create: `ai_worker/tasks/evaluation/errors.py`
- Create: `ai_worker/tasks/evaluation/canonical.py`
- Create: `ai_worker/tasks/evaluation/schemas/common.py`
- Modify: `ai_worker/tasks/evaluation/schemas/__init__.py`
- Test: `ai_worker/tests/evaluation/test_canonical.py`
- Test: `ai_worker/tests/evaluation/test_common_schemas.py`

**Interfaces:**
- Produces: `canonical_json_bytes(value: JsonValue) -> bytes`, `sha256_hex(data: bytes) -> str`, `canonical_sha256(value: JsonValue, *, excluded_top_level_keys: frozenset[str] = frozenset()) -> str`, `normalize_resource_path(value: str) -> str`.
- Produces: `EvaluationValidationError(code: EvaluationErrorCode, safe_path: str | None = None)` whose string never includes sensitive values.
- Produces: shared enums, constrained scalar aliases, `ActorRef`, `ImmutableReference`, `ReviewProvenance`, `ExecutionDecisionMixin`, and `StrictContractModel`.
- Consumes: only the standard library and Pydantic.

- [ ] **Step 1: Write failing canonicalization and scalar tests**

```python
def test_canonical_json_uses_utf16_key_order_and_rejects_float() -> None:
    assert canonical_json_bytes({"\ue000": 1, "\U00010000": 2}) == '{"𐀀":2,"":1}'.encode()
    with pytest.raises(EvaluationValidationError, match="EVAL_JSON_NUMBER_INVALID"):
        canonical_json_bytes({"ratio": 0.5})

def test_execution_decision_rejects_pass_before_completion() -> None:
    with pytest.raises(ValidationError):
        ExecutionDecision(execution_status="NOT_EVALUATED", decision_status="PASS")
```

- [ ] **Step 2: Run the tests and confirm they fail because the modules or behavior do not exist**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation/test_canonical.py ai_worker/tests/evaluation/test_common_schemas.py -q`

- [ ] **Step 3: Implement the minimal canonical and common contracts**

```python
def canonical_json_bytes(value: JsonValue) -> bytes:
    validated = _validated_json_value(value)
    ordered = _order_objects(validated, key=lambda item: item.encode("utf-16-be"))
    return json.dumps(ordered, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")

class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
```

Reject booleans where integers are expected, integers outside `[-(2**53)+1, (2**53)-1]`, lone surrogates, duplicate normalized resource paths, absolute/backslash/dot-segment/NUL paths, invalid timestamps, noncanonical UUID, noncanonical decimal strings, and self-approval by `(namespace, actor_id)`.

- [ ] **Step 4: Run Task 1 tests green, then lint and type-check the new package**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation/test_canonical.py ai_worker/tests/evaluation/test_common_schemas.py -q
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run mypy ai_worker/tasks/evaluation
```

- [ ] **Step 5: Commit Task 1**

```bash
git add ai_worker/tasks/evaluation ai_worker/tests/evaluation
git commit -m "✨ feat: RAG 평가 공통 계약과 canonical hash 추가"
```

### Task 2: Authoring schemas, provenance, and privacy boundary

**Files:**
- Create: `ai_worker/tasks/evaluation/schemas/authoring.py`
- Create: `ai_worker/tasks/evaluation/privacy.py`
- Modify: `ai_worker/tasks/evaluation/schemas/__init__.py`
- Test: `ai_worker/tests/evaluation/test_authoring_schemas.py`
- Test: `ai_worker/tests/evaluation/test_privacy_validation.py`

**Interfaces:**
- Consumes: Task 1 common types and canonical hashing.
- Produces: `EvaluationCase` as an outer discriminated union of five task-specific Case models, `EVALUATION_CASE_ADAPTER: TypeAdapter[EvaluationCase]`, five expected-value models, `DatasetManifest`, `CaseResource`, `EvidenceReference`, `EvidenceMappingManifest`, `CriticalClaimRubric`, and `validate_privacy_boundary(value: JsonValue) -> None`.
- Produces: `EvaluationContext` with only `prescription_fixture`, `medication_fixtures`, `patient_context_fixture`, and `runtime_fixture`.

- [ ] **Step 1: Write failing task-union, provenance, and privacy tests**

```python
def test_retrieval_case_rejects_answer_gold_fields(valid_retrieval_case: dict[str, object]) -> None:
    valid_retrieval_case["expected"]["gold_claims"] = []
    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER.validate_python(valid_retrieval_case)

def test_privacy_boundary_rejects_nested_ocr_raw_without_echoing_value() -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        validate_privacy_boundary({"context": {"nested": {"ocr_raw": "SECRET_SENTINEL"}}})
    assert caught.value.code == EvaluationErrorCode.PRIVACY_FIELD_FORBIDDEN
    assert "SECRET_SENTINEL" not in str(caught.value)
```

- [ ] **Step 2: Run Task 2 tests and observe missing-schema failures**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation/test_authoring_schemas.py ai_worker/tests/evaluation/test_privacy_validation.py -q`

- [ ] **Step 3: Implement strict authoring models and recursive privacy validation**

Use `EvaluationCase = Annotated[RetrievalCase | AnswerQualityCase | AnswerGroundingCase | SafetyCase | EndToEndRagCase, Field(discriminator="task_type")]`; each task-specific Case binds its `Literal` task type to exactly one expected-value model. Non-applicable expected fields remain present and must be explicit null. Implement the exact evidence types `PRESCRIPTION`, `KNOWLEDGE_CHUNK`, `INTERACTION_RULE`, `LIFESTYLE_GUIDELINE`, `SAFETY_POLICY`; `DatasetManifest` requires exactly one of `fixture_git_commit_sha` and `protected_artifact_receipt_ref`, and requires `deidentification_approval_receipt_ref` only for `APPROVED_DEIDENTIFIED`.

Normalize deny keys case-insensitively after removing `-` and `_`. Reject the complete design denylist and value sentinels for email, Korean phone number, resident-registration-number shape, Bearer token, and known secret prefixes. Store only safe JSON Pointer paths in errors.

- [ ] **Step 4: Run Task 2 tests and Task 1 regression tests green**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation/test_canonical.py ai_worker/tests/evaluation/test_common_schemas.py ai_worker/tests/evaluation/test_authoring_schemas.py ai_worker/tests/evaluation/test_privacy_validation.py -q
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run mypy ai_worker/tasks/evaluation
```

- [ ] **Step 5: Commit Task 2**

```bash
git add ai_worker/tasks/evaluation ai_worker/tests/evaluation
git commit -m "✨ feat: RAG 평가 authoring과 privacy 계약 추가"
```

### Task 3: Evaluation profile, suite, and immutable policy schemas

**Files:**
- Create: `ai_worker/tasks/evaluation/schemas/policy.py`
- Modify: `ai_worker/tasks/evaluation/schemas/__init__.py`
- Test: `ai_worker/tests/evaluation/test_policy_schemas.py`

**Interfaces:**
- Consumes: Task 1 references, states, actor identities, canonical hash primitives.
- Produces: `EvaluationProfile`, `SuiteDefinition`, `ComparisonPolicy`, `EvaluationPolicy`, ordered policy members, comparison scopes, and immutable hash validation helpers.

- [ ] **Step 1: Write failing policy invariants**

```python
def test_release_profile_requires_end_to_end_holdout_and_safety(profile_payload: dict[str, object]) -> None:
    profile_payload["runtime_eligible"] = True
    profile_payload["required_experiment_types"] = ["KNOWLEDGE_RETRIEVAL"]
    with pytest.raises(ValidationError):
        EvaluationProfile.model_validate(profile_payload)

def test_comparison_policy_rejects_self_approval(policy_payload: dict[str, object]) -> None:
    policy_payload["approved_by"] = policy_payload["proposed_by"]
    with pytest.raises(ValidationError):
        ComparisonPolicy.model_validate(policy_payload)
```

- [ ] **Step 2: Run the policy tests and confirm RED**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation/test_policy_schemas.py -q`

- [ ] **Step 3: Implement policy models and cross-field validators**

`runtime_eligible=true` must require `END_TO_END_RAG`, `HOLDOUT`, and `SAFETY_REGRESSION`. Comparison scopes own metric/version, partition/slice, required, unit/estimator, minimum case and independent group counts, cluster dimension, canonical decimal threshold, decision basis, CI method/version/parameters, and nullable seed. Evaluation Policy members reject duplicate natural keys, duplicate `member_order`, and mismatched `member_manifest_hash`.

- [ ] **Step 4: Run policy tests plus all prior evaluation tests, Ruff, and mypy**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation/test_policy_schemas.py ai_worker/tests/evaluation/test_common_schemas.py ai_worker/tests/evaluation/test_canonical.py -q
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run mypy ai_worker/tasks/evaluation
```

- [ ] **Step 5: Commit Task 3**

```bash
git add ai_worker/tasks/evaluation ai_worker/tests/evaluation
git commit -m "✨ feat: RAG 평가 profile과 policy 계약 추가"
```

### Task 4: Eight result artifacts and deterministic JSON Schema export

**Files:**
- Create: `ai_worker/tasks/evaluation/schemas/artifacts.py`
- Create: `ai_worker/tasks/evaluation/schema_exports.py`
- Modify: `ai_worker/tasks/evaluation/schemas/__init__.py`
- Create: `evals/schemas/1.0.0/authoring/*.json`
- Create: `evals/schemas/1.0.0/policy/*.json`
- Create: `evals/schemas/1.0.0/artifacts/*.json`
- Create: `evals/schemas/1.0.0/operational/rag-eval.validation-receipt.schema.json`
- Test: `ai_worker/tests/evaluation/test_artifact_schemas.py`
- Test: `ai_worker/tests/evaluation/test_schema_exports.py`

**Interfaces:**
- Consumes: Tasks 1–3 models.
- Produces: one strict Pydantic model for each of the eight fixed Result Artifact IDs and `ValidationReceipt` outside that set.
- Produces: `schema_documents() -> dict[str, dict[str, JsonValue]]`, `normalize_schema_document(document: dict[str, JsonValue]) -> dict[str, JsonValue]`, and `write_schema_documents(root: Path) -> None` with deterministic canonical bytes and no silent drift. Normalization removes non-contract `title` and `description` metadata recursively but retains `$schema`, `$id`, constraints, and definitions.

- [ ] **Step 1: Write failing artifact-state and schema-set tests**

```python
EXPECTED_ARTIFACT_IDS = {
    "rag-eval.run", "rag-eval.case-result", "rag-eval.metrics", "rag-eval.suite-results",
    "rag-eval.comparison", "rag-eval.gate", "rag-eval.failure", "rag-eval.content-manifest",
}

def test_artifact_registry_contains_exactly_eight_ids() -> None:
    assert set(RESULT_ARTIFACT_MODELS) == EXPECTED_ARTIFACT_IDS

def test_incomplete_run_rejects_decision_and_content_manifest(valid_run: dict[str, object]) -> None:
    valid_run.update(execution_status="ERROR", decision_status="FAIL", result_content_manifest_hash="a" * 64)
    with pytest.raises(ValidationError):
        RagEvaluationRun.model_validate(valid_run)
```

- [ ] **Step 2: Run artifact/schema-export tests and confirm RED**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation/test_artifact_schemas.py ai_worker/tests/evaluation/test_schema_exports.py -q`

- [ ] **Step 3: Implement the eight models and validation receipt**

All Result objects and JSONL records require `schema_id`, `schema_version="1.0.0"`, and canonical UUID `run_id`. Enforce task-specific actual nullability, no `passed` boolean, completed-only decisions, completed/inconclusive metric counts and reason codes, required-member gate aggregation, safe 500-character Failure summaries, sorted content entries, content-manifest self exclusion, and runtime-eligible Local Guard bindings. `ValidationReceipt` uses `validation_id`, never `run_id`, always `release_eligible=false`, and permits only `COMPLETED/N/A`, `INVALID/null`, or `ERROR/null`.

- [ ] **Step 4: Export schemas, then prove committed files exactly match fresh canonical export**

Run the exporter once to create the versioned files. In the parity test, export to `tmp_path`, compare relative filename sets and exact bytes with `evals/schemas/1.0.0`, verify all `$schema`, `$id`, and root `additionalProperties=false`, and verify the Result registry contains exactly eight IDs.

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run python -m ai_worker.tasks.evaluation.schema_exports --output evals/schemas/1.0.0
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation/test_artifact_schemas.py ai_worker/tests/evaluation/test_schema_exports.py -q
```

- [ ] **Step 5: Run all evaluation tests, Ruff, format check, and mypy**

```bash
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation -q
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run mypy ai_worker/tasks/evaluation
```

- [ ] **Step 6: Commit Task 4**

```bash
git add ai_worker/tasks/evaluation ai_worker/tests/evaluation evals/schemas/1.0.0
git commit -m "✨ feat: RAG 평가 결과 artifact schema 8종 추가"
```

### Task 5: Fail-closed dataset loader and five-case synthetic fixture

**Files:**
- Create: `ai_worker/tasks/evaluation/loaders.py`
- Create: `evals/retrieval/cases/dev-foundation-v1/*.json`
- Create: `evals/retrieval/evidence/dev-foundation-v1.evidence-mapping.json`
- Create: `evals/retrieval/manifests/dev-foundation-v1.dataset.json`
- Create: `evals/retrieval/manifests/dev-foundation-v1.critical-claim-rubric.json`
- Create: `evals/profiles/dev-foundation-v1.profile.json`
- Create: `evals/policies/dev-foundation-v1.comparison-policy.json`
- Create: `evals/policies/dev-foundation-v1.evaluation-policy.json`
- Create: `evals/suites/dev-foundation-v1.suite.json`
- Test: `ai_worker/tests/evaluation/test_loaders.py`
- Test: `ai_worker/tests/evaluation/test_foundation_fixture.py`

**Interfaces:**
- Consumes: Tasks 1–4 contracts and schema registry.
- Produces: `load_json_object(path: Path, model: type[T]) -> T` and `load_dataset(manifest_path: Path, *, evals_root: Path) -> ValidatedDataset`.
- `ValidatedDataset` contains immutable manifest, ordered cases, evidence mapping, rubric, profile, policies, and suite; it has no execution method.

- [ ] **Step 1: Write failing loader and fixture acceptance tests**

```python
def test_foundation_dataset_has_one_case_per_task_and_only_dev() -> None:
    loaded = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)
    assert len(loaded.cases) == 5
    assert {case.task_type for case in loaded.cases} == {
        TaskType.RETRIEVAL, TaskType.ANSWER_QUALITY, TaskType.ANSWER_GROUNDING,
        TaskType.SAFETY, TaskType.END_TO_END_RAG,
    }
    assert {case.partition for case in loaded.cases} == {Partition.DEV}

def test_loader_rejects_cross_partition_leakage(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.move_case_to_partition("rag-dev-safety-001", "HOLDOUT")
    tmp_dataset.share_leakage_value("question_template", "rag-dev-retrieval-001", "rag-dev-safety-001")
    with pytest.raises(EvaluationValidationError, match="EVAL_LEAKAGE_CROSS_PARTITION"):
        load_dataset(tmp_dataset.manifest, evals_root=tmp_dataset.root)
```

- [ ] **Step 2: Run loader tests and observe RED**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation/test_loaders.py ai_worker/tests/evaluation/test_foundation_fixture.py -q`

- [ ] **Step 3: Implement duplicate-key-aware loading, containment, and semantic validation**

Pipeline order is bytes read, UTF-8/I-JSON parse with duplicate-key rejection, strict Pydantic validation, NFC relative-path containment and symlink rejection, exact resource file hash, cross-resource semantics, then canonical manifest hashes. Validate Dataset/Case identity, exact partition counts, case ID/path/evidence duplicates, four-axis leakage, Rubric exact match, all Evidence references mapped, evidence/resource/manifest hashes, provenance, classification, and privacy. Never include input values in exceptions.

- [ ] **Step 4: Add the exact synthetic fixture**

Use dataset code/version `rag-dev-foundation`/`1.0.0`; case IDs `rag-dev-retrieval-001`, `rag-dev-answer-quality-001`, `rag-dev-answer-grounding-001`, `rag-dev-safety-001`, `rag-dev-end-to-end-001`; all classification `SYNTHETIC`, partition `DEV`, and only `SYNTHETIC_*` context tokens. Use evidence IDs `ev-synthetic-prescription-001`, `ev-synthetic-chunk-001`, `ev-synthetic-rule-001`, `ev-synthetic-guideline-001`, and `ev-synthetic-safety-policy-001`. Profile, policies, rubric, suite, resource hashes, and manifest hashes must be real recomputed values, never placeholders. Suite adapter is `validation-only.v1`, and no Release PASS is represented.

- [ ] **Step 5: Add parameterized negative mutations and run the complete loader suite**

Cover every stable code: invalid JSON, duplicate JSON key, schema, digest format, digest mismatch, path, missing resource, case duplicate, partition enum/count, each leakage axis, Rubric mismatch, Evidence mapping, provenance, state combination, forbidden privacy key, privacy value, and missing deidentification approval. Expectations assert error code and absence of the injected sentinel from exception text.

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation/test_loaders.py ai_worker/tests/evaluation/test_foundation_fixture.py ai_worker/tests/evaluation/test_privacy_validation.py -q
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation -q
```

- [ ] **Step 6: Commit Task 5**

```bash
git add ai_worker/tasks/evaluation ai_worker/tests/evaluation evals/retrieval evals/profiles evals/policies evals/suites
git commit -m "✨ feat: RAG 평가 dataset loader와 합성 fixture 추가"
```

### Task 6: Validation-only CLI, atomic receipt, and repository guidance

**Files:**
- Create: `ai_worker/tasks/evaluation/cli.py`
- Create: `ai_worker/tasks/evaluation/__main__.py`
- Modify: `ai_worker/tasks/evaluation/__init__.py`
- Modify: `evals/README.md`
- Test: `ai_worker/tests/evaluation/test_cli.py`

**Interfaces:**
- Consumes: `load_dataset`, `ValidationReceipt`, canonical JSON bytes, stable safe errors.
- Produces: `main(argv: Sequence[str] | None = None) -> int` and `publish_receipt_no_clobber(destination: Path, payload: bytes) -> None`.

- [ ] **Step 1: Write failing CLI outcome and no-clobber tests**

```python
def test_cli_validates_fixture_without_creating_release_artifacts(tmp_path: Path) -> None:
    result = tmp_path / "receipt.json"
    assert main(["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)]) == 0
    receipt = json.loads(result.read_text())
    assert (receipt["execution_status"], receipt["decision_status"]) == ("COMPLETED", "N/A")
    assert receipt["release_eligible"] is False
    assert not {"metrics", "gate", "passed"} & receipt.keys()

def test_cli_does_not_overwrite_existing_result(tmp_path: Path) -> None:
    result = tmp_path / "receipt.json"
    result.write_bytes(b"existing")
    assert main(["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)]) == 2
    assert result.read_bytes() == b"existing"
```

- [ ] **Step 2: Run CLI tests and observe RED**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation/test_cli.py -q`

- [ ] **Step 3: Implement validation-only CLI and atomic no-clobber publication**

Require `--result` under repository `evals/validation-results/` in the real CLI; expose an injected allowed root for isolated tests. Reject destination/parent symlinks, existing destination, and existing lock. Create `<destination>.lock` and same-directory temp with `O_CREAT|O_EXCL` mode `0600`; write once, file `fsync`, then use same-filesystem hard-link publication so an existing destination fails atomically. Remove temp, directory-fsync where supported, and remove lock. If no atomic no-clobber primitive exists, return `EVAL_ATOMIC_PUBLISH_UNSUPPORTED` rather than ordinary rename. Exit codes are `0` valid, `2` invalid/path conflict, and `1` internal error. Receipt and stderr contain only stable codes and safe relative paths.

- [ ] **Step 4: Document the command and non-release boundary**

Add this exact example to `evals/README.md`:

```bash
uv run python -m ai_worker.tasks.evaluation validate \
  --manifest evals/retrieval/manifests/dev-foundation-v1.dataset.json \
  --result evals/validation-results/dev-foundation-v1.validation.json
```

State that it validates only, produces no Run/Metric/Gate/Markdown report, never calls a Provider, and cannot change `PUBLIC_TRACK_F`.

- [ ] **Step 5: Run Issue #122 checks and inspect the fixture receipt**

```bash
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation -q
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run mypy ai_worker/tasks/evaluation
uv run python -m ai_worker.tasks.evaluation validate --manifest evals/retrieval/manifests/dev-foundation-v1.dataset.json --result evals/validation-results/dev-foundation-v1.validation.json
git diff --check
```

After inspection, remove only the generated ignored validation receipt and its empty generated directory if applicable; do not remove committed fixtures or schemas.

- [ ] **Step 6: Commit Task 6**

```bash
git add ai_worker/tasks/evaluation ai_worker/tests/evaluation evals/README.md
git commit -m "✨ feat: RAG 평가 validation CLI 추가"
```

## Final Verification

Run fresh from the branch head:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests/evaluation -q
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run mypy ai_worker/tasks/evaluation
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/private/tmp/ah_issue122_uv_cache uv run pytest ai_worker/tests -q -p no:cacheprovider
git diff --check
git status --short
```

Inspect the full branch diff against its `origin/develop` merge base and confirm no DB/API/Frontend/runtime/Provider/release-flag change exists, every committed fixture is synthetic, all generated result paths remain ignored, and the eight Result Artifact IDs are exact.
