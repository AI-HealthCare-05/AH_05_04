# Guide Card → Claim/Citation Validation 계약 v1 (#794)

상태: Proposed · 구현 PR 리뷰 대상
범위: `#180` Slice 3 · Track F Runtime
구현: `ai_worker/tasks/rag/guide_claim_citation_validation.py`
검증: `ai_worker/tests/rag/test_guide_claim_citation_validation.py`

---

## 1. 위치

```text
#787 GuideGenerationCardOutcome (PR #788)
        ↓  ← 이 계약
ClaimCitationCandidateSet
        ↓
ClaimSupportVerificationReceipt[]
        ↓
validate_claim_citations()
        ↓
ValidatedCitationSelection   ← 종료점
```

`CitationAuthorizationRequest`, `CitationAuthorizationReceipt`,
`AuthorizedCitationSelection`, PATIENT_CITATION Guard, Release Gate는
이 계약의 범위 밖이며 구현하지 않는다.

## 2. #787 GuidelineCardOutcome consumer

입력은 `GuideClaimCitationValidationRequest(guide_outcome)` 하나뿐이다.
`GuidelineCard`, `VerifiedGuideEvidenceHandoff`, generation provenance,
validator policy, support assessment ref를 caller가 다시 넘길 수 없다.
중복 input 경로를 허용하면 caller가 서로 짝이 맞지 않는 Card와 handoff를
조립할 수 있으므로 금지한다.

정본 위치:

| 값 | 정본 |
| --- | --- |
| Card | `guide_outcome.card_outcome.card` |
| upstream handoff | `guide_outcome.upstream_outcome.ready_inputs.evidence_handoff` |
| generation provenance | `card.provenance` (`prompt_ref` / `model_ref` / `parser_ref`) |

## 3. GENERATED-only boundary

다음을 모두 만족할 때만 projection을 시작한다.

```text
decision == COMPLETED
card_outcome != None
card_outcome.status == GENERATED
card_outcome.card != None (claims 비어 있지 않음)
upstream_outcome.ready_inputs != None
```

그 외 — orchestration `STOPPED`, `NO_RESULT`, `LIMITED`, `STALE`,
`VALIDATION_REJECTED`, fallback only — 는 `CARD_NOT_ELIGIBLE`에서 멈추고
candidate를 단 하나도 만들지 않는다. fallback 문구를 `ClaimCandidate`로
만들지 않는다.

## 4. Card ↔ VerifiedGuideEvidenceHandoff exact binding

Card citation은 같은 `evidence_key`의 `VerifiedGuideEvidenceSelection`을 찾고
다음 11개가 전부 exact-match해야 한다.

```text
evidence_key
source_snapshot_id / source_snapshot_member_id
source_code / source_version
locator / content_sha256
assessment_artifact_ref
eligibility_receipt_ref
retrieval_receipt_ref
verifier_artifact_ref
```

하나라도 어긋나면 `CARD_PROJECTION`에서 fail closed한다. handoff 내 `evidence_key`
중복, 한 claim 안의 `evidence_key` 중복도 fail closed다. silent correction,
normalization, dedup, drop은 없다.

Source approval / member eligibility / assessment validity / freshness는 #760이
이미 판정했으므로 재판정하지 않는다. 이 단계는 좌표 identity 확인일 뿐이다.

## 5. ClaimCitationCandidateSet projection

```text
target.target_kind = "GUIDELINE_CARD"
target.target_ref  = card.artifact_ref.content_sha256

ClaimCandidate
  claim_key    = GuidelineClaim.claim_key
  claim_kind   = MEDICAL
  text_digest  = sha256(action_text UTF-8)
  display_order= Card claim 순서 1..N

CitationCandidate
  citation_key = f"{claim_key}:{evidence_key}"
  claim_key    = parent claim
  source_type  = LIFESTYLE_GUIDELINE
  display_order= Card 전체 citation flatten 순서 1..M

generation_provenance = card.provenance 의 prompt/model/parser
validator_policy_ref  = GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_REF
```

`GuidelineCardProvenance.validator_ref`는 RAG-16 `GenerationProvenance`의 field가
아니므로 넣지 않는다.

### LifestyleGuidelineEvidenceRef

```text
guideline_evidence_ref  = f"{source_snapshot_id}/{source_snapshot_member_id}/{evidence_key}"
guideline_artifact_ref  = citation.guideline_evidence_binding_ref   (#781 binding artifact)
source_version / locator / content_sha256 = Card citation 값
execution_provenance    = handoff selection에서 기계적 projection
```

임의의 `source_snapshot_ref` artifact를 합성하지 않는다. production evidence에는
그런 artifact가 없고, 만들어 넣으면 citation에 위조된 artifact identity가 들어간다.

### SourceExecutionProvenance

