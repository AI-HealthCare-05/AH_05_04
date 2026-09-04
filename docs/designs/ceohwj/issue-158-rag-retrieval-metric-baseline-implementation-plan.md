# RAG Retrieval Metric·DEV Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 합성 DEV Retrieval Dataset에서 `RET-L` Baseline과 `RET-HR` Candidate의 결정적 Retrieval Metric·95% CI·비교 Artifact를 생성하고 `evals/results/<run-id>/`에 보존한다.

**Architecture:** 기존 #157 Runner·Reporter를 유지하면서 versioned replay fixture를 `RetrievalCaseResult`로 변환하는 Adapter, Case 결과만 소비하는 독립 Metric Kernel, 두 불변 Run Bundle을 검증하는 Comparison Builder를 추가한다. 공통 Source/Index/filter/embedding/parser provenance는 두 config의 동일 `model_config_hash`로 결속한다. Baseline은 기존 7-file Bundle로, Candidate는 `comparison.json`을 포함한 8-file Bundle로 원자 발행하며 JSON을 기계 정본으로 사용한다.

**Tech Stack:** Python 3.13, Pydantic v2, `Decimal`/`random.Random`, pytest, Ruff, mypy, 기존 canonical JSON·Schema Set 1.2 authoring·Artifact Schema 1.0.0·atomic publisher.

**Spec:** `docs/designs/ceohwj/issue-158-rag-retrieval-metric-baseline-design.md`

## Global Constraints

- `DEV`와 `SYNTHETIC`만 실행하며 `HOLDOUT`·`SAFETY_REGRESSION`을 load·execute·observe하지 않는다.
- Authoring graph는 Schema Set 1.2의 `DRAFT` provenance를 사용하고, 공유 Evaluation Artifact Schema 1.0.0의 필드·enum·requiredness는 변경하지 않는다.
- Resolver Candidate 결과는 Retrieval Case·Metric·comparison에 포함하지 않는다.
- 승인된 Release threshold가 없으므로 비교 delta는 기록하되 Release PASS를 만들지 않는다.
- 실제 환자정보, OCR 원문, 보험코드, Provider trace, credential을 fixture·result·log에 기록하지 않는다.
- 기존 Run ID·결과 디렉터리는 덮어쓰거나 자동 삭제하지 않는다.
- 새 외부 dependency를 추가하지 않는다.
- 모든 기능 단계는 실패 테스트 확인 후 최소 구현을 추가하는 TDD 순서를 따른다.

---

## File Structure

### 새 구현 파일

- `ai_worker/tasks/evaluation/retrieval_replay.py`: replay manifest 검증과 `RetrievalCaseResult` Adapter.
- `ai_worker/tasks/evaluation/retrieval_metrics.py`: Retrieval observation, five Metric 계산, bootstrap CI.
- `ai_worker/tasks/evaluation/comparison.py`: 기존 Run Bundle 로드·hash 검증·통제 변수 비교·delta 생성.

### 수정 구현 파일

- `ai_worker/tasks/evaluation/config.py`: replay path와 Source/Index/embedding/parser/filter provenance 결속.
- `ai_worker/tasks/evaluation/runner.py`: Adapter request에 variant context 전달, Retrieval failure record 생성.
- `ai_worker/tasks/evaluation/manifest.py`: 실제 Metric과 optional comparison 직렬화·semantic hash 검증.
- `ai_worker/tasks/evaluation/publisher.py`: 7-file baseline과 8-file candidate Bundle allowlist.
- `ai_worker/tasks/evaluation/reporter.py`: Retrieval 표, CI, 비교 표, DEV/HOLDOUT 경계 투영.
- `ai_worker/tasks/evaluation/cli.py`: built-in replay registry와 `--baseline-run-id` candidate 비교 흐름.
- `ai_worker/tasks/evaluation/errors.py`: Retrieval/Comparison의 안정 오류 code.
- `evals/README.md`: 실행 명령, 저장 위치, 결과 해석과 Git/CI 보존 규칙.

### 새 평가 자산

- `evals/retrieval/cases/rag-retrieval-dev-v1/*.json`: 5개 합성 Retrieval Case.
- `evals/retrieval/evidence/rag-retrieval-dev-v1.evidence-mapping.json`: Gold Evidence mapping.
- `evals/retrieval/evidence/resources/rag-retrieval-dev-v1/synthetic-retrieval-index.json`: 합성 index records.
- `evals/retrieval/manifests/rag-retrieval-dev-v1.dataset.json`: DEV-only Dataset Manifest.
- `evals/retrieval/manifests/rag-retrieval-dev-v1.critical-claim-rubric.json`: Retrieval Dataset 결속용 빈 Claim rubric.
- `evals/retrieval/replays/rag-retrieval-dev-v1/ret-l-v1.replay.json`: Baseline ranked results.
- `evals/retrieval/replays/rag-retrieval-dev-v1/ret-hr-v1.replay.json`: Candidate ranked results.
- `evals/configs/rag-retrieval-dev-ret-l-v1.execution.json`: Baseline execution request.
- `evals/configs/rag-retrieval-dev-ret-hr-v1.execution.json`: Candidate execution request.
- `evals/profiles/rag-retrieval-dev-v1.profile.json`: `KNOWLEDGE_RETRIEVAL/DEV` Profile.
- `evals/policies/rag-retrieval-dev-v1.comparison-policy.json`: Metric·CI·minimum sample diagnostic policy.
- `evals/policies/rag-retrieval-dev-v1.evaluation-policy.json`: Dataset graph policy.
- `evals/suites/rag-retrieval-dev-v1.suite.json`: replay Adapter와 exact Case set.

### 새/수정 테스트 파일

- `ai_worker/tests/evaluation/test_retrieval_replay.py`
- `ai_worker/tests/evaluation/test_retrieval_metrics.py`
- `ai_worker/tests/evaluation/test_comparison.py`
- `ai_worker/tests/evaluation/test_retrieval_dev_fixture.py`
- `ai_worker/tests/evaluation/test_runner.py`
- `ai_worker/tests/evaluation/test_result_manifest.py`
- `ai_worker/tests/evaluation/test_publisher.py`
- `ai_worker/tests/evaluation/test_reporter.py`
- `ai_worker/tests/evaluation/test_cli.py`

---

### Task 1: 합성 DEV Retrieval Dataset과 두 Replay Variant 고정

