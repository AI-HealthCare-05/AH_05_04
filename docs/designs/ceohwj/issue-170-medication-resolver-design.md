# Issue #170 의약품 Candidate Resolver 설계

## 상태와 결정

| 항목 | 값 |
| --- | --- |
| Issue | `#170` · RAG-08 Candidate Resolver |
| 문서 상태 | Approved implementation design · pure/local slice |
| 구현 준비 | `PURE_SLICE_READY=true`, `INTEGRATION_READY=false` |
| 구현 담당 | 정현우 (`@ceohwj`) — Resolver·AI/RAG |
| 책임 리뷰 | 권가빈 — 제품·안전·평가, 송은영 — Backend 소비 계약, 김지혜 — 확정 입력 경계 |
| 공개 게이트 | `PUBLIC_TRACK_F=false` 유지 |
| 기준 | 2026-09-09 최신 `origin/develop` |

이 설계는 [MFDS 공식 의약품 식별·Candidate 계약 v1](../../contracts/targets/post-mvp-1/medication-identification-v1.md)과
Issue #170의 병행 가능 조건을 따른다. 확정 Interface만 사용하는 Protocol, 합성 fixture, 순수 로컬 구현과
단위 테스트는 지금 구현한다. PostgreSQL adapter, Candidate Search transaction, production policy와 공개
활성화는 선행 Receipt가 준비될 때까지 구현하지 않는다.

## 범위

### 이번 PR에 포함

- Backend 소유 `CandidateIndexPort`, attribute/relevance evaluator Protocol과 불변 input/evidence/result 타입
- `Exact → Alias → Ingredient diagnostic → Trigram/Edit → optional Dense` 호출 순서
- 공식 Product Identity `(code_system, canonical_code)` 기준 dedupe
- versioned, caller-injected policy를 사용한 deterministic fusion
- 함량·제형 compatibility, 제품 활성 상태, minimum relevance, top1/top2 margin Gate
- `SINGLE_CANDIDATE | AMBIGUOUS | NO_CANDIDATE | INGREDIENT_ONLY | INVALID_INPUT`
- execution failure와 business outcome의 typed 분리
- Fake Port 기반 deterministic unit test와 import-isolation test

### 이번 PR에서 제외

- #168 PostgreSQL Candidate Index adapter와 active pointer 조회
- #169 Prescription Version Medication repository mapper
- #171 Candidate Search/Result 저장, Finalizer, 확인·거절, Identification
- production policy loader/default threshold/environment fallback
- 실제 MFDS/HIRA/환자/처방/OCR 원문 fixture
- 자동 `MATCHED`, 외부 DTO, publication gate 변경

## 모듈 경계

```text
confirmed medication snapshot + index/policy versions
                         │
                         ▼
                 ResolverInput validation
                         │
                         ▼
             CandidateIndexPort (injected Fake)
       Exact → Alias → Ingredient → Trigram → Dense
                         │
                         ▼
          evidence validation + identity dedupe
                         │
                         ▼
       deterministic fusion + attribute hard gates
                         │
                         ▼
                Single Candidate Gate
                         │
       ┌─────────────────┼──────────────────┐
       ▼                 ▼                  ▼
SINGLE_CANDIDATE   AMBIGUOUS/NO...   typed execution failure
       │
       └── candidate 1개; 그 외 outcome은 candidate 없음
```

`backend/app/services/rag/`는 `ai_worker`, SQLAlchemy, Redis, Outbox 또는 Provider SDK를 import하지 않는다.
#168 adapter는 나중에 이 Backend Protocol을 구현하고 Worker-owned 타입을 경계에서 변환한다.

## 타입 계약

### 입력

`ResolverInput`은 다음 필드만 가진다.

- `medication_name: str`
- `strength_text: str | None`
- `index_version: str`
- `policy_version: str`

문자열은 이미 trim, 공백 정리, Unicode NFC가 적용된 확정값이어야 한다. Resolver는 입력을 조용히
변환하지 않는다. `medication_name`과 nullable `strength_text`의 blank·비-NFC·길이 초과는 검색 전
`INVALID_INPUT`으로 끝난다. index/policy version은 사용자 입력이 아닌 실행 context이므로 blank,
불일치 또는 비-NFC이면 typed execution failure로 끝난다.

### 검색 Protocol

`CandidateIndexPort`는 동기 메서드로 구성한다.

- `describe(index_version)`
- `search_product_name_exact(request)`
- `search_approved_alias_exact(request)`
- `search_ingredient_exact(request)`
- `search_trigram_edit_distance(request)`
- `search_dense_vector(request)`

