# Issue #513 C2-b Protected Dataset Lifecycle & FREEZE Control-Plane 설계

## 1. 상태와 목적

- 이슈: `#513`
- 상위 Decision: `PD-368-R2` / PR `#498`
- 선행 작업: `#512` C2-a Identity Control-Plane
- 구현 담당: 정현우 (`@ceohwj`)
- 단일 책임 리뷰: 권가빈 (`@hazelnutflavoured`)
- 상태: Active · 2026-09-14 설계 승인

이 설계는 `RegisterDatasetCommand`, `TransitionDatasetCommand`, `FreezeDatasetCommand`를 기존
PostgreSQL protected retrieval control-plane에 추가한다. 일반 전이와 FREEZE를 분리하고,
40건·전수 검토·4축 0 누출·독립 승인 evidence·불변 receipt를 하나의 트랜잭션에
결속한다. `protected_dataset.binding`은 전체 UPDATE하지 않고 식별자와 무결성 hash를
계속 보호한다.

본 변경은 기존 target 계약의 구현이며 API·DB schema·메시지 계약을 바꾸지 않는다.
HOLDOUT 문항 작성·열람·실행, production trusted source, protected 환경 provisioning,
`EXT-PRIV-001`, Current 승격과 공개 gate는 범위 밖이다.

## 2. 정본과 선행 조건

구현은 다음 순서로 정본을 해석한다.

1. `AGENTS.md`, `CONTRIBUTING.md`
2. `docs/governance/decisions/2026-09-14-protected-retrieval-c2-command-and-privilege-expansion.md`
3. `docs/contracts/targets/post-mvp-1/protected-retrieval-infrastructure-v1.md`
4. Issue `#513`
5. C2-a가 확장한 command/result/audit/replay 구조
6. 기존 `protected_retrieval.py` domain kernel과 PostgreSQL adapter

C2-b는 C2-a 병합 결과를 선행 기준으로 삼는다. 설계 작성 시점의 `develop@b85d32d9`에는 C2-a가
병합되지 않았고, C2-a PR `#522`가 병합 절차 중이다. Antigravity Gemini는 C2-a가
`develop`에 병합된 뒤 C2-b 브랜치를 rebase하거나,
담당자가 승인한 동일 C2-a HEAD를 선행 기준으로 사용해야 한다. C2-b가 C2-a 코드를
독자적으로 복제하거나 다른 형태로 재구현해서는 안 된다.

## 3. 핵심 결정

### 3.1 저장 책임 분리

`protected_dataset`의 정보를 세 부류로 나눈다.

| 부류 | 저장 정본 | 변경 규칙 |
| --- | --- | --- |
| Dataset 식별·무결성 | `binding` JSON 원본 + `dataset_id`, `dataset_version`, `manifest_sha256`, `protected_artifact_sha256`, `hmac_key_version` 컬럼 | 등록 후 불변 |
| Lifecycle | `state`, `state_revision`, `authored_count`, `review_complete` 컬럼 | Application Service의 CAS UPDATE만 허용 |
| Freeze receipt | 성공한 `FREEZE_DATASET` CONTROL 감사 | 추가만 가능, UPDATE·DELETE 불가 |

`binding` JSON은 등록 명령의 정규화된 `ProtectedDatasetBinding` 원본이다. 이후 상태
전이에서는 JSON을 바꾸지 않고 lifecycle 컬럼만 바꾼다. 조회 시에는 JSON의 불변
필드와 lifecycle 컬럼을 결합해 권위 있는 `ProtectedDatasetBinding`을 조립한다.

### 3.2 감사 event를 Freeze Receipt로 사용

성공한 `FREEZE_DATASET` CONTROL 감사의 `event_id`는 command의 canonical UUIDv4 `request_id`와 같다.
이 값을 다음 logical receipt로 해석한다.

```python
OpaqueLogicalRef(
    namespace=OpaqueRefNamespace.AUDIT_EVENT,
    value=freeze_control_audit.event_id,
)
```

