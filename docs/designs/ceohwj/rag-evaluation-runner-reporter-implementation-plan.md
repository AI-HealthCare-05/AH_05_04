# RAG Evaluation DEV Runner·Reporter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Issue #157의 첫 단계로 합성 DEV Dataset만 실행하는 결정적 Runner와 JSON 정본 기반 Reporter를 구현하고 HOLDOUT·SAFETY_REGRESSION 접근을 사전에 차단한다.

**Architecture:** `config.py`가 versioned execution request와 Variant를 검증하고 resolved configuration hash를 만든다. `runner.py`는 Case 선택·Adapter 1회 호출·상태 집계를 담당하고, `manifest.py`와 `reporter.py`가 schema-valid 기계 Artifact와 비정본 Markdown을 만든 뒤 `publisher.py`가 디렉터리를 no-clobber 방식으로 원자 발행한다. 기존 `validate` CLI와 Artifact Schema는 변경하지 않는다.

**Tech Stack:** Python 3.13, Pydantic v2, `argparse`, 표준 라이브러리 `ctypes/hashlib/json/os/pathlib/subprocess`, pytest, Ruff, mypy.

**Spec:** `docs/designs/ceohwj/rag-evaluation-runner-reporter-design.md`

## Global Constraints

- 이번 계획은 합성 DEV Runner·Reporter만 구현한다. HOLDOUT·SAFETY_REGRESSION을 load·execute·observe하지 않는다.
- `evals/schemas/`, `docs/contracts/`, #214 동결 Dataset·Case·Gold·Evidence·Rubric·provenance·hash graph를 수정하지 않는다.
- 실제 Provider·Model·Judge·DB·환자 API를 호출하거나 결과를 Application DB에 저장하지 않는다.
- `comparison.json`, `gate.json`, `baseline-freeze-receipt.json`은 생성하지 않는다.
- 실행 상태와 판정 상태를 분리하고 미완료 상태는 항상 `decision_status=null`이다.
- 자동 재시도는 금지한다. 한 Run에서 선택된 `(case_id, task_type)`마다 Adapter를 정확히 한 번 호출한다.
- 결과는 `evals/results/<run_id>/` 아래에만 생성하고 기존 directory·lock을 덮어쓰거나 자동 삭제하지 않는다.
- 기계 Artifact는 canonical JSON/JSONL이고 Markdown은 판정 입력이 아닌 projection이다.
- 실제 환자정보, 질문·답변 원문, Provider payload/trace, credential, 내부 예외 문자열을 Artifact와 stderr에 기록하지 않는다.
- 외부 평가 정본은 `evaluation-plan.md@1.35`, SHA-256 `526f83dedc05a777c0963bfa10bb8bd8ebd940ab3eb12523f4c8fa15447e542f`이다.
- 외부 Authority Manifest hash는 `f2c98884c841d3fccdbec552f14aad1fd471730eae6d80c472c1b332ed95a570`이다.
- Artifact Schema Set 1.0의 필드·enum·nullability를 그대로 사용한다.
- 이 계획의 완료는 Issue #157 Close, HOLDOUT Baseline Freeze 또는 Release 승인이 아니다.

## File Map

| 파일 | 책임 |
| --- | --- |
| `ai_worker/tasks/evaluation/config.py` | strict JSON execution request·Variant model, 안전한 참조 경로, repository state, resolved config hash, DEV preflight |
| `ai_worker/tasks/evaluation/runner.py` | Experiment별 Case 선택, Adapter protocol/registry, 1회 실행, 중립 미완료 결과, 상태 집계 |
| `ai_worker/tasks/evaluation/manifest.py` | provenance-bound input hash, JSONL 직렬화, typed ReportData, Run/Metric/Suite Artifact, content/semantic hash |
| `ai_worker/tasks/evaluation/reporter.py` | typed ReportData와 machine entry에서 비민감 Markdown 생성 |
| `ai_worker/tasks/evaluation/publisher.py` | private staging directory, fsync, no-clobber lock, atomic rename |
| `ai_worker/tasks/evaluation/cli.py` | 기존 `validate` 유지, `run-dev` argument·dependency 연결, 안전한 exit/stderr |
| `ai_worker/tasks/evaluation/errors.py` | Runner 전용 안정 오류 code |
| `evals/configs/dev-foundation-*.execution.json` | 세 Experiment Type의 versioned DEV 실행 요청 |
| `ai_worker/tests/evaluation/test_config.py` | execution request·Variant·path·repository state·preflight 검증 |
| `ai_worker/tests/evaluation/test_runner.py` | Case 선택·순서·Adapter 1회 호출·오류 격리·상태 우선순위 |
| `ai_worker/tests/evaluation/test_result_manifest.py` | input/config/content/semantic hash와 schema-valid Artifact 검증 |
| `ai_worker/tests/evaluation/test_reporter.py` | JSON projection·민감정보 제거·Markdown 비정본성 |
| `ai_worker/tests/evaluation/test_publisher.py` | mode·fsync·rename·symlink·no-clobber·실패 정리 |
| `ai_worker/tests/evaluation/test_cli.py` | 세 DEV 명령, HOLDOUT 사전 차단, 기존 validate 회귀 |
| `evals/README.md` | DEV 실행 명령과 비-Release 제한 설명 |

---

### Task 1: Strict execution request와 resolved configuration hash

**Files:**

- Create: `ai_worker/tasks/evaluation/config.py`
- Create: `ai_worker/tests/evaluation/test_config.py`
- Create: `evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json`
- Create: `evals/configs/dev-foundation-answer-grounding-safety-v1.execution.json`
- Create: `evals/configs/dev-foundation-end-to-end-rag-v1.execution.json`
- Modify: `ai_worker/tasks/evaluation/loaders.py`
- Modify: `ai_worker/tasks/evaluation/errors.py`

**Interfaces:**

- Consumes: `canonical_json_bytes()`, `sha256_hex()`, `normalize_resource_path()`, `JsonValue`, `StrictContractModel`, `StableId`, `SemanticVersion`, `SafeInteger`, `ExperimentType`, `Partition`.
- Produces: `DevVariant`, `DevExecutionRequest`, `RepositoryState`, `ResolvedDevExecution`, `load_dev_execution_request(path, *, repository_root, repository_state_provider) -> ResolvedDevExecution`.
- Produces for later tasks: `ResolvedDevExecution.dataset_manifest_path`, `.request`, `.referenced_file_hashes`, `.resolved_evaluation_config_hash`, `.retrieval_variant_manifest_hash`, `.answer_variant_manifest_hash`, `.model_config_hash`, `.prompt_version`, `.runner_commit_sha`.

- [ ] **Step 1: 기존 JSON loader helper의 회귀 테스트를 추가한다**

`test_config.py`에 public helper가 duplicate key와 root 이탈을 기존 오류 code로 거부하는 테스트를 작성한다.

```python
def test_parse_json_object_bytes_rejects_duplicate_keys() -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        parse_json_object_bytes(b'{"seed":1,"seed":2}')
    assert caught.value.code is EvaluationErrorCode.JSON_DUPLICATE_KEY


def test_safe_path_under_root_rejects_parent_escape(tmp_path: Path) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        safe_path_under_root(tmp_path, Path("../outside.json"))
    assert caught.value.code is EvaluationErrorCode.RESOURCE_PATH_INVALID
```

- [ ] **Step 2: helper 회귀 테스트가 실패하는지 확인한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_config.py -k 'parse_json_object_bytes or safe_path_under_root' -q
```

Expected: 두 public helper import가 없어 collection 단계에서 FAIL.

- [ ] **Step 3: `loaders.py`의 private helper를 public 이름으로 전환한다**

`_parse_json_object`를 `parse_json_object_bytes`, `_safe_path`를 `safe_path_under_root`로 이름 변경하고 파일 내부 호출을 모두 갱신한다. 동작과 오류 mapping은 바꾸지 않는다.

```python
# _SnapshotReader.read_path 내부 호출
value = parse_json_object_bytes(raw_bytes)

# _SnapshotReader.path/read_path 내부 호출
safe_path = safe_path_under_root(self.root, candidate_path)
```

함수 본문은 기존 `_parse_json_object`, `_safe_path` 구현을 이름만 바꿔 그대로 이동한다. 새 예외 분기나
fallback을 추가하지 않는다.

- [ ] **Step 4: helper 테스트와 기존 Loader 테스트를 통과시킨다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_config.py ai_worker/tests/evaluation/test_loaders.py -q
```