Descriptor는 index version과 `LEXICAL_ONLY | HYBRID` capability를 제공한다. Dense는 descriptor가
`HYBRID`이고 `ResolverPolicy.enable_dense=true`일 때만 실행한다. 나머지 경우 Dense 메서드는 호출하지 않는다.
Ingredient hit는 진단 evidence일 뿐 Product 후보, dedupe, fusion, count에 포함하지 않는다.

각 Product hit는 공식 identity, product snapshot, stage/rank와 #167 의미를 그대로 보존한 finite
`stage_score`를 포함한다. Resolver는 stage score를 `[0, 1]` relevance로 추정 변환하지 않는다.
Product snapshot의 표시 원문은 Catalog 계약대로 NFD와 원래 공백을 보존할 수 있으며, Resolver는 이를
검색용 normalized text로 오인하거나 조용히 변환하지 않는다.
함량·제형·제조사 compatibility를 #168 persistence adapter가 임의로 결정하게 하지 않는다. Resolver는
별도 동기 `CandidateAttributeMatcher` Protocol에 확정 입력과 product snapshot만 전달하고,
`MATCH | CONFLICT | NOT_APPLICABLE | UNKNOWN` 결과를 hard gate에 사용한다. production matcher는
Strength/Form Mapping Decision 이후 별도 integration slice에서 구현하며, 이번 테스트는 합성 Fake만 주입한다.
`CandidateRelevanceEvaluator`도 deduped signal을 받아 `[0, 1]` relevance를 반환하는 별도 동기
Protocol이다. 이번 slice는 inline synthetic Fake만 사용하고, #168 adapter나 raw stage score에 relevance
변환 의미를 부여하지 않는다. Production evaluator와 calibration은 Resolver Policy Decision 이후 연결한다.

### 정책

`ResolverPolicy`는 호출자가 명시적으로 주입하는 불변 값이다.

- `policy_version`, `maximum_input_length`
- `retrieval_limit`, `enable_dense`, `release_eligible`
- stage별 positive weight
- `rrf_k`, `minimum_relevance`, `minimum_margin`
- 자동 선택을 허용하는 stage 집합

정책 객체는 데이터만 보존하고 Resolver가 실행 전에 fail-closed 검증한다. version은 trim/NFC nonblank,
정수 필드는 bool이 아닌 양수, RRF와 weight는 finite positive, relevance/margin은 finite `[0, 1]`,
stage weight는 네 Product stage를 정확히 한 번씩 포함해야 한다. 자동 선택 stage는
`PRODUCT_NAME_EXACT | APPROVED_ALIAS_EXACT | TRIGRAM_EDIT_DISTANCE`의 비어 있지 않은 부분집합이며
`DENSE_VECTOR`는 포함할 수 없다. `enable_dense=false` 정책의 selectable stage에도 Dense를 허용하지
않는다. bool 필드는 정확한 bool 타입이어야 하고 이 pure slice는 `release_eligible is False`만 허용한다.
잘못된 객체는 예외를 외부로 던지지 않고 `POLICY_INVALID`이다.
이번 구현은 loader나 기본 production policy를 제공하지 않는다.
테스트는 기존 `policy.synthetic.json`을 로드하지 않고, 각 테스트가 `release_eligible=false`인 inline
synthetic policy를 명시적으로 생성한다. fixture의 `TBC`를 숫자 기본값으로 해석하지 않는다.

## 결정 로직

1. 확정 약명·함량 입력을 검증한다. 실패하면 Port를 호출하지 않고 `INVALID_INPUT`을 반환한다.
   version context 오류는 typed failure로 분리한다.
2. index descriptor를 검증한 뒤 고정 순서로 stage를 호출한다. Dense는 index capability와 policy가 모두
   활성화한 경우에만 마지막에 호출한다.
3. hit의 stage, rank, score, identity, version과 snapshot shape를 검증하고 각 stage 결과가
   `retrieval_limit`을 넘지 못하게 한다. Protocol이 선언한 domain dependency exception이나 malformed evidence는
   business outcome으로 강등하지 않고 `ResolverFailure`로 반환한다.
4. 동일 `(code_system, canonical_code)` hit를 하나로 합친다. 동일 identity의 product snapshot이 충돌하면
   index integrity failure로 닫는다.
5. fusion score는 `Σ(stage_weight / (rrf_k + rank))`로 계산한다. 같은 identity·같은 stage의 중복 hit는
   가장 낮은 rank, 그 다음 높은 finite stage score 하나만 사용한다. 정렬 tie-break는 identity key다.
6. 각 후보는 다음 hard gate를 모두 통과해야 eligible이다.
   - product status가 `ACTIVE`
   - 명시 함량의 compatibility가 `MATCH`; 함량이 null이면 `NOT_APPLICABLE`
   - 제형·제조사 compatibility가 `MATCH | NOT_APPLICABLE`; `UNKNOWN | CONFLICT`는 fail-closed
   - 후보의 최고 relevance가 `minimum_relevance` 이상
   - 자동 선택 허용 stage signal이 하나 이상 존재
