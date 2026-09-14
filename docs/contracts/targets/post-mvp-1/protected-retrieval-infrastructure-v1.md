# Protected Retrieval Infrastructure 계약 v1

> 상태: Approved Target · Partially implemented
>
> 근거 Decision: `PD-368-20260909`; authorization control decision `PD-368-R1` (PR #463 완료); C2 command 및 권한 확장 candidate `PD-368-R2`

이 계약은 protected retrieval의 PostgreSQL data-plane 실행 경계와 control-plane 구현 범위를
분리한다. 현재 실행 가능한 공개 계약이나 실제 protected 환경 활성화 승인이 아니다.

## 구현된 data-plane 경계

- Runtime은 data 전용 제한 로그인으로 시작하며, 시작 시와 각 transaction에서 `session_user`, data/control
  role membership, schema/table/column 권한을 검증한다. control role membership이나 예상 밖 권한이 있으면
  시작과 작업을 거부한다.
- READ/RUN은 실제 envelope SHA-256, 저장된 `envelope_sha256`, capability의 승인된
  `protected_artifact_sha256`이 모두 같은 경우에만 내용을 callback에 전달한다. 불일치하면 callback은
  호출되지 않는다.
- 작업 준비 transaction은 Dataset·grant·operation history를 검증하고 `INTENT`와 단일 사용 capability 소비를
  commit한 뒤에만 보호 내용을 읽거나 전달한다.
- 실행 transaction은 현재 Dataset·grant를 다시 검증하고 잠근 뒤 작업과 `SUCCEEDED`를 함께 commit한다.
- callback 실패, 연결 단절 또는 결과 확정 불가 시 실행 transaction은 rollback하고 별도 transaction으로
  `UNKNOWN`을 기록한다. 이 기록마저 실패해도 먼저 commit된 `INTENT`가 남아 같은 operation의 자동 재실행을
  차단한다.
- artifact·audit row가 존재하는 downgrade는 거부하며 데이터를 삭제하지 않는다. protected Alembic graph는
  단일 head를 유지한다.

## 구현 완료된 authorization control-plane (C1)

`PD-368-R1`에 따라 PR #463에서 다음 C1 Application Service 구현 및 통합 테스트가 완료되었다:

- approval evidence ingestion
- grant/revoke/expire
- `session_user` 기반 control identity/approval role 결속
- command payload hash와 request ID 기반 exact replay/conflict
- original executor 및 선행 AUTHORIZATION audit reference에 결속된 exact replay
- 성공 mutation과 AUTHORIZATION·CONTROL global hash-chain audit의 단일 transaction
- `GRANT → REVOKE|EXPIRE` 단일 terminal 수명주기와 lock 이후 DB UTC 재확인
- 신뢰 가능한 거부의 CONTROL audit 선행 commit과 고정 오류 재현
- DATA/CONTROL identity shape, subject/Dataset scope grant revision, C1 column privilege와 DATA audit-kind 격리

Command/결과/audit 필드, 승인자 분리, lock order는
[`PD-368-R1`](../../../governance/decisions/2026-09-11-protected-retrieval-authorization-control.md)을 따른다.

## 계약 확정된 C2 control-plane 및 최소 권한 확장 경계

[`PD-368-R2`](../../../governance/decisions/2026-09-14-protected-retrieval-c2-command-and-privilege-expansion.md)는
남은 control-plane 범위(C2)의 계약과 최소 권한 확장 기준을 다음과 같이 고정한다:

- Identity 등록(`RegisterIdentityCommand`) 및 비활성화(`DisableIdentityCommand`) 계약
- Dataset 등록(`RegisterDatasetCommand`), 일반 전이(`TransitionDatasetCommand`), 불변 고정(`FreezeDatasetCommand`) 계약
- FREEZE 전용 전이 분리: `TransitionDatasetCommand`로 `to_state=FROZEN` 우회 시도 차단(`ROLE_ACTION_STATE_DENIED`), `FreezeDatasetCommand`에서만 40건·전수 검토·4축 0 누출·`FreezeApprovalSourceEvidence` 결속 및 `freeze_receipt_ref` 생성 불변 조건을 강제
- C2 DTO 형상 확정: `ControlCommandResult` 및 `ControlCommandAuditEntry`의 C2 분기(target_id 형식, authorization_audit_event_id=None 강제), `FreezeApprovalSourceEvidence` DTO 정의 및 replay 회귀 방지
- 승인자 분리: Identity는 `PRODUCT_SAFETY_REVIEWER`, Dataset 등록/전이는 `DATASET_CUSTODIAN`, FREEZE는 Independent `DATASET_CUSTODIAN` (송은영 구현 참여 시 김지혜 담당) + Safety Reviewer 승인 evidence 결속
- 최소 권한 확장: `protected_identity` INSERT 및 `enabled` UPDATE, `protected_dataset` INSERT 및 lifecycle 컬럼 UPDATE
- 음성 테스트 보존: identity/dataset 불변 식별자·해시 UPDATE 거부 단언 및 data-plane envelope/capability/OPERATION 감사 격리 단언 유지

## 구현 완료 및 남은 범위

- C2-a Identity control-plane Application Service 구현: #512(#522) 병합으로 구현 완료
- C2-b Dataset lifecycle 및 FREEZE Application Service 구현: #513 구현 완료 (Freeze receipt는 성공한 `DATASET_FROZEN` CONTROL 감사 이벤트 식별자를 참조)
- production trusted approval source connector와 protected 환경 provisioning (`EXT-PRIV-001`): 미구현 / unprovisioned

위 production connector 및 protected 환경 provisioning은 외부 gate로 남는다. Issue #368은 Open이며 이 계약은 `current/`로 승격하지 않고 `targets/` 상태를 유지한다.

## 활성화 상태

- Repository adapter: `PARTIALLY_IMPLEMENTED`
- Effective enforcement: `NOT_IMPLEMENTED`
- HOLDOUT authorization: `NOT_RECORDED`
- HOLDOUT authored: `0`
- Freeze/Runner execution: blocked
- Disposal: `BLOCKED_BY_ISSUE_425`
- Production approval source and protected environment: unprovisioned
- Production/publication: blocked by `EXT-PRIV-001` and the Track F external gate

Issue #425의 disposal audit 계약은 실제 폐기 활성화 조건이지만, 이 data-plane 부분 구현의 선행조건은 아니다.

## 책임 검토

- 구현: 정현우 (`@ceohwj`)
- 책임 리뷰: 권가빈 (`@hazelnutflavoured`) — 단일 책임 리뷰어 (Product·Privacy·Safety·Evaluation, command 계약 및 권한 확장 타당성)