Expected: PASS.

- [ ] **Step 5: execution request의 실패 테스트를 작성한다**

다음 경우를 각각 독립 테스트로 만든다.

```python
def test_load_dev_execution_request_binds_actual_variant_and_runner_hashes(tmp_path: Path) -> None:
    resolved = load_dev_execution_request(
        write_request(tmp_path, experiment_type="KNOWLEDGE_RETRIEVAL"),
        repository_root=tmp_path,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    assert resolved.runner_commit_sha == "a" * 40
    assert resolved.retrieval_variant_manifest_hash == canonical_sha256(
        resolved.request.retrieval_variant.model_dump(mode="json", by_alias=True)
    )
    assert resolved.answer_variant_manifest_hash is None


def test_resolved_hash_changes_when_seed_or_referenced_file_changes(tmp_path: Path) -> None:
    first = load_fixture_request(tmp_path, seed=157)
    second = load_fixture_request(tmp_path, seed=158)
    assert first.resolved_evaluation_config_hash != second.resolved_evaluation_config_hash


def test_production_request_rejects_dirty_repository(tmp_path: Path) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        load_dev_execution_request(
            write_request(tmp_path),
            repository_root=tmp_path,
            repository_state_provider=lambda _root: RepositoryState("a" * 40, False),
        )
    assert caught.value.code is EvaluationErrorCode.REPOSITORY_STATE_INVALID
```

추가로 unknown field, absolute path, symlink, missing reference, `evaluated_partitions != [DEV]`, retry policy 변경,
`max_attempts != 1`, Experiment에 맞지 않는 null Variant, 두 Variant의 model/prompt 불일치를 검증한다.

- [ ] **Step 6: execution request 테스트가 실패하는지 확인한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_config.py -k 'execution_request or resolved_hash or dirty_repository' -q
```

Expected: execution request model과 loader가 없어 FAIL.

- [ ] **Step 7: execution request model과 loader를 최소 구현한다**

```python
class DevVariant(StrictContractModel):
    variant_id: StableId
    variant_version: SemanticVersion
    kind: Literal["RETRIEVAL", "ANSWER"]
    model_config_payload: dict[str, JsonValue] = Field(alias="model_config")
    prompt_version: StableId
    parameters: dict[str, JsonValue]


class DevExecutionRequest(StrictContractModel):
    config_id: StableId
    config_version: SemanticVersion
    experiment_id: StableId
    experiment_type: ExperimentTypeValue
    variant_id: StableId
    evaluated_partitions: Annotated[
        tuple[Literal["DEV"]],
        BeforeValidator(lambda value: tuple(value) if isinstance(value, list) else value),
    ]
    environment: Literal["LOCAL", "CI"]
    dataset_manifest_path: ResourcePath
    profile_path: ResourcePath
    comparison_policy_path: ResourcePath
    evaluation_policy_path: ResourcePath
    suite_path: ResourcePath
    upstream_contract_manifest_hash: Sha256Hex
    retrieval_variant: DevVariant | None
    answer_variant: DevVariant | None
    seed: SafeInteger
    retry_policy: Literal["NO_AUTOMATIC_RETRY"]
    max_attempts: Literal[1]


@dataclass(frozen=True, slots=True)
class RepositoryState:
    commit_sha: str
    clean: bool


@dataclass(frozen=True, slots=True)
class ResolvedDevExecution:
    request: DevExecutionRequest
    dataset_manifest_path: Path
    referenced_file_hashes: Sequence[tuple[str, str]]
    resolved_evaluation_config_hash: str
    retrieval_variant_manifest_hash: str | None
    answer_variant_manifest_hash: str | None
    model_config_hash: str
    prompt_version: str
    runner_commit_sha: str
```

`git_repository_state()`는 `git rev-parse HEAD`와 `git status --porcelain`을
`subprocess.run(command, cwd=repository_root, check=True, capture_output=True, text=True)`로 실행한다. stderr 원문은 외부로 전달하지 않고 실패를
`EVAL_REPOSITORY_STATE_INVALID`로 정규화한다. resolved hash preimage에는 request 전체, 다섯 참조 파일의 실제
SHA-256, 두 Variant hash, Runner commit을 넣는다.

`errors.py`에는 `REPOSITORY_STATE_INVALID = "EVAL_REPOSITORY_STATE_INVALID"`를 추가한다. request의
`upstream_contract_manifest_hash`는 상단 Global Constraints의 Authority Manifest hash와 정확히 같아야 한다.

```python
active_variants = tuple(
    variant
    for variant in (request.retrieval_variant, request.answer_variant)
    if variant is not None
)
model_payloads = {
    canonical_json_bytes(variant.model_config_payload)
    for variant in active_variants
}
prompt_versions = {variant.prompt_version for variant in active_variants}
if len(model_payloads) != 1 or len(prompt_versions) != 1:
    raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)
model_config_hash = sha256_hex(next(iter(model_payloads)))
prompt_version = next(iter(prompt_versions))
```

`KNOWLEDGE_RETRIEVAL`은 Retrieval만, `ANSWER_GROUNDING_SAFETY`와 `END_TO_END_RAG`는 Retrieval+Answer를
요구한다. Variant `kind`가 위치와 다르거나 active Variant가 이 matrix와 다르면 같은 오류로 거부한다.

- [ ] **Step 8: 세 DEV execution request를 canonical JSON으로 추가한다**

세 파일은 Experiment Type과 Variant nullability만 다르게 하고 다음 공통 값을 사용한다.

```json
{
  "config_id": "rag-dev-foundation-end-to-end-rag",
  "config_version": "1.0.0",
  "experiment_id": "rag-dev-foundation-infrastructure",
  "experiment_type": "END_TO_END_RAG",
  "variant_id": "dev-synthetic-adapter-v1",
  "evaluated_partitions": ["DEV"],
  "environment": "LOCAL",
  "dataset_manifest_path": "evals/retrieval/manifests/dev-foundation-v1.dataset.json",
  "profile_path": "evals/profiles/dev-foundation-v1.profile.json",
  "comparison_policy_path": "evals/policies/dev-foundation-v1.comparison-policy.json",
  "evaluation_policy_path": "evals/policies/dev-foundation-v1.evaluation-policy.json",
  "suite_path": "evals/suites/dev-foundation-v1.suite.json",
  "upstream_contract_manifest_hash": "f2c98884c841d3fccdbec552f14aad1fd471730eae6d80c472c1b332ed95a570",
  "retrieval_variant": {
    "variant_id": "dev-synthetic-retrieval-v1",
    "variant_version": "1.0.0",
    "kind": "RETRIEVAL",
    "model_config": {"adapter_id": "validation-only.v1", "provider_invocation": false},
    "prompt_version": "synthetic-no-provider-v1",
    "parameters": {"seed": 157}
  },
  "answer_variant": {
    "variant_id": "dev-synthetic-answer-v1",
    "variant_version": "1.0.0",
    "kind": "ANSWER",
    "model_config": {"adapter_id": "validation-only.v1", "provider_invocation": false},
    "prompt_version": "synthetic-no-provider-v1",
    "parameters": {"seed": 157}
  },
  "seed": 157,
  "retry_policy": "NO_AUTOMATIC_RETRY",
  "max_attempts": 1
}
```

Retrieval 파일만 `answer_variant=null`로 둔다. Answer Grounding·Safety와 End-to-End 파일은 grounding provenance를
보존하기 위해 두 Variant를 모두 가지며 동일한 `model_config`와 `prompt_version`을 사용한다.
실제 파일 bytes는 위 객체를 `canonical_json_bytes()`로 직렬화한 한 줄 JSON이며 trailing newline을 붙이지 않는다.

- [ ] **Step 9: Task 1 테스트를 실행하고 커밋한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_config.py ai_worker/tests/evaluation/test_loaders.py -q
```

Expected: PASS.

```bash
git add ai_worker/tasks/evaluation/config.py ai_worker/tasks/evaluation/loaders.py ai_worker/tasks/evaluation/errors.py ai_worker/tests/evaluation/test_config.py evals/configs
git commit -m "feat(evals): add versioned dev execution config"
```

---

### Task 2: DEV-only Manifest preflight와 loaded graph 결속