이 방식은 새 DB 컬럼·테이블·migration 없이 receipt를 불변 audit chain에 저장한다.
Dataset lifecycle UPDATE와 CONTROL 감사 append·`audit_head` CAS가 동일 트랜잭션에 있으므로
둘 중 하나만 성공할 수 없다. receipt는 command 결과에 새 필드로 실어 나르지 않고,
후속 Dataset 조회에서 조립한다.

전용 컬럼 추가는 Issue `#513`의 "DB schema 변경 없음"과 충돌하며, `jsonb_set(binding, ...)`는
PostgreSQL 컬럼 권한 특성상 control role에 `binding` 전체 UPDATE 권한을 주게 되므로 모두
선택하지 않는다.

### 3.3 Fail-closed Dataset 조립

PostgreSQL adapter의 Dataset 조회는 control/data plane에서 같은 조립 규칙을 사용한다.

1. `binding` JSON을 strict `ProtectedDatasetBinding`으로 검증한다.
2. JSON의 `dataset_id`, `dataset_version`, `manifest_sha256`, `protected_artifact_sha256`,
   `hmac_key_version`가 동일 행의 분리 컬럼과 exact-match하는지 검증한다.
3. 반환 model의 `state`, `state_revision`, `authored_count`, `review_complete`를 lifecycle 컬럼 값으로
   덮어쓴다. JSON의 이 복제 값은 등록 시점 snapshot이지 현재 상태 정보가 아니다.
4. 현재 상태가 `FROZEN`이면 전체 hash chain 검증을 통과한 CONTROL 감사에서 다음을
   모두 만족하는 유일한 entry를 찾는다.
   - `command_kind == "FREEZE_DATASET"`
   - `target_kind == PROTECTED_DATASET`
   - `target_id == f"{dataset_id}:{dataset_version}"`
   - `outcome == SUCCEEDED`
   - `reason_code == DATASET_FROZEN`
   - `result_effective_revision == protected_dataset.state_revision`
5. 위 entry가 없거나 복수이거나 현재 revision과 다르면 `AUDIT_BINDING_MISMATCH`로 거부한다.
6. 일치하면 entry의 `event_id`로 `freeze_receipt_ref`를 조립한다.
7. `FROZEN`이 아닌 상태에서 `freeze_receipt_ref`는 항상 `None`이다.

새 C2-b 경로로 등록된 Dataset은 FROZEN 조회 시 반드시 FREEZE audit receipt를 요구한다.
기존 test seed가 JSON에 직접 넣은 `freeze_receipt_ref`를 무조건 신뢰하는 fallback은 두지 않는다.
기존 data-plane 통합 fixture는 실제 FREEZE CONTROL audit를 함께 만들도록 갱신한다.

## 4. Command·Evidence·Result 계약

### 4.1 `RegisterDatasetCommand`

```text
request_id
dataset_id
dataset_version
binding: ProtectedDatasetBinding
manifest_sha256
protected_artifact_sha256
hmac_key_version
```

다음을 DTO validator에서 고정한다.

- `request_id`: canonical lowercase UUIDv4
- `dataset_id`: canonical lowercase UUIDv4
- `dataset_version`: canonical SemVer 숫자 3요소
- digest: lowercase SHA-256 hex 64자
- top-level Dataset ID/version/hash/key version과 `binding`의 동일 필드 exact-match
- `binding.state == ACCESS_AUTHORIZED`
- `binding.state_revision == 1`
- `binding.authored_count == 0`
- `binding.review_complete is false`
- `binding.freeze_receipt_ref is None`

`leakage_axis_intersections`, `execution_authorization_ref`, `retriever_binding_ref`는 상위 계약이
등록 시 금지하지 않으므로 추가 제약을 만들지 않는다. FREEZE와 RUN은 각 명령이
필요한 값을 별도로 검증한다.

### 4.2 `TransitionDatasetCommand`

