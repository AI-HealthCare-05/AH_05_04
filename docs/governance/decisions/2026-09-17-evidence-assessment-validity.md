# Product Decision Candidate: Evidence Assessment Validity Contract (#722)

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-722-20260917` |
| 상태 | Candidate / Review Pending · Issue #722 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 단일 책임 리뷰 | 송은영 (`@phina-io`) — Backend, DB·Security 기술 통제 및 Track F Chat 데이터 경계 |
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
그러나 상류 런타임 검색 계층에 `assessment_valid_until`을 산출할 승인된 권위(authoritative expiry/deadline)가 부재하였고, 근거 없는 임의 TTL(`valid_until = evaluated_at + timedelta(hours=24)`)의 코드 날조가 엄격히 금지됨에 따라 #712 작업은 `BLOCKED_BY_ASSESSMENT_VALIDITY_AUTHORITY`로 fail-closed 중단되었다.

본 결정은 #712의 유일한 차단 요인인 `assessment_valid_from`과 `assessment_valid_until`의 authoritative contract를 명시적으로 확정하기 위한 경량 거버넌스 계약이다.
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

**결론**: 실제 Runtime Evidence에 직접 exact-bind할 수 있는 기존 authoritative expiry/deadline이 0건이므로, 아래 **Path B (versioned Assessment Validity Policy)** 를 공식 적용한다.

---

## 3. `assessment_valid_from` 정본 계약

`assessment_valid_from`은 다음과 같이 고정한다:

> **해당 selected evidence의 Production Evidence Gate 및 Eligibility evaluation이 성공하여 assessment artifact가 성립한 authoritative evaluation timestamp.**

* **산출식**:
  ```text
  assessment_valid_from = authoritative evaluated_at
  ```
* **시간대 규칙**: 반드시 **timezone-aware UTC** (`datetime.timezone.utc`)여야 하며, naive datetime 또는 비-UTC 값은 fail-closed 거부된다.
* **불변성**: 평가 완료 시점의 evaluation context timestamp를 그대로 고정하며, 과거 CURRENT 상태를 보고 사후에 재계산하지 않는다.

---

## 4. `assessment_valid_until` 정본 및 우선순위 규칙

`assessment_valid_until` 산출 우선순위는 다음과 같이 엄격히 계층화한다.

```text
[평가 시점 (evaluated_at)]
       │
       ▼
Path A: 실제 Runtime authority 존재 여부 확인
       ├── [YES] ──► min(applicable runtime approval expiry, applicable source freshness deadline)
       │
       └── [NO]  ──► Path B: EvidenceAssessmentValidityPolicy v1 (ceiling: 24h)
                           │
                           ▼
              effective_valid_until = min(valid_from + policy.max_validity_duration, applicable authoritative upper bounds)
```

### Path A — 기존 Runtime authority가 존재하는 경우
실제 Runtime Evidence에 적용 가능하고 해당 selection에 exact-bind할 수 있는 authoritative expiry/freshness boundary가 존재하는 경우:
```text
assessment_valid_until = min(
    applicable runtime approval expiry,
    applicable source/snapshot freshness deadline,
    other explicitly applicable authoritative expiry
)
```
단, 실제로 존재하고 해당 selection에 exact-bind할 수 있는 upper bound만 포함하며, Catalog 전용 승인, Job lease 등 무관한 만료 시점은 결합 대상에서 제외한다.

### Path B — 기존 Runtime upper bound가 없는 경우 (현 Post-MVP-1 기준)
기존 Runtime Evidence upper bound가 부재하므로, 명시적인 Product/Runtime 거버넌스 계약인 **`EvidenceAssessmentValidityPolicy v1`** 을 도입한다.
본 정책에 따른 산출 규칙:
```text
assessment_valid_until = min(
    assessment_valid_from + policy.max_validity_duration,
    applicable authoritative upper bounds
)
```
* `policy.max_validity_duration`은 **maximum operational ceiling**으로 기능한다.
* 평가 시점에 유효한 더 이른 authoritative boundary가 존재할 경우 그것이 ceiling보다 우선한다.

---

## 5. `EvidenceAssessmentValidityPolicy v1` 규격

본 값은 애플리케이션 코드에 은닉된 임의 TTL이 아니며, 공식 승인 대상 거버넌스 계약이다.

### 5.1 정책 정의
* **`policy_code`**: `evidence-assessment-validity`
* **`version`**: `v1`
* **`max_validity_duration`**: `24 hours` (`timedelta(hours=24)` / `86,400 seconds`)
* **`semantics`**:
  > **하나의 Production Evidence assessment 결과를 재검증 없이 Runtime Handoff에서 재사용할 수 있는 최대 operational lifetime.**
  * 본 24시간은 의약품 소스 데이터가 24시간마다 변경된다는 의학적·데이터적 주장이 아니며, 시스템의 fail-closed 운영 안전 상한(operational safety ceiling)이다.

### 5.2 Canonical Projection 및 식별자 (`ImmutableArtifactRef`)
Issuer는 본 정책 identity를 assessment artifact 및 authority provenance에 결속해야 한다.

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
  * `artifact_code = "evidence-assessment-validity"`
  * `version = "v1"`
  * `content_sha256 = SHA256(canonical_json_bytes(payload))` (lowercase 64-hex)
  * `ref = ImmutableArtifactRef("evidence-assessment-validity", "v1", content_sha256)`

---

## 6. 구간 및 결정론 불변식 (Interval & Determinism Rules)

### 6.1 Half-Open Interval 규칙
기존 Handoff 계약과 100% 일관성을 유지한다:
```text
assessment_valid_from <= evaluated_at < assessment_valid_until
```
* 시작 시점(`assessment_valid_from`)은 **inclusive** (`<=`).
* 종료 시점(`assessment_valid_until`)은 **strictly exclusive** (`<`).
* 모든 datetime 비교는 UTC tz-aware 기준이어야 한다.

### 6.2 Retry 결정론 (Retry Determinism)
* 동일한 production assessment를 재시도(retry)할 때 wall-clock `now()`를 재조회하여 validity window를 연장하는 행위는 엄격히 금지된다.
* 최초 authoritative evaluation timestamp를 고정한다:
  ```text
  assessment_valid_from = frozen evaluated_at (T0)
  ```
* 동일한 policy 및 applicable upper bounds 하에서:
  ```text
  same assessment_valid_from
  same assessment_valid_until
  ```
  이 보장되어야 한다 (예: T0 시점 평가 후 T0 + 10분에 재시도하더라도 `valid_from = T0`, `valid_until = T0 + 24h`로 고정되며, `(T0 + 10m) + 24h`로 밀어내지 않는다).

### 6.3 과거 이력 불변성 (Historical Immutability)
* Assessment authority가 발급·영속화된 후, 외부 환경에서 다음과 같은 변화가 발생하더라도 과거 persisted assessment validity window 자체를 사후 수정(mutate)하거나 다시 쓰지 않는다:
  - Source snapshot 상태가 `STALE`로 변경됨
  - 특정 operator approval이 revoke됨
  - 새로운 Snapshot이 인입되어 `CURRENT`가 교체됨
* 과거 아티팩트는 불변(`historical artifact = immutable`)이다.
* 현재 시점의 적격성 확인이 필요한 경우, 다운스트림 런타임에서 별도의 currentness / revocation 검사를 수행하며, 과거 authority 레코드를 CURRENT DB state로 재구성하지 않는다.

---

## 7. Issue #712 소비 인터페이스 계약 (Input Contract for #712)

본 결정을 통해 Issue #712 구현 담당자는 다음 입력 계약을 기반으로 Authority Issuer 및 Persistence를 구현한다:

```text
Inputs:
  - evaluated_at: datetime (UTC tz-aware)
  - policy: EvidenceAssessmentValidityPolicy (v1)
  - applicable_upper_bounds: tuple[datetime, ...] (UTC tz-aware)

