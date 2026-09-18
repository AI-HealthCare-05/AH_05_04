# Product Decision Candidate: Citation Authorization Production Authority Boundary (#799)

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-799-20260918` |
| 상태 | Proposed / Review pending · Issue #799 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 단일 책임 리뷰 | 송은영 (`@phina-io`) — Backend·Data·Security / DB schema · migration · Repository 쓰기 경계 |
| 교차 리뷰 (FYI) | 김지혜 (`@Jye-rookie`) — Source·Snapshot·Catalog 경계 / 권가빈 (`@hazelnutflavoured`) — PM·Privacy·Citation Gate |
| 추적 Issue | [#799](https://github.com/AI-HealthCare-05/AH_05_04/issues/799) |
| 조사 기준 | `origin/develop` @ `2012720fca84d081139a5c02065ebc63470104fa` |
| 상위·관련 결정 | [`PD-713-20260917`](./2026-09-17-request-authority-persistence.md), [`PD-672-20260916`](./2026-09-16-sync-guide-evidence-authority.md), [`PD-125-20260831`](./2026-08-31-rag-p0-contract-freeze.md) |
| 관련 Issue | #180, #794 / PR #795, #713, #709, #174, #185 |

---

## 1. Context

#794 / PR #795 병합으로 production Guide pure 계층은 `ValidatedCitationSelection`까지 도달한다.
RAG-16 pure Citation kernel(`citation_authorization.py`, `citation_finalizer.py`)은 이미 완성되어
있으므로 재구현하지 않는다.

비어 있는 것은 그 사이의 production authority 경계다.

```text
ValidatedCitationSelection
        ↓
CitationAuthorizationRequest
        ↓
production authority          ← 여기가 비어 있다
        ↓
