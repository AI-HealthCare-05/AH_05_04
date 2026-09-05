# Issue #170 의약품 Candidate Resolver 설계

## 상태와 책임

| 항목 | 값 |
| --- | --- |
| Issue | `#170` · RAG-08 Candidate Resolver |
| 문서 상태 | **Approved Target / Not implemented** 설계 snapshot |
| 구현 준비 | `READY=false` |
| 구현 담당 | 정현우 (`@ceohwj`) — Resolver·AI/RAG |
| Worker/OCR 입력 경계 책임 리뷰 | 김지혜 (`@Jye-rookie`) |
| Backend·DB·Transaction 교차 리뷰 | 송은영 (`@phina-io`) |
| Product·Safety·Evaluation 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) |
| 공개 게이트 | `PUBLIC_TRACK_F=false` |
| 저장소 증거 기준 | `b549e3c` (`origin/develop`, 2026-09-06 확인) |

이 문서는 [MFDS 공식 의약품 식별·Candidate 계약 v1](../../contracts/targets/post-mvp-1/medication-identification-v1.md)의
Resolver 구간을 구현 가능한 단위로 좁히기 위한 설계 snapshot이다. 기존 Target의 상태·DTO·오류·원자성
의미를 Current Runtime으로 승격하지 않는다. 아래 G0–G7은 모두 **미해결**이다. G0은 `BLOCKED`,
G1–G7은 `TBC (BLOCKED BY G0)`이며, 표에 적은 권고안은 책임 리뷰와 승인 artifact가 갖춰지기 전까지
선택 완료나 구현 승인이 아니다.

## 범위와 안전 경계

### 목표

- 활성 불변 Prescription Version Medication에서 서버가 읽은 사용자 확정 `medication_name`과 nullable
  `strength_text`만 Resolver 입력으로 허용한다.
- RAG-07A의 단계별 raw hit를 공식 Product Identity로 중복 제거하고, 승인된 속성·활성 상태 규칙과
  Single Candidate Gate를 적용한다.
- 내부 후보는 감사 가능하게 저장하되 공개 후보는 `READY`에서 최대 1개로 제한한다.
- Candidate Search Finalizer가 Result 저장, count, 표시·선택 flag와 최종 상태를 하나의 transaction에서
  확정하게 한다.

### 금지와 제외

- OCR `raw_value`, `normalized_value`, 검수 전 Structured Output, LLM draft, `source_ids`,
  `insurance_code_text`, HIRA 데이터와 실제 환자·처방 원문은 입력하지 않는다.
- Candidate score·rank·distance·Top-K·query digest·내부 reason은 환자 DTO나 Citation으로 노출하지 않는다.
- #166 Catalog, #168 Candidate Index persistence, #169 Prescription Version, #171 API·Identification을 이
  Issue에서 구현하거나 그 계약을 추정 보완하지 않는다.
- 외부 Source activation, Production Bundle 편입, 외부 승인 또는 `PUBLIC_TRACK_F` 해제는 범위 밖이다.
- 예시는 모두 합성·비식별 값만 사용한다.

## Dependency receipt snapshot

아래 표는 네트워크나 Issue 상태 추정이 아니라 기준 commit의 저장소 파일만 읽어 만든 snapshot이다.
`Receipt hash`는 연결된 canonical Receipt의 의미 hash이며 해당 선행 Issue의 완료 hash가 아니다.
전용 completion Receipt가 저장소에 없으면 이를 명시하고 가장 가까운 fail-closed gate Receipt만 연결한다.

