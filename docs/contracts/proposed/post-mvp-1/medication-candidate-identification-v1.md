# Medication Candidate Search·Identification 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Proposed Target · Not implemented — `proposed/`에서 RAG-00 팀 승인 대기 |
| 구현 담당 | 정현우 — Candidate Resolver·Identification 연계 |
| 책임 리뷰 | 송은영 — Backend·DB·소유권·Transaction, 김지혜 — OCR 확정 입력 경계, 남한솔 — 확인 UI·공개 DTO, 권가빈 — 제품 범위·안전 문구 |
| 외부 정본 | Manifest `post-mvp-rag-evaluation-contract@2026-08-29.8`; Design `1.47` SHA-256 `798dfad94477100d8de242846a3885e6dadf83cc3024b34bcba207d1fdaae932`; DB `1.44` SHA-256 `79d1c6587fab2df1864b9a68d7d5bd23206dd3afded1ad67939cfa31905f3634` |
| 기존 선행 계약 | [MFDS 공식 의약품 식별 계약 v1](../../targets/post-mvp-1/medication-identification-v1.md), [처방 버전 계약 v1](../../targets/post-mvp-1/prescription-version-v1.md) |
| Last verified | 2026-08-31 |

## 목적과 변경 분류

사용자가 검수·확정한 Prescription Medication을 공식 MFDS 제품 Identity 후보에 연결하고, 단일 후보 확인 결과를 append-only Identification으로 저장한다. 이 문서는 기존 의약품 식별 목표를 대체하지 않고 RAG-00에서 필요한 공유 API·DTO·상태·오류 경계를 추가한다.

다음은 Backend·Frontend·OCR·RAG가 함께 사용하는 공유 계약 변경이다.

- Candidate Search 요청·결과 DTO와 상태
- Candidate 확인·거절 요청과 Identification 결과 DTO
- Prescription Version Medication 입력과 현재성
- 공개 오류 code와 `Cache-Control: no-store`

Route Template과 물리 컬럼은 구현 PR에서 OpenAPI·Migration·Contract Test와 함께 확정한다. 공유 DTO, HTTP 의미와 공개 오류 code는 아래 계약을 변경하지 않고 구현해야 한다.

## 입력 정본

- 사용자 검수 완료 값을 이관한 활성 `prescription_version_medication.medication_name`
- nullable `strength_text`
- 공유 계약과 Source Snapshot이 활성화된 경우에만 내부 nullable `insurance_code_text`
- Candidate Index·Resolver Policy·Runtime Release Bundle version

OCR `raw_value`, `normalized_value`, 검수 전 Structured Output와 `source_ids`는 직접 입력하지 않는다. 보험코드는 Grounding·서버 형식 검증과 승인 식약처 Identifier Snapshot이 모두 준비된 경우에만 내부 Exact 신호로 사용하고 제품명 Embedding·Trigram 문자열에 합치지 않는다. 사용자 화면과 공개 DTO에는 보험코드를 표시하지 않는다.

## 검색과 Single Candidate Gate

```text
선택적 보험코드 Exact
→ 제품명 Exact
→ 승인 제품 Alias Exact
→ 공식 성분명·Alias Exact 진단
→ pg_trgm·편집거리
→ OCR 전용 pgvector
→ 제품 Identity 중복 제거·RRF
→ 함량·제형·제조사·활성 상태·순위 차이 검증
```

점수·Vector Distance·RRF 순위는 후보 생성 신호일 뿐 자동 확정 근거나 의료적 확신도가 아니다. Gate를 모두 통과해 공식 제품 하나로 특정되는 경우에만 Search를 `READY`로 확정하고 후보 최대 1개를 공개한다.

Candidate Search 상태는 `RUNNING | READY | AMBIGUOUS | NO_CANDIDATE | INGREDIENT_ONLY | INVALID_INPUT | INVALIDATED_INPUT_CHANGED | INVALIDATED_USER_REJECTED | EXPIRED | FAILED | CONSUMED`로 제한한다. `READY`일 때만 표시·선택 가능한 Result가 정확히 하나다. `AMBIGUOUS | NO_CANDIDATE | INGREDIENT_ONLY | INVALID_INPUT | FAILED`에서는 표시 후보가 0개다.

