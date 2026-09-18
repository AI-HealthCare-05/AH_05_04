# Product Decision: Protected Retrieval Production Approval Source Locator and Commit Authority Binding

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-368-R3` |
| 상태 | Candidate · Coordination Confirmed · PR Review Required |
| 선행 Decision | `PD-368-20260909`, `PD-368-R1`, `PD-368-R2` |
| 추적 Issue | [#772](https://github.com/AI-HealthCare-05/AH_05_04/issues/772), 상위 [#368](https://github.com/AI-HealthCare-05/AH_05_04/issues/368) |
| 구현 | 정현우 (`@ceohwj`) |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — 단일 책임 리뷰어 (Product·Privacy·Safety·Evaluation, approval authority 및 governance 타당성) |
| Backend/Security 협의 | 송은영 (`@phina-io`) — Backend, Security 경계 및 DTO 정합 |

## 1. 목적

Issue #772 Production `TrustedApprovalSource` 구현을 위해 GitHub exact review/event를 production approval authority로 사용한다.

다만 FREEZE 경로는 C1과 달리 기존 Dataset binding에 approval target commit anchor가 없고, `source_event_id`가 UUIDv4이므로 GitHub exact review를 결정론적으로 조회하기 위한 locator와 commit authority binding을 추가로 고정한다.

또한 GitHub PR review body는 GitHub REST API를 통해 수정 가능한 mutable text이므로, 승인 대상 payload/hash의 정본은 review body가 아닌 `review.commit_id` exact tree에 저장된 immutable repository artifact로 고정한다(Option B 확정).

이 Decision은 production connector의 조회·검증 경계를 정의하며, protected environment 활성화, HOLDOUT 접근, FREEZE 실행 또는 Release 승인을 의미하지 않는다.

## 2. Production Authority

Production approval authority는 GitHub exact PR APPROVED review event로 고정한다.

### C1 Canonical Source Identity

```text
github:<owner>/<repo>:pull:<pull_number>:review:<review_id>
```

C1은 이 canonical string 자체가 lookup locator를 포함한다.

### Approval Semantics

`snapshot-at-ingest`를 유지한다.

GitHub review가 사후 dismiss되더라도 기존 grant를 자동 취소하지 않는다. 취소는 기존 `RevokeAuthorizationCommand`를 통해서만 수행하며, 이 command 역시 `approval_source_event_id`와 `expected_raw_sha256` 근거를 요구한다.

force-push 또는 target commit 변경 시 기존 approval이 새 commit으로 확대 해석되지 않도록 `commit_id == target_commit_oid` 검증을 강제한다.

## 3. Expected Repository / Branch Authority

Production approval repository는 caller나 untrusted locator가 결정하지 않는다.
- locator/caller는 repository authority를 제공하지 않는다.
- FREEZE/C1 모두 configured repository(`PROTECTED_APPROVAL_REPOSITORY`)가 유일한 authority source다.
- locator는 untrusted ephemeral lookup hint다.
- locator는 FreezeDatasetCommand, control_command_sha256(), replay identity, audit, persistence에 포함되지 않는다.

Protected environment configuration에 다음 authority 값을 고정한다:

```text
PROTECTED_APPROVAL_REPOSITORY
PROTECTED_APPROVAL_BRANCH
```

형식:
- `PROTECTED_APPROVAL_REPOSITORY`: `<owner>/<repo>`
- `PROTECTED_APPROVAL_BRANCH`: non-empty branch name (예: `develop` 또는 `main`)

Production connector는 모든 C1/FREEZE 조회에서 이 값을 authority-side expected repository로 사용한다.

다음은 fail-closed한다:
- config 미설정 또는 partial config
- malformed repository 형식
- C1 canonical source의 `owner/repo`와 config 불일치
- GitHub PR base repository와 configured repository 불일치 (`base.repo.full_name != config`)
- fork PR 거부 (`head.repo.full_name != base.repo.full_name` 또는 `head.repo.full_name != config`)

## 4. FreezeApprovalLocator — 최소 Shape 및 격리

FREEZE의 authority identity는 기존 UUIDv4 계약을 그대로 유지한다:

```text
FreezeDatasetCommand.approval_source_event_id = canonical lowercase UUIDv4
FreezeApprovalSourceEvidence.source_event_id = canonical lowercase UUIDv4
```

connector가 UUID를 생성하거나 GitHub review ID에서 UUIDv4/UUIDv5를 파생하지 않는다. approval 요청자가 승인 요청 시점에 UUIDv4를 먼저 발급하고, 승인 대상 artifact가 그 UUIDv4를 명시적으로 포함한다.

GitHub exact review 조회를 위해 다음 locator를 별도 untrusted lookup hint로 사용한다:

```python
class FreezeApprovalLocator(StrictContractModel):
    source_event_id: str
    pull_number: int
    review_id: int
