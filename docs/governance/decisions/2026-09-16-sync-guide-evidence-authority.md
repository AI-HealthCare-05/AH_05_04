# Product Decision Candidate: Sync Guide Evidence Authority Assembly Contract (#672)

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-672-20260916` |
| 상태 | Proposed / Review pending · Issue #672 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 단일 책임 리뷰 | 김지혜 (`@Jye-rookie`) — Worker & Source Provenance / Pure Authority Assembly Seam |
| 필요 교차 리뷰 | 송은영 (`@phina-io`) — Backend / REQUEST Guard Authority / Decision Ownership / 권가빈 (`@hazelnutflavoured`) — PM·Product Acceptance·Privacy Gate |
| 추적 Issue | [#672](https://github.com/AI-HealthCare-05/AH_05_04/issues/672) |
| 상위·관련 결정 | [`PD-180-EM-20260916`](./2026-09-16-endpoint-member-authority-contract.md), [`PD-180-20260915`](./2026-09-15-guide-evidence-handoff.md), [`PD-315-20260908`](./2026-09-08-production-evidence-retrieval-contract-divergence.md) |

---

## 1. 목적과 권위 경계

본 결정은 `PD-180-EM-20260916` 남아 있는 차단 사항 2번(`"#174 REQUEST Guard: Authenticated Assembler 미구현"`)을 해소하기 위해, **동기 Guide 실행 경계에서 사용할 REQUEST Guard / Source Decision / Member Decision authority assembly의 계약 및 pure/read-only seam**을 확정한다.

본 결정은 #174의 전체 비동기 런타임 전환(Async Job, Outbox, 202 Accepted, Worker Handler)이 아니며, 오직 순수 인메모리 권위 조립 계약 및 synthetic reader 기반 검증에만 적용된다.

### 권위 한계 (Authority Boundary)

1. **순수 계약 솔기 (Pure/Read-Only Seam)**:
   - 데이터베이스 Trigger, RLS, Stored Procedure, Event Bus, Registry, 신규 Queue, 신규 Worker, 신규 persistence 테이블을 일절 추가하지 않는다.
   - 본 PR은 외부 권한 저장소에 의사결정을 쓰거나 재평가하지 않으며, 이미 발행된 authoritative observation을 읽어 `RequestSourceMemberBinding`으로 조립·투영하는 순수 seam만을 제공한다.
2. **호출자 임의 PASS 주장 원천 차단 (No Caller PASS Assertion)**:
   - 호출자 요청 DTO(`SyncGuideEvidenceAuthoritySelection`)에는 `PASS` 등 결정 결과를 나타내는 필드를 일절 두지 않는다.
   - `PASS` 결과는 반드시 `GuideEvidenceAuthorityReaderPort`가 exact ref로 조회한 authoritative observation의 `actual_decision_outcome`에서만 도출된다.
3. **검증 범위와 비검증 경계의 엄격한 분리**:
   - **검증 대상**: `GuideEvidenceAuthorityReaderPort`가 authoritative observation으로 반환한 Guard/Source/Member Decision의 requested ref 일치, 소유자 일치, 요청 연산 일치, REQUEST 결정 단계 일치, actual PASS 결과, Source/Member exact binding. (Production reader/storage binding은 여전히 후속임)
   - **검증 제외 대상**: `assessment_artifact_ref` 진위, `eligibility_receipt_ref` 진위, assessment 신선도(freshness), retrieval receipt 진위. 이들은 Evidence Gate / `EvidenceEligibilityVerifierPort` 및 #180 오케스트레이션 소관이며 본 계약에서 인증한다고 주장하지 않는다.
4. **원자적 Fail-Closed 원칙**:
   - 둘 이상의 selection 중 단 하나라도 검증에 실패하면, 조립 결과는 전체가 `decision = REJECTED`, `bindings = ()`로 거부된다. 부분 성공 바인딩(partial authenticated bindings)을 반환하지 않는다.
5. **Production DB 어댑터 제외**:
   - Decision 영속 테이블 및 저장 위치가 미확정 상태이므로, 본 PR에서는 synthetic test double 및 Protocol 포트만 정의하고 프로덕션 DB 어댑터 구현은 배제한다.
6. **PD-315 상태 명시 (Condition unresolved)**:
   - PR #361 책임 리뷰의 APPROVED event는 존재하지만, 원 승인 본문이 요구한 final HEAD의 Source·DB specialist 확인 조건에 대한 해소 근거가 없으며 governance readjudication도 기록되지 않았다.
   - #672는 `PD-315-20260908`을 Approved로 전제하지 않는다.

---

## 2. 핵심 설계 결정 사항

### 2.1 Reader Observation의 `decision_stage` 검증 및 승격
- Reader가 반환하는 관측치는 영속 계층의 wire-format 특성을 감안하여 `decision_stage: str` 날것의 문자열로 수신한다.
- 조립 솔기는 이를 `RequestDecisionStage.REQUEST.value`(`"REQUEST"`)와 엄격히 문자열 동등 비교 검증하고, 불일치 시 `DECISION_STAGE_MISMATCH`로 즉시 거부한다.
- 검증 통과 후 `RequestSourceMemberBinding` 생성 시 비로소 typed enum인 `RequestDecisionStage.REQUEST`로 안전하게 승격한다.

### 2.2 Reader 반환 `artifact_ref` exact-match 검증 (`AUTHORITY_REF_MISMATCH`)
- Reader가 반환한 관측치의 `artifact_ref`가 호출자가 조회를 요청한 ref와 정확히 일치하는지 검증한다.
- 조회 키와 다른 ref가 반환된 경우 `AUTHORITY_REF_MISMATCH`로 fail-closed 거부한다.

### 2.3 단계별 Fail-Fast Rejection Order (Phase-Ordered Fail-Fast)
불필요한 Reader I/O 호출을 차단하고 결정론적 검증 순서를 보장하기 위해 3단계 fail-fast 구조를 채택한다:
1. **Phase 1: Request Structural Validation** -> 실패 시 `REQUEST_INVALID` 즉시 반환 (Guard/Decision Reader 호출 0건).
2. **Phase 2: Authoritative Guard Verification** -> Reader 조회 오류(`AUTHORITY_READER_ERROR`), 누락(`REQUEST_GUARD_NOT_FOUND`), ref 불일치(`AUTHORITY_REF_MISMATCH`), 소유자 불일치(`OWNER_MISMATCH`), operation 불일치(`REQUEST_OPERATION_MISMATCH`), stage 불일치(`DECISION_STAGE_MISMATCH`) 발생 시 즉시 반환 (Decision Reader 호출 0건).
3. **Phase 3: Selections Loop in Order** -> selection 순서대로 Source Decision 검증 후 Member Decision 검증을 수행하며, 첫 번째 실패 발생 시 즉시 중단 및 거부 반환.

### 2.4 예외 격리 및 프로그래밍 버그 투명 전파
- 오직 Reader의 명시적 의존성 오류인 `GuideEvidenceAuthorityReaderError`만 catch하여 typed `AUTHORITY_READER_ERROR` rejection으로 변환한다.
- `RuntimeError` 등 예상하지 못한 예외는 broad `except Exception`으로 덮어 삼키지 않고 그대로 전파시켜 숨겨진 결함이 없도록 고정한다.

---

## 3. 남은 후속 과제 및 차단 사항

본 결정 및 #672 구현으로 pure authority assembly seam은 확정되었으나, 다음 항목은 후속 작업으로 연결된다:
1. **#180 런타임 오케스트레이션**: 조립된 `RequestSourceMemberBinding`을 `guide_evidence_handoff.py`의 `build_guide_evidence_handoff`로 연결하고, Evidence Gate 검증 및 LangGraph 런타임 오케스트레이션에 통합하는 작업.
2. **#622 비동기 전환**: Guide/Chat 접수 API의 `202 Accepted + JobStatusResponse` 전환 및 Outbox/Worker 연동 (Post-demo 분리 범위).
3. **프로덕션 Reader 어댑터**: Decision 저장 스키마 확정 후 `GuideEvidenceAuthorityReaderPort`의 PostgreSQL 구현체 작성.
4. **프로덕션 공개 게이트**: 외부 의료·약학·Source·Privacy·Safety 승인 및 `PUBLIC_TRACK_F=false` 유지.
