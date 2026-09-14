# RAG-16 Claim·Citation Finalization Pure Layer 설계 (Issue #180)

| 항목 | 값 |
| --- | --- |
| 대상 Issue | [#180](https://github.com/AI-HealthCare-05/AH_05_04/issues/180) |
| 구현 담당자 | 정현우 (`@ceohwj`) |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Product·Safety·Citation·Evaluation |
| Source 경계 리뷰 | 김지혜 (`@Jye-rookie`) — `PATIENT_CITATION` Source/Member 승인 의미 |
| 후속 Backend·DB 리뷰 | 송은영 (`@phina-io`) — Guard persistence·transaction 연결 시에만 필요 |
| 상위 계약 | [`rag-runtime-v1.md`](../../contracts/targets/post-mvp-1/rag-runtime-v1.md), [`safety-result-v2.md`](../../contracts/targets/post-mvp-1/safety-result-v2.md) |
| 문서 상태 | Draft implementation design · pure/local slice · Current Runtime 아님 |
| 기준 commit | `cacdd3e2` (`origin/develop`, 2026-09-13) |

## 1. 결정

RAG-16은 먼저 **Claim–Citation 검증과 Citation Authorization Receipt 검증을 수행하는 persistence-free
pure layer**로 구현한다. 이번 slice는 다음 세 모듈만 추가한다.

1. `claim_citation_validator.py`: 생성된 Claim과 Citation 후보의 구조·완전성·근거 결속을 검증하고,
   caller가 제공한 support Receipt를 exact-match한다.
2. `citation_authorization.py`: 검증된 Citation Selection을 승인 요청으로 투영하고, 외부 승인 경계에서 관측한
   Receipt가 요청과 exact-match하는지 순수 판정한다.
3. `citation_finalizer.py`: 검증된 Selection·승인 요청·관측 Receipt의 결속을 최종 확인하고, 승인된 Selection 또는
   생성 내용 폐기 지시를 반환한다.

`handler.py`, `retriever.py`, `generator.py`와 LangGraph 조립은 이번 slice에서 만들지 않는다. 이미 존재하는
RAG-14 `evidence_retrieval.py`·`evidence_gate.py`와 RAG-15 `guideline_card.py`를 다시 감싸는 빈 계층을 만들지
않고, 실제 Graph·Worker 소비자가 준비될 때 얇은 adapter로 추가한다.

이 결정은 `CONTRIBUTING.md`의 다음 원칙을 따른다.

- 실제 구현체·소비자가 없는 미래 확장용 interface를 만들지 않는다.
- 같은 의미의 DTO/model/schema를 계층마다 복제하지 않는다.
- 실제 I/O 소비자가 생기기 전에는 sync/async를 고정하는 미래용 Protocol을 만들지 않는다.
- AI Worker 흐름은 `Task/Consumer → Service → Repository/External Client`로 추적 가능하게 유지한다.

## 2. 착수 가능 범위와 차단 범위

### 2.1 지금 완결할 수 있는 범위

- `safety-result-v2.md`가 고정한 다섯 Citation Source 유형과 Claim support 규칙
- 의료 Claim의 Citation 필수·`SUPPORTED` 필수 판정
- 유형별 Evidence Ref의 정확히 하나(exactly-one) 결속
- Source version·locator·content hash·실행 provenance의 구조 검증
- Citation Selection의 결정적 canonical projection/hash
- Generator가 자체 표기한 `SUPPORTED`를 신뢰하지 않고 versioned support assessment Receipt를 검증하는 경계
- 원 `REQUEST/PASS`와 `CITATION_AUTHORIZATION` 요청의 Bundle·환경·Manifest·Scope exact-match
- 모든 선택 Source/Member가 `PATIENT_CITATION`, `selected_for_operation=true`, `PASS`인지 Receipt 검증
- 누락·malformed·요청 불일치 Receipt의 fail-closed 처리
- 비식별 합성 fixture와 순수 단위·계약 테스트

위 범위는 DB·Graph·HTTP·Provider·시계·네트워크 없이 검증 가능하다. Target 계약의 enum이나 공개 DTO를
변경하지 않는다.

### 2.2 선행조건 완료까지 차단하는 범위

| 차단 범위 | 해소 조건 | 이번 설계의 대응 |
| --- | --- | --- |
| Citation Guard row 생성과 Parent/Source/Member Decision 원자 저장 | #174 후속 Guard persistence·transaction 계약 | pure request/Receipt 계약까지만 고정하고 저장 구현 0건 |
| Guide/Chat `JOB_EXECUTE`와 LangGraph 실행 | #174 접수·Full Context·currentness 연결, #148 공통 Job API 연결 | Graph·Handler registry·Worker assembly 0건 |
| 결과 commit과 `AI_JOB=STALE` 전이 | #174 결과 commit currentness transaction | finalizer가 DB/Job 상태를 반환하지 않음 |
| 공개 Citation DTO와 `PASS` 공개 | v2 DTO·OpenAPI·Migration·Contract/Integration Test 동시 승격 | pure outcome을 public DTO로 직렬화하지 않음 |
| Citation persistence schema 정합 | 후속 Decision/forward migration으로 v2 다형 Citation을 물리화 | 현재 `rag_citation`의 단일 `evidence_id`, `source_type` 부재, `PRESCRIPTION` 표현 불가를 소비하지 않음 |
| Claim당 복수 Citation 저장 | 현재 `uq_rag_citation_target_claim`을 유지할지 v2 tuple Citation에 맞게 변경할지 결정 | pure core는 Claim당 1개 이상을 허용하고 현재 DB unique에 맞춰 축소하지 않음 |
| Graph/Validator version의 Runtime Bundle pinning | `graph_ref`·`validator_ref` 추가 또는 계약 개정 Decision | artifact ref는 pure trace에만 보존하고 Bundle 고정 완료로 주장하지 않음 |
| Production 활성화 | RAG-17과 외부 의료·약학·Source·Privacy·Safety 승인 | `PUBLIC_TRACK_F=false` 유지 |

`#174`만이 유일한 차단은 아니다. `rag_runtime_execution_manifest`에는 현재 `graph_ref`와
`validator_ref`가 없으므로, 이번 pure validator의 존재를 Runtime Bundle 재현성 완료로 해석하지 않는다.
또한 현재 `rag_citation`은 source type별 Evidence FK가 아니라 단일 non-null `evidence_id`를 사용하고,
`(target_type, target_id, claim_key)` unique로 Claim당 Citation 하나만 저장하며, authorization은
`PENDING | REJECTED`, release는 `NOT_PUBLIC`만 허용한다. 이 구조를 v2 Target의 다형·복수 Citation과
억지로 맞추는 adapter는 만들지 않는다.

## 3. 기존 구현과의 관계

### 3.1 재사용하는 정본

- `ai_worker.tasks.rag.evidence_retrieval.ImmutableArtifactRef`
  - `artifact_code`, `version`, `content_sha256` 결속을 재사용한다.
- `ai_worker.tasks.rag.guideline_card.GuidelineCardOutcome`
  - 승인된 RAG-15 Card는 후속 얇은 변환의 첫 실제 소비자다. Card에 이미 포함된 Evidence assessment,
    Guideline Evidence Binding과 verifier artifact를 Claim support assertion으로 투영한다.
- `ai_worker.tasks.rag.evidence_gate.EvidenceGateOutcome`
  - RAG-14의 Evidence sufficiency/freshness 판정을 다시 계산하지 않는다.
- `docs/contracts/targets/post-mvp-1/safety-result-v2.md`
  - Citation Source 유형, support 상태, 공개 차단 규칙의 유일한 Target 정본이다.

### 3.2 직접 Runtime에 재사용하지 않는 구현

`ai_worker.tasks.rag.source_governance`는 module docstring과 타입명이 명시하듯 **synthetic-only evaluation**이다.
그 안의 `CITATION_AUTHORIZATION`, `PATIENT_CITATION`, origin/scope/bundle exact-match 불변조건과 합성 fixture는
회귀 근거로 재사용하지만, `Synthetic*` 타입이나 `evaluate_synthetic_source_governance` 결과를 Runtime 승인으로
취급하지 않는다.

새 authorization layer는 Source 적합성을 다시 판정하지 않는다. 실제 권위 Source에서 승인을 발급·저장하는 책임은
#174가 확정할 persistence 계약을 따르는 후속 Worker Application Service와 `ai_worker/adapters` 구현에 있다.
Worker는 `backend.app`을 import하지 않으며, transaction commit/rollback은 기존 Consumer의 ResultStore 경계가 소유한다.
pure layer는 외부 I/O를 호출하지 않고 관측 Receipt가 자신이 만든 요청과 정확히 결속됐는지만 검증한다.

### 3.3 이름 충돌 방지

`ai_worker.tasks.evaluation.release_gate`는 후보 Runtime의 **평가 배포 Gate**다. 이번 `citation_finalizer`는 환자에게
공개 가능한 Citation 후보를 Release Gate 입력으로 만드는 **실행 내부 경계**이며 서로 다른 책임이다.
이번 slice에서 `release_gate.py`, `release_policy.py`, `release_decision`을 추가하거나 수정하지 않는다.

## 4. 전체 데이터 흐름

```text
RAG-14 EvidenceGateOutcome / RAG-15 GuidelineCardOutcome / 후속 Generator Draft
        │
        │ caller-owned 얇은 변환: 미확정 DB DTO를 사용하지 않음
        ▼
ClaimCitationCandidateSet
        │
        ▼
validate_claim_citations(..., support_receipts)
        ├─ shape/binding/support 실패
        │      → ValidationRejected + DISCARD_GENERATED_CONTENT
        │      → Authorization request 생성 0건
        │
        └─ ValidatedCitationSelection
                 │ canonical selection projection/hash
                 ▼
          build_citation_authorization_request()
                 │  후속 async Worker Service가 외부 승인·저장을 수행
                 ▼
          observed CitationAuthorizationReceipt
                 │
                 ▼
          finalize_citations(validated selection, request, receipt)
                 ├─ missing / malformed / mismatch / FAIL
                 │      → AuthorizationRejected + DISCARD_GENERATED_CONTENT
                 └─ exact-bound PASS Receipt
                        → AuthorizedCitationSelection
                        → 후속 Runtime Release Gate 입력
```

순수 finalizer는 승인된 환자 응답, `release_decision`, `ai_job.status`, fallback 문구를 만들지 않는다. 성공 결과도
최종 공개 허가가 아니라 **Release Gate가 추가로 검증할 수 있는 승인된 Citation Selection**이다.

## 5. 모듈과 타입

### 5.1 `claim_citation_validator.py`

#### 입력

```python
ClaimCitationCandidateSet(
    target=ClaimTargetRef(target_kind, target_ref),
    claims=(ClaimCandidate(...),),
    citations=(CitationCandidate(...),),
    generation_provenance=GenerationProvenance(...),
    validator_policy_ref=ImmutableArtifactRef(...),
)
```

입력에는 사용자 질문, Provider 원문, Source 원문 전체, 처방 표시값과 환자 식별자를 넣지 않는다.
`target_ref`는 pure 실행 내부의 opaque reference이며 공개 DTO나 DB ID 계약이 아니다.

#### Claim

- `claim_key`: candidate set 안에서 유일한 NFC 문자열
- `claim_kind`: 기존 저장 어휘 `MEDICAL | AUXILIARY | SAFETY_FALLBACK`
- `support_assertion`: `support_status`, Claim/Citation projection hash, versioned assessment/binding ref를 함께 가진다.
  `support_status` 어휘는 `SUPPORTED | PARTIALLY_SUPPORTED | CONTRADICTED | NOT_SUPPORTED`만 사용한다.
- `text_digest`: 원문 대신 소문자 SHA-256
- `display_order`: 1부터 시작하는 연속된 양수

#### Citation과 유형별 Evidence Ref

`source_type`은 v2의 다섯 값만 사용한다.

- `PRESCRIPTION` → `PrescriptionEvidenceRef`
- `KNOWLEDGE_CHUNK` → `KnowledgeChunkEvidenceRef`
- `INTERACTION_RULE` → `InteractionRuleEvidenceRef`
- `LIFESTYLE_GUIDELINE` → `LifestyleGuidelineEvidenceRef`
- `SAFETY_POLICY` → `SafetyPolicyEvidenceRef`

각 `CitationCandidate`는 위 tagged Evidence Ref 중 정확히 하나를 값으로 가진다. nullable FK 다섯 개를 한
dataclass에 병렬로 두지 않아 잘못된 조합을 구성하기 어렵게 한다. 각 변형은 필요한 Evidence/Source Snapshot,
artifact/member, source version, locator, content digest를 명시한다. Source 실행 provenance는 Source와
`ENDPOINT_OPERATION | ARTIFACT_MEMBER` tagged member identity, 원 REQUEST의 Source/Member Decision ref를 분리해
보존한다. 새 Citation Authorization Decision ref는 아직 존재하지 않으므로 후보나 요청에 넣지 않고 Receipt에서만
관측한다. `PRESCRIPTION`만 Source 실행 provenance를 nullable로 허용한다.

#### Claim support 권위

Generator가 만든 `support_status` 문자열은 승인 근거가 아니다. Validator는 caller가 외부 권위 경계에서 관측한
`ClaimSupportVerificationReceipt`를 입력받고, Candidate에서 다시 계산한 projection과 exact-match한다.

성공 Receipt는 assertion artifact ref, verifier artifact ref, Claim text digest, Citation Evidence Ref 전량,
Claim/Citation projection hash와 support status를 exact-bind해야 한다. 검증 성공 시 support Receipt 전량을
`ValidatedCitationSelection.selection_sha256`의 canonical projection에 포함하므로 verifier artifact의 code·version·hash가
하나라도 바뀌면 Authorization request identity도 바뀌고, 이전 승인 Receipt를 재사용할 수 없다. Receipt가 단순히 `SUPPORTED`만 반환하거나
요청의 일부 Citation만 확인하면 malformed Receipt로 거부한다.

이 Receipt 계약은 의미 기반 NLI를 새로 도입하지 않는다. 현재 첫 소비자인 RAG-15 Guideline Card는 이미 검증한
Evidence assessment와 Guideline Evidence Binding을 투영한다. 후속 Interaction Rule은 결정적 Rule Binding을,
일반 생성 답변은 승인된 Claim support assessment가 마련된 경우에만 같은 Receipt를 제공한다. pure validator에
Claim별 Receipt가 없으면 `DEPENDENCY_ERROR/REJECTED + SUPPORT_RECEIPT_REQUIRED`, Receipt 구조나 projection 결속이
맞지 않으면 `DEPENDENCY_ERROR/REJECTED + SUPPORT_RECEIPT_MISMATCH`로 fail-closed하며, Generator의 자기 선언으로
우회하지 않는다. 후속 adapter가 이를 Runtime fallback code로 변환하는 규칙은 #174 이후 통합 범위이며, pure 계층에
존재하지 않는 별도 reason code를 가정하지 않는다.

pure layer는 Receipt 발급자의 권위나 DB 존재를 증명하지 않는다. 후속 Worker Service가 #174의 승인 저장소에서
Receipt를 관측한 뒤 이 순수 검증 함수에 전달해야 하며, 이 단계가 연결되기 전 pure 성공은 Runtime 공개 권한이 아니다.

#### 출력

```python
ClaimCitationValidationOutcome(
    execution_status,       # EVALUATED | VALIDATION_ERROR | DEPENDENCY_ERROR
    decision,               # VALIDATED | REJECTED
    reasons,                # 내부 안정 reason tuple
    validated_selection,    # 성공 때만 존재
)
```

`ValidatedCitationSelection`은 검증에 사용한 support Receipt 전량을 canonical Claim 순서로 보존하고 selection hash에도
Receipt 전량을 포함한다. 후속 Authorization 진입은 후보와 Receipt를 다시 순수 검증해 Receipt가 누락된 forged selection을
거부하며, 유효한 다른 verifier Receipt로 교체된 경우에는 새 selection/request identity와 새 승인을 요구한다.

이 enum은 pure module 내부 결과이며 API·DB·공유 메시지 계약이 아니다. v2의 `release_decision` 값을 재사용하거나
새 값을 추가하지 않는다.

### 5.2 `citation_authorization.py`

#### 요청 결속

```python
CitationAuthorizationRequest(
    origin_request_guard=OriginRequestGuardBinding(...),
    runtime_binding=RuntimeAuthorizationBinding(
        environment,
        bundle_id,
        bundle_manifest_hash,
        request_scope_codes,
        scope_manifest_hash,
    ),
    validated_selection_sha256,
    selection_manifest=(CitationSelectionEntry(...),),
    selection_manifest_sha256,
    request_sha256,
)
```

Scope code와 Selection entry는 canonical UTF-8 byte order로 정렬하고 중복을 거부한다. Hash는 NFC 문자열과
projection version을 포함한 canonical JSON의 SHA-256이다. 잘못된 입력을 조용히 정규화하지 않는다.

#### I/O 경계

이번 pure slice는 `build_citation_authorization_request()`와 `verify_citation_authorization_receipt()`만 제공한다.
동기 `Protocol`을 미리 고정하지 않는다. 후속 Worker Application Service는 현재 Worker 실행 모델에 맞는 async
persistence port를 정의하고, 요청을 deep-copy한 값으로 승인·저장한 뒤 관측 Receipt를 pure verifier에 전달한다.
이렇게 해야 DB I/O를 연결할 때 pure 함수의 sync 호출을 async로 바꾸는 재작업과 `backend.app` 역방향 import를 피한다.

`Pass` Receipt는 최소한 다음을 반환해야 한다.

- immutable receipt artifact ref
- origin REQUEST Guard ref와 `PASS` decision
- `operation_type=CITATION_AUTHORIZATION`
- environment·Bundle ID·Bundle Manifest Hash
- 정렬 Scope와 Scope Manifest Hash
- Selection Manifest Hash
- 모든 Selection entry의 `selected_for_operation=true`
- 목적 `PATIENT_CITATION`
- 새 Citation Authorization Source Decision ref와 Member Decision ref
- Source Decision과 Member Decision 전부 `PASS`

pure layer는 Receipt가 `PASS`라고 말한 사실만 신뢰하지 않고 위 값을 요청과 exact-match한다.
요청 Selection은 Source/Member identity만 담고 새 Decision ref를 미리 만들지 않는다. `PRESCRIPTION` 후보의 구조
검증은 지금 지원하지만, Source/Member Selection이 0건인 Citation Authorization 요청은 #174가 그 Guard 표현을
확정하기 전까지 fail-closed한다. 빈 Selection을 성공으로 해석해 계약을 선점하지 않는다.

### 5.3 `citation_finalizer.py`

`finalize_citations(validated_selection, authorization_request, authorization_receipt)` 한 개의 구체 함수를 둔다.
Factory, Registry, class hierarchy는 만들지 않는다.

반환값은 다음 둘 중 하나다.

- `AuthorizedCitationSelection`: 검증된 Claim/Citation, authorization Receipt, validator/policy provenance
- `DiscardGeneratedContent`: 실패 단계와 내부 안정 reason만 포함하며 생성 본문·Source 원문·예외 메시지를 포함하지 않음

고정 fallback에 의료 Claim·Source Citation이 없어서 빈 Citation Guard를 만들지 않는 분기는 이 함수의 입력이
아니다. fallback 생성·승인은 각 Safety/Guideline kernel이 소유하며, 후속 Runtime Release Gate가 no-claim
fallback임을 별도로 확인한다. 이를 자동 우회 경로로 추가하면 생성 답변이 빈 Claim 집합으로 위장할 수 있으므로
이번 slice에서는 허용하지 않는다.

## 6. 검증 순서와 불변조건

### 6.1 Claim–Citation Validator

다음 순서를 고정한다.

1. **Request shape**: 정확한 dataclass/enum/tuple 타입, NFC, hash 형식, 연속 display order를 확인한다.
2. **Claim identity**: 빈 값·중복 `claim_key`·중복 display order를 차단한다.
3. **Citation identity**: 빈 값·중복 citation key·없는 Claim 참조를 차단한다.
4. **Typed Evidence Ref**: `source_type`과 tagged ref 변형이 exact-match해야 한다.
5. **Provenance**: source version, locator, digest, Snapshot/artifact/member ref의 필수값과 결속을 확인한다.
6. **Support assertion**: 검증된 값으로 만든 detached Claim/Citation projection과 caller가 제공한 Receipt의
   artifact·verifier·projection hash·Claim/Citation 전량 결속을 확인한다.
7. **Support decision**:
   - `MEDICAL`은 `SUPPORTED`만 허용하고 Citation이 하나 이상이어야 한다.
   - `AUXILIARY/PARTIALLY_SUPPORTED`는 Target 계약이 허용하는 비의료 보조 Claim에만 가능하다.
   - `CONTRADICTED | NOT_SUPPORTED` Claim은 Selection에 남기지 않고 전체 생성 후보를 거부한다.
8. **Selection projection**: 검증된 값만 canonical 정렬하고 hash를 만든다.

부분적으로 유효한 Claim만 골라 공개하지 않는다. 하나라도 의료 Claim 검증이 실패하면 전체 생성 후보를 폐기한다.

### 6.2 Citation Authorization

1. origin Guard가 `REQUEST/PASS`인지 확인한다.
2. origin과 요청의 환경·Bundle·Manifest·정렬 Scope·Scope Hash가 exact-match하는지 확인한다.
3. 검증된 Citation만 Selection Manifest로 투영한다.
4. 후속 I/O Service에 넘길 immutable request를 반환한다.
5. 별도로 관측된 Receipt의 구조·self reference·request binding·Selection 전량을 확인한다.
6. Source/Member 중 하나라도 미선택·목적 불일치·FAIL이면 전체 Authorization을 거부한다.

외부 I/O exception은 후속 Service가 안정된 dependency failure로 변환해야 하며 pure outcome에 exception message를
전달하지 않는다.

## 7. 실패·성공 결과 정렬

### 7.1 Claim–Citation Validator

| 입력 조건 | `execution_status / decision` | 실제 reason | 결과 payload |
| --- | --- | --- | --- |
| Candidate/dataclass/enum/hash 형식 오류 | `VALIDATION_ERROR / REJECTED` | `REQUEST_INVALID` 등 shape·identity·provenance reason | `validated_selection=None` |
| Claim별 Support Receipt 누락·Claim key 집합 불완전 | `DEPENDENCY_ERROR / REJECTED` | `SUPPORT_RECEIPT_REQUIRED` | `validated_selection=None` |
| Receipt 구조 오류 또는 status·digest·assessment·projection 불일치 | `DEPENDENCY_ERROR / REJECTED` | `SUPPORT_RECEIPT_MISMATCH` | `validated_selection=None` |
| 의료 Claim Citation 누락 | `EVALUATED / REJECTED` | `MEDICAL_CLAIM_CITATION_REQUIRED` | `validated_selection=None` |
| 허용되지 않은 Claim kind/support status 조합 | `EVALUATED / REJECTED` | 의료 Claim은 `MEDICAL_CLAIM_NOT_SUPPORTED`, 그 밖은 `CLAIM_NOT_SUPPORTED` | `validated_selection=None` |
| Candidate와 Support Receipt 전량 통과 | `EVALUATED / VALIDATED` | 빈 tuple | `ValidatedCitationSelection` |

동일 요청에서 semantic reason과 Receipt dependency reason이 함께 생기면 구현은 dependency reason 존재 여부를 우선해
`DEPENDENCY_ERROR`를 반환하고 reason tuple에는 중복을 제거한 실제 원인들을 함께 보존한다.

### 7.2 Citation Authorization

Authorization 결과에는 `execution_status`가 없다. Build와 Receipt verification은 각각 별도 decision/result shape을
반환하며, 후속 adapter가 이를 Runtime 상태로 임의 추정해서는 안 된다.

| 경계·입력 조건 | 실제 decision | 실제 reason | 결과 payload |
| --- | --- | --- | --- |
| Build: forged validated selection | `REJECTED` | `VALIDATED_SELECTION_INVALID` | `request=None` |
| Build: Runtime 또는 origin REQUEST 결속 오류 | `REJECTED` | `RUNTIME_BINDING_INVALID` 또는 `ORIGIN_REQUEST_MISMATCH` | `request=None` |
| Build: 빈 Source selection 또는 잘못된 selection | `REJECTED` | `AUTHORIZATION_SELECTION_REQUIRED` 또는 `AUTHORIZATION_SELECTION_INVALID` | `request=None` |
| Build: 전 조건 통과 | `BUILT` | 빈 tuple | `CitationAuthorizationRequest` |
| Verify: request self-binding 오류 | `REJECTED` | `AUTHORIZATION_REQUEST_INVALID` | `receipt=None` |
| Verify: Receipt 누락·구조 오류 | `REJECTED` | `RECEIPT_INVALID` | `receipt=None` |
| Verify: request/Receipt 또는 selection 전량 불일치 | `REJECTED` | `RECEIPT_BINDING_MISMATCH` 또는 `SELECTION_RECEIPT_MISMATCH` | `receipt=None` |
| Verify: 목적·선택 여부·Source/Member PASS 불충족 | `REJECTED` | `SELECTION_NOT_AUTHORIZED` | `receipt=None` |
| Verify: exact-bound PASS | `AUTHORIZED` | 빈 tuple | 검증된 `CitationAuthorizationReceipt` |

### 7.3 Finalizer와 후속 Runtime

Finalizer는 위 결과를 다시 검증해 성공 시 `AuthorizedCitationSelection`, 실패 시
`DiscardGeneratedContent(failed_stage, reasons)`를 반환한다. Finalizer에 Receipt 자체가 전달되지 않으면
`CITATION_AUTHORIZATION / AUTHORIZATION_RECEIPT_REQUIRED`, rebuild한 요청과 전달 요청이 다르면
`CITATION_AUTHORIZATION / AUTHORIZATION_REQUEST_MISMATCH`로 폐기한다. 이 두 문자열은 finalizer의 안정 reason이며
Authorization enum에 새 값을 추가한 것이 아니다.

pure layer는 이 결과를 직접 저장하거나 공개 enum으로 변환하지 않는다. 정확한
`execution_status/evidence_status/release_decision/fallback_code/ai_job.status` 조합과 `DEPENDENCY_UNAVAILABLE` 같은
Runtime fallback mapping은 RAG-16 Runtime integration PR에서 v2 Target과 transaction 계약을 함께 연결한다.

## 8. 개인정보·의료 안전

- 비식별 합성 fixture만 사용한다.
- 질문·답변·Source 원문 전체를 dataclass, repr, reason, exception, test snapshot에 저장하지 않는다.
- Claim은 `text_digest`, Citation은 승인된 짧은 표시 excerpt의 digest만 pure 검증에 사용한다. 실제 excerpt 공개는
  후속 DTO/승인 범위다.
- `SensitiveText`가 필요한 변환 adapter는 기존 redacted wrapper를 사용하고 finalizer 경계 전에 digest로 축소한다.
- raw Provider body, 내부 score, Guard 상세 reason은 public output으로 직렬화하지 않는다.
- Citation 검증 실패를 부분 성공으로 낮추지 않는다.
- `PUBLIC_TRACK_F`와 실제 사용자 history 외부 전송 flag를 변경하지 않는다.

## 9. 테스트 설계

### 9.1 파일

| 파일 | 검증 |
| --- | --- |
| `ai_worker/tests/rag/test_claim_citation_validator.py` | shape, support Receipt, typed ref, provenance, 전체 폐기, 결정적 hash |
| `ai_worker/tests/rag/test_citation_authorization.py` | origin/scope/bundle/selection exact-match, 결정적 request, malformed Receipt |
| `ai_worker/tests/rag/test_citation_finalizer.py` | Selection/request/Receipt 순서 결속, 실패 시 전체 폐기, 승인 결과 handoff |
| `tests/contract/rag/test_claim_citation_contract.py` | v2 다섯 Source type·support 어휘·최소 불변식 drift |
| `tests/fixtures/rag/citation/finalization_cases.json` | 비식별 합성 PASS/누락/변조/상충/승인 실패 matrix |

`ai_worker/tasks/evaluation/`에는 새 구현을 추가하지 않는다. 이번 결정적 안전 matrix는 RAG pure/contract lane에서
검증하고, 실제 응답 품질·Citation precision/coverage·E2E 평가는 기존 RAG Evaluation Issue와 후속 Graph 연결에서
소비한다.

### 9.2 필수 사례

- 다섯 Citation Source type 정상 사례
- `source_type`과 Evidence Ref 변형 불일치 전량
- 의료 Claim의 0 Citation, `PARTIALLY_SUPPORTED`, `CONTRADICTED`, `NOT_SUPPORTED`
- Generator가 `SUPPORTED`를 표기했지만 support assertion/Receipt가 없거나 Claim/Citation 일부만 결속한 사례
- Support Receipt 누락, malformed success, projection 변조와 verifier artifact mismatch
- 존재하지 않는 Claim 참조, 중복 key/order, order gap
- source version/locator/digest/Snapshot/member ref 누락·변조
- 입력 순서 변경에도 같은 Selection hash
- Unicode 비-NFC, 대문자/짧은 hash, bool-as-int 등 Python shape 공격
- origin REQUEST가 FAIL 또는 다른 Bundle/환경/Manifest/Scope를 참조
- `PATIENT_CITATION` 대신 `RETRIEVAL` 승인만 반환
- Selection 일부만 PASS, `selected_for_operation=false`, 추가·누락 Selection
- Authorization request와 다른 malformed PASS Receipt
- Validator 실패 시 Authorization request 생성 0건
- 어떤 실패 outcome/repr에도 합성 sentinel·Source 원문·exception message가 없음

### 9.3 검증 명령

첫 구현 PR은 작은 검사부터 실행한다.

```bash
uv run pytest ai_worker/tests/rag/test_claim_citation_validator.py \
  ai_worker/tests/rag/test_citation_authorization.py \
  ai_worker/tests/rag/test_citation_finalizer.py \
  tests/contract/rag/test_claim_citation_contract.py -q
uv run pytest ai_worker/tests/rag -q
uv run pytest ai_worker/tests/evaluation -q
uv run ruff check ai_worker/tasks/rag ai_worker/tests/rag tests/contract/rag
uv run ruff format ai_worker/tasks/rag ai_worker/tests/rag tests/contract/rag --check
uv run mypy ai_worker/tasks/rag
git diff --check
bash scripts/ci/run_test.sh
```

DB·Redis·Provider·실제 환자 데이터 검증은 이 pure slice의 완료 조건이 아니다. 실행하지 않은 통합/E2E는
`PASS`로 보고하지 않고 차단 사유와 후속 Issue를 기록한다.

## 10. 후속 확장 계약

선행조건이 완료돼도 pure core를 수정하는 방식으로 확장하지 않는다.

| 후속 단계 | 추가되는 구현 | pure core와의 접점 |
| --- | --- | --- |
| RAG-13~15 통합 | Rule/Knowledge/Guideline 결과 변환 adapter와 support verifier | 각 결과와 승인 Binding을 `ClaimCitationCandidateSet`/support Receipt로 변환 |
| #174 Guard persistence | Worker Application Service + `ai_worker/adapters` persistence port; Consumer ResultStore transaction | pure request를 저장 경계로 전달하고 관측 Receipt를 verifier에 반환 |
| #174 Context/currentness | pinned Context → `RuntimeAuthorizationBinding` 변환 | pure 입력 생성만 담당 |
| LangGraph | `claim_citation_validator` Node와 Finalizer service 호출 | `finalize_citations` 호출, 내부 로직 복제 금지 |
| Runtime Release Gate | Authorization outcome + Safety/Currentness 종합 | `AuthorizedCitationSelection` 소비 |
| 결과 persist | claims/citations/provenance/usage 원자 저장 | 검증된 output만 저장, 재판정 금지 |
| Runtime Bundle | 승인된 Decision에 따라 graph/validator ref 고정 | pure trace의 artifact ref를 Manifest adapter가 사용 |

adapter가 미확정 DB DTO를 pure 타입에 누출하거나 pure core가 SQLAlchemy/FastAPI/LangGraph를 import하면 경계
위반이다. 후속 연결에서 타입 변환 비용은 의도된 anti-corruption boundary이며 재작업이 아니다.
후속 persistence는 `AGENTS.md`와 `CONTRIBUTING.md`에 따라 Python Service/Repository validation과 transaction,
ordinary FK/unique/CHECK 및 최소 권한으로 구현하고 DB trigger·RLS·업무 규칙용 stored function을 추가하지 않는다.

## 11. 변경 파일과 비목표

### 첫 구현 PR 예상 변경

- `ai_worker/tasks/rag/claim_citation_validator.py`
- `ai_worker/tasks/rag/citation_authorization.py`
- `ai_worker/tasks/rag/citation_finalizer.py`
- 위 세 모듈의 `ai_worker/tests/rag/` 단위 테스트
- `tests/contract/rag/test_claim_citation_contract.py`
- `tests/fixtures/rag/citation/finalization_cases.json`
- 본 설계 문서와 필요한 최소 테스트 추적 문서

### 수정하지 않는 영역

- `backend/app/**`, Alembic migration, DB schema
- `ai_worker/core/runtime_assembly.py`, Worker registry
- `ai_worker/tasks/evaluation/**`
- 기존 Chat v1·Guide v3 동기 Runtime
- `rag-runtime-v1.md`, `safety-result-v2.md`의 enum·상태·공개 DTO
- `source_governance.py`의 synthetic-only 권위
- Runtime Bundle `READY`·active pointer·Production flag

구현 중 기존 Target으로 표현할 수 없는 enum, 필수 필드, 오류 의미, transaction 순서가 필요해지면 구현을 멈추고
새 Decision/Contract Freeze 및 영향 owner 리뷰로 전환한다. 임시 enum이나 nullable placeholder로 우회하지 않는다.

## 12. 대안과 기각 이유

### 대안 A — `handler/retriever/generator/validator` 네 계층을 먼저 생성

기각한다. Retriever는 이미 RAG-14에, Guideline generator/finalizer는 RAG-15에 실제 구현이 있다. 새 파일은 기존
타입을 감싸는 빈 Protocol/DTO가 되고 실제 Graph 연결 전에 변경될 가능성이 높다.

### 대안 B — 하나의 `handler.py`에서 검증·승인·release를 모두 수행

기각한다. Claim 구조 오류, Source 승인 실패, Release/STALE 판정을 분리해 테스트할 수 없고 후속 DB transaction과
LangGraph 연결 시 다시 분해해야 한다.

### 선택안 — Validator → Authorization → Finalizer의 동작하는 수직 slice

각 경계에 현재 보안·의료 안전 책임이 있고, 결정적 request/Receipt fixture와 후속 persistence adapter라는 실제 두
소비 방식이 존재한다. I/O 호출 자체를 pure core 밖에 두므로 후속 async adapter가 추가돼도 지금 작성한 판정은
유지할 수 있다.

## 13. 완료 기준

- pure 세 모듈이 SQLAlchemy/FastAPI/LangGraph/네트워크/시계/파일 I/O를 import하지 않는다.
- 의료 Claim별 승인 Citation 누락·변조·근거 불일치가 모두 전체 거부된다.
- Generator의 자기 선언 `SUPPORTED`만으로 Claim이 검증되지 않고 승인된 support Receipt가 exact-bind된다.
- 다섯 Source type과 typed Evidence Ref의 exact-match가 고정된다.
- Authorization Receipt가 origin REQUEST, Bundle, 환경, Manifest, Scope와 Selection 전량에 결속된다.
- `RETRIEVAL` 승인이 `PATIENT_CITATION` 승인을 대신하지 못한다.
- 실패 전에 또는 실패 후에 생성 의료 답변이 반환되지 않는다.
- 성공 결과도 Release Gate 입력이며 공개 허가가 아님을 타입·docstring·테스트가 고정한다.
- 기존 Source Governance synthetic verdict를 Runtime authority로 소비하지 않는다.
- Graph·DB·Worker·Backend DTO·Production 활성화 변경이 0건이다.
- 관련 pure/contract/evaluation 회귀와 전체 CI 결과를 정확히 기록한다.
- #174 및 graph/validator Bundle pinning 차단이 Issue/PR에 남고 #180을 조기 Close하지 않는다.