| 선행 Issue | 기준 commit의 current interface | Authority 상태 | Receipt hash와 판정 |
| --- | --- | --- | --- |
| `#166` RAG-06 Catalog | Catalog runtime·export·completion Receipt가 연결되어 있지 않다. RAG-07A는 `CandidateCatalogExport`를 기대하지만 생산자는 없다. | Source Target은 Approved Target / Not implemented. Source Governance Receipt의 #166 typed slot은 `NOT_CONNECTED/null`. | Source gate `sha256:d687e75ebfbb3bc10b9887280e5c994bcc7bc0481722c660c6ce2cda8c3d402a`; `READY=false`, `BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT`. #166 completion Receipt 없음. |
| `#167` RAG-07A | `ai_worker.tasks.rag.candidate_index`의 `CandidateCatalogExport`, `CandidateIndexBuildSuccess`, `CandidateSearchQuery`, `CandidateRawHit`, `CandidateIndexSearchPort`, `build_candidate_index(...)`, `search_candidate_index(...)`. 단계는 Product Exact → Approved Alias Exact → Trigram/Edit Distance → OCR Dense Vector이며 raw stage signal을 보존한다. | PR #260 구현은 병합됐지만 `PD-167-20260904` 문서는 `Review pending`으로 남아 있고, Source/Catalog 통합 readiness는 false다. | 입력 Source gate `sha256:d687e75ebfbb3bc10b9887280e5c994bcc7bc0481722c660c6ce2cda8c3d402a`; `BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT`. #167 completion Receipt 없음. |
| `#168` RAG-07B | Candidate Index manifest/member의 PostgreSQL adapter, active pointer와 persistence interface가 기준 commit에 없다. `medication_candidate_search.candidate_index_version_id`는 nullable UUID placeholder이며 Candidate Index FK가 아니다. | Approved Target의 후속 물리 경계 / Not implemented. #171 Candidate Search 저장을 RAG-07B Candidate Index 저장으로 간주하지 않는다. | 입력 Source gate `sha256:d687e75ebfbb3bc10b9887280e5c994bcc7bc0481722c660c6ce2cda8c3d402a`; `READY=false`, `BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT`. #168 completion Receipt 없음. |
| `#169` Prescription Version | 목표 `prescription_version`, `prescription_version_medication`, `prescription.active_version_id`가 없다. 현재 #171 repository는 `medication.id`를 목표 ID placeholder로 사용한다. | Prescription Version v1은 Approved Target / Not implemented. 현재 `medication` row를 활성 불변 Medication Snapshot으로 간주할 수 없다. | OCR input Receipt `sha256:e134ad8ff700050456d5d77976336b61a795207cafbe12f385cb7c9bba2c92fe`; `COMPLETED_WITH_GAPS`, `PRESCRIPTION_VERSION_NOT_IMPLEMENTED`, `TARGET_STRENGTH_MAPPING_NOT_FROZEN`. |
| `#171` Candidate Search·Identification | Backend model/repository/service와 Deferred Constraint가 존재한다. 공개 Search 생성 route는 `503 SERVICE_UNAVAILABLE`로 닫히고, 확인·거절 route는 먼저 `Idempotency-Key`를 검증하므로 잘못된 헤더에는 `400`을 반환하며 정상 형식이면 이후 `503`으로 fail-closed한다. | Candidate 저장 slice는 구현됐지만 #169 ownership chain, #166/#168 Source·Index와 #170 Resolver가 연결되지 않았다. 공유 Target 전체는 Not implemented이며 공개 사용 불가다. | OCR input Receipt `sha256:e134ad8ff700050456d5d77976336b61a795207cafbe12f385cb7c9bba2c92fe`와 Source gate `sha256:d687e75ebfbb3bc10b9887280e5c994bcc7bc0481722c660c6ce2cda8c3d402a`; 전용 #171 completion Receipt 없음, `READY=false`. |

Canonical Receipt의 근거는 [RAG-01 OCR 확정 입력 Contract Receipt](../../validation/rag/rag-01-ocr-input-contract-receipt.md)와
[RAG Source Governance Contract Receipt](../../validation/rag/rag-source-governance-contract-receipt.md)다. 후자의
합성 계약 `COMPLETED/PASS`는 실제 Source readiness가 아니라 evaluator 계약 증빙이다.

### 보존해야 하는 현재 blockers

다음 문자열은 승인 artifact가 실제로 갱신될 때까지 이름을 바꾸거나 성공 상태로 흡수하지 않는다.

- `PRESCRIPTION_VERSION_NOT_IMPLEMENTED`
- `TARGET_STRENGTH_MAPPING_NOT_FROZEN`
- `BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT`

## G0–G7 미해결 결정 원장

`TBC`는 결정을 승인했다는 뜻이 아니라 선택 확정이 필요하다는 뜻이다. “권고 선택”은 검토를 시작할
기본안이고, “비선택 대안”은 현재 채택하지 않을 후보를 기록한 것이다. 승인 artifact 없이는 권고안을
코드·migration·DTO 기대값으로 고정하지 않는다.

