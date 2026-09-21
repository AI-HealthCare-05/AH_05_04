# Product Decision: Guide CLOSED_DEMO Provider Transmission and Medical Safety Boundary

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-927-20260921` |
| 상태 | UNDER_REVIEW / PENDING_APPROVAL (Awaiting External Approval) |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 책임 리뷰 | 송은영 (`@phina-io`) — Backend·Data Boundary / 권가빈 (`@hazelnutflavoured`) — PM·Product Acceptance·Privacy Gate |
| 필요 교차 리뷰 | 남한솔 (`@sol-nam`) — Frontend Integration / 김지혜 (`@Jye-rookie`) — Source Provenance |
| 추적 PR | [#927](https://github.com/AI-HealthCare-05/AH_05_04/pull/927) |
| 상위 결정 | [`PD-179-20260917`](./2026-09-17-rag15-guideline-approval-pack.md), [`PD-362-20260909`](./2026-09-09-source-snapshot-approval-boundary.md), [`PD-207`](../contracts/proposed/consent-gate-207.md) |
| 관련 계약 | [`guide-closed-demo-rag-v1.md`](../contracts/proposed/guide-closed-demo-rag-v1.md) |

---

## 1. 목적 및 배경 (Context & Purpose)

본 문서는 내부 폐쇄형 시연(INTERNAL CLOSED_DEMO) 목적에 한해 임시로 운영되는 Guide CLOSED_DEMO RAG 경로의 외부 Provider 전송 경계, 데이터 최소화 원칙, 의료 안전 검증 규칙, 그리고 정규 승인 절차와의 비인가 분리 경계를 정의한다.

정규 Track F 및 RAG-15 파이프라인(`docs/release-gates/post-mvp-1-external-approvals.md`, `PD-179-20260917`)은 의료/약학/개인정보 외부 정식 승인이 완결되기 전까지 프로덕션 일반 사용자 대상 공개가 엄격히 차단되어 있다(`PUBLIC_TRACK_F_ENABLED=false`).

그러나 내부 시연 및 제품 UI 확인을 위해 한시적으로 프로덕션 호스팅 환경에서 허가된 내부 테스트 사용자에 한해 RAG 기반 복약 가이드를 생성할 필요가 제기되었다. 이에 따라 외부 승인 이전의 잠재적 리스크를 통제하고 개인정보/의료안전 원칙을 준수하기 위한 엄격한 격리 경계와 전송 계약을 고정한다.

> [!WARNING]
> 본 결정은 **UNDER_REVIEW / PENDING_APPROVAL** 상태이며, 외부 권위 승인이 완료될 때까지 **AWAITING_EXTERNAL_APPROVAL** 상태로 유지된다. PR #927의 머지 상태는 `MERGE_STATUS=BLOCKED_AWAITING_EXTERNAL_APPROVAL`이다.

---

## 2. 명시적 적용 범위 및 전제 조건 (Scope & Prerequisites)

CLOSED_DEMO RAG 경로는 다음 전제 조건을 **모두 충족하는 경우에만 한시적으로 활성화**된다:

1. **글로벌 공개 플래그 비활성화**:
   - `PUBLIC_TRACK_F_ENABLED = false`
   - `GUIDE_RUNTIME_ENABLED = false`
2. **명시적 사용자 허용 목록(Allowlist)**:
   - `GUIDE_CLOSED_DEMO_RAG_USER_IDS`에 명시된 내부 시연용 `user_id`만 접근 허용
   - 비허용 사용자, 미로그인 요청, 또는 플래그 비활성화 시 일반 레거시 생성기(`GuideGenerator`) 경로 유지
3. **최대 7일 유효 기간(Strict Expiry Window)**:
   - `GUIDE_CLOSED_DEMO_RAG_STARTS_AT` ~ `GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT` (최대 7일 이내의 half-open `[start, end)` 구간)
   - 만료 즉시 자동으로 비활성화됨
4. **합성/데모 처방 데이터 한정**:
   - 승인된 17개 고정 약물 카탈로그에 부합하는 합성/데모 처방 데이터에만 적용
   - 실제 환자의 식별 가능한 처방 데이터 사용 절대 금지

---

## 3. 외부 Provider 전송 경계 (External Provider Transmission Boundaries)

### 3.1. Embedding Provider 경계 (식약처 허가사항 검색)

식약처 허가사항(MFDS) 청크 임베딩 검색을 위해 OpenAI text-embedding API를 소비한다:
- **전송 내용**: 정규화된 의약품명 및 함량 문자열 (`f"{medication_name} {strength_text}"`)
- **차단 내용**: 환자 개인식별정보(PII/PHII: `user_id`, `prescription_id`, 이름, 생년월일), 처방 일자, 의료기관/약국 정보, 의사 처방 번호, 복용법, 메모 등 처방 전반의 맥락 정보는 **절대 전송하지 않음**
- **봉인 카탈로그 한정**: 봉인된 17개 허용 약물 식별 외 임의 텍스트 임베딩 생성 금지
- **예외 인정**: 기존 Repository 정책상 Provider 전송 금지 규정에 대한 시연 목적의 명시적 한시 예외로 인정하되, 약품명/함량 단일 튜플 외 추가 맥락 전송을 원천 차단함

### 3.2. Generation Provider 경계 (구조화 가이드 생성)

OpenAI `responses.parse` API 호출 시 전송되는 페이로드는 극단적으로 최소화된다:
- **전송 제거 대상 (Stipped Fields)**:
  - `medication_name`, `strength_text` (약품명/함량)
  - `dose_value`, `dose_unit` (1회 투약량 및 단위)
  - `frequency_per_day`, `timing_text`, `duration_days` (1일 투약 횟수, 복용 시점, 총 투약 일수)
  - `source_code`, `source_version`, `locator`, `external_document_id` (식약처 문서 메타데이터)
- **전송 허용 대상 (Allowed Payload)**:
  - `source_index`: 약물 배열 인덱스 (0, 1, ...)
  - `evidence`: `[{"slot": int, "content": str}]` (식약처 공식 허가사항 본문 및 slot 번호)
- **로컬 렌더링 분리**:
  - 프론트엔드 소비용 Plaintext Envelope 렌더링(`render_closed_demo_plaintext_guide`)은 외부 Provider를 거치지 않고, 로컬의 `guide_input.medications` 원본 처방 정보를 결합하여 서버 내부에서 결정론적으로 합성함

```json
// Provider로 전송되는 최소화 페이로드 예시
{
  "medications": [
    {
      "source_index": 0,
      "evidence": [
        {
          "slot": 1,
          "content": "이 약은 고혈압 치료에 사용됩니다. 매일 일정한 시간에 투여하십시오..."
        }
      ]
    }
  ]
}
```

---

## 4. 의료 안전 검증기 강제 (Medical Safety Validator Enforcement)

생성된 모든 텍스트는 `backend/app/services/guide_ai/validators.py`의 `_validate_text()` 안전 규칙을 전수 적용받는다:

1. **4대 안전 검증 규칙 강제**:
   - `UNSAFE_MARKUP`: HTML 태그, 마크다운 링크, URL, 제어 문자, BIDI 숨김 문자 포함 시 즉시 거부
   - `RX_NUMERIC_IN_AI_TEXT` (`_contains_prescription_quantity`): 아라비아 숫자 및 한글 수량/단위("5mg", "1정", "3일" 등) 포함 시 즉시 거부
   - `RX_MEDICAL_CLAIM` (`_MEDICAL_CLAIM`): 질병 치료/예방 확언, 효능/부작용 단정, 혈압/혈당 조절 등 비인가 의료 주장 포함 시 즉시 거부
   - `RX_CHANGE_DIRECTIVE`: 처방 임의 중단/변경 지시("복용을 중단하세요", "용량을 줄이세요" 등) 포함 시 즉시 거부
2. **검증 대상**:
   - 약물별 6개 구조화 필드 전수: `medication_caution`, `food_and_drink`, `alcohol_and_smoking`, `possible_discomfort`, `seek_medical_care`, `pregnancy_and_breastfeeding`
   - 공통 안내 문구: `general_notice`
3. **무예외 원칙**:
   - 유효한 `evidence_slots`가 바인딩되어 있더라도, 생성된 환자 안내 텍스트에 처방 수치/단위가 포함되어 있으면 예외 없이 `GuideGenerationSafetyError`로 Fail-closed됨

---

## 5. 인용(Citation) 및 공개 권위 비정규 경계 (Non-canonical Citation Boundary)

CLOSED_DEMO 경로는 RAG-15 정규 승인 체계를 거치지 않는 임시 경로이다:

1. **API 반환 메타데이터**:
   - `prompt_version = "guide-closed-demo-rag-v1"`
   - `release_decision = null`
   - `release_is_current = null`
   - `fallback_code = null`
   - `citations = []` (빈 배열)
2. **비인가 인용 발행 차단**:
   - RAG-15 Formal Approval Pack (`PD-179-20260917`), Citation Authorization Authority (`#869`), PATIENT_CITATION Approval Pin (`#853`)을 거치지 않음
   - 정식 인용 식별자나 승인 영속 레코드를 생성하거나 발행하지 않음
   - Track F 정식 릴리스 게이트(`docs/release-gates/post-mvp-1-external-approvals.md`)가 통과되기 전까지 일반 사용자를 위한 정규 가이드로 승격 불가