CitationAuthorizationReceipt
```

#799 Phase A 조사 결과는 `BLOCKED_BY_MISSING_AUTHORITY_CONTRACT`였다. 그 차단 요인 중
**구현 이전에 공유 의미로 먼저 확정해야 하는 3건**만 본 결정이 소유한다.

```text
D1  Citation Origin Identity
D2  Citation Runtime Environment Identity
D3  Citation Runtime Bundle Identity
```

`request_scope_codes` persistence와 PATIENT_CITATION authority 자체는 본 결정에서 구현하지 않고
규칙만 고정한 뒤 child issue로 넘긴다.

## 2. Existing production authority

재사용 가능한 것과 그 정확한 경계는 다음과 같다.

| 자산 | 위치 | 본 결정에서의 취급 |
| --- | --- | --- |
| Pure Citation kernel | `ai_worker/tasks/rag/citation_authorization.py` | **무변경 소비.** 본 결정은 이 모듈의 어떤 타입·함수·enum·hash 의미도 바꾸지 않는다 |
| Citation Finalizer | `ai_worker/tasks/rag/citation_finalizer.py` | 무변경 소비 |
| `ValidatedCitationSelection` | `guide_claim_citation_validation.run_guide_claim_citation_validation()` | #794 종료점. 입력으로만 사용 |
| selection manifest 원천 | `SourceExecutionProvenance` | `_authorization_entries()`가 기계적으로 추출. 추가 authority 불필요 |
| REQUEST Guard·Source·Member authority | `rag_runtime/request_authority.py`, `rag_request_*` 3표, `SqlAlchemyGuideEvidenceAuthorityReader` | **의미 무변경 보존.** Source/Member의 REQUEST 단계 실제 PASS는 그대로 재사용하되, Citation origin identity로는 사용하지 않는다 |
| Runtime Bundle / Environment | `rag_runtime_release_bundle`, `rag_runtime_environment` | D2·D3의 정본 저장소 |

## 3. Confirmed gaps

`2012720f` 기준으로 코드에서 재확인한 사실이다.

| # | 사실 | 근거 |
| --- | --- | --- |
| G1 | `RequestAuthorityDecisionStage`는 `REQUEST` 단일 멤버이고 `_require_request_stage()`가 그 외 값을 하드 거부한다 | `rag_runtime/request_authority.py` |
| G2 | REQUEST Guard에 actual PASS 관측치가 없다 | `RequestGuardAuthorityRecord`(`user_id`·`request_operation_code`·`decision_stage` 3필드), `rag_request_guard_authority` 모델, `AuthoritativeRequestGuardObservation` 모두 outcome 필드/컬럼 없음 |
| G3 | REQUEST Guard artifact identity는 request instance를 식별하지 않는다 | `request_guard_authority_projection()`은 `decision_stage`·`projection_version`·`request_operation_code`·`user_id` 4키뿐. Repository도 동일 identity를 의도적으로 dedupe한다 |
| G4 | `request_scope_codes`의 production persistence가 없다 | 비테스트 py 파일은 `citation_authorization.py`와 synthetic `source_governance.py` 2개뿐. DB의 scope 컬럼(`approval_scope`·`expected_scope_codes`·`failure_scope`·`metric_scope`·`scope_policy_hash`·`source_scope_manifest_hash`)은 모두 다른 도메인 |
| G5 | `source_scope_manifest_hash`가 `canonical_scope_manifest_hash()`와 같은 canonical domain이라는 근거가 없다 | 그 값을 **계산하는 producer가 없고** canonical 정의가 코드·문서 어디에도 없다. Citation 쪽은 `sha256(JCS(list(codes)))`로 codes에 종속 |
| G6 | PATIENT_CITATION production writer/reader/issuer가 없다 | `backend/` 전체에서 `PATIENT_CITATION` 0건. `catalog_source_approval`은 backend writer repository가 없고 유일한 reader는 `purpose` 컬럼을 select조차 하지 않는다 |
| G7 | `source_governance.py`는 synthetic-only다 | 파일 1행 docstring: `Synthetic-only RAG source-governance evaluation with no runtime authority.` |
| G8 | CITATION_AUTHORIZATION 단계 Decision/Receipt persistence가 없다 | `rag_request_*` 3표 모두 `CheckConstraint("decision_stage = 'REQUEST'")` |

## 4. D1 — Citation Origin Identity

### 결정

**D1-B2 (refined)를 채택한다.** NEW A는 **Per-request REQUEST Guard Decision Authority**를 정의한다.
이 artifact는 REQUEST Guard를 설명하는 별도 계층이 아니라 **그 자체가 REQUEST Guard Decision**이다.

canonical projection은 최소 다음을 결속한다.

```text
request_guard_decision_id      ← per-request instance identity
actual_decision_outcome        ← PASS | FAIL
user_id
request_operation_code
decision_stage = REQUEST
environment                    ← D2
bundle_id                      ← D3
bundle_manifest_hash           ← D3
request_scope_codes            ← §8
scope_manifest_hash            ← §8
legacy_request_authority_ref   ← 기존 #713 guard ref를 보존 결속
```

그리고 다음을 고정한다.

```text
OriginRequestGuardBinding.guard_ref
=
Per-request REQUEST Guard Decision Authority artifact ref
```

기존 #713 guard ref 자체는 변경하지 않는다. `user × operation × REQUEST`의 semantic authority
identity라는 의미를 그대로 보존하고, 위 projection의 `legacy_request_authority_ref`로만 참조한다.

```text
#713
semantic REQUEST authority (user × operation × REQUEST)
        ↓ preserved reference only

NEW A
per-request REQUEST Guard Decision authority
        ↓
