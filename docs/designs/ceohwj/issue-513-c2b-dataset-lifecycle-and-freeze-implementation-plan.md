# Issue #513 C2-b Protected Dataset Lifecycle & FREEZE Control-Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 제한된 control login이 Dataset을 등록·일반 전이·FREEZE하되 불변 binding, 40건·전수 검토·4축 0 누출·독립 승인 evidence, 원자적 audit receipt, 최소 DB 권한을 지키는 C2-b control-plane을 구현한다.

**Architecture:** `protected_dataset.binding` JSON은 등록 시점의 불변 원본으로 유지하고 lifecycle 컬럼만 CAS UPDATE한다. 성공한 `FREEZE_DATASET` CONTROL 감사 event ID를 `AUDIT_EVENT` logical receipt로 사용하며, data/control adapter가 불변 JSON·lifecycle 컬럼·검증된 audit chain을 합성해 권위 있는 `ProtectedDatasetBinding`을 반환한다.

**Tech Stack:** Python 3.13, Pydantic v2 strict models, SQLAlchemy 2 async, PostgreSQL, Alembic protected graph, Pytest, Ruff, Mypy

**Spec:** `docs/designs/ceohwj/issue-513-c2b-dataset-lifecycle-and-freeze-design.md`

## Global Constraints

- C2-a Issue `#512`의 병합 HEAD를 선행 기준으로 삼고 C2-a command/result/audit/replay 확장을 복제하지 않는다.
- `PD-368-R2`, Issue `#513`, target contract의 필드·상태·오류 semantics를 바꾸지 않는다.
- 새 DB column/table/migration, `binding` UPDATE, trigger, RLS, stored procedure, user-defined DB function을 추가하지 않는다.
- control role은 Dataset INSERT와 `state`, `state_revision`, `authored_count`, `review_complete`, `lock_marker` UPDATE만 허용한다.
- Dataset ID, request ID, approval source event ID는 canonical lowercase UUIDv4이고 Dataset version은 canonical numeric SemVer다.
- `TransitionDatasetCommand(to_state=FROZEN)`은 DB에 접근하지 않고 `ROLE_ACTION_STATE_DENIED`를 반환한다.
- FREEZE는 `REVIEW_READY`, 정확히 40건, `review_complete=true`, `(0,0,0,0)` leakage, 독립 Product Safety Reviewer evidence를 모두 요구한다.
- 새 C2-b Dataset의 FROZEN 조회는 일치하는 유일한 FREEZE CONTROL audit receipt가 없으면 fail-closed한다.
- 현재 schema의 한계로 동일 actor의 활성 `HOLDOUT_AUTHOR` DATA identity와 `DATASET_CUSTODIAN` CONTROL identity 겸직을 전역적으로 거부한다. 지정 리뷰어가 이 보수적 해석을 인수하지 않으면 구현을 멈추고 계약/schema 변경을 별도로 진행한다.
- 실제 환자·처방·OCR·Provider 원문, secret, credential, SQL, storage location을 fixture·log·error·audit에 넣지 않는다.
- effective enforcement, HOLDOUT access/run, production connector/provisioning, Current 승격, publication gate를 열지 않는다.

---

### Task 0: C2-a PR #522 병합 기준 확인

**Files:**
- Read: `ai_worker/tasks/evaluation/protected_retrieval.py`
- Read: `ai_worker/tasks/evaluation/protected_retrieval_control.py`
- Read: `ai_worker/adapters/postgresql_protected_retrieval_control.py`
- Read: `tests/integration/rag/test_protected_retrieval_control_postgresql.py`

**Interfaces:**
- Consumes: C2-a `RegisterIdentityCommand`, `DisableIdentityCommand`, `ControlAuditTargetKind.PROTECTED_IDENTITY`, `_ControlExecutor.database_login`, identity result/audit validation
- Produces: C2-b 작업이 시작할 수 있는 검증된 branch baseline

- [ ] **Step 1: 현재 branch와 C2-a 병합 여부를 확인한다**

Run:

```bash
git status --short --branch
git log -1 --oneline --decorate
rg -n "RegisterIdentityCommand|DisableIdentityCommand|PROTECTED_IDENTITY|database_login: str" ai_worker/tasks/evaluation ai_worker/adapters/postgresql_protected_retrieval_control.py
```

Expected: PR `#522`의 merge commit이 최신 `develop`에 포함되고, branch는 그 기준으로 정렬된
`feat/513-c2b-dataset-lifecycle-and-freeze`이며 네 C2-a symbol이 모두 존재한다.

- [ ] **Step 2: C2-a가 없으면 구현을 멈춘다**

`develop`에 Issue `#512`가 병합된 후라면 사용자가 승인한 Git 절차로 C2-b branch를 최신 `develop`에 재정렬한다. 병합 전이면 로컬 C2-a branch의 코드를 복사하거나 C2-b 커밋에 혼합하지 않는다.

- [ ] **Step 3: baseline 단위 테스트를 실행한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest ai_worker/tests/evaluation/test_protected_retrieval_control.py -q
```

Expected: C1/C2-a baseline PASS. 이 시점 실패를 C2-b 변경으로 가리지 않는다.

---

### Task 1: Dataset command·evidence·result·audit 계약

**Files:**
- Modify: `ai_worker/tasks/evaluation/protected_retrieval.py`
- Modify: `ai_worker/tasks/evaluation/protected_retrieval_control.py`
- Modify: `ai_worker/tests/evaluation/test_protected_retrieval_control.py`

**Interfaces:**
- Consumes: `_ControlCommand`, `_require_uuid_v4`, `ProtectedDatasetBinding`, `ProtectedDatasetState`, `ProtectedApprovalPrincipal`, C2-a result/audit category validators
- Produces: `RegisterDatasetCommand`, `TransitionDatasetCommand`, `FreezeDatasetCommand`, `FreezeApprovalSourceEvidence`, `verify_freeze_approval()`, three Dataset command kinds/reasons, Dataset result/audit validation

- [ ] **Step 1: Dataset command 형상 실패 테스트를 작성한다**

`ai_worker/tests/evaluation/test_protected_retrieval_control.py`에 다음 이름의 테스트를 추가한다.

```python
def test_register_dataset_command_requires_exact_initial_binding() -> None:
    binding = _dataset_binding()
    command = RegisterDatasetCommand(
        request_id=REQUEST_ID,
        dataset_id=binding.dataset_id,
        dataset_version=binding.dataset_version,
        binding=binding,
        manifest_sha256=binding.manifest_sha256,
        protected_artifact_sha256=binding.protected_artifact_sha256,
        hmac_key_version=binding.hmac_key_version,
    )
    assert command.binding.state is ProtectedDatasetState.ACCESS_AUTHORIZED
    assert command.binding.state_revision == 1
    assert command.binding.authored_count == 0
    assert command.binding.review_complete is False
    assert command.binding.freeze_receipt_ref is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_id", "not-a-uuid"),
        ("dataset_version", "01.0.0"),
        ("manifest_sha256", "A" * 64),
        ("protected_artifact_sha256", "b" * 63),
    ],
)
def test_dataset_commands_reject_noncanonical_identifiers(field: str, value: str) -> None:
    payload = _register_dataset_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        RegisterDatasetCommand.model_validate(payload)