```text
request_id
dataset_id
dataset_version
from_state
to_state
expected_state_revision >= 1
authored_count: 0..40
review_complete: bool
```

허용 DAG은 다음 세 개뿐이다.

- `ACCESS_AUTHORIZED -> AUTHORING`
- `AUTHORING -> REVIEW_READY`, 단 `authored_count > 0`
- `REVIEW_READY -> AUTHORING`

`to_state == FROZEN`은 DB connection, replay 조회, 상태 조회, 감사 append 전에
`ProtectedSecurityError("ROLE_ACTION_STATE_DENIED")`로 거부한다. 이는 `PD-368-R2 §2.2`의
"어떤 DB 조작도 하지 않고 즉시 거부"를 우선한 preflight 거부이다. DTO 생성 자체를
막지 않고 Application Service가 고정 security error를 반환하게 한다.

기타 비허용 전이, `from_state`·revision 불일치는 `DATASET_STATE_MISMATCH`로 거부한다.
계약에 없는 authored count 단조 증가, `review_complete` 추가 상태 규칙은 임의로 만들지 않는다.

### 4.3 `FreezeApprovalSourceEvidence`

`PD-368-R2 §2.4.1`의 DTO를 그대로 구현한다.

- `action == ProtectedAction.FREEZE`
- `source_event_id`: canonical lowercase UUIDv4
- Dataset ID/version, manifest hash, protected artifact hash
- `authored_count == 40`
- `review_complete is true`
- `leakage_axis_intersections == (0, 0, 0, 0)`
- issuer role `PRODUCT_SAFETY_REVIEWER`
- `state == APPROVED`
- UTC-aware `recorded_at`
- 40자 lowercase commit OID, artifact/raw SHA-256
- 중복 없는 `implementation_participants`

`TrustedApprovalSource`는 C1 계약을 혼합하지 않도록 typed method를 사용한다.

```python
class TrustedApprovalSource(Protocol):
    async def fetch(self, source_event_id: str) -> ApprovalSourceEvidence: ...
    async def fetch_freeze(self, source_event_id: str) -> FreezeApprovalSourceEvidence: ...
```

C1 `fetch()`의 반환 형을 union으로 바꾸지 않아 기존 ingestion·grant/revoke 검증의
역직렬화와 replay를 보존한다.

### 4.4 `FreezeDatasetCommand`

```text
request_id
dataset_id
dataset_version
expected_state_revision >= 1
approval_source_event_id
expected_raw_sha256
```

`request_id`, `dataset_id`, `approval_source_event_id`는 canonical lowercase UUIDv4이고 Dataset version과
digest는 등록 command와 같은 형식 규칙을 따른다.

검증 순서는 다음이다.

1. 인증된 executor가 `DATASET_CUSTODIAN`인지 사전 확인한다.
2. executor가 동일 actor identity의 `HOLDOUT_AUTHOR` data-plane identity를 갖지 않았는지 사전 확인한다.
3. 위 자격이 확인된 뒤 trusted source의 evidence를 조회한다.
4. executor와 evidence issuer가 다르고, 둘 모두 `implementation_participants`에 포함되지
   않았는지 확인한다.
5. 현재 Dataset이 `REVIEW_READY`이고 expected revision이 일치하는지 확인한다.
6. 조립한 Dataset의 `authored_count == 40`, `review_complete is true`,
   `leakage_axis_intersections == (0, 0, 0, 0)`를 확인한다.
7. evidence의 source ID/raw hash, Dataset ID/version, 두 hash, count/review/leakage가 exact-match하는지
   확인한다.
8. lifecycle 컬럼을 `FROZEN`, `state_revision + 1`로 CAS UPDATE한다.
9. 같은 트랜잭션에서 `DATASET_FROZEN` CONTROL 감사를 append한다.