OriginRequestGuardBinding.guard_ref
```

### 근거

1. 정본 계약이 이미 per-request Guard Decision을 요구한다. `rag-runtime-v1.md`는
   CITATION_AUTHORIZATION Guard가 "원 `REQUEST` Guard ID를 필수로 참조"한다고 하고, 각 Intake·Full
   Context가 "해당 단계의 `runtime_guard_decision_id`를 필수로 저장한다"고 한다. 즉 정본에서 REQUEST
   Guard는 요청마다 하나씩 발행되는 Decision이다.
2. 같은 문서가 "Guard 물리 테이블 도입 전까지"라는 전제를 명시한다. #713은 #709에 필요한 최소
   부분집합만 구현했고, 정본이 말하는 Guard Decision 물리 테이블은 아직 없다. NEW A가 그것을 도입한다.
3. 반면 #713 guard ref는 content-addressed **class identity**다(G3). 같은 사용자의 같은 operation은
   몇 번을 요청해도 동일한 ref를 만들고 repository가 이를 dedupe한다. #709의 owner·operation·stage
   대조에는 충분하지만, "이 Citation이 어느 요청에서 나왔는가"를 증명하지 못한다.
4. pure kernel은 `guard_ref`의 구조적 유효성(`is_valid_immutable_artifact_ref`)만 검사하므로 어떤
   artifact든 통과할 수 있다. 그러나 RAG-16 design과 `rag-runtime-v1.md`는 이 field를 의미상
   **origin REQUEST Guard ref / 원 REQUEST Guard ID**로 사용한다. 따라서 그 자리에 들어가는 것은
   REQUEST Guard를 가리키는 별도 계층이 아니라 **실제 per-request REQUEST Guard Decision Authority
   artifact**여야 한다. 본 결정은 이 의미를 명시적으로 고정한다.

### 기각안

- **D1-A (기존 #713 guard ref를 Citation origin으로 사용).** migration이 없다는 장점이 있으나, 같은
  user + operation의 반복 요청을 구별하지 못한다. 따라서 cross-request replay를 차단할 수 없고,
  과거 Citation을 재현할 때 어느 요청의 Bundle·Scope였는지 복원할 수 없으며, "원 REQUEST Guard"
  의미와 일치하지 않는다.
- **D1-B1 (#713 guard projection에 request identity 추가).** 이미 저장된 모든 guard ref가 무효가 되고
  #709의 recomputation contract가 깨진다. `PD-713-20260917`이 확정한 의미를 되돌리는 rollback 비용이
  가장 크다.

### 영향

- #713 / #709: **무변경.** `rag_runtime/request_authority.py`, `rag_request_*` 3표, 기존 reader 전부 그대로.
- Chat/Guide/Safety 공용성: Per-request REQUEST Guard Decision Authority를 domain-neutral로 정의하므로
  Chat Safety Citation도 같은 경로를 소비할 수 있다.
- 현재 동기 Guide route(`GuideService.create_guide`)는 legacy 직접 생성 경로라 REQUEST Guard·Bundle·RAG가
  없다. 이 Decision을 실제로 발행할 주체는 아직 없는 Track F Guide runtime entrypoint이며, NEW A는 그
  entrypoint가 소비할 authority를 만들 뿐 entrypoint 자체를 만들지 않는다.

## 5. Origin REQUEST PASS

### 결정

**Option B를 유지한다.** 기존 `rag_request_guard_authority`에 `actual_decision_outcome`을 추가하지
않는다. NEW A의 **Per-request REQUEST Guard Decision Authority**가 `actual_decision_outcome`을 직접
소유한다.

### 근거

Option A(#713 Guard에 outcome 추가)를 기각하는 이유는 편의가 아니라 의미다.

1. #713 guard projection은 request instance를 식별하지 않는다(G3). 여기에 outcome을 넣으면 저장되는
   사실은 "이 사용자가 이 operation에서 **언젠가** PASS를 받은 적이 있다"가 된다. authoritative해
   보이지만 요청 단위 PASS가 아니므로 없는 것보다 위험하다.
2. Source/Member Decision은 projection에 `source_snapshot_id`·`source_snapshot_member_id`·outcome이
   들어가 PASS/FAIL이 서로 다른 artifact identity를 만든다. Guard에는 그 구별 축이 없어
   "Source/Member처럼 Guard에도 넣으면 된다"는 대칭이 성립하지 않는다.
3. 기존 artifact identity를 파괴적으로 바꾸게 되어 D1-B1과 동일한 rollback 비용을 진다.

### 고정 규칙

```text
Guard row 존재 → PASS 추론 금지
caller → PASS 입력 금지
missing / NULL / unknown → PASS 추론 금지
FAIL도 historical record로 append-only 저장
Citation path는 actual PASS일 때만 진행
```

## 6. D2 — Runtime Environment Identity

### 결정

**D2-A를 채택한다.** `rag_runtime_environment.environment_code`와
`rag_runtime_release_bundle.environment_code`를 pure Citation 계약의 canonical vocabulary로 제한한다.

```text
LOCAL | TEST | CLOSED_DEMO | PRODUCTION
```

### mapping rule

```text
RuntimeEnvironment(environment_code)  — exact, case-sensitive
대소문자 변환·trim·alias·normalization·default 없음
어휘 외 값은 fail closed
caller가 environment 문자열을 고르는 경로 없음 (저장된 row에서만 읽음)
```

### 근거

1. `environment_code`는 이미 `bundle_manifest_hash`의 canonical 입력이다
   (`runtime_bundle_builder.py`의 manifest projection). 모델 주석도 "environment_code is therefore the
   only place the environment that this content was built for can be pinned, and it enters
   bundle_manifest_hash"로 명시한다. 즉 environment는 **이미 bundle manifest에 exact-bound** 되어
   있고, 필요한 것은 그 값의 어휘를 고정하는 것뿐이다.
2. `rag_runtime_environment`에는 `UNIQUE(environment_code)`가 이미 있으므로 어휘를 제한하면
   environment row와 enum이 1:1이 된다. 별도 mapping 계약이 필요 없다.

### 기각안

- **D2-B (별도 mapping layer).** 기존 free-text 어휘를 보존하지만 "mapping authority"라는 새 공유
  계약을 하나 더 만들고, 그 mapping을 누가 소유하는가라는 문제를 남긴다. caller 임의 mapping을 막으려면
  결국 어휘를 고정해야 하므로 D2-A의 우회로일 뿐이다.
- **D2-C (UUID authority + pure kernel enum 변경).** frozen pure kernel의 shared contract 변경이며,
  `CLOSED_DEMO`를 포함한 4값 어휘는 `PD-125-20260831` 계열 계약에서 이미 합의되어 있다. 최후 대안으로만
  검토했고 채택하지 않는다.

### migration impact

본 결정은 migration을 수행하지 않는다. 구현 전 **preflight를 선행 조건으로 고정한다.**

저장소 코드 기준으로는 기존 production population path가 확인되지 않았다.

```text
rag_runtime_environment seed migration 없음
non-test create_environment caller 없음
non-test execute_runtime_bundle_build caller 없음
```

그러나 **실제 배포 DB의 data presence는 본 결정에서 검증하지 않았다.** 따라서 D2 migration 전에
다음 preflight를 반드시 수행한다.

```sql
SELECT count(*) FROM rag_runtime_environment;
SELECT count(*) FROM rag_runtime_release_bundle;
SELECT DISTINCT environment_code FROM rag_runtime_environment;
SELECT DISTINCT environment_code FROM rag_runtime_release_bundle;
```

또는 동등한 repository-based preflight를 실행하고, 결과에 따라 분기한다.

- **기존 row가 없으면:** CHECK 제약 추가 + fixture/기대 hash 갱신으로 충분하다.
- **기존 row가 있으면:** silent uppercase normalization을 **금지**한다. 기존 row의 semantic meaning을
  먼저 검증하고, explicit migration과 `bundle_manifest_hash` recomputation 여부를 별도 migration
  plan으로 판단해 별도 승인을 받는다.

테스트 fixture 영향도 함께 확인한다. 현재 fixture는 소문자 `"local"` / `"test"` / `"production"`을
쓰며, `environment_code`가 hash 입력이므로 어휘 전환 시 fixture의 `bundle_manifest_hash`가 다시
계산된다. 하드코딩된 기대 hash가 있으면 함께 갱신해야 한다.

**시점 제약:** 이 변경은 실제 환경에서 Bundle이 build·활성화되기 전에 들어갈수록 비용이 낮다.
미룰수록 데이터 migration과 manifest 재계산 범위가 커진다.

## 7. D3 — Bundle Identity

### 결정

**D3-A를 채택한다.**

```text
bundle_id = rag_runtime_release_bundle.id 의 canonical lowercase UUID string
```

그리고 `bundle_id`는 언제나 `bundle_manifest_hash`와 **쌍으로** 검증한다. 두 값은 실제
`rag_runtime_release_bundle` 행 하나의 `(id, bundle_manifest_hash)`와 exact-match해야 한다.

### 의미 구분

```text
bundle_id
= persisted Runtime Release Bundle row identity
= content identity가 아니다

