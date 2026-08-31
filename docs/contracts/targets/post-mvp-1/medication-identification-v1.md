# MFDS 공식 의약품 식별 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Contract Freeze v4 target — 2026-08-27 |
| 구현·리뷰 | Not implemented · Track F 구현 동기화와 지정 리뷰어·Source·Privacy 검토 대기 |
| Source of Truth | `FinalProject Documents/04_Decision/contract-freeze-v1.md`, `track-f-rag-citation-safety-v1.md` |
| Last verified | 2026-08-27 |

## 목적과 입력 정본

Track F는 사용자가 확정한 처방약을 MFDS 공식 Source의 안정적인 제품 Identity에 연결하고, 현재 Identification을 통과한 처방만 Guide·Chat Rule-first RAG에 사용한다.

- Resolver 입력 정본은 현재 Prescription Version의 `Prescription Medication.medication_name`과 nullable `strength_text`다.
- OCR `raw_value`, rule 정규화값, LLM 초안과 HIRA 적용약가 데이터는 후보 검색 입력·정답 원장으로 사용하지 않는다.
- 사용자 확인 전 Candidate는 확정 처방 또는 공식 Identity가 아니다.

## Source·Catalog lifecycle

- MFDS 공식 제품·성분·복합제 Component·승인 Alias를 versioned Source Catalog와 immutable Source Snapshot으로 적재한다.
- `Raw Artifact → 불변 Normalization → 승인 Source Snapshot → Verification`을 checksum, importer·normalization version, 행 수와 검증 결과로 재현한다.
- 필수 schema drift, 부분 적재, 중복 Identity, checksum 불일치와 미승인 snapshot은 활성화하지 않는다.
- 비활성 Source는 신규 후보·Rule 평가에서 제외하되 과거 Identification과 Citation provenance는 보존한다.
- OCR Candidate Index와 의료 Evidence Index는 별도 version과 물리 경계를 가진다. pgvector는 OCR Candidate 보조 단계에만 사용한다.

## Candidate Resolver와 Single Candidate Gate

후보 생성 순서는 `Exact → 승인 Alias → Trigram·편집거리 → OCR 전용 pgvector`로 고정하고 공식 제품 Identity 기준으로 중복 제거한다. 내부 Top-K는 평가와 애매함 판정에만 사용한다.

Single Candidate Gate는 다음을 검증한다.

- 활성 공식 제품과 Source/Candidate Index version
- 제품명 최소 관련도
- 입력에 함량이 있으면 함량 일치
- 제형 일치 또는 계약된 안전한 동등 조건
- 1위/2위 margin과 Resolver policy version
- 함량이 없으면 제품명으로 활성 공식 제품이 정확히 하나인지

외부 결과는 다음으로 제한한다.

| 결과 | 공개 동작 |
| --- | --- |
| `SINGLE_CANDIDATE` | Gate를 통과한 후보 최대 1개와 확인에 필요한 공식 제품 정보 표시 |
| `AMBIGUOUS` | 내부 1위·Top-K·score를 숨기고 입력 수정·재검색 제공 |
| `NO_CANDIDATE` | 후보와 Identification 없이 수정·검토 제공 |
| `INGREDIENT_ONLY` | 제품 Identity로 승격하지 않고 검토 제공 |
| `INVALID_INPUT` | 후보 검색 없이 입력 수정 제공 |

잘못된 자동 `MATCHED`와 `AMBIGUOUS` 내부 후보 노출 허용치는 각각 0건이다.

## 사용자 확인·거절과 append-only Identification