def test_register_dataset_command_rejects_binding_mismatch_and_noninitial_state() -> None:
    payload = _register_dataset_payload()
    payload["manifest_sha256"] = SHA_B
    with pytest.raises(ValidationError, match="binding"):
        RegisterDatasetCommand.model_validate(payload)
    payload = _register_dataset_payload()
    payload["binding"] = _dataset_binding(state=ProtectedDatasetState.AUTHORING, state_revision=2)
    with pytest.raises(ValidationError, match="initial"):
        RegisterDatasetCommand.model_validate(payload)
```

`_dataset_binding()`은 UUIDv4 Dataset ID, `1.0.0`, synthetic lowercase hash, `ACCESS_AUTHORIZED`, revision 1, count 0, review false, receipt `None`을 반환하게 한다.

- [ ] **Step 2: FREEZE evidence·result·audit 실패 테스트를 작성한다**

```python
def test_freeze_evidence_requires_exact_completed_review_shape() -> None:
    evidence = _freeze_evidence()
    assert evidence.action is ProtectedAction.FREEZE
    assert evidence.authored_count == 40
    assert evidence.review_complete is True
    assert evidence.leakage_axis_intersections == (0, 0, 0, 0)
    assert evidence.issuer.role is ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authored_count", 39),
        ("review_complete", False),
        ("leakage_axis_intersections", (0, 0, 1, 0)),
        ("source_event_id", "not-a-uuid"),
    ],
)
def test_freeze_evidence_rejects_incomplete_or_noncanonical_values(field: str, value: object) -> None:
    payload = _freeze_evidence().model_dump(mode="python")
    payload[field] = value
    with pytest.raises(ValidationError):
        FreezeApprovalSourceEvidence.model_validate(payload)


@pytest.mark.parametrize(
    ("kind", "reason", "revision"),
    [
        (ControlCommandKind.REGISTER_DATASET, "DATASET_REGISTERED", 1),
        (ControlCommandKind.TRANSITION_DATASET, "DATASET_TRANSITIONED", 2),
        (ControlCommandKind.FREEZE_DATASET, "DATASET_FROZEN", 3),
    ],
)
def test_dataset_control_results_require_revision_without_authorization_audit(
    kind: ControlCommandKind,
    reason: str,
    revision: int,
) -> None:
    result = ControlCommandResult(
        request_id=REQUEST_ID,
        command_kind=kind,
        target_id=f"{DATASET_ID}:1.0.0",
        effective_revision=revision,
        authorization_audit_event_id=None,
        reason_code=reason,
    )
    assert result.effective_revision == revision
```

Dataset audit test는 `target_kind=PROTECTED_DATASET`, `target_id=f"{DATASET_ID}:1.0.0"`, success revision 존재, authorization audit ref `None`을 허용하고 target/reason/ref mismatch를 `ValidationError`로 거부해야 한다. DENIED `FREEZE_EVIDENCE_INCOMPLETE`는 success reference 없이 허용해야 한다.

- [ ] **Step 3: 실패를 확인한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest ai_worker/tests/evaluation/test_protected_retrieval_control.py -q
```

Expected: imports or missing enum/model/validator assertions FAIL.

- [ ] **Step 4: domain audit 계약을 최소 구현한다**

`ai_worker/tasks/evaluation/protected_retrieval.py`에 C2-a 맵을 확장한다.

```python
class ProtectedAuditReason(StrEnum):  # add these members to the existing enum
    DATASET_FROZEN = "DATASET_FROZEN"
    DATASET_REGISTERED = "DATASET_REGISTERED"
    DATASET_TRANSITIONED = "DATASET_TRANSITIONED"


_EXPECTED_CONTROL_TARGETS.update(
    {
        "REGISTER_DATASET": ControlAuditTargetKind.PROTECTED_DATASET,
        "TRANSITION_DATASET": ControlAuditTargetKind.PROTECTED_DATASET,
        "FREEZE_DATASET": ControlAuditTargetKind.PROTECTED_DATASET,
    }
)

_EXPECTED_CONTROL_REASONS.update(
    {
        "REGISTER_DATASET": ProtectedAuditReason.DATASET_REGISTERED,
        "TRANSITION_DATASET": ProtectedAuditReason.DATASET_TRANSITIONED,
        "FREEZE_DATASET": ProtectedAuditReason.DATASET_FROZEN,
    }
)
```

실제 enum에는 기존 member를 유지한 채 세 member를 알파벳/도메인 관례에 맞는 위치에 넣는다. `_CONTROL_DENIAL_REASONS`에 `FREEZE_EVIDENCE_INCOMPLETE`를 추가한다. Dataset target validator는 `rsplit(":", 1)`로 UUIDv4 ID와 SemVer를 검증하되 C1/C2-a target validation을 변경하지 않는다.

- [ ] **Step 5: command·evidence·result 계약을 최소 구현한다**

`ai_worker/tasks/evaluation/protected_retrieval_control.py`에 다음 형태를 추가한다.

```python
class ControlCommandKind(StrEnum):
    REGISTER_DATASET = "REGISTER_DATASET"
    TRANSITION_DATASET = "TRANSITION_DATASET"
    FREEZE_DATASET = "FREEZE_DATASET"


class _DatasetControlCommand(_ControlCommand):
    dataset_id: str
    dataset_version: str = Field(pattern=r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")

    @field_validator("dataset_id")
    @classmethod
    def require_dataset_uuid_v4(cls, value: str) -> str:
        return _require_uuid_v4(value)


class RegisterDatasetCommand(_DatasetControlCommand):
    binding: ProtectedDatasetBinding
    manifest_sha256: Sha256Hex
    protected_artifact_sha256: Sha256Hex
    hmac_key_version: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_initial_binding(self) -> RegisterDatasetCommand:
        immutable = (
            self.binding.dataset_id,
            self.binding.dataset_version,
            self.binding.manifest_sha256,
            self.binding.protected_artifact_sha256,
            self.binding.hmac_key_version,
        )
        commanded = (
            self.dataset_id,
            self.dataset_version,
            self.manifest_sha256,
            self.protected_artifact_sha256,
            self.hmac_key_version,
        )
        if immutable != commanded:
            raise ValueError("dataset binding does not match registration fields")
        if (
            self.binding.state is not ProtectedDatasetState.ACCESS_AUTHORIZED
            or self.binding.state_revision != 1
            or self.binding.authored_count != 0
            or self.binding.review_complete
            or self.binding.freeze_receipt_ref is not None
        ):
            raise ValueError("dataset binding must use the initial lifecycle")
        return self


class TransitionDatasetCommand(_DatasetControlCommand):
    from_state: ProtectedDatasetState
    to_state: ProtectedDatasetState
    expected_state_revision: int = Field(ge=1)
    authored_count: int = Field(ge=0, le=40)
    review_complete: bool


class FreezeDatasetCommand(_DatasetControlCommand):
    expected_state_revision: int = Field(ge=1)
    approval_source_event_id: str
    expected_raw_sha256: Sha256Hex

    @field_validator("approval_source_event_id")
    @classmethod
    def require_source_uuid_v4(cls, value: str) -> str:
        return _require_uuid_v4(value)
```

`FreezeApprovalSourceEvidence`는 spec §4.3의 exact fields와 `Literal[40]`, `Literal[True]`, 네 개 `Literal[0]`, UTC validator, unique participant validator를 구현한다. `TrustedApprovalSource` protocol에 `fetch_freeze()`를 추가하고 통합 테스트 fake source들이 typed method를 명시적으로 구현하게 한다.