**Files:**
- Create: `evals/retrieval/cases/rag-retrieval-dev-v1/rag-ret-dev-001.json` through `rag-ret-dev-005.json`
- Create: `evals/retrieval/evidence/rag-retrieval-dev-v1.evidence-mapping.json`
- Create: `evals/retrieval/evidence/resources/rag-retrieval-dev-v1/synthetic-retrieval-index.json`
- Create: `evals/retrieval/manifests/rag-retrieval-dev-v1.dataset.json`
- Create: `evals/retrieval/manifests/rag-retrieval-dev-v1.critical-claim-rubric.json`
- Create: `evals/retrieval/replays/rag-retrieval-dev-v1/ret-l-v1.replay.json`
- Create: `evals/retrieval/replays/rag-retrieval-dev-v1/ret-hr-v1.replay.json`
- Create: `evals/profiles/rag-retrieval-dev-v1.profile.json`
- Create: `evals/policies/rag-retrieval-dev-v1.comparison-policy.json`
- Create: `evals/policies/rag-retrieval-dev-v1.evaluation-policy.json`
- Create: `evals/suites/rag-retrieval-dev-v1.suite.json`
- Test: `ai_worker/tests/evaluation/test_retrieval_dev_fixture.py`

**Interfaces:**
- Consumes: `load_dataset(Path, evals_root=Path) -> ValidatedDataset`, canonical self-hash 규칙, Schema Set 1.2.0.
- Produces: load 가능한 `rag-retrieval-dev@1.0.0`, exact Case IDs 5개, replay manifest 2개.

- [ ] **Step 1: Dataset invariants를 고정하는 실패 테스트 작성**

```python
def test_retrieval_dev_dataset_is_synthetic_dev_only_with_five_independent_cases() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)

    assert dataset.manifest.dataset_code == "rag-retrieval-dev"
    assert dataset.manifest.partition_counts.model_dump() == {
        "AUTHORING": 0,
        "DEV": 5,
        "HOLDOUT": 0,
        "SAFETY_REGRESSION": 0,
    }
    assert {case.task_type.value for case in dataset.cases} == {"RETRIEVAL"}
    assert {case.data_classification.value for case in dataset.cases} == {"SYNTHETIC"}
    assert len({case.leakage_group_ids.question_template for case in dataset.cases}) == 5
    assert all(len(case.expected.required_evidence_refs or ()) == 1 for case in dataset.cases)
```

- [ ] **Step 2: 테스트를 실행해 자산 부재 실패 확인**

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_retrieval_dev_fixture.py -q`

Expected: FAIL with `EVAL_RESOURCE_MISSING` or missing manifest path.

- [ ] **Step 3: 정확한 Case/Gold matrix로 합성 자산 작성**

| Case | question template group | required evidence | relevant evidence |
|---|---|---|---|
| `rag-ret-dev-001` | `SYNTHETIC_QT_MED_INFO_A` | `ev-ret-dev-med-a` | `ev-ret-dev-med-a`, `ev-ret-dev-med-a-detail` |
| `rag-ret-dev-002` | `SYNTHETIC_QT_PRECAUTION_B` | `ev-ret-dev-precaution-b` | `ev-ret-dev-precaution-b` |
| `rag-ret-dev-003` | `SYNTHETIC_QT_LIFESTYLE_C` | `ev-ret-dev-lifestyle-c` | `ev-ret-dev-lifestyle-c` |
| `rag-ret-dev-004` | `SYNTHETIC_QT_STORAGE_D` | `ev-ret-dev-storage-d` | `ev-ret-dev-storage-d` |
| `rag-ret-dev-005` | `SYNTHETIC_QT_MISSED_DOSE_E` | `ev-ret-dev-missed-dose-e` | `ev-ret-dev-missed-dose-e` |

각 Case는 `partition=DEV`, `task_type=RETRIEVAL`, `expected_retrieval_invocation=true`, 서로 다른
`medication_family`, `question_template`, `source_segment`, `transform_origin`을 사용한다. query와 Evidence
statement는 `SYNTHETIC_*` 토큰만 사용한다.

- [ ] **Step 4: Replay expected rank를 고정**

```json
{
  "RET-L": {
    "rag-ret-dev-001": ["ev-ret-dev-med-a", "ev-ret-dev-noise-01", "ev-ret-dev-noise-02", "ev-ret-dev-noise-03", "ev-ret-dev-noise-04"],
    "rag-ret-dev-002": ["ev-ret-dev-noise-01", "ev-ret-dev-precaution-b", "ev-ret-dev-noise-02", "ev-ret-dev-noise-03", "ev-ret-dev-noise-04"],
    "rag-ret-dev-003": ["ev-ret-dev-noise-01", "ev-ret-dev-noise-02", "ev-ret-dev-noise-03", "ev-ret-dev-lifestyle-c", "ev-ret-dev-noise-04"],
    "rag-ret-dev-004": ["ev-ret-dev-noise-01", "ev-ret-dev-noise-02", "ev-ret-dev-noise-03", "ev-ret-dev-noise-04", "ev-ret-dev-noise-05"],
    "rag-ret-dev-005": ["ev-ret-dev-noise-01", "ev-ret-dev-noise-02", "ev-ret-dev-missed-dose-e", "ev-ret-dev-noise-03", "ev-ret-dev-noise-04"]
  },
  "RET-HR": {
    "rag-ret-dev-001": ["ev-ret-dev-med-a", "ev-ret-dev-med-a-detail", "ev-ret-dev-noise-01", "ev-ret-dev-noise-02", "ev-ret-dev-noise-03"],
    "rag-ret-dev-002": ["ev-ret-dev-precaution-b", "ev-ret-dev-noise-01", "ev-ret-dev-noise-02", "ev-ret-dev-noise-03", "ev-ret-dev-noise-04"],
    "rag-ret-dev-003": ["ev-ret-dev-lifestyle-c", "ev-ret-dev-noise-01", "ev-ret-dev-noise-02", "ev-ret-dev-noise-03", "ev-ret-dev-noise-04"],
    "rag-ret-dev-004": ["ev-ret-dev-noise-01", "ev-ret-dev-storage-d", "ev-ret-dev-noise-02", "ev-ret-dev-noise-03", "ev-ret-dev-noise-04"],
    "rag-ret-dev-005": ["ev-ret-dev-missed-dose-e", "ev-ret-dev-noise-01", "ev-ret-dev-noise-02", "ev-ret-dev-noise-03", "ev-ret-dev-noise-04"]
  }
}
```

두 실제 replay 파일은 공통 envelope `schema_id=rag-eval.retrieval-replay`, `schema_version=1.0.0`,
`dataset_code=rag-retrieval-dev`, `dataset_version=1.0.0`, `variant_id`, `top_k=5`, `case_results`를 사용한다.

- [ ] **Step 5: Comparison Policy Scope 고정**

다섯 Scope는 `partition=DEV`, `slice_id=ALL`, `unit_of_analysis=CASE`,
`independence_unit=question_template`, `cluster_dimension=question_template`, `minimum_case_count=5`,
`minimum_independent_group_count=5`, `ci_method_id=PERCENTILE_CLUSTER_BOOTSTRAP`,
`ci_method_version=1.0.0`, `ci_parameters={"iterations":10000,"level":"0.95","sidedness":"TWO_SIDED"}`,
`seed=158`, `decision_basis=DIAGNOSTIC_ONLY`, `required=false`, `threshold=0`을 사용한다.

Metric ID는 UTF-16 정렬 순서로 `MRR`, `NDCG_AT_5`, `NO_HIT_RATE`, `PRECISION_AT_5`, `RECALL_AT_5`이며
모두 version `1.0.0`이다. Policy의 technical loader 승인 actor는 기존 non-release 관례의
`SYSTEM/rag-eval-draft-validator/SYSTEM_VALIDATOR`로 두고, Release 승인으로 해석하지 않는다.

- [ ] **Step 6: Canonical hash를 계산하고 전체 graph load 테스트 통과**

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_retrieval_dev_fixture.py ai_worker/tests/evaluation/test_loaders.py -q`