| 결과 | 공개 동작 |
| --- | --- |
| `READY` | 제품명·표시 함량·제형·제조사·제품 상태와 `candidate_search_result_id`를 표시 |
| `AMBIGUOUS` | 내부 후보·Top-K·score를 숨기고 입력 수정·재검색 안내 |
| `NO_CANDIDATE` | 후보 없이 수정·검토 안내 |
| `INGREDIENT_ONLY` | 제품으로 승격하지 않고 제품명 확인 안내 |
| `INVALID_INPUT` | 검색하지 않고 입력 수정 안내 |

## 사용자 확인과 Identification

- 사용자가 화면의 단일 후보에 `맞아요`를 명시 선택해야 `USER_SELECTED/MATCHED` Identification을 저장한다.
- Backend는 소유권, 활성 Prescription Version, Search 만료·Query Digest·Bundle·Candidate Index·제품 활성 상태와 미소비 여부를 잠금 검증한다.
- `prescription_version_medication_id + candidate_search_result_id + Idempotency-Key`를 사용하며 Identification과 Search `CONSUMED`를 하나의 Transaction으로 저장한다.
- `아니에요`는 거절 Event와 Search 무효화를 남기고 약제를 `UNRESOLVED`로 유지한다.
- 확정 후 약품명·함량 수정은 OCR 필드를 되돌려 쓰지 않고 새 Prescription Version 생성·활성화 후 재검색한다.
- Candidate·Search·Identification 이력은 덮어쓰지 않는다.

## 공유 API DTO

모든 UUID는 공개 DTO에서 하이픈을 포함한 UUID 문자열로 직렬화한다. `insurance_code_text`, 내부 score·rank·distance·Query Digest와 내부 후보 목록은 공개 DTO에 포함하지 않는다.

### Candidate Search 요청

| 필드 | 타입 | 필수 | 의미 |
| --- | --- | --- | --- |
| `prescription_version_medication_id` | string(UUID) | 필수 | 현재 활성 Prescription Version의 약제 |

요청 헤더 `Idempotency-Key`는 필수다. Backend는 약제의 확정 `medication_name`, nullable `strength_text`와 내부 보험코드 신호를 서버에서 읽으며 클라이언트가 약품명·함량·보험코드를 덮어쓰게 하지 않는다.

### Candidate Search 응답

| 필드 | 타입 | 필수 | 의미 |
| --- | --- | --- | --- |
| `search_id` | string(UUID) | 필수 | Candidate Search 식별자 |
| `prescription_version_medication_id` | string(UUID) | 필수 | 검색 입력 약제 |
| `medication_index` | integer | 필수 | 처방 내 1부터 시작하는 약제 순서 |
| `status` | enum | 필수 | 이 계약의 Candidate Search 상태 |
| `status_reason` | enum \| null | 필수 | 고정 Reason code 또는 `null` |
| `candidate_count` | integer | 필수 | 내부 중복 제거 후 후보 수. 0 이상 |
| `displayed_candidate_count` | integer | 필수 | 공개 후보 수. 0 또는 1 |
| `display_limit` | integer | 필수 | P0에서는 항상 1 |
| `candidate_search_result_id` | string(UUID) \| null | 필수 | `READY`일 때만 non-null |
| `candidate` | object \| null | 필수 | 아래 표시 Snapshot. `READY`일 때만 non-null |
| `expires_at` | string(date-time) \| null | 필수 | 확인 가능 만료 시각. 아직 계산되지 않은 `RUNNING`은 null 가능 |

표시 Snapshot `candidate`는 `product_name`, nullable `strength_text`, nullable `dosage_form`, nullable `manufacturer_name`, `product_status`만 포함한다. `READY`이면 `candidate_count>=1`, `displayed_candidate_count=1`, `candidate_search_result_id`와 `candidate`가 모두 non-null이어야 한다. 그 밖의 상태에서는 `displayed_candidate_count=0`이고 두 필드는 모두 null이다.

`status_reason`의 P0 허용값은 `INVALID_INPUT | MISSING_STRENGTH_MULTIPLE_VARIANTS | ATTRIBUTE_CONFLICT | IDENTIFIER_ATTRIBUTE_CONFLICT | PRODUCT_NAME_REQUIRED`다. 상태만으로 의미가 충분하면 null을 사용하며 구현자가 자유문장이나 새로운 Reason code를 추가하지 않는다. 새 code는 공유 계약 변경으로 처리한다.

### 확인·거절 요청과 응답