**Files:**

- Modify: `ai_worker/tasks/evaluation/config.py`
- Modify: `ai_worker/tests/evaluation/test_config.py`

**Interfaces:**

- Consumes: `ResolvedDevExecution`, `ValidatedDataset`, `parse_json_object_bytes()`, `safe_path_under_root()`.
- Produces: `preflight_dev_manifest(resolved) -> None`, `validate_loaded_bindings(resolved, dataset) -> None`.

- [ ] **Step 1: HOLDOUT manifest를 preflight에서 막는 실패 테스트를 작성한다**

```python
def test_preflight_rejects_holdout_manifest(resolved_holdout_request: ResolvedDevExecution) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        preflight_dev_manifest(resolved_holdout_request)
    assert caught.value.code is EvaluationErrorCode.PARTITION_INVALID
```

`AUTHORING`, `SAFETY_REGRESSION`, 혼합 partition, non-synthetic classification, DEV=0, Case resource 중 하나가
DEV가 아닌 경우도 같은 방식으로 검증한다.

- [ ] **Step 2: generic DEV fixture 허용 테스트를 작성한다**

Dataset ID, review status, DEV Case 수를 기존 foundation과 다르게 만든 합성 manifest를 사용한다.

```python
def test_preflight_is_not_bound_to_foundation_id_status_or_case_count(tmp_path: Path) -> None:
    resolved = resolved_request_for(
        tmp_path,
        dataset_code="another-synthetic-dev",
        status="REVIEWED",
        dev_count=1,
    )
    preflight_dev_manifest(resolved)
```

- [ ] **Step 3: preflight 테스트가 실패하는지 확인한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_config.py -k 'preflight or holdout or foundation_id' -q
```

Expected: preflight 함수가 없어 FAIL.

- [ ] **Step 4: manifest-only preflight를 구현한다**

```python
def preflight_dev_manifest(resolved: ResolvedDevExecution) -> None:
    payload = parse_json_object_bytes(resolved.dataset_manifest_path.read_bytes())
    counts = payload.get("partition_counts")
    resources = payload.get("case_resources")
    valid = (
        resolved.request.evaluated_partitions == ("DEV",)
        and type(counts) is dict
        and type(counts.get("DEV")) is int
        and counts["DEV"] > 0
        and all(counts.get(name) == 0 for name in ("AUTHORING", "HOLDOUT", "SAFETY_REGRESSION"))
        and type(resources) is list
        and len(resources) == counts["DEV"]
        and all(type(item) is dict and item.get("partition") == "DEV" for item in resources)
        and payload.get("data_classification") == "SYNTHETIC"
    )
    if not valid:
        raise EvaluationValidationError(EvaluationErrorCode.PARTITION_INVALID)
```

파일 read 오류와 malformed JSON은 기존 안정 code로 mapping하고, preflight에서는 child resource를 열지 않는다.

- [ ] **Step 5: loaded graph와 request 참조를 교차 검증한다**

```python
def validate_loaded_bindings(resolved: ResolvedDevExecution, dataset: ValidatedDataset) -> None:
    requested_hashes = dict(resolved.referenced_file_hashes)
    role_bindings = {
        "dataset_manifest_path": dataset.dataset_manifest_resource,
        "profile_path": dataset.configuration_resources.profile,
        "comparison_policy_path": dataset.configuration_resources.comparison_policy,
        "evaluation_policy_path": dataset.configuration_resources.evaluation_policy,
        "suite_path": dataset.configuration_resources.suite,
    }
    for field, binding in role_bindings.items():
        requested_path = getattr(resolved.request, field)
        if requested_path != f"evals/{binding.relative_path}":
            raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
        if requested_hashes.get(requested_path) != binding.sha256:
            raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
    if dataset.profile.required_partitions != (Partition.DEV,):
        raise EvaluationValidationError(EvaluationErrorCode.PARTITION_INVALID)
    if dataset.profile.runtime_eligible:
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)
```

다음 조건을 같은 함수에서 모두 확인하고 하나라도 다르면 안정 오류 code로 실패시킨다.

```python
if resolved.request.experiment_type not in dataset.profile.required_experiment_types:
    raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)
if dataset.suite.input_selector.partitions != (Partition.DEV,):
    raise EvaluationValidationError(EvaluationErrorCode.PARTITION_INVALID)
if (
    dataset.suite.input_selector.dataset_code != dataset.manifest.dataset_code
    or dataset.suite.input_selector.dataset_version != dataset.manifest.dataset_version
):
    raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
loaded_hashes = dict(dataset.resource_hashes)
for path, expected_hash in resolved.referenced_file_hashes:
    try:
        evals_relative = Path(path).relative_to("evals").as_posix()
    except ValueError as error:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from error
    if loaded_hashes.get(evals_relative) != expected_hash:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
```

모든 request reference는 파일 read 전에 `evals/` namespace인지 검사한다. Dataset Manifest도 Loader가 실제 읽은
snapshot의 path/hash binding을 별도로 보존하여 나머지 네 역할과 동일하게 검증하고, `Path.relative_to("evals")`
실패는 순수 `ValueError`로 유출하지 않고 `EVAL_RESOURCE_PATH_INVALID`로 정규화한다.

- [ ] **Step 6: Task 2 테스트를 실행하고 커밋한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_config.py ai_worker/tests/evaluation/test_loaders.py -q
```

Expected: PASS.

```bash
git add ai_worker/tasks/evaluation/config.py ai_worker/tests/evaluation/test_config.py
git commit -m "feat(evals): enforce dev-only execution boundary"
```

---

### Task 3: Runner, Adapter 경계, 1회 호출과 상태 집계

**Files:**

- Create: `ai_worker/tasks/evaluation/runner.py`
- Create: `ai_worker/tasks/evaluation/manifest.py`
- Create: `ai_worker/tests/evaluation/test_runner.py`
- Create: `ai_worker/tests/evaluation/test_result_manifest.py`
- Modify: `ai_worker/tasks/evaluation/errors.py`

**Interfaces:**

- Consumes: `ValidatedDataset`, `ResolvedDevExecution`, `CASE_RESULT_ADAPTER`, `CaseResult`, `ExecutionStatus`, `DecisionStatus`, `TaskType`.
- Produces: `CaseInputBinding`, `case_input_sha256(binding)`, `AdapterRequest`, `EvaluationAdapter`, `AdapterRegistry`, `RunOutcome`, `execute_dev_cases(dataset, resolved, *, run_id, adapter_registry) -> RunOutcome`.
- `RunOutcome` fields: `case_results`, `failure_records`, `execution_status`, `decision_status`, `blocking_execution_statuses`, `selected_case_ids`, `task_types`.

- [ ] **Step 1: Experiment별 선택과 UTF-16BE 순서 테스트를 작성한다**

```python
@pytest.mark.parametrize(
    ("experiment_type", "expected_tasks"),
    [
        ("KNOWLEDGE_RETRIEVAL", ["RETRIEVAL"]),
        ("ANSWER_GROUNDING_SAFETY", ["ANSWER_GROUNDING", "ANSWER_QUALITY", "SAFETY"]),
        ("END_TO_END_RAG", ["END_TO_END_RAG"]),
    ],
)
def test_selects_only_tasks_for_experiment_in_utf16_order(
    loaded_dev_dataset: ValidatedDataset,
    experiment_type: str,
    expected_tasks: list[str],
) -> None:
    outcome = execute_with_success_adapter(loaded_dev_dataset, experiment_type)
    assert [item.task_type.value for item in outcome.case_results] == expected_tasks
```

`test_result_manifest.py`에는 Adapter 호출 전에 필요한 provenance-bound hash를 고정한다.

```python
def case_binding(*, resolved_hash: str) -> CaseInputBinding:
    return CaseInputBinding(
        case_id="case-001",
        task_type="RETRIEVAL",
        partition="DEV",
        case_resource_sha256="1" * 64,
        dataset_manifest_sha256="2" * 64,
        evidence_mapping_manifest_sha256="3" * 64,
        critical_claim_rubric_hash="4" * 64,
        resolved_evaluation_config_hash=resolved_hash,
    )


def test_case_input_hash_binds_case_task_dataset_evidence_rubric_and_config() -> None:
    first = case_input_sha256(case_binding(resolved_hash="a" * 64))
    changed = case_input_sha256(case_binding(resolved_hash="b" * 64))
    assert first != changed
    assert first == canonical_sha256(
        {
            "case_id": "case-001",
            "task_type": "RETRIEVAL",
            "partition": "DEV",
            "case_resource_sha256": "1" * 64,
            "dataset_manifest_sha256": "2" * 64,
            "evidence_mapping_manifest_sha256": "3" * 64,
            "critical_claim_rubric_hash": "4" * 64,
            "resolved_evaluation_config_hash": "a" * 64,
        }
    )
```