---

## 6. 장애 격리 및 Fail-closed 운영 원칙

1. **데모 인프라 부재 시 503 SERVICE_UNAVAILABLE 즉시 반환**:
   - 데모 대상 사용자(`is_guide_closed_demo_active=True`)의 요청이나 데모 생성기/검색기(`_closed_demo_generator`, retrieval service)가 초기화되지 않았거나 주입되지 않은 경우, **DB에 Guide row를 생성하기 전에 503 SERVICE_UNAVAILABLE로 즉시 차단**한다.
   - 데모 대상자에게 레거시 생성기(`GuideGenerator`)로 Silent fail-open/fallback하는 것을 엄격히 금지한다.
2. **비대상 사용자의 레거시 격리**:
   - 데모 비활성/비대상 사용자(`is_guide_closed_demo_active=False`)는 기존의 검증된 레거시 `GuideGenerator` 파이프라인을 그대로 안전하게 경유한다.
3. **식별 불가 약물 Fail-closed**:
   - 처방전에 포함된 약물이 봉인된 17개 제품 카탈로그에 exact match(약품명 + 함량 일치)하지 않거나 식별되지 않는 경우 즉시 500 `GUIDE_GENERATION_FAILED`로 fail-closed하며, 외부 Provider 및 DB 검색 호출은 0회로 차단된다.
