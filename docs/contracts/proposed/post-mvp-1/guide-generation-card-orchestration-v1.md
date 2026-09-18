# Guide Generator → Guideline Card Orchestration 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Proposed · 구현·로컬 검증 완료 · 담당 리뷰 대기 · Current 아님 |
| 구현 담당 | 정현우 (`@ceohwj`) — AI/RAG Guide Runtime Orchestration |
| 책임 리뷰 | 송은영 (`@phina-io`) — authority 경계·무결성 |
| 상위 승인 근거 | [Guideline Card typed port 계약 v1](../../targets/post-mvp-1/guideline-card-v1.md), [RAG-15 Production Guideline Evidence Input 계약 v1](./guideline-production-evidence-input-v1.md), [Dynamic Guideline Evidence Binding Authority 계약 v1](./guideline-evidence-binding-authority-v1.md), [Guide Runtime Preflight 계약 v1](./guide-runtime-preflight-v1.md) |
| 추적 Issue | [#787](https://github.com/AI-HealthCare-05/AH_05_04/issues/787) |

## 목적과 상태

#180 Slice 1(`orchestrate_guide_preflight_handoff()`)은 `READY_FOR_GENERATION`에서
멈춘다. Generator를 실제로 실행하고 그 결과를 Guideline Card까지 연결하는 구간은
비어 있었다. 이 계약은 그 Slice 2 구간을 확정한다.

```text
#765 READY_FOR_GENERATION
        ↓
#774 project_guideline_evidence_from_handoff()
        ↓
GuidelineGenerationRequest
        ↓
await generator.generate()
        ↓
GuidelineCardDraft | GuidelineGenerationFailure
        ↓
#781 dynamic / static approval authority
        ↓
GuidelineCardRequest
        ↓
finalize_guideline_card()
        ↓
GuidelineCardOutcome        ← 이 계약의 종료점
```

구현 모듈은 `ai_worker/tasks/rag/guide_generation_card_orchestration.py`이며
`orchestrate_guide_generation_card()` 하나가 public entry point다.

문서와 구현의 병합은 Citation 승인, Release 승인, Card 영속화,
`PUBLIC_TRACK_F` 활성화를 의미하지 않는다.

## 상류 정본

| 입력 | 정본 |
| --- | --- |
| preflight / handoff | `GuideOrchestrationRequest` (Slice 1 그대로) |
| evidence | `project_guideline_evidence_from_handoff(ready_inputs.evidence_handoff)` |
| policy | `upstream_request.preflight_request.policy` |
| fallbacks | `upstream_request.preflight_request.fallbacks` |
| provenance | #729 READY `ReadyGuideRuntimeContext.generation_provenance` |
| medication identities | `GuideGenerationCardOrchestrationRequest.medication_identities` |

`GuideGenerationCardOrchestrationRequest`는 policy·fallbacks·provenance를 **다시 받지
않는다.** 다시 받으면 caller가 #729가 승인하지 않은 policy나 fallback set을 Card에
넘길 수 있기 때문이다.

evidence 입력 정본은 `ProductionGuidelineEvidenceSet` 하나이며, legacy RAG-14
Evidence Gate domain은 **금지**다. import·재구성·wrapping·converter 모두 없다.

```text
evidence_gate.EvidenceGateOutcome
evidence_gate.GatePassedKnowledgeEvidenceSelection
evidence_gate.canonical_gate_selection_hash()
```

## Slice 1 불변

`orchestrate_guide_preflight_handoff()`의 semantics는 변경되지 않았다. Slice 1 모듈은
여전히

```text
#729 preflight → #760 authoritative handoff → READY_FOR_GENERATION → STOP
```

이며, `GuidelineGenerationRequest`·`finalize_guideline_card`·
`guideline_evidence_binding_authority`를 import하지 않는다. Slice 1의
`Generator call == 0` 테스트와 containment guard는 그대로 유지되고, 이 계약의 테스트가
Slice 1의 import graph를 다시 한 번 고정한다.

## 실행 순서

```text
1. orchestrate_guide_preflight_handoff()
2. READY_FOR_GENERATION 확인 (아니면 STOPPED / UPSTREAM)
3. project_guideline_evidence_from_handoff()
4. GuidelineGenerationRequest 구성
5. await generator.generate()          ← 정확히 1회
6A. GuidelineCardDraft
      → build_request_scoped_guideline_authority()
      → GuidelineCardRequest(approved_evidence_bindings=authority.bindings)
      → finalize_guideline_card(approval_verifier=authority.approval_verifier)
6B. GuidelineGenerationFailure
      → build_request_scoped_guideline_static_approval_verifier()
      → GuidelineCardRequest(draft=None, approved_evidence_bindings=())
      → finalize_guideline_card(approval_verifier=static_verifier)
```

### Pre-generation stop

`REQUEST` 무효, #729 `BLOCKED`, #760 handoff `REJECTED` 중 어느 것이든
`STOPPED / UPSTREAM`에서 멈추며 **`generator.generate` 호출 횟수는 0**이다. 상류
outcome은 `upstream_outcome`에 원본 그대로 보존되고 새 reason으로 재해석되지 않는다.

### Generation failure

`GuidelineGenerationFailure`에는 draft가 없다. 따라서

- fake / empty / synthetic `GuidelineCardDraft`를 만들지 않는다
- #781 dynamic binding authority를 호출하지 않는다
- `approved_evidence_bindings`는 `()`다

failure 값은 `GuidelineCardRequest.generation_failure`에 **그대로** 실려
`finalize_guideline_card()`로 간다.

### Static approval bridge (#781 최소 확장)

`build_request_scoped_guideline_static_approval_verifier(preflight_outcome)`가
`guideline_evidence_binding_authority`에 추가됐다. #729 READY가 아니면 `None`을
돌려주어 fail closed한다.

승인 대상은 정확히 다음 두 가지다.

```text
ReadyGuideRuntimeContext.policy_ref
ReadyGuideRuntimeContext.fallback_refs
```

거부 대상은 dynamic binding ref, `approval_pack_ref`, `candidate_ref`, 그리고 모든
unknown artifact ref다. concrete verifier class는 계속 private이고, 새 verifier
artifact identity를 만들지 않으며 기존 `GUIDELINE_APPROVAL_VERIFIER_REF`를 재사용한다.
#729 READY가 sequencing boundary일 뿐 위조 불가능한 capability가 아니라는 #781의
root-of-trust 한계도 그대로 유지된다(UNRESOLVED).

## Failure mapping 복제 금지

다음 매핑의 정본은 **기존 `finalize_guideline_card()` 하나**이며 이 계약은 복제하지
않는다.

```text
PROVIDER_TIMEOUT
DEPENDENCY_UNAVAILABLE
VALIDATION_FAILED
PRESCRIPTION_STALE
EXECUTION_CONTEXT_STALE
UNSUPPORTED_REQUEST
```

orchestration은 fallback을 선택·재매핑·생성하지 않는다. 테스트는 AST 기반 guard로
이 모듈이 `GuidelineGenerationFailure` member 이름을 코드에서 참조하지 않음을 고정한다.

## Outcome

```text
GuideGenerationCardDecision : COMPLETED | STOPPED
GuideGenerationCardStage    : UPSTREAM | GENERATION | DYNAMIC_BINDING_AUTHORITY
```

둘 다 orchestration-local 상태이며 공개 Guide runtime·Job·release 상태가 아니다.
`PASS | LIMITED | REJECTED | STALE`와 runtime fallback 사영은 여기서 만들지 않는다.

`GuideGenerationCardOutcome`은 각 단계의 결과를 그대로 보존한다.

```text
upstream_outcome    : GuideOrchestrationOutcome (항상)
generation_result   : GuidelineCardDraft | GuidelineGenerationFailure | None
authority_outcome   : RequestScopedGuidelineAuthorityOutcome | None  (draft 경로에서만)
card_outcome        : GuidelineCardOutcome | None
stopped_stage       : GuideGenerationCardStage | None
```

`CARD_FINALIZATION` stage는 정의하지 않는다. finalizer에 도달하면 언제나
`GuidelineCardOutcome`이 나오고 그것이 이 slice의 종료점이므로, 그 단계에는 stop이
존재하지 않는다.

`COMPLETED`는 finalizer가 답을 냈다는 뜻일 뿐이며, 그 답 자체가 fallback일 수 있다.
Card 판정을 읽는 것은 caller의 몫이다.

## Fail closed

#781이 draft에 대해 authority 발급을 거부하면 `STOPPED / DYNAMIC_BINDING_AUTHORITY`로
멈추고 **finalizer를 호출하지 않는다.** 결속 불가능한 draft를 이 slice가 임의의
fallback 답으로 격하시키지 않는다.

Generator port가 약속한 `GuidelineCardDraft | GuidelineGenerationFailure` 외의 값을
돌려주면 `STOPPED / GENERATION`이며, Generator가 말하지 않은 failure reason으로
재해석하지 않는다.

## 순수성 경계

Generator port가 async이므로 `orchestrate_guide_generation_card()`만 async다. 그 외에
자체 DB·repository·network·clock·persistence는 없고 `backend.*`·`sqlalchemy.*`를
import하지 않는다.

## 이 계약이 구현하지 않는 것

```text
ClaimCitationCandidateSet / ClaimSupportVerificationReceipt
Citation Authorization / CitationAuthorizationReceipt / AuthorizedCitationSelection
DiscardGeneratedContent wiring
Release Gate
LangGraph full graph
GuidelineCard persistence / DB / migration / Worker / Guide API
EVIDENCE_INSUFFICIENT · EVIDENCE_CONFLICTED · EVIDENCE_STALE runtime mapping
PUBLIC_TRACK_F
```

#180의 기존 Citation pure kernel도 이 계약에서는 호출하지 않는다.

완료 상태 `GUIDE_GENERATOR_CARD_ORCHESTRATION_READY`는 Citation 승인, Release 승인,
Guide 영속화, `PUBLIC_TRACK_F` 활성화 중 어느 것도 의미하지 않는다.