count/review/leakage 미충족은 `FREEZE_EVIDENCE_INCOMPLETE`, 상태·revision 불일치는
`DATASET_STATE_MISMATCH`, evidence action·결속 불일치는 기존 allowlist의
`APPROVAL_ACTION_MISMATCH`·`APPROVAL_EVIDENCE_MISMATCH`, 역할·독립성 위반은
`ISSUER_ROLE_DENIED`·`SELF_APPROVAL_DENIED`로 매핑한다.

### 4.5 Result·Audit 확장

`ControlCommandKind`과 success reason에 다음을 추가한다.

| Command | reason | target | effective revision | authorization audit ref |
| --- | --- | --- | ---: | --- |
| `REGISTER_DATASET` | `DATASET_REGISTERED` | `dataset_id:dataset_version` | 1 | `None` |
| `TRANSITION_DATASET` | `DATASET_TRANSITIONED` | `dataset_id:dataset_version` | 새 revision | `None` |
| `FREEZE_DATASET` | `DATASET_FROZEN` | `dataset_id:dataset_version` | 새 revision | `None` |

`ControlAuditTargetKind.PROTECTED_DATASET`, 세 success reason, C2-b command literal을
`ProtectedAuditReason`, `ControlCommandAuditEntry`, `_EXPECTED_CONTROL_TARGETS`,
`_EXPECTED_CONTROL_REASONS`, `ControlCommandResult`, replay success reason map에 같이 추가한다.
정책 거부 감사에 필요한 `FREEZE_EVIDENCE_INCOMPLETE`도 `_CONTROL_DENIAL_REASONS`에
명시적으로 추가한다. DB 접근 전에 반환하는 일반 전이의 FROZEN preflight
`ROLE_ACTION_STATE_DENIED`는 DENIED audit을 만들지 않으므로 allowlist 확장 근거로 사용하지 않는다.
기존 C1/C2-a entry의 exact-field 역직렬화와 result validation은 변하지 않아야 한다.

Dataset `target_id`는 canonical UUIDv4 Dataset ID와 canonical SemVer를 `:`로 연결한 형태로
검증한다. Dataset ID 자체가 UUIDv4이므로 별도 escape 규칙이나 새 문자열 제약을
만들지 않는다.

## 5. Application Service 흐름

### 5.1 공통 사전 처리

- command의 canonical SHA-256을 기존 `control_command_sha256()`로 계산한다.
- executor를 인증한 뒤 기존 exact replay/conflict를 먼저 확인한다. 기록된 terminal이 있으면
  trusted source를 다시 조회하거나 Dataset을 변경하지 않는다.
- 외부 trusted source 조회가 필요한 FREEZE는 먼저 짧은 DB transaction에서 executor의
  Custodian 자격과 Author 중복 부재를 확인한다. 권한 있는 executor에 한해 DB 행 lock 전에
  evidence를 읽고, mutation transaction에서 executor·역할·exact binding을 재확인한다.
- 시작 시점 executor와 mutation transaction의 executor가 다르면
  `AUTHORIZATION_NOT_FOUND`로 fail-closed한다.
- 신뢰 가능한 policy denial만 DENIED CONTROL 감사로 commit한다. DB/audit/serialization
  내부 오류는 policy denial로 위장하지 않고 전체 rollback한다.

### 5.2 역할 분리

모든 Dataset command executor는 `approval_role=DATASET_CUSTODIAN`이어야 한다. 또한 같은
`actor_namespace + actor_id`로 활성 `HOLDOUT_AUTHOR` data-plane identity가 존재하면 Dataset 등록,
전이, FREEZE를 거부한다. 이는 database login 문자열이 다른 dual-plane identity로
Author/Custodian 분리를 우회하지 못하게 한다.

