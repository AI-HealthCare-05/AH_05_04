# Product Decision: Evidence Assessment Validity Contract (#722)

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-722-20260917` |
| 상태 | **Approved** (단일 책임 리뷰어 승인 확정 · PR #725 repository integration pending merge) |
| 승인 근거 | PR #725 단일 책임 리뷰어(`@phina-io`)의 Path B 공식 승인 의사결정 코멘트 (2026-09-17) |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 단일 책임 리뷰 | 송은영 (`@phina-io`) — Backend, DB·Security 기술 통제 및 Track F Chat 데이터 경계 (승인 완료) |
| 필요 교차 리뷰 | 권가빈 (`@hazelnutflavoured`) — PM·Product Acceptance·Privacy Gate / 김지혜 (`@Jye-rookie`) — Worker & Source Provenance |
| 추적 Issue | [#722](https://github.com/AI-HealthCare-05/AH_05_04/issues/722) |
| 소비 Issue | [#712](https://github.com/AI-HealthCare-05/AH_05_04/issues/712) (Production Assessment·Eligibility Authority Persistence & Issuer), [#180](https://github.com/AI-HealthCare-05/AH_05_04/issues/180) |
| 상위 결정 | [`PD-362-20260909`](./2026-09-09-source-snapshot-approval-boundary.md), [`PD-315-20260908`](./2026-09-08-production-evidence-retrieval-contract-divergence.md), [`PD-180-20260915`](./2026-09-15-guide-evidence-handoff.md), [`PD-178-20260916`](./2026-09-16-retrieval-selection-manifest-jcs.md), [`PD-672-20260916`](./2026-09-16-sync-guide-evidence-authority.md) |

---

## 1. 목적과 배경 (Context & Purpose)

Issue #712 Phase A에서 Production Evidence Assessment 및 Eligibility Authority 영속화를 조사한 결과, 다음 5개 필드가 현재 영속 계층에 존재하지 않음을 확인하였다:
```text
eligibility_receipt_ref
assessment_artifact_ref
verifier_artifact_ref
assessment_valid_from
assessment_valid_until
```

Guide Evidence Handoff 계약([guide-evidence-handoff-v1.md](../../contracts/proposed/post-mvp-1/guide-evidence-handoff-v1.md) 및 `PD-180-20260915`)은 증거의 신선도 조건으로 `assessment_valid_from <= evaluated_at < assessment_valid_until`을 필수로 요구한다.
그러나 상류 런타임 검색 계층에 `assessment_valid_until`을 산출할 승인된 권위(authoritative expiry/deadline)가 부재하였고, 근거 없는 임의 TTL의 코드 날조가 엄격히 금지됨에 따라 #712 작업은 `BLOCKED_BY_ASSESSMENT_VALIDITY_AUTHORITY`로 fail-closed 중단되었다.

본 결정은 #712의 유일한 차단 요인인 `assessment_valid_from`과 `assessment_valid_until`의 authoritative contract를 확정하기 위한 거버넌스 계약이다.
PR #725 리뷰 과정에서 단일 책임 리뷰어(`@phina-io`)가 Path B 정책 채택을 명시적으로 승인함에 따라, 본 문서는 승인된 운영 안전 상한(operational safety ceiling)의 정본 계약과 권위 경계를 normative하게 확정한다.
본 결정은 새로운 Freshness 하위 시스템, DB 스키마, 마이그레이션, 런타임 구현으로 확장하지 않으며, 오직 #712 Issuer 및 후속 #180 Orchestrator가 소비할 유효 기간 권위의 의미와 산출 규칙을 봉인한다.

---

## 2. 확인된 기존 권위 및 한계 (Authority Boundary Findings)

저장소 조사 결과는 다음과 같다:

1. **Runtime Evidence용 명시적 approval expiry 부재**:
   - `rag_source_snapshot`, `rag_source_snapshot_verification`, `retrieval_run`, `retrieval_hit` 등 프로덕션 검색 및 스냅샷 원장에 만료 시점 컬럼이 존재하지 않는다.
   - `backend/app/models/catalog_approval.py`의 `CatalogSourceApproval`(`valid_from`, `expires_at`)은 **Catalog 빌드 전용 운영자 승인**이다. 계약상 Runtime Evidence 사용까지 포함한다고 명시적으로 증명되지 않았으므로, `catalog_source_approval.expires_at`을 런타임 `assessment_valid_until`로 재해석하지 않는다.
2. **Source/Snapshot freshness deadline 부재**:
   - `PD-362-20260909` line 218-219는 Freshness 계산 구현을 명시적으로 제외하고 #178 소관으로 이관하였다.
   - `Issue #178 PostgreSQL hybrid search design` line 351-353 또한 현재 스키마의 `CURRENT` 확인을 Target Freshness 완료로 취급하지 않고 후속 과제로 유보하였다.
   - 따라서 `CURRENT` Snapshot 상태를 사후 조회하여 historical validity upper bound로 날조하거나 재해석하지 않는다.