- [ ] **Step 2: no-retry와 오류 격리 테스트를 작성한다**

```python
def test_adapter_exception_is_recorded_once_and_next_case_runs(loaded_dev_dataset: ValidatedDataset) -> None:
    adapter = CountingAdapter(fail_case_id="rag-dev-answer-quality-001")
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        resolved_answer_request,
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(adapter),
    )
    assert adapter.calls == [
        "rag-dev-answer-grounding-001",
        "rag-dev-answer-quality-001",
        "rag-dev-safety-001",
    ]
    failed = next(item for item in outcome.case_results if item.case_id == "rag-dev-answer-quality-001")
    assert (failed.execution_status.value, failed.decision_status) == ("ERROR", None)
    assert failed.failure_codes == ("EVAL_INTERNAL_ERROR",)
    assert outcome.blocking_execution_statuses == (ExecutionStatus.ERROR,)
    assert outcome.failure_records == ()
```

예외 문자열에 `patient@example.com` sentinel을 넣고 outcome·stderr 어디에도 남지 않는지 확인한다.

- [ ] **Step 3: 미등록 Adapter와 empty-output 표현 테스트를 작성한다**

```python
def test_missing_adapter_produces_not_implemented_without_fake_answer(loaded_dev_dataset: ValidatedDataset) -> None:
    outcome = execute_dev_cases(
        loaded_dev_dataset,
        resolved_answer_request,
        run_id=RUN_ID,
        adapter_registry=StaticRegistry(None),
    )
    assert all(item.execution_status.value == "NOT_IMPLEMENTED" for item in outcome.case_results)
    assert all(item.decision_status is None for item in outcome.case_results)
    answer = next(item for item in outcome.case_results if item.task_type.value == "ANSWER_QUALITY")
    assert answer.answer_sha256 == sha256_hex(b"")
    assert answer.actual_claim_ids == ()
    assert outcome.decision_status is None
```

- [ ] **Step 4: 부모 우선순위 테스트를 작성한다**

`INVALID > ERROR > NOT_IMPLEMENTED > NOT_EVALUATED`의 모든 조합과 모든 child가 `COMPLETED/N/A`인 비필수 DEV
Suite의 `COMPLETED/N/A`를 검증한다.

```python
def test_aggregate_keeps_all_blockers_in_normative_order() -> None:
    status, decision, blockers = aggregate_statuses(
        [ExecutionStatus.NOT_EVALUATED, ExecutionStatus.ERROR, ExecutionStatus.NOT_IMPLEMENTED]
    )
    assert status is ExecutionStatus.ERROR
    assert decision is None
    assert blockers == (
        ExecutionStatus.ERROR,
        ExecutionStatus.NOT_IMPLEMENTED,
        ExecutionStatus.NOT_EVALUATED,
    )
```

- [ ] **Step 5: Runner 테스트가 실패하는지 확인한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_runner.py ai_worker/tests/evaluation/test_result_manifest.py -q
```

Expected: Runner/manifest module과 interface가 없어 FAIL.

- [ ] **Step 6: Runner를 최소 구현한다**

```python
TASK_TYPES_BY_EXPERIMENT = {
    ExperimentType.KNOWLEDGE_RETRIEVAL: (TaskType.RETRIEVAL,),
    ExperimentType.ANSWER_GROUNDING_SAFETY: (
        TaskType.ANSWER_GROUNDING,
        TaskType.ANSWER_QUALITY,
        TaskType.SAFETY,
    ),
    ExperimentType.END_TO_END_RAG: (TaskType.END_TO_END_RAG,),
}


@dataclass(frozen=True, slots=True)
class CaseInputBinding:
    case_id: str
    task_type: str
    partition: str
    case_resource_sha256: str
    dataset_manifest_sha256: str
    evidence_mapping_manifest_sha256: str
    critical_claim_rubric_hash: str
    resolved_evaluation_config_hash: str


def case_input_sha256(binding: CaseInputBinding) -> str:
    return canonical_sha256(asdict(binding))


@dataclass(frozen=True, slots=True)
class AdapterRequest:
    run_id: str
    case: EvaluationCaseContract
    task_type: TaskType
    input_sha256: str


class EvaluationAdapter(Protocol):
    def execute(self, request: AdapterRequest) -> CaseResult:
        raise NotImplementedError


class AdapterRegistry(Protocol):
    def resolve(self, adapter_id: str) -> EvaluationAdapter | None:
        raise NotImplementedError


class EmptyAdapterRegistry:
    def resolve(self, adapter_id: str) -> None:
        del adapter_id
        return None


EMPTY_ADAPTER_REGISTRY = EmptyAdapterRegistry()


def execute_dev_cases(
    dataset: ValidatedDataset,
    resolved: ResolvedDevExecution,
    *,
    run_id: str,
    adapter_registry: AdapterRegistry,
) -> RunOutcome:
    selected = select_cases(dataset.cases, resolved.request.experiment_type)
    adapter = adapter_registry.resolve(dataset.suite.adapter_id)
    case_results = execute_once_in_order(selected, resolved, run_id=run_id, adapter=adapter)
    return aggregate_run_outcome(case_results)
```

`select_cases(cases, experiment_type)`는 `TASK_TYPES_BY_EXPERIMENT`에 속하는 Case만 고르고
`(case_id, task_type.value)` UTF-16BE 순서로 정렬한다. `execute_once_in_order(selected, resolved, *, run_id, adapter)`는 각 항목을 한 번 순회하며 Adapter를 한
번만 호출하고, `aggregate_run_outcome(case_results)`는 blocker set을 고정 우선순위로 정렬한다. 이 세 helper는
재귀 호출, retry loop, backoff를 포함하지 않는다.

Runner는 Adapter 반환값을 `CASE_RESULT_ADAPTER.validate_python()`으로 다시 검증하고 Run ID, Case ID, Task Type,
Dataset, partition, input hash가 요청과 다르면 해당 Case를 `INVALID/null`로 바꾼다. technical exception은
`cases.jsonl`에만 기록하고 기존 `FailureSummary` enum으로 오표현하지 않는다.

- [ ] **Step 7: Task 3 테스트를 실행하고 커밋한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_runner.py ai_worker/tests/evaluation/test_result_manifest.py -q
```

Expected: PASS.

```bash
git add ai_worker/tasks/evaluation/runner.py ai_worker/tasks/evaluation/manifest.py ai_worker/tasks/evaluation/errors.py ai_worker/tests/evaluation/test_runner.py ai_worker/tests/evaluation/test_result_manifest.py
git commit -m "feat(evals): execute dev cases deterministically"
```

---

### Task 4: Machine Artifact, content manifest와 semantic hash

**Files:**

- Modify: `ai_worker/tasks/evaluation/manifest.py`
- Modify: `ai_worker/tests/evaluation/test_result_manifest.py`
- Modify: `ai_worker/tasks/evaluation/runner.py`

**Interfaces:**

- Consumes: `RunOutcome`, `ResolvedDevExecution`, `ValidatedDataset`, Artifact Schema model 전체, `canonical_json_bytes()`.
- Produces: `RunMaterial`, `ReportData`, `ArtifactDraft`, `PublishedArtifacts`, `build_artifact_draft(material)`, `build_content_manifest(run_id, files)`, `finalize_artifacts(draft, report_bytes, completed_at)`, `semantic_content_hash(files)`.

- [ ] **Step 1: provenance-bound Case input hash 회귀 테스트를 먼저 실행한다**

```bash
uv run pytest ai_worker/tests/evaluation/test_result_manifest.py -k 'case_input_hash' -q
```

Expected: Task 3에서 추가한 hash 회귀 테스트 PASS.

- [ ] **Step 2: canonical JSONL 테스트를 작성한다**