현재 schema에는 Dataset별 author 참여 관계가 없다. `authorization_grant`는 Dataset 등록 후에만
만들 수 있어 등록 command의 author 분리를 증명할 수 없고, 기존 `protected_identity`는
actor와 plane/role만 알고 Dataset scope를 알지 못한다. 따라서 이 설계는 schema를 늘리지 않는
보수적 해석으로 **전역 actor-level Author/Custodian 겸직을 금지**한다. 이는 다른 Dataset의
Author였던 actor도 Custodian command를 실행할 수 없게 하므로, 구현 전 단일 책임
리뷰어가 계약 의도와 맞는지 확인해야 한다. Dataset별 분리가 필요하다면 이슈
범위와 계약·schema를 별도로 바꾸어야 하며 Antigravity Gemini가 임의로 grant 유무만 검사하는 불완전한
대체안을 만들지 않는다.

FREEZE evidence issuer는 `PRODUCT_SAFETY_REVIEWER`이어야 하며 executor와 다른 전체
`ActorIdentity`여야 한다. actor ID만 같고 namespace를 바꾸는 승인은 독립 승인으로
취급하지 않는다. executor와 issuer 모두 control implementation participant여서는 안 된다.

### 5.3 등록

1. executor 신원 행과 같은 actor의 Author identity 행을 `database_login` 오름차순으로 잠근다.
2. Dataset key 존재 여부를 확인한다.
3. 동시 등록 race를 transaction-aborting unique violation으로 처리하지 않고
   `INSERT ... ON CONFLICT DO NOTHING RETURNING state_revision`로 수렴시킨다.
4. insert가 없으면 lock 후 replay를 재확인하고, exact replay가 아니면
   `CONTROL_COMMAND_CONFLICT`를 DENIED audit한다.
5. insert가 있으면 `state_revision=1`과 `DATASET_REGISTERED` CONTROL 감사를 함께 commit한다.

### 5.4 일반 전이

1. `to_state=FROZEN`을 DB 접근 전에 거부한다.
2. executor/Author identity를 잠근다.
3. Dataset 행을 `FOR UPDATE`로 잠근다.
4. audit head를 잠근 exact replay/conflict를 재확인한다.
5. 현재 상태·revision·DAG·`AUTHORING -> REVIEW_READY` count를 검증한다.
6. `WHERE state=:from_state AND state_revision=:expected` CAS로 lifecycle 컬럼과 revision을 바꾼다.
7. `DATASET_TRANSITIONED` CONTROL 감사를 append한다.

### 5.5 FREEZE

1. 짧은 사전 transaction에서 executor를 인증하고 exact replay/conflict를 확인한다.
2. 새 command이면 executor의 Custodian 자격과 Author 중복 부재를 확인한다.
3. 권한 확인 후 trusted evidence를 사전 조회한다.
4. mutation transaction에서 executor/Author identity를 잠그고 자격을 재확인한다.
5. Dataset 행을 `FOR UPDATE`로 잠근다.
6. audit head를 잠근 exact replay/conflict를 재확인한다.
7. Dataset·evidence·독립성·FREEZE 불변 조건을 검증한다.
8. `REVIEW_READY -> FROZEN` CAS UPDATE를 수행한다.
9. 성공 CONTROL 감사를 append해 `request_id` receipt를 완성한다.

UPDATE 후 audit append·head CAS가 실패하면 Dataset UPDATE도 함께 rollback된다. 반대로 audit만
남고 Dataset이 FROZEN이 아닌 상태도 허용하지 않는다.

## 6. Lock·동시성·Replay

다중 리소스를 만지는 mutation transaction의 lock 순서는 `PD-368-R2`를 따른다.

1. `protected_identity`: 관련 행을 `database_login` 오름차순
2. `protected_dataset`: `(dataset_id, dataset_version)`
3. `authorization_grant`: C2-b는 직접 변경하지 않음
4. `audit_head`

동일 command의 동시 실행은 identity/Dataset lock 후 audit replay를 다시 확인해 한 번의
mutation과 한 개의 terminal CONTROL entry로 수렴한다. 동일 `request_id`+동일 command hash는
기존 result/error를 반환하고, 동일 ID+다른 hash는 `CONTROL_COMMAND_CONFLICT`다.

