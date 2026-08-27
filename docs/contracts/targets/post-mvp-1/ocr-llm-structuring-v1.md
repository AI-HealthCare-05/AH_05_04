# OCR 비-RAG LLM 구조화 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Contract Freeze v4 target — 2026-08-27 |
| 구현·리뷰 | Not implemented · Track E 구현 동기화와 지정 리뷰어·Privacy 검토 대기 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-e-ocr-regression-v1.md` |
| Last verified | 2026-08-27 |

## 목적과 소유 경계

Track E는 처방전 OCR부터 비-RAG LLM 구조화 초안, 사용자 원본 대조·수정과 처방 확정까지 소유한다. 공식 의약품 후보 검색·Identity와 Guide·Chat Preflight는 처방 확정 뒤 Track F가 수행한다.

- HIRA 적용약가 데이터나 HIRA API를 품목 식별 입력·정답 원장으로 사용하지 않는다.
- 비-RAG LLM은 Retrieval, RAG, vector search 또는 외부 의료 Source 검색을 호출하지 않는다.
- LLM 초안과 미확정 OCR 값은 Prescription, Prescription Version, Guide·Chat·일정 또는 공식 Identity 입력으로 사용할 수 없다.
- Track E의 정본 출력은 사용자가 원문과 대조해 확정한 `Prescription Medication.medication_name`과 nullable `strength_text`다.

## 처리 순서와 Job 경계

고정 순서는 다음과 같다.

`CLOVA OCR → rule 정규화 snapshot → 비-RAG LLM 구조화 초안 → schema·안전 검증 → 사용자 원본 대조·수정 → 처방·Prescription Version 확정`

1. OCR 인식과 비-RAG LLM 호출은 공통 `OCR` Job 안에서 수행한다.
2. Worker는 검증된 검수 payload를 commit한 뒤 `AI_JOB=COMPLETED`로 종료할 수 있다.
3. `REVIEW_REQUIRED`는 OCR 도메인 결과의 사용자 검수 상태이며 Job 상태가 아니다.
4. 사용자 수정·확정은 별도 동기 mutation이다. 확정 transaction 전에는 처방이나 Prescription Version을 만들지 않는다.
5. 사용자 확정 뒤 Track F가 공식 Candidate Search를 시작한다. Track E 성공을 공식 Identity 성공으로 해석하지 않는다.

## 외부 LLM 입력 최소화

- 입력은 versioned allowlist의 OCR 필드와 구조화에 필요한 최소 문맥으로 제한한다.
- 원본 처방 이미지, `patient_name`, `patient_birth` 원문, 내부 `user_id`, `document_id`, `prescription_id`와 인증정보는 기본 전송하지 않는다.
- 예외 필드를 추가하려면 Privacy 승인과 allowlist version 변경, Provider payload sentinel 테스트가 필요하다.
- 전송 기록에는 allowlist version과 content 없는 digest만 남기며 의료 원문을 일반 로그에 기록하지 않는다.
- Provider 전송은 목적별 동의 상태를 확인하고 철회 뒤 신규 전송을 차단한다.

## 출력 검증과 provenance

출력은 versioned schema, prompt와 validator를 모두 통과해야 한다. 모델은 원문에 없는 약명·함량·단위·횟수·복용 시점·기간을 보완하거나 자동 확정할 수 없다.

다음 값을 서로 덮어쓰지 않고 역할과 version을 분리한다.

| 역할 | 의미 |
| --- | --- |
| `raw_value` | CLOVA OCR 원문 |
| `rule_normalized_value` | 결정적 rule 정규화값과 rule version |
| `llm_draft_value` | schema·prompt·model·validator version에 귀속된 구조화 초안 |
| `user_corrected_value` | 사용자가 원문과 대조해 수정한 값 |
| `confirmed_value` | 사용자가 최종 확정한 처방 입력 |

사용자 확정 transaction은 검수 payload revision과 소유권을 확인하고 Prescription, 불변 Prescription Version, Medication snapshot과 감사 이력을 함께 commit한다. 일부만 저장하지 않는다.

## 실패 복구

- LLM timeout·dependency 오류: 제한된 자동 재시도 뒤 원문 대조·수동 입력 또는 재시도 제공
- schema·validator 실패: 생성값 폐기 후 rule 결과·OCR 원문을 이용한 검수 화면 제공
- OCR 필수값 누락·저신뢰: 직접 수정, 재촬영·재업로드 또는 검토 필요 상태 제공
- 사용자 확정 revision 충돌: 최신 검수 payload를 다시 불러오고 명시적으로 재확정
- Provider 장애나 모델 변경: 미확정 초안을 처방으로 자동 승격하지 않음

## 최소 검증

- 외부 LLM payload allowlist와 금지 필드 sentinel
- Retrieval·RAG·외부 Source 검색 미호출
- raw/rule/LLM draft/user-corrected/confirmed provenance와 version 분리
- timeout·schema·validator·필수값 실패 시 자동 확정 차단과 수동 복구
- 사용자 확정 전 Prescription·Prescription Version·Identification 미생성
- 확정 transaction의 revision 경쟁·소유권·원자성
- Track F Resolver에 confirmed `medication_name + nullable strength_text`만 전달
- HIRA·OCR 원문·LLM 초안의 공식 Identity 입력 차단
- Worker 이관 전후 핵심 OCR 필드와 검수 흐름 비퇴행
- 로그·Stream·quarantine·DLQ의 의료 원문 비노출

## Current와의 관계

현재 runtime의 rule 정규화와 약품 행 구조화 계약은 [`../../current/ocr-medication-normalization.md`](../../current/ocr-medication-normalization.md)와 [`../../current/ocr-medication-structuring.md`](../../current/ocr-medication-structuring.md)다. 이 목표 계약은 구현·migration·OpenAPI/DTO·자동 테스트와 지정 리뷰어 승인이 함께 병합되기 전에는 Current로 승격하지 않는다.