| Gate | 상태 | 권고 선택 | 비선택 대안과 이유 | Owner / 필수 승인 artifact / 승인 뒤 시작 가능 Task |
| --- | --- | --- | --- | --- |
| `G0` 착수·dependency gate | `BLOCKED` | #166 Source/Catalog completion Receipt, 승인된 #167 interface/Decision artifact, #168 active Candidate Index Receipt, #169 활성 Medication Snapshot Receipt가 모두 연결된 뒤 Resolver 통합을 시작한다. 그 전에는 순수 합성 contract test 설계만 허용한다. | 현재 `medication`·nullable index ID·합성 Source Receipt나 `Review pending`인 #167 Decision으로 통합을 시작하지 않는다. 서로 다른 목표 객체를 Current로 승격하거나 미승인 interface를 고정하기 때문이다. | 정현우 + 김지혜·송은영·권가빈 / 네 선행 승인 artifact와 새 dependency snapshot / Task 1 입력 adapter·합성 fixture 착수 |
| `G1` 입력 envelope·query digest | `TBC (BLOCKED BY G0)` | 서버가 활성 `prescription_version_medication_id`로 `medication_name`, nullable `strength_text`와 활성 Bundle·Index provenance를 읽고, 원문을 노출하지 않는 versioned canonical query digest를 계산한다. | 클라이언트 약명·함량, OCR raw/normalized 값, 현재 `medication.id` placeholder, 이름 hash만을 입력 정본으로 사용하지 않는다. | 김지혜 입력 경계 + 송은영 Backend + 정현우 / Target·Decision·DTO/OpenAPI·contract test 및 OCR Receipt 재발행 / Task 1 input mapper |
| `G2` 검색 stage와 `result_method` | `TBC (BLOCKED BY G0)` | #167 raw stages를 고정 순서로 실행하고 stage signal을 보존한 뒤 Resolver가 명시적인 versioned method enum으로 결과를 기록한다. Ingredient Exact는 제품 후보가 아닌 진단 분기로만 둔다. | 자유 문자열 `result_method`, DB score를 단계 간 그대로 비교, 보험코드 exact 활성화, Ingredient를 Product로 승격하지 않는다. 현재 컬럼에 CHECK가 없으므로 코드가 enum을 추정해서는 안 된다. | 정현우 + 송은영 / Resolver Policy Decision, Target·DB constraint·contract index 갱신 / Task 2 stage adapter와 method mapping |
| `G3` 공식 Identity dedupe·fusion | `TBC (BLOCKED BY G0)` | `(code_system, canonical_code)`로 stage hit를 중복 제거하고, raw signal을 잃지 않는 versioned deterministic fusion tie-break를 사용한다. | `product_id`, 표시 이름, 입력 순서, vector score 단독, stage별 첫 hit를 identity나 자동 선택 근거로 사용하지 않는다. | 정현우 + 권가빈 / versioned Resolver Policy·합성 중복/순서 회귀 evidence / Task 3 dedupe·fusion kernel |
| `G4` 함량·제형·제품 속성 eligibility | `TBC (BLOCKED BY G0)` | nullable `strength_text`는 정상 입력으로 유지하되, 복수 variant에서는 자동 단일 후보를 만들지 않는다. 승인된 normalization/mapping으로 함량·제형·제조사·활성 상태 충돌을 fail-closed 판정한다. | 문자열 포함 비교, 숫자 강제 변환, `null` 함량을 wildcard 단일 선택, 제조사·제형 충돌 무시, OCR 원문 재조회는 채택하지 않는다. | 권가빈 Product·Safety + 김지혜 OCR + 정현우 / Strength Mapping Decision, Target·DTO/DB mapping·평가 fixture·OCR Receipt 재발행 / Task 4 attribute gate |
| `G5` Single Candidate outcome | `TBC (BLOCKED BY G0)` | eligible 공식 Product Identity가 정확히 1개일 때만 `READY`; 2개 이상은 `AMBIGUOUS`; 0개는 진단 분기에 따라 `NO_CANDIDATE`, `INGREDIENT_ONLY`, `INVALID_INPUT`. 공개 후보는 `READY`의 1개뿐이다. | top-1 score 자동 선택, threshold 미만 후보 표시, `SINGLE_CANDIDATE`를 새 공개 status로 추가, `AMBIGUOUS`에서 Top-K 공개는 채택하지 않는다. | 권가빈 + 정현우 / Resolver Policy 승인과 Single Candidate Gate evaluation Receipt / Task 5 outcome classifier |
| `G6` Finalizer·count·원자성 | `TBC (BLOCKED BY G0)` | deduped Product Result 전체 저장, eligibility/표시 flag, 다섯 count와 최종 status를 #171 Finalizer의 한 transaction에서 확정하는 handoff contract를 정의한다. Resolver 실패 때 partial Result를 성공처럼 commit하지 않는다. | Result 선저장 후 별도 `READY`, `candidate_count`에 raw hit 수 사용, displayed 수 사후 계산, 실패 partial Result 재사용은 채택하지 않는다. | 송은영 + 정현우 / Transaction Decision, Target·migration/deferred constraint·repository contract test·계약 index 갱신 / Task 6 Resolver→#171 Finalizer mapping·handoff fixture; #171 persistence 구현은 #170 범위 밖 |
| `G7` failure taxonomy·safe detail·receipt | `TBC (BLOCKED BY G0)` | 입력/Source/Index/port/policy/finalizer failure를 closed internal code로 분리하고 원문 없는 안정 detail만 남긴다. 정상적인 no-match 계열은 Search outcome으로, 인프라 장애만 `FAILED` 및 공개 `CANDIDATE_RESOLVER_UNAVAILABLE`로 투영한다. | 자유문장 오류, Provider 원문·검색어·score 노출, 모든 0건을 503, 예외를 `AMBIGUOUS`로 강등하는 방식은 채택하지 않는다. | 정현우 + 송은영 + 권가빈 / Failure Mapping Decision, Target·공개 오류/OpenAPI·Receipt schema·contract/eval evidence / Task 7 orchestration·failure mapper·completion Receipt |

