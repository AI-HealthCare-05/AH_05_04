# RAG Evaluation Review Provenance 1.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add immutable RAG Evaluation Schema Set 1.2.0 so DRAFT review provenance has no fictional reviewer, real review is traceable, and existing Schema Set 1.0/1.1 behavior stays unchanged.

**Architecture:** New `common_v1_2`, authoring, and policy model variants preserve the existing 1.1 evaluation behavior while replacing only actor/provenance types. Registry and loader dispatch select the complete version bundle so every Dataset graph resource is validated against the matching member version. Canonical export and contract documents bind the resulting 18-member set hash.

**Tech Stack:** Python 3.13, Pydantic v2, Draft 2020-12 JSON Schema, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-03-issue-241-rag-evaluation-review-provenance-1-2-design.md`

## Global Constraints

- Preserve committed `evals/schemas/1.0.0/` and `evals/schemas/1.1.0/` bytes exactly.
- Keep the Schema Set at exactly 18 unique member paths and schema IDs.
- Add no dependencies and use only synthetic evaluation fixtures.
- `DRAFT` requires null review/approval fields and empty review evidence; `REVIEWED` and `APPROVED` require non-null reviewer fields and at least one immutable review-evidence reference.
- `EVALUATION_REVIEWER` is an internal review role, never an external medical approval substitute.
- Do not modify PR #236 Dataset resources, Freeze state, or Runner behavior in this Issue.

---

### Task 1: Define 1.2 actor and review provenance models

**Files:**
- Create: `ai_worker/tasks/evaluation/schemas/common_v1_2.py`
- Test: `ai_worker/tests/evaluation/test_common_schemas.py`

**Interfaces:**
- Consumes: immutable scalar aliases, `ActorNamespace`, `ExternalMedicalReviewStatus`, `ImmutableReference`, and `ReviewProvenance` behavior from `schemas/common.py`.
- Produces: `ActorRoleV12`, `ActorRefV12`, and `ReviewProvenanceV12` for versioned authoring and policy models.

- [ ] **Step 1: Write failing provenance-state tests**

```python
def test_review_provenance_v12_accepts_draft_without_reviewer() -> None:
    provenance = ReviewProvenanceV12.model_validate(_v12_payload(status="DRAFT"))
    assert provenance.reviewed_by is None
    assert provenance.reviewed_at is None


@pytest.mark.parametrize(
    ("status", "reviewed_by", "reviewed_at", "evidence_review_refs"),
    [
        ("DRAFT", _review_actor("reviewer", "EVALUATION_REVIEWER"), None, []),
        ("DRAFT", None, "2026-09-03T00:01:00.000000Z", []),
        ("REVIEWED", None, "2026-09-03T00:01:00.000000Z", [_review_ref()]),
        ("REVIEWED", _review_actor("reviewer", "EVALUATION_REVIEWER"), None, [_review_ref()]),
        ("REVIEWED", _review_actor("reviewer", "EVALUATION_REVIEWER"), "2026-09-03T00:01:00.000000Z", []),
    ],
)
def test_review_provenance_v12_rejects_inconsistent_review_state(
    status: str,
    reviewed_by: dict[str, str] | None,
    reviewed_at: str | None,
    evidence_review_refs: list[dict[str, str]],
) -> None:
    payload = _v12_payload(status=status)
    payload.update(
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        evidence_review_refs=evidence_review_refs,
    )
    with pytest.raises(ValidationError):
        ReviewProvenanceV12.model_validate(payload)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue241_uv_cache uv run pytest ai_worker/tests/evaluation/test_common_schemas.py -q`

Expected: FAIL because `ReviewProvenanceV12` is not importable.

- [ ] **Step 3: Implement immutable 1.2 types**

```python
class ActorRoleV12(StrEnum):
    EVALUATION_IMPLEMENTER = "EVALUATION_IMPLEMENTER"
    DATASET_CUSTODIAN = "DATASET_CUSTODIAN"
    PRODUCT_SAFETY_REVIEWER = "PRODUCT_SAFETY_REVIEWER"
    MEDICAL_REVIEWER = "MEDICAL_REVIEWER"
    PRIVACY_REVIEWER = "PRIVACY_REVIEWER"
    SYSTEM_VALIDATOR = "SYSTEM_VALIDATOR"
    EVALUATION_REVIEWER = "EVALUATION_REVIEWER"


class ReviewProvenanceV12(StrictContractModel):
    reviewed_by: ActorRefV12 | None
    reviewed_at: UtcTimestamp | None

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        self._validate_actor_rules()
        self._validate_team_gold_rules()
        self._validate_external_review_rules()
        self._validate_evidence_refs()
        return self