bundle_manifest_hash
= bundle content/configuration identity

bundle_key / bundle_version
= human-readable naming / version metadata
= Citation authority identity로 사용하지 않는다
```

Citation Runtime Binding은 `(bundle_id, bundle_manifest_hash)` exact pair를 사용한다.

### exact semantic value

RFC 4122 소문자 canonical 표기(`rag_runtime/request_authority.py`의 UUID 규칙과 동일). 대문자·중괄호·
urn 접두사·하이픈 없는 표기를 허용하지 않는다. 형식상 `str(uuid)`이지만 **본 결정이 그 의미를 명시적으로
승인했기 때문에** 허용되는 projection이며, Decision 없이 편의로 쓰는 것과 구분된다.

### manifest binding

- `UNIQUE(bundle_manifest_hash)` — 내용당 한 행.
- `UNIQUE(id, bundle_manifest_hash)`.
- `rag_runtime_environment.(active_bundle_id, active_bundle_manifest_hash)` 복합 FK가 이 쌍을 이미
  활성 Bundle identity로 사용한다.

따라서 `(bundle_id, bundle_manifest_hash)`는 새로 만드는 identity가 아니라 **DB가 이미 identity로
취급하고 있는 쌍**이다.

### 근거

`rag-runtime-v1.md`가 결정적이다.

> Bundle 이름(`bundle_key`·`bundle_version`)·`created_by`·`governance_revision_ref`는 **내용 identity가
> 아니므로 제외한다.** 따라서 `uq_rag_runtime_bundle_manifest_hash`는 「내용당 한 행」을 뜻하며,
> 동일 Manifest 평가의 재사용 근거가 된다.

정본이 bundle 이름을 identity에서 명시적으로 배제했고, 계약 전반이 "Bundle ID · Manifest Hash" 쌍을 쓴다.

### 기각안

- **D3-B (`bundle_key`).** 정본이 identity가 아니라고 명시했고, 단독으로는 UNIQUE도 아니다
  (`UNIQUE(bundle_key, bundle_version)`).
- **D3-C (`bundle_key@bundle_version`).** 쌍으로는 UNIQUE지만 여전히 정본이 배제한 "이름"이며,
  `@` 합성 규칙이 저장소에 존재하지 않아 새 canonicalization을 발명하게 된다.
- **D3-D (새 immutable bundle authority token).** DB가 이미 가진 identity를 중복 정의하고, 그 token의
  발행·저장·조회 authority를 하나 더 만든다.
- pure kernel test fixture의 `"bundle-001"`은 **근거로 사용하지 않았다.** 테스트 편의 값이며 계약이 아니다.

### cross-environment reuse

`environment_code`가 manifest hash 입력이므로 같은 내용이라도 환경이 다르면 다른 hash·다른 행이 된다.
따라서 `(bundle_id, bundle_manifest_hash)` 쌍은 환경을 가로질러 재사용되지 않으며 D2 검증과 일관된다.

## 8. Request scope persistence rule

본 결정은 `request_scope_codes` persistence를 **구현하지 않고 규칙만 고정한다.** 구현은 NEW A가 소유한다.

`request_scope_codes`는 REQUEST 시점에 그 요청이 Guard에 실제로 제출한 canonical tuple이며 다음을
모두 만족해야 한다.

```text
non-empty
중복 없음
모든 code가 Unicode NFC
UTF-8 byte 순 정렬
exact historical persistence (append-only, 조회 시점 재구성 금지)
```

그리고 저장 시점에 다음을 계산 또는 검증한다.

```text
scope_manifest_hash == canonical_scope_manifest_hash(request_scope_codes)
```

`canonical_scope_manifest_hash`의 정본은 기존 `ai_worker/tasks/rag/citation_authorization.py`이며
새 hash domain을 정의하지 않는다.

### 금지

```text
hash → codes 역산
("GUIDE", "PATIENT_CITATION") 등 default 주입
caller가 임의 codes 전달
ValidatedCitationSelection을 보고 뒤늦게 scope 생성
config 상수·환경변수로 복원
ai_job_execution_context.source_scope_manifest_hash를 동일 canonical domain으로 간주
```

마지막 항목을 명시적으로 고정한다. `source_scope_manifest_hash`는 값을 계산하는 producer가 없고
canonical 정의가 존재하지 않으므로, 이름이 비슷하다는 이유로 Citation의 `scope_manifest_hash`와 같은
것으로 취급하지 않는다.

## 9. PATIENT_CITATION authority boundary

본 결정은 schema를 구현하지 않고 **의미 경계만 고정한다.** 구현은 NEW B가 소유한다.

### Source Use Approval과 Guard Member Decision의 분리

`rag-source-ingestion-v1.md` / #185 governance 계약은 다음 둘을 구분한다.

```text
Source Use Approval
≠
Runtime Guard Member Decision
```

따라서 본 결정은 다음을 고정한다.

```text
PATIENT_CITATION Source Use Approval
≠
Citation Member Decision
```

NEW B는 **Source Use Approval만** 소유한다. member-level 판정은 NEW C가 소유한다(§10).

### 대체 불가 규칙

```text
Source Use Approval (PATIENT_CITATION)
  ≠ RETRIEVAL 승인
  ≠ REQUEST Source/Member Decision
  ≠ Runtime Bundle membership
  ≠ Snapshot CURRENT / ACTIVE 상태