7. eligible identity가 2개 이상이면 top1/top2 fusion score 차가 margin 이상이어도 자동 top-1 선택하지 않고
   `AMBIGUOUS`로 끝낸다. Margin은 score가 비슷한 후보를 감지하고 근거를 보존하는 추가 안전 gate이며,
   복수 공식 identity를 단일 identity로 축소하는 근거로 사용하지 않는다.
8. eligible identity가 정확히 1개이고, 다른 deduped 후보와의 score 차가 `minimum_margin` 이상일 때만
   `SINGLE_CANDIDATE`다. 다른 후보가 없으면 margin은 충족한 것으로 본다.
9. Product hit가 없고 Ingredient hit만 있으면 `INGREDIENT_ONLY`, 둘 다 없으면 `NO_CANDIDATE`다.
   Product hit가 있으나 모두 hard gate에서 제외되면 `NO_CANDIDATE`다.
10. Attribute matcher와 relevance evaluator는 dedupe 후 identity당 각각 정확히 한 번 호출한다. 각
    Protocol의 명시적 domain dependency exception은 `PORT_FAILURE`, 잘못된 return shape/enum/범위는
    `EVIDENCE_INVALID`이며 외부 결과에서 예외 메시지와 원문을 버린다. 예상 밖 프로그래밍 예외는
    `PORT_FAILURE`로 오분류하지 않고 전파해 내부 진단 가능성을 보존한다.

`SINGLE_CANDIDATE`만 `candidate`가 non-null이다. 내부 evidence는 모든 정상 outcome에 남긴다.
`ResolverResult.redacted()`는 outcome과 Identity 없는 표시 allowlist candidate만 가진 별도
`ResolverVisibleResult`를 반환하며 top-K, rank, score, 공식 code 필드가 구조적으로 없다. 이는 공개 API DTO 구현이나 Finalizer handoff가
아니라 외부 redaction projection 경계다. #171 Finalizer에는 별도 integration 계약으로 내부 결과 전체를
전달해야 한다.

## 실패 경계

정상 결과와 실행 실패를 union으로 분리한다.

- `ResolverResult`: outcome, nullable single candidate, internal candidates, counts
- `ResolverFailure`: `POLICY_INVALID | POLICY_VERSION_MISMATCH | INDEX_VERSION_MISMATCH | PORT_FAILURE | EVIDENCE_INVALID`

`INVALID_INPUT`은 사용자 확정 입력 shape가 잘못된 정상 business outcome이다. Repository 장애,
version mismatch, NaN/무한 score, stage/rank 위조, identity/snapshot 충돌은 `INVALID_INPUT`, `AMBIGUOUS` 또는
`NO_CANDIDATE`로 위장하지 않는다. 원문 query와 raw provider detail은 failure에 넣지 않는다.

## 검증 기준

- 입력 오류에서 Port 호출 0회
- descriptor 검증, stage 순서와 Dense capability/policy 조합
- cross-stage 및 same-stage identity dedupe의 순서 독립성
- 동일 identity snapshot 충돌 fail-closed
- Fake matcher 기반 함량 누락 복수 variant, 명시 함량 충돌, form/manufacturer conflict,
  inactive product, relevance/margin 경계
- matcher/relevance evaluator의 `UNKNOWN`, exception, malformed result와 identity당 호출 1회
- partial stage 성공 뒤 후속 Port 실패 시 partial evidence 비노출 및 원문 예외 detail 폐기
- `ResolverVisibleResult`에 top-K/rank/score 필드가 구조적으로 없음
- 기존 TBC policy fixture runtime load 0건과 inline non-release policy만 사용
- dense-only 자동 선택 차단
- Ingredient가 Product로 승격되는 사례 0건
- `SINGLE_CANDIDATE` 외 candidate 노출 0건
- Backend Resolver의 `ai_worker`, DB, Redis, Outbox import 0건

## 통합 대기 조건

다음 연결은 #168 active index read Receipt, #169 current confirmed Medication Snapshot Receipt, 승인된
Resolver production policy와 #171 Finalizer handoff가 확보된 뒤 별도 integration slice에서 수행한다.

- 실제 `rag_candidate_index_repository.py`
- production policy loader 및 Runtime Release Bundle binding
- Resolver outcome → Candidate Search lifecycle/status/result mapping
- Contract Acceptance Receipt와 HOLDOUT/SAFETY release evidence

그 전까지 `PUBLIC_TRACK_F=false`, `BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT`와 미완료 integration 상태를
유지한다. 순수 slice 완료를 전체 #170 또는 Production 완료로 표현하지 않는다.