```

Implement the complete state matrix, actor identity separation for non-null actors, timestamp ordering only when its prior event exists, existing external-review validation, and sorted unique immutable evidence references.

- [ ] **Step 4: Run the focused model tests and verify GREEN**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue241_uv_cache uv run pytest ai_worker/tests/evaluation/test_common_schemas.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the model and test**

```bash
git add ai_worker/tasks/evaluation/schemas/common_v1_2.py ai_worker/tests/evaluation/test_common_schemas.py
git commit -m "feat(evals): add review provenance schema 1.2"
```

### Task 2: Add versioned authoring and policy artifacts

**Files:**
- Create: `ai_worker/tasks/evaluation/schemas/authoring_v1_2.py`
- Create: `ai_worker/tasks/evaluation/schemas/policy_v1_2.py`
- Modify: `ai_worker/tasks/evaluation/schemas/__init__.py`
- Test: `ai_worker/tests/evaluation/test_authoring_v1_1_schemas.py`
- Test: `ai_worker/tests/evaluation/test_policy_schemas.py`

**Interfaces:**
- Consumes: `ReviewProvenanceV12`, `ActorRoleV12`, 1.1 Case expected/runtime models, and existing authoring/policy validators.
- Produces: `EVALUATION_CASE_ADAPTER_V1_2`, `DatasetManifestV12`, `EvidenceMappingManifestV12`, `CriticalClaimRubricV12`, `ProtectedArtifactReceiptV12`, `EvaluationProfileV12`, `SuiteDefinitionV12`, and `EvaluationPolicyV12`.

- [ ] **Step 1: Write failing 1.2 model tests**

```python
def test_safety_case_v12_accepts_draft_without_reviewer() -> None:
    payload = _safety_case().model_dump(mode="json")
    payload["schema_version"] = "1.2.0"
    payload["review_provenance"] = _v12_draft_provenance()
    assert SafetyCaseV12.model_validate(payload).schema_version == "1.2.0"


def test_policy_v12_rejects_v11_provenance_shape() -> None:
    payload = _profile_payload(schema_version="1.2.0")
    payload["review_provenance"] = _v11_draft_with_reviewer()
    with pytest.raises(ValidationError):
        EvaluationProfileV12.model_validate(payload)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue241_uv_cache uv run pytest ai_worker/tests/evaluation/test_authoring_v1_1_schemas.py ai_worker/tests/evaluation/test_policy_schemas.py -q`

Expected: FAIL because 1.2 model modules and adapters are absent.

- [ ] **Step 3: Implement 1.2 artifact variants**

```python
class DatasetManifestV12(DatasetManifestV11):
    schema_version: Literal["1.2.0"]
    review_provenance: ReviewProvenanceV12


class EvaluationPolicyV12(EvaluationPolicy):
    schema_version: Literal["1.2.0"]
    review_provenance: ReviewProvenanceV12
```

Define all five discriminated Case variants rather than reusing the 1.1 adapter, retain their safety approval-role validators, and create corresponding v1.2 Evidence Mapping, Rubric, and receipt models. Define Profile, Suite, and Evaluation Policy variants; retain Comparison Policy at `1.0.0`.

- [ ] **Step 4: Run focused artifact tests and verify GREEN**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue241_uv_cache uv run pytest ai_worker/tests/evaluation/test_authoring_v1_1_schemas.py ai_worker/tests/evaluation/test_policy_schemas.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the versioned artifacts**

```bash
git add ai_worker/tasks/evaluation/schemas/authoring_v1_2.py ai_worker/tasks/evaluation/schemas/policy_v1_2.py ai_worker/tasks/evaluation/schemas/__init__.py ai_worker/tests/evaluation/test_authoring_v1_1_schemas.py ai_worker/tests/evaluation/test_policy_schemas.py
git commit -m "feat(evals): version provenance artifacts for schema 1.2"
```

### Task 3: Version registry, canonical exports, and loader graph validation

**Files:**
- Modify: `ai_worker/tasks/evaluation/schema_registry.py`
- Modify: `ai_worker/tasks/evaluation/schema_exports.py`
- Modify: `ai_worker/tasks/evaluation/loaders.py`
- Test: `ai_worker/tests/evaluation/test_schema_exports.py`
- Test: `ai_worker/tests/evaluation/test_loaders.py`

**Interfaces:**
- Consumes: 1.2 authoring/policy variants from Task 2 and existing 1.0/1.1 registries.
- Produces: `SCHEMA_REGISTRY_V1_2`, `SCHEMA_REGISTRIES["1.2.0"]`, fresh 1.2 exports, and loader bundle dispatch across every provenance-bearing Dataset artifact.

- [ ] **Step 1: Write failing export and loader tests**

```python
def test_schema_set_v1_2_has_eight_versioned_provenance_members() -> None:
    versions = {entry.schema_id: entry.member_version for entry in SCHEMA_REGISTRIES["1.2.0"]}
    assert {schema_id for schema_id, version in versions.items() if version == "1.2.0"} == EXPECTED_V12_IDS