기존 `_ApprovalSource`, `_FailingApprovalSource`, `_MissingApprovalSource`에도 아래 시그니처를 추가해 C1 `fetch()` test와 protocol type checking을 유지한다. 각 구현은 Dataset test에 재사용하지 않고 `ApprovalSourceNotFoundError`를 발생시킨다.

```python
async def fetch_freeze(self, source_event_id: str) -> FreezeApprovalSourceEvidence:
    del source_event_id
    raise ApprovalSourceNotFoundError
```

- [ ] **Step 6: FREEZE evidence 결속 helper를 구현한다**

```python
def verify_freeze_approval(
    dataset: ProtectedDatasetBinding,
    evidence: FreezeApprovalSourceEvidence,
    *,
    approval_source_event_id: str,
    expected_raw_sha256: str,
    executor: ProtectedApprovalPrincipal,
) -> None:
    if evidence.source_event_id != approval_source_event_id:
        raise ProtectedSecurityError("APPROVAL_EVIDENCE_MISMATCH")
    if evidence.action is not ProtectedAction.FREEZE:
        raise ProtectedSecurityError("APPROVAL_ACTION_MISMATCH")
    if executor.role is not ProtectedApprovalRole.DATASET_CUSTODIAN:
        raise ProtectedSecurityError("ISSUER_ROLE_DENIED")
    if evidence.issuer.role is not ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER:
        raise ProtectedSecurityError("ISSUER_ROLE_DENIED")
    if evidence.issuer.actor == executor.actor:
        raise ProtectedSecurityError("SELF_APPROVAL_DENIED")
    if executor.actor in evidence.implementation_participants or evidence.issuer.actor in evidence.implementation_participants:
        raise ProtectedSecurityError("SELF_APPROVAL_DENIED")
    if (
        evidence.canonical_raw_sha256 != expected_raw_sha256
        or evidence.dataset_id != dataset.dataset_id
        or evidence.dataset_version != dataset.dataset_version
        or evidence.manifest_sha256 != dataset.manifest_sha256
        or evidence.protected_artifact_sha256 != dataset.protected_artifact_sha256
        or evidence.authored_count != dataset.authored_count
        or evidence.review_complete != dataset.review_complete
        or evidence.leakage_axis_intersections != dataset.leakage_axis_intersections
    ):
        raise ProtectedSecurityError("APPROVAL_EVIDENCE_MISMATCH")
```

line length와 기존 formatter 규칙에 맞게 조건을 개행한다. Dataset의 40/review/leakage 상태 검증은 Task 4 service에서 `FREEZE_EVIDENCE_INCOMPLETE`로 별도 매핑한다.

- [ ] **Step 7: 단위 계약 테스트를 통과시킨다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest ai_worker/tests/evaluation/test_protected_retrieval_control.py -q
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run ruff check ai_worker/tasks/evaluation/protected_retrieval.py ai_worker/tasks/evaluation/protected_retrieval_control.py ai_worker/tests/evaluation/test_protected_retrieval_control.py
```

Expected: PASS.

- [ ] **Step 8: 계약 slice를 커밋한다**

```bash
git add ai_worker/tasks/evaluation/protected_retrieval.py ai_worker/tasks/evaluation/protected_retrieval_control.py ai_worker/tests/evaluation/test_protected_retrieval_control.py
git commit -m "✨ feat: #513 Dataset control command 계약 추가"
```

---

### Task 2: 불변 binding·lifecycle·receipt Dataset 조립

**Files:**
- Modify: `ai_worker/adapters/postgresql_protected_retrieval.py`
- Modify: `ai_worker/adapters/postgresql_protected_retrieval_control.py`
- Modify: `tests/integration/rag/test_protected_retrieval_postgresql.py`
- Modify: `tests/integration/rag/test_protected_retrieval_control_postgresql.py`

**Interfaces:**
- Consumes: `ProtectedDatasetBinding`, `ControlCommandAuditEntry`, `ProtectedAuditEntry`, `audit_entry_sha256()`
- Produces: `_assemble_dataset_binding(...) -> ProtectedDatasetBinding`, lifecycle-aware data/control reads, audit-backed `freeze_receipt_ref`

- [ ] **Step 1: 조립 helper 실패 테스트를 작성한다**

`tests/integration/rag/test_protected_retrieval_postgresql.py`의 adapter fixture를 이용해 다음 행동을 각각 독립 test로 고정한다.

이 Task에서 사용하는 fixture helpers를 해당 test module에 먼저 추가한다.

- `_dataset(**updates) -> ProtectedDatasetBinding`: canonical UUIDv4 Dataset과 기본 lifecycle을 만들고 update 후 strict 재검증한다.
- `_insert_dataset(database, dataset)`: owner fixture connection으로 JSON과 모든 정규화 컬럼을 동일 값으로 INSERT한다.
- `_owner_update_lifecycle(database, dataset, *, state, state_revision, authored_count, review_complete)`: lifecycle 네 컬럼만 바꾸며 negative/overlay fixture 준비에만 사용한다.
- `_load_dataset_through_data_adapter(database, dataset) -> ProtectedDatasetBinding`: limited data login으로 새 `PostgresqlAuthorizationLedger` session을 만들고 `require_dataset()`을 호출한다.
- `_append_freeze_audit_fixture(database, dataset, *, event_id, revision)`: 기존 audit append/hash helper를 재사용해 유효한 CONTROL chain과 head를 함께 만든다.

기존 `_read_scenario()`의 FROZEN fixture가 임의 REQUEST receipt를 JSON에 직접 넣는 경로는 새 정책과 충돌한다. RUN 관련 기존 테스트는 유효한 FREEZE audit fixture를 함께 넣도록 바꾸고, JSON receipt만으로 FROZEN 조회가 성공하는 기대는 제거한다.

```python
async def test_dataset_read_overlays_lifecycle_columns_without_updating_binding(
    protected_database: _ProtectedDatabase,
) -> None:
    stored = _dataset(state=ProtectedDatasetState.ACCESS_AUTHORIZED, state_revision=1)
    await _insert_dataset(protected_database, stored)
    await _owner_update_lifecycle(
        protected_database,
        stored,
        state=ProtectedDatasetState.AUTHORING,
        state_revision=2,
        authored_count=17,
        review_complete=False,
    )
    loaded = await _load_dataset_through_data_adapter(protected_database, stored)
    assert loaded.state is ProtectedDatasetState.AUTHORING
    assert loaded.state_revision == 2
    assert loaded.authored_count == 17
    assert loaded.freeze_receipt_ref is None
```

추가 test는 JSON/column Dataset ID·version·manifest hash·artifact hash·key version 중 하나를 변조했을 때 `DATASET_BINDING_MISMATCH`, FROZEN lifecycle에 matching FREEZE audit가 없을 때 `AUDIT_BINDING_MISMATCH`를 기대한다.

- [ ] **Step 2: 실패를 확인한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/integration/rag/test_protected_retrieval_postgresql.py -q
```

Expected: adapter가 여전히 JSON `binding`만 반환해 lifecycle overlay/receipt test FAIL.

- [ ] **Step 3: pure 조립 helper를 구현한다**

`ai_worker/adapters/postgresql_protected_retrieval.py`에 다음 interface를 두고 data/control adapter가 공유하게 한다.