3. **Runtime execution context expiry 부재**:
   - `ai_job_execution_context`에는 context 만료나 validity window 컬럼이 정의되어 있지 않다.
4. **기존 versioned freshness/validity policy 부재**:
   - `VersionedEvidenceGatePolicy` 등 기존 provisional 정책에는 coverage item 개수만 정의되어 있으며 유효 기간 산출 로직이 없다.

**결론**: 실제 Runtime Evidence에 직접 exact-bind할 수 있는 기존 authoritative expiry/deadline이 0건이므로, 책임 리뷰어의 승인을 거친 **Path B (versioned Assessment Validity Policy)** 를 공식 적용한다.

---

## 3. "Arbitrary TTL" 논리 정정과 권위의 근거 (Grounding of Policy Authority)

본 결정에 도입된 `24h` 정책값의 권위적 성격에 대해 다음 사항을 명확히 기록한다:

1. **기존 데이터/시스템 사실로부터의 유도 부인**:
   - `24h`는 기존 Source/Runtime 원장, 의약품 허가사항 갱신 주기, 또는 데이터 자체의 유효기간에서 유도(derive)된 값이 아니다.
2. **형식에 의한 비임의성 주장 배제**:
   - 단순히 versioned policy 형식을 갖추었거나 `ImmutableArtifactRef`로 감쌌다는 절차적 포장만으로 임의값이 비임의적 값으로 전환되는 것은 아니다.
