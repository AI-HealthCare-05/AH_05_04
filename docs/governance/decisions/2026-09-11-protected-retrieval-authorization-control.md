# Product Decision Candidate: Protected Retrieval Authorization Control

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-368-R1` |
| 상태 | Candidate · Coordination Confirmed · PR Review Required |
| 조율 확인 | 구현 담당자가 2026-09-11 현재 작업에서 영향 도메인 협의 완료를 확인 |
| 선행 Decision | `PD-368-20260909` |
| 구현 | 정현우 (`@ceohwj`) |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Product·Privacy·Safety·Evaluation |
| Backend·Security·DB | 송은영 (`@phina-io`) |
| 추적 | Issue #368; PR #373·#386·#432 |

이 문서는 이미 승인된 Protected Retrieval 목표 중 PR #432가 의도적으로 남긴 authorization control-plane
구현 계약을 구체화한다. 영향 도메인 협의 완료는 구현 착수 근거이며, 이 문서와 구현의 최종 승인 증거는
지정 리뷰어의 GitHub PR review event다. 이 문서만으로 protected 환경, HOLDOUT 접근, Freeze, 실행, 외부 승인,
Current 승격 또는 Production 공개가 승인되지 않는다.

## 1. 범위

포함:

- 신뢰 승인 원본에서 approval evidence ingestion
- authorization GRANT, REVOKE, EXPIRE
- control login과 approval identity 결속
- command payload hash 기반 멱등성과 conflict
- AUTHORIZATION 및 CONTROL 감사의 원자적 기록
- C1에 필요한 identity shape, grant revision uniqueness, audit enum 및 최소 권한

제외:

- identity 등록·비활성화
- Dataset 등록·상태 전이와 FREEZE
- 실제 approval source connector·credential·protected 환경 provisioning
- HOLDOUT 작성·열람·Freeze·실행, 보존·폐기, Release·Production

## 2. Command와 성공 결과

모든 `request_id`와 `grant_id`는 canonical lowercase UUIDv4다. digest는 lowercase SHA-256 hex다.

| Command | 필드 |
| --- | --- |
| `IngestApprovalCommand` | `request_id`, `source_event_id`, `expected_raw_sha256` |
| `GrantAuthorizationCommand` | `request_id`, `grant`, `expected_dataset_state_revision` |
| `RevokeAuthorizationCommand` | `request_id`, `grant_id`, `approval_source_event_id`, `expected_raw_sha256`, `expected_effective_revision` |
| `ExpireAuthorizationCommand` | `request_id`, `grant_id`, `expected_effective_revision` |

`ControlCommandResult`는 성공만 표현하며 `request_id`, `command_kind`, opaque `target_id`, nullable
`effective_revision`, nullable `authorization_audit_event_id`, 그리고
`APPROVAL_VERIFIED|AUTHORIZED|REVOKED|EXPIRED` 중 하나인 `reason_code`만 반환한다. 거부는 결과 객체를 반환하지
않고 고정 `ProtectedSecurityError`를 발생시킨다.

## 3. 승인 원본과 역할 분리

Application Service는 caller가 제출한 evidence body를 신뢰하지 않는다. `TrustedApprovalSource`에서
`source_event_id`로 immutable `ApprovalSourceEvidence`를 읽고 command의 expected raw hash와 대조한 뒤 정확한
DTO를 저장한다. 실제 production connector는 이 범위에서 선택하지 않는다.

- Custodian subject grant issuer는 `PRODUCT_SAFETY_REVIEWER`다.
- Author와 Runner subject grant issuer는 `DATASET_CUSTODIAN`이다.
- issuer가 subject 또는 control implementation participant면 거부한다.
- GRANT/REVOKE 실행 control identity는 승인 evidence issuer와 actor·approval role이 같아야 한다.
- EXPIRE는 DB UTC가 `expires_at` 이상일 때 enabled control identity가 실행할 수 있다. AUTHORIZATION 감사는
  원 grant issuer를 보존하고 CONTROL 감사가 실제 executor를 기록한다.

## 4. Identity와 revision

`protected_identity`는 `identity_plane=DATA|CONTROL`을 가진다. DATA row는 `principal_role`만, CONTROL row는
`approval_role=DATASET_CUSTODIAN|PRODUCT_SAFETY_REVIEWER`만 가진다. 기존 row는 DATA로 backfill한다. 한 actor는
plane별 최대 한 row를 가지며 database login은 계속 primary key다.

GRANT는 subject actor/namespace/role + Dataset ID/version scope의 현재 최대 revision 다음 값만 허용한다. DB
UNIQUE constraint가 동일 scope/revision의 동시 삽입을 거부한다. REVOKE와 EXPIRE는 caller가 지정한 current
effective revision과 일치할 때만 이를 1 증가시킨다.

## 5. CONTROL 감사와 멱등성

`ProtectedAuditEventKind`에 `CONTROL`을 추가한다. `ControlCommandAuditEntry`는 다음 필드를 hash-chain body에
포함한다.

- `event_kind`, global `sequence`, `event_id=request_id`
- `command_kind=INGEST_APPROVAL|GRANT|REVOKE|EXPIRE`
- `executed_by`, `target_kind`, opaque `target_id`
- `command_sha256`, `outcome=SUCCEEDED|DENIED`
- nullable `result_effective_revision`, nullable `authorization_audit_event_id`
- fixed `reason_code`, DB UTC `recorded_at`, `previous_entry_sha256`, `entry_sha256`

command hash는 command kind와 strict command DTO 전체의 canonical JSON SHA-256이다. 같은 request ID와 같은
hash는 기존 성공 결과 또는 기존 거부 오류를 재현하고 새 mutation/audit를 만들지 않는다. 같은 request ID와
다른 hash는 새 고정 이유 `CONTROL_COMMAND_CONFLICT`로 거부한다. 별도 mutable command table은 만들지 않는다.

## 6. Transaction과 거부

승인 원본을 쓰는 command는 먼저 짧은 read-only transaction으로 control `session_user`와 exact ACL을 검증한
뒤 외부 원본을 읽는다. mutation transaction은 identity와 ACL을 다시 검증하고 replay를 확인한 뒤
Dataset → grant scope/target → audit head 순서로 lock한다.

GRANT/REVOKE/EXPIRE 성공은 mutation, AUTHORIZATION audit, CONTROL audit, audit head 갱신을 한 transaction에서
commit한다. ingestion은 evidence insert, CONTROL audit, head 갱신을 함께 commit한다. 알려진 정책 거부는
mutation/domain audit 없이 CONTROL DENIED만 commit한 후 transaction 밖에서 같은 고정 오류를 발생시킨다.
identity 또는 audit integrity 자체를 신뢰할 수 없으면 어떤 mutation도 하지 않으며 denial audit 부재를
성공으로 표현하지 않는다.

동시 duplicate가 audit event ID UNIQUE에서 패하면 전체 transaction을 rollback하고 새 transaction으로
winner의 global chain과 command hash를 검증한 뒤 결과를 재현한다.

## 7. 최소 권한

control role은 identity·Dataset·evidence·grant·audit의 필요한 column SELECT, approval evidence·grant·audit의
column INSERT, grant effective revision/revocation/lock marker, Dataset lock marker, audit head만 UPDATE할 수 있다.
PR #432가 C2를 위해 미리 열어 둔 identity INSERT/disable과 Dataset lifecycle INSERT/UPDATE는 C1에서 회수한다.
data role 권한은 바꾸지 않는다.

DB function, procedure, trigger, RLS, DELETE, TRUNCATE, schema CREATE, blanket table INSERT 또는 새 dependency를
추가하지 않는다.

## 8. 상태와 승인 조건

C1 구현·테스트가 완료돼도 계약은 target에 남고 다음 상태를 유지한다.

- effective enforcement: `NOT_IMPLEMENTED`
- HOLDOUT authorization: `NOT_RECORDED`
- HOLDOUT authored: `0`
- Dataset lifecycle/FREEZE/Runner execution: blocked
- production approval source와 protected environment: unprovisioned

병합에는 권가빈과 송은영의 지정 범위 승인이 필요하다. 송은영이 ACL 구현자로 참여하는 경우 independent
Dataset Custodian approval은 김지혜(`@Jye-rookie`)가 담당한다.