`VerifiedGuideEvidenceSelection`에서 `source_code`, `source_version`,
`member_kind`, `endpoint_code`, `operation_code`, `artifact_code`,
`artifact_version`, `request_source_decision_ref`, `request_member_decision_ref`를
그대로 projection한다. 새 Decision ref를 만들지 않으며, legacy
`EvidenceGateOutcome` / synthetic Source Governance를 runtime authority로 쓰지
않는다. #774 production evidence projection이 이 audit field들을 의도적으로
제외하므로 Card만 보고는 복원할 수 없고, 상류 handoff가 유일한 정본이다.

## 6. Multi-citation claim support semantics

한 Claim이 여러 Citation을 가질 수 있다. 따라서 다음은 금지한다.

```text
첫 citation의 assessment_artifact_ref 사용
마지막 citation의 assessment_artifact_ref 사용
임의 citation 하나를 claim 전체 support로 대표
```

claim당 하나의 deterministic support assessment artifact를 만들고 그 preimage에
해당 claim의 **모든** citation을 결속한다.

## 7. Claim-level support assessment artifact

```text
artifact_code    = guideline-claim-support-assessment
artifact_version = guideline-claim-support-assessment-v1
projection       = guideline-claim-support-derivation-v1
```

preimage (canonical JSON + SHA-256):

```text
projection_version
card_artifact_ref
claim_key
medication_identity
scope
action_class
action_text_sha256
citations = canonical 정렬 [
    evidence_key,
    assessment_artifact_ref,
    guideline_evidence_binding_ref,
    guideline_evidence_binding_verifier_ref,
    source_version,
    locator,
    content_sha256,
]
```

citation entry는 자기 canonical 직렬화 기준으로 정렬하므로, 같은 Card에서 claim의
citation tuple 순서만 바뀌어도 support identity는 같다. 이 canonicalization은
preimage 안에만 있고, runtime `display_order`는 Card 순서를 그대로 따른다. 둘을
혼동해 데이터를 조용히 reorder하지 않는다.

`card_artifact_ref`가 preimage에 들어가므로 Card 자체가 달라지면 — Generator가
citation을 다른 순서로 낸 Card를 포함해 — support identity도 달라진다. 이는
Card 정체성 결속이며 citation 정렬과 별개다.

artifact code/version은 caller가 선택할 수 없다.

## 8. Support verifier identity

```text
artifact_code    = guideline-claim-support-verifier
artifact_version = guideline-claim-support-verifier-v1
```

`compute_guideline_claim_support_verifier_artifact_ref()`는 기존 verifier identity
convention(`guideline_evidence_binding_authority.compute_guideline_approval_verifier_artifact_ref`,
그 위로 `rag_runtime.evidence_authority.compute_verifier_artifact_ref`)과 동일하게
canonical contract projection JCS SHA-256이다. `sha256(b"constant")`가 아니고
source file byte hash도 아니다.

preimage는 domain, artifact identity, derivation version, 주장 가능한 claim kind와
support status, input authority(Guideline Card + Guideline Evidence Binding +
binding verifier + upstream handoff provenance), 생성하는 assessment artifact
identity, 결속된 pure kernel projection version, fail-closed 동작을 담는다.
request-specific 값은 하나도 없으므로 요청이 달라져도 ref는 같다.

다른 domain의 verifier ref를 "의미가 비슷하다"는 이유로 재사용하지 않는다.

이 ref는 규칙의 identity와 무결성만 증명한다. NLI나 semantic entailment 판정이
수행됐다는 증거가 아니며, 이 계약은 그런 판정을 하지도 주장하지도 않는다.

## 9. validator_policy_ref

```text
artifact_code    = guide-claim-citation-validator-policy
artifact_version = guide-claim-citation-validator-policy-v1
```

#794 이전 저장소에는 production Claim/Citation validator policy identity가 없었다.
`claim-citation-validator`는 synthetic test fixture code로만 존재했고 synthetic
fixture identity는 production으로 승격하지 않는다. 이 계약에서 고정하며 caller
parameter가 아니다. digest는 실제 적용되는 validation 계약 — pure kernel의
projection version, Card 유래 candidate semantics, support receipt issuer — 의
canonical projection이다.

## 10. ClaimSupportVerificationReceipt derivation

candidate set을 먼저 만든 뒤 Claim당 정확히 하나 발급한다.

```text
claim_key             = claim.claim_key
support_status        = SUPPORTED
claim_text_digest     = claim.text_digest
assessment_ref        = claim-level support assessment artifact
verifier_artifact_ref = GUIDELINE_CLAIM_SUPPORT_VERIFIER_REF
projection_sha256     = canonical_claim_support_projection_hash(candidate_set, claim_key)
```

개수는 citation이 아니라 claim을 따른다.

```text
1 Claim + 1 Citation  → Receipt 1
1 Claim + 2 Citations → Receipt 1
2 Claims              → Receipt 2
```

Receipt는 Generator 자기 선언을 승인 근거로 쓰지 않는다. 정본은 이미 검증된
Guideline Card, Guideline Evidence Binding, binding verifier, upstream authoritative
evidence provenance다. 새 NLI/semantic model을 호출하지 않는다.

## 11. 기존 validate_claim_citations 재사용

```python
validate_claim_citations(candidate_set, support_receipts)
```

