# Product Decision Candidate: RAG-15 Guideline Formal Approval Pack & Handoff Contract

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-179-20260917` |
| 상태 | Candidate / Formal Approval Pending · Issue #179 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — PM·Product Acceptance·Privacy Gate |
| 필요 교차 리뷰 | 송은영 (`@phina-io`) — Backend·Data Boundary / 김지혜 (`@Jye-rookie`) — Source Provenance·Fallback Alignment |
| 추적 Issue | [#179](https://github.com/AI-HealthCare-05/AH_05_04/issues/179) |
| 소비 Issue | [#180](https://github.com/AI-HealthCare-05/AH_05_04/issues/180) |
| 상위 결정 | [`PD-362-20260909`](./2026-09-09-source-snapshot-approval-boundary.md), [`PD-315-20260908`](./2026-09-08-production-evidence-retrieval-contract-divergence.md), [`PD-180-20260915`](./2026-09-15-guide-evidence-handoff.md), [`PD-178-20260916`](./2026-09-16-retrieval-selection-manifest-jcs.md) |

## 목적과 배경 (Context & Purpose)

PR #690에서 `GuidelineGeneratorPort`를 구현하는 `OpenAIGuidelineGeneratorAdapter` 및 `build_candidate_provenance`가 병합되었다.
Issue #179의 핵심 목적은 “승인된 입력을 인위적으로 날조하는 것”이 아니라:

> **#179의 기술적 산출물(Prompt, Model, Parser, Validator, Policy, Fallback Set)을 #180이 즉시 소비할 수 있도록 검증 가능하고 승인 상태가 명시된 불변 입력(Immutable Approval Pack)으로 봉인하는 것**

이다.

외부 권위 승인 증적(Medical, Pharmacy, Source, Privacy, Safety)이 확보되기 전에는 승인 상태를 추정하거나 임의로 부여하지 않으며, 승인 팩은 다음 상태로 정상 생성된다:
```text
approval_status = PENDING
production_consumable = false
```

본 결정은 후속 #180 Runtime Orchestrator가 불투명 런타임 설정이나 DB 스키마 변경 없이 RAG-15 기술 후보와 승인 증적을 순수 fail-closed 방식으로 검증하고 안전하게 소비할 수 있는 계약을 고정한다.

## 핵심 결정 사항

### 1. 2단계 불변 식별자 분리 (Two-Stage Identity)
승인 위조 및 replay 공격을 원천 차단하기 위해 기술적 후보 식별자(`candidate_ref`)와 승인 팩 식별자(`pack_ref`)를 명확히 분리한다.

1. **`candidate_ref` (Technical Candidate Identity)**:
   - 승인 대상인 기술적 구현체 및 정책의 canonical projection SHA-256 해시로 구성된다.
   - 구성 요소:
     * `generation_provenance`: `prompt_ref`, `model_ref`, `parser_ref`, `validator_ref`
     * `policy_ref`: `VersionedGuidelinePolicy` 아티팩트 참조
     * `fallback_pins`: 모든 `GuidelineFallbackCode`의 `code` 및 `artifact_ref` (code.value 기준 정렬)
   - 식별자:
     * `artifact_code = "rag15-guideline-candidate"`
     * `version = "rag15-guideline-v1"`
     * `content_sha256 = SHA256(canonical(generation_provenance, policy_ref, ordered fallback pins))`

2. **`pack_ref` (Approval Pack Identity)**:
   - 기술 후보와 외부 승인 증적 상태를 결속한 canonical projection SHA-256 해시로 구성된다.
   - 구성 요소:
     * `candidate_ref`: 위 기술 후보 참조
     * `approval_evidence`: 각 승인 영역별 증적 (`scope`, `approval_status`, `candidate_ref`, `decision_ref`; scope 기준 정렬)
   - 식별자:
     * `artifact_code = "rag15-approval-pack"`
     * `version = "rag15-guideline-v1"`
     * `content_sha256 = SHA256(canonical(candidate_ref, ordered approval evidence))`

### 2. 기술 후보 구성 요소 엄격성 (Technical Candidate Components)
- **Prompt candidate**: exact prompt version (`guideline-claim-selector-v1`) + 시스템 지시문 UTF-8 바이트 해시
- **Model candidate**: canonical identity (`openai:<model>`) + UTF-8 바이트 해시. 프로덕션 임의 기본값(예: `gpt-4o`)을 두지 않으며, 호출 시 명시적으로 주입받는다.
- **Parser candidate**: 실제 파서 모듈(`ai_worker/tasks/rag/guideline_generator_prompt.py`) 소스 파일 바이트 SHA-256 해시
- **Validator candidate**: 실제 카드 커널 모듈(`ai_worker/tasks/rag/guideline_card.py`) 소스 파일 바이트 SHA-256 해시
- **Policy candidate**: `create_canonical_guideline_policy`를 통해 생성된 `VersionedGuidelinePolicy` 참조. `maximum_claims`는 기본값 없이 명시적으로 주입받는다.
- **Fallback pins**: `GuidelineFallbackCode` 전체 멤버(누락 없음, 중복 없음, 정규화된 한글 copy 보존)에 대한 불변 핀 튜플

### 3. 승인 어휘 및 필수 승인 영역 (Approval Vocabulary & Scopes)
- **어휘(Vocabulary)**: 신규 enum을 생성하지 않으며 기존 런타임 어휘와 일치하는 string literal로 제한한다:
  * `"PENDING"`
  * `"APPROVED"`
  * `"REJECTED"`
- **필수 승인 영역 (Required Scopes)**: Issue #179 Production 활성화 조건에 따른 5개 필수 영역:
  1. `MEDICAL` (의료 전문가 승인)
  2. `PHARMACY` (약학 전문가 승인)
  3. `SOURCE` (공식 의약품 소스 최신성 및 인가 승인)
  4. `PRIVACY` (개인정보 보호 및 비식별화 게이트)
  5. `SAFETY` (Product / Safety Reviewer 경계 승인)
- 5개 영역은 exact set이어야 하며 누락·중복·임의 확장은 fail-closed 거부된다.

### 4. 증적 결속 및 Replay 방지 (Evidence Binding & Replay Protection)
단순히 `decision_ref`의 존재나 호출자의 일방적 선언만으로는 승인이 성립하지 않으며, 다음 **2단계 결속(Two-Stage Binding)**을 모두 충족해야만 승인 증적이 검증된다:

1. **Internal Pack Binding (내부 팩 정합성)**:
   - 각 증적의 `evidence.candidate_ref == pack.candidate_ref`가 강제된다.
   - 다른 후보에 속한 증적을 팩 내부에 혼입하는 것을 차단한다.

2. **External Decision Authority Binding (외부 권위 결정 결속)**:
   - 전용 결정 검증 포트(`Rag15ApprovalDecisionVerifierPort`)를 통해 외부 권위가 인증한 실제 결정 레코드와 대조한다:
     ```text
     verifier가 인증한:
       decision_ref
       candidate_ref
       scope
       approval_status
     ==
     pack evidence가 선언한:
       decision_ref
       candidate_ref
       scope
       approval_status
     ```
   - Candidate A에 대해 발급된 결정을 Candidate B에 재포장하거나, SAFETY 결정을 MEDICAL에 재사용하거나, REJECTED 결정을 APPROVED로 허위 선언하는 모든 행위는 verifier와의 exact-match 실패로 fail-closed 거부된다.
   - `APPROVED` 및 `REJECTED` 상태의 증적은 반드시 권위 결정 참조(`decision_ref`)를 포함해야 하며, `PENDING` 증적의 경우 외부 결정 검증을 호출하지 않는다.

### 5. 순수 검증 커널 및 소비 가능성 경계 (Pure Verification & Production Consumability)
- 검증 인터페이스는 DB 의존성 및 네트워크 호출이 없는 순수 함수로 구현된다:
  ```python
  def verify_rag15_approval_pack(
      pack: Rag15ApprovalPack,
      *,
      decision_verifier: Rag15ApprovalDecisionVerifierPort,
  ) -> Rag15ApprovalPackVerification:
  ```
- **검증 항목**:
  1. Runtime Candidate Drift: 현재 실행 코드의 candidate provenance(`prompt`, `model`, `parser`, `validator`)와 pack의 provenance exact match
  2. Policy ref 및 Fallback pins 구조·완전성 검증
  3. `candidate_ref` 재계산 일치 검증
  4. 5개 필수 승인 영역 완전성 및 internal candidate binding 검증
  5. `pack_ref` 재계산 일치 검증
  6. 외부 결정 참조 검증: 주입된 `Rag15ApprovalDecisionVerifierPort`를 통해 `(decision_ref, candidate_ref, scope, approval_status)`를 검증하고, 반환된 response의 전 필드 exact match 및 `verifier_artifact_ref` 유효성, 입력 mutation 방어를 엄격히 검증
- **승인 상태 집계(Aggregation)**:
  * 어느 하나라도 `REJECTED` -> `approval_status = "REJECTED"`
  * 그렇지 않고 하나라도 `PENDING` -> `approval_status = "PENDING"`
  * 모든 5개 영역이 `APPROVED` -> `approval_status = "APPROVED"`
- **프로덕션 소비 가능 조건 (`production_consumable`)**:
  ```text
  production_consumable = (
      integrity_verified
      AND approval_status == "APPROVED"
      AND approval_evidence_verified
  )
  ```
  이 조건 외의 모든 상황(PENDING 상태 포함)에서는 `production_consumable = false`이다.

### 6. 코드 추적 메타데이터 (`source_revision`)
- `source_revision`은 승인 팩 생성 시점의 코드 버전(Git commit SHA 등)을 기록하는 감사 메타데이터이다.
- semantic 무관 commit 변경으로 인한 hash 변경을 막고 cyclic dependency를 방지하기 위해 `candidate_ref` 및 `pack_ref`의 해시 preimage에서 명시적으로 제외된다. (실제 파서/검증기 구현체 변경은 `parser_ref`와 `validator_ref`의 소스 바이트 해시로 완벽히 추적된다.)

### 7. #180 Consumer Handoff 경계
- #179의 산출물인 `Rag15ApprovalPack`은 정적 승인 패키지(Static Approval Pack)이다.
- 요청별 가변 데이터(`MedicationIdentityRef`, `EvidenceGateOutcome`, `ApprovedGuidelineEvidenceBinding`, retrieval/assessment receipt 등)는 본 승인 팩에 포함되지 않으며, #180 런타임 오케스트레이터가 개별 가이드 생성 요청 시 동적으로 결속한다.

## 명시적 보류 및 경계 외 항목 (Explicit Deferred Scope)

1. **LangGraph 배제**: 복잡한 그래프 프레임워크를 도입하지 않고 순수 파이썬 포트/어댑터 패턴을 유지한다.
2. **DB 스키마 및 마이그레이션 배제**: `backend/app/models/rag_runtime.py`에 `guideline_set_manifest_hash` 등 신규 컬럼을 추가하지 않으며 마이그레이션을 생성하지 않는다.
3. **PUBLIC_TRACK_F 배제**: 본 승인 팩 도입은 런타임 활성화나 트랙 C/F의 공개를 의미하지 않으며, `PUBLIC_TRACK_F` 플래그는 비활성 상태를 유지한다.
4. **Issue #179 유지**: 본 PR은 승인 팩 구조와 검증 커널을 전달하는 bounded slice이며, 실제 5개 영역의 외부 권위 승인이 완료될 때까지 Issue #179는 닫지 않고(`Closes #179` 금지) OPEN 상태를 유지한다.
