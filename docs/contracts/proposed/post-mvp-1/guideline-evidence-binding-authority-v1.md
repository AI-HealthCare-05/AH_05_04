# Dynamic Guideline Evidence Binding Authority 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Proposed · 구현·로컬 검증 완료 · 담당 리뷰 대기 · Current 아님 |
| 구현 담당 | 정현우 (`@ceohwj`) — AI/RAG Guideline Card·Binding |
| 책임 리뷰 | 송은영 (`@phina-io`) — authority 경계·무결성 |
| 상위 승인 근거 | [Guideline Card typed port 계약 v1](../../targets/post-mvp-1/guideline-card-v1.md), [RAG-15 Production Guideline Evidence Input 계약 v1](./guideline-production-evidence-input-v1.md), [Guide Runtime Preflight 계약 v1](./guide-runtime-preflight-v1.md) |
| 추적 Issue | [#781](https://github.com/AI-HealthCare-05/AH_05_04/issues/781) |

## 목적과 상태

`GuidelineCard` finalizer는 성공 경로에서 `ApprovedGuidelineEvidenceBinding[]`과
`GuidelineApprovalVerifierPort`를 요구한다. 그러나 #774까지의 production runtime에는
실제 `GuidelineCardDraft`의 claim/citation과 production evidence를 **요청 단위로**
결속하는 authority가 없었다.

```text
ProductionGuidelineEvidenceSet + GuidelineCardDraft
        ↓
        ???        ← #781이 채우는 구간
        ↓
ApprovedGuidelineEvidenceBinding[] + GuidelineApprovalVerifierPort
        ↓
finalize_guideline_card()
```

이 계약은 그 구간을 순수·동기·요청 범위 seam으로 확정한다. 문서와 구현의 병합은
Generator orchestration, Card 영속화, Citation 승인, Release 승인,
`PUBLIC_TRACK_F` 활성화를 의미하지 않는다.

## 상류 정본

```text
#729 preflight_guide_runtime() → READY
        ↓
#765 ReadyGuideGenerationInputs
        ↓
#774 project_guideline_evidence_from_handoff()
        ↓
ProductionGuidelineEvidenceSet
        +
GuidelineCardDraft
        +
pinned MedicationIdentityRef[]
        ↓
#781 build_request_scoped_guideline_authority()
```

`ProductionGuidelineEvidenceSet`이 **evidence 입력 정본**이다.

legacy RAG-14 Evidence Gate domain은 **금지**이며 import·재구성·wrapping하지 않는다.

```text
evidence_gate.EvidenceGateOutcome
evidence_gate.GatePassedKnowledgeEvidenceSelection
evidence_gate.canonical_gate_selection_hash()
```

`selection_projection_sha256`의 의미는 항상
`compute_production_guideline_evidence_selection_hash()`이며 legacy hash 값은
production binding으로 인정하지 않는다. 이 분리는
`ai_worker/tests/rag/test_guideline_production_evidence.py`의 AST 기반 source-level
guard가 신규 모듈에도 적용되어 강제한다.

## Binding derivation rule

binding은 **실제 draft에 등장한 조합에 대해서만** 만든다.

```text
for claim in draft.claims:
    for citation in claim.citations:
        → binding 1개
```

`all medications × all evidence × all scopes` Cartesian product는 만들지 않는다.

각 binding이 결속하는 값:

| binding 필드 | 출처 |
| --- | --- |
| `medication_identity` | `claim.medication_identity` |
| `scope` | `claim.scope` |
| `action_class` | `claim.action_class` |
| `evidence_key` | `citation.evidence_key` |
| `assessment_artifact_ref` | matching `ProductionGuidelineEvidence.assessment_artifact_ref` |
| `selection_projection_sha256` | `compute_production_guideline_evidence_selection_hash(matching evidence)` |
| `action_text_sha256` | `sha256(claim.action_text UTF-8 bytes)` |

`ApprovedGuidelineEvidenceBinding.create()`의 self-hash payload와 signature는
변경하지 않는다.

결과 순서는 draft claim 순서 → citation 순서로 결정적이다.

### Citation exact-match

`evidence_key`만으로 binding을 만들지 않는다. 같은 key의 production selection과
아래 좌표 **전체**가 exact-match해야 한다.

```text
evidence_key
source_snapshot_id
source_snapshot_member_id
source_code
source_version
locator
content_sha256
```

이는 좌표 동일성 확인이며 **authority 재판정이 아니다**. Source 승인, member
eligibility, assessment 유효기간, freshness는 #760이 이미 종료했고 다시 평가하지
않는다.

### Medication pin exact membership

모든 `claim.medication_identity`는 caller가 넘긴
`tuple[MedicationIdentityRef, ...]`에 exact membership이어야 한다.

identity 생성·정규화·canonical code 보정·partial matching은 하지 않는다.
pin되지 않은 medication을 draft가 참조하면 전체 derivation이 실패한다.

### Binding identity key

finalizer와 동일하게 다음을 binding identity로 취급한다.

```text
(evidence_key, medication_identity, scope)
```

동일 identity가 둘 이상 나오면 malformed input으로 **fail closed**한다. exact
semantic duplicate도 silent deduplication·repair·normalization하지 않는다.

정상 Generator parser는 `(medication, scope)` group과 evidence를 canonical하게
정리하므로 production 정상 출력에서는 중복이 발생하지 않는다.

## Binding artifact identity

runtime caller가 선택할 수 없다. 저장소 조사 결과 승인된 production binding
identity는 존재하지 않았고(`guideline-evidence-binding`은 테스트 fixture code로만
등장했으며 `...@synthetic-N` 버전은 production으로 승격하지 않는다), 이 계약에서
다음을 고정한다.

```text
artifact_code = "guideline-evidence-binding"
version       = "guideline-evidence-binding-v1"
```

derivation rule 자체의 버전은 별도로 고정한다.

```text
GUIDELINE_EVIDENCE_BINDING_DERIVATION_VERSION = "guideline-evidence-binding-derivation-v1"
```

## Fail-closed 결과 표현

새 framework·registry·class hierarchy를 만들지 않는다. derivation과 factory는 단일
enum과 두 개의 작은 typed outcome만 쓴다.

```python
GuidelineAuthorityFailureReason:
    PREFLIGHT_NOT_READY
    REQUEST_INVALID
    DRAFT_INVALID
    MEDICATION_NOT_PINNED
    EVIDENCE_NOT_FOUND
    CITATION_MISMATCH
    DUPLICATE_BINDING
```

`bindings` / `authority`는 `reason is None`일 때에만 채워진다. partial success는
없으며, 하나라도 invalid하면 binding set 전체를 발급하지 않는다.

## Root of trust 경계 — 정확한 보장 범위

`build_request_scoped_guideline_authority()`는 `GuideRuntimePreflightOutcome`만
받고 최소 다음을 요구한다.

```text
type(preflight_outcome) is GuideRuntimePreflightOutcome
decision == READY
reason is None
ready_context is not None
```

그 외 형태(맨 `ReadyGuideRuntimeContext`, BLOCKED outcome, context 없는 READY,
blocking reason이 남은 READY)는 모두 fail closed한다.

### UNRESOLVED

```text
Python value provenance cannot be authenticated within this pure seam.
The production guarantee is sequencing through #729, not unforgeable object origin.
```

`GuideRuntimePreflightOutcome`과 `ReadyGuideRuntimeContext`는 일반 frozen
dataclass이며 그 자체로 위조 불가능한 capability가 아니다. 따라서 이 계약은
**"READY outcome 객체이므로 authenticity가 증명됐다"고 주장하지 않는다.** #781이
보장하는 것은 production orchestration의 sequencing 경계

```text
#729 preflight function에서 READY → #781 authority factory
```

이며, 이를 위조 방지 보안 primitive로 문서화하지 않는다. 이 한계를 해소하기 위해
새 signature/token/capability framework를 발명하거나 secret 기반 서명을 추가하거나
DB provenance를 재조회하지 않는다.

## Request-scoped approval verifier

기존 port는 변경하지 않는다.

```python
class GuidelineApprovalVerifierPort(Protocol):
    def verify(self, artifact_ref: ImmutableArtifactRef) -> (
        GuidelineApprovalVerificationSuccess | GuidelineApprovalVerificationFailure
    ): ...
```

`RequestScopedGuidelineApprovalVerifier`는 새 static approval을 발급하지 않는
bridge다. success를 반환할 수 있는 ref는 정확히 다음 둘뿐이다.

### 1. Static pin membership

```text
ReadyGuideRuntimeContext.policy_ref
ReadyGuideRuntimeContext.fallback_refs (각 항목)
```

다음은 **승인 대상이 아니다.** provenance/context일 뿐이므로 verify 시 실패한다.

```text
approval_pack_ref
candidate_ref
```

### 2. Dynamic binding refs — independent recomputation

verifier는 issuer가 반환한 tuple을 whitelist로 신뢰하지 않는다. 금지 구조:

```python
RequestScopedGuidelineApprovalVerifier(
    approved_binding_refs=tuple(b.artifact_ref for b in issuer_output)   # 금지
)
```

verifier는 authoritative request inputs

```text
ProductionGuidelineEvidenceSet
MedicationIdentityRef[]
GuidelineCardDraft
```

에서 위의 canonical derivation rule을 **스스로 다시 실행**해 expected binding refs를
계산한다. issuer와 verifier는 동일한 순수 derivation 함수를 공유하지만, verifier의
expected refs source는 언제나 authoritative inputs이며 issuer output tuple이 아니다.

따라서 `RequestScopedGuidelineAuthority.bindings`에 forged binding을 끼워 넣어도
`approval_verifier`가 그것을 승인하지 않는다.

### 실패하는 ref

```text
unknown artifact ref
approval_pack_ref
candidate_ref
unknown policy / fallback
derivation 결과에 없는 ref
tampered binding ref
issuer tuple에만 존재하는 forged binding
ImmutableArtifactRef가 아닌 입력
```

## Verifier artifact identity

`GuidelineApprovalVerificationSuccess.verifier_artifact_ref`는 Card/Citation
provenance에 보존되므로 임의 ID가 아니다.

```text
artifact_code = "request-scoped-guideline-approval-verifier"
version       = "request-scoped-guideline-approval-verifier-v1"
```

`content_sha256`은 저장소의 기존 verifier identity convention
(`rag_runtime.evidence_authority.compute_verifier_artifact_ref()`)과 동일하게
**canonical contract projection의 RFC 8785 JCS SHA-256**이다. 소스 파일 전체 byte
hash가 아니며, 임의 상수 문자열 하나의 SHA-256도 아니다.

preimage가 결속하는 stable semantics:

```text
domain
artifact code / version
contract (GuidelineApprovalVerifierPort)
approvable artifact classes
rejected artifact classes (approval pack / candidate)
static pin source
dynamic binding derivation source
binding artifact code / version
binding derivation version
production selection projection version
failure behavior
```

preimage에 포함하지 않는 값:

```text
patient / request identity
medication
evidence
draft
binding refs
evaluated_at
timestamp
handoff_sha256
```

같은 implementation/configuration이면 요청이 달라도 verifier identity는 동일하다.
self-hash는 rule identity·integrity만 증명하며 **approval 자체가 아니다.**

Assessment/Eligibility 등 다른 domain의 verifier identity는 재사용하지 않는다.

## Combined authority factory

```python
build_request_scoped_guideline_authority(
    preflight_outcome: GuideRuntimePreflightOutcome,
    *,
    evidence: ProductionGuidelineEvidenceSet,
    medication_identities: tuple[MedicationIdentityRef, ...],
    draft: GuidelineCardDraft,
) -> RequestScopedGuidelineAuthorityOutcome
```

성공 시:

```python
@dataclass(frozen=True, slots=True)
class RequestScopedGuidelineAuthority:
    bindings: tuple[ApprovedGuidelineEvidenceBinding, ...]
    approval_verifier: RequestScopedGuidelineApprovalVerifier
```

`approval_verifier`는 `bindings` field를 읽지 않는다.

## Pure / I/O-free 경계

이 seam은 순수·동기·요청 범위·결정적이다. 다음이 없다.

```text
SQLAlchemy / DB / repository / migration / persistence
network / OpenAI
clock / datetime.now() / utcnow
async / event loop / worker / API
```

특히 **#746 Assessment/Eligibility Authority DB read를 다시 수행하지 않는다.**
#760·#774에서 이미 authority가 소비·투영됐으므로 second read는 authority
duplication과 TOCTOU 가능성을 만든다.

이 경계는 `ai_worker/tests/rag/test_guideline_evidence_binding.py`의 AST 기반
guard(import 그래프, async 함수 부재, `now`/`utcnow` attribute 부재)가 강제한다.

## Finalizer pure composition

#180 orchestration wiring 없이 아래 합성이 성립함을 테스트로 고정한다.

```text
#729 READY outcome
+ ProductionGuidelineEvidenceSet
+ pinned MedicationIdentityRef[]
+ GuidelineCardDraft
        ↓
build_request_scoped_guideline_authority()
        ↓
bindings + approval_verifier
        ↓
GuidelineCardRequest → finalize_guideline_card()
        ↓
GuidelineCardStatus.GENERATED
```

DB·network 없이 동작하며, 생성된 `GuidelineCitation`의
`guideline_evidence_binding_ref`와 `guideline_evidence_binding_verifier_ref`가 각각
derivation 결과와 verifier identity로 채워진다.

## 구현 참조

```text
ai_worker/tasks/rag/guideline_evidence_binding_authority.py       (신규)

ai_worker/tests/rag/test_guideline_evidence_binding.py            (신규)
ai_worker/tests/rag/test_request_scoped_guideline_approval.py     (신규)
ai_worker/tests/rag/test_guideline_production_evidence.py         (guard 대상 확장)
```

`ai_worker/tasks/rag/guideline_card.py`는 수정하지 않는다. 기존 public seam
(`ApprovedGuidelineEvidenceBinding.create()`, `GuidelineApprovalVerifierPort.verify()`,
`finalize_guideline_card()`)이 그대로 충분했다.

## 명시적 미구현

```text
binding persistence / repository / table / migration
#746 DB second read
Generator invocation wiring
#180 guide_orchestration.py 확장
GuidelineCard persistence
ClaimCitationCandidateSet · ClaimSupportVerificationReceipt
Citation Authorization · CitationAuthorizationReceipt
Release Gate · LangGraph · Worker · Guide API
EVIDENCE_INSUFFICIENT / EVIDENCE_CONFLICTED / EVIDENCE_STALE runtime mapping
PUBLIC_TRACK_F
```

완료 상태는 `DYNAMIC_GUIDELINE_EVIDENCE_BINDING_AUTHORITY_READY`이며, 이는 Generator
orchestration, Card 영속화, Citation 승인, Release 승인, 공개 활성화를 의미하지
않는다.