```

- `source_event_id`: canonical lowercase UUIDv4
- `pull_number`: 양의 정수 (`> 0`)
- `review_id`: 양의 정수 (`> 0`)

repository는 locator에 포함하지 않고 `PROTECTED_APPROVAL_REPOSITORY` config에서만 가져온다.

### Locator Isolation (절대 포함 금지)

`FreezeApprovalLocator`는 다음에 포함하지 않는다:
- `FreezeDatasetCommand`
- `control_command_sha256()`
- request replay / idempotency fingerprint
- authorization / control audit evidence
- persisted Dataset state
- `approval_evidence` persistence

locator는 control service → source port 호출 시점의 ephemeral lookup hint로만 전달한다. 동일한 authoritative approval을 다른 locator representation으로 다시 조회하더라도 FREEZE command identity 자체는 바뀌지 않는다.

## 5. FREEZE Commit Authority Binding

FREEZE approval artifact는 branch head나 default branch에서 읽지 않는다.

GitHub review의 exact:

```text
review.commit_id
```

가 가리키는 commit tree에서만 읽는다.

Production connector는:

```text
review.commit_id == evidence.target_commit_oid
```

를 강제한다.

또한 `review.commit_id`는 pinned repository의 `PROTECTED_APPROVAL_BRANCH` history에 포함된 조상(ancestor)이어야 한다:

```text
merge-base(review.commit_id, PROTECTED_APPROVAL_BRANCH) == review.commit_id
```

GitHub compare API (`GET /repos/{owner}/{repo}/compare/{branch}...{commit_id}`)에서 `ahead_by == 0` (또는 status in `("behind", "identical")`)로 검증한다. 병합되지 않은 feature/fork branch의 artifact는 production approval authority로 사용하지 않는다.

Ancestry API failure는 not-found가 아니라 dependency failure로 처리한다.

## 6. Exact Approval Artifact Path Rules 및 Hash 의미

구현에서 commit tree 전체를 scan/search하거나, review body 검색 또는 source_event_id 문자열 검색을 수행하지 않는다.

### Exact Deterministic Repository Path Rule

- **C1 Artifact Path**:
  ```text
  docs/validation/protected_retrieval/c1/pull_{pull_number}_review_{review_id}.json
  ```
- **FREEZE Artifact Path**:
  ```text
  docs/validation/protected_retrieval/freeze/{source_event_id}.json
  ```

`review.commit_id` tree에서 지정된 exact path의 blob만을 조회한다. 파일이 없으면 exact artifact missing으로 취급한다.

### Hash 의미 분리

`approval_artifact_sha256`와 `target_artifact_sha256`의 의미를 명확히 분리한다:

1. `approval_artifact_sha256`:
   - `review.commit_id`의 exact approval artifact blob UTF-8 bytes의 lowercase SHA-256 hex digest.
   - canonical review projection에 포함되는 값이다.
2. `target_artifact_sha256`:
   - C1: `ApprovalSourceEvidence.target_artifact_sha256 == grant.control_implementation.artifact_sha256`. Immutable approval artifact 내부의 `target_artifact_sha256` 필드에서 읽어 binding과 대조한다.
   - FREEZE: `FreezeApprovalSourceEvidence.target_artifact_sha256`. Immutable freeze approval artifact 내부의 `target_artifact_sha256` 필드에서 읽어 evidence에 결속한다.

## 7. Immutable Approval Artifact DTO

`ai_worker/tasks/evaluation/protected_retrieval_control.py`에 다음 typed artifact DTO를 정의한다:

### C1ApprovalArtifact

```python
class C1ApprovalArtifact(StrictContractModel):
    format_id: Literal["c1.authorization-approval-artifact"] = "c1.authorization-approval-artifact"
    format_version: Literal["1.0.0"] = "1.0.0"
    source_event_id: str = Field(min_length=1, max_length=160)
    authorization_action: AuthorizationAuditAction
    approved_grant_payload_sha256: Sha256Hex
    issuer_role: ProtectedApprovalRole
    target_commit_oid: str = Field(pattern=r"^[0-9a-f]{40}$")
    target_artifact_sha256: Sha256Hex
    implementation_participants: tuple[ActorIdentity, ...] = Field(min_length=1)