```python
def test_jsonl_is_canonical_sorted_and_lf_terminated(case_results: Sequence[CaseResult]) -> None:
    payload = serialize_jsonl(case_results)
    assert payload.endswith(b"\n")
    assert b"\r" not in payload
    assert all(line == canonical_json_bytes(json.loads(line)) for line in payload.splitlines())
```

빈 record collection은 정확히 `b""`이고 header·주석·빈 줄을 만들지 않는지도 검증한다.

- [ ] **Step 3: Schema-valid machine Artifact 테스트를 작성한다**

`finalize_artifacts()` 결과를 Pydantic model과 `evals/schemas/1.0.0/artifacts/*.schema.json` 양쪽으로 검증한다.

```python
def test_machine_artifacts_validate_against_models_and_exported_schemas(dev_run_material: RunMaterial) -> None:
    draft = build_artifact_draft(dev_run_material)
    artifacts = finalize_artifacts(draft, b"safe report\n", completed_at=FIXED_TIME)
    RagEvaluationRun.model_validate_json(artifacts.files["run.json"])
    MetricResults.model_validate_json(artifacts.files["metrics.json"])
    SuiteResults.model_validate_json(artifacts.files["suite-results.json"])
    validate_jsonl(CASE_RESULT_ADAPTER, artifacts.files["cases.jsonl"])
    validate_jsonl(FailureRecord, artifacts.files["failures.jsonl"])
```

Metric은 DEV comparison scope를 `NOT_IMPLEMENTED/null`과 모든 계산값 `null`로 기록한다. Suite case의
`artifact_ref.hash`는 해당 CaseResult canonical bytes SHA-256이며 `artifact_hash`는 재귀 self-hash를 만들지 않도록
`null`로 유지한다.

- [ ] **Step 4: content manifest 순환 방지 테스트를 작성한다**

```python
def test_content_manifest_excludes_run_and_self_but_includes_report() -> None:
    files = machine_files() | {"report.md": b"safe report\n"}
    manifest, payload = build_content_manifest(RUN_ID, files)
    paths = [item.relative_path for item in manifest.artifacts]
    assert paths == sorted(
        ["cases.jsonl", "failures.jsonl", "metrics.json", "report.md", "suite-results.json"],
        key=lambda value: value.encode("utf-16-be"),
    )
    assert "run.json" not in paths
    assert "result-content-manifest.json" not in paths
    assert manifest.manifest_sha256 == canonical_sha256(
        manifest.model_dump(mode="json"),
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    assert ContentManifest.model_validate_json(payload) == manifest
```

- [ ] **Step 5: completed/incomplete Run 연결 테스트를 작성한다**

```python
def test_completed_run_links_content_manifest_but_incomplete_run_does_not() -> None:
    completed = finalize_artifacts(
        build_artifact_draft(completed_material()), b"safe\n", completed_at=FIXED_TIME
    ).run
    incomplete = finalize_artifacts(
        build_artifact_draft(error_material()), b"safe\n", completed_at=FIXED_TIME
    ).run
    assert completed.result_content_manifest_hash is not None
    assert completed.completed_at == FIXED_TIME
    assert incomplete.result_content_manifest_hash is None
    assert incomplete.completed_at is None
    assert incomplete.decision_status is None
```

- [ ] **Step 6: semantic hash 테스트를 작성한다**

```python
def test_semantic_hash_ignores_only_run_identity_and_clock() -> None:
    first = published_artifacts(run_id=RUN_ID_A, clock=TIME_A)
    second = published_artifacts(run_id=RUN_ID_B, clock=TIME_B)
    assert semantic_content_hash(first.files) == semantic_content_hash(second.files)
    changed = published_artifacts(run_id=RUN_ID_B, clock=TIME_B, seed=158)
    assert semantic_content_hash(first.files) != semantic_content_hash(changed.files)
```

projection은 `run.json`, `cases.jsonl`, `metrics.json`, `suite-results.json`, `failures.jsonl`만 사용하고 `run_id`,
`started_at`, `completed_at`, `result_content_manifest_hash`만 schema-aware allowlist로 제외한다. `report.md`, content
manifest, comparison, gate는 입력에 포함하지 않는다.

- [ ] **Step 7: Artifact 테스트가 실패하는지 확인한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_result_manifest.py -q
```

Expected: manifest module과 Artifact builder가 없어 FAIL.

- [ ] **Step 8: Artifact builder를 구현한다**

```python
@dataclass(frozen=True, slots=True)
class RunMaterial:
    outcome: RunOutcome
    dataset: ValidatedDataset
    resolved: ResolvedDevExecution
    run_id: str
    executed_by: ActorRef
    started_at: str


@dataclass(frozen=True, slots=True)
class ReportData:
    run_id: str
    experiment_id: str
    experiment_type: ExperimentType
    variant_id: str
    dataset_code: str
    dataset_version: str
    evaluation_profile_ref: ImmutableReference
    comparison_policy_ref: ImmutableReference
    evaluation_policy_ref: ImmutableReference
    suite_ref: ImmutableReference
    execution_status: ExecutionStatus
    decision_status: DecisionStatus | None
    blocking_execution_statuses: Sequence[ExecutionStatus]
    task_case_counts: Mapping[str, int]
    failure_codes: Sequence[str]


@dataclass(frozen=True, slots=True)
class ArtifactDraft:
    report_data: ReportData
    run_payload: Mapping[str, JsonValue]
    cases: Sequence[CaseResult]
    metrics: MetricResults
    suite_results: SuiteResults
    failures: Sequence[FailureRecord]


@dataclass(frozen=True, slots=True)
class PublishedArtifacts:
    run: RagEvaluationRun
    content_manifest: ContentManifest
    files: Mapping[str, bytes]

    def semantic_files(self) -> dict[str, bytes]:
        return {
            "run.json": canonical_json_bytes(self.run.model_dump(mode="json")),
            "cases.jsonl": self.files["cases.jsonl"],
            "metrics.json": self.files["metrics.json"],
            "suite-results.json": self.files["suite-results.json"],
            "failures.jsonl": self.files["failures.jsonl"],
        }


def finalize_artifacts(
    draft: ArtifactDraft,
    report_bytes: bytes,
    *,
    completed_at: str,
) -> PublishedArtifacts:
    files = {
        "cases.jsonl": serialize_jsonl(draft.cases),
        "metrics.json": canonical_json_bytes(draft.metrics.model_dump(mode="json")),
        "suite-results.json": canonical_json_bytes(draft.suite_results.model_dump(mode="json")),
        "failures.jsonl": serialize_jsonl(draft.failures),
        "report.md": report_bytes,
    }
    content_manifest, content_bytes = build_content_manifest(draft.report_data.run_id, files)
    run_payload = dict(draft.run_payload)
    if draft.report_data.execution_status is ExecutionStatus.COMPLETED:
        run_payload.update(
            completed_at=completed_at,
            result_content_manifest_hash=content_manifest.manifest_sha256,
        )
    else:
        run_payload.update(completed_at=None, decision_status=None, result_content_manifest_hash=None)
    run = RagEvaluationRun.model_validate(run_payload)
    files["run.json"] = canonical_json_bytes(run.model_dump(mode="json"))
    files["result-content-manifest.json"] = content_bytes
    return PublishedArtifacts(run=run, content_manifest=content_manifest, files=files)
```

Run의 `artifact_schema_set_ref`는 Evaluation Policy member의 immutable reference를 사용하고
`partition_manifest_hash`는 Loader와 같은 `{partition, resources[{case_id,path,sha256}]}` canonical preimage로 계산한다.

`build_artifact_draft()`는 Run 필드를 다음 출처에서만 채운다.

| Run 필드 | 출처 |
| --- | --- |
| experiment/variant/config/model/prompt/environment | `ResolvedDevExecution` |
| Profile/Comparison/Evaluation/Schema Set refs | 검증된 `ValidatedDataset` graph |
| dataset/version/manifest/resource/evidence/rubric/fixture/receipt | 검증된 Dataset Manifest와 Rubric |
| partitions/partition hash | DEV-only request와 Manifest Case resources |
| task types/status/decision/blockers | `RunOutcome` |
| executed_by/started_at | `RunMaterial` |
| runtime_eligible | 항상 `false` |
| candidate bundle/guard 필드 | 항상 `null` |
| completed_at/content manifest hash | `finalize_artifacts()` |

`MetricResult`는 Comparison Scope의 metric/version/partition/slice/required/unit/estimator/independence/cluster/CI/threshold를
그대로 복사하고 `execution_status=NOT_IMPLEMENTED`, `decision_status=null`, sample/count/value/CI result/reason을
`null`로 둔다. `SuiteCaseResult.artifact_ref`는 `id=case_id`, `version="1.0.0"`과 CaseResult canonical bytes의
SHA-256 hash로 만들며 failure code가 여러 개면 UTF-16BE 정렬의 첫 code만 Suite 요약에 넣는다.

- [ ] **Step 9: Task 4 테스트를 실행하고 커밋한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_result_manifest.py ai_worker/tests/evaluation/test_artifact_schemas.py -q
```