Derivations:
  - assessment_valid_from = evaluated_at
  - assessment_valid_until = min(evaluated_at + policy.max_validity_duration, *applicable_upper_bounds)

Bound Provenance:
  - validity_policy_ref: ImmutableArtifactRef
  - assessment_valid_from: datetime
  - assessment_valid_until: datetime

Persistence Constraints:
  - UNIQUE (retrieval_run_id, knowledge_chunk_id)
  - CHECK (assessment_valid_from < assessment_valid_until)
```

---

## 8. 범위 한계 및 절대 금지 사항 (Scope & Strict Prohibitions)

본 결정 문서의 범위는 거버넌스 계약 확정에 한정되며 다음 사항은 배제 및 금지된다:
1. `rag_evidence_authority` DB 모델 추가 및 Alembic 마이그레이션 작성 금지 (#712 소관).
2. Assessment/Eligibility Issuer 및 Reader 구현 금지 (#712 소관).
3. #709 REQUEST Guard/Source/Member persistence 구현 금지.
4. Handoff assembly, content_text hydration, LangGraph, Guideline Generator, Citation Authorization 구현 금지.
5. DB Trigger, Row Level Security, Stored Procedure, 사용자 정의 DB 함수 일절 도입 금지 ([AGENTS.md](../../../AGENTS.md) 준수).
6. Catalog 전용 만료 시점을 Runtime authority로 재해석하는 행위 금지.
7. `now()` 기반 retry 만료 시점 연장 금지.
8. CURRENT DB 상태로부터 과거 validity window 재구성 금지.

---

## 9. 차단 해소 및 승인 절차 (Resolution & Blocker Transition)

* **기술적 설계 종결**: 본 결정으로 `assessment_valid_from`과 `assessment_valid_until`의 산출 규칙, 우선순위, 불변식, projection 사양이 완전히 확정되었다. 더 이상의 열린 기술 설계나 브레인스토밍은 불필요하다.
* **단일 책임 리뷰어 승인 대기**:
  - [AGENTS.md](../../../AGENTS.md) 규칙에 따라 구현자(`@ceohwj`)의 자가 승인은 불허되며, 단일 책임 리뷰어 송은영(`@phina-io`)의 검토 및 승인이 필요하다.
  - 따라서 Issue #712의 구현 차단 코드는 원인 불명 정책 부재였던:
    ```text
    BLOCKED_BY_ASSESSMENT_VALIDITY_AUTHORITY
    ```
    에서, 공식 거버넌스 정책 승인 이벤트 대기 상태인:
    ```text
    BLOCKED_BY_VALIDITY_POLICY_APPROVAL
    ```
    단 하나로 좁혀진다.
* **#712 재개 조건**:
  - 책임 리뷰어의 승인(`APPROVED`) 이벤트 확인 즉시 상태는 `ASSESSMENT_VALIDITY_CONTRACT_RESOLVED`로 전환되며, Issue #712의 persistence 및 issuer 구현이 즉시 재개된다.