Dataset의 다른 request ID로 들어온 동시 전이는 `expected_state_revision` CAS로 하나만 성공한다.
대기 후 다시 검증할 때는 초기 시각이 아니라 PostgreSQL `clock_timestamp()`를 새로 읽어
감사 시각을 갱신한다.

FROZEN Dataset 조립은 receipt 위조를 막기 위해 기존 journal처럼 전체 audit chain을 검증한다.
이는 조회당 `O(audit entries)` 비용을 갖지만 현재 adapter가 replay·operation history에서 이미
같은 전체 검증을 사용하며, Issue `#513`에 별도 성능 요구가 없다. 캐시·별도 인덱스·receipt
테이블은 측정된 병목과 새 계약 없이 추가하지 않는다.

## 7. DB 최소 권한

`infra/python/protected_retrieval_role_policy.py`의 control plane에 다음만 추가한다.

- INSERT `protected_dataset`:
  `dataset_id`, `dataset_version`, `binding`, `manifest_sha256`, `protected_artifact_sha256`,
  `hmac_key_version`, `state`, `state_revision`, `authored_count`, `review_complete`, `lock_marker`
- UPDATE `protected_dataset`:
  `state`, `state_revision`, `authored_count`, `review_complete`, `lock_marker`

다음은 control login에서 계속 DB 권한 수준으로 거부해야 한다.

- UPDATE `dataset_id`, `dataset_version`
- UPDATE `binding`
- UPDATE `manifest_sha256`, `protected_artifact_sha256`, `hmac_key_version`
- `protected_artifact.envelope` SELECT/INSERT/UPDATE
- `operation_capability` INSERT/UPDATE
- `audit_entry` OPERATION 위조
- DELETE, TRUNCATE, TRIGGER, REFERENCES, schema CREATE, 테이블 전체 권한

DB trigger, RLS, stored procedure, user-defined function으로 lifecycle 규칙을 옮기지 않는다.

## 8. 수정 예상 파일과 책임

| 파일 | 변경 책임 |
| --- | --- |
| `ai_worker/tasks/evaluation/protected_retrieval.py` | Dataset audit target/reason, C2-b audit 형상과 거부 allowlist |
| `ai_worker/tasks/evaluation/protected_retrieval_control.py` | Dataset command/evidence/result DTO, typed trusted source, verification helper |
| `ai_worker/adapters/postgresql_protected_retrieval.py` | 공통 Dataset 행 조립과 FROZEN receipt 검증 |
| `ai_worker/adapters/postgresql_protected_retrieval_control.py` | register/transition/freeze Application Service, lock/CAS/audit/replay |
| `infra/python/protected_retrieval_role_policy.py` | Dataset INSERT/lifecycle UPDATE 컬럼 권한 |
| `ai_worker/tests/evaluation/test_protected_retrieval_control.py` | DTO·hash·result·audit·evidence 단위 계약 |
| `tests/integration/rag/test_protected_retrieval_control_postgresql.py` | 제한 control login의 실제 lifecycle/FREEZE/replay/rollback/동시성 |
| `tests/integration/rag/test_protected_retrieval_postgresql.py` | FROZEN Dataset 조립과 data-plane receipt 회귀 |
| `tests/migration/test_protected_retrieval_migration.py` | positive 컬럼 권한과 불변 필드 negative 권한 |
| `docs/contracts/targets/post-mvp-1/protected-retrieval-infrastructure-v1.md` 및 validation 산출물 | 구현 PR에서 상태·증거 정렬; Current 승격 금지 |

새 migration, API, Backend, Frontend, evaluation Dataset content, dependency는 추가하지 않는다.

## 9. 테스트 설계

### 9.1 단위 계약

- 세 command의 strict field, UUID, SemVer, hash, range, extra field 거부
- Register Dataset과 binding의 식별자·hash exact-match, 초기 lifecycle, receipt `None`
- Dataset ID UUIDv4와 `target_id=UUIDv4:SemVer` 규칙
- `TransitionDatasetCommand(to_state=FROZEN)` service preflight 거부
- 세 허용 DAG과 기타 전이 거부
- `FreezeApprovalSourceEvidence` exact shape, UTC, 40건, 4축 tuple, participant 중복 거부
- Dataset result/audit success·denial 형상, reason·target mismatch 거부
- C1/C2-a 기존 entry의 역직렬화·hash/replay 불변