Expected: PASS.

```bash
git add ai_worker/tasks/evaluation/manifest.py ai_worker/tasks/evaluation/runner.py ai_worker/tests/evaluation/test_result_manifest.py
git commit -m "feat(evals): build canonical dev run artifacts"
```

---

### Task 5: 비민감 Markdown Reporter

**Files:**

- Create: `ai_worker/tasks/evaluation/reporter.py`
- Create: `ai_worker/tests/evaluation/test_reporter.py`

**Interfaces:**

- Consumes: `ReportData`, 검증된 `MetricResults`, `SuiteResults`, `Sequence[ContentArtifact]`.
- Produces: `render_report(report_data, metrics, suite_results, entries) -> bytes`.

- [ ] **Step 1: 허용 정보 projection 테스트를 작성한다**

```python
def test_report_contains_only_machine_summary(dev_artifact_draft: ArtifactDraft) -> None:
    report = render_report(
        dev_artifact_draft.report_data,
        dev_artifact_draft.metrics,
        dev_artifact_draft.suite_results,
        machine_entries(dev_artifact_draft),
    ).decode()
    assert f"Run ID: `{dev_artifact_draft.report_data.run_id}`" in report
    assert "DEV validation only" in report
    assert "Not a Release decision" in report
    assert "cases.jsonl" in report
    assert "report.md" not in report
    assert "result-content-manifest.json" not in report
```

- [ ] **Step 2: 민감·원문 sentinel 테스트를 작성한다**

```python
def test_report_never_projects_dataset_query_or_gold_text(
    loaded_dev_dataset: ValidatedDataset,
    dev_artifact_draft: ArtifactDraft,
) -> None:
    report = render_report(
        dev_artifact_draft.report_data,
        dev_artifact_draft.metrics,
        dev_artifact_draft.suite_results,
        machine_entries(dev_artifact_draft),
    )
    forbidden = [
        case.query
        for case in loaded_dev_dataset.cases
    ] + [
        claim.claim_text
        for case in loaded_dev_dataset.cases
        for claim in (case.expected.gold_claims or ())
    ]
    assert all(value.encode() not in report for value in forbidden)
    validate_privacy_boundary({"report": report.decode("utf-8")})
```

- [ ] **Step 3: Markdown 비정본성 테스트를 작성한다**

```python
def test_editing_report_does_not_change_semantic_hash(dev_artifacts: PublishedArtifacts) -> None:
    before = semantic_content_hash(dev_artifacts.semantic_files())
    edited_report = dev_artifacts.files["report.md"] + b"\noperator note\n"
    assert edited_report
    assert semantic_content_hash(dev_artifacts.semantic_files()) == before
```

- [ ] **Step 4: Reporter 테스트가 실패하는지 확인한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_reporter.py -q
```

Expected: reporter module과 `render_report()`가 없어 FAIL.

- [ ] **Step 5: Reporter를 고정된 section 순서로 구현한다**

```python
def render_report(
    report_data: ReportData,
    metrics: MetricResults,
    suite_results: SuiteResults,
    entries: Sequence[ContentArtifact],
) -> bytes:
    lines = [
        "# RAG Evaluation DEV Report",
        "",
        "> DEV validation only — Not a Release decision",
        "",
        f"- Run ID: `{report_data.run_id}`",
        f"- Experiment Type: `{report_data.experiment_type.value}`",
        f"- Variant ID: `{report_data.variant_id}`",
        f"- Execution Status: `{report_data.execution_status.value}`",
        f"- Decision Status: `{report_data.decision_status.value if report_data.decision_status else 'null'}`",
        f"- Dataset: `{report_data.dataset_code}@{report_data.dataset_version}`",
        f"- Evaluation Profile: `{report_data.evaluation_profile_ref.id}@{report_data.evaluation_profile_ref.version}` `{report_data.evaluation_profile_ref.hash}`",
        f"- Comparison Policy: `{report_data.comparison_policy_ref.id}@{report_data.comparison_policy_ref.version}` `{report_data.comparison_policy_ref.hash}`",
        f"- Evaluation Policy: `{report_data.evaluation_policy_ref.id}@{report_data.evaluation_policy_ref.version}` `{report_data.evaluation_policy_ref.hash}`",
        f"- Suite: `{report_data.suite_ref.id}@{report_data.suite_ref.version}` `{report_data.suite_ref.hash}`",
        f"- Metric Records: `{len(metrics.metrics)}`",
        f"- Suite Status: `{suite_results.aggregate_execution_status.value}`",
        "",
        "## Task Counts",
        "",
    ]
    lines.extend(
        f"- `{task_type}`: {count}"
        for task_type, count in sorted(
            report_data.task_case_counts.items(),
            key=lambda item: item[0].encode("utf-16-be"),
        )
    )
    lines.extend(["", "## Blocking and Failures", ""])
    lines.extend(f"- `{status.value}`" for status in report_data.blocking_execution_statuses)
    lines.extend(f"- `{code}`" for code in report_data.failure_codes)
    lines.extend(["", "## Machine Artifacts", ""])
    lines.extend(f"- `{entry.relative_path}` `{entry.sha256}`" for entry in entries)
    return ("\n".join(lines) + "\n").encode("utf-8")
```

Reporter는 미완성 `run.json`, case context/expected/actual text와 exception object를 인자로 받지 않는다.

- [ ] **Step 6: Task 5 테스트를 실행하고 커밋한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_reporter.py -q
```

Expected: PASS.

```bash
git add ai_worker/tasks/evaluation/reporter.py ai_worker/tests/evaluation/test_reporter.py
git commit -m "feat(evals): render noncanonical safe reports"
```

---

### Task 6: Run directory no-clobber 원자 발행

**Files:**

- Create: `ai_worker/tasks/evaluation/publisher.py`
- Create: `ai_worker/tests/evaluation/test_publisher.py`
- Modify: `ai_worker/tasks/evaluation/cli.py`

**Interfaces:**

- Consumes: `dict[str, bytes]` 완성 Artifact set, allowed root, canonical Run ID.
- Produces: `publish_run_directory(*, allowed_root: Path, run_id: str, files: Mapping[str, bytes]) -> Path`.
- Preserves: 기존 `publish_receipt_no_clobber()` behavior와 `validate` CLI tests.

- [ ] **Step 1: 정상 발행과 permission 테스트를 작성한다**

```python
def test_publish_run_directory_is_private_and_complete(tmp_path: Path) -> None:
    files = safe_complete_bundle_files()
    destination = publish_run_directory(
        allowed_root=tmp_path,
        run_id=RUN_ID,
        files=files,
    )
    assert destination == tmp_path / RUN_ID
    assert destination.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in destination.iterdir())
    assert sorted(path.name for path in tmp_path.iterdir()) == [RUN_ID]
```

- [ ] **Step 2: no-clobber와 symlink 테스트를 작성한다**

기존 final directory, 기존 `<run_id>.lock`, ancestor symlink, final symlink, absolute/비정규 Unicode Run ID,
`..`를 모두 거부하고 기존 bytes가 보존되는지 확인한다.

```python
def test_publish_does_not_remove_operator_owned_lock(tmp_path: Path) -> None:
    lock = tmp_path / f"{RUN_ID}.lock"
    lock.write_bytes(b"operator-owned")
    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(
            allowed_root=tmp_path,
            run_id=RUN_ID,
            files=safe_complete_bundle_files(),
        )
    assert caught.value.code is EvaluationErrorCode.RESULT_PATH_CONFLICT
    assert lock.read_bytes() == b"operator-owned"
```

- [ ] **Step 3: 부분 write·fsync·rename 실패 테스트를 작성한다**