```

### FreezeApprovalArtifact

```python
class FreezeApprovalArtifact(StrictContractModel):
    format_id: Literal["freeze.dataset-approval-artifact"] = "freeze.dataset-approval-artifact"
    format_version: Literal["1.0.0"] = "1.0.0"
    source_event_id: str
    action: Literal[ProtectedAction.FREEZE] = ProtectedAction.FREEZE
    dataset_id: str
    dataset_version: str = Field(pattern=r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
    manifest_sha256: Sha256Hex
    protected_artifact_sha256: Sha256Hex
    authored_count: Literal[40] = 40
    review_complete: Literal[True] = True
    leakage_axis_intersections: tuple[Literal[0], Literal[0], Literal[0], Literal[0]] = (0, 0, 0, 0)
    issuer_role: Literal[ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER] = ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER
    target_commit_oid: str = Field(pattern=r"^[0-9a-f]{40}$")
    target_artifact_sha256: Sha256Hex
    implementation_participants: tuple[ActorIdentity, ...] = Field(min_length=1)
```

## 8. canonical_raw_sha256 Exact Projection

`canonical_raw_sha256`는 mutable GitHub review body의 해시가 아니다.

기존 `canonical_json_bytes` (RFC 8785)를 재사용하여 다음 immutable projection을 직렬화한 뒤 SHA-256한다:

```json
{
  "approval_artifact_sha256": "<lowercase-64-hex-string>",
  "commit_id": "<lowercase-40-hex-string>",
  "pull_number": 123,
  "repository": "<owner>/<repo>",
  "review_id": 456789,
  "reviewer": {
    "actor_id": "<login-string>",
    "namespace": "GITHUB_LOGIN"
  },
  "state": "APPROVED",
  "submitted_at": "YYYY-MM-DDTHH:MM:SSZ"
}
```

포함 금지:
- review body
- token / credentials
- provider raw response 전체
- locator 자체
- mutable display text
- provider error body

`submitted_at`은 ISO 8601 UTC 초 단위 문자열(`%Y-%m-%dT%H:%M:%SZ`)로 정규화한다.

## 9. GitHub Review 및 승인자 검증

connector는 최소 다음을 검증한다:
- `state == "APPROVED"` (CHANGES_REQUESTED, DISMISSED, COMMENTED, PENDING은 승인으로 간주하지 않음)
- `reviewer` identity/role
- `repository == PROTECTED_APPROVAL_REPOSITORY`
- PR repository/base authority 및 fork PR 거부
- `review.commit_id == evidence.target_commit_oid`
- protected branch ancestry
- exact commit artifact blob 및 hash/binding 대조

FREEZE 승인자 독립성:
- GitHub Product/Safety review는 1건이다 (`issuer.role == PRODUCT_SAFETY_REVIEWER`).
- Custodian은 DB command 실행 주체(`executor.role == DATASET_CUSTODIAN`)로 증명된다.
- `issuer.actor != executor.actor`
- `issuer`와 `executor` 모두 `implementation_participants`에 포함되지 않아야 한다.

## 10. Error Mapping & Capability-Specific Probing

### 404 분류 규칙 (Capability-Specific Probing)

리소스 404 수신 시 단순 `GET /repos/{repo}` 메타데이터(200)만으로 `ApprovalSourceNotFoundError`를 속단하지 않고, 각 리소스 API 패밀리의 권한/기능(capability)이 정상인지 단계별로 검증한다:

1. **PR 조회 404 (`GET /repos/{repo}/pulls/{pull_number}`)**:
   - `GET /repos/{repo}` 메타데이터 정상 확인
   - PR 컬렉션 읽기 capability 확인 (`GET /repos/{repo}/pulls?per_page=1` HTTP 200)
   - PR API 접근이 확인되고 exact PR만 부재할 때만 `ApprovalSourceNotFoundError`
   - PR API 권한/접근이 불확실(401/403/404/타임아웃 등)하면 `ProtectedSecurityError("INTERNAL_ERROR")`

2. **Review 조회 404 (`GET /repos/{repo}/pulls/{pull_number}/reviews/{review_id}`)**:
   - 상위 PR 자체의 정상 조회(`GET /repos/{repo}/pulls/{pull_number}` HTTP 200) 확인
   - PR 접근이 정상인데 exact review만 부재하면 `ApprovalSourceNotFoundError`
   - PR 접근 자체가 불확실하면 `ProtectedSecurityError("INTERNAL_ERROR")`

3. **Artifact 조회 404 (`exact commit_id` + `deterministic path`)**:
   - 상위 exact commit/ref 정상 접근 확인 (`GET /repos/{repo}/commits/{commit_id}` HTTP 200)
   - contents API capability 정상 확인
   - commit/contents 접근이 정상인데 exact artifact path만 부재하면 `ApprovalSourceNotFoundError`
   - commit 또는 contents 접근이 불확실하면 `ProtectedSecurityError("INTERNAL_ERROR")`

4. **Branch Ancestry 조회**:
   - GitHub compare API 실패(401, 403, 404, 429, 5xx, timeout 등)는 not-found가 아닌 dependency failure(`ProtectedSecurityError("INTERNAL_ERROR")`)로 fail-closed.

### Dependency Failure

다음 모든 항목은 dependency failure로 분류하며 fail-closed한다:
- invalid / missing credentials
- repository 접근 불가 (401, 403, 404, 네트워크 실패)
- 401 Unauthorized
- 403 Forbidden (secondary rate limit 포함)
- 429 Too Many Requests
- network timeout
- 5xx Server Error
- malformed provider response
- ambiguous result
- protected branch ancestry API failure

소비 측 서비스에서 최종적으로:

```text
ProtectedSecurityError("INTERNAL_ERROR")
```

로 처리된다. Dependency failure를 permanent `APPROVAL_NOT_VERIFIED` policy denial evidence로 오기록하지 않는다.

token, raw provider response, error response body는 예외 메시지나 로그에 노출하지 않는다.

## 11. Configuration 및 Runtime Boundary

다음 설정을 protected environment configuration에 추가한다:

```text
PROTECTED_APPROVAL_REPOSITORY: str | None
PROTECTED_APPROVAL_BRANCH: str | None
PROTECTED_APPROVAL_GITHUB_TOKEN: SecretStr | None
```

`PROTECTED_RETRIEVAL_ENABLED=true`인 경우 세 항목 모두 필수이며, 누락 또는 형식 오류 시 startup validation에서 fail-closed한다.

`PROTECTED_RETRIEVAL_ENABLED=false`인 경우 optional (기본값 `None`).

Runtime Assembly:
- `create_trusted_approval_source(config: Config) -> TrustedApprovalSource` 추가.
- `PROTECTED_RETRIEVAL_ENABLED=false` 또는 설정 누락 시 즉시 예외 발생.
- synthetic/fake fallback은 허용하지 않는다.
- 기존 `create_protected_authorization_control_service(config, approval_source)`의 explicit injection seam은 그대로 유지한다.

## 12. Status Boundary

이 Decision 및 Phase B 구현만으로 다음 operational status를 승격하지 않는다:

```text
effective_enforcement_status = NOT_IMPLEMENTED
access_authorized = false
holdout_authored = false
freeze_recorded = false
release_eligible = false
```
