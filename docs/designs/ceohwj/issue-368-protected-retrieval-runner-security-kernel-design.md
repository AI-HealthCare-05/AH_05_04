# Issue #368 Protected Retrieval Runner Security Kernel 설계

## 상태와 결정

- 상위 평가: Issue `#273`
- 구현 Issue: `#368`
- 구현 브랜치: `368-protected-retrieval-runner`
- 구현 담당: 정현우 (`@ceohwj`)
- Product·Privacy·Safety·Evaluation 검토: 권가빈 (`@hazelnutflavoured`)
- Dataset Custodian·Backend·Security 검토: 송은영 (`@phina-io`)
- 역할 분리 예외: `@phina-io`가 실제 접근 통제를 구현하면 김지혜 (`@Jye-rookie`)가 독립 Dataset Custodian 승인을 담당한다.
- 선택 범위: 인프라 독립 fail-closed security kernel, synthetic adapter, 공개 가능한 B3 증빙
- 제외 범위: 실제 PostgreSQL schema·role·credential·보호 경로, HOLDOUT 작성·Freeze·실행

이 설계는 실제 저장 인프라를 추정하지 않는다. protected operation의 승인·역할·감사 순서를 저장 방식과
분리하여 검증 가능한 kernel로 고정하고, 실제 PostgreSQL 또는 다른 보호 저장 adapter는 Dataset Custodian과
Security 검토 이후 후속 PR에서 연결한다.

## 문제와 완료 경계

PR #366은 접근 통제의 선행조건을 정의했지만 실제 실행 경계는 없다. 현재 공통 Evaluation CLI는 사용자가
지정한 `--executed-by` 문자열과 repository-root DEV manifest를 받아 `run-dev`만 수행한다. 이 CLI에 HOLDOUT
명령을 바로 추가하면 인증된 principal, authorization receipt, 보호 storage, append-only audit가 없는 상태에서
실행 표면만 먼저 열리게 된다.

이번 단계의 완료는 다음만 의미한다.

- Issue #368 생성 사실을 공개 B3 evidence로 기록한다.
- 보호 작업을 호출하는 application service가 따라야 할 fail-closed kernel 계약을 구현한다.
- 승인 실패, 역할 위반, 감사 실패, 실행 예외가 protected operation을 열거나 성공으로 표시하지 않음을 검증한다.
- 실제 adapter가 없으므로 `BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER`와 접근 승인 blocker를 유지한다.

완료는 ACL 구성, 접근 승인, HOLDOUT 작성·Freeze, `run-protected-holdout`, 실제 Retrieval, Metric 또는 Release를
뜻하지 않는다.

## 검토한 접근

### 1. 현재 CLI에 `run-protected-holdout` 직접 추가

구현량은 작지만 신뢰할 수 있는 principal과 storage adapter가 없다. 문자열 actor와 일반 경로를 받는 순간
보호 경계를 우회할 수 있으므로 채택하지 않는다.

### 2. PostgreSQL schema·role·audit까지 한 번에 구현

프로젝트의 기존 PostgreSQL 및 append-only audit 패턴을 재사용할 수 있다. 그러나 보호 database/schema,
운영 role, secret 주입, migration과 배포 책임이 승인되지 않았다. 지금 구현하면 Backend·Security 공유 계약을
추정하게 되므로 후속 단계로 둔다.

### 3. 인프라 독립 security kernel과 synthetic adapter 우선 구현 — 채택

승인·역할·감사·실행 순서를 Protocol 경계로 고정한다. kernel은 원문 bytes나 경로를 입력·출력하지 않고,
실제 operation은 보호 adapter 내부에서만 수행한다. DB 방식이 확정되면 kernel을 변경하지 않고 verifier,
journal, operation adapter를 연결할 수 있다.

## 모듈 구조

### `protected_retrieval.py`

저장소나 CLI에 의존하지 않는 domain/application kernel이다.