`os.write`, `os.fsync`, `exclusive_rename`을 각각 monkeypatch해 실패시키고 final directory가 노출되지 않으며 해당 호출이
만든 staging만 제거되는지 확인한다. cross-filesystem `EXDEV`는 `EVAL_ATOMIC_PUBLISH_UNSUPPORTED`로 mapping한다.
추가로 staging `mkdir` 성공 직후 `os.open`에 `EIO`를 주입한다. fd가 아직 없더라도 생성 시 저장한 inode identity로
본인이 만든 빈 staging을 제거하고 lock과 임시 directory가 모두 남지 않는지 검증한다.
lock·staging entry 교체와 descriptor 또는 entry identity 조회 실패를 동시에 주입한다. descriptor와 entry의
일치를 확정하지 못한 경우 replacement와 원본을 모두 보존하고 안정 오류로 실패하는지 확인한다. staging
fsync 뒤 entry 교체, rename syscall 경계 교체, 허용되지 않은 추가 파일 주입도 각각 재현하여 잘못된 final을
성공으로 반환하지 않고 자신이 확정한 lock만 정리하는지 확인한다.

- [ ] **Step 4: Publisher 테스트가 실패하는지 확인한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_publisher.py -q
```

Expected: publisher module과 directory publication interface가 없어 FAIL.

- [ ] **Step 5: directory publisher를 dir-fd 기반으로 구현한다**

```python
def publish_run_directory(
    *, allowed_root: Path, run_id: str, files: Mapping[str, bytes]
) -> Path:
    root_fd = open_allowed_root(allowed_root)
    lock = acquire_owned_lock(root_fd, run_id)
    staging = create_private_staging(root_fd, run_id)
    write_and_fsync_allowlisted_files(staging.fd, files)
    fsync_directory(staging.fd)
    rename_staging_no_clobber(root_fd, staging.name, run_id)
    fsync_directory(root_fd)
    remove_owned_lock(root_fd, lock)
    return allowed_root / run_id
```

filename allowlist는 `run.json`, `cases.jsonl`, `metrics.json`, `suite-results.json`, `failures.jsonl`,
`result-content-manifest.json`, `report.md`로 고정한다. cleanup은 inode identity를 확인한 staging/lock에만 수행한다.
staging directory 생성 identity는 fd 획득 여부와 별도로 보존하며, `mkdir` 뒤 `open` 실패 cleanup은 identity가
일치하는 빈 directory에만 `rmdir`을 수행한다. directory 또는 lock entry를 제거한 뒤에는 parent를 fsync한다.
파일과 열린 staging은 descriptor identity와 entry identity가 일치한 경우에만 owned로 표시한다. identity가
미확정이거나 불일치하면 현재 경로의 identity를 cleanup ownership으로 다시 채우지 않고 entry를 보존한다.
rename 직전에는 fd/name identity와 실제 7-file set을 검증하고, 이름 기반 exclusive rename 직후 final identity를
같은 fd identity와 다시 비교한다. post-rename 불일치 시 replacement final은 삭제하지 않고 fail-closed하되,
확정된 lock은 제거한다. cleanup 대상은 예측 불가능한 격리 이름으로 exclusive rename한 뒤 기록된 identity와
재비교하고, 일치한 격리 entry만 삭제한다. identity 조회·비교·삭제가 실패하면 원래 이름으로 복원하거나 원래
이름이 점유된 경우 격리 이름으로도 보존한다. Bundle 검증은 파일명 set뿐 아니라 생성 시 기록한 일곱 파일의
개별 identity와 생성 입력의 byte length·SHA-256까지 확인하며, directory cleanup 실패와 lock cleanup은 서로
독립적으로 시도한다. 이 cleanup 보장은 private root와 예측 불가능한 격리 이름을 준수하는 협조적 publisher를
대상으로 하며, 삭제 syscall 내부에서 이름을 바꿀 수 있는 비협조적 same-UID actor는 별도 OS identity 또는
프로세스 sandbox로 격리해야 한다.
입력 key set이 이 일곱 파일과 정확히 같지 않으면 staging 생성 전에 `EVAL_MANIFEST_INVALID`로 거부한다.

`rename_staging_no_clobber()`는 일반 `os.rename()`을 사용하지 않는다. Darwin에서는 libc
`renameatx_np(parent_fd, source, parent_fd, target, RENAME_EXCL)` (`RENAME_EXCL=0x00000004`), Linux에서는 libc
`renameat2(parent_fd, source, parent_fd, target, RENAME_NOREPLACE)` (`RENAME_NOREPLACE=1`)를 `ctypes`로 호출한다.
symbol이 없거나 지원하지 않는 플랫폼은 `EVAL_ATOMIC_PUBLISH_UNSUPPORTED`로 fail closed한다. 반환값이 `-1`이면
`ctypes.get_errno()`를 기존 publication error mapping으로 전달한다.

- [ ] **Step 6: 기존 receipt publisher helper를 이동하거나 wrapper로 유지한다**

공통 dir-fd primitive를 `publisher.py`로 옮기되 `cli.publish_receipt_no_clobber` import가 깨지지 않도록 CLI에서
re-export한다.

```python
from ai_worker.tasks.evaluation.publisher import publish_receipt_no_clobber, publish_run_directory
```

- [ ] **Step 7: Task 6 테스트를 실행하고 커밋한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_publisher.py ai_worker/tests/evaluation/test_cli.py -q
```

Expected: PASS.

```bash
git add ai_worker/tasks/evaluation/publisher.py ai_worker/tasks/evaluation/cli.py ai_worker/tests/evaluation/test_publisher.py ai_worker/tests/evaluation/test_cli.py
git commit -m "feat(evals): publish run bundles atomically"
```

---

### Task 7: `run-dev` CLI 통합

**Files:**

- Modify: `ai_worker/tasks/evaluation/cli.py`
- Modify: `ai_worker/tasks/evaluation/__init__.py`
- Modify: `ai_worker/tests/evaluation/test_cli.py`

**Interfaces:**

- Consumes: Task 1~6의 config/preflight/runner/manifest/reporter/publisher interface.
- Produces: `python -m ai_worker.tasks.evaluation run-dev --config <config-path> --run-id <uuid> --executed-by <github-login>`.
- Exit contract: 성공 `0`, invalid/user input `2`, internal/publish failure `1`; stderr에는 stable code 한 줄만 출력.

- [ ] **Step 1: 기존 `validate` 회귀 테스트를 먼저 고정한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_cli.py -k 'not run_dev' -q
```

Expected: 현재 모든 기존 `validate` 테스트 PASS.

- [ ] **Step 2: 세 Experiment CLI 통합 테스트를 작성한다**

```python
@pytest.mark.parametrize(
    "config_name",
    [
        "dev-foundation-knowledge-retrieval-v1.execution.json",
        "dev-foundation-answer-grounding-safety-v1.execution.json",
        "dev-foundation-end-to-end-rag-v1.execution.json",
    ],
)
def test_run_dev_publishes_schema_valid_bundle(
    config_name: str,
    tmp_path: Path,
    clean_repository_state: RepositoryStateProvider,
    success_registry: AdapterRegistry,
) -> None:
    run_id = str(uuid4())
    exit_code = main(
        ["run-dev", "--config", f"evals/configs/{config_name}", "--run-id", run_id,
         "--executed-by", "ceohwj"],
        allowed_result_root=tmp_path,
        repository_state_provider=clean_repository_state,
        adapter_registry=success_registry,
        clock=fixed_clock,
    )
    assert exit_code == 0
    assert sorted(path.name for path in (tmp_path / run_id).iterdir()) == [
        "cases.jsonl", "failures.jsonl", "metrics.json", "report.md",
        "result-content-manifest.json", "run.json", "suite-results.json",
    ]
```

- [ ] **Step 3: invalid preflight는 Artifact를 만들지 않는 테스트를 작성한다**

```python
def test_run_dev_rejects_holdout_before_load_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def spy_loader(*args: object, **kwargs: object) -> object:
        nonlocal called
        del args, kwargs
        called = True
        raise AssertionError("load_dataset must not be called")

    monkeypatch.setattr(cli_module, "load_dataset", spy_loader)
    run_id = str(uuid4())
    exit_code = main(
        run_dev_args(config=write_holdout_request(tmp_path), run_id=run_id),
        allowed_result_root=tmp_path / "results",
        repository_state_provider=clean_repository_state,
    )
    assert exit_code == 2
    assert called is False
    assert not (tmp_path / "results" / run_id).exists()