```python
def _assemble_dataset_binding(
    *,
    binding_value: object,
    dataset_id: str,
    dataset_version: str,
    manifest_sha256: str,
    protected_artifact_sha256: str,
    hmac_key_version: str,
    state: str,
    state_revision: int,
    authored_count: int,
    review_complete: bool,
    audit_entries: tuple[ProtectedAuditEntry, ...],
) -> ProtectedDatasetBinding:
    stored = _model(ProtectedDatasetBinding, binding_value, "DATASET_BINDING_MISMATCH")
    if (
        stored.dataset_id != dataset_id
        or stored.dataset_version != dataset_version
        or stored.manifest_sha256 != manifest_sha256
        or stored.protected_artifact_sha256 != protected_artifact_sha256
        or stored.hmac_key_version != hmac_key_version
    ):
        raise ProtectedSecurityError("DATASET_BINDING_MISMATCH")
    updates: dict[str, object] = {
        "state": state,
        "state_revision": state_revision,
        "authored_count": authored_count,
        "review_complete": review_complete,
        "freeze_receipt_ref": None,
    }
    if state == ProtectedDatasetState.FROZEN.value:
        target_id = f"{dataset_id}:{dataset_version}"
        matches = tuple(
            entry
            for entry in audit_entries
            if isinstance(entry, ControlCommandAuditEntry)
            and entry.command_kind == "FREEZE_DATASET"
            and entry.target_kind is ControlAuditTargetKind.PROTECTED_DATASET
            and entry.target_id == target_id
            and entry.outcome is ControlAuditOutcome.SUCCEEDED
            and entry.reason_code is ProtectedAuditReason.DATASET_FROZEN
            and entry.result_effective_revision == state_revision
        )
        if len(matches) != 1:
            raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
        updates["freeze_receipt_ref"] = OpaqueLogicalRef(
            namespace=OpaqueRefNamespace.AUDIT_EVENT,
            value=matches[0].event_id,
        )
    payload = stored.model_dump(mode="python")
    payload.update(updates)
    return _model(ProtectedDatasetBinding, payload, "DATASET_BINDING_MISMATCH")
```

`model_copy(update=...)`는 사용하지 않는다. Pydantic v2에서 update payload 재검증을 보장하지 않으므로 위와 같이 전체 payload를 strict model로 다시 검증한다. `state`, revision, count, review column 값이 model validation을 통과하지 못하면 고정 오류 `DATASET_BINDING_MISMATCH`로 fail-closed한다.

- [ ] **Step 4: 모든 Dataset read site를 공통 조립으로 바꾼다**

다음 세 경로의 SQL을 `binding` 단일 SELECT에서 불변/lifecycle 컬럼 SELECT로 바꾸고, FROZEN이면 `PostgresqlProtectedAuditJournal._verified_entries(lock_head=False)` 결과를 helper에 전달한다.

- `PostgresqlAuthorizationLedger.require_dataset()`
- `_PostgresqlGuardSession`/`_GuardContext.__aenter__()`
- `_ControlSession.require_grant_dataset()`

SQL projection은 다음과 같다.

```sql
SELECT binding, dataset_id, dataset_version, manifest_sha256,
       protected_artifact_sha256, hmac_key_version, state,
       state_revision, authored_count, review_complete
FROM protected_schema.protected_dataset
WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version
```

FROZEN이 아니면 empty tuple을 전달해 불필요한 full audit scan을 하지 않는다. FROZEN은 full verified chain 없이 JSON에 들어 있던 receipt를 사용하지 않는다.

- [ ] **Step 5: 실제 FREEZE audit fixture를 사용해 receipt 성공/위변조 테스트를 완성한다**

FROZEN fixture는 `ControlCommandAuditEntry`를 `audit_entry_sha256()`로 hash하고 `audit_entry`·`audit_head`를 함께 갱신한다. `event_id`, target, revision, reason, entry hash, previous hash를 각각 변조한 test가 고정 error로 거부되는지 확인한다.

- [ ] **Step 6: 조립 회귀를 통과시킨다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/integration/rag/test_protected_retrieval_postgresql.py tests/integration/rag/test_protected_retrieval_control_postgresql.py -q
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run ruff check ai_worker/adapters/postgresql_protected_retrieval.py ai_worker/adapters/postgresql_protected_retrieval_control.py tests/integration/rag/test_protected_retrieval_postgresql.py tests/integration/rag/test_protected_retrieval_control_postgresql.py
```

Expected: PASS.

- [ ] **Step 7: 조립 slice를 커밋한다**

```bash
git add ai_worker/adapters/postgresql_protected_retrieval.py ai_worker/adapters/postgresql_protected_retrieval_control.py tests/integration/rag/test_protected_retrieval_postgresql.py tests/integration/rag/test_protected_retrieval_control_postgresql.py
git commit -m "✨ feat: #513 Dataset lifecycle binding 조립"
```

---

### Task 3: Dataset 등록과 일반 전이 Application Service

**Files:**
- Modify: `ai_worker/adapters/postgresql_protected_retrieval_control.py`
- Modify: `tests/integration/rag/test_protected_retrieval_control_postgresql.py`

**Interfaces:**
- Consumes: Task 1 command/result/audit models, Task 2 `_assemble_dataset_binding()`, C2-a `_ControlExecutor.database_login`
- Produces: `_ControlSession.lock_dataset_executor()`, `register_dataset()`, `transition_dataset()`, exact replay/CAS/audit behavior

- [ ] **Step 1: register/transition 실패 통합 테스트를 작성한다**

`tests/integration/rag/test_protected_retrieval_control_postgresql.py`에 다음 이름의 async test를 추가한다.

먼저 기존 `_ApprovalSource(evidence)`를 무인자 생성하지 않는다. 기존 C1 fake의 `fetch()` 동작을 보존하면서 Dataset 테스트용 typed fake와 fixture helpers를 명시적으로 추가한다.

```python
class _DatasetApprovalSource:
    def __init__(self, freeze_evidence: FreezeApprovalSourceEvidence | None = None) -> None:
        self.freeze_evidence = freeze_evidence
        self.freeze_calls: list[str] = []

    async def fetch(self, source_event_id: str) -> ApprovalSourceEvidence:
        del source_event_id
        raise ApprovalSourceNotFoundError

    async def fetch_freeze(self, source_event_id: str) -> FreezeApprovalSourceEvidence:
        self.freeze_calls.append(source_event_id)
        if self.freeze_evidence is None:
            raise ApprovalSourceNotFoundError
        return self.freeze_evidence
```

이 Task에서 다음 helpers를 실제 구현한 뒤 아래 tests가 사용하게 한다.

- `_dataset_binding(**updates) -> ProtectedDatasetBinding`: canonical UUIDv4와 초기 lifecycle을 만들고, update 후 `ProtectedDatasetBinding.model_validate()`로 재검증한다.
- `_register_dataset_command(**updates) -> RegisterDatasetCommand`와 `_transition_command(...) -> TransitionDatasetCommand`: 새 UUIDv4 request ID를 기본값으로 사용한다.
- `_control_service(database, approval_source) -> PostgresqlProtectedAuthorizationControlService`: C2-a factory의 control login/role 구성을 재사용한다.
- `_owner_read_dataset_row(database, dataset_id, dataset_version)`: owner connection에서 JSON과 모든 lifecycle 컬럼을 반환한다.
- `_registered_dataset_service(database) -> tuple[PostgresqlProtectedAuthorizationControlService, ProtectedDatasetBinding]`: Custodian CONTROL identity를 준비하고 Dataset을 service로 등록한 뒤 등록 결과를 조회해 반환한다.
- `_transition_to_frozen_command() -> TransitionDatasetCommand`: 독립 preflight test용 canonical command를 만든다.

helper는 SQL mutation을 숨기지 않는다. owner SQL은 검증 조회와 fixture 준비에만 쓰고, 성공 경로 등록·전이는 반드시 service method를 통과시킨다.

```python
async def test_custodian_registers_dataset_and_new_session_reads_initial_state(
    protected_database: _ProtectedDatabase,
) -> None:
    service = _control_service(protected_database, _DatasetApprovalSource())
    command = _register_dataset_command()
    result = await service.register_dataset(command)
    assert result.reason_code == "DATASET_REGISTERED"
    assert result.effective_revision == 1
    assert result.authorization_audit_event_id is None
    persisted = await _owner_read_dataset_row(protected_database, command.dataset_id, command.dataset_version)
    assert persisted.state == "ACCESS_AUTHORIZED"
    assert persisted.state_revision == 1
    assert persisted.binding == command.binding.model_dump(mode="json")


