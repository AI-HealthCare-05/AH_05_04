# Guide Authority × Production Retrieval Composition Contract v1

| 항목 | 값 |
| --- | --- |
| 문서 버전 | `guide-retrieval-composition-v1` |
| 상태 | Proposed / Review pending · Issue #697 |
| 관련 선행 계약 | [`sync-guide-evidence-authority-v1`](./sync-guide-evidence-authority-v1.md), [`retrieval-run-v1`](./retrieval-run-v1.md), [`guide-evidence-handoff-v1`](./guide-evidence-handoff-v1.md) |
| 구현 위치 | `ai_worker/tasks/rag/guide_retrieval_composition.py` |
| 테스트 위치 | `ai_worker/tests/rag/test_guide_retrieval_composition.py` |
| 계약 및 구현 책임 | 정현우 (`@ceohwj`) — AI/RAG |
| 단일 책임 리뷰 | 김지혜 (`@Jye-rookie`) — Worker & Source Provenance / Pure Composition Seam |

---

## 1. 목적 및 권위 한계

본 문서는 #672가 조립한 인증된 `RequestSourceMemberBinding`과 #178 production retrieval이 선택한 chunk hit를 결합하는 순수(pure) 인메모리 composition 솔기의 계약을 규정한다. 이 계층은 새로운 권위(authority)나 정책을 발행하지 않고, 이미 확정된 두 경계의 결과만 소비한다.

### 1.1 권위 한계 (Authority Boundary)

1. **순수/읽기 전용 솔기**: DB write, migration, network I/O, persistence, 신규 큐·워커·테이블을 일절 생성하지 않는다.
2. **신규 권위 없음**: `assemble_sync_guide_evidence_authority`가 이미 확인한 authority 의미와 `execute_production_retrieval`이 이미 확정한 retrieval 의미를 재검증·재해석하지 않는다. 본 계층은 자체 receipt, hash domain, ranking policy를 소유하지 않는다.
3. **Production 경계 한정**: production `production_evidence_gate.EvidenceGateSuccess`와 `ProductionSearchReceipt`만 소비한다. provisional `ai_worker/tasks/rag/evidence_gate.py` 및 그 `EvidenceGateRetrievalReceipt`는 별도 receipt domain이므로 import·변환·wrapper·adapter 어느 형태로도 사용하지 않는다.
4. **Silent transform 금지**: 결합은 exact equality로만 수행하며 normalize, trim, lower-case 등의 문자열 변환을 하지 않는다.
5. **범위 종료 지점**: 결과는 `AuthenticatedGuideRetrievalSelection[]`에서 멈춘다. Handoff, assessment, content hydration, #180 orchestration으로 확장하지 않는다.

---

## 2. Inputs

```text
SyncGuideEvidenceAuthorityOutcome
ProductionRetrievalOutcome
ProductionSearchReceipt
production EvidenceGateSuccess.selected_hits
```

## 3. Preconditions

phase-ordered fail-fast로 검증한다.

| Phase | 조건 | 위반 시 reason |
| --- | --- | --- |
| 1 | `authority_outcome.decision == AUTHENTICATED` | `AUTHORITY_NOT_AUTHENTICATED` |
| 2 | `retrieval_outcome.status == SUCCEEDED` | `RETRIEVAL_NOT_SUCCEEDED` |
| 2 | `receipt`가 존재하며 `ProductionSearchReceipt` 타입 | `RETRIEVAL_RECEIPT_MISSING` |
| 2 | `receipt.retrieval_execution_status == SUCCEEDED` | `RETRIEVAL_NOT_SUCCEEDED` |
| 2 | `type(gate_outcome) is EvidenceGateSuccess` | `EVIDENCE_GATE_NOT_SUCCEEDED` |

authority outcome이 `REJECTED`이면 `bindings`가 우연히 포함되어 있어도 소비하지 않는다.

---

## 4. Authority Join Key

```text
source_snapshot_id
source_snapshot_member_id
source_code
source_version
```

Binding 측은 `RequestSourceMemberBinding`의 동명 필드에서, hit 측은 `hit.provenance`의 동명 필드에서 읽는다. exact equality만 사용한다.

---

## 5. Cardinality

```text
N chunk hits : 1 authenticated member binding
```

authority join key는 **Source Member 단위**이고 `EvidenceGateSuccess.selected_hits`는 **chunk 단위**다. 따라서 동일 Source Member 아래에서 `external_document_id`, `chunk_index`, `knowledge_chunk_id`가 서로 다른 chunk가 복수 선택되는 것은 정상적인 production 결과다.