### 9.2 PostgreSQL 통합

- Custodian register -> 새 세션에서 `ACCESS_AUTHORIZED/revision=1` 조회
- 전이 DAG 왕복과 revision 증가
- 잘못된 role과 Author/Custodian actor 중복 거부
- 잘못된 from state/revision 거부 및 mutation 0
- 일반 FROZEN 전이 거부 시 DB/audit 변경 0
- 39건, review false, 각 leakage 축 non-zero 별 FREEZE 거부
- source missing, raw hash, action, issuer role, Dataset/version/hash, participant 불일치 거부
- 정상 FREEZE 후 `state=FROZEN`, revision 증가, 성공 audit event ID와
  `freeze_receipt_ref` exact-match
- 정상 FREEZE 후 새 control/data 세션 모두 같은 Dataset binding 조립
- audit append/head CAS 실패 시 Dataset lifecycle rollback
- lifecycle UPDATE 실패 시 success audit 0
- 동시 동일 request는 effect 1개, 동시 다른 request/revision은 성공 1개
- 성공/denial exact replay, 변경 command conflict
- receipt audit 누락·복수·revision mismatch·hash-chain tamper 시 fail-closed

### 9.3 최소 권한

- control login Dataset INSERT 성공
- control login lifecycle 컬럼 UPDATE 성공
- `binding`, Dataset ID/version, 두 hash, key version UPDATE 각각 DBAPIError
- data login Dataset lifecycle UPDATE 거부
- 기존 artifact/capability/OPERATION audit 격리 거부 유지
- connection validator가 추가·누락 privilege를 모두 탐지

### 9.4 검증 명령

구현은 작은 범위부터 검증한다.

```bash
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest ai_worker/tests/evaluation/test_protected_retrieval_control.py -q
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/integration/rag/test_protected_retrieval_control_postgresql.py -q
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/integration/rag/test_protected_retrieval_postgresql.py -q
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run pytest tests/migration/test_protected_retrieval_migration.py -q
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run ruff check ai_worker/tasks/evaluation ai_worker/adapters ai_worker/tests/evaluation tests/integration/rag tests/migration infra/python
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run ruff format ai_worker/tasks/evaluation ai_worker/adapters ai_worker/tests/evaluation tests/integration/rag tests/migration infra/python --check
UV_CACHE_DIR=/private/tmp/ah_issue513_uv_cache uv run mypy ai_worker/tasks/evaluation ai_worker/adapters
git diff --check
```

그 뒤 `CONTRIBUTING.md`의 전체 필수 검사를 수행한다. protected PostgreSQL 통합 테스트가
환경 gate로 skip되면 통과로 신고하지 않고, 실제 제한 login으로 실행한 결과를 PR
증거에 남긴다. medical AI 내용이나 모델 행동은 바꾸지 않으므로 별도 LLM 품질
eval은 필요하지 않지만, protected evaluation control 회귀는 기본 Worker lane에서 반드시 통과해야
한다.

## 10. 실패·보안·비공개 경계

- exception, SQL, query/Gold/Evidence body, credential, HMAC/fingerprint, 저장 위치를 오류·result·audit에
  넣지 않는다.
- caller가 제공한 receipt를 받지 않고, 성공한 감사 event로부터만 조립한다.
- `binding` JSON과 분리 컬럼의 불변 필드가 다르면 `INTERNAL_ERROR`로 축약하지 않고
  무결성 계약에 맞는 고정 `DATASET_BINDING_MISMATCH`로 거부한다.