Expected: PASS; manifest, resource set, mapping, policy, suite, evaluation-policy self hash가 모두 일치한다.

- [ ] **Step 7: Commit**

```bash
git add evals/retrieval evals/profiles/rag-retrieval-dev-v1.profile.json evals/policies/rag-retrieval-dev-v1.comparison-policy.json evals/policies/rag-retrieval-dev-v1.evaluation-policy.json evals/suites/rag-retrieval-dev-v1.suite.json ai_worker/tests/evaluation/test_retrieval_dev_fixture.py
git commit -m "test(eval): add synthetic retrieval DEV baseline fixtures"
```

---

### Task 2: Replay Manifest·Provenance Config와 Adapter 구현

**Files:**
- Create: `ai_worker/tasks/evaluation/retrieval_replay.py`
- Create: `ai_worker/tests/evaluation/test_retrieval_replay.py`
- Create: `evals/configs/rag-retrieval-dev-ret-l-v1.execution.json`
- Create: `evals/configs/rag-retrieval-dev-ret-hr-v1.execution.json`
- Modify: `ai_worker/tasks/evaluation/config.py`
- Modify: `ai_worker/tasks/evaluation/runner.py`
- Modify: `ai_worker/tasks/evaluation/errors.py`
- Test: `ai_worker/tests/evaluation/test_config.py`
- Test: `ai_worker/tests/evaluation/test_runner.py`

**Interfaces:**
- Consumes: `EvaluationAdapter.execute(AdapterRequest) -> CaseResult`, `ResolvedDevExecution`.
- Produces: `RetrievalReplayManifest`, `ReplayRetrievalAdapter`, `build_adapter_registry(resolved)`.

- [ ] **Step 1: Replay validation과 exact ranked output 실패 테스트 작성**

```python
def test_replay_adapter_returns_ranked_ids_for_exact_case_binding() -> None:
    replay = load_retrieval_replay(REPLAY_PATH, repository_root=REPOSITORY_ROOT)
    adapter = ReplayRetrievalAdapter(replay)
    result = adapter.execute(_adapter_request("rag-ret-dev-002"))

    assert result.execution_status.value == "COMPLETED"
    assert result.decision_status.value == "N/A"
    assert result.retrieved_evidence_ids[:2] == (
        "ev-ret-dev-noise-01",
        "ev-ret-dev-precaution-b",
    )
    assert result.selected_evidence_ids == result.retrieved_evidence_ids[:5]
```

중복 rank, 알 수 없는 Case, Dataset/variant mismatch, 5 초과 result, replay path traversal 테스트도 같은 파일에
추가한다.

- [ ] **Step 2: 테스트를 실행해 module 부재 실패 확인**

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_retrieval_replay.py -q`

Expected: FAIL with import error for `retrieval_replay`.

- [ ] **Step 3: strict replay models와 loader 구현**

```python
class ReplayCaseResult(StrictContractModel):
    case_id: StableId
    ranked_evidence_ids: Annotated[tuple[StableId, ...], BeforeValidator(_tuple_from_wire)]


class RetrievalReplayManifest(StrictContractModel):
    schema_id: Literal["rag-eval.retrieval-replay"]
    schema_version: Literal["1.0.0"]
    dataset_code: StableId
    dataset_version: SemanticVersion
    variant_id: StableId
    top_k: Literal[5]
    case_results: Annotated[tuple[ReplayCaseResult, ...], BeforeValidator(_tuple_from_wire)]
    replay_sha256: Sha256Hex
```

`load_retrieval_replay()`는 repository root 아래 NFC 상대경로만 허용하고, duplicate JSON key·self hash·Case
정렬·중복 rank를 검증한다.

- [ ] **Step 4: Retrieval provenance를 config에 결속**

```python
class RetrievalReplayModelConfig(StrictContractModel):
    adapter_id: Literal["retrieval-replay.v1"]
    provider_invocation: Literal[False]
    source_snapshot_ref: ImmutableReference
    knowledge_index_ref: ImmutableReference
    embedding_model_ref: ImmutableReference
    parser_ref: ImmutableReference
    filter_snapshot_hash: Sha256Hex


class DevVariant(StrictContractModel):
    variant_id: StableId
    variant_version: SemanticVersion
    kind: Literal["RETRIEVAL", "ANSWER"]
    model_config_payload: dict[str, JsonValue] = Field(alias="model_config")
    prompt_version: StableId
    parameters: dict[str, JsonValue]
    replay_artifact_path: ResourcePath | None = None