- 각 selected hit는 정확히 하나의 authenticated binding을 resolve해야 한다.
- 하나의 binding은 같은 Source Member의 여러 distinct chunk hit에서 재사용될 수 있다.
- 전체 bindings와 hits 사이의 global bijection(`len(selected_hits) == len(bindings)`)을 요구하지 않는다.

성공 조건은 다음을 모두 만족하는 경우다.

```text
모든 selected hit가 정확히 하나의 binding key를 resolve
AND
모든 authority binding key가 최소 1개의 selected hit에서 사용됨
AND
duplicate binding 없음
AND
duplicate stable-coordinate hit 없음
```

---

## 6. Caller Scope Precondition

본 절은 composition 경계가 **입력에 요구하는 범위 조건**을 규정한다. 실행 시점(temporal orchestration order)에 대한 규정이 아니다.

### 6.1 불변식

```text
set(authority binding member keys)
==
set(member authority keys represented by selected_hits)
```

여기서 member authority key는 §4의 4필드다. `selected_hits`에는 동일 member의 여러 chunk가 존재할 수 있으므로 비교는 **set 기준**이다.

| 입력 | 결과 |
| --- | --- |
| bindings `{A}` / hits `A/chunk-1`, `A/chunk-2`, `A/chunk-3` | VALID — 3 selections, binding A 재사용 |
| bindings `{A, B}` / hits `A/chunk-1`, `A/chunk-2` | `EXTRA_BINDING` — B에서 선택된 hit 없음 |
| bindings `{A}` / hits `A/chunk-1`, `B/chunk-1` | `BINDING_NOT_FOUND` — B의 authority 없음 |

### 6.2 규정

- authority bindings는 `selected_hits`가 나타내는 distinct Source Member 집합과 정확히 동일한 범위여야 한다.
- 동일 member의 복수 chunk hit는 하나의 binding을 공유할 수 있다.
- selected hit가 하나도 없는 authority binding이 남으면 `EXTRA_BINDING`이다.
- composition seam은 authority superset을 자동 filter하지 않는다.
- composition seam은 temporal orchestration order를 정의하지 않는다.
- 이 precondition을 만족시키는 책임은 #180 caller/orchestration에 있다.

### 6.3 자동 filtering을 하지 않는 이유

```text
Filtering an authenticated authority outcome inside #697 would
introduce a new authority-selection policy owned by this seam.
#697 intentionally does not own that policy.
```

인증된 authority outcome의 일부를 composition 내부에서 조용히 버리는 것은 "어떤 authority를 사용할지"를 결정하는 새로운 정책이다. 본 계약은 그 정책을 소유하지 않으므로, 범위가 어긋난 입력은 filter하지 않고 fail-closed로 거부한다.

### 6.4 Orchestration 경계

```text
#697 does not prescribe temporal orchestration order.

The caller MUST provide an AUTHENTICATED authority outcome
scoped to exactly the distinct Source Member authority keys
represented by production selected_hits.

#180 orchestration is responsible for satisfying this scope
precondition before invoking the composition seam.
```

retrieval을 먼저 실행해 selected member 범위를 확정한 뒤 authority assembly를 수행하는 방식은 이 scope 조건을 만족시키는 자연스러운 구현일 수 있으나, 본 계약은 `retrieval MUST run before authority assembly`를 normative ordering으로 확정하지 않는다. 실제 실행 순서는 #180이 소유한다.

---

## 7. Duplicate Semantics

```text
duplicate binding
= 동일 authority member join key가 bindings에 2회 이상 존재

duplicate hit
= 동일 production stable coordinate
  (source_code, source_version, external_document_id, chunk_index)
```

duplicate hit 판정은 upstream production Evidence Gate가 이미 사용하는 stable coordinate identity를 그대로 재사용한다. 본 계층은 새로운 dedupe·ranking policy를 정의하지 않는다. 동일 member authority key를 가지되 stable coordinate가 다른 hit는 duplicate가 아니다.

duplicate를 deduplicate해서 성공시키지 않고 fail-closed한다.

### 7.1 Authority-side multiplicity를 허용하지 않는 이유

```text
N hits : 1 binding은 chunk-side cardinality를 허용하는 것이다.

동일 authenticated binding의 duplicate copies를 authority-side
multiplicity로 허용하는 의미가 아니다.
```

authority member key별 canonical input은 정확히 1개여야 하며, 중복 binding을 자동 dedupe하지 않는다. #672가 caller selection 순서를 보존하고 duplicate를 허용할 수 있더라도, #697 composition 경계에서는 ambiguous/redundant authority input을 fail-closed로 거부한다. 본 계약은 새로운 deduplication policy를 만들지 않는다.