- “맞아요” 요청은 `prescription_version_medication_id + candidate_search_result_id + Idempotency-Key`를 사용한다.
- Backend는 소유권, active Prescription Version, Candidate 현재성, 미소비 상태와 현재 Runtime Release Bundle 호환성을 잠금 안에서 검증한다.
- 성공 시 기존 Prescription Version을 수정하지 않고 append-only `USER_SELECTED/MATCHED` Identification과 Candidate Search `CONSUMED`를 같은 transaction에서 저장한다.
- 동일 키·동일 request hash는 최초 성공 결과를 재현하고 동일 키·상이 hash는 `409 IDEMPOTENCY_KEY_CONFLICT`다. 서로 다른 Candidate의 동시 선택은 하나만 성공한다.
- “아니에요”는 거절 event와 Search 무효화를 남긴다. 약명·함량 수정은 기존 불변 Prescription Version을 직접 변경하지 않고 [새 Prescription Version을 생성·활성화](./prescription-version-v1.md#활성화)한 뒤 새 Version Medication에 귀속된 Candidate 재검색으로 연결한다.
- 처방 Medication 수정, Source 비활성화 또는 Bundle 변경으로 현재성이 사라져도 과거 Identification을 덮어쓰지 않고 새 상태·event로 추적한다.

위 식별자·멱등 키와 확인·거절 transaction 불변 조건은 Approved v4가 고정한 최소 계약이다. 그 밖의 Candidate Search·확인·거절 route template, 성공 status, 전체 DTO 구성과 이 문서에서 고정하지 않은 오류 code는 후속 Product Decision과 구현 OpenAPI·계약 테스트에서 함께 확정한다.

## Identification Preflight

- 모든 활성 Prescription Medication이 현재 Runtime Release Bundle의 활성 공식 제품으로 `MATCHED`일 때만 RAG 활성 Guide·Chat Job을 생성한다.
- 미식별·불일치·Source 비활성·Bundle 충돌은 `job_id` 없는 동기 `REVIEW_REQUIRED`다. AI Job 안에서 사용자 입력을 기다리지 않는다.
- Job은 Prescription Version, 최소 Patient Context, Identification 목록, Runtime Release Bundle과 Execution Manifest를 snapshot한다. 재시도도 같은 snapshot을 사용한다.
- 처방·Context·Identification·Bundle·Execution Manifest·환경 revision이 바뀌면 과거 결과는 `STALE`, `is_current=false`이며 현재 답변으로 공개하지 않는다.

Runtime Release Bundle은 Source, Candidate/Knowledge Index, Rule, Guideline, Safety, Resolver, Graph, Prompt, Validator, Model과 Worker artifact version을 묶으며 환경별 Active Bundle은 최대 하나다.

`RETRY_WAIT` 중 active Bundle이 바뀐 Job을 기존 snapshot으로 계속 실행할지 즉시 `STALE`로 종결할지, 그리고 구·신 Worker가 함께 실행되는 배포에서 Worker artifact와 Job Bundle 호환성을 어떻게 검증할지는 Approved v4가 고정하지 않았다. [후속 Product Decision](../../../governance/post-mvp-1-document-authority.md#구현-전-재결정이-필요한-충돌)과 Track A 상태 전이·배포·계약 테스트가 함께 확정되기 전에는 어느 한 동작을 추정해 구현하지 않는다.

## 최소 검증

- confirmed 처방 필드만 Resolver 입력으로 사용하고 HIRA·미확정 OCR/LLM 입력 차단
- Exact/Alias/fuzzy/vector 후보 중복 제거와 Single Candidate Gate
- 함량 누락·복수 variant·명시 함량 충돌, `AMBIGUOUS` 내부 후보 비노출
- 확인/거절 멱등 transaction, 동시 선택 단일 성공과 append-only Identification
- 모든 활성 약 `MATCHED` 전 Job 미생성과 동기 `REVIEW_REQUIRED`
- 처방·Identification·Bundle 변경의 `STALE`
- 타 사용자·이전 Prescription Version Candidate·Identification·결과 공개 차단
- Source Snapshot 활성화 실패와 이전 version rollback

## 공개 게이트

`EXT-SOURCE-001`, `EXT-PRIV-001`과 관련 Safety 회귀, Source Snapshot 검증·rollback 증빙 전에는 실제 사용자 Identity와 `PUBLIC_TRACK_F`를 활성화하지 않는다. Synthetic fixture를 사용하는 접근 통제된 closed demo와 Production 공개를 구분한다.