- `ProtectedAction`: `READ | WRITE | FREEZE | RUN`
- `ProtectedPrincipalRole`: `HOLDOUT_AUTHOR | DATASET_CUSTODIAN | PROTECTED_RUNNER`
- `ProtectedApprovalRole`: control-plane grant issuer 전용
  `DATASET_CUSTODIAN | PRODUCT_SAFETY_REVIEWER`. protected operation 권한은 부여하지 않음
- `ProtectedAuditEventKind`: `AUTHORIZATION | OPERATION`
- `AuthorizationAuditAction`: `GRANT | REVOKE | EXPIRE`
- `OperationAuditOutcome`: `DENIED | INTENT | SUCCEEDED | UNKNOWN`
- `ProtectedDatasetState`: `ACCESS_AUTHORIZED | AUTHORING | REVIEW_READY | FROZEN`
- `ProtectedPrincipal`: 인증 adapter가 제공하는 actor ID, namespace와 역할 전체
- `OpaqueLogicalRef`: `REQUEST | DATASET | HOLDOUT_SET | RUN_RESULT | AUDIT_EVENT` namespace와
  content-independent random UUIDv4만 허용하는 typed ref. URI·경로·free-form label은 받을 수 없으며,
  UUID와 실제 protected object의 대응표도 보호 저장소 밖으로 내보내지 않음
- `ControlImplementationBinding`: ACL implementation commit·artifact hash와 구현 참여 actor 전체
- `VerifiedAuthorizationApproval`: immutable approval source event와 구현 binding을 검증한 뒤에만 생성되는 값
- `ApprovalEvidenceVerifier`: source event ID, actor·state·timestamp, target commit·artifact hash,
  canonical raw hash와 implementation participant 전체를 신뢰 source에서 대조함
- `ProtectedDatasetBinding`: Dataset ID/version/manifest hash, protected artifact digest, HMAC key version,
  state, monotonic state revision, 상태별 review/leakage/Freeze/execution evidence refs
- `ProtectedOperationRequest`: request ID, idempotency key, Dataset binding, opaque target ref, action, principal
- `ProtectedAuthorizationGrant`: grant ID/revision, subject actor ID·namespace·`ProtectedPrincipalRole` 전체,
  Dataset ID/version/hash, protected artifact digest, HMAC key version, 허용 action,
  issuer actor ID·namespace·`ProtectedApprovalRole`,
  control implementation binding, authorization receipt id/version/raw hash, 승인 시각과 만료 시각
- `ProtectedAuthorizationCapability`: request·grant revision·Dataset state revision·artifact digest·action·target,
  single-use nonce와 짧은 expiry를 결속한 operation 전용 capability
- `ProtectedOperationResult`: protected content가 아닌 logical result ref와 비민감 reason code만 포함
- `AuthorizationAuditEntry`: `event_kind=AUTHORIZATION`, global sequence, grant ID/revision, subject·issuer 전체
  identity, implementation binding, approval receipt ref, `GRANT | REVOKE | EXPIRE`, reason, trusted UTC time,
  이전 entry hash와 self-hash
- `OperationAuditEntry`: `event_kind=OPERATION`, global sequence, operation/request key, principal, protected action,
  logical refs, grant·capability·state revision, `DENIED | INTENT | SUCCEEDED | UNKNOWN`, reason, trusted UTC time,
  이전 entry hash와 self-hash
- `ProtectedAuditEntry`: 위 두 exact-field variant의 discriminated union. variant 밖 field는 거부함
- `TrustedClock`: adapter의 신뢰 시간원. request가 제공한 시간은 만료나 audit에 사용하지 않음
- `AuthorizationLedger`: `VerifiedAuthorizationApproval`만 받아 grant/revoke를 global audit CAS와 같은
  transaction에서 효력화하고 current revision 조회
- `AuthorizationGuard`: operation 동안 revocation/state lock 또는 동등한 transaction을 유지하고 single-use
  capability를 발급·소비하는 async context manager
