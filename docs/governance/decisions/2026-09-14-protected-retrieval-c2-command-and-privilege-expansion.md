# Product Decision Candidate: Protected Retrieval C2 Control-Plane Command 계약과 최소 권한 확장

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-368-R2` |
| 상태 | Candidate · Coordination Confirmed · PR Review Required |
| 조율 확인 | 구현 담당자가 2026-09-14 현재 작업에서 영향 도메인 협의 완료를 확인 |
| 선행 Decision | `PD-368-20260909`, `PD-368-R1` |
| 구현 | 정현우 (`@ceohwj`) |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — 단일 책임 리뷰어 (Product·Privacy·Safety·Evaluation, command 계약, 승인자 분리 및 최소 권한 타당성) |
| 추적 Issue | [#490](https://github.com/AI-HealthCare-05/AH_05_04/issues/490), [#368](https://github.com/AI-HealthCare-05/AH_05_04/issues/368) |
| 관련 PR | PR #432, PR #463 (C1 구현), PR #498 |

이 문서는 #368의 남은 control-plane 구현 범위(C2)인 identity 등록·비활성화와 Dataset 등록·상태 전이·FREEZE에
대해 command 계약, 승인자 분리 규칙, 최소 권한 확장 범위와 negative test 보존 기준을 확정한다.
영향 도메인 협의 완료는 구현 착수 근거이며, 이 문서의 승인 증거는 지정 책임 리뷰어 권가빈(`@hazelnutflavoured`)의
단일 GitHub PR review event다. 이 문서만으로 protected 환경 활성화, HOLDOUT 접근·실행, 외부 승인, Current 승격
또는 Production 공개가 승인되지 않는다.

## 1. 범위

포함:

- identity 등록(`RegisterIdentityCommand`)과 비활성화(`DisableIdentityCommand`) 계약
- Dataset 등록(`RegisterDatasetCommand`), 라이프사이클 일반 전이(`TransitionDatasetCommand`), 불변 고정(`FreezeDatasetCommand`) 계약
- FREEZE 전용 전이 분리와 기존 kernel/PD-368 조건(40건, 전수 검토, 4축 0 누출, 승인 evidence 결속, freeze receipt 생성)의 계약 고정
- C2 `ControlCommandResult`, `ControlCommandAuditEntry`, `TrustedApprovalSource` 및 `FreezeApprovalSourceEvidence` DTO 확장 형상 명시
- 성공 결과 코드 및 거부 사유 매핑 (`ProtectedSecurityError` allowlist 범위 내)
- 승인자 분리 규칙 및 FREEZE 독립 Custodian 승인 주체 확정
- Control role의 `protected_identity`, `protected_dataset` 최소 권한 확장 컬럼 목록과 근거
- **확장하지 않고 거부(FORBIDDEN)를 유지할 컬럼 및 negative test 보존 기준**
- Python 계층에서의 Dataset State 전이 DAG 강제 원칙
- CONTROL 감사 대상(`ControlAuditTargetKind`) 확장, 전역 락 순서(Lock Hierarchy), 멱등성 및 충돌 규칙
- C2 구현 Issue(C2-a identity, C2-b dataset/FREEZE) 분리 및 회귀 검증 지침

제외:

- 실제 Application Service 구현 코드 작성 (Decision 확정 후 별도 Issue)
- production trusted approval source connector 및 protected 환경 provisioning (`EXT-PRIV-001` 외부 게이트)
- HOLDOUT 작성·열람·실행, 보존·폐기 (#425)

## 2. Command 계약, 결과 및 감사 DTO 확장

모든 `request_id`, `dataset_id`, `approval_source_event_id`는 canonical lowercase UUIDv4 문자열이다. digest는 lowercase SHA-256 hex 문자열(64자리)이다.

### 2.1 Command DTO 정의

| Command | 필드 | 검증 규칙 |
| --- | --- | --- |
| `RegisterIdentityCommand` | `request_id`, `database_login`, `actor_id`, `actor_namespace`, `identity_plane`, `principal_role`, `approval_role`, `enabled` | `database_login`은 PostgreSQL 식별자 형식(최대 63자). `identity_plane=DATA`이면 `principal_role` 필수·`approval_role` 금지; `identity_plane=CONTROL`이면 `approval_role` 필수·`principal_role` 금지. 초기 `enabled`는 true. |
| `DisableIdentityCommand` | `request_id`, `database_login`, `expected_actor_id`, `expected_actor_namespace` | 대상 `database_login`이 존재하고 현재 `enabled=true`이며 actor 식별자가 일치해야 함. |
| `RegisterDatasetCommand` | `request_id`, `dataset_id`, `dataset_version`, `binding`, `manifest_sha256`, `protected_artifact_sha256`, `hmac_key_version` | 초기 상태는 항상 `state=ACCESS_AUTHORIZED`, `state_revision=1`, `authored_count=0`, `review_complete=false`로 고정 삽입. |
| `TransitionDatasetCommand` | `request_id`, `dataset_id`, `dataset_version`, `from_state`, `to_state`, `expected_state_revision`, `authored_count`, `review_complete` | 일반 상태 전이 전용(§2.2). **`to_state=FROZEN` 목적지는 엄격히 금지**되며 호출 시 `ROLE_ACTION_STATE_DENIED`로 즉시 거부됨. `expected_state_revision`이 현재 DB 값과 정확히 일치해야 함. |
| `FreezeDatasetCommand` | `request_id`, `dataset_id`, `dataset_version`, `expected_state_revision`, `approval_source_event_id`, `expected_raw_sha256` | **FREEZE 전용 전이**. 현재 상태가 `REVIEW_READY`이고, `authored_count == 40`, `review_complete == true`, 네 leakage 축 모두 0(`(0, 0, 0, 0)`)이어야 함(§2.2). `TrustedApprovalSource`의 `FreezeApprovalSourceEvidence`와 결속 검증 후 최종 상태를 `FROZEN`으로 전이하고 `freeze_receipt_ref`를 생성·보존함. |

`ControlCommandKind`에 다음 열거형 값을 추가한다:
- `REGISTER_IDENTITY = "REGISTER_IDENTITY"`
- `DISABLE_IDENTITY = "DISABLE_IDENTITY"`
- `REGISTER_DATASET = "REGISTER_DATASET"`
- `TRANSITION_DATASET = "TRANSITION_DATASET"`
- `FREEZE_DATASET = "FREEZE_DATASET"`

### 2.2 FREEZE 전용 전이 분리와 불변 조건

Dataset 라이프사이클에서 `FROZEN` 상태는 평가 런타임이 참조하는 기준선의 불변 잠금 상태이므로, 일반 상태 전이와 완전히 격리한다:

1. **일반 전이(`TransitionDatasetCommand`)에서의 FREEZE 우회 차단**:
   - `TransitionDatasetCommand`는 오직 다음 전이만 허용한다:
     - `ACCESS_AUTHORIZED` → `AUTHORING`
     - `AUTHORING` → `REVIEW_READY` (조건: `authored_count > 0`)
     - `REVIEW_READY` → `AUTHORING` (리뷰 반려에 따른 재작성)
   - `TransitionDatasetCommand`에 `to_state=FROZEN`이 전달되면, Application Service는 어떠한 상태 검사나 DB 조작도 수행하지 않고 즉시 **`ROLE_ACTION_STATE_DENIED`**로 거부한다.

2. **`FreezeDatasetCommand`의 필수 불변 조건 (kernel `protected_retrieval.py:619` 및 `PD-368 §5` 정렬)**:
   `FreezeDatasetCommand`는 다음 모든 조건이 동시에 만족될 때만 상태를 `FROZEN`으로 전이한다:
   - **현재 상태**: `from_state == REVIEW_READY` (`DATASET_STATE_MISMATCH` 거부)
   - **문항 수**: **정확히 40건** (`authored_count == 40`, 39건 이하 시 `FREEZE_EVIDENCE_INCOMPLETE` 거부)
   - **전수 검토**: `review_complete == true` (미완료 시 `FREEZE_EVIDENCE_INCOMPLETE` 거부)
   - **정보 누출 부재**: **네 leakage 축 모두 0** (`leakage_axis_intersections == (0, 0, 0, 0)`, 비영 누출 시 `FREEZE_EVIDENCE_INCOMPLETE` 거부)
   - **승인 evidence 결속**: `approval_source_event_id`로 조회한 `FreezeApprovalSourceEvidence`가 `PRODUCT_SAFETY_REVIEWER`에 의해 `state=APPROVED`로 발행되었으며, Dataset ID, 버전, `manifest_sha256`, `protected_artifact_sha256`이 정확히 일치함
   - **Freeze Receipt 생성 및 보존**: FREEZE 전이 성공 트랜잭션에서 불변 `freeze_receipt_ref` (OpaqueLogicalRef)가 생성된다. 불변 식별자/해시 보호를 유지하기 위해 `binding` 전체 일괄 UPDATE는 금지하며, receipt 저장 및 어댑터의 `ProtectedDatasetBinding` 조회 조립 경계는 C2-b(§7 [WATCH])에서 확정하여 결속·영속화한다.

3. **후속 구현의 필수 거부 테스트 요건**:
   - `TransitionDatasetCommand`를 이용한 `to_state=FROZEN` 우회 시도 거부 (`ROLE_ACTION_STATE_DENIED`)
   - `authored_count=39` 이하 상태에서의 FREEZE 시도 거부 (`FREEZE_EVIDENCE_INCOMPLETE`)
   - `leakage_axis_intersections != (0, 0, 0, 0)` 상태에서의 FREEZE 시도 거부 (`FREEZE_EVIDENCE_INCOMPLETE`)
   - `review_complete=false` 상태에서의 FREEZE 시도 거부 (`FREEZE_EVIDENCE_INCOMPLETE`)
   - evidence 누락, 해시 불일치, 발행자 자격 부적격 거부

### 2.3 `ControlCommandResult` 확장 형상 및 검증 분기

`ControlCommandResult` DTO의 필드 규칙을 다음과 같이 C2 command별로 엄격히 고정한다:

| Command Kind | `target_id` 형식 | `effective_revision` | `authorization_audit_event_id` | `reason_code` |
| --- | --- | --- | --- | --- |
| `INGEST_APPROVAL` (C1) | `source_event_id` (str) | `None` | `None` | `APPROVAL_VERIFIED` |
| `GRANT`, `REVOKE`, `EXPIRE` (C1) | `grant_id` (UUIDv4) | `revision` (`int >= 1`) | UUIDv4 (str) | `AUTHORIZED`, `REVOKED`, `EXPIRED` |
| `REGISTER_IDENTITY` (C2) | `database_login` (pg identifier) | `None` | **`None`** | `IDENTITY_REGISTERED` |
| `DISABLE_IDENTITY` (C2) | `database_login` (pg identifier) | `None` | **`None`** | `IDENTITY_DISABLED` |
| `REGISTER_DATASET` (C2) | `f"{dataset_id}:{dataset_version}"` | `state_revision` (`int >= 1`) | **`None`** | `DATASET_REGISTERED` |
| `TRANSITION_DATASET` (C2) | `f"{dataset_id}:{dataset_version}"` | `state_revision` (`int >= 1`) | **`None`** | `DATASET_TRANSITIONED` |
| `FREEZE_DATASET` (C2) | `f"{dataset_id}:{dataset_version}"` | `state_revision` (`int >= 1`) | **`None`** | `DATASET_FROZEN` |

- C2 명령은 authorization grant 변이가 아니므로 **`authorization_audit_event_id`는 항상 `None`**이어야 한다. C1에서 요구하던 authorization mutation audit reference 제약은 C2 command에서는 적용되지 않도록 validator 분기를 구성한다.
- 거부는 결과 객체를 반환하지 않고 기존 `ProtectedSecurityError` allowlist 오류를 발생시킨다.

### 2.4 FREEZE 승인 원본 evidence DTO 및 원본 조회 포트

기존 C1의 `ApprovalSourceEvidence`는 grant 승인 전용(`approved_grant_payload_sha256`)이므로, C2 FREEZE 승인을 위한 전용 evidence DTO를 정의한다.

#### 2.4.1 `FreezeApprovalSourceEvidence` DTO 정의

```python
class FreezeApprovalSourceEvidence(StrictContractModel):
    source_event_id: str = Field(min_length=1, max_length=160)
    action: Literal[ProtectedAction.FREEZE]
    dataset_id: str = Field(min_length=1, max_length=160)
    dataset_version: str = Field(pattern=r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
    manifest_sha256: Sha256Hex
    protected_artifact_sha256: Sha256Hex
    authored_count: Literal[40]
    review_complete: Literal[True]
    leakage_axis_intersections: tuple[Literal[0], Literal[0], Literal[0], Literal[0]]
    issuer: ProtectedApprovalPrincipal  # role은 PRODUCT_SAFETY_REVIEWER 필수
    state: Literal["APPROVED"]
    recorded_at: datetime  # UTC-aware 필수
    target_commit_oid: str = Field(pattern=r"^[0-9a-f]{40}$")
    target_artifact_sha256: Sha256Hex
    canonical_raw_sha256: Sha256Hex
    implementation_participants: tuple[ActorIdentity, ...] = Field(min_length=1)
```

#### 2.4.2 `TrustedApprovalSource` 포트 확장

Application Service가 사용하는 `TrustedApprovalSource`는 단일 `fetch` 메서드에서 discriminated union을 반환하거나, typed fetch 메서드로 확장한다:

```python
class TrustedApprovalSource(Protocol):
    async def fetch(self, source_event_id: str) -> ApprovalSourceEvidence | FreezeApprovalSourceEvidence: ...
    # 또는:
    # async def fetch_authorization(self, source_event_id: str) -> ApprovalSourceEvidence: ...
    # async def fetch_freeze(self, source_event_id: str) -> FreezeApprovalSourceEvidence: ...
```

C1 evidence와 C2 FREEZE evidence는 명확히 구분되며, FREEZE 처리 시 `action is ProtectedAction.FREEZE`와 대상 Dataset 속성이 일치하지 않으면 `APPROVAL_EVIDENCE_MISMATCH`로 거부한다.

## 3. 승인자 분리 및 역할 경계

Application Service는 호출자가 주장하는 권한을 신뢰하지 않으며, 인증된 control `session_user`를 `protected_identity`에서 조회하여 엄격한 승인자 분리를 강제한다.

1. **Identity 등록 및 비활성화 (`RegisterIdentityCommand`, `DisableIdentityCommand`)**:
   - 실행 자격: `approval_role=PRODUCT_SAFETY_REVIEWER`만 실행 가능하다.
   - 자기 조작 방지 (`SELF_APPROVAL_DENIED`): 실행자는 자신의 `database_login`을 등록하거나 비활성화할 수 없다.
   - 구현자 분리: control-plane 구현 참여자(`@ceohwj`, `@phina-io`)의 identity 등록/비활성화 시 상호 검증을 거친다.

2. **Dataset 등록 및 상태 전이 (`RegisterDatasetCommand`, `TransitionDatasetCommand`)**:
   - 실행 자격: `approval_role=DATASET_CUSTODIAN`만 실행 가능하다.
   - 역할 분리: Dataset의 평가 문항 작성에 참여하는 Author identity는 해당 Dataset의 Custodian 전이 command를 실행할 수 없다.

3. **Dataset FREEZE (`FreezeDatasetCommand`)**:
   - 실행 자격: Independent `DATASET_CUSTODIAN`이 실행한다.
   - 승인 원본 결속: `TrustedApprovalSource`에서 읽은 evidence는 `PRODUCT_SAFETY_REVIEWER`가 발행한 `FREEZE` 승인(`FreezeApprovalSourceEvidence`)이어야 한다.
   - 독립 검토자 유지: 송은영(`@phina-io`)이 control-plane 또는 ACL 구현자로 참여하는 경우, independent Dataset Custodian 승인은 김지혜(`@Jye-rookie`)가 담당한다.

## 4. 최소 권한 확장 및 Negative Test 보존 원칙

Control role의 권한을 C2 요구사항에 맞추어 열되, 불변 속성 훼손 및 data-plane 경계 침범을 원천 차단하기 위해 컬럼 단위로 제한한다.

### 4.1 확장 허용 컬럼 (INSERT / UPDATE)

| 테이블 | 허용 작업 | 허용 컬럼 | 목적 및 근거 |
| --- | --- | --- | --- |
| `protected_identity` | `INSERT` | `database_login`, `actor_id`, `actor_namespace`, `identity_plane`, `principal_role`, `approval_role`, `enabled` | 신규 DATA 및 CONTROL identity 프로비저닝에 필수적임 |
| `protected_identity` | `UPDATE` | `enabled` | 퇴역 또는 자격 박탈 계정의 비활성화(`enabled=false`)에만 사용 |
| `protected_dataset` | `INSERT` | `dataset_id`, `dataset_version`, `binding`, `manifest_sha256`, `protected_artifact_sha256`, `hmac_key_version`, `state`, `state_revision`, `authored_count`, `review_complete`, `lock_marker` | 평가 Dataset의 초기 등록에 필수적임 |
| `protected_dataset` | `UPDATE` | `state`, `state_revision`, `authored_count`, `review_complete` (기존 `lock_marker` 유지) | 라이프사이클 전이 및 FREEZE 완료 상태 반영에 필수적임 |

### 4.2 확장하지 않고 거부(FORBIDDEN)를 유지할 컬럼 및 근거

| 테이블 | 차단 작업 | 차단 대상 컬럼 | 차단 유지 근거 |
| --- | --- | --- | --- |
| `protected_identity` | `UPDATE` | `database_login`, `actor_id`, `actor_namespace`, `identity_plane`, `principal_role`, `approval_role` | identity 식별자 및 역할의 불변성 보장. 권한 상승(privilege escalation) 공격 원천 방지 |
| `protected_dataset` | `UPDATE` | `dataset_id`, `dataset_version`, `binding`, `manifest_sha256`, `protected_artifact_sha256`, `hmac_key_version` | Dataset 불변 메타데이터 및 무결성 해시 변조 방지. 평가 데이터 변조 공격 원천 차단 |
| `protected_artifact` | `SELECT` / `INSERT` / `UPDATE` | 전 컬럼 (`envelope` 포함) | 보호 평가 데이터 envelope는 data plane의 Runner/Author 독점 영역이며, control plane은 메타데이터(해시)만 검증하고 평문/암호문 envelope에 일체 접근 불가 |
| `operation_capability` | `INSERT` / `UPDATE` | 전 컬럼 | 런타임 1회용 capability 발행 및 소비는 data plane 트랜잭션 독점 영역 |
| `audit_entry` | `INSERT` | `control_entry = false` (OPERATION 감사) | control role이 data plane의 OPERATION 감사를 위조하는 것 방지 |

### 4.3 Negative Test 보존 및 커버리지 보호 기준

`tests/migration/test_protected_retrieval_migration.py`의 `control_forbidden_statements`는 CI에서 control role의 권한 경계를 증명하는 핵심 보안 방어선이다.

1. **테스트 이동**: C2 구현 시 권한이 정상 부여되는 구문(예: 정상적인 `INSERT INTO protected_identity`, `UPDATE protected_identity SET enabled = false`, `INSERT INTO protected_dataset`, `UPDATE protected_dataset SET state = ...`)은 권한 성공 테스트(positive test)로 이동한다.
2. **Negative Test 대체 단언 보강**: positive test로 이동하며 빠지는 단언으로 인해 보안 검증 커버리지가 축소되는 일이 없도록, 위 4.2의 차단 대상 컬럼에 대한 구문을 `control_forbidden_statements`에 명시적으로 유지·추가한다:
   - `UPDATE protected_identity SET principal_role = 'HOLDOUT_RUNNER'` (역할 위조 차단 단언)
   - `UPDATE protected_identity SET database_login = 'forged-login'` (식별자 변조 차단 단언)
   - `UPDATE protected_dataset SET manifest_sha256 = 'aaaaaaaa...'` (무결성 해시 변조 차단 단언)
   - `UPDATE protected_dataset SET dataset_id = 'forged-dataset'` (식별자 변조 차단 단언)
   - `SELECT envelope FROM protected_artifact` (기존 유지: artifact 기밀성 보호)
   - `INSERT INTO audit_entry ... control_entry = false` (기존 유지: OPERATION 감사 위조 차단)
   - `INSERT INTO operation_capability ...` (capability 위조 차단 단언)

## 5. Dataset State 전이의 Python 강제 원칙

`AGENTS.md`의 "DB 트리거, RLS, 저장 프로시저 및 사용자 정의 함수에 비즈니스 규칙을 구현하지 않는다"는 원칙과 어댑터 설계 문서 §3의 원칙에 따라, Dataset 상태 전이는 DB 제약이 아닌 Python Domain Kernel 및 Application Service 계층에서 단일하게 강제한다.

- **허용 상태 전이 DAG**:
  - `ACCESS_AUTHORIZED` → `AUTHORING`: 문항 작성 시작
  - `AUTHORING` → `REVIEW_READY`: 작성 완료 및 리뷰 요청 (`authored_count > 0` 조건 충족 필수)
  - `REVIEW_READY` → `AUTHORING`: 리뷰 피드백에 따른 재작성 반려
  - `REVIEW_READY` → `FROZEN`: `FreezeDatasetCommand`를 통해서만 가능 (§2.2 불변 조건 충족 필수)
- **DB 계층의 책임**:
  - `state` 컬럼의 유효 enum 값 검증 (`CHECK (state IN (...))`)
  - `state_revision`을 통한 낙관적 동시성 제어 (CAS: `WHERE state_revision = :expected`)
  - `lock_marker = 0`을 통한 명시적 행 잠금 지원

## 6. CONTROL 감사, 전역 락 순서, 멱등성 및 Replay 회귀 방지

### 6.1 `ControlCommandAuditEntry` DTO 확장 및 검증 분기

`ControlAuditTargetKind`에 다음 대상을 추가한다:
- `PROTECTED_IDENTITY = "PROTECTED_IDENTITY"`: Identity 등록 및 비활성화 기록
- `PROTECTED_DATASET = "PROTECTED_DATASET"`: Dataset 등록, 상태 전이, FREEZE 기록

`ControlCommandAuditEntry`는 다음 검증 분기를 가진다:
1. `command_kind`: 9개 literal 전체 허용
2. `target_kind` 및 `target_id` 매핑:
   - `APPROVAL_SOURCE_EVENT` (`INGEST_APPROVAL`): `target_id = source_event_id`
   - `AUTHORIZATION_GRANT` (`GRANT`, `REVOKE`, `EXPIRE`): `target_id = UUIDv4`
   - `PROTECTED_IDENTITY` (`REGISTER_IDENTITY`, `DISABLE_IDENTITY`): `target_id = database_login` (pg identifier)
   - `PROTECTED_DATASET` (`REGISTER_DATASET`, `TRANSITION_DATASET`, `FREEZE_DATASET`): `target_id = f"{dataset_id}:{dataset_version}"`
3. `outcome == SUCCEEDED` 시 레퍼런스 검증:
   - C1 authorization mutation: `result_effective_revision is not None and authorization_audit_event_id is not None`
   - Identity command: `result_effective_revision is None and authorization_audit_event_id is None`
   - Dataset command: `result_effective_revision is not None and authorization_audit_event_id is None`
   - Ingest approval: `result_effective_revision is None and authorization_audit_event_id is None`
4. `outcome == DENIED` 시 레퍼런스 검증:
   - 모든 command에서 `result_effective_revision is None and authorization_audit_event_id is None`
   - `reason_code`는 `_CONTROL_DENIAL_REASONS`에 포함되어야 함

### 6.2 전역 락 계층 (Global Lock Hierarchy)

다중 리소스 접근 시 교착 상태(Deadlock)를 방지하기 위해 모든 control command 트랜잭션은 반드시 다음 엄격한 순서로 행을 잠근다:

1. `protected_identity` (대상 행 잠금, 필요 시 `database_login` 오름차순)
2. `protected_dataset` (`dataset_id, dataset_version` 오름차순 via `lock_marker`)
3. `authorization_grant` (`grant_id` 오름차순 via `lock_marker`)
4. `audit_head` (singleton sequence lock)

### 6.3 멱등성 및 Replay 회귀 방지

1. 모든 command는 `command_kind`와 strict DTO 전체의 canonical JSON SHA-256 해시를 계산한다.
2. 동일 `request_id` + 동일 SHA-256: 기존 성공 결과 또는 기존 거부 오류를 안전하게 재현(Replay)하며 새 변경을 만들지 않는다.
3. 동일 `request_id` + 상이한 SHA-256: 즉시 `CONTROL_COMMAND_CONFLICT`로 거부한다.
4. **회귀 검증 요건**: C2 DTO 확장이 도입되어도 기존 C1의 replay 판정 로직과 기 기록된 C1 CONTROL 감사의 역직렬화·재조회 동작에 일체의 회귀가 발생하지 않아야 한다.

## 7. 상태 및 C2 구현 Issue 분리 지침

이 Decision이 승인되어도 runtime 활성화 상태는 다음을 유지한다:
- effective enforcement: `NOT_IMPLEMENTED`
- HOLDOUT authorization: `NOT_RECORDED`
- HOLDOUT authored: `0`
- Freeze/Runner execution: blocked
- production approval source 및 protected environment: unprovisioned

### C2 구현 Issue 분리 계획

본 Decision 승인 후 C2 구현은 다음 두 단위의 focused Issue로 분리하여 순차 착수한다:

1. **C2-a (Identity Control-Plane)**:
   - `RegisterIdentityCommand`, `DisableIdentityCommand` DTO, Result 검증 분기 및 Application Service 구현
   - `infra/python/protected_retrieval_role_policy.py`의 `protected_identity` INSERT 및 `enabled` UPDATE 권한 반영
   - `tests/migration/test_protected_retrieval_migration.py`의 identity 관련 positive/negative 권한 테스트 재편
   - C1 replay 호환성 검증
2. **C2-b (Dataset Lifecycle & FREEZE Control-Plane)**:
   - `RegisterDatasetCommand`, `TransitionDatasetCommand`, `FreezeDatasetCommand` DTO, `FreezeApprovalSourceEvidence` 및 Application Service 구현
   - 일반 전이에서의 `to_state=FROZEN` 우회 차단 검증
   - FREEZE의 40건, 전수 검토, 4축 0 누출, evidence 결속 및 freeze receipt 생성 구현
   - **[WATCH] 불변 binding과 lifecycle·Freeze receipt 저장/조회 경계 확정**:
     - `protected_dataset.binding` 전체 UPDATE 권한을 일괄 개방하지 않고, Dataset 불변 식별자/해시(`dataset_id`, `dataset_version`, `manifest_sha256`, `protected_artifact_sha256`, `hmac_key_version` 등) 보호를 엄격히 유지
     - `freeze_receipt_ref`의 저장 경로(전용 receipt 컬럼/테이블 또는 엄격히 제어된 부분 갱신)와 어댑터의 `ProtectedDatasetBinding` 조회 시점 조립(합성) 방식을 명시하고 계약·권한 목록 정렬
     - 제한 control 로그인으로 전이·FREEZE 후 새 세션 재조회 정합성, 실패 시 원자적 rollback, 불변 필드 변조 거부를 테스트로 입증
   - `TrustedApprovalSource`의 FREEZE evidence 검증 연동
   - `infra/python/protected_retrieval_role_policy.py`의 `protected_dataset` INSERT 및 lifecycle 컬럼 UPDATE 권한 반영
   - positive 전이/FREEZE 테스트 및 Dataset negative 권한 테스트 보강

## 8. 승인 조건

이 Decision의 승인 증거는 지정 책임 리뷰어 권가빈(`@hazelnutflavoured`)의 단일 GitHub PR `APPROVED` 리뷰 이벤트다.