```

`model_config.adapter_id == "retrieval-replay.v1"`인 Retrieval variant는 `model_config_payload` 전체를
`RetrievalReplayModelConfig`로 검증하고 `replay_artifact_path`를 필수로 요구한다. Baseline과 Candidate의
`model_config`은 byte-for-byte 같고 `parameters.retrieval_strategy`, `parameters.reranker_enabled`, replay path만
다르게 둔다. 기존 `validation-only.v1` config 세 개는 그대로 load되어야 한다. replay bytes/hash를
`ResolvedDevExecution.referenced_file_hashes`와 resolved config hash에 포함한다.

- [ ] **Step 5: AdapterRequest에 variant binding을 추가하고 replay adapter 구현**

```python
@dataclass(frozen=True, slots=True)
class AdapterRequest:
    run_id: str
    case: EvaluationCaseContract
    task_type: TaskType
    input_sha256: str
    variant_id: str
    variant_manifest_hash: str
```

Adapter는 Case query나 Evidence 원문을 결과에 복제하지 않고 ranked stable evidence ID만 반환한다. Replay에
Case가 없으면 `EvaluationValidationError(EVAL_RETRIEVAL_REPLAY_INVALID)`을 발생시켜 해당 Case를
`INVALID/null`로 만든다.

- [ ] **Step 6: Targeted tests 통과**

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_retrieval_replay.py ai_worker/tests/evaluation/test_config.py ai_worker/tests/evaluation/test_runner.py -q`

Expected: PASS, 기존 validation-only Adapter 테스트도 유지.

- [ ] **Step 7: Commit**

```bash
git add ai_worker/tasks/evaluation/config.py ai_worker/tasks/evaluation/runner.py ai_worker/tasks/evaluation/errors.py ai_worker/tasks/evaluation/retrieval_replay.py ai_worker/tests/evaluation/test_config.py ai_worker/tests/evaluation/test_runner.py ai_worker/tests/evaluation/test_retrieval_replay.py evals/configs/rag-retrieval-dev-ret-l-v1.execution.json evals/configs/rag-retrieval-dev-ret-hr-v1.execution.json
git commit -m "feat(eval): add versioned retrieval replay adapter"
```

---

### Task 3: Retrieval Metric과 결정적 95% CI 구현

**Files:**
- Create: `ai_worker/tasks/evaluation/retrieval_metrics.py`
- Create: `ai_worker/tests/evaluation/test_retrieval_metrics.py`

**Interfaces:**
- Consumes: `ValidatedDataset.cases`, `tuple[CaseResult, ...]`, `ComparisonPolicy.scopes`.
- Produces: `build_retrieval_metrics(dataset, case_results) -> MetricResults`.

- [ ] **Step 1: 수작업 정답 Metric 실패 테스트 작성**

```python
def test_five_case_fixture_matches_hand_calculated_metrics() -> None:
    metrics = build_retrieval_metrics(DATASET, ret_l_case_results())
    values = {metric.metric_id: metric.metric_value for metric in metrics.metrics}

    assert values == {
        "MRR": "0.416667",
        "NDCG_AT_5": "0.434951",
        "NO_HIT_RATE": "0.200000",
        "PRECISION_AT_5": "0.160000",
        "RECALL_AT_5": "0.800000",
    }
```

테스트의 기대값은 Task 1 rank matrix를 독립적인 수작업 계산표로 다시 검산한다. 구현 중 계산 결과에 맞춰
기대값을 변경하지 않는다.

- [ ] **Step 2: 분모 0·표본 부족·중복 결과·실행 실패 테스트 작성**

```python
@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        ("zero_required_evidence", "ZERO_DENOMINATOR"),
        ("four_independent_groups", "MINIMUM_INDEPENDENT_GROUP_COUNT_NOT_MET"),
        ("four_cases", "MINIMUM_CASE_COUNT_NOT_MET"),
    ],
)
def test_required_scope_never_passes_invalid_sample(fixture: str, reason: str) -> None:
    metric = metric_for_fixture(fixture)
    assert metric.execution_status.value == "COMPLETED"
    assert metric.decision_status.value == "INCONCLUSIVE"
    assert metric.reason_code == reason
```

- [ ] **Step 3: 테스트를 실행해 module 부재 실패 확인**

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_retrieval_metrics.py -q`

Expected: FAIL with import error for `retrieval_metrics`.

- [ ] **Step 4: Case observation과 point estimator 구현**

```python
@dataclass(frozen=True, slots=True)
class RetrievalObservation:
    case_id: str
    slice_ids: tuple[str, ...]
    independent_group_id: str
    required_ids: tuple[str, ...]
    relevant_ids: tuple[str, ...]
    ranked_ids: tuple[str, ...]


def recall_at_k(observation: RetrievalObservation, k: int = 5) -> Decimal:
    required = set(observation.required_ids)
    if not required:
        raise ZeroDivisionError("required evidence denominator is zero")
    hits = required.intersection(observation.ranked_ids[:k])
    return _quantize_six(Decimal(len(hits)) / Decimal(len(required)))


def precision_at_k(observation: RetrievalObservation, k: int = 5) -> Decimal:
    relevant = set(observation.relevant_ids)
    hits = relevant.intersection(observation.ranked_ids[:k])
    return _quantize_six(Decimal(len(hits)) / Decimal(k))


def reciprocal_rank(observation: RetrievalObservation, k: int = 5) -> Decimal:
    relevant = set(observation.relevant_ids)
    rank = next((index for index, item in enumerate(observation.ranked_ids[:k], 1) if item in relevant), None)
    return Decimal("0.000000") if rank is None else _quantize_six(Decimal(1) / Decimal(rank))


def ndcg_at_k(observation: RetrievalObservation, k: int = 5) -> Decimal:
    relevant = set(observation.relevant_ids)
    dcg = sum(
        (Decimal(1) / Decimal(str(math.log2(rank + 1))) for rank, item in enumerate(observation.ranked_ids[:k], 1) if item in relevant),
        Decimal(0),
    )
    ideal_count = min(len(relevant), k)
    idcg = sum(
        (Decimal(1) / Decimal(str(math.log2(rank + 1))) for rank in range(1, ideal_count + 1)),
        Decimal(0),
    )
    return Decimal("0.000000") if idcg == 0 else _quantize_six(dcg / idcg)