- `ProtectedAuditJournal`: global monotonic sequence와 durable head에 대한 append-CAS, operation-key history 제공.
  update/delete API가 없음
- `ProtectedOperation`: guard 안에서 capability를 필수 입력으로 받아 실행하고, operation key 기준
  `observe/reconcile`와 비민감 result만 제공
- `execute_protected_operation(...)`: 승인·감사·실행을 순서대로 조정하는 유일한 진입점

공유 Evaluation schema와 `ActorRole`, `EvaluationErrorCode`는 변경하지 않는다. 위 타입은 실제 인프라가
확정되기 전의 Issue-local Python 계약이며 portable JSON schema나 공개 runtime API가 아니다.

### `protected_retrieval_synthetic.py`

실제 저장소를 흉내 내지 않고 kernel Protocol을 exact-match해 행동을 검증하는 in-memory adapter다.

- global audit append-CAS와 같은 critical section에서 `GRANT | REVOKE | EXPIRE` 및 revision을
  원자적으로 변경하는 in-memory `AuthorizationLedger`
- 고정된 synthetic immutable approval source를 대조하고 forged/rehashed/participant-omitted approval을
  거부하는 in-memory `ApprovalEvidenceVerifier`
- revocation revision, Dataset state revision과 artifact digest를 lock하고 single-use capability를 발급·소비하는
  in-memory `AuthorizationGuard`
- global monotonic sequence와 durable head를 모사하는 append-CAS journal
- operation key별 멱등 실행, side-effect 관찰과 `observe/reconcile`을 제공하는 synthetic operation

이 모듈은 production registry나 CLI에 등록하지 않는다. HOLDOUT 본문, Gold, 경로, credential을 fixture로
사용하지 않는다.

### B3 공개 evidence

기존 `holdout-freeze-preparation.json`은 PR #366 시점의 immutable preparation snapshot이므로 다시 쓰지 않는다.
대신 아래 artifact를 추가한다.

- `docs/validation/rag/issue-273/protected-runner-foundation.json`
- `docs/validation/rag/issue-273/protected-runner-foundation.md`

artifact는 다음을 exact-field로 고정한다.

- `phase=PHASE_B3_PROTECTED_RUNNER_FOUNDATION`
- Issue #368의 source repository, API resource ID, number, URL, created-at, evidence captured-at,
  `state_at_capture=OPEN`과 stable canonical source subset raw SHA-256
- `policy_foundation_status=IMPLEMENTED`
- `issue_completion_status=IN_PROGRESS`
- `effective_enforcement_status=NOT_IMPLEMENTED`
- `infrastructure_adapter_status=NOT_IMPLEMENTED`
- `access_authorized=false`, `holdout_authored=false`, `freeze_recorded=false`
- 구현자·두 책임 검토자·기본 Dataset Custodian과 조건부 대체 Custodian
- 실제 인프라 결정을 PR에서 요청한다는 후속 gate
- Dataset manifest와 PR #366 preparation raw/self hash
- artifact self-hash

`status.json`과 `report.md`는 Issue-local validation status projection version을 `1.2.1`, phase를 B3로
전이하고 새 artifact raw/self hash를 결속한다. 이 `schema_version`은 Evaluation Schema Set version이 아니다.
Schema Set은 별도 `schema_set_ref=1.3.0`, `schema_set_status=REVIEW_REQUIRED`를 그대로 유지한다. DEV 60,
HOLDOUT 0, Dataset `DRAFT`, Adapter `NOT_IMPLEMENTED`, actual run 없음,
`release_eligible=false`는 유지한다. Issue 생성으로 충족된 gate만 제거하며 protected infrastructure,
authorization, #178 Adapter와 Freeze blocker는 남긴다.

## 역할과 권한 행렬