만 호출한다. validator를 재구현하지 않고, 기존 `execution_status` / `decision` /
`reasons`를 새 enum으로 재매핑하지 않는다. 거부 결과도 `validation_outcome`에
그대로 보존한다.

성공 조건:

```text
execution_status == EVALUATED
decision         == VALIDATED
validated_selection != None
```

## 12. Outcome

```text
GuideClaimCitationDecision : VALIDATED | STOPPED
GuideClaimCitationStage    : CARD_NOT_ELIGIBLE | CARD_PROJECTION | CLAIM_CITATION_VALIDATION
```

`CLAIM_SUPPORT_AUTHORITY` stage는 두지 않는다. projection이 성공한 뒤의
support assessment와 receipt tuple은 candidate set의 total deterministic function이라
독립적으로 실패할 수 없다.

**이것은 이후 Claim/Citation validator가 실패하지 않는다는 뜻이 아니다.**
NFC가 아닌 `claim_key`, 빈 `claim_key`, duplicate `claim_key` 등 generic
Claim/Citation identity 위반은 기존 `validate_claim_citations()`가 계속 소유하며,
이 adapter는 그 검증을 복제하지 않고 `GuidelineClaim.claim_key`를 그대로 projection
한다. 같은 규칙에 소유자가 둘이 되면 서로 어긋날 수 있기 때문이다.

정상 #179 → #787 production chain에서는 #179 finalizer가 non-NFC 및
duplicate `claim_key`를 먼저 거부하므로 이 입력은 GENERATED Card로 도달하지 않는다.
(`guideline_card._is_valid_draft_shape()`의 `_bounded_nfc(claim.claim_key, 100)` 및
`claim.claim_key in claim_keys` 검사)

아래 경로는 #794의 downstream ownership과 defense-in-depth 동작을 고정하기 위해
직접 구성된 비정상 Card를 입력했을 때의 경계다. #794는 upstream 검증을 복제하지
않고 generic candidate identity 검증을 기존 `validate_claim_citations()`에 위임한다.

이 경우 실행은 projection과 support receipt 생성까지 정상 진행한 뒤:

```text
decision      = STOPPED
stopped_stage = CLAIM_CITATION_VALIDATION
candidate_set / support_receipts / validation_outcome = 보존
validated_selection = None
```

으로 fail closed한다. claim key를 normalize하거나 duplicate claim을
`CARD_PROJECTION`에서 조용히 제거·수정하지 않는다. 회귀 테스트:
`test_non_nfc_claim_key_is_rejected_by_the_existing_validator_not_by_projection`,
`test_duplicate_claim_key_is_rejected_by_the_existing_validator_without_silent_dedup`.

#794 Guide projection은 이미 검증된 Guideline Card, Guideline Evidence Binding,
binding verifier, upstream authoritative handoff를 support authority로 사용하므로
`ClaimSupportStatus.SUPPORTED`만 발급한다. 따라서 generic `claim_citation_validator`의
`MEDICAL_CLAIM_NOT_SUPPORTED` / `CLAIM_NOT_SUPPORTED` 같은 unsupported
support-status branch는 현재 Guide adapter를 통해서는 구조적으로 도달하지 않는다.
이는 해당 branch를 제거한다는 뜻이 아니다 — 다른 candidate producer가 같은 generic
kernel을 소비할 수 있으므로 kernel capability는 그대로 유지한다. #794는 새로운 NLI나
semantic support 판정을 수행하지 않는다. 회귀 테스트:
`test_guide_projection_only_issues_supported_claim_assertions`.

Outcome은 `guide_outcome`, `candidate_set`, `support_receipts`,
`validation_outcome`, `validated_selection`, `stopped_stage`를 보존한다.
`PASS | LIMITED | REJECTED | STALE` 같은 공개 runtime 상태는 만들지 않는다.

## 13. 순수 경계

동기·무 I/O·결정론적이다. DB, SQLAlchemy, repository, migration, network, clock,
`datetime.now()`, async, worker가 없다. `backend.*`를 import하지 않는다.
legacy RAG-14 `EvidenceGateOutcome` / `GatePassedKnowledgeEvidenceSelection`을
import하거나 재구성하지 않는다. `citation_authorization.py`,
`citation_finalizer.py`, `claim_citation_validator.py`는 수정하지 않는다.
AST 기반 source guard 테스트가 이를 강제한다.

## 14. 범위 밖

```text
CitationAuthorizationRequest / Receipt
build_citation_authorization_request()
citation_finalizer wiring
AuthorizedCitationSelection
DiscardGeneratedContent wiring
PATIENT_CITATION Guard 발급·저장
Release Gate
DB / repository / migration
Worker / Guide API / LangGraph full graph
PUBLIC_TRACK_F
```

## 15. 완료 상태

```text
GUIDE_CLAIM_CITATION_VALIDATION_READY
```

이는 Citation Authorized, PATIENT_CITATION Approved,
`AuthorizedCitationSelection` 생성, Release Approved, Guide persisted,
`PUBLIC_TRACK_F` enabled 중 어느 것도 의미하지 않는다.