async def test_custodian_transitions_only_the_approved_dataset_dag(
    protected_database: _ProtectedDatabase,
) -> None:
    service, registered = await _registered_dataset_service(protected_database)
    authoring = await service.transition_dataset(
        _transition_command(
            registered,
            from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
            to_state=ProtectedDatasetState.AUTHORING,
            revision=1,
            authored_count=0,
            review_complete=False,
        )
    )
    assert authoring.effective_revision == 2
    review_ready = await service.transition_dataset(
        _transition_command(
            registered,
            from_state=ProtectedDatasetState.AUTHORING,
            to_state=ProtectedDatasetState.REVIEW_READY,
            revision=2,
            authored_count=40,
            review_complete=True,
        )
    )
    assert review_ready.effective_revision == 3
```

별도 tests는 non-Custodian `ISSUER_ROLE_DENIED`, global Author/Custodian overlap `SELF_APPROVAL_DENIED`, duplicate Dataset `CONTROL_COMMAND_CONFLICT`, `AUTHORING -> REVIEW_READY` count 0, wrong from/revision, unsupported DAG을 각각 검증한다.

- [ ] **Step 2: FROZEN preflight가 DB를 사용하지 않는 테스트를 작성한다**

```python
async def test_transition_to_frozen_is_rejected_before_database_access() -> None:
    service = PostgresqlProtectedAuthorizationControlService(
        _FailIfConnectedEngine(),
        schema="synthetic_schema",
        data_access_role="synthetic_data_role",
        control_role="synthetic_control_role",
        approval_source=_MissingApprovalSource(),
    )
    with pytest.raises(ProtectedSecurityError) as captured:
        await service.transition_dataset(_transition_to_frozen_command())
    assert captured.value.reason_code == "ROLE_ACTION_STATE_DENIED"
```

`_FailIfConnectedEngine` 대신 기존 mock/monkeypatch 관례가 있으면 session factory 호출 count를 0으로 단언한다. 이 test에서 DENIED audit를 기대하지 않는다.

- [ ] **Step 3: 실패를 확인한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/integration/rag/test_protected_retrieval_control_postgresql.py -k "registers_dataset or transitions_only or transition_to_frozen or author_custodian" -q
```

Expected: missing methods/helpers FAIL.

- [ ] **Step 4: executor/Author identity lock helper를 구현한다**

`_ControlSession`에 다음 의미의 method를 추가한다.

```python
async def lock_dataset_executor(self, expected: _ControlExecutor) -> _ControlExecutor:
    result = await self._execute(
        f"""
        SELECT database_login, actor_id, actor_namespace, identity_plane,
               principal_role, approval_role, enabled
        FROM {self._schema}.protected_identity
        WHERE database_login = :database_login
           OR (actor_id = :actor_id AND actor_namespace = :actor_namespace)
        ORDER BY database_login
        FOR UPDATE
        """,
        {
            "database_login": expected.database_login,
            "actor_id": expected.actor.actor_id,
            "actor_namespace": expected.actor.namespace,
        },
    )
    rows = tuple(result)
    executor_rows = tuple(row for row in rows if row.database_login == expected.database_login)
    if len(executor_rows) != 1:
        raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
    if any(
        row.enabled
        and row.identity_plane == "DATA"
        and row.principal_role == ProtectedPrincipalRole.HOLDOUT_AUTHOR.value
        for row in rows
    ):
        raise ProtectedSecurityError("SELF_APPROVAL_DENIED")
    locked = await self.resolve_executor()
    if locked != expected:
        raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
    if locked.principal.role is not ProtectedApprovalRole.DATASET_CUSTODIAN:
        raise ProtectedSecurityError("ISSUER_ROLE_DENIED")
    return locked
```

query는 identity lock을 `database_login` 오름차순으로 획득한다. error는 caller에 row 존재 여부나 role 세부를 노출하지 않는다.

- [ ] **Step 5: `register_dataset()`를 구현한다**

existing C2-a method의 prepare executor -> early replay -> mutation transaction -> locked replay -> mutation -> CONTROL audit -> result pattern을 유지한다. insert는 다음 SQL 형태로 unique race가 transaction을 abort하지 않게 한다.

```sql
INSERT INTO protected_schema.protected_dataset (
    dataset_id, dataset_version, binding, manifest_sha256,
    protected_artifact_sha256, hmac_key_version, state,
    state_revision, authored_count, review_complete, lock_marker
) VALUES (
    :dataset_id, :dataset_version, CAST(:binding AS jsonb), :manifest_sha256,
    :protected_artifact_sha256, :hmac_key_version, 'ACCESS_AUTHORIZED',
    1, 0, false, 0
)
ON CONFLICT (dataset_id, dataset_version) DO NOTHING
RETURNING state_revision
```

insert result가 없으면 locked replay를 확인하고 exact replay가 아니면 `CONTROL_COMMAND_CONFLICT` DENIED audit를 commit한다. success는 target `UUID:SemVer`, revision 1, `DATASET_REGISTERED`, authorization audit ref `None`을 반환한다.

- [ ] **Step 6: `transition_dataset()`를 구현한다**

method 첫 분기에 다음 preflight를 둔다.

```python
if command.to_state is ProtectedDatasetState.FROZEN:
    raise ProtectedSecurityError("ROLE_ACTION_STATE_DENIED")
```

허용 DAG은 하나의 불변 map으로 검증한다.

```python
_DATASET_TRANSITIONS = {
    (ProtectedDatasetState.ACCESS_AUTHORIZED, ProtectedDatasetState.AUTHORING),
    (ProtectedDatasetState.AUTHORING, ProtectedDatasetState.REVIEW_READY),
    (ProtectedDatasetState.REVIEW_READY, ProtectedDatasetState.AUTHORING),
}
```

Dataset row를 `FOR UPDATE`로 잠근 후 current state/revision, DAG, REVIEW_READY 진입 count `> 0`을 확인한다. UPDATE는 `state`, `state_revision=expected+1`, `authored_count`, `review_complete`만 SET하고 `WHERE state=:from_state AND state_revision=:expected` CAS가 한 행을 반환해야 한다. success CONTROL audit가 같은 transaction에서 실패하면 UPDATE도 rollback된다.