- 감사 chain을 검증할 수 없으면 receipt 추정·fallback을 하지 않고 `AUDIT_UNAVAILABLE`로 거부한다.
- control DB credential은 Application Service 외의 임의 SQL 실행에 사용되지 않는 신뢰 경계다.
  DB에는 enum/range/CAS용 일반 constraint와 최소 컬럼 권한만 두고, 상태 DAG·독립 승인·FREEZE
  의미 규칙은 Python Service에서 명시적으로 강제한다.
- 이 구현은 effective enforcement, HOLDOUT access/run, production/publication을 열지 않는다.

## 11. 구현 전 게이트와 리뷰 결론

기술적 설계 검토 결과, audit-backed receipt 저장·조립은 다음 조건에서 구현 가능하다.

- schema 변경 없음
- `binding` UPDATE 권한 불필요
- lifecycle와 receipt의 원자적 commit
- 기존 hash-chain tamper 검증 재사용
- `ControlCommandResult` 형상 변경 없음
- 새 세션과 data/control plane의 동일 조립 결과

다만 다음 게이트는 Antigravity Gemini 구현 전·PR 병합 전에 확인해야 한다.

1. **C2-a 선행 통합**: C2-a가 `develop`에 없는 상태에서 C2-b를 독자 구현하지 않는다.
2. **Decision metadata 정렬**: PR `#498`은 병합됐고 Issue `#513`은 이를 승인된 계약으로
   인용하지만, Decision 문서 표의 상태는 아직 `Candidate · Coordination Confirmed · PR Review Required`이고
   target contract도 `C2 command 및 권한 확장 candidate`로 표기한다. 구현 PR은 계약 내용을
   바꾸지 말고, 단일 책임 리뷰어에게 이 metadata가 병합 상태와 정렬되어야 하는지
   확인한다.
3. **승인 provenance**: PR `#498`에는 `@hazelnutflavoured`의 `최종 판단: APPROVE` 리뷰
   본문이 있지만 GitHub API의 현재 review state는 `DISMISSED`다. PR이 이미 병합됐더라도
   `AGENTS.md`의 지정 리뷰어 승인 규칙과 추적 증거를 맞추기 위해, 구현 PR에서
   Issue `#513`의 지정 리뷰어가 PD-368-R2 계약 해석과 본 receipt 결정을 새로 승인해야 한다.
4. **Author/Custodian 분리 해석**: 현재 schema에 Dataset별 author 참여 관계가 없으므로
   전역 actor-level 겸직 금지를 적용한다. 지정 리뷰어가 이 보수적 거부 범위를 인수하지
   않으면 C2-b 구현 전에 새 Decision/contract/schema 범위가 필요하다.
5. **공개 상태 보존**: validation/status 산출물의 `effective enforcement=NOT_IMPLEMENTED`,
   HOLDOUT 0/blocked, production unprovisioned를 그대로 유지한다.

위 metadata/provenance 표기는 코드 구현을 위해 임의로 재해석하거나 이 설계 문서에서
대신 수정하지 않는다. 담당자와 단일 책임 리뷰어가 구현 PR의 검토 범위와 증거로
정렬한다.

## 12. Antigravity Gemini 인계 규칙

Antigravity Gemini는 이 문서와 Issue `#513`을 구현 정본으로 삼되 다음을 지켜야 한다.

- 먼저 C2-a 통합 HEAD를 확인한다.
- 실패 테스트로 DTO, 조립, state DAG, FREEZE 불변 조건, 권한 경계를 고정한 후
  구현한다.
- receipt 컬럼/테이블, `binding` UPDATE, DB trigger/RLS/function, 새 dependency를 추가하지 않는다.
- C1/C2-a replay·audit exact-field 호환성을 깨지 않는다.
- 구현 중 설계와 계약이 충돌하면 새 semantics를 추정하지 말고 Issue `#513`의
  담당자와 단일 책임 리뷰어에게 올린다.
- 구현 PR에 영향 도메인, 지정 리뷰어, 제한 login 테스트 증거, 미실행/skip 검사,
  effective enforcement 비활성 상태를 명시한다.