```

malformed config, dirty repository, loaded reference hash mismatch를 parameterize하고 각각 기대 code
`EVAL_JSON_INVALID`, `EVAL_REPOSITORY_STATE_INVALID`, `EVAL_HASH_MISMATCH` 한 줄만 stderr에 출력되는지 확인한다.

- [ ] **Step 4: 새 CLI 테스트가 실패하는지 확인한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_cli.py -k 'run_dev' -q
```

Expected: `run-dev` parser와 orchestration이 없어 FAIL.

- [ ] **Step 5: CLI parser와 dependency injection을 구현한다**

```python
run_dev = commands.add_parser("run-dev")
run_dev.add_argument("--config", required=True)
run_dev.add_argument("--run-id", required=True)
run_dev.add_argument("--executed-by", required=True)


type Clock = Callable[[], str]


def main(
    argv: Sequence[str] | None = None,
    *,
    allowed_result_root: Path | None = None,
    repository_state_provider: RepositoryStateProvider = git_repository_state,
    adapter_registry: AdapterRegistry = EMPTY_ADAPTER_REGISTRY,
    clock: Clock = system_clock,
) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "validate":
        return _run_validate(arguments, allowed_result_root=allowed_result_root)
    return _run_dev(
        arguments,
        allowed_result_root=allowed_result_root,
        repository_state_provider=repository_state_provider,
        adapter_registry=adapter_registry,
        clock=clock,
    )
```

Production root는 `evals/results`, `executed_by`는 `GITHUB_LOGIN/EVALUATION_IMPLEMENTER` ActorRef로 생성한다. 입력
graph 검증이 끝나기 전에는 lock/staging/final directory를 만들지 않는다.

- [ ] **Step 6: Artifact 조립 순서를 구현한다**

순서는 고정한다.

```text
config load + manifest bytes snapshot → manifest-only preflight → 같은 snapshot으로 load_dataset → loaded binding validation
→ execute_dev_cases → machine models → machine entry hashes → report.md
→ content manifest → finalized run.json → all models/privacy revalidation
→ publish_run_directory
```

미등록 production Adapter는 `NOT_IMPLEMENTED/null` bundle을 생성할 수 있지만 성공 완료로 표현하지 않는다.
`comparison.json`, `gate.json`, baseline receipt는 어떤 분기에서도 만들지 않는다.

- [ ] **Step 7: 실제 CLI를 임시 root에 두 번 실행해 semantic hash를 비교한다**

테스트용 success registry와 fixed clock injection을 사용하는 integration helper로 서로 다른 Run ID를 실행한다.

```python
first_hash = semantic_content_hash(read_machine_files(tmp_path / RUN_ID_A))
second_hash = semantic_content_hash(read_machine_files(tmp_path / RUN_ID_B))
assert first_hash == second_hash
```

- [ ] **Step 8: Task 7 테스트를 실행하고 커밋한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation/test_cli.py ai_worker/tests/evaluation/test_runner.py ai_worker/tests/evaluation/test_result_manifest.py ai_worker/tests/evaluation/test_reporter.py ai_worker/tests/evaluation/test_publisher.py -q
```

Expected: PASS.

```bash
git add ai_worker/tasks/evaluation/cli.py ai_worker/tasks/evaluation/__init__.py ai_worker/tests/evaluation/test_cli.py
git commit -m "feat(evals): expose dev runner reporter cli"
```

---

### Task 8: 문서화와 전체 검증

**Files:**

- Modify: `evals/README.md`
- Modify only if implementation changed the agreed behavior: `docs/designs/ceohwj/rag-evaluation-runner-reporter-design.md`

**Interfaces:**

- Consumes: 완성된 `run-dev` CLI와 검증 명령.
- Produces: 팀원이 복사해 실행할 수 있는 세 DEV 명령, 상태 해석, 다음 단계 차단선.

- [ ] **Step 1: README에 세 실행 예시를 추가한다**

```bash
uv run python -m ai_worker.tasks.evaluation run-dev \
  --config evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json \
  --run-id 123e4567-e89b-42d3-a456-426614174000 \
  --executed-by ceohwj
```

Answer Grounding·Safety와 End-to-End는 config path만 바꾼 명령을 각각 적는다. 결과가 로컬·CI DEV evidence이며
Release PASS, HOLDOUT 승인, Baseline Freeze가 아니라는 문장을 바로 아래에 둔다.

- [ ] **Step 2: 금지 동작과 후속 단계 상태를 문서화한다**

다음을 명시한다.

```text
- HOLDOUT·SAFETY_REGRESSION은 이 명령으로 load·execute·observe할 수 없다.
- Provider/Metric 미구현은 NOT_IMPLEMENTED/null이며 PASS가 아니다.
- 기존 Run ID는 덮어쓰지 않는다. 재실행에는 새 Run ID가 필요하다.
- #158~#161 DEV Metric과 승인된 Comparison/Evaluation Policy 전 상태는
  WAITING_FOR_APPROVED_COMPARISON_POLICY다.
- evals/results/는 Git 추적 대상이 아니다.
```

- [ ] **Step 3: 전체 Evaluation 테스트를 실행한다**

Run:

```bash
uv run pytest ai_worker/tests/evaluation -q
```

Expected: 모든 테스트 PASS, skipped/xfail이 새로 생기지 않음.

- [ ] **Step 4: 정적 검사를 실행한다**

Run:

```bash
uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation
uv run ruff format ai_worker/tasks/evaluation ai_worker/tests/evaluation --check
uv run mypy ai_worker/tasks/evaluation
```

Expected: 세 명령 모두 exit 0.

- [ ] **Step 5: Artifact와 scope를 최종 점검한다**

Run:

```bash
git diff --check
git status --short
git diff --name-only origin/develop..HEAD
git ls-files evals/results evals/validation-results
```

Expected:

- whitespace 오류 없음.
- 예상 파일 외 변경 없음.
- `evals/results/`, `evals/validation-results/` 결과 파일이 Git에 추가되지 않음.
- `evals/schemas/`, `docs/contracts/`, `evals/retrieval/cases/rag-holdout-safety-v1/` 변경 없음.

- [ ] **Step 6: 문서와 최종 보완을 커밋한다**

```bash
git add evals/README.md docs/designs/ceohwj/rag-evaluation-runner-reporter-design.md
git commit -m "docs(evals): document dev runner workflow"
```

설계 문서에 구현 중 동작 변경이 없었다면
`docs/designs/ceohwj/rag-evaluation-runner-reporter-design.md`는 stage하지 않는다.

- [ ] **Step 7: PR 증빙을 준비한다**

PR에는 다음을 기록한다.

```text
- Issue #157 중 이번 PR이 완료하는 DEV Runner·Reporter 범위
- 세 Experiment Type별 synthetic DEV 실행 결과
- 서로 다른 Run ID 2개의 semantic content hash 일치
- HOLDOUT loader spy가 0회 호출된 negative test
- 전체 pytest/Ruff/format/mypy 결과
- HOLDOUT Baseline Freeze와 Issue #157 Close가 아님
- 책임 리뷰어 @hazelnutflavoured, Artifact/DB 경계 교차 리뷰어 @phina-io
```

---

## Plan Self-Review 결과

- Spec coverage: execution config, Variant, DEV preflight, deterministic execution, no retry, Case 오류 격리, 상태
  집계, JSON/JSONL, content/semantic hash, Reporter, privacy, atomic publish, HOLDOUT 차단, 검증 명령이 Task 1~8에
  각각 연결되어 있다.
- Scope boundary: comparison/gate/baseline/provider/metric/DB/API는 구현 Task에서 제외되어 있다.
- Type consistency: `ResolvedDevExecution → RunOutcome → RunMaterial → ArtifactDraft/ReportData → PublishedArtifacts → Publisher`
  흐름과 함수명이 모든 Task에서 동일하다.
- Schema caveat: technical Adapter 오류는 `CaseResult(ERROR/null)`로 기록하고, 기존 enum으로 정확히 표현할 수
  없는 `FailureRecord`를 만들지 않는다. 미완료 Answer의 필수 hash는 `SHA-256(empty bytes)`로 고정한다.
- Placeholder scan: 미결정 표식이나 생략된 구현 단계가 없다.