- [ ] **Step 7: register/transition 통합 테스트를 통과시킨다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/integration/rag/test_protected_retrieval_control_postgresql.py -k "dataset and not freeze" -q
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest ai_worker/tests/evaluation/test_protected_retrieval_control.py -q
```

Expected: PASS.

- [ ] **Step 8: lifecycle service slice를 커밋한다**

```bash
git add ai_worker/adapters/postgresql_protected_retrieval_control.py tests/integration/rag/test_protected_retrieval_control_postgresql.py
git commit -m "✨ feat: #513 Dataset 등록과 일반 전이 구현"
```

---

### Task 4: FREEZE evidence 결속과 원자적 receipt

**Files:**
- Modify: `ai_worker/adapters/postgresql_protected_retrieval_control.py`
- Modify: `tests/integration/rag/test_protected_retrieval_control_postgresql.py`

**Interfaces:**
- Consumes: `FreezeDatasetCommand`, `FreezeApprovalSourceEvidence`, `verify_freeze_approval()`, Task 2 Dataset assembly, Task 3 identity/Dataset locks
- Produces: `freeze_dataset()` and atomic `REVIEW_READY -> FROZEN + DATASET_FROZEN CONTROL audit`

- [ ] **Step 1: FREEZE matrix 실패 통합 테스트를 작성한다**

Task 3 helpers에 이어 다음 FREEZE fixture helpers를 먼저 구현한다.

- `_freeze_evidence(dataset, **updates) -> FreezeApprovalSourceEvidence`: 독립 Product Safety Reviewer, Dataset exact binding, count 40/review true/4축 0, canonical UUIDv4 source ID와 raw hash를 기본값으로 만들고 update 후 strict 재검증한다.
- `_freeze_command(dataset, evidence, **updates) -> FreezeDatasetCommand`: Dataset/revision/source ID/raw hash를 exact binding하고 canonical request ID를 만든다.
- `_review_ready_dataset_service(database, dataset_updates) -> tuple[PostgresqlProtectedAuthorizationControlService, ProtectedDatasetBinding]`: service의 register/transition 경로로 REVIEW_READY를 만든 뒤 해당 negative case에 필요한 lifecycle만 owner fixture SQL로 변조한다.
- `_freezable_dataset_service(database) -> tuple[PostgresqlProtectedAuthorizationControlService, ProtectedDatasetBinding, _DatasetApprovalSource]`: exact FREEZE evidence를 가진 typed source까지 반환한다.
- `_load_dataset_in_new_data_session(database, dataset) -> ProtectedDatasetBinding`: 새 data-plane session/ledger를 열어 Task 2 조립 경로로 조회한다.

fixture helper의 owner 변조는 negative precondition을 만들기 위한 테스트 전용 동작에 한정하고, success lifecycle은 모두 service를 통과시킨다.

```python
@pytest.mark.parametrize(
    ("dataset_updates", "expected_reason"),
    [
        ({"authored_count": 39}, "FREEZE_EVIDENCE_INCOMPLETE"),
        ({"review_complete": False}, "FREEZE_EVIDENCE_INCOMPLETE"),
        ({"leakage_axis_intersections": (1, 0, 0, 0)}, "FREEZE_EVIDENCE_INCOMPLETE"),
        ({"leakage_axis_intersections": (0, 1, 0, 0)}, "FREEZE_EVIDENCE_INCOMPLETE"),
        ({"leakage_axis_intersections": (0, 0, 1, 0)}, "FREEZE_EVIDENCE_INCOMPLETE"),
        ({"leakage_axis_intersections": (0, 0, 0, 1)}, "FREEZE_EVIDENCE_INCOMPLETE"),
        ({"state": ProtectedDatasetState.AUTHORING}, "DATASET_STATE_MISMATCH"),
    ],
)
async def test_freeze_rejects_incomplete_dataset_evidence(
    protected_database: _ProtectedDatabase,
    dataset_updates: dict[str, object],
    expected_reason: str,
) -> None:
    service, dataset = await _review_ready_dataset_service(protected_database, dataset_updates)
    with pytest.raises(ProtectedSecurityError) as captured:
        await service.freeze_dataset(_freeze_command(dataset))
    assert captured.value.reason_code == expected_reason
    persisted = await _owner_read_dataset_row(protected_database, dataset.dataset_id, dataset.dataset_version)
    assert persisted.state != "FROZEN"
```

evidence mutation matrix는 source ID, raw hash, action, issuer role, issuer actor=executor, executor participant, issuer participant, Dataset ID/version, manifest hash, artifact hash, count/review/leakage를 각각 한 개씩 변경해 고정 error와 mutation 0을 단언한다.

- [ ] **Step 2: FREEZE success·receipt·replay 실패 테스트를 작성한다**

```python
async def test_freeze_commits_lifecycle_and_audit_receipt_atomically(
    protected_database: _ProtectedDatabase,
) -> None:
    service, dataset, source = await _freezable_dataset_service(protected_database)
    command = _freeze_command(dataset, source.evidence)
    result = await service.freeze_dataset(command)
    assert result.reason_code == "DATASET_FROZEN"
    assert result.effective_revision == dataset.state_revision + 1
    assert result.authorization_audit_event_id is None
    replay = await service.freeze_dataset(command)
    assert replay == result
    loaded = await _load_dataset_in_new_data_session(protected_database, dataset)
    assert loaded.state is ProtectedDatasetState.FROZEN
    assert loaded.freeze_receipt_ref == OpaqueLogicalRef(
        namespace=OpaqueRefNamespace.AUDIT_EVENT,
        value=command.request_id,
    )
    assert source.freeze_calls == [command.approval_source_event_id]
```

replay에서 trusted source call이 추가로 발생하지 않는지 확인한다. changed command with same request ID는 `CONTROL_COMMAND_CONFLICT`를 반환한다.

- [ ] **Step 3: 실패를 확인한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/integration/rag/test_protected_retrieval_control_postgresql.py -k "freeze" -q
```

Expected: missing `freeze_dataset()` or missing atomic receipt behavior FAIL.

- [ ] **Step 4: trusted FREEZE source fetch를 구현한다**

service private method은 connector exception을 노출하지 않는다.

```python
async def _fetch_freeze_approval(
    self,
    command: FreezeDatasetCommand,
) -> tuple[FreezeApprovalSourceEvidence | None, ProtectedAuditReason | None]:
    try:
        evidence = await self._approval_source.fetch_freeze(command.approval_source_event_id)
    except ApprovalSourceNotFoundError:
        return None, ProtectedAuditReason.APPROVAL_NOT_VERIFIED
    except Exception:
        raise ProtectedSecurityError("INTERNAL_ERROR") from None
    if (
        evidence.source_event_id != command.approval_source_event_id
        or evidence.canonical_raw_sha256 != command.expected_raw_sha256
    ):
        return evidence, ProtectedAuditReason.APPROVAL_EVIDENCE_MISMATCH
    return evidence, None
```

exact replay를 먼저 확인한 뒤에만 이 method를 호출한다. non-Custodian/Author overlap은 connector 호출 0을 테스트한다.

- [ ] **Step 5: `freeze_dataset()`를 구현한다**

flow는 다음 순서를 고정한다.

```text
authenticate executor
read exact replay/conflict
verify Custodian and global Author separation
fetch typed trusted evidence
begin mutation transaction
lock/revalidate identity rows
lock Dataset row
lock/revalidate audit replay
assemble authoritative REVIEW_READY Dataset
validate 40/review/leakage
verify exact evidence and independence
CAS UPDATE state/revision
append DATASET_FROZEN CONTROL audit with event_id=request_id
commit
```

CAS SQL은 lifecycle 중 `state`와 `state_revision`만 바꾸고 count/review는 보존한다.

