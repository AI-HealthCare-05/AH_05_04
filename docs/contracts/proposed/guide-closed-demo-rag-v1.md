# Guide CLOSED_DEMO RAG v1 Contract

| 항목 | 내용 |
| --- | --- |
| 문서 상태 | Proposed / Awaiting External Approval |
| 계약 버전 | `guide-closed-demo-rag-v1` |
| 구현 위치 | `backend/app/services/guide_ai/closed_demo_generator.py`, `backend/app/services/guides.py` |
| 상위 결정 | [`PD-927-20260921`](../../governance/decisions/2026-09-21-guide-closed-demo-rag-provider-boundary.md) |
| 승인 게이트 | [`post-mvp-1-external-approvals.md`](../../release-gates/post-mvp-1-external-approvals.md) |

---

## 1. 개요 및 목적

본 계약은 승인 전 내부 폐쇄형 시연(INTERNAL CLOSED_DEMO)에 한정하여 한시적으로 동작하는 Guide CLOSED_DEMO RAG 파이프라인의 데이터 교환 규격, 외부 Provider 최소화 페이로드 구조, 의료 안전 검증 요건, 그리고 장애 차단(Fail-closed) 동작을 정의한다.

정규 Track F 및 RAG-15 정식 공개 게이트 통과 전까지 프로덕션 일반 사용자에게는 절대 노출되지 않으며, 사전에 지정된 내부 시연용 사용자 ID 및 유효 기간 내에서만 격리 실행된다.

---

## 2. CLOSED_DEMO 활성화 및 격리 게이트 (Activation Gate)

요청이 CLOSED_DEMO RAG 경로를 경유하기 위해서는 아래 조건이 모두 참(`True`)이어야 한다 (`is_guide_closed_demo_active`):

1. `GUIDE_CLOSED_DEMO_RAG_ENABLED = true`
2. `PUBLIC_TRACK_F_ENABLED = false`
3. `GUIDE_RUNTIME_ENABLED = false`
4. `user_id in GUIDE_CLOSED_DEMO_RAG_USER_IDS`
5. `GUIDE_CLOSED_DEMO_RAG_STARTS_AT <= now < GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT` (최대 7일 이내)

### 2.1. 장애 시 Fail-closed 차단 규칙 (503 SERVICE_UNAVAILABLE)
- `is_guide_closed_demo_active`가 참이나 데모 의존성(`GuideClosedDemoGenerator` 또는 `GuideClosedDemoRetrievalService`)이 초기화되지 않은 경우:
  - **DB에 Guide 레코드를 생성하기 전에 즉시 503 `SERVICE_UNAVAILABLE` 오류를 반환한다.**
  - 일반 사용자를 위한 레거시 생성기(`GuideGenerator`)로의 Silent Fallback/Fail-open을 절대 허용하지 않는다.
- 비시연 사용자(`is_guide_closed_demo_active = false`):
  - 기존 레거시 생성기(`GuideGenerator`) 경로를 그대로 정상 경유한다.

---

## 3. 외부 Provider 전송 페이로드 최소화 규격

OpenAI LLM(`responses.parse`)으로 전송되는 페이로드는 처방전 본문 및 환자 맥락을 일체 포함하지 않으며, 검색된 식약처 공식 허가 근거(evidence)와 약물 식별 인덱스(`source_index`)로만 구성된다.

### 3.1. 전송 데이터 스키마
```json
{
  "medications": [
    {
      "source_index": 0,
      "evidence": [
        {
          "slot": 1,
          "content": "식약처 공식 효능/효과, 용법/용량, 사용상의 주의사항 본문 발췌문"
        }
      ]
    }
  ]
}
```

### 3.2. 제거 및 차단 필드 (Stripped Fields)
- 처방전 원본 상세: `medication_name`, `strength_text`, `dose_value`, `dose_unit`, `frequency_per_day`, `timing_text`, `duration_days`
- 문서 식별 메타데이터: `source_code`, `source_version`, `locator`, `external_document_id`
- 환자/처방전 식별 정보: `user_id`, `prescription_id`, 복용자명, 날짜 등