| 역할·작업 | 허용 상태와 필수 evidence |
| --- | --- |
| `HOLDOUT_AUTHOR / READ·WRITE` | `ACCESS_AUTHORIZED` 또는 `AUTHORING`, unfrozen, current authorization revision |
| `DATASET_CUSTODIAN / READ` | `ACCESS_AUTHORIZED`, `AUTHORING`, `REVIEW_READY`, `FROZEN` 중 하나, 독립적으로 발급된 Custodian access grant |
| `DATASET_CUSTODIAN / FREEZE` | `REVIEW_READY`, authored 40, 전수 검토, 네 leakage 축 0 evidence |
| `PROTECTED_RUNNER / READ·RUN` | `FROZEN`, Freeze Receipt, execution authorization, #178 Adapter·Index·config binding |

kernel은 표에 없는 역할·action·상태 조합을 항상 거부한다. WRITE는 Freeze 뒤 거부하고, FREEZE는 40개
작성·전수 검토·네 축 intersection 0 evidence 중 하나라도 없으면 거부하며, Runner READ/RUN은 Freeze Receipt,
별도 execution authorization, #178 Adapter·Index·config binding 중 하나라도 없으면 거부한다. 첫 검증부터
operation commit까지 Dataset state revision과 protected artifact digest가 같아야 한다.

Author와 Runner grant는 `ProtectedApprovalRole.DATASET_CUSTODIAN`이 발급하되 issuer는 subject 및 control implementation participant와
달라야 한다. 기본 Custodian `@phina-io` 자신의 READ/FREEZE access grant는 `@hazelnutflavoured`가
`ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER` 역할로 독립 발급한다. 실제 ACL 구현 participant에 `@phina-io`가 포함되면 Author와
Runner grant issuer 및 Dataset 최종 승인 actor를 `@Jye-rookie`로 전환한다. grant는 subject/issuer의 actor ID,
namespace, role 전체를 exact-match하며 actor ID만 같은 다른 namespace·role을 허용하지 않는다.

실제 principal은 CLI 인자로 받지 않는다. 후속 infrastructure adapter가 DB `current_user/session_user` 또는
동등하게 인증된 service identity에서 파생해야 한다. synthetic adapter가 문자열 principal을 받는 것은 테스트
전용이며 production registration을 금지한다.

## 실행 순서와 fail-closed 규칙

1. request의 UUID/idempotency key, typed opaque refs, principal, Dataset binding과 역할·action·state predicate를
   `TrustedClock.now_utc()` 기준으로 검증한다.
2. journal에서 operation key의 history를 읽는다. 기존 `INTENT | UNKNOWN`이 있으면 자동·새 request ID 재시도를
   모두 `RECONCILIATION_REQUIRED`로 거부한다. 기존 `SUCCEEDED`면 기록된 비민감 result만 반환한다.
3. `AuthorizationGuard`를 획득한다. guard가 current grant, revocation revision, Dataset state revision과 artifact
   digest를 같은 보호 transaction/lease에서 고정하지 못하면 adapter 등록 또는 실행을 거부한다.
4. `ApprovalEvidenceVerifier`가 immutable source event ID를 기준으로 actor·state·timestamp, target
   commit·artifact hash, canonical raw hash와 implementation participant 전체를 신뢰 source에서 다시 읽어
   exact-match하고 `VerifiedAuthorizationApproval`을 발급한다. caller가 구성한 receipt나 단순 재해시 값은
   ledger 입력으로 받을 수 없다. 이어 grant의 subject/issuer 전체 identity, Dataset ID/version/hash,
   artifact digest, HMAC key version, action과 `valid_from <= now < expires_at`을 exact-match한다.
5. 거부는 비민감 `DENIED` audit를 global-head CAS로 append한다. append 실패 여부와 무관하게 operation은 호출하지 않는다.
6. 승인된 요청은 guard 안에서 `INTENT`를 durable append-CAS한다. 실패하면 operation을 호출하지 않는다.
7. guard가 request·grant revision·state revision·digest·action·target·nonce·expiry에 결속된 single-use capability를
   발급한다. operation은 이 capability 없이는 호출될 수 없다.