```sql
UPDATE protected_schema.protected_dataset
SET state = 'FROZEN', state_revision = :new_revision
WHERE dataset_id = :dataset_id
  AND dataset_version = :dataset_version
  AND state = 'REVIEW_READY'
  AND state_revision = :expected_revision
RETURNING state_revision
```

row가 없으면 `DATASET_STATE_MISMATCH`다. success audit은 target `UUID:SemVer`, revision `new_revision`, reason `DATASET_FROZEN`, authorization audit ref `None`을 사용한다.

- [ ] **Step 6: audit 실패 rollback을 검증한다**

existing audit-head corruption/monkeypatch pattern을 사용해 CONTROL append 또는 head CAS가 `AUDIT_CAS_CONFLICT`로 실패하게 한다. 새 owner session으로 Dataset이 여전히 `REVIEW_READY`, original revision이고 request audit row가 0개임을 확인한다.

- [ ] **Step 7: FREEZE 통합 테스트를 통과시킨다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/integration/rag/test_protected_retrieval_control_postgresql.py -k "freeze" -q
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/integration/rag/test_protected_retrieval_postgresql.py -q
```

Expected: PASS.

- [ ] **Step 8: FREEZE slice를 커밋한다**

```bash
git add ai_worker/adapters/postgresql_protected_retrieval_control.py tests/integration/rag/test_protected_retrieval_control_postgresql.py
git commit -m "✨ feat: #513 Dataset FREEZE receipt 결속"
```

---

### Task 5: Dataset 최소 컬럼 권한

**Files:**
- Modify: `infra/python/protected_retrieval_role_policy.py`
- Modify: `tests/migration/test_protected_retrieval_migration.py`

**Interfaces:**
- Consumes: existing `_INSERT_COLUMNS`, `_UPDATE_COLUMNS`, exact connection privilege validator
- Produces: control Dataset INSERT/lifecycle UPDATE positive policy and immutable/data-plane negative policy

- [ ] **Step 1: positive/negative 권한 실패 테스트를 재편한다**

`test_limited_logins_have_plane_specific_column_privileges()`의 control positive transaction에 정상 Dataset INSERT와 lifecycle UPDATE를 추가한다.

```sql
INSERT INTO protected_schema.protected_dataset (
    dataset_id, dataset_version, binding, manifest_sha256,
    protected_artifact_sha256, hmac_key_version, state,
    state_revision, authored_count, review_complete, lock_marker
) VALUES (
    :dataset_id, '1.0.0', CAST(:binding AS jsonb), :manifest_sha256,
    :protected_artifact_sha256, 'synthetic-key-v1',
    'ACCESS_AUTHORIZED', 1, 0, false, 0
)
```

```sql
UPDATE protected_schema.protected_dataset
SET state = 'AUTHORING', state_revision = 2,
    authored_count = 1, review_complete = false
WHERE dataset_id = :dataset_id AND dataset_version = '1.0.0'
```

positive transaction은 rollback해 fixture state를 남기지 않는다.

- [ ] **Step 2: Dataset immutable negative matrix를 고정한다**

`control_forbidden_statements`에 다음 각 컬럼 UPDATE를 독립 statement로 유지한다.

```text
UPDATE protected_dataset SET dataset_id = '<synthetic uuid>'
UPDATE protected_dataset SET dataset_version = '2.0.0'
UPDATE protected_dataset SET binding = '{}'::jsonb
UPDATE protected_dataset SET manifest_sha256 = '<64 lowercase a>'
UPDATE protected_dataset SET protected_artifact_sha256 = '<64 lowercase b>'
UPDATE protected_dataset SET hmac_key_version = 'forged-key-version'
```

기존 identity immutable 컬럼, artifact envelope, operation capability, OPERATION audit, DELETE/TRUNCATE/CREATE 거부를 제거하지 않는다. data login의 Dataset lifecycle UPDATE 거부도 유지한다.

- [ ] **Step 3: 실패를 확인한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/migration/test_protected_retrieval_migration.py -q
```

Expected: control Dataset INSERT/UPDATE positive statement permission denied FAIL.

- [ ] **Step 4: role policy를 최소 확장한다**

```python
_INSERT_COLUMNS["control"]["protected_dataset"] = (
    "dataset_id",
    "dataset_version",
    "binding",
    "manifest_sha256",
    "protected_artifact_sha256",
    "hmac_key_version",
    "state",
    "state_revision",
    "authored_count",
    "review_complete",
    "lock_marker",
)

_UPDATE_COLUMNS["control"]["protected_dataset"] = (
    "state",
    "state_revision",
    "authored_count",
    "review_complete",
    "lock_marker",
)
```

실제 구현은 dict literal의 기존 control 섹션에 두 tuple을 넣는다. 테이블 전체 privilege를 주지 않고 connection validator의 exact expected policy를 그대로 사용한다.

- [ ] **Step 5: migration/privilege 테스트를 통과시킨다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/migration/test_protected_retrieval_migration.py -q
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run ruff check infra/python/protected_retrieval_role_policy.py tests/migration/test_protected_retrieval_migration.py
```

Expected: PASS, skip 0. protected PostgreSQL environment 미준비로 skip되면 검증 미완료로 보고한다.

- [ ] **Step 6: privilege slice를 커밋한다**

```bash
git add infra/python/protected_retrieval_role_policy.py tests/migration/test_protected_retrieval_migration.py
git commit -m "🔒 feat: #513 Dataset lifecycle 최소 권한 확장"
```

---

### Task 6: 동시성·rollback·replay 보안 회귀

**Files:**
- Modify: `tests/integration/rag/test_protected_retrieval_control_postgresql.py`
- Modify: `ai_worker/adapters/postgresql_protected_retrieval_control.py` only if a failing regression proves a production change is required

**Interfaces:**
- Consumes: completed register/transition/freeze methods and existing PostgreSQL fixture lock helpers
- Produces: one-effect concurrency, fresh-clock audit, no partial mutation, exact replay evidence

- [ ] **Step 1: concurrent identical register test를 작성한다**

```python
async def test_concurrent_identical_dataset_register_converges_to_one_effect(
    protected_database: _ProtectedDatabase,
) -> None:
    first = _control_service(protected_database, _DatasetApprovalSource())
    second = _control_service(protected_database, _DatasetApprovalSource())
    command = _register_dataset_command()
    first_result, second_result = await asyncio.gather(
        first.register_dataset(command),
        second.register_dataset(command),
    )
    assert first_result == second_result
    assert await _dataset_row_count(protected_database, command) == 1
    assert await _request_control_audit_count(protected_database, command.request_id) == 1
```

- [ ] **Step 2: concurrent competing transition/FREEZE test를 작성한다**

같은 Dataset/revision에 다른 request ID로 두 transition을 `asyncio.gather(..., return_exceptions=True)`로 실행해 success 1개와 `DATASET_STATE_MISMATCH` 1개를 확인한다. FREEZE도 같은 패턴으로 FROZEN success 1개, receipt 1개, success audit 1개만 허용한다.

- [ ] **Step 3: lock 대기 후 clock refresh test를 작성한다**

owner connection으로 Dataset row를 `FOR UPDATE`해 service command를 대기시킨 뒤 lock을 해제한다. CONTROL audit `recorded_at`이 command 시작 전 시각이 아니라 lock 해제 후 DB clock임을 단언한다. wall-clock sleep 대신 기존 event/transaction 동기화 패턴을 사용한다.

- [ ] **Step 4: policy denial과 internal failure을 분리한다**

- wrong state/revision/evidence/role: DENIED CONTROL entry 1개, Dataset mutation 0
- audit unavailable/hash mismatch/DB exception: request DENIED entry 0, Dataset mutation 0
- exact denied replay: 같은 fixed error, 추가 audit 0
- same request ID changed payload: `CONTROL_COMMAND_CONFLICT`, 추가 mutation 0

각 행동을 독립 test로 작성해 이전 transaction의 rollback에 의존하지 않게 한다.

- [ ] **Step 5: 회귀 실패를 확인하고 최소 수정한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/integration/rag/test_protected_retrieval_control_postgresql.py -q
```