## 공유 계약 변경 경로

기존 Target을 그대로 구현하는 세부 로직은 구현 PR에서 로컬 테스트와 함께 연결할 수 있다. 그러나 아래
조건에 해당하면 구현 상세로 처리하지 않고 새 Decision 또는 Contract Freeze version을 먼저 승인한다.

| Gate | 공유 계약 변경 조건 | 필수 갱신 경로 |
| --- | --- | --- |
| `G1` | 입력 필드·requiredness·query context·DTO 또는 활성 Snapshot 의미가 바뀜 | Product/OCR/Backend Decision → `docs/contracts/targets/post-mvp-1/medication-identification-v1.md`와 필요 시 `prescription-version-v1.md` → OpenAPI/DTO → contract/integration test → `docs/contracts/README.md` → OCR Receipt 재발행 |
| `G2` | stage 순서·공유 enum·`result_method` 저장 의미·보험코드/Ingredient 역할이 바뀜 | Resolver/Backend Decision → Medication Identification Target → migration/model constraint → Worker–Backend contract test → 계약 index → Resolver Policy Receipt |
| `G4` | `strength_text` 물리 mapping·정규화·충돌 reason·제품 속성 requiredness가 바뀜 | Product/Safety/OCR Decision → Prescription Version + Medication Identification Target → migration/DTO → contract/eval fixture → 계약 index → OCR Receipt 재발행 |
| `G6` | count 의미·상태 불변식·Result flag·transaction owner·deferred constraint가 바뀜 | Backend Transaction Decision → Medication Identification Target → migration/repository → PostgreSQL integration test → 계약 index → Finalizer Receipt |
| `G7` | 공개 status/error code·HTTP 의미·failure field·Receipt schema가 바뀜 | Product/Safety/Backend Decision → Medication Identification Target와 오류 계약 → OpenAPI/DTO → contract/eval test → 계약 index → completion Receipt |

문서를 `docs/contracts/current/`로 이동하는 것은 위 구현, migration, OpenAPI/DTO, 자동 테스트, evidence와
지정 리뷰 승인까지 같은 PR에 갖춰진 경우뿐이다. Target과 Current에 같은 계약을 중복 보관하지 않는다.
`G3`은 내부 policy로 머물고 공유 DTO에 signal을 추가하지 않을 때만 계약 변경이 아니다. `G5`는 기존
Target의 상태 투영을 그대로 따를 때만 계약 변경이 아니며, status·후보 수 의미가 달라지면 G7 경로를
따른다.

## Resolver data flow

```text
활성 Prescription Version Medication
  → G1 입력 allowlist·query digest
  → 활성 Source/Catalog/Candidate Index provenance 검증
  → G2 RAG-07A 단계별 raw hit
  → G3 Product Identity dedupe·fusion
  → G4 attribute eligibility
  → G5 outcome 판정
  → G6 Candidate Search Finalizer 단일 transaction
  → #171 공개 조회·사용자 확인 또는 거절
```