---

## 4. LLM 구조화 출력 및 필드 바인딩 (Draft Schema)

LLM은 아래의 엄격한 JSON Schema(`ClosedDemoGuideDraft`)로만 응답을 반환하도록 강제된다 (`text_format=ClosedDemoGuideDraft`, `extra="forbid"`):

```python
class ClosedDemoGuidanceField(BaseModel):
    text: str | None = None
    evidence_slots: list[int] = Field(default_factory=list)

class ClosedDemoMedicationGuidance(BaseModel):
    source_index: int
    medication_caution: ClosedDemoGuidanceField
    food_and_drink: ClosedDemoGuidanceField
    alcohol_and_smoking: ClosedDemoGuidanceField
    possible_discomfort: ClosedDemoGuidanceField
    seek_medical_care: ClosedDemoGuidanceField
    pregnancy_and_breastfeeding: ClosedDemoGuidanceField

class ClosedDemoGuideDraft(BaseModel):
    medications: list[ClosedDemoMedicationGuidance]
    general_notice: str
```

### 4.1. 근거 바인딩(Slot Binding) 규칙
1. `text`가 문자열인 경우:
   - `evidence_slots`는 반드시 1개 이상의 정수를 포함해야 함
   - 포함된 모든 슬롯 번호는 해당 `source_index`에 제공된 유효 `slot` 집합의 부분집합이어야 함 (`EVIDENCE_SLOT_MISMATCH` 방지)
2. `text`가 `null`인 경우:
   - `evidence_slots`는 반드시 빈 리스트(`[]`)여야 함
3. 처방 약물 수 불일치 또는 중복 `source_index`: 즉시 `PRESCRIPTION_MISMATCH` 거부

---

## 5. 의료 안전 검증기 (Medical Safety Validation)

`ClosedDemoGuideDraft`의 6개 안내 필드 문자열 및 `general_notice`는 `backend/app/services/guide_ai/validators.py`의 `_validate_text()` 규칙을 전수 통과해야 한다:

1. **`UNSAFE_MARKUP`**: 제어 문자, BIDI 숨김 문자, HTML 태그, 마크다운 링크, URL 링크 포함 시 거부
2. **`RX_NUMERIC_IN_AI_TEXT` (`_contains_prescription_quantity`)**: 
   - 아라비아 숫자 또는 한글 수량 단위("5mg", "1정", "3회", "7일간" 등) 포함 시 거부
   - **근거 슬롯이 매핑되어 있더라도 텍스트 본문에 처방 수치/단위가 포함되면 무조건 거부**
3. **`RX_MEDICAL_CLAIM` (`_MEDICAL_CLAIM`)**: 질병 치료, 효능, 예방 확언 또는 혈압/혈당 조절 등 비인가 의료 주장 포함 시 거부
4. **`RX_CHANGE_DIRECTIVE`**: 처방 임의 중단, 감량, 증량, 복용법 변경 지시 문구 포함 시 거부

---

## 6. 프론트엔드 호환 Plaintext Envelope 렌더링

검증을 통과한 드래프트는 프론트엔드 `GuidePage.tsx` 파서가 소비할 수 있는 결정론적 Plaintext 규격으로 조립된다:
- 약품명, 함량, 용법, 용량 등 로컬 처방전 정보는 서버 로컬의 `GuideGenerationInput`에서 직접 결합한다.
- `text`가 `null`인 필드는 해당 줄 전체가 생략된다.
- 공통 안내(`general_notice`)와 안전 안내("임의로 복용을 중단하거나 변경하지 말고...")가 하단에 결속된다.

---

## 7. 인용 및 릴리스 상태 비인가 경계

- `prompt_version = "guide-closed-demo-rag-v1"`
- `release_decision = null`
- `release_is_current = null`
- `fallback_code = null`
- `citations = []` (빈 배열)

본 결과물은 RAG-15 정식 승인 팩이나 정식 Citation Authorization Authority를 거치지 않은 비정규 시연 데이터이며, 영속적인 정식 인용 레코드를 생성하지 않는다.