def no_hit(observation: RetrievalObservation, k: int = 5) -> Decimal:
    relevant = set(observation.relevant_ids)
    return Decimal("0.000000") if relevant.intersection(observation.ranked_ids[:k]) else Decimal("1.000000")
```

각 함수는 입력 tuple을 변경하지 않고, 결과를 `_quantize_six()`로 canonicalize한다. Unknown Evidence ID는
non-relevant로 취급하되 duplicate ranked ID는 `INVALID` 입력으로 거부한다.

- [ ] **Step 5: deterministic group bootstrap 구현**

```python
def percentile_group_bootstrap_ci(
    group_scores: Mapping[str, tuple[Decimal, ...]],
    *,
    seed: int,
    iterations: int,
    level: Decimal,
) -> tuple[Decimal, Decimal]:
    rng = random.Random(seed)
    group_ids = tuple(sorted(group_scores, key=lambda value: value.encode("utf-16-be")))
    estimates = []
    for _ in range(iterations):
        sampled = [group_ids[rng.randrange(len(group_ids))] for _ in group_ids]
        scores = [score for group_id in sampled for score in group_scores[group_id]]
        estimates.append(sum(scores, Decimal(0)) / Decimal(len(scores)))
    estimates.sort()
    return _percentile_bounds(estimates, level)
```

Percentile index rule은 `floor((n-1)*alpha)`와 `ceil((n-1)*(1-alpha))`로 version `1.0.0`에 고정한다.

- [ ] **Step 6: MetricResult 상태와 count 의미 구현**

`build_retrieval_metrics()`는 Policy scope 순서가 아니라 `MetricResult.sort_key`로 정렬한다. 정상 diagnostic
scope는 `COMPLETED/N/A`, 분모 0·최소 표본 미달은 `COMPLETED/INCONCLUSIVE`, Case 실행 실패가 섞이면
`ERROR/null`로 만든다. MRR/nDCG numerator는 non-zero Case 수, denominator는 Case 수다.

- [ ] **Step 7: 반복 호출 결정성과 전체 Metric 테스트 통과**

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_retrieval_metrics.py -q`

Expected: PASS; 동일 seed로 직렬화 bytes와 CI bound가 동일.

- [ ] **Step 8: Commit**

```bash
git add ai_worker/tasks/evaluation/retrieval_metrics.py ai_worker/tests/evaluation/test_retrieval_metrics.py
git commit -m "feat(eval): calculate deterministic retrieval metrics"
```

---

### Task 4: Metric·Failure Artifact를 Run Builder에 통합

**Files:**
- Modify: `ai_worker/tasks/evaluation/runner.py`
- Modify: `ai_worker/tasks/evaluation/manifest.py`
- Modify: `ai_worker/tests/evaluation/test_runner.py`
- Modify: `ai_worker/tests/evaluation/test_result_manifest.py`

**Interfaces:**
- Consumes: `build_retrieval_metrics()`, `RunOutcome.case_results`.
- Produces: 계산된 `metrics.json`, 재현 가능한 `failures.jsonl`, updated semantic hash.

- [ ] **Step 1: Retrieval run이 placeholder Metric을 만들지 않는 실패 테스트 작성**

```python
def test_retrieval_artifact_draft_contains_completed_metric_counts_and_ci() -> None:
    draft = build_artifact_draft(retrieval_run_material("RET-L"))
    recall = next(metric for metric in draft.metrics.metrics if metric.metric_id == "RECALL_AT_5")

    assert recall.execution_status.value == "COMPLETED"
    assert (recall.numerator, recall.denominator) == (4, 5)
    assert recall.metric_value == "0.800000"
    assert recall.ci_lower is not None
    assert recall.ci_upper is not None
```

- [ ] **Step 2: Failed Case record의 비민감 내용 실패 테스트 작성**

```python
def test_retrieval_miss_creates_stable_non_sensitive_failure_record() -> None:
    draft = build_artifact_draft(retrieval_run_material("RET-L"))
    failure = next(item for item in draft.failures if item.case_id == "rag-ret-dev-004")

    assert failure.failure_stage == "RETRIEVAL_MISS"
    assert failure.failure_code == "REQUIRED_EVIDENCE_NOT_IN_TOP_5"
    assert failure.expected_summary.value == "EXPECTED_REQUIRED_EVIDENCE"
    assert failure.actual_summary.value == "ACTUAL_REQUIRED_EVIDENCE_MISSING"
```

- [ ] **Step 3: 테스트를 실행해 placeholder 상태 실패 확인**

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_runner.py ai_worker/tests/evaluation/test_result_manifest.py -q`

Expected: FAIL because `_build_metrics()` returns `NOT_IMPLEMENTED` and failures are empty.

- [ ] **Step 4: RunOutcome failure 생성과 Metric Builder 연결**

`execute_dev_cases()`는 completed Retrieval Case 중 required evidence가 Top-5에 없는 Case마다 stable
`FailureRecord`를 만든다. `build_artifact_draft()`는 `KNOWLEDGE_RETRIEVAL`일 때
`build_retrieval_metrics(dataset, outcome.case_results)`를 호출하고, 다른 experiment는 기존 placeholder Metric
동작을 유지한다. `semantic_content_hash()`는 `FailureRecord.created_at`을 semantic projection에서 제외하여
Run 시각만 다른 반복 실행의 hash를 안정화한다.

- [ ] **Step 5: Semantic hash가 Run ID·시각에는 독립이고 Metric 변화에는 민감한지 검증**

```python
def test_semantic_hash_changes_when_ranked_retrieval_result_changes() -> None:
    before = semantic_content_hash(ret_l_artifacts().files)
    after = semantic_content_hash(ret_l_artifacts(rank_override={"rag-ret-dev-004": ["ev-ret-dev-storage-d"]}).files)
    assert before != after
```

- [ ] **Step 6: Targeted tests 통과**

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_runner.py ai_worker/tests/evaluation/test_result_manifest.py ai_worker/tests/evaluation/test_retrieval_metrics.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ai_worker/tasks/evaluation/runner.py ai_worker/tasks/evaluation/manifest.py ai_worker/tests/evaluation/test_runner.py ai_worker/tests/evaluation/test_result_manifest.py
git commit -m "feat(eval): emit retrieval metric and failure artifacts"
```

---

### Task 5: Baseline Bundle 검증과 Candidate Comparison 구현