3. **책임 거버넌스 결정에 의한 권위 부여 (Approved Policy Authority)**:
   - `24h`는 본 결정(`PD-722`)에서 단일 책임 리뷰어(`@phina-io`)의 명시적인 검토와 승인을 통해 새로 확정된 **versioned operational safety policy value**이다.
   - 따라서 승인 이벤트 전에는 제안안(proposal)이었으며, 책임 리뷰어의 승인 이벤트(PR #725 decision comment) 이후 본 결정(`PD-722`) 자체가 해당 정책값의 authority가 된다.

---

## 4. 승인된 정본 계약 (Approved Normative Contract)

### 4.1 `assessment_valid_from` 정본

```text
assessment_valid_from = authoritative evaluated_at
```

* **정의**: Production Evidence Gate 및 Eligibility evaluation이 성공하여 해당 assessment artifact가 성립한 최초 authoritative evaluation timestamp.
* **시간대 규칙**: 반드시 **timezone-aware UTC** (`datetime.timezone.utc`)여야 하며, naive datetime 또는 비-UTC 값은 fail-closed 거부된다.
* **불변성**: 최초 평가 시점의 evaluation context timestamp를 그대로 동결하며, retry 시 `now()`로 재계산하거나 CURRENT Source/Snapshot 상태에서 사후 재구성하지 않는다.

### 4.2 `assessment_valid_until` 정본 및 우선순위 규칙

```text
assessment_valid_until = min(
    assessment_valid_from + 24h,
    applicable authoritative upper bounds
)
```

* **산출 규칙**:
  - `24h`는 승인된 `EvidenceAssessmentValidityPolicy` (`policy_code = "evidence-assessment-validity"`, `version = "v1"`)의 **operational maximum ceiling**이다.
  - 실제 Runtime Evidence에 exact-bind 가능한 더 이른 authoritative upper bound(예: 실제 적용 가능한 승인 만료 시점 등)가 존재하는 경우 그것이 우선한다 (`min(...)`).
  - 존재하지 않는 상한을 허구로 만들어내지 않으며, Catalog 전용 승인 만료일(`catalog_source_approval.expires_at`)이나 Job lease 등 무관한 만료 시점은 결합 대상에 포함하지 않는다.

---

## 5. 반드시 명시할 비의미 (Explicit Non-Claims)

본 정책 승인(`PD-722`)으로 다음 사항을 결코 주장하거나 보증하지 않는다:

```text
[X] Source가 24시간 동안 의학적으로 최신이라는 주장
[X] Source가 24시간 동안 데이터적으로 최신이라는 주장
[X] 의약품 허가사항이 24시간 동안 변하지 않는다는 주장
[X] Source publication freshness가 24시간이라는 주장
[X] Runtime Evidence 자체를 사용자 공개 근거로 승인한다는 의미
[X] 실제 사용자 대상의 의료 안전성을 승인한다는 의미
[X] PUBLIC_TRACK_F 게이트를 해제한다는 의미
[X] Guide/Chat Runtime 공개를 승인한다는 의미
[X] #709 완료, content hydration 완료, #180 orchestration 완료 선언
```

**`24h`의 유일한 정규 의미**:
> **Production Evidence assessment 결과를 재검증 없이 Handoff에서 재사용할 수 있는 최대 operational lifetime (fail-closed operational safety ceiling).**

---

## 6. 구간, 결정론 및 불변성 규칙 (Interval, Determinism & Immutability)

### 6.1 Half-Open Interval 규칙
기존 Handoff 계약([guide-evidence-handoff-v1.md](../../contracts/proposed/post-mvp-1/guide-evidence-handoff-v1.md))과 100% 일치한다:
```text
assessment_valid_from <= evaluated_at < assessment_valid_until
```
* 시작 시점(`assessment_valid_from`)은 **inclusive** (`<=`).
* 종료 시점(`assessment_valid_until`)은 **strictly exclusive** (`<`).
* 모든 비교는 timezone-aware UTC 기준이다.

### 6.2 Retry 결정론 (Retry Determinism)
* 동일한 production assessment를 재시도(retry)할 때 wall-clock `now()`를 재조회하여 validity window를 연장하는 행위는 엄격히 금지된다.
* 최초 authoritative evaluation timestamp를 동결(`T0`)한다:
  - 최초 assessment evaluation = `T0`
  - retry execution = `T0 + 10분`
  - 결과:
    ```text
    assessment_valid_from = T0
    assessment_valid_until = T0 + 24h
    ```
    (결코 `(T0 + 10분) + 24h`로 밀어내지 않는다).
* 재시도가 validity window를 연장하는 것은 fail-closed 계약 위반이다.

### 6.3 과거 이력 불변성 (Historical Immutability)
* Assessment authority가 발급되어 영속화된 이후, 외부 환경에서 다음과 같은 변화가 발생하더라도 과거 persisted assessment validity window 자체를 사후 수정하거나 CURRENT state로 재계산하지 않는다:
  - Source snapshot 상태가 `STALE`로 변경됨
  - 특정 operator approval이 revoke됨
  - 새로운 Snapshot이 인입되어 `CURRENT`가 교체됨
  - Source 메타데이터 상태 변경
* 과거 영속화된 authority는 불변 사실(`historical artifact = immutable`)이다.
* 현재 시점의 적격성/유효성(viability/currentness/revocation) 판정은 런타임 소비 계층의 별도 검사 책임이다.

---

## 7. `validity_policy_ref` 및 #712 소비 인터페이스 계약

후속 Issue #712 authority issuer는 authority 레코드를 발급할 때 다음 불변 provenance를 결속해야 한다:

```text
Bound Provenance:
  - validity_policy_ref: ImmutableArtifactRef
  - assessment_valid_from: datetime (UTC tz-aware)
  - assessment_valid_until: datetime (UTC tz-aware)
```

### 7.1 `EvidenceAssessmentValidityPolicy v1` 아티팩트 규격
* **`policy_code`**: `"evidence-assessment-validity"`
* **`version`**: `"v1"`
* **`max_validity_duration`**: `24 hours` (`86,400 seconds`)
* **Canonical Projection Payload (RFC 8785 JCS)**:
  ```json
  {
    "max_validity_duration_seconds": 86400,
    "policy_code": "evidence-assessment-validity",
    "semantics": "Maximum operational lifetime for reusing a production evidence assessment in runtime handoff without re-evaluation.",
    "version": "v1"
  }
  ```
* **식별자**:
  - 기존 저장소 공용 직렬화 모듈(`canonical_json_bytes`)을 재사용한다 (신규 serializer 작성 금지).
  - `content_sha256 = SHA256(canonical_json_bytes(payload))` (lowercase 64-hex).
  - `validity_policy_ref = ImmutableArtifactRef("evidence-assessment-validity", "v1", content_sha256)`.

### 7.2 #712 Issuer 입력 및 영속화 스키마 계약
```text
Inputs:
  - evaluated_at: datetime (UTC tz-aware)
  - policy: EvidenceAssessmentValidityPolicy (v1)
  - applicable_upper_bounds: tuple[datetime, ...] (UTC tz-aware)

Derivations:
  - assessment_valid_from = evaluated_at
  - assessment_valid_until = min(evaluated_at + policy.max_validity_duration, *applicable_upper_bounds)

Persistence Constraints:
  - UNIQUE (retrieval_run_id, knowledge_chunk_id)
  - CHECK (assessment_valid_from < assessment_valid_until)
```
*주의: 이번 PR #725에서는 실제 DB persistence, 모델, 마이그레이션, Issuer 코드를 구현하지 않는다.*

---

## 8. 범위 한계 및 절대 금지 사항 (Scope & Strict Prohibitions)

본 결정의 범위는 거버넌스 권위 계약 확정에 엄격히 한정되며 다음 사항은 배제 및 금지된다:
1. `rag_evidence_authority` DB 모델 추가 및 Alembic 마이그레이션 작성 금지 (#712 소관).
2. Assessment/Eligibility Issuer 및 Reader 구현 금지 (#712 소관).
3. #709 REQUEST Guard/Source/Member persistence 구현 금지.
4. Handoff assembly, content_text hydration, LangGraph, Guideline Generator, Citation Authorization 구현 금지.
5. DB Trigger, Row Level Security, Stored Procedure, 사용자 정의 DB 함수 일절 도입 금지 ([AGENTS.md](../../../AGENTS.md) 준수).
6. Catalog 전용 만료 시점을 Runtime authority로 재해석하는 행위 금지.
7. `now()` 기반 retry 만료 시점 연장 금지.
8. CURRENT DB 상태로부터 과거 validity window 재구성 금지.

---

## 9. 차단 해소 및 승인·통합 상태 (Resolution & Integration Status)

* **정책 의사결정 상태**:
  - 단일 책임 리뷰어(`@phina-io`)의 명시적 Path B 승인(PR #725 코멘트)에 따라, 기존의 기술 블로커였던 `BLOCKED_BY_VALIDITY_POLICY_APPROVAL`은 완전히 해소되었다.
  - Issue #712에서 발견된 `BLOCKED_BY_ASSESSMENT_VALIDITY_AUTHORITY`를 해결하는 권위 계약이 본 결정(`PD-722`)으로 확정되었다.
* **명확한 상태 구분**:
  ```text
  Policy authority contract:       RESOLVED (Approved by responsible reviewer @phina-io)
  PR #725 repository integration:  PENDING MERGE (문서 개정 검토 및 머지 대기)
  #712 implementation:             NOT STARTED / RESUME AFTER MERGE
  ```
* **후속 작업 연결**:
  - PR #725가 `develop` 브랜치에 머지되기 전에는 `#712 구현 완료`나 `ASSESSMENT_ELIGIBILITY_AUTHORITY_PERSISTED`를 선언하지 않는다.
  - PR #725 머지 완료 확인 후 Issue #712의 authority persistence 및 issuer 구현을 공식 재개한다.