Expected: 추가 tests가 초기에 구현 결함을 노출하면 FAIL. 어댑터를 수정할 때는 나타난 race/rollback/replay 원인에 필요한 최소 변경만 한다.

- [ ] **Step 6: 전체 protected 통합 회귀를 통과시킨다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/integration/rag/test_protected_retrieval_control_postgresql.py tests/integration/rag/test_protected_retrieval_postgresql.py tests/migration/test_protected_retrieval_migration.py -q
```

Expected: PASS, skip 0.

- [ ] **Step 7: concurrency/security regression slice를 커밋한다**

```bash
git add tests/integration/rag/test_protected_retrieval_control_postgresql.py ai_worker/adapters/postgresql_protected_retrieval_control.py
git commit -m "✅ test: #513 Dataset control 동시성과 rollback 고정"
```

adapter가 변경되지 않았다면 실제 변경된 test 파일만 stage한다.

---

### Task 7: 계약 상태·validation 증거·전체 검증

**Files:**
- Modify: `docs/contracts/targets/post-mvp-1/protected-retrieval-infrastructure-v1.md`
- Modify: `docs/validation/rag/issue-273/protected-runner-infrastructure-adapter.md`
- Modify: `docs/validation/rag/issue-273/protected-runner-infrastructure-adapter.json`
- Modify: other `docs/validation/rag/issue-273/` generated projections only when their existing generator proves they are derived outputs
- Modify: `scripts/verify_protected_runner_evidence.py` only when current exact projection checks require C2-b status fields

**Interfaces:**
- Consumes: verified C2-b code/test results
- Produces: target contract status aligned with implementation while effective enforcement/HOLDOUT/production remain blocked

- [ ] **Step 1: status source와 generated projection 경계를 확인한다**

Run:

```bash
rg -n "C2-b|Dataset lifecycle|FREEZE|NOT_IMPLEMENTED|HOLDOUT|unprovisioned" docs/contracts/targets/post-mvp-1/protected-retrieval-infrastructure-v1.md docs/validation/rag/issue-273 scripts/verify_protected_runner_evidence.py ai_worker/tasks/evaluation/natural_language_retrieval_validation.py
```

Expected: C2-b만 implemented로 바꾸고 production connector/protected environment/effective enforcement를 열지 않아야 할 정확한 projection 필드가 확인된다.

- [ ] **Step 2: target contract을 현재 구현 상태와 정렬한다**

다음 의미를 문서에 반영한다.

```text
C2-a Identity control-plane: implemented only if #512 is merged
C2-b Dataset lifecycle/FREEZE control-plane: implemented by #513
Freeze receipt: successful CONTROL audit event reference
Effective enforcement: NOT_IMPLEMENTED
HOLDOUT authorization/authored/run: NOT_RECORDED / 0 / blocked
Production approval source and protected environment: unprovisioned
Contract location/status: targets, not current
```

Decision metadata를 수정해야 한다면 Issue `#513`의 지정 리뷰어 확인 범위에서만 별도 문서 변경으로 수행한다. 승인 state를 코드가 임의로 추정하지 않는다.

- [ ] **Step 3: validation projection을 정본 generator로 갱신한다**

repository의 기존 `scripts/verify_protected_runner_evidence.py`/evaluation validation command를 사용해 JSON·Markdown를 갱신한다. 손으로 hash를 계산해 복사하지 않는다. fresh generation을 두 번 실행해 byte equality를 확인한다.

- [ ] **Step 4: focused test suite를 실행한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest ai_worker/tests/evaluation/test_protected_retrieval_control.py tests/integration/rag/test_protected_retrieval_control_postgresql.py tests/integration/rag/test_protected_retrieval_postgresql.py tests/migration/test_protected_retrieval_migration.py -q
```

Expected: PASS, skip 0.

- [ ] **Step 5: static checks를 실행한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/adapters ai_worker/tests/evaluation tests/integration/rag tests/migration infra/python
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/adapters ai_worker/tests/evaluation tests/integration/rag tests/migration infra/python --check
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run mypy ai_worker/tasks/evaluation ai_worker/adapters ai_worker/core/runtime_assembly.py
git diff --check
```

Expected: PASS.

- [ ] **Step 6: repository 필수 검사를 실행한다**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run ruff check .
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run ruff format . --check
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run mypy backend/app ai_worker
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache bash scripts/ci/run_test.sh
```

Expected: PASS. 실패·skip·환경 미준비를 숨기지 않고 정확한 command/result를 PR에 기록한다.

- [ ] **Step 7: 최종 diff와 보안 경계를 검토한다**

Run:

```bash
git status --short
git diff --check
git diff --stat develop...HEAD
git diff develop...HEAD -- ai_worker/tasks/evaluation/protected_retrieval.py ai_worker/tasks/evaluation/protected_retrieval_control.py ai_worker/adapters/postgresql_protected_retrieval.py ai_worker/adapters/postgresql_protected_retrieval_control.py infra/python/protected_retrieval_role_policy.py tests/migration/test_protected_retrieval_migration.py
```

Review checklist:

- `binding` UPDATE/grant 없음
- schema/migration 없음
- FROZEN preflight DB access 없음
- Dataset mutation + CONTROL audit 동일 transaction
- FROZEN read의 verified receipt 필수
- immutable Dataset 컬럼 negative test 유지
- C1/C2-a replay/audit regression 없음
- synthetic data only
- effective enforcement/publication 차단 유지

- [ ] **Step 8: status/evidence slice를 커밋한다**

```bash
git add docs/contracts/targets/post-mvp-1/protected-retrieval-infrastructure-v1.md docs/validation/rag/issue-273 scripts/verify_protected_runner_evidence.py ai_worker/tasks/evaluation/natural_language_retrieval_validation.py ai_worker/tests/evaluation/test_natural_language_retrieval_validation_report.py
git commit -m "📝 docs: #513 Dataset control 구현 증거 정렬"
```

나열된 경로 중 실제로 변경된 파일만 stage한다. 변경되지 않은 generated file을 억지로 재생성하지 않는다.

---

## Antigravity Gemini 실행 완료 보고 형식

Antigravity Gemini는 구현 완료 시 다음을 모두 보고한다.

```text
Baseline: C2-a merged commit / C2-b base commit
Changed files: exact paths and responsibility
Behavior: register / transition / freeze / receipt assembly
Security: immutable-column negative tests and role separation
Focused tests: command, result, pass/fail/skip counts
Full checks: Ruff, format, Mypy, CI test runner
Remaining gates: designated reviewer approval, Decision metadata, effective enforcement disabled
```

지정 리뷰어 `@hazelnutflavoured`의 승인 전에 병합하지 않는다. 설계의 actor-level Author/Custodian 겸직 금지를 리뷰어가 인수하지 않으면 구현을 더 늘리지 말고 계약/schema 재결정으로 반환한다.