def test_loader_rejects_schema_set_v1_2_mixed_policy_member_version(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.set_manifest_schema_version("1.2.0")
    tmp_dataset.set_policy_schema_version("1.0.0")
    with pytest.raises(EvaluationValidationError, match="MANIFEST_INVALID"):
        load_dataset(tmp_dataset.manifest_path, evals_root=tmp_dataset.root)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue241_uv_cache uv run pytest ai_worker/tests/evaluation/test_schema_exports.py ai_worker/tests/evaluation/test_loaders.py -q`

Expected: FAIL because Schema Set 1.2 and its loader dispatch do not exist.

- [ ] **Step 3: Implement registry and export conditions**

```python
PROVENANCE_MEMBER_IDS = frozenset(
    {
        "rag-eval.case",
        "rag-eval.dataset-manifest",
        "rag-eval.evidence-mapping-manifest",
        "rag-eval.critical-claim-rubric",
        "rag-eval.evaluation-profile",
        "rag-eval.suite-definition",
        "rag-eval.evaluation-policy",
        "rag-eval.protected-artifact-receipt",
    }
)
```

Add 1.2 review-provenance JSON Schema conditionals that match the state matrix exactly. Keep old schema exports unmodified and add the 1.2 canonical export directory only after fresh export tests pass.

- [ ] **Step 4: Implement complete loader bundle dispatch**

```python
@dataclass(frozen=True, slots=True)
class _AuthoringContract:
    manifest_model: type[BaseModel]
    case_adapter: TypeAdapter[Any]
    evidence_mapping_model: type[BaseModel]
    rubric_model: type[BaseModel]
    profile_model: type[BaseModel]
    evaluation_policy_model: type[BaseModel]
    suite_model: type[BaseModel]
    protected_artifact_receipt_model: type[BaseModel]
```

Pass the selected bundle to every resource loader, require selected artifact `schema_version` values to exact-match registry member versions, and retain the existing 1.1 FROZEN child-Gold closure for 1.2.

- [ ] **Step 5: Run export and loader tests and verify GREEN**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue241_uv_cache uv run pytest ai_worker/tests/evaluation/test_schema_exports.py ai_worker/tests/evaluation/test_loaders.py -q`

Expected: PASS.

- [ ] **Step 6: Commit registry, export, and loader work**

```bash
git add ai_worker/tasks/evaluation/schema_registry.py ai_worker/tasks/evaluation/schema_exports.py ai_worker/tasks/evaluation/loaders.py ai_worker/tests/evaluation/test_schema_exports.py ai_worker/tests/evaluation/test_loaders.py
git commit -m "feat(evals): dispatch complete schema set 1.2"
```

### Task 4: Commit canonical schema set and contract documentation

**Files:**
- Create: `evals/schemas/1.2.0/**`
- Modify: `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`
- Create: `docs/governance/decisions/2026-09-03-rag-evaluation-schema-set-1-2-freeze.md`
- Modify: `docs/contracts/README.md`
- Modify: `evals/README.md`
- Test: `ai_worker/tests/evaluation/test_schema_exports.py`
- Test: `ai_worker/tests/evaluation/test_retryable_error_classification.py`

**Interfaces:**
- Consumes: fresh 1.2 schema export and deterministic Schema Set hash from Task 3.
- Produces: committed canonical Schema Set 1.2, matching Decision/contract references, and document-hash regression coverage.

- [ ] **Step 1: Write failing canonical export and document-hash tests**

```python
def test_committed_schema_set_1_2_matches_fresh_canonical_export_byte_for_byte(tmp_path: Path) -> None:
    export_schemas(tmp_path, schema_set_version="1.2.0")
    assert_tree_bytes_equal(tmp_path, EVALS_ROOT / "schemas" / "1.2.0")


def test_documented_schema_set_1_2_hash_matches_canonical_export() -> None:
    expected = _schema_set_hash_from_committed_files("1.2.0")
    assert _extract_schema_set_hash(CONTRACT_PATH, "1.2.0") == expected
    assert _extract_schema_set_hash(DECISION_PATH, "1.2.0") == expected
    assert _extract_schema_set_hash(EVALS_README, "1.2.0") == expected
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue241_uv_cache uv run pytest ai_worker/tests/evaluation/test_schema_exports.py ai_worker/tests/evaluation/test_retryable_error_classification.py -q`

Expected: FAIL because the committed 1.2 directory and documented hash do not exist.

- [ ] **Step 3: Generate and commit canonical 1.2 schemas**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue241_uv_cache uv run python -m ai_worker.tasks.evaluation.schema_exports --output /private/tmp/ah_issue241_schema_export --schema-set-version 1.2.0`

Copy the exact generated 18 files into `evals/schemas/1.2.0/` using a non-destructive repository patch. Do not regenerate or alter 1.0/1.1 files.

- [ ] **Step 4: Update contract and Decision documents with the computed hash**

Record the exact generated `rag-eval.schema-set@1.2.0` SHA-256 in the target RAG evaluation contract, a new Decision, the contract index, and `evals/README.md`. State the DRAFT/REVIEWED/APPROVED matrix, `EVALUATION_REVIEWER` boundary, eight changed member versions, #214 migration order, and unchanged external approval/Production gates.

- [ ] **Step 5: Run canonical export and documentation tests and verify GREEN**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue241_uv_cache uv run pytest ai_worker/tests/evaluation/test_schema_exports.py ai_worker/tests/evaluation/test_retryable_error_classification.py -q`

Expected: PASS.

- [ ] **Step 6: Commit canonical artifacts and documents**

```bash
git add evals/schemas/1.2.0 docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md docs/governance/decisions/2026-09-03-rag-evaluation-schema-set-1-2-freeze.md docs/contracts/README.md evals/README.md ai_worker/tests/evaluation/test_schema_exports.py ai_worker/tests/evaluation/test_retryable_error_classification.py
git commit -m "docs(evals): freeze review provenance schema set 1.2"
```

### Task 5: Run integrated verification and prepare the PR

**Files:**
- Modify: `docs/superpowers/plans/2026-09-03-rag-evaluation-review-provenance-1-2.md` (mark completed checks only)

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified #241 branch ready for Product·Safety, Gold·Fixture, and Schema·Loader review.

- [ ] **Step 1: Run focused and full Evaluation tests**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue241_uv_cache uv run pytest ai_worker/tests/evaluation -q`

Expected: PASS, including existing 1.0 and 1.1 regression fixtures.

- [ ] **Step 2: Run static checks**

Run: `UV_CACHE_DIR=/private/tmp/ah_issue241_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation`

Run: `UV_CACHE_DIR=/private/tmp/ah_issue241_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check`

Run: `UV_CACHE_DIR=/private/tmp/ah_issue241_uv_cache uv run mypy ai_worker/tasks/evaluation`

Expected: each command exits 0.

- [ ] **Step 3: Inspect immutable boundaries and diff**

Run: `git diff --exit-code HEAD~1 -- evals/schemas/1.0.0 evals/schemas/1.1.0`

Run: `git diff --check origin/develop...HEAD`

Run: `git status --short`

Expected: old schema directories unchanged, no whitespace errors, and only intended tracked changes.

- [ ] **Step 4: Commit final verification metadata if the plan was updated**

```bash
git add docs/superpowers/plans/2026-09-03-rag-evaluation-review-provenance-1-2.md
git commit -m "docs(evals): record schema 1.2 verification plan"
```

- [ ] **Step 5: Push and create the #241 PR**

```bash
git push -u origin feat/241-rag-eval-provenance-schema
gh pr create --base develop --head feat/241-rag-eval-provenance-schema --title "[Track F][Evaluation] RAG-EVAL-001C Review Provenance Schema 1.2 보정" --body-file /tmp/rag_eval_schema_1_2_pr.md
```

Request `@hazelnutflavoured` for Product·Safety·Evaluation provenance approval, `@phina-io` for Schema·Loader/export parity, and `@Jye-rookie` for Gold·Fixture role semantics. Keep the PR non-mergeable until the named responsible reviewer approves.

## Plan Self-Review

- Spec coverage: Tasks 1–4 cover state semantics, role meaning, versioned artifact models, loader dispatch, schema export, hash documentation, and #214 migration boundary. Task 5 verifies integration and PR review routing.
- Placeholder scan: all implementation steps name concrete models, files, commands, and expected assertions.
- Type consistency: `ReviewProvenanceV12` and `ActorRefV12` are the only new common interfaces; all versioned artifacts and the loader bundle consume those names consistently.