**Files:**
- Create: `ai_worker/tasks/evaluation/comparison.py`
- Create: `ai_worker/tests/evaluation/test_comparison.py`
- Modify: `ai_worker/tasks/evaluation/manifest.py`
- Modify: `ai_worker/tests/evaluation/test_result_manifest.py`

**Interfaces:**
- Consumes: immutable baseline Bundle path, candidate `ArtifactDraft`, `semantic_content_hash()`.
- Produces: `load_published_run_bundle()`, `build_retrieval_comparison() -> ComparisonResult`.

- [ ] **Step 1: 정상 delta와 hash binding 실패 테스트 작성**

```python
def test_comparison_binds_semantic_hashes_and_reports_metric_delta() -> None:
    baseline = published_ret_l_bundle()
    candidate = finalized_ret_hr_artifacts()
    comparison = build_retrieval_comparison(baseline, candidate)
    recall = next(item for item in comparison.scope_comparisons if item.metric_id == "RECALL_AT_5")

    assert comparison.baseline_run_hash == semantic_content_hash(baseline.files)
    assert comparison.candidate_run_hash == semantic_content_hash(candidate.files)
    assert recall.baseline_value == "0.800000"
    assert recall.candidate_value == "1.000000"
    assert recall.absolute_delta == "0.200000"
    assert recall.comparison_decision.value == "INCONCLUSIVE"
    assert comparison.decision_status.value == "INCONCLUSIVE"
```

- [ ] **Step 2: 통제 변수 mismatch·tamper 실패 테스트 작성**

Dataset Manifest, resource set, Evidence Mapping, Policy, partition manifest 또는 공통 `model_config_hash` 중
하나라도 다르면 해당 `ControlledVariableCheck.matched=false`,
`execution_status=INVALID`, `decision_status=null`이어야 한다. `run.json`, `metrics.json`, content manifest hash가
맞지 않는 baseline directory는 comparison을 생성하지 않고 `EVAL_BASELINE_ARTIFACT_INVALID`로 실패한다.

- [ ] **Step 3: 테스트를 실행해 module 부재 실패 확인**

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_comparison.py -q`

Expected: FAIL with import error for `comparison`.

- [ ] **Step 4: 안전한 Bundle loader 구현**

```python
@dataclass(frozen=True, slots=True)
class LoadedRunBundle:
    root: Path
    run: RagEvaluationRun
    metrics: MetricResults
    content_manifest: ContentManifest
    files: Mapping[str, bytes]
    semantic_hash: str


```

공개 함수 signature는
`load_published_run_bundle(result_root: Path, run_id: str) -> LoadedRunBundle`로 고정한다.

Loader는 canonical UUID directory 한 단계만 열고 symlink를 따르지 않으며, content manifest의 모든 payload
hash/size와 runtime schema를 검증한다. baseline에 `comparison.json`이 있어도 semantic hash 입력에서는 제외한다.

- [ ] **Step 5: 통제 변수와 Scope delta builder 구현**

통제 key는 `CASE_SET`, `DATASET`, `GOLD`, `METRIC_POLICY`, `SOURCE_INDEX_FILTER_MODEL`로 정렬한다.
`SOURCE_INDEX_FILTER_MODEL`은 두 Run의 `model_config_hash`를 비교하며, 실제 Source/Index/embedding/parser/filter
값은 해당 Run의 Git commit에 있는 versioned config에서 해석한다.
Baseline/Candidate Metric natural key 집합이 다르면 comparison 자체를 `INVALID/null`로 만든다. 승인 threshold가
없으므로 모든 정상 Scope의 `comparison_decision=INCONCLUSIVE`, `paired_test_method=null`, `p_value=null`로 두고
값과 delta만 기록한다.

- [ ] **Step 6: ArtifactDraft가 optional ComparisonResult를 소유하도록 확장**

```python
@dataclass(frozen=True, slots=True)
class ArtifactDraft:
    report_data: ReportData
    run_payload: Mapping[str, JsonValue]
    cases: Sequence[CaseResult]
    metrics: MetricResults
    suite_results: SuiteResults
    comparison: ComparisonResult | None
    failures: Sequence[FailureRecord]
```

`machine_artifact_files()`는 comparison이 있을 때만 `comparison.json`을 추가한다. content manifest는 기존
allowlist를 사용해 이를 hash한다.

- [ ] **Step 7: Targeted tests 통과**

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_comparison.py ai_worker/tests/evaluation/test_result_manifest.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add ai_worker/tasks/evaluation/comparison.py ai_worker/tasks/evaluation/manifest.py ai_worker/tests/evaluation/test_comparison.py ai_worker/tests/evaluation/test_result_manifest.py
git commit -m "feat(eval): compare immutable retrieval run bundles"
```

---

### Task 6: Optional Comparison Bundle의 원자 발행과 Contract 검증

**Files:**
- Modify: `ai_worker/tasks/evaluation/publisher.py`
- Modify: `ai_worker/tasks/evaluation/manifest.py`
- Modify: `ai_worker/tests/evaluation/test_publisher.py`
- Modify: `ai_worker/tests/evaluation/test_result_manifest.py`

**Interfaces:**
- Consumes: baseline 7-file 또는 candidate 8-file `PublishedArtifacts.files`.
- Produces: 둘 중 정확한 파일 집합만 허용하는 `publish_run_directory()`.

- [ ] **Step 1: 8-file candidate 발행 실패 테스트 작성**

```python
def test_publisher_atomically_publishes_candidate_bundle_with_comparison(tmp_path: Path) -> None:
    files = candidate_bundle_files()
    published = publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=files)
    assert set(path.name for path in published.iterdir()) == set(files)
    assert (published / "comparison.json").read_bytes() == files["comparison.json"]
```

comparison 없이 7개인 baseline, comparison만 있고 core file이 빠진 입력, 알 수 없는 파일, 기존 Run 충돌,
short write/rename 실패 cleanup 테스트도 유지·추가한다.

- [ ] **Step 2: 테스트를 실행해 strict 7-file allowlist 실패 확인**

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_publisher.py -q`

Expected: FAIL with `EVAL_MANIFEST_INVALID` for the candidate bundle.

- [ ] **Step 3: required와 optional file set으로 publisher 검증 변경**

```python
_REQUIRED_BUNDLE_FILENAMES = frozenset({
    "run.json", "cases.jsonl", "metrics.json", "suite-results.json",
    "failures.jsonl", "result-content-manifest.json", "report.md",
})
_OPTIONAL_BUNDLE_FILENAMES = frozenset({"comparison.json"})