```

어느 것도 PATIENT_CITATION 승인을 대신하지 않는다.

### NEW B가 소유하는 dimensions

```text
source_snapshot_id
source_code
source_version

environment                    (D2 어휘)
purpose = PATIENT_CITATION
approval_version               (불변)

valid_from
expires_at
revoked_at

필요 시:
  scope_policy_ref / hash
  freshness_policy_ref / hash
  approval evidence / actor provenance
```

### NEW B가 소유하지 않는 것

NEW B는 **member-specific approval artifact를 만들지 않는다.** 다음을 approval identity 자체에 넣는
요구는 NEW B 범위에서 제외한다.

```text
member_kind
endpoint_code
operation_code
artifact_code
artifact_version
```

### Source-level 승인의 한계

Source-level PATIENT_CITATION approval은 **Member Decision을 자동 PASS시키지 않는다.** 각 Citation
selection member는 NEW C에서 별도의 request-bound member eligibility/binding 판정을 거친다.

해결책은 member-level approval artifact를 신규 생성하는 것이 **아니라**, 다음 합성이다.

```text
Source Use Approval + member eligibility/binding → Citation Member Decision
```

### synthetic 승격 금지

`ai_worker/tasks/rag/source_governance.py`는 synthetic-only evaluator다. 이를 production issuer로
승격하거나 그 `SyntheticUsePurpose`를 production 어휘의 근거로 사용하지 않는다. 또한 Bundle 구성 목적인
`RagRuntimeSourcePurpose`(`CATALOG | KNOWLEDGE | CANDIDATE_INDEX_INPUT | RULE | GUIDELINE |
SAFETY_POLICY`)를 `UsePurpose`와 동일시하지 않는다.

## 10. Citation Decision / Receipt issuer boundary

NEW C가 소유할 경계를 고정한다.

```text
입력
  CitationAuthorizationRequest              (pure kernel이 생성, selection_manifest 포함)
  NEW A  Per-request REQUEST Guard Decision + Runtime Binding Authority
  NEW B  PATIENT_CITATION Source Use Approval Authority

