# Issue #273 Phase B3 Protected Runner Foundation

> 이 문서는 인프라 독립 policy foundation의 공개 증빙입니다.
> 실제 접근 통제, HOLDOUT 접근 승인, Freeze, Retrieval 실행 또는 Release 완료 증빙이 아닙니다.

## 현재 상태

- Phase: `PHASE_B3_PROTECTED_RUNNER_FOUNDATION`
- Protected Runner Issue: [#368](https://github.com/AI-HealthCare-05/AH_05_04/issues/368) (`OPEN` at capture)
- Issue API resource ID: `I_kwDOT3EWNs8AAAABQTH1bg`
- Issue canonical subset SHA-256: `3b2ec85d392179b444295d9a7cea3f04d49be9cc5f0e77f933ecb4e3ec8723c6`
- Dataset: `rag-natural-language-retrieval-dev@1.0.0` (`DRAFT`)
- Dataset manifest SHA-256: `b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2`
- Policy foundation: `IMPLEMENTED`
- Issue completion: `IN_PROGRESS`
- Effective enforcement: `NOT_IMPLEMENTED`
- Infrastructure adapter: `NOT_IMPLEMENTED`
- Reconciliation adapter: `NOT_IMPLEMENTED`
- HOLDOUT: `0`; access authorization: `false`; Freeze: `false`; actual run: `NOT_CREATED`
- Release eligible: `false`; Production remains closed.

## 역할 분리

- 구현 담당: 정현우 (`@ceohwj`)
- Product·Privacy·Safety·Evaluation 검토: 권가빈 (`@hazelnutflavoured`)
- Dataset Custodian·Backend·Security 검토: 송은영 (`@phina-io`)
- `@phina-io`가 실제 ACL 구현에 참여하면 독립 Custodian은 김지혜 (`@Jye-rookie`)로 전환합니다.

## 구현된 Foundation

- 승인 원문 provenance 검증 및 self-approval 차단
- 역할·Dataset 상태·artifact digest·grant revision에 결속된 fail-closed authorization
- authorization/operation 감사 이벤트 분리와 global append-CAS hash chain
- single-use capability, revocation guard, 성공 결과 멱등 반환과 UNKNOWN 자동 재실행 차단
- UNKNOWN 독립 승인 reconciliation adapter는 아직 구현하지 않음
- 실제 저장 위치를 노출하지 않는 random UUIDv4 logical reference
- production CLI에 등록되지 않은 synthetic adapter 검증

## 실제 인프라 결정 요청

- `APPROVE_DATABASE_OR_SCHEMA_BOUNDARY`
- `APPROVE_OWNER_AUTHOR_CUSTODIAN_RUNNER_ROLES`
- `APPROVE_PROTECTED_CREDENTIAL_ENVIRONMENT`
- `APPROVE_APPEND_ONLY_AUDIT_AND_RETENTION`
- `APPROVE_BACKUP_REVOKE_INCIDENT_RESPONSE`

위 결정과 독립 승인이 기록되기 전에는 PostgreSQL migration, credential, protected loader와
`run-protected-holdout`을 구현하거나 HOLDOUT 작성을 시작하지 않습니다.

## 남은 Blocker

- `BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER`
- `BLOCKED_BY_RAG_14_ADAPTER`
- `WAITING_FOR_HOLDOUT_ACCESS_AUTHORIZATION`
- `WAITING_FOR_HOLDOUT_FREEZE`

Captured at `2026-09-09T00:00:00.000000Z`. Foundation self hash: `72394b0cb63d035005830fb6d8abb811d4caba09e9ff12a52f78a29c4261636c`
