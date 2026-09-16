# Product Decision Candidate: Endpoint Member 권위 계약 단일화 및 차단 marker 해소

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-180-EM-20260916` |
| 상태 | Proposed / Review pending · Issue #180 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — PM·Product Acceptance·Privacy Gate |
| 필요 교차 리뷰 | 송은영 (`@phina-io`) — DB·Guard / 김지혜 (`@Jye-rookie`) — Source provenance |
| 추적 Issue | [#180](https://github.com/AI-HealthCare-05/AH_05_04/issues/180) |
| 상위 결정 | [`PD-180-20260915`](./2026-09-15-guide-evidence-handoff.md), [`PD-362-20260909`](./2026-09-09-source-snapshot-approval-boundary.md), [`PD-315-20260908`](./2026-09-08-production-evidence-retrieval-contract-divergence.md) |

## 목적과 권위 경계

`BLOCKED_BY_180_ENDPOINT_MEMBER_CONTRACT`라는 비강제 문자열 마커를, Endpoint / Operation / Artifact Member 권위를 단일 정의하는 **공유 순수 계약 모듈(`source_member_identity.py`) + typed precondition + exact-match verifier**로 대체한다.

본 결정은 순수 계약 계층, 공유 모델, 단위/계약 테스트에만 적용되며, 데이터베이스 영속 계층, Alembic 마이그레이션, API 라우트, LangGraph 런타임 오케스트레이션은 변경하지 않는다.

### 권위 한계 (Authority Boundary)

1. **PD-315 상태와 제안(Proposed) 경계**:
   - 상위 결정 `PD-315-20260908`은 PR #361 최종 HEAD에서 책임 리뷰 `APPROVED` event를 확보했으나, 그 승인 본문이 요구한 최신 HEAD specialist 확인이 없어 **승인 조건이 미해소**다([승인 절차 provenance와 미해소 조건](./2026-09-08-production-evidence-retrieval-contract-divergence.md#승인-절차-provenance와-미해소-조건)). 따라서 본 결정에서 정의하는 `operation_code` nullable 규칙의 채택은 **제안(Proposed)** 단계이며 순수 계약 계층에만 적용된다.
   - 본 계약의 `docs/contracts/current/` 승격 및 #180 런타임 오케스트레이션과의 연결은 구현·migration·OpenAPI/DTO·자동 테스트·실행 증빙을 갖춘 별도 PR에서 지정 책임 리뷰어 승인과 함께 수행한다.
2. **순수 검증 및 결속 한계 (Pure Kernel Scope)**:
   - `verify_member_authority_binding` verifier는 구조적 정합성 및 5개 필드의 인메모리 exact-match 일치 여부만을 검증한다.
   - 데이터베이스 상의 레코드 존재 여부, Decision ownership, #174 REQUEST Guard의 실제 `PASS` 판정, 또는 발급자 진위(issuer authenticity)를 증명하지 않는다.
   - 용어 불변성 규칙(PR #623)에 따라 이 경계에서는 `Authorized`라는 명칭을 일절 사용하지 않으며, 구조 검증(`Verified`)의 의미만을 갖는다.
3. **병행 가능 진행 기준 (Issue #180 진행 근거)**:
   - Issue #180의 작업 기준 중 "병행 가능: 확정 Interface 기반 Protocol·합성 Fixture·단위 테스트·로컬 구현은 병행할 수 있다"에 따라, 런타임 소비처(#180 orchestration) 구현에 앞서 공유 순수 계약을 선행 확정한다.
   - 본 PR에서 추가되는 `verify_member_authority_binding` 및 `member_kind_from_persisted`는 선행 인터페이스 확정 범위로서 합성 테스트에서 검증되며, 프로덕션 호출부는 후속 #180 런타임 연결 PR에서 조립된다.

## 핵심 결정 사항

### 1. Endpoint Member의 `operation_code` nullable 규칙 통일
- `ENDPOINT_OPERATION` member의 경우:
  - `endpoint_code`: 필수 (비어있지 않은 NFC 정규화 문자열, `_is_nonblank_nfc`)
  - `operation_code`: **nullable** (제공될 경우 비어있지 않은 NFC 정규화 문자열, `None` 허용)
  - `artifact_code`, `artifact_version`: 반드시 `None`
  - 정책 상수: `ENDPOINT_MEMBER_OPERATION_CODE_POLICY = EndpointMemberOperationCodePolicy.NULLABLE`
- `ARTIFACT_MEMBER`의 경우:
  - `artifact_code`: 필수 (비어있지 않은 NFC 정규화 문자열)
  - `artifact_version`: 필수 (비어있지 않은 NFC 정규화 문자열)
  - `endpoint_code`, `operation_code`: 반드시 `None`
- 기존 3개 소비처(`claim_citation_validator`, `citation_authorization`, `guide_evidence_handoff`)의 member 검증 분기를 공유 순수 모듈 `source_member_identity`의 `is_valid_source_member_identity`로 단일 위임한다.
- 위임 시 duck typing을 금지하며 각 소비처의 호출부에서 5개 필드로 `SourceMemberIdentity`를 명시적으로 인스턴스화하여 엄격한 `type(value) is SourceMemberIdentity` 검사를 통과시킨다.
- `StrEnum`과 문자열의 동등 비교로 인한 검증 우회(예: 문자열 `"ENDPOINT_OPERATION"`이 `in` 조건을 통과하고 `is` 분기에서 artifact 분기로 빠지는 결함)를 방지하기 위해, 분기 전에 `type(value.member_kind) is SourceMemberKind`를 강제하여 동일 값의 문자열 및 타 StrEnum을 fail-closed로 `MEMBER_KIND_INVALID` 거부한다.

### 2. 영속 저장값 분기(F7) 해소 및 매핑 결정 ("왜 DB 값을 바꾸지 않는가")
- **현상 (F7)**:
  - DB `rag_source_snapshot_member.member_kind` 컬럼 및 Alembic 마이그레이션 CHECK 제약, ORM 모델, `snapshot_lifecycle.SourceSnapshotMemberKind`는 `"ENDPOINT_OPERATION"`과 `"ARTIFACT"`를 사용한다.
  - 순수 커널(`claim_citation_validator`)의 `SourceMemberKind`는 `"ENDPOINT_OPERATION"`과 `"ARTIFACT_MEMBER"`를 사용한다.
- **결정**:
  - 이미 배포되어 영속화된 DB 스키마와 데이터 마이그레이션 위험을 방지하기 위해 **DB 영속값(`"ARTIFACT"`)은 변경하지 않는다.**
  - 영속 wire value와 커널 enum 간의 양방향 변환 함수를 `source_member_identity`에 단일 정의한다:
    - `persisted_member_kind_value(kind: SourceMemberKind) -> str` (`ARTIFACT_MEMBER` → `"ARTIFACT"`)
    - `member_kind_from_persisted(value: str) -> SourceMemberKind` (`"ARTIFACT"` → `ARTIFACT_MEMBER`)
  - 미지의 영속값 입력 시 호출자에게 원시 `ValueError`를 던지지 않고, typed 예외 `SourceMemberIdentityError(SourceMemberIdentityReason.PERSISTED_MEMBER_KIND_UNKNOWN)`를 발생시키거나 `try_member_kind_from_persisted`를 통해 typed 사유를 반환한다.
  - `SourceSnapshotMemberKind`와의 정의 drift를 방지하기 위해 `test_persisted_member_kind_values_match_snapshot_lifecycle_enum` 테스트를 CI에 배치하여 두 정의가 갈라지지 않도록 강제한다.

### 3. 해시 프로젝션 바이트 불변성 (Byte-level Invariance)
- 위임 후에도 `_source_binding_payload`, `_selection_payload`, `_selection_projection`의 키 순서, 키 이름, 필드 타입, nullable 직렬화 표현은 바이트 단위로 변경되지 않는다.
- `origin/develop`(`66328a09`) 베이스라인에서 사전 산출한 10종의 골든 벡터 해시를 리터럴 상수로 고정하고 불변성을 증명한다.

### 4. 기존 비강제 마커의 해소
- `guide_evidence_handoff.py`의 `BLOCKED_BY_180_ENDPOINT_MEMBER_CONTRACT` 비강제 문자열 상수를 제거하고, 공유 모듈 참조와 typed 검증 위임으로 교체한다.
- `guide-evidence-handoff-v1.md` 및 `2026-09-15-guide-evidence-handoff.md`의 충돌 항목에 해소 경위를 명시한다.
- 선행 PR #636(`fix/178-retrieval-selection-manifest-jcs`)이 `develop`에 병합되어 canonical JCS 해시 계약 정렬 및 `BLOCKED_BY_178_CANONICAL_HASH_CONTRACT` 해소가 완료되었으며, 본 결정에서는 endpoint/member 권위 계약 단일화를 통해 `BLOCKED_BY_180_ENDPOINT_MEMBER_CONTRACT`를 해소한다.

## 남아 있는 차단 사항 (Runtime 연결 불가 근거)

본 결정으로 계약 수준의 단일화는 완료되었으나, 아래 네 가지 선행 사유로 인해 #180 런타임 연결은 여전히 차단 상태로 유지된다:

1. **`PD-315-20260908` 승인 조건**: 책임 리뷰 `APPROVED` event는 PR #361 최종 HEAD에 확보됐으나 그 승인 본문이 요구한 최신 HEAD specialist 확인이 없어 조건이 미해소다. 조건이 해소되어도 Decision 승인은 구현 증빙이 아니므로, 구현·migration·OpenAPI/DTO·자동 테스트·실행 증빙을 갖춘 별도 PR 전까지 본 계약은 `Proposed`로 유지한다.
2. **`#174 REQUEST Guard`**: Authenticated Assembler 미구현 (결정 진위, 실제 `PASS` 판정, Decision ownership 검증 선행 필요; 순수 구조/identity 검증만으로 authenticated runtime authority가 성립하지 않음)
3. **`#180 런타임 오케스트레이션 및 파이프라인 연결`**: LangGraph runtime pipeline, Outbox/Job/Worker integration, DB persistence port 연결 및 E2E 평가 미완료 (계약 정렬은 완료되었으나 runtime integration 책임은 #180에 여전히 유효하게 잔존함)
4. **프로덕션 공개 게이트 (Production Release Gate)**: 외부 의료·약학·Source·Privacy·Safety 승인 및 `PUBLIC_TRACK_F=false` 유지 조건 별도 충족 필요