어느 단계든 활성 Version·Source·Catalog·Index·Bundle이 바뀌면 과거 계산을 현재 결과로 재사용하지
않는다. 환자 화면은 Finalizer가 commit한 공개 상태만 읽고 내부 단계 수나 점수로 상태를 추정하지 않는다.

## Count 정의와 outcome/failure/finalizer truth table

### Count 정의

- `raw_count`: Product 후보 검색 stage가 반환한 검증 완료 raw hit 수. 같은 공식 Product의 여러 stage
  signal도 각각 센다. Ingredient 진단 hit는 별도 진단 축이며 이 count에 합산하지 않는 안을 권고한다.
- `deduped_count`: `(code_system, canonical_code)` 기준 고유 Product 수.
- `eligible_count`: G4의 Source·활성 상태·함량·제형·제품 속성 gate를 모두 통과한 고유 Product 수.
- `persisted_count`: Finalizer transaction에서 실제 삽입된 Candidate Result row 수.
- `displayed_count`: Finalizer가 `is_displayed=true`로 고정한 감사 수. 현재 화면 카드 수가 아니며
  `selection_eligible=true`이면 반드시 displayed다.

성공적으로 최종화된 비실패 outcome의 기본 불변식 권고안은
`0 <= eligible_count <= deduped_count <= raw_count`, `persisted_count = deduped_count`,
`displayed_count in {0, 1}`이다. `RUNNING`과 `FAILED`에는 `persisted_count = deduped_count`를 적용하지
않는다. G2의 invalid raw hit는 count 전에 port failure로 닫는다. 실패 행의 raw/deduped/eligible 값은
rollback 전에 관측한 처리 진척도일 뿐 저장 성공 count가 아니며, `persisted_count=0`으로 분리한다.
이 count 정의 자체가 G6 승인 전에는 구현 계약이 아니다.

| 분기 | raw_count | deduped_count | eligible_count | persisted_count | displayed_count | Finalizer 결과·저장 의미 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `RUNNING` 접수 직후 | `0` | `0` | `0` | `0` | `0` | Search만 commit 가능. Result flag는 모두 false이며 최종화 전 상태다. |
| `READY` | `>=1` | `>=1` | `=1` | `=deduped_count` | `=1` | 유일 eligible Result만 displayed·selection eligible. Result 전체와 count·status를 한 transaction에서 commit. |
| `AMBIGUOUS` | `>=2` | `>=2` | `>=2` | `=deduped_count` | `0` | 내부 Result는 감사용으로 보존하되 공개 candidate/result ID는 null. |
| `NO_CANDIDATE` — raw 0 | `0` | `0` | `0` | `0` | `0` | 정상 outcome. |
| `NO_CANDIDATE` — 모두 부적격 | `>=1` | `>=1` | `0` | `=deduped_count` | `0` | 진단 reason이 G4/G5의 승인 mapping과 일치할 때만 정상 outcome; 내부 후보 비노출. |
| `INGREDIENT_ONLY` | `0` | `0` | `0` | `0` | `0` | 별도 ingredient diagnostic hit가 1개 이상이어도 Product count는 모두 0. reason=`PRODUCT_NAME_REQUIRED`. |
| `INVALID_INPUT` | `0` | `0` | `0` | `0` | `0` | 검색 stage 호출 0회. reason=`INVALID_INPUT`. |
| `FAILED` — 입력/Source/Index/port/policy | `0..N` | `0..N` | `0..N` | `0` | `0` | Resolver 결과 저장 transaction을 rollback하고 Search만 안전한 `FAILED` 감사 상태로 별도 finalization하는 안을 권고. partial Result 재사용 금지. |
| `FAILED` — Finalizer constraint/DB | `0..N` | `0..N` | `0..N` | `0` | `0` | Result·count·최종 status transaction 전체 rollback. 동일 transaction에서 `FAILED`를 주장하지 않고 별도 안전 경계에서 기록. |
| `INVALIDATED_INPUT_CHANGED` / `EXPIRED` | 생성 당시 값 보존 | 생성 당시 값 보존 | 생성 당시 값 보존 | 생성 당시 값 보존 | `0..1` 보존 | 새 Resolver 실행이 아니라 #171 lifecycle transition. 공개 candidate는 null. |
| `INVALIDATED_USER_REJECTED` / `CONSUMED` | 생성 당시 값 보존 | 생성 당시 값 보존 | `1` 보존 | 생성 당시 값 보존 | `1` 보존 | 과거 displayed·eligible 감사 flag를 보존하지만 공개 candidate와 재선택은 차단. |