8. operation은 같은 guard/transaction 안에서 capability와 revocation/state revision을 재검증·소비하고 수행한다.
   보호 원문은 kernel로 반환하지 않는다.
9. 성공하면 같은 guard 안에서 `SUCCEEDED`를 append-CAS하고 비민감 result를 반환한다.
10. operation 또는 terminal audit가 불확실하면 `UNKNOWN`을 append한다. journal까지 불가하면
    `AUDIT_UNAVAILABLE`만 반환하고 operation key를 reconciliation 대상으로 유지한다.
11. `UNKNOWN`은 `observe/reconcile(operation_key)`로 실제 side effect와 durable audit head를 대조하고 독립 승인을
    기록하기 전에는 재실행할 수 없다.

exception message, raw query, Evidence/Gold body, filesystem/object key, SQL, credential과 HMAC/fingerprint 값은
request, result, audit, 오류 메시지에 포함하지 않는다.

## Hash-chain audit

각 audit variant는 canonical JSON에서 `entry_sha256`을 제외해 self-hash를 계산하고 journal 전체의 이전 entry hash를
`previous_entry_sha256`으로 결속한다. 첫 entry는 `null`이다. global monotonic sequence와 durable head에 대해
compare-and-swap append하며 journal은 다음을 검사한다.

- authorization variant lifecycle: `GRANT → REVOKE | EXPIRE`. grant/revoke는 해당 audit CAS 성공과 같은
  transaction에서만 효력이 생김
- operation outcome 전이: `DENIED` 단독 또는 `INTENT → SUCCEEDED | UNKNOWN | DENIED`
- authorization variant에는 protected action/outcome을, operation variant에는 grant lifecycle action을 넣지 못함
- 이전 hash 정확 일치
- sequence 연속성, request/operation key/principal/action/target/receipt/state revision 결속 불변
- UTC microsecond timestamp 형식
- duplicate event ID와 duplicate terminal outcome 거부

synthetic journal의 hash chain은 전이·tamper 검증용이며 실제 durable retention을 증명하지 않는다. 실제
infrastructure adapter는 storage-level UPDATE/DELETE/TRUNCATE 차단, journal 전체의 global sequence와 필요 시
그에 종속된 partition sequence, durable
head/checkpoint, backup과 retention 증빙을 별도로 제출해야 한다. kernel은 audit 저장 성공을 추정하지 않고
`append()`가 반환한 확정 entry만 기록된 것으로 취급한다.

## Grant와 revoke 경계

grant/revoke 요청은 operation request와 다른 control-plane 명령이다. 둘 다 grant ID/revision, subject 전체
identity, Dataset ID/version/hash, 허용 action, issuer 전체 identity, control implementation ref,
authorization receipt id/version/raw hash, trusted recorded time과 고정 reason code를 기록한다. ledger는
`ApprovalEvidenceVerifier`가 발급한 `VerifiedAuthorizationApproval`만 받으며 caller가 직접 구성한 receipt,
source event를 다시 직렬화해 만든 hash, participant가 누락된 binding은 모두 거부한다.

- grant는 `GRANT` authorization audit와 ledger revision 변경이 원자적으로 성공한 뒤에만 유효하다.
- revoke는 `REVOKE` authorization audit와 revision 증가가 원자적으로 성공한 뒤에 즉시 새 capability 발급을 막는다.
- expiry는 trusted clock으로 판정하고 최초 관찰 시 `EXPIRE` authorization audit를 append한다.
- issuer가 subject 또는 control implementation participant이면 거부한다.
- revoked/expired revision으로 생성된 capability는 guard 안에서 소비할 수 없다.

## 공개·비공개 경계

Repository에 허용:

- Issue #368 공개 metadata
- kernel version/hash
- Dataset 및 기존 preparation immutable ref/hash
- 상태·수량·역할·고정 reason code
- 테스트 수와 검증 명령

Repository에 금지:

- HOLDOUT 질문·Gold·negative label
- 실제 authoring identity digest와 fingerprint/HMAC 값
- credential, key material, authorization token
- protected database/schema/table/bucket/root/object key
- operation exception과 query/Evidence content
- 실제 authorization receipt body

## 테스트 전략

테스트는 실제 kernel 행동을 호출하며 다음 mutation을 잡는다.

- 역할 행렬의 허용·거부 조합
- grant 없음, subject/Dataset/action/receipt mismatch, 만료, self-approval
- control implementation participant가 승인한 grant 및 actor ID만 같고 namespace/role이 다른 grant
- approval source의 actor/state/timestamp/target commit/artifact hash mismatch, forged receipt, 단순 rehash,
  implementation participant omission을 `ApprovalEvidenceVerifier`가 거부함
- synthetic `AuthorizationLedger`를 실제 호출해 grant/revoke audit 실패 시 ledger revision이 변하지 않음
- `ACCESS_AUTHORIZED | AUTHORING`이 아닌 상태의 WRITE, evidence 없는 FREEZE, 미동결 또는 binding 없는 RUN
- INTENT audit 실패 시 operation 미호출
- synthetic `AuthorizationGuard`를 실제 호출해 guard 획득/atomic capability 미지원 adapter 등록 거부와
  guard 안 revoke 시 operation 미호출
- Dataset state revision·artifact digest 변경 시 operation 미호출
- operation 예외와 success-audit 실패가 성공으로 변환되지 않음
- synthetic operation의 `observe/reconcile`을 실제 호출해 `UNKNOWN` 또는 미해결 `INTENT`의 자동 retry와
  새 request ID 우회 거부
- synthetic append-CAS journal을 실제 호출해 global audit hash-chain tamper·절단, CAS 충돌,
  duplicate terminal, 잘못된 전이 거부
- authorization/operation audit variant에 상대 variant의 action·outcome field를 섞으면 exact-field 검증이 거부함
- request/grant/result/audit 및 고정 오류에 금지 key·scalar 값이 들어오면 거부
- opaque logical ref가 UUIDv4가 아니거나 URI, slash, backslash, 공백, free-form storage label을 포함하면 거부하고,
  같은 content로 생성해도 참조가 같아지지 않음을 검증
- trusted fake clock으로 UTC awareness, `valid_from <= now < expires_at`와 timestamp 단조성 검증
- synthetic adapter가 production registry/CLI에 노출되지 않음
- B3 JSON/Markdown fresh build byte equality, raw/self hash와 status/report 결속
- B3 Issue source repository·API resource ID·stable canonical subset raw hash의 exact 결속
- HOLDOUT 0, 접근 승인/Freeze/run false와 blocker 유지

검증은 focused pytest 후 전체 `ai_worker/tests/evaluation`, Ruff check/format, Mypy, `git diff --check` 순서로
수행한다.

## 후속 실제 인프라 결정

PR에서 `@phina-io`에게 다음을 결정 요청한다.

- 별도 Evaluation PostgreSQL database 또는 전용 schema
- owner/author/Custodian/Runner role과 일반 app·CI deny 정책
- credential을 주입할 protected GitHub Environment 또는 별도 실행 환경
- DB server identity/time 기반 append-only audit와 보존 기간
- backup, revoke, incident response와 접근 재검토 주기

승인 전에는 PostgreSQL migration, secret, protected path와 `run-protected-holdout`을 구현하지 않는다.

## 완료 후 상태

- protected Runner Issue: `CREATED`
- policy foundation: `IMPLEMENTED`
- #368 issue completion: `IN_PROGRESS`
- effective enforcement: `NOT_IMPLEMENTED`
- infrastructure adapter: `NOT_IMPLEMENTED`
- HOLDOUT access authorization: `NOT_RECORDED`
- HOLDOUT authored: `0`
- HOLDOUT Freeze: `NOT_STARTED`
- actual Retriever Adapter: `NOT_IMPLEMENTED`
- actual run: `NOT_CREATED`
- Release/Production: 차단 유지
