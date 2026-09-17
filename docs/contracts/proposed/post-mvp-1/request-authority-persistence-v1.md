# REQUEST Authority Persistence 계약 v1 (#713)

| 항목 | 값 |
| --- | --- |
| 상태 | Proposed / 구현·로컬 검증 완료 · 담당 리뷰 대기 |
| 추적 Issue | [#713](https://github.com/AI-HealthCare-05/AH_05_04/issues/713) |
| 결정 | [`PD-713-20260917`](../../../governance/decisions/2026-09-17-request-authority-persistence.md) |
| 선행·관련 | [#672 Sync Guide Evidence Authority](./sync-guide-evidence-authority-v1.md), [#697 Guide Retrieval Composition](./guide-retrieval-composition-v1.md), #174, #180 |
| 소비자 | [#709](https://github.com/AI-HealthCare-05/AH_05_04/issues/709) Production `GuideEvidenceAuthorityReaderPort` |

---

## 1. 목적과 권위 경계

#709 조사에서 REQUEST Guard·Source Decision·Member Decision의 authoritative historical
persistence가 존재하지 않아 Production Reader가 fail-closed로 차단되었다. 본 계약은 그 차단
요인만 제거한다. 즉 **그 REQUEST 시점에 실제로 발행된 authority observation**을 immutable
증거로 남기고, 이를 exact `ImmutableArtifactRef`로 되돌려 주는 것까지가 범위다.

본 계약은 다음을 포함하지 않는다.

```text
GuideEvidenceAuthorityReaderPort Production Adapter (#709)
#180 runtime orchestration
Evidence Gate · Retrieval · content hydration · assessment authority
Guideline Generator · Citation Authorization
현재 상태를 historical authority로 backfill
```

### 이 표들이 대체하는 것과 대체하지 않는 것

다음 기존 persistence는 REQUEST authority가 **아니다**. 본 계약은 이들을 재사용하지 않으며
조회 시점에 해석해 historical Decision을 만들지도 않는다.

| 기존 persistence | 실제 의미 | authority가 아닌 이유 |
| --- | --- | --- |
| `catalog_source_approval` | 운영자 기준 Source 사용 승인의 현재 상태 | 특정 사용자 요청에 결속되지 않고 `request_operation_code`·`decision_stage`가 없다 |
| `rag_source_snapshot` (`verification_status='CURRENT'`) | 현재 유효한 Snapshot 상태 | 과거 REQUEST 시점 Decision이 아니다 |
| `rag_source_snapshot_member` | Snapshot 구성원 존재 사실 | request-bound PASS/FAIL Decision이 아니다 |
| `ai_job_intake_context` / `ai_job_execution_context` / `retrieval_run`의 `runtime_guard_decision_ref` | 호출자가 전달한 runtime environment guard 참조 문자열 | caller 주장이며 사용자별 REQUEST Guard 관측치가 아니다 |
| `eval_case_result.request_guard_ref` | 평가 결과 행의 nullable 참조값 | Guard Decision 레코드가 아니다 |

---

## 2. 저장 구조

Migration `713a1b2c3d4e`가 세 개의 typed historical authority 표를 추가한다. 하나의 generic
JSON authority 표에 세 종류를 몰아넣지 않는다.

```text
rag_request_guard_authority
rag_request_source_decision
rag_request_member_decision
```

### 2.1 `rag_request_guard_authority`

| 열 | 타입 | 비고 |
| --- | --- | --- |
| `id` | UUID | PK |
| `artifact_code` | VARCHAR(100) | CHECK `= 'request_guard_authority'` |
| `artifact_version` | VARCHAR(50) | 계약 버전 |
| `artifact_content_sha256` | VARCHAR(64) | canonical projection digest |
| `user_id` | UUID | FK `user.id` |
| `request_operation_code` | VARCHAR(100) | 공백 금지 |
| `decision_stage` | VARCHAR(20) | CHECK `= 'REQUEST'` |
| `created_at` | TIMESTAMPTZ | |

`UNIQUE (artifact_code, artifact_version, artifact_content_sha256)`.

### 2.2 `rag_request_source_decision`

Guard 참조는 opaque 문자열이 아니라 Guard artifact identity에 대한 실제 복합 FK다.

```text
FK (request_guard_artifact_code, request_guard_artifact_version, request_guard_content_sha256)
   → rag_request_guard_authority (artifact_code, artifact_version, artifact_content_sha256)
   ON DELETE RESTRICT
```

추가 열: `user_id`, `request_operation_code`, `decision_stage`, `source_snapshot_id`,
`source_code`, `source_version`, `actual_decision_outcome`.

`actual_decision_outcome`은 CHECK `IN ('PASS','FAIL')`이며 NULL을 허용하지 않는다.

### 2.3 `rag_request_member_decision`

Source Decision의 열에 더해 `source_snapshot_member_id`와 `SourceMemberIdentity` 5필드를
lossless하게 보존한다.

| 열 | ENDPOINT_OPERATION | ARTIFACT |
| --- | --- | --- |
| `member_kind` | `'ENDPOINT_OPERATION'` | `'ARTIFACT'` |
| `endpoint_code` | 필수 | NULL |
| `operation_code` | nullable (기존 계약 유지) | NULL |
| `member_artifact_code` | NULL | 필수 |
| `member_artifact_version` | NULL | 필수 |

`member_kind`의 저장 어휘는 기존 `source_member_identity` 계약의 persisted 값을 그대로 쓴다.
CHECK 제약이 위 조합만 허용한다.

### 2.4 교차 도메인 좌표

`source_snapshot_id`와 `source_snapshot_member_id`는 기록 사실로 보존하며 FK를 두지 않는다.
Source 수명주기(cleanup·retention)와 authority 증거의 보존 기간을 결합하지 않기 위한 선택이며,
정합성은 Repository writer의 명시적 검증으로 관리한다. 이는 `retrieval_run.execution_context_id`
등 기존 교차 도메인 좌표 관례와 같다.

### 2.5 DB 로직 금지

Trigger, RLS Policy, Stored Procedure, 사용자 정의 DB 함수를 추가하지 않는다. 불변성은 typed
schema · NOT NULL · UNIQUE · FK · CHECK와 Python append-only writer로만 구성한다.

---

## 3. Immutable Artifact Identity

`ai_worker/tasks/rag/request_authority_artifact.py`가 identity를 확정한다. 새 hash domain을
정의하지 않고 기존 RFC 8785 JCS canonical helper(`ai_worker.tasks.evaluation.canonical`)와
`ImmutableArtifactRef`를 재사용한다.

```text
artifact_code  = 종류별 고정 상수
version        = REQUEST_AUTHORITY_ARTIFACT_VERSION = "1.0"
content_sha256 = sha256(canonical_json(semantic projection))
```

| 종류 | `artifact_code` | projection version |
| --- | --- | --- |
| Guard | `request_guard_authority` | `request-guard-authority-v1` |
| Source Decision | `request_source_decision_authority` | `request-source-decision-authority-v1` |
| Member Decision | `request_member_decision_authority` | `request-member-decision-authority-v1` |

caller는 artifact identity를 고르지 않는다. writer가 authority 사실로부터 계산하고 그 값을
반환한다.

### 3.1 Canonical projection

DB primary key, `created_at`, transaction timestamp, 임의 UUID, 행 삽입 순서는 digest에
포함하지 않는다. 동일한 semantic authority 입력은 항상 동일한 digest를 만든다.

**Guard**

```text
decision_stage
projection_version
request_operation_code
user_id
```

**Source Decision**

```text
actual_decision_outcome
decision_stage
projection_version
request_guard_ref { artifact_code, content_sha256, version }
request_operation_code
source_code
source_snapshot_id
source_version
user_id
```

**Member Decision**

```text
actual_decision_outcome
decision_stage
member_identity { member_kind, endpoint_code, operation_code, artifact_code, artifact_version }
projection_version
request_guard_ref { artifact_code, content_sha256, version }
request_operation_code
source_snapshot_id
source_snapshot_member_id
user_id
```

Identity가 semantic 내용 전체의 digest이므로 **동일 identity는 곧 동일 authority 내용**이다.
따라서 같은 REQUEST context에 대한 정상 retry는 새 행을 만들지 않고 결정론적으로 같은 ref를
돌려준다.

---

## 4. Writer

`backend/app/repositories/rag_request_authority_repository.py`.

```python
record_request_guard_authority(record) -> ImmutableArtifactRef
record_request_source_decision(record) -> ImmutableArtifactRef
record_request_member_decision(record) -> ImmutableArtifactRef
```

Writer는 Decision 정책을 새로 계산하지 않는다. 이미 authoritative한 결과를 받아 구조·결속을
검증하고 canonical identity를 확정한 뒤 immutable insert만 수행한다.

### 4.1 Fail-closed 검증

`RequestAuthorityValidationError`로 거부한다.

```text
공백/비정규 request_operation_code
decision_stage != REQUEST
UUID가 아닌 user_id / snapshot / member 좌표
유효하지 않은 SourceMemberIdentity
PASS/FAIL이 아닌 outcome, NULL outcome
유효하지 않은 request_guard_ref

Source·Member 공통:
- 참조한 Guard가 저장되어 있지 않음
- Guard user 불일치
- Guard request_operation_code 불일치
- Guard decision_stage 불일치
```

값을 strip·lower·casefold·normalize해서 맞춰 주지 않는다. persisted semantics를 그대로 둔다.

### 4.2 Idempotency / Conflict

```text
동일 identity + 동일 내용 → 새 행 없이 같은 ref 반환
동일 identity + 다른 내용 → RequestAuthorityConflictError (덮어쓰지 않음)
```

update/delete API를 제공하지 않는다.

### 4.3 Transaction 경계

Repository는 스스로 commit하지 않는다. 한 REQUEST의 Guard → Source → Member authority를
호출자가 단일 transaction으로 기록할 수 있다.

---

## 5. Exact Read Primitive

```python
get_request_guard_authority_by_artifact_ref(artifact_ref)
get_request_source_decision_by_artifact_ref(artifact_ref)
get_request_member_decision_by_artifact_ref(artifact_ref)
```

조회는 `artifact_code` · `version` · `content_sha256`의 exact equality뿐이다.
`latest`, `CURRENT`, `order_by(created_at desc).first()` 같은 fallback을 제공하지 않는다.

| 상황 | 결과 |
| --- | --- |
| 정상 no-row | `None` |
| 저장 사실이 요청 artifact identity와 불일치 | `RequestAuthorityCorruptError` |
| 지원하지 않는 persisted outcome / stage / member kind | `RequestAuthorityCorruptError` |
| 중복 행 | SQLAlchemy `MultipleResultsFound` (UNIQUE로 차단되며 숨기지 않는다) |

읽기 경로는 persisted 사실로 canonical identity를 다시 계산해 요청 ref와 대조한다. 따라서
저장 내용이 손상되면 정상 not-found로 숨지 않는다.

---

## 6. #709 Handoff

본 계약은 관측치를 다음 형태로 되돌려 준다.

```text
RequestGuardAuthorityRecord
RequestSourceDecisionRecord
RequestMemberDecisionRecord
```

#709는 이를 `AuthoritativeRequestGuardObservation` 등으로 투영하기만 하면 된다. 투영 책임은
#709에 있으며 본 계약은 `GuideEvidenceAuthorityReaderPort`를 구현하지 않는다.

`ObservedDecisionOutcome`, `RequestDecisionStage`, `SourceMemberIdentity`, `SourceMemberKind`,
`ImmutableArtifactRef`의 의미는 변경하지 않는다. #676 authority assembly와 #703 exact join
semantics도 변경하지 않는다.

---

## 7. 개인정보 경계

authority persistence는 ID·code·hash·outcome 수준의 provenance만 저장한다. 환자 free text,
처방전·OCR 원문, retrieved chunk 원문, Provider 원문 응답, API key/credential, LLM 출력은
저장하거나 로그하지 않는다.

---

## 8. 검증

```text
ai_worker/tests/rag/test_request_authority_artifact.py          canonical identity 32건
backend/app/tests/rag/test_rag_request_authority_repository.py  persistence·binding·conflict·DB integration 34건
```
