# Guide Runtime Preflight 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 버전 | `guide-runtime-preflight-v1` |
| 상태 | Proposed / Review pending · Issue #180 |
| 관련 선행 계약 | [`guideline-card-v1`](../../targets/post-mvp-1/guideline-card-v1.md), [`rag-runtime-v1`](../../targets/post-mvp-1/rag-runtime-v1.md) |
| 선행 구현 | #707 RAG-15 Formal Approval Pack (`ai_worker/tasks/rag/guideline_approval_pack.py`) |
| 구현 위치 | `ai_worker/tasks/rag/guide_runtime_preflight.py` |
| 테스트 위치 | `ai_worker/tests/rag/test_guide_runtime_preflight.py` |
| 계약 및 구현 책임 | 정현우 (`@ceohwj`) — AI/RAG |
| 단일 책임 리뷰 | `@hazelnutflavoured` — RAG-15 approval consumption, Product/Safety fail-closed runtime gate, Generator/Policy/Fallback exact binding, #180 scope boundary |

---

## 1. 목적 (Purpose)

본 문서는 Guide 생성 호출이 허용되기 **이전에**, 공식 승인된 RAG-15 runtime candidate와 현재 runtime에 실제로 구성된 Generator·Policy·Fallback Set이 exact-match하는지를 판정하는 순수 경계를 규정한다.

본 계층이 답하는 질문은 하나뿐이다.

```text
지금 실행하려는 Generator·Policy·Fallback이
RAG-15가 공식 승인한 그 candidate와 정확히 같은가?
```

### 1.1 권위 한계 (Authority Boundary)