def _valid_bundle_files(names: set[str]) -> bool:
    return (
        _REQUIRED_BUNDLE_FILENAMES.issubset(names)
        and names <= _REQUIRED_BUNDLE_FILENAMES | _OPTIONAL_BUNDLE_FILENAMES
    )
```

staging 검증도 hard-coded equality 대신 실행 시 받은 exact expected set과 위 allowlist를 모두 확인한다.

- [ ] **Step 4: Comparison runtime/schema 검증 추가**

`validate_published_artifact_contracts()`는 `comparison.json`이 있으면 checked-in
`artifacts/rag-eval.comparison.schema.json` bytes와 `ComparisonResult.model_validate_json()`을 검증한다.

- [ ] **Step 5: Publisher·Manifest tests 통과**

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_publisher.py ai_worker/tests/evaluation/test_result_manifest.py ai_worker/tests/evaluation/test_schema_exports.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ai_worker/tasks/evaluation/publisher.py ai_worker/tasks/evaluation/manifest.py ai_worker/tests/evaluation/test_publisher.py ai_worker/tests/evaluation/test_result_manifest.py
git commit -m "feat(eval): publish optional comparison artifacts atomically"
```

---

### Task 7: CLI와 Markdown Report에 Baseline 비교 연결

**Files:**
- Modify: `ai_worker/tasks/evaluation/cli.py`
- Modify: `ai_worker/tasks/evaluation/reporter.py`
- Modify: `ai_worker/tests/evaluation/test_cli.py`
- Modify: `ai_worker/tests/evaluation/test_reporter.py`

**Interfaces:**
- Consumes: replay registry factory, optional `--baseline-run-id`, `build_retrieval_comparison()`.
- Produces: baseline/candidate 실행 명령, read-only `verify-result`, Retrieval 결과 Projection.

- [ ] **Step 1: CLI candidate comparison 실패 테스트 작성**

```python
def test_run_dev_with_baseline_writes_comparison_into_candidate_bundle(tmp_path: Path) -> None:
    assert run_dev(RET_L_CONFIG, BASELINE_RUN_ID, result_root=tmp_path) == 0
    assert run_dev(
        RET_HR_CONFIG,
        CANDIDATE_RUN_ID,
        result_root=tmp_path,
        baseline_run_id=BASELINE_RUN_ID,
    ) == 0

    comparison = json.loads((tmp_path / CANDIDATE_RUN_ID / "comparison.json").read_bytes())
    assert comparison["baseline_run_id"] == BASELINE_RUN_ID
    assert comparison["candidate_run_id"] == CANDIDATE_RUN_ID
    assert comparison["decision_status"] == "INCONCLUSIVE"
```

`--baseline-run-id`가 `KNOWLEDGE_RETRIEVAL` 외 실험에 쓰이거나 baseline과 candidate variant가 같으면
`EVAL_STATE_COMBINATION_INVALID`로 거부하는 테스트도 추가한다.

- [ ] **Step 2: Report Retrieval/Comparison Projection 실패 테스트 작성**

```python
def test_candidate_report_projects_metric_counts_ci_and_dev_boundary() -> None:
    report = candidate_report().decode("utf-8")
    assert "# RAG Evaluation DEV Retrieval Report" in report
    assert "SYNTHETIC_REPLAY_DEV" in report
    assert "Recall@5" in report
    assert "4/5" in report
    assert "95% CI" in report
    assert "RET-L" in report and "RET-HR" in report
    assert "HOLDOUT Baseline Freeze: `NOT_PERFORMED`" in report
    assert "BLOCKED_BY_RAG_07A_07B_OR_08" in report
```

- [ ] **Step 3: 테스트를 실행해 CLI option/report 부재 실패 확인**

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_cli.py ai_worker/tests/evaluation/test_reporter.py -q`

Expected: FAIL because `--baseline-run-id` and Retrieval tables are absent.

- [ ] **Step 4: CLI parser와 registry factory 구현**

```python
run_dev.add_argument("--baseline-run-id")
```

`_run_dev()`는 config를 먼저 resolve한 뒤 built-in registry를 만든다. Baseline 없이 실행하면 7-file Run을
발행한다. Baseline ID가 있으면 baseline Bundle을 load하고 candidate base artifacts의 semantic hash를 만든 뒤
comparison을 추가하여 한 번만 candidate directory를 발행한다. 비교 준비 실패 시 candidate directory를 남기지
않는다.

- [ ] **Step 5: Report에 기계 Artifact와 동일한 값 투영**

Retrieval 표는 Metric ID, sample Case/Group, numerator/denominator, value, CI, execution/decision/reason을 표시한다.
Comparison 표는 baseline/candidate Run ID·semantic hash, metric 값, absolute delta, comparison decision을 표시한다.
Case query, Evidence statement 또는 원문 trace는 표시하지 않는다.

- [ ] **Step 6: CLI/Reporter tests 통과**

`verify-result --run-id <uuid>` 테스트도 추가한다. 명령은 `load_published_run_bundle()`로 모든 Schema와 content
hash를 검증한 뒤 stdout에 semantic hash 한 줄만 출력한다. 존재하지 않는 Run, symlink, tampered payload는
`EVAL_BASELINE_ARTIFACT_INVALID`와 non-zero exit로 종료하며 payload 내용을 출력하지 않는다.

Run: `/Users/junghyunwoo/PycharmProjects/AH_05_04/.venv/bin/pytest ai_worker/tests/evaluation/test_cli.py ai_worker/tests/evaluation/test_reporter.py ai_worker/tests/evaluation/test_comparison.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ai_worker/tasks/evaluation/cli.py ai_worker/tasks/evaluation/reporter.py ai_worker/tests/evaluation/test_cli.py ai_worker/tests/evaluation/test_reporter.py
git commit -m "feat(eval): expose DEV retrieval baseline comparison CLI"
```

---

### Task 8: 운영 문서, 실제 DEV 결과 확보와 전체 검증

**Files:**
- Modify: `evals/README.md`
- Runtime output, not committed: `evals/results/<baseline-run-id>/`
- Runtime output, not committed: `evals/results/<candidate-run-id>/`

**Interfaces:**
- Consumes: 완성된 clean Git commit, 두 versioned execution config.
- Produces: 프로젝트 구조 내 두 Run Bundle, 재현성 evidence, PR용 비민감 요약.

- [ ] **Step 1: README 명령과 해석을 먼저 테스트로 고정**

`ai_worker/tests/evaluation/test_retrieval_dev_fixture.py`에 README가 다음 문자열을 포함하는 테스트를 추가한다.

```python
assert "rag-retrieval-dev-ret-l-v1.execution.json" in readme
assert "rag-retrieval-dev-ret-hr-v1.execution.json" in readme
assert "--baseline-run-id" in readme
assert "evals/results/<run-id>/" in readme
assert "HOLDOUT Baseline Freeze가 아닙니다" in readme
```

- [ ] **Step 2: README 테스트 실패 확인 후 실행 절차 작성**

Baseline 명령:

```bash
uv run python -m ai_worker.tasks.evaluation run-dev \
  --config evals/configs/rag-retrieval-dev-ret-l-v1.execution.json \
  --run-id 15800000-0000-4000-8000-000000000001 \
  --executed-by ceohwj
