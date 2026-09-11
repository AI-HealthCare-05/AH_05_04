# Protected Retrieval Infrastructure 계약 v1

> 상태: Approved Target · Partially implemented
>
> 근거 Decision: `PD-368-20260909`

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

## 미구현 control-plane 범위

다음 Application Service는 이 부분 구현에 포함되지 않는다.

- approval evidence ingestion
- grant/revoke/expire와 identity disable
- Dataset lifecycle transition과 FREEZE

해당 서비스의 command, 승인자 분리, 멱등성, 상태 전이 및 감사 계약은 담당자 합의와 구현·통합 테스트가
필요하다. 구현 전까지 Issue #368은 Open이며 이 계약은 `current/`로 승격하지 않는다.

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