1. **순수 계층**: DB, migration, 외부 호출, 시간, 난수를 사용하지 않는다. 동일 입력에 대해 항상 동일 출력이다.
2. **신규 승인 권위 없음**: approval 검증은 #707 `verify_rag15_approval_pack`을 그대로 재사용한다. 본 module은 approval 판정을 재구현하지 않는다.
3. **Generator 미실행**: `generator.generate(...)`를 절대 호출하지 않는다. 읽는 값은 `generator.provenance` 하나뿐이다.
4. **환자·요청 데이터 없음**: `MedicationIdentityRef`, `EvidenceGateOutcome`, `SensitiveText`, 환자 UUID, 질문 원문, Source 원문을 입력에도 출력에도 두지 않는다. 본 preflight는 static/runtime configuration authority만 다룬다.
5. **값 생성 금지**: `rerank_score`, assessment ref, eligibility receipt, validity window, verifier ref, selection projection hash를 임의 생성하지 않는다.
6. **Fallback 본문 비소유**: fallback text의 정본은 계속 `ai_worker/tasks/rag/guideline_card.py`가 소유한다. 본 module은 code와 `ImmutableArtifactRef`만 비교하며 본문을 재검증하거나 복사하지 않는다.
7. **공유 계약 불변**: `GuidelineGeneratorPort`(#667/#690)를 수정하지 않는다. runtime identity 요구는 #180 local structural Protocol `RuntimeGuidelineGeneratorPort`로만 표현한다.

---

## 2. Input

```python
@dataclass(frozen=True, slots=True)
class GuideRuntimePreflightRequest:
    approval_pack: Rag15ApprovalPack
    policy: VersionedGuidelinePolicy
    fallbacks: tuple[ApprovedGuidelineFallback, ...]
```

의존성은 request가 아닌 함수 인자로 전달한다.

```python
def preflight_guide_runtime(
    request: GuideRuntimePreflightRequest,
    *,
    generator: RuntimeGuidelineGeneratorPort,
    decision_verifier: Rag15ApprovalDecisionVerifierPort,
) -> GuideRuntimePreflightOutcome: ...
```

`RuntimeGuidelineGeneratorPort`는 기존 `GuidelineGeneratorPort`에 실제 실행 provenance 노출만 더한 structural Protocol이다.

```python
class RuntimeGuidelineGeneratorPort(GuidelineGeneratorPort, Protocol):
    @property
    def provenance(self) -> GuidelineGenerationProvenance: ...
```

`OpenAIGuidelineGeneratorAdapter`는 이미 이 Protocol을 구조적으로 만족하며, 본 PR에서 adapter를 수정하지 않는다.

---

## 3. Phase 순서 (fail-fast 고정)

각 Phase는 실패 즉시 종료하며 이후 Phase를 실행하지 않는다.

| Phase | 검증 | 실패 reason |
| --- | --- | --- |
| 1 | request·approval_pack·policy exact type, fallbacks tuple 및 non-empty | `REQUEST_INVALID` |
| 2 | `verify_rag15_approval_pack` 결과의 4개 조건 동시 충족 | `APPROVAL_PACK_NOT_CONSUMABLE` |
| 3 | `generator.provenance == approval_pack.generation_provenance` | `GENERATOR_PROVENANCE_MISMATCH` |
| 4 | `policy.artifact_ref == approval_pack.policy_ref` | `POLICY_REF_MISMATCH` |
| 5 | runtime fallback set == `approval_pack.fallback_pins` | `FALLBACK_SET_MISMATCH` |

### 3.1 Phase 2 — RAG-15 Approval Pack consumption

#707의 `verify_rag15_approval_pack(pack, decision_verifier=...)`을 그대로 호출하고, 다음 네 값이 **모두** 성립할 때만 통과시킨다.

```text
integrity_verified          == True
approval_status             == "APPROVED"
approval_evidence_verified  == True
production_consumable       == True
```

PENDING, REJECTED, decision 인증 실패, candidate/pack hash drift는 모두 단일 reason `APPROVAL_PACK_NOT_CONSUMABLE`로 닫는다.

### 3.2 Phase 3 — Generator identity binding

`prompt_ref`, `model_ref`, `parser_ref`, `validator_ref` 중 하나만 비교하지 않는다. 네 ref가 결속된 `GuidelineGenerationProvenance` 전체 값 동등성을 요구한다. 네 ref 중 무엇이 어긋나든 reason은 `GENERATOR_PROVENANCE_MISMATCH` 하나로 닫으며, ref별 reason을 신설하지 않는다.

### 3.3 Phase 4 — Policy binding

승인된 `artifact_ref`가 같다는 사실만으로는 runtime object가 그 ref가 content-address하는 의미를 그대로 유지한다는 보장이 없다. `VersionedGuidelinePolicy.artifact_ref`는 `maximum_claims`, `uncertainty_text_sha256`, `consultation_text_sha256`을 포함한 canonical payload를 content-address하지만, Generator는 실제로 `policy.maximum_claims` 값을 소비한다. 따라서 `artifact_ref`만 보존한 채 내부 필드를 변조한 객체는 승인된 identity와 실제 runtime behavior가 달라진다.

Policy exact binding은 다음 **두 조건을 모두** 요구한다.

```text
1. runtime VersionedGuidelinePolicy가 자신의 content-addressed artifact_ref와
   self-consistent할 것 (public factory로 재계산한 ref가 값 동등할 것)
2. 그 artifact_ref가 Approval Pack policy_ref와 값 동등할 것
```

재계산은 public `VersionedGuidelinePolicy.create(...)`만 사용하며, `guideline_card`의 private helper(`_is_valid_policy` 등)를 import하지 않는다. policy hash를 독자적으로 재해석하지 않는다. 둘 중 하나라도 실패하면 새 reason 없이 `POLICY_REF_MISMATCH`로 닫는다.

### 3.4 Phase 5 — Fallback set binding

Fallback도 동일한 fail-open을 갖는다. `code`와 `artifact_ref`만 비교하면 ref와 code를 유지한 채 `text`만 변조한 runtime fallback을 감지하지 못한다.

따라서 code/ref 집합을 Approval Pack pin과 비교하기 **전에**, 각 runtime fallback의 `(code, text)` semantic payload가 public `ApprovedGuidelineFallback.create(...)`를 통해 자신의 `artifact_ref`를 재생산하는지 확인한다.

비교 기준은 `GuidelineFallbackCode` + `ImmutableArtifactRef` exact equality다. 입력 tuple의 순서는 의미가 없으며 canonical code 순으로 비교한다. 따라서 순서만 뒤집힌 정확한 집합은 READY이고, 다음은 모두 `FALLBACK_SET_MISMATCH`다.

```text
fallback missing
fallback duplicate
extra fallback
same code + different ref
same code + same ref + different text
unsupported code/type
```

본 self-integrity는 runtime object가 pin된 ref에서 벗어나지 않았음만 증명한다. Korean copy policy, forbidden action scan, approved fallback text lookup, medical copy validation 등 canonical approved copy 검증은 계속 `guideline_card.py`가 소유하며 여기서 재구현하지 않는다.

---

## 4. Output

```python
class GuideRuntimePreflightDecision(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class GuideRuntimePreflightOutcome:
    decision: GuideRuntimePreflightDecision
    reason: GuideRuntimePreflightReason | None
    ready_context: ReadyGuideRuntimeContext | None
```

불변식:

```text
READY   → reason is None,   ready_context exists
BLOCKED → reason exists,    ready_context is None
```

READY context는 최소값만 보관하며 `Rag15ApprovalPackVerification` 자체나 환자·요청 데이터를 담지 않는다.

```python
@dataclass(frozen=True, slots=True)
class ReadyGuideRuntimeContext:
    approval_pack_ref: ImmutableArtifactRef
    candidate_ref: ImmutableArtifactRef
    generation_provenance: GuidelineGenerationProvenance
    policy_ref: ImmutableArtifactRef
    fallback_refs: tuple[ImmutableArtifactRef, ...]
```

`GuideRuntimePreflightDecision`은 #180 local domain 전용이며 public API 상태가 아니다. `PASS | LIMITED | REJECTED | STALE`, AI Job state, `GuidelineCardStatus`, `Rag15ApprovalStatus`와 혼동하지 않는다.

---

## 5. READY의 의미

READY가 뜻하는 것은 정확히 하나다.

```text
Runtime에서 사용할 RAG-15 static candidate/configuration의 승인 및 identity가
현재 실행 객체와 exact-match한다.
```

최종 READY는 다음이 모두 성립할 때만 허용된다.

```text
Approval Pack production_consumable
        AND Generator provenance exact-match
        AND Policy self-integrity valid
        AND Policy ref exact-match
        AND Fallback self-integrity valid
        AND Fallback code/ref set exact-match
```

READY는 다음 중 **어느 것도** 의미하지 않는다.

```text
Guide Evidence Ready              X
Generator executed                X
Card generated                    X
Citation authorized               X
Release passed                    X
Production enabled                X
Public enabled                    X
```

**This contract does NOT authenticate Guide evidence.**

**Production Evidence Handoff remains dependent on Assessment / Eligibility Authority work (#712 and its prerequisite decisions).**

---

## 6. 범위 밖 (Explicit downstream blockers)

본 계약은 다음을 포함하지 않으며, 각 항목은 별도 선행 작업에 의존한다.

| 미구현 항목 | 차단 원인 |
| --- | --- |
| `GuideEvidenceHandoffRequest` authoritative 조립 | #712 Assessment / Eligibility Authority persistence & issuer |
| `VerifiedGuideEvidenceHandoff` 생성 | 동일 |
| Production `GuideEvidenceAuthorityReader` 완성 | 동일 |
| assessment validity 판정 소비 | #722 계약의 후속 issuer 구현 |
| Generator 실행 / Finalizer 실행 | 위 evidence 경계 미완성 |
| Claim-Citation Validator, Citation Authorization | 동일 |
| Safety / Release Gate, LangGraph, Handler Registry | 동일 |
| DB / migration / Guide API / `PUBLIC_TRACK_F` | 본 slice 범위 밖 |

특히 `VerifiedGuideEvidenceHandoff → EvidenceGateOutcome` 변환기를 본 계약에서 만들지 않는다. #711 content hydration이 병합되었다는 사실은 authority 완료를 의미하지 않는다.

---

## 7. 후속 slice

```text
authoritative retrieval selection + hydrated content + assessment / eligibility authority
        ↓
GuideEvidenceHandoffRequest
        ↓
VerifiedGuideEvidenceHandoff
        ↓
RAG-15 production input integration
```

위 연결은 #712 authority 완료 이후 별도 #180 evidence-input integration slice에서 다룬다.