---

## 8. Coordinate ↔ Provenance 책임 경계

`ProductionSearchHit`의 두 좌표계는 본 계약에서 서로 다른 목적으로 소비된다.

| 용도 | 소비 대상 |
| --- | --- |
| authority join key | `hit.provenance`의 Source Member coordinates (§4) |
| duplicate hit identity | `hit.coordinate`의 production stable coordinate (§7) |

```text
ProductionSearchHit의 coordinate ↔ provenance coherence는
production search/retrieval upstream 계약의 책임이다.

#697은:
- authority join에는 provenance의 Source Member coordinates를 소비하고
- duplicate detection에는 production stable coordinate를 소비한다.

#697은 coordinate ↔ provenance consistency를 새로 재검증하지 않는다.

downstream Guide Evidence Handoff는 Verified Handoff 생성 전에
해당 exact consistency를 다시 검증한다.
```

따라서 본 계약은 `COORDINATE_PROVENANCE_MISMATCH` 계열의 새 reason을 추가하지 않으며, 형제 kernel(`guide_evidence_handoff`)이 이미 수행하는 검증을 복제하지 않는다.

---

## 9. Fail-closed Reasons

```text
AUTHORITY_NOT_AUTHENTICATED
RETRIEVAL_NOT_SUCCEEDED
RETRIEVAL_RECEIPT_MISSING
EVIDENCE_GATE_NOT_SUCCEEDED
BINDING_NOT_FOUND
EXTRA_BINDING
DUPLICATE_BINDING
DUPLICATE_HIT
```

- `BINDING_NOT_FOUND`: selected hit의 member authority key에 대응하는 binding이 없으면 즉시 fail-fast한다.
- `EXTRA_BINDING`: 모든 selected hit의 join이 끝난 뒤에도 한 번도 사용되지 않은 binding key가 남아 있을 때만 보고한다. 즉 binding은 존재하지만 그 member에서 선택된 production hit가 하나도 없는 경우다. 이는 §6 Caller Scope Precondition 위반이며, 본 계층은 authority superset을 자동 filter하지 않는다.

---

## 10. Rejection Semantics

```text
fail-fast
single typed reason
no partial selections
retrieval_receipt=None on rejection
```

- 검증은 phase-ordered fail-fast다.
- rejection outcome은 최초로 확정 가능한 typed reason **하나만** 반환한다. 여러 reason을 집계(aggregation)하지 않는다.
- 하나라도 문제가 있으면 `decision = REJECTED`, `selections = ()`, `retrieval_receipt = None`이며 partial success를 반환하지 않는다.
- 실패한 join key를 outcome에 노출하지 않는다. #697은 operational diagnostic schema를 정의하는 작업이 아니며, join key를 외부 outcome 계약에 추가하는 것은 현재 요구사항이 아니다.

---

## 11. Ordering

성공 결과의 selection 순서는 `EvidenceGateSuccess.selected_hits`의 production 순서를 그대로 보존한다. 별도 sort를 하지 않는다. `fusion_rank` 순서는 production Evidence Gate가 소유하므로 본 계층에서 ranking policy를 재구현하지 않는다.

---

## 12. Explicit Exclusions

```text
Production Reader
assessment authority
content hydration
GuideEvidenceHandoffRequest
build_guide_evidence_handoff
VerifiedGuideEvidenceHandoff
LangGraph
Generator
Citation Authorization
persistence
Job/Outbox/Worker
Production/Public activation
```

- `ProductionSearchHit`에는 `SensitiveText content_text`가 없으므로 content hydration, KnowledgeChunk reader, chunk text lookup, `SensitiveText` 생성을 수행하지 않는다. `AuthenticatedGuideRetrievalSelection`은 `hit` + `binding`까지만 보존한다.
- downstream Handoff는 `content_text`, `eligibility_receipt_ref`, `assessment_artifact_ref`, `verifier_artifact_ref`, `assessment_valid_from/until` 권위를 추가로 요구하므로 본 계약은 거기까지 연결하지 않는다.
- `GuideEvidenceAuthorityReaderPort`의 production DB 구현은 본 계약 범위가 아니다.
- provisional `evidence_gate.py` 및 `EvidenceGateRetrievalReceipt`를 사용하지 않는다.

본 계약은 `docs/contracts/current/`로 승격하지 않는다. 승격은 구현·자동 테스트·실행 증빙 및 지정 책임 리뷰어 승인을 갖춘 별도 PR에서만 수행하며, 본 계약 정렬은 #180 runtime authority 활성화나 Production/Public 전환을 의미하지 않는다.