```

Candidate 명령:

```bash
uv run python -m ai_worker.tasks.evaluation run-dev \
  --config evals/configs/rag-retrieval-dev-ret-hr-v1.execution.json \
  --run-id 15800000-0000-4000-8000-000000000002 \
  --executed-by ceohwj \
  --baseline-run-id 15800000-0000-4000-8000-000000000001
```

README에는 결과가 Git 비추적 로컬/CI DEV Artifact이며 새 실행은 새 Run ID를 사용해야 한다고 명시한다.

- [ ] **Step 3: 문서와 구현을 commit하여 clean repository 조건 확보**

```bash
git add evals/README.md ai_worker/tests/evaluation/test_retrieval_dev_fixture.py docs/designs/ceohwj/issue-158-rag-retrieval-metric-baseline-design.md docs/designs/ceohwj/issue-158-rag-retrieval-metric-baseline-implementation-plan.md
git commit -m "docs(eval): document retrieval DEV baseline workflow"
```

- [ ] **Step 4: 전체 Evaluation test·lint·type check 실행**

```bash
uv run pytest ai_worker/tests/evaluation -q
uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
uv run mypy ai_worker/tasks/evaluation
git diff --check origin/develop...HEAD
```

Expected: 모든 명령 exit 0. 기존 기준선 `634 passed, 1 skipped`보다 새 테스트 수만 증가하고 기존 테스트는
회귀하지 않는다.

- [ ] **Step 5: 첫 Baseline/Candidate 결과를 프로젝트 폴더에 생성**

Step 2의 두 명령을 clean repository에서 순서대로 실행한다. 생성 후 다음 파일 집합을 확인한다.

```bash
find evals/results/15800000-0000-4000-8000-000000000001 -maxdepth 1 -type f -print | sort
find evals/results/15800000-0000-4000-8000-000000000002 -maxdepth 1 -type f -print | sort
git status --short --ignored evals/results
```

Expected: baseline 7개, candidate 8개 파일. 두 디렉터리는 `!! evals/results/`로 ignored 상태다.

- [ ] **Step 6: 두 번째 독립 실행으로 semantic hash 결정성 검증**

새 Run ID `15800000-0000-4000-8000-000000000003`,
`15800000-0000-4000-8000-000000000004`를 사용해 같은 두 config를 다시 실행한다. 첫 쌍과 두 번째 쌍의
baseline semantic hash, candidate semantic hash, `metrics.json`에서 `run_id`를 제외한 canonical projection이
각각 동일한지 검증한다.

- [ ] **Step 7: 결과 내용과 Privacy 경계 검증**

```bash
uv run python -m ai_worker.tasks.evaluation verify-result \
  --run-id 15800000-0000-4000-8000-000000000001
uv run python -m ai_worker.tasks.evaluation verify-result \
  --run-id 15800000-0000-4000-8000-000000000002
```

두 명령은 모든 runtime Schema와 content hash를 검증하고 stdout에 semantic hash 한 줄만 출력해야 한다.

- [ ] **Step 8: 최종 Artifact와 PR 요약 검토**

확인 항목:

- Baseline Recall@5 `4/5`, Candidate Recall@5 `5/5`가 JSON과 report에서 일치한다.
- 모든 Metric에 Case 수 5, 독립 Group 수 5, CI method/version/seed가 기록된다.
- Candidate `comparison.json`이 두 Run ID와 semantic hash를 정확히 참조한다.
- 비교 decision은 승인 threshold 부재로 `INCONCLUSIVE`다.
- `report.md`에 `SYNTHETIC_REPLAY_DEV`, `NOT_PERFORMED`, `BLOCKED_BY_RAG_07A_07B_OR_08`가 표시된다.
- 실패 Case는 ID와 안정 reason code만 포함하고 query/Evidence 원문을 포함하지 않는다.
- Git diff에 `evals/results/` 파일이 없다.

- [ ] **Step 9: 최종 evidence commit**

실행 결과는 commit하지 않는다. 검증 과정에서 문서 명령이나 비민감 예상값을 바로잡은 경우에만 해당 파일을
stage하고 다음 commit을 만든다.

```bash
git add evals/README.md docs/designs/ceohwj/issue-158-rag-retrieval-metric-baseline-design.md docs/designs/ceohwj/issue-158-rag-retrieval-metric-baseline-implementation-plan.md
git commit -m "docs(eval): record retrieval DEV baseline verification"
```

변경이 없으면 빈 commit을 만들지 않는다.

---

## Completion Evidence

구현 완료 보고에는 다음을 정확히 남긴다.

- Baseline/Candidate Run ID와 semantic content hash
- Dataset Manifest, Evidence Mapping, Comparison/Evaluation Policy hash
- 각 Metric의 numerator/denominator/value/95% CI와 sample Case/Group count
- Candidate comparison delta와 `INCONCLUSIVE` 사유
- 실패 Case ID와 안정 비민감 reason code
- `pytest`, Ruff, mypy, `git diff --check` 실행 결과
- `evals/results/`가 Git ignored이며 로컬 프로젝트 구조에 존재한다는 확인
- HOLDOUT Baseline Freeze, Release PASS, Production 승인이 수행되지 않았다는 명시