| 동작 | 요청 필드 | 성공 응답 필드 |
| --- | --- | --- |
| 확인 | `prescription_version_medication_id`, `candidate_search_result_id` | `identification_id`, `prescription_version_medication_id`, `status=MATCHED`, `source=USER_SELECTED`, `product_id`, `confirmed_at` |
| 거절 | `prescription_version_medication_id`, `candidate_search_result_id` | `identification_event_id`, `prescription_version_medication_id`, `status=UNRESOLVED`, `search_status=INVALIDATED_USER_REJECTED`, `rejected_at` |

두 요청 모두 `Idempotency-Key` 헤더가 필수다. `search_id`는 `candidate_search_result_id`의 FK에서 서버가 도출하며 클라이언트가 중복 제출하지 않는다. 확인 응답의 `product_id`는 공식 제품 Identity이며 성분 배열이나 내부 Source Record 전체를 반환하지 않는다. 동일 키·동일 요청은 최초 성공 응답을 재현하고 동일 키·상이 요청은 오류로 끝낸다.

## Guide·Chat Preflight 경계

- 자동 Guide는 모든 활성 약제의 현재 `MATCHED` Identification이 있어야 Job을 생성한다.
- Chat 질문과 최소 Safety Intake Job은 Identification 완료 전에도 생성할 수 있다.
- Chat의 `ROUTINE` 분기만 Identification Preflight 통과 후 일반 Rule·Retrieval·Composer를 실행한다.
- 식별 실패 시 LLM이 제품·성분을 추측하지 않으며 일반 의료 답변을 생성하지 않는다.

## 공개 오류와 Cache

| HTTP | 공개 `code` | 저장 동작 |
| --- | --- | --- |
| `404` | `CANDIDATE_SEARCH_NOT_FOUND` | 저장 없음. 존재하지 않거나 타 사용자 Search를 구분하지 않음 |
| `404` | `PRESCRIPTION_MEDICATION_NOT_FOUND` | 저장 없음. 존재하지 않거나 타 사용자 약제를 구분하지 않음 |
| `409` | `CANDIDATE_SEARCH_STALE` | 확인·거절·Identification 신규 저장 없음. 만료·입력 변경·이미 소비된 Search 포함 |
| `409` | `IDENTIFICATION_CONTEXT_STALE` | 신규 Identification 저장 없음. Prescription Version·Source·Index·Bundle 현재성 상실 |
| `409` | `PRESCRIPTION_MEDICATION_IDENTIFICATION_INCOMPLETE` | Guide 일반 RAG Job 저장 없음. Chat 최소 Safety Intake Job은 Runtime 계약에 따름 |
| `409` | `IDEMPOTENCY_KEY_CONFLICT` | 도메인 신규 저장 없음 |
| `503` | `CANDIDATE_RESOLVER_UNAVAILABLE` | 성공 Search·Identification 저장 없음. 실패 Search 감사 상태는 같은 transaction에서 `FAILED`로 기록 가능 |

`AMBIGUOUS`, `NO_CANDIDATE`, `INGREDIENT_ONLY`, `INVALID_INPUT`은 정상적인 Search 결과 상태이며 HTTP 오류로 바꾸지 않는다. 정확한 성공 HTTP status와 Route Template은 구현 OpenAPI에서 고정하되 위 DTO·오류 의미를 변경하지 않는다.

Candidate Search·확인·거절·Identification 조회와 모든 오류 응답은 `Cache-Control: no-store`를 반환한다. 존재하지 않거나 다른 사용자의 리소스는 `404`로 통일한다.

## 최소 Contract Test

- 검수 전 OCR·LLM 값과 HIRA 입력 차단
- Exact·Alias·Trigram·OCR pgvector 후보 중복 제거
- 보험코드 미인식은 이름 경로 계속, 코드·제품 속성 충돌은 `AMBIGUOUS`
- 함량 누락·복수 Variant·명시 함량 충돌 시 임의 후보 표시 금지
- 공개 후보 최대 1개, `AMBIGUOUS` 내부 Top-K·score 노출 0건
- 사용자 확인 전 `MATCHED` 저장 0건
- 확인·거절 멱등성, 동시 선택 단일 성공과 append-only 이력
- 새 Prescription Version 생성 시 이전 Search·Identification 현재성 상실
- Candidate·Identification 성공·오류 응답의 `Cache-Control: no-store`