NEW C가 직접 발행
  request-bound Citation Source Decision
  request-bound Citation Member Decision
  CitationAuthorizationReceipt              (pure kernel 타입 그대로)
```

### Citation Source Decision

다음을 결속한다.

```text
PATIENT_CITATION Source Use Approval
+ environment
+ source snapshot / source_code / source_version
+ request selection
+ current eligibility
```

### Citation Member Decision

다음을 결속한다.

```text
Citation Source Decision
+ exact member identity
    ENDPOINT_OPERATION : endpoint_code, operation_code (nullable)
    ARTIFACT_MEMBER    : artifact_code, artifact_version
+ current Endpoint/Operation 또는 Artifact eligibility
+ selection exact-match
```

그리고 승인된 Source/Snapshot에 해당 member가 실제로 결속되는지 확인한다. snapshot-level Source Use
Approval 하나만 있다고 아무 member나 PASS시키지 않는다.

### pure semantics 소유권

NEW C는 **issuer / persistence / reader**이며 pure verification semantics의 소유자가 아니다.
다음은 재정의·복제·재구현하지 않는다.

```text
request_sha256
validated_selection_sha256
selection_manifest_sha256
UsePurpose.PATIENT_CITATION
selected_for_operation
source_decision / member_decision
verify_citation_authorization_receipt()
AuthorizationReason
finalize_citations()
```

`selected_for_operation` 주의: 현재 production의 `rag_runtime_bundle_source.selected_for_operation`은
**Bundle 구성 단위** 값이다. Receipt의 `selected_for_operation`은 **요청 × PATIENT_CITATION 단위**
사실이므로 Bundle 값을 그대로 옮기지 않는다.

## 11. Rejected alternatives

| 기각안 | 사유 |
| --- | --- |
| D1-A 기존 #713 guard ref를 Citation origin으로 사용 | request instance를 식별하지 못해 cross-request replay 차단 불가, historical reproduction 불충분, "원 REQUEST Guard" 의미와 불일치 |
| D1-B1 #713 guard projection 확장 | 기존 persisted ref와 #709 recomputation contract 파괴 |
| §5 Option A 기존 Guard에 outcome 추가 | dedupe된 class identity에 outcome을 붙이면 "언젠가 PASS"가 되어 오히려 위험. Source/Member와 대칭이 성립하지 않음 |
| `RequestAuthorityDecisionStage`에 `CITATION_AUTHORIZATION` 추가 | enum 추가만으로 동작하지 않고(`_require_request_stage`) 3표 CHECK migration을 동반하며 #709의 `DECISION_STAGE_MISMATCH` 의미를 약화 |
| D2-B 별도 mapping layer | 새 공유 계약을 늘리면서 caller 임의 mapping 위험을 남김 |
| D2-C pure kernel enum 변경 | frozen shared contract 변경. 최후 대안 |
| D3-B / D3-C bundle 이름 사용 | 정본이 이름을 identity에서 명시적으로 배제 |
| D3-D 새 bundle token | DB가 이미 가진 identity 중복 정의 |
| NEW B에 member-level approval artifact 신설 | Source Use Approval과 Guard Member Decision의 계약상 구분을 무너뜨림. member 판정은 NEW C의 request-bound Decision이 소유 |
| `source_scope_manifest_hash` 재사용 | producer 없음, canonical 정의 부재. 이름 유사성은 근거가 아님 |
| synthetic `source_governance` 승격 | 모듈 스스로 runtime authority가 아님을 선언 |

## 12. Migration / shared-contract impact

### 본 결정이 승인되면 발생하는 것

| 대상 | 영향 | 소유 |
| --- | --- | --- |
| `rag_runtime_environment.environment_code` | preflight 후 CHECK 제약 추가 (4값 어휘) | D2 / NEW A 또는 선행 migration |
| `rag_runtime_release_bundle.environment_code` | 동상 | 동상 |
| 테스트 fixture | 어휘 전환과 그에 따른 `bundle_manifest_hash` 재계산 | 동상 |
| Per-request REQUEST Guard Decision · Runtime Binding authority | 신규 테이블 + repository + reader | NEW A |
| PATIENT_CITATION Source Use Approval authority | 신규/확장 테이블 + repository + reader | NEW B |
| Citation Source/Member Decision · Receipt | 신규 테이블 + issuer + reader | NEW C |

### 본 결정이 **바꾸지 않는** 것

```text
rag_runtime/request_authority.py
rag_request_guard_authority / rag_request_source_decision / rag_request_member_decision
ai_worker/tasks/rag/guide_evidence_authority.py
ai_worker/adapters/sqlalchemy_guide_evidence_authority.py
ai_worker/tasks/rag/citation_authorization.py
ai_worker/tasks/rag/citation_finalizer.py
ai_worker/tasks/rag/source_governance.py
ai_job_intake_context / ai_job_execution_context (#174 / #622 경계)
```

Target contract 문서 동기화(`rag-runtime-v1.md`, `rag-source-ingestion-v1.md`,
`docs/contracts/README.md`, `docs/data-schema.md`)는 본 결정 승인 이후 별도 change로 진행한다.

## 13. Child issue split

```text
#799  umbrella / acceptance
  ├─ NEW A  Per-request REQUEST Guard Decision · Runtime Binding Authority
  ├─ NEW B  PATIENT_CITATION Production Source Use Approval Authority
  └─ NEW C  CITATION_AUTHORIZATION Decision·Receipt Historical Persistence
```

### NEW A — Per-request REQUEST Guard Decision · Runtime Binding Authority

- Owner `@ceohwj` / Required reviewer `@phina-io`
- Depends on: 본 결정 (D1, §5, D2, D3, §8)
- 소유: §4의 canonical projection 전체(`request_guard_decision_id`, `actual_decision_outcome`,
  `user_id`, `request_operation_code`, `decision_stage`, `environment`, `bundle_id`,
  `bundle_manifest_hash`, `request_scope_codes`, `scope_manifest_hash`, `legacy_request_authority_ref`)의
  append-only 저장·조회.
- 출력: 기존 pure `OriginRequestGuardBinding`과 `RuntimeAuthorizationBinding`을 **caller input 없이**
  구성할 수 있는 production reader seam. 실제 타입명은 child issue에서 저장소 naming convention에 맞춰
  확정하고, 본 결정은 의미만 고정한다.
- 금지: caller self-asserted PASS, Guard row 존재 → PASS 추론, #713 의미·projection·스키마 변경,
  `RequestAuthorityDecisionStage` 확장, #174 async execution context를 동기 Guide에 새로 강제,
  `source_scope_manifest_hash` 재사용, hash → scope 역산.

### NEW B — PATIENT_CITATION Production Source Use Approval Authority

- Owner `@ceohwj` / Required reviewer `@phina-io` / Specialist FYI `@Jye-rookie`, `@hazelnutflavoured`
- Depends on: 본 결정 **D2 environment decision** (environment가 승인 dimension이므로 공유 선행)
- **NEW A 구현과는 병렬 가능**하다. 코드·스키마 의존이 없다. 다만 D2 Decision 없이는 착수할 수 없다.
- 소유: §9의 Source Use Approval dimensions.
- 소유하지 않음: member-specific approval artifact. member 판정은 NEW C.

### NEW C — CITATION_AUTHORIZATION Decision·Receipt Historical Persistence

- Owner `@ceohwj` / Required reviewer `@phina-io`
- Depends on: NEW A, NEW B 둘 다
- 소유: §10의 request-bound Citation Source Decision, Citation Member Decision,
  `CitationAuthorizationReceipt` 발행·저장·조회.
- 금지: pure kernel(`citation_authorization.py`, `citation_finalizer.py`) 수정, pure hash/enum/verify
  의미 재정의, `rag_request_*` 3표 CHECK 변경.

## 14. Activation / non-activation

본 결정은 공유 의미 3건과 그에 따르는 규칙만 확정한다. 완료 상태는 다음이며

```text
CITATION_AUTHORIZATION_AUTHORITY_BOUNDARY_DECISION_READY
```

이는 다음이 아니다.

```text
CITATION_AUTHORIZATION_PRODUCTION_AUTHORITY_READY
```

#799는 NEW A / NEW B / NEW C가 모두 완료되고 아래가 성립할 때까지 OPEN으로 유지한다.

```text
RuntimeAuthorizationBinding production 구성 가능
OriginRequestGuardBinding production 구성 가능
PATIENT_CITATION Source Use Approval authority 존재
CitationAuthorizationReceipt production observation 가능
existing pure verifier exact pass
caller self-asserted PASS 없음
```

This Decision does not authorize:

- patient-visible Citation
- Release
- Guide persistence
- `PUBLIC_TRACK_F`