`NO_CANDIDATE — 모두 부적격`과 `FAILED`의 경계, 실패 Search를 별도 transaction에서 기록하는 방식은
G6·G7에서 승인해야 한다. 현재 Target의 `FAILED` “내부 이력값 유지”를 위 권고안의 partial rollback으로
바꾸려면 공유 계약 변경 경로를 먼저 밟는다.

## 합성 예시

합성 입력 `medication_name="가상정"`, `strength_text="10 mg"`에 Exact와 Alias stage가 동일한
`(MFDS_ITEM, SYNTH-0001)`을 각각 반환하면 `raw_count=2`, `deduped_count=1`이다. 승인된 속성 mapping이
두 signal을 같은 활성 10 mg 제품으로 판정한 경우에만 `eligible_count=1`, `persisted_count=1`,
`displayed_count=1`, `READY`가 가능하다. 이 예시는 실제 제품·환자·처방 정보를 포함하지 않으며
평가 정답이나 Source 승인 증빙이 아니다.

동일한 합성 이름에 10 mg과 20 mg 제품이 남고 입력 함량이 null이면 두 제품 중 하나를 score로 고르지
않는다. 승인된 G4 mapping 전에는 `TARGET_STRENGTH_MAPPING_NOT_FROZEN`을 유지하고, mapping 승인 뒤에도
두 제품이 eligible이면 `AMBIGUOUS`, displayed 0으로 닫는다.

## 구현 착수·완료 조건

1. G0 선행 Receipt 세트가 실제 artifact hash와 `READY=true`를 제공한다.
2. G1–G7 각 owner가 최신 HEAD의 필수 승인 artifact를 검토하고 `TBC` 상태를 해소한다.
3. 공유 계약 변경 행은 Decision, Target, 계약 index, migration/OpenAPI/DTO와 contract/integration test를
   같은 focused PR에서 정렬한다.
4. 합성 fixture로 raw→dedupe→eligibility→outcome→finalizer의 count와 실패 rollback을 재현한다.
5. Source·Prescription·Index·Bundle currentness 변경, 내부 정보 비노출, 최대 표시 후보 1개와 잘못된
   자동 `MATCHED` 0건을 평가한다.
6. #171 공개 route는 #169 ownership chain과 #170 Resolver completion Receipt가 연결되기 전까지
   fail-closed를 유지한다.
7. 외부 승인 정본이 충족되기 전에는 `PUBLIC_TRACK_F=false`를 유지한다.

## 근거 파일

- [MFDS 공식 의약품 식별·Candidate 계약 v1](../../contracts/targets/post-mvp-1/medication-identification-v1.md)
- [Prescription Version 계약 v1](../../contracts/targets/post-mvp-1/prescription-version-v1.md)
- [RAG Source Ingestion 계약 v1](../../contracts/targets/post-mvp-1/rag-source-ingestion-v1.md)
- [RAG-07A Candidate Index 입력 경계 Decision](../../governance/decisions/2026-09-04-rag-candidate-index-input-boundary.md)
- [RAG-01 OCR 확정 입력 Contract Receipt](../../validation/rag/rag-01-ocr-input-contract-receipt.md)
- [RAG Source Governance Contract Receipt](../../validation/rag/rag-source-governance-contract-receipt.md)
- [Post-MVP-1 계약 추적표](../../testing/post-mvp-1-contract-traceability.md)
- `ai_worker/tasks/rag/candidate_index.py`
- `backend/app/models/rag_candidate.py`
- `backend/app/repositories/medication_candidate_repository.py`
- `backend/app/services/medication_identification.py`
- `backend/app/apis/v1/medication_candidate_routers.py`

## 결론

Issue #170의 목표 Resolver 경계와 후보 권고안은 기록됐지만 G0은 `BLOCKED`, G1–G7은
`TBC (BLOCKED BY G0)`로 모두 미해결이다. 특히
`PRESCRIPTION_VERSION_NOT_IMPLEMENTED`, `TARGET_STRENGTH_MAPPING_NOT_FROZEN`,
`BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT`가 해소되고 새 Receipt가 연결되기 전에는 Current Runtime,
통합 완료 또는 공개 가능 상태로 해석하지 않는다.
