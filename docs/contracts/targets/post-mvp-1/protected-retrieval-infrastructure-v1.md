# Protected Retrieval Infrastructure 계약 v1

> 상태: Approved Target · Partially implemented
>
> 근거 Decision: `PD-368-20260909`; authorization control candidate `PD-368-R1`

이 계약은 protected retrieval의 PostgreSQL data-plane 실행 경계와 아직 구현되지 않은 control-plane 범위를
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

## 조율 완료·구현 중인 authorization control-plane

`PD-368-R1`은 다음 C1 Application Service 계약을 고정하며, 실제 구현·테스트와 지정 PR review가 완료되기
전에는 runtime 구현으로 간주하지 않는다.

- approval evidence ingestion
- grant/revoke/expire
- `session_user` 기반 control identity/approval role 결속
- command payload hash와 request ID 기반 exact replay/conflict
- original executor 및 선행 AUTHORIZATION audit reference에 결속된 exact replay
- 성공 mutation과 AUTHORIZATION·CONTROL global hash-chain audit의 단일 transaction
- `GRANT → REVOKE|EXPIRE` 단일 terminal 수명주기와 lock 이후 DB UTC 재확인
- 신뢰 가능한 거부의 CONTROL audit 선행 commit과 고정 오류 재현
- DATA/CONTROL identity shape, subject/Dataset scope grant revision, C1 column privilege와 DATA audit-kind 격리

Command/결과/audit 필드, 승인자 분리, lock order와 C2 제외 범위는
[`PD-368-R1`](../../../governance/decisions/2026-09-11-protected-retrieval-authorization-control.md)을 따른다.

## 미구현 control-plane 범위

- identity 등록·비활성화
- Dataset 등록·lifecycle transition과 FREEZE
- production trusted approval source connector와 protected 환경 provisioning

위 범위는 C2 또는 외부 gate로 남는다. Issue #368은 Open이며 이 계약은 `current/`로 승격하지 않는다.

## 활성화 상태

- Repository adapter: `PARTIALLY_IMPLEMENTED`
- Effective enforcement: `NOT_IMPLEMENTED`
- HOLDOUT authorization: `NOT_RECORDED`
- HOLDOUT authored: `0`
- Freeze/Runner execution: blocked
- Disposal: `BLOCKED_BY_ISSUE_425`
- Production/publication: blocked by `EXT-PRIV-001` and the Track F external gate

Issue #425의 disposal audit 계약은 실제 폐기 활성화 조건이지만, 이 data-plane 부분 구현의 선행조건은 아니다.

## 책임 검토

- 구현: 정현우
- Backend·Security·DB·transaction: 송은영 (`@phina-io`)
- Product·Privacy·Safety·완료 범위: 권가빈 (`@hazelnutflavoured`)
