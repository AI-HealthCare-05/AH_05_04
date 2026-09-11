# Product Decision Candidate: Chat OTC 질문의 안정 Identity 확정 전이

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-176-20260911` |
| 상태 | **Candidate · Review Required** |
| 제안일 | 2026-09-11 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Product·Safety (OTC 범위·안전 문구) |
| 교차 리뷰 | 송은영 (`@phina-io`) — API·소유권·멱등성 / 남한솔 (`@solia142`) — 확인 UI·공개 DTO |
| 추적 Issue | [#176](https://github.com/AI-HealthCare-05/AH_05_04/issues/176) · 후속 [#177](https://github.com/AI-HealthCare-05/AH_05_04/issues/177) |
| 상위 결정 | [`PD-125-20260831`](./2026-08-31-rag-p0-contract-freeze.md) RAG P0 Contract Freeze |
| 관련 계약 | [`medication-identification-v1.md`](../../contracts/targets/post-mvp-1/medication-identification-v1.md) · [`safety-result-v1.md`](../../contracts/targets/post-mvp-1/safety-result-v1.md) · [`idempotency-v1.md`](../../contracts/targets/post-mvp-1/idempotency-v1.md) |

> 리뷰어 지정은 [#176](https://github.com/AI-HealthCare-05/AH_05_04/issues/176) 본문의 「담당과 검토」를 그대로 따랐다. `AGENTS.md`가 금지한 대로 파일 이력·문서 작성자로 추정하지 않았다.

## 1. 목적 — 저장소가 「미정」이라고 명시한 바로 그 전이

`docs/governance/post-mvp-1-document-authority.md`의 「구현 전 재결정이 필요한 충돌」이 이 항목을 다음과 같이 남겨 두었다.

> **OTC 질문의 안정 Identity:** Chat 자유 입력에서 OTC 제품·성분·함량·제형을 식별하고 애매함·사용자 확인을 거쳐 Rule 입력 Identity로 고정하는 전이가 미정이다. 이 전이가 확정되기 전에는 불충분한 입력으로 Rule 평가를 실행하지 않는다.

`safety-result-v1.md`(Approved Contract Freeze v4 target)도 같은 공백을 명시한다 — 「자유 입력에서 추가 정보와 사용자 확인을 거쳐 안정적인 OTC Identity를 확정하는 상세 전이는 아직 미정이다. 후속 Product Decision 전에는 LLM 추론만으로 OTC Identity를 확정하거나 Rule 평가로 진행하지 않는다.」

이 공백 때문에 [#176](https://github.com/AI-HealthCare-05/AH_05_04/issues/176)이 자기 규칙으로 `BLOCKED_BY_OTC_IDENTITY_DECISION`을 걸어 두었고, 그 후속인 [#177](https://github.com/AI-HealthCare-05/AH_05_04/issues/177) Rule 구현도 함께 막혀 있다. 이 문서는 그 전이를 확정하기 위한 **Candidate**다.

## 2. 범위

**포함:** Chat 자유 입력에서 OTC Identity를 확정하는 상태·전이·확인 행위, Rule 입력으로 쓸 수 있는 Identity의 조건, 확정 실패 시 fail-closed 경계, 저장·provenance 경계.

**제외:** Rule Engine·Evidence 구현([#177](https://github.com/AI-HealthCare-05/AH_05_04/issues/177)), 별도 OTC 검색·평가 endpoint와 `OTC_CHECK` Job, OTC 전용 공개 flag, 처방약–처방약 상호작용, 음식·보충제 판정. 이 문서는 코드를 바꾸지 않는다.

## 3. 현재 구현 조사

`origin/develop` `7f6cc858` 기준이다. 라인 번호 대신 심볼 이름으로 인용한다.

| 확인한 사실 | 근거 | 이 결정에 미치는 영향 |
| --- | --- | --- |
| 처방약 쪽 **단일 후보 확인 전이가 이미 승인·구현**되어 있다 | `medication-identification-v1.md` (`Approved Target · Partially implemented`), `backend/app/apis/v1/medication_candidate_routers.py`의 `confirm_medication_candidate`·`reject_medication_candidate` | §4-1에서 **새 전이를 발명하지 않는 근거**다 |
| Catalog가 **PRODUCT와 INGREDIENT를 모두** 1급 Identity로 갖는다 | `catalog/types.py` · `CandidateEntityType` = `PRODUCT`\|`INGREDIENT`, `P0_IDENTITY_CODE_SYSTEMS` = `MFDS_ITEM_SEQ`\|`MFDS_INGREDIENT_CODE` | §4-2의 Identity 축을 새로 만들 필요가 없다 |
| 제품에 **함량·제형이 이미 있다** | `CatalogProduct.strength_text`·`dosage_form`, `CatalogComponent.strength_value`·`strength_unit` | 「함량·제형 식별」을 위한 새 필드가 필요 없다 |
| Catalog에 **전문/일반 구분 필드가 없다** | `CatalogProduct`·`CatalogIngredient`·`ProductIdentity` 어디에도 분류 필드 없음. `catalog/*.py` 전체에서 ETC/OTC 검색 결과 0건 | **확정된 Identity가 OTC라는 것을 시스템이 증명할 수 없다.** §5-D2 |
| Chat 요청 DTO에 **행위 표현이 없다** | `app/dtos/chat.py` · `SendChatMessageRequest`는 `content: str` 하나뿐 | §4-3의 DTO 확장이 필요하며, 이는 `AGENTS.md`상 Decision을 요구하는 변경이다 |
| Chat 전송은 현재 **동기 `201`** 이다 | `chat_routers.py` · `send_chat_message`가 `HTTP_201_CREATED` | 목표인 `CHAT` Job 2단계 접수와 다르다. **이 결정은 목표 Job 기반을 전제하므로 [#148](https://github.com/AI-HealthCare-05/AH_05_04/issues/148) Chat `202` 전환이 구현 선행 조건이다**(§4-8·§7-3) |
| 멱등성 키는 **Job 생성 POST 전체 + Track F 확인·거절**에 필요하다 | `idempotency-v1.md` 「비동기 Job을 생성하는 모든 POST 요청과 … Track F 사용자 Candidate 확인·거절 요청은 `Idempotency-Key` 헤더를 요구한다」. `medication-identification-v1.md`의 「확인·거절에만」은 Job을 만들지 않는 **Candidate Search 생성**에 대한 규칙이다 | §4-4가 두 경로를 구분한다 |
| Preflight 판정 kernel이 이미 있다 | `rag_runtime/identification_preflight.py` · `evaluate_medication_identification_preflight` ([#173](https://github.com/AI-HealthCare-05/AH_05_04/issues/173)) | §4-7의 fail-closed 형태를 같은 방식으로 맞춘다 |
| Chat 접수는 목표 계약에서 **2단계**다 | `rag-runtime-v1.md` 「약품 식별이 불완전해도 질문과 최소 Chat Job을 먼저 저장하고 Safety Intake·Triage를 실행한다 … `ROUTINE`만 Identification Preflight를 통과한 뒤 진행한다」 | OTC 확인 전이는 이 2단계의 `ROUTINE` 분기 안에 놓인다(§4-8) |

## 4. 결정 (제안)

### 4-1. 새 전이를 만들지 않고, 승인된 Single Candidate Gate를 OTC 입력에 적용한다

`medication-identification-v1.md`가 고정한 상태축과 확인·거절 전이를 **그대로** 쓴다.

| 승인된 처방약 전이 | OTC 적용 |
| --- | --- |
| Search 상태 `RUNNING \| READY \| AMBIGUOUS \| NO_CANDIDATE \| INGREDIENT_ONLY \| INVALID_INPUT \| INVALIDATED_INPUT_CHANGED \| INVALIDATED_USER_REJECTED \| EXPIRED \| FAILED \| CONSUMED` | 동일 |
| `SINGLE_CANDIDATE ⇔ status=READY AND 표시 가능 Result 정확히 1개` | 동일 |
| 내부 Top-K·score·애매한 1위 비노출, `AMBIGUOUS`는 입력 수정·재검색 안내 | 동일 |
| 「맞아요」 명시 선택 → `USER_SELECTED/MATCHED` | 동일 |
| 「아니에요」 → 거절 Event + Search 무효화, 대상은 `UNRESOLVED` 유지 | 동일 |
| 활성 Search 최대 1개 | OTC는 **대화 맥락당** 최대 1개(§4-5) |

차이는 **대상(subject)** 하나뿐이다. 처방약은 `prescription_version_medication_id`에 묶이고, OTC는 그런 행이 없으므로 `chat_session_id`와 사용자 입력 지문에 묶인다.

`#176`이 「승인 전 `MATCHED | AMBIGUOUS | UNMATCHED`를 Current 계약으로 간주하지 않는다」고 한 부분은 이 결정으로 해소한다 — **그 세 값이 아니라 위 승인된 상태축을 쓴다.**

### 4-2. Rule 입력 Identity는 Catalog의 공식 Identity만 허용한다

확정된 OTC Identity는 `ProductIdentity`(`entity_type` + `code_system` + `canonical_code`)이며, `P0_IDENTITY_CODE_SYSTEMS`에 있는 `MFDS_ITEM_SEQ`(제품) 또는 `MFDS_INGREDIENT_CODE`(성분)만 쓴다.

- **자유문장 원문을 Rule Identity로 저장하지 않는다.** 원문은 입력 provenance로만 남는다.
- LLM·fuzzy·vector 1위를 확정으로 쓰지 않는다. 확정은 사용자 명시 선택으로만 만들어진다.
- 성분 단위 Identity를 Rule 입력으로 허용할지는 **결정 필요**(§5-D1).

### 4-3. 전송은 기존 Chat 경로만 쓴다

`#176`의 금지 사항을 그대로 따른다 — `/api/v1/otc-products`, `/api/v1/otc-evaluations`, `OTC_CHECK` Job, OTC 전용 공개 flag를 만들지 않는다. 처방약처럼 `/medication-candidates/confirm` 형태의 **새 endpoint도 만들지 않는다.**

확인·거절은 기존 `POST /api/v1/chat-sessions/{session_id}/messages` 요청 안의 **action**으로 표현한다. 지금 `SendChatMessageRequest`는 `content` 하나뿐이므로 자유문장과 확인 행위를 구분할 최소 확장이 필요하다.

`#176`이 「후보 제시·추가 정보 요청·사용자 확인 action의 정확한 DTO와 상태 전이는 **선행 Product Decision에서 확정**한다」를 요구하므로, 아래 표를 이 Decision의 승인 대상으로 제시한다. 필드명은 `medication-identification-v1.md`의 승인된 표시 Snapshot·확인·거절 표를 그대로 대응시킨 것이다. **수정이 필요하면 구현 PR이 아니라 이 Candidate에서 고친 뒤 승인한다**(§7-1).

#### 후보 제시 (assistant 응답에 포함되는 OTC block)

| 필드 | 타입 | 필수 | 의미 |
| --- | --- | --- | --- |
| `otc_search_id` | string(UUID) | 필수 | OTC Candidate Search 식별자 |
| `status` | enum | 필수 | §4-1의 승인된 Search 상태축 |
| `candidate_search_result_id` | string(UUID) \| null | 필수 | `READY`일 때만 non-null |
| `candidate` | object \| null | 필수 | 표시 Snapshot. `READY`일 때만 non-null |
| `expires_at` | string(date-time) \| null | 필수 | 확인 가능 만료 시각. `RUNNING`은 null 가능 |

표시 Snapshot `candidate`는 승인 계약과 동일하게 `product_name`, nullable `strength_text`, nullable `dosage_form`, nullable `manufacturer_name`, `product_status`만 포함한다. `candidate_count`·`displayed_candidate_count`·`display_limit`·`status_reason`·Query Digest·Top-K·score·rank·distance는 공개 DTO에 넣지 않는다.

#### 요청 (`SendChatMessageRequest` 확장)

| 필드 | 타입 | 필수 | 의미 |
| --- | --- | --- | --- |
| `content` | string | `action`이 없을 때 필수 | 자유문장. action 요청에는 넣지 않는다 |
| `action` | object \| null | 선택 | 있으면 확인·거절 행위다 |
| `action.type` | enum `OTC_CANDIDATE_CONFIRM` \| `OTC_CANDIDATE_REJECT` | `action`이 있을 때 필수 | 행위 종류 |
| `action.otc_search_id` | string(UUID) | `action`이 있을 때 필수 | 화면에 표시된 현재 Search |
| `action.candidate_search_result_id` | string(UUID) | `action`이 있을 때 필수 | 표시된 단일 후보 |

`content`와 `action`을 함께 제출하면 `400`이다. 자유문장 메시지의 기존 형태(`content`만)는 그대로 유효하므로 기존 Chat 소비자는 영향을 받지 않는다. **확인은 문장이 아니라 참조로만 이뤄진다** — action 요청의 문장을 Rule 입력으로 쓰지 않는다.

#### 성공 응답

| 동작 | 응답 필드 |
| --- | --- |
| 확인 | `otc_identification_id`, `otc_search_id`, `status=MATCHED`, `source=USER_SELECTED`, `identity`(`entity_type`·`code_system`·`canonical_code`), `confirmed_at` |
| 거절 | `otc_identification_event_id`, `otc_search_id`, `status=UNRESOLVED`, `search_status=INVALIDATED_USER_REJECTED`, `rejected_at` |

거절 사유는 서버 고정 `USER_REJECTED_DISPLAYED_CANDIDATE`이며 자유문장을 받지 않는다. Backend는 `candidate_search_result_id`가 같은 Search 소속이고 `READY`·표시 가능·선택 가능한지 검증한다(승인 계약과 동일).

#### 오류 의미

| 상황 | 응답 |
| --- | --- |
| `Idempotency-Key` 누락·빈 값 | `400 IDEMPOTENCY_KEY_REQUIRED` |
| `Idempotency-Key` 형식 오류 | `400 IDEMPOTENCY_KEY_INVALID` |
| `content`와 `action` 동시 제출 | `400` |
| 타 사용자 세션·Search·Result | `404` |
| 현재성 불일치(§4-5), 동일 키·상이 요청 | `409` |
| 스키마 검증 실패 | `422` |

이 표들은 승인 시 `#176` 구현 PR이 OpenAPI·Pydantic DTO·계약 테스트로 그대로 반영해야 하는 계약이다.

### 4-4. `Idempotency-Key`는 두 경로 모두에 필요하다

`idempotency-v1.md`는 「**비동기 Job을 생성하는 모든 POST 요청**과 Post-MVP-1 B·C 동기 상태 변경, **Track F 사용자 Candidate 확인·거절 요청**은 `Idempotency-Key` 헤더를 요구한다」로 정한다. OTC 경로에는 두 조건이 **모두** 걸린다.

| 요청 | 키 | 근거 |
| --- | --- | --- |
| 자유문장 메시지 (`CHAT` Job을 생성) | **필수** | `idempotency-v1.md`의 「Job을 생성하는 모든 POST」. scope는 비동기 접수 `(user_id, operation_id, key_hmac)` |
| 확인·거절 action | **필수** | `idempotency-v1.md`의 Track F 확인·거절 범위, `medication-identification-v1.md`와 동일 |

- 동일 key + 동일 request → 최초 성공 응답 재현
- 동일 key + 다른 request → `409`
- 누락·형식 오류 → `400 IDEMPOTENCY_KEY_REQUIRED` / `IDEMPOTENCY_KEY_INVALID`

> **정정.** 이 문서의 이전 초안은 「확인·거절에만 요구한다」고 썼다. 그 규칙은 `medication-identification-v1.md`가 **Candidate Search 생성**(Job을 만들지 않는 경로)에 대해 정한 것이며, `CHAT` Job을 만드는 메시지 POST에 일반화하면 Job 생성 POST의 키 필수 요건을 누락하게 된다.

### 4-5. 현재성과 무효화

확정된 OTC Identity는 다음에 **모두** 묶인다.

- `chat_session_id`와 그 세션의 pinned `prescription_version_id`
- 확정 시점의 Runtime Release Bundle·Candidate Index version
- 확인 대상 Search와 Result의 식별자

이 중 하나라도 바뀌면 기존 확정은 **무효**가 되고, Rule 평가 전에 다시 확인을 요구한다. 처방약 쪽 `INVALIDATED_INPUT_CHANGED`와 같은 취급이다. 타 사용자 접근은 `404`, 현재성 불일치는 `409`로 기존 오류 계약을 따른다.

세션 안에서 한 번 확정한 Identity를 이후 질문에 재사용할 수 있는지는 **결정 필요**(§5-D4).

### 4-6. fail-closed — 확정 전에는 아무것도 실행하지 않는다

확정된 Identity가 없으면 Rule·Retrieval·Composer·Provider 호출이 **0건**이어야 한다. 이는 `#173` Preflight kernel이 이미 쓰는 형태와 같다 — 예외가 아니라 **typed decision**으로 표현하고, 예외 경로가 실행을 허용하지 못하게 한다.

| 상황 | 결과 |
| --- | --- |
| 후보 0개 / 애매 / 입력 부족 | 승인된 추가 정보 요청 또는 제한 응답. Rule 미실행 |
| 사용자 거절 | `UNRESOLVED` 유지, Rule 미실행 |
| 확정 후 현재성 깨짐 | 재확인 요구, Rule 미실행 |
| 확정 실패 종결 | `IDENTIFICATION_FAILED` fallback |

「Rule 없음」을 「안전함·함께 복용 가능함」으로 표현하지 않는다(`safety-result-v1.md`의 기존 문구를 유지한다).

### 4-7. 입력 경계

자유 입력에 allowlist·정규화·최대 길이·속도 제한을 적용한다. 정규화는 Catalog가 쓰는 `normalization_version`과 같은 규칙을 쓰고 OTC 전용 정규화를 새로 만들지 않는다. 구체 수치는 **결정 필요**(§5-D5).

### 4-8. 저장과 provenance — 목표 `CHAT` Job 기반이다

**이 결정은 `rag-runtime-v1.md`의 2단계 Chat Job 접수를 기반으로 한다.** 즉 질문과 최소 `CHAT` Job을 먼저 저장하고 Safety Intake·Triage를 거친 뒤, `ROUTINE` 분기에서만 OTC Identity 확인 전이가 일어난다. `URGENT`·`EMERGENCY`·`UNKNOWN`은 승인 Safety Flow로 분기하므로 OTC 확인을 시작하지 않는다.

현재 구현은 동기 `201`이라 이 기반이 아직 아니다. 따라서 **[#148](https://github.com/AI-HealthCare-05/AH_05_04/issues/148)(OCR/Guide/Chat `202` 전환)이 `#176` 구현의 선행 조건**이며, 그전에는 존재하지 않는 Job을 전제한 구현을 시작하지 않는다.

확정 결과는 그 `CHAT` Job에 귀속해 **append-only**로 남긴다. 저장 항목은 확정된 `ProductIdentity`, 확인·거절 행위와 시각, 입력 provenance(원문이 아닌 지문), Resolver policy/version/hash, Bundle·Index version이다. 부분 저장은 허용하지 않는다(하나의 transaction).

## 5. 결정이 필요한 항목

제안자가 정하지 않는다. 값을 추정해 채우면 `AGENTS.md`가 금지한 「규칙 발명」이 된다.

### D1. 성분 단위 Identity를 Rule 입력으로 허용하는가 — **권가빈**

`#176`은 「제품 **또는** 성분 하나를 명시적으로 확인」이라고 쓰고, Catalog도 `INGREDIENT`를 1급 Identity로 갖는다. 반면 처방약 쪽 승인 계약은 `INGREDIENT_ONLY`를 「제품으로 승격하지 않고 제품명 확인 안내」로 처리한다 — 성분만으로는 확정하지 않는다는 뜻이다.

| 안 | 결과 |
| --- | --- |
| A. 제품만 허용 | 처방약 쪽과 규칙이 완전히 같다. 사용자가 제품명을 모르면 Rule에 도달하지 못한다 |
| B. 성분도 허용 | 「이부프로펜 먹어도 되나요」류가 Rule에 도달한다. 대신 함량·제형이 없는 Identity로 상호작용을 판정하게 된다 |

### D2. 확정된 Identity가 OTC임을 무엇으로 증명하는가 — **권가빈 · 김지혜(Source)**

**현재 Catalog에는 전문/일반 구분 필드가 없다.** 따라서 지금 구조로는 사용자가 확인한 제품이 전문의약품이어도 OTC 질문 경로가 그대로 진행된다. 선택지는 세 가지다.

| 안 | 결과 |
| --- | --- |
| A. MFDS Source에서 분류를 추가 수집해 Catalog 필드로 승격 | 가장 정확하나 Source·Catalog·migration 변경이 선행된다(별도 Issue) |
| B. 분류 없이 진행하되 Rule 입력에 「사용자가 OTC로 제시함」만 기록 | 구현 부담 0. 대신 전문의약품을 OTC로 오취급할 수 있다 |
| C. 분류를 확보하기 전까지 OTC Rule 실행을 차단 유지 | 안전하나 `#176`·`#177`이 계속 막힌다 |

**이 항목이 해소되지 않으면 §4를 승인해도 `#177` Rule 실행은 열 수 없다고 본다.** 제안자 의견은 A를 목표로 두고 그전까지 C다.

### D3. 함량·제형이 확정되지 않은 Identity로 Rule을 실행하는가 — **권가빈**

`CatalogProduct.strength_text`·`dosage_form`은 nullable이다. 상호작용이 함량과 무관한 경우까지 확인을 요구하면 도달률이 떨어지고, 반대로 생략을 허용하면 함량 의존 상호작용을 놓친다.

### D4. 확정된 Identity의 유효 범위 — **권가빈 · 남한솔**

한 세션 안에서 재사용하는지, 질문마다 다시 확인받는지. 재사용은 대화 흐름이 자연스럽지만, 사용자가 「아까 그 약」을 다르게 기억하는 경우 잘못된 Identity로 Rule이 돈다.

### D5. 자유 입력 최대 길이·속도 제한 수치 — **송은영 · 권가빈**

§4-7의 경계는 두되 수치는 API·남용 방지 관점의 판단이 필요하다.

## 6. 검토한 대안

| 대안 | 판단 |
| --- | --- |
| **OTC 전용 확인 endpoint 신설**(처방약 쪽 `/medication-candidates/confirm`과 대칭) | 기각. `#91` Approved v4와 `#176`이 OTC 전용 API를 명시적으로 금지한다. 기존 Chat transport로 충분하다 |
| **OTC 전용 Identity 상태축 신설**(`MATCHED\|AMBIGUOUS\|UNMATCHED`) | 기각. 승인된 Single Candidate Gate 상태축이 이미 같은 문제를 푼다. `CONTRIBUTING.md`의 「새 status·enum 추가 전 기존 모델로 표현할 수 없는 이유 확인」에 걸린다 |
| **LLM이 추출한 제품명을 Identity로 확정** | 기각. `safety-result-v1.md`가 LLM 추론만으로 확정하지 않는다고 이미 고정했고, 잘못된 상호작용 판정의 직접 원인이 된다 |
| **자유문장을 그대로 Rule 입력으로 저장** | 기각. `#176`이 금지하고, 같은 약을 가리키는 다른 문장이 서로 다른 Identity가 되어 Rule 재현이 깨진다 |
| **확인 없이 단일 후보를 자동 확정** | 기각. 「최대 1개 후보 사용자 확인/거절」이 Approved v4 Track F 흐름의 일부다 |
| **Chat 전송을 비동기 `CHAT` Job으로 함께 전환** | 이 문서에서 **결정하지 않되 선행 조건으로 연결**한다. 전환 자체는 Track A 비동기 계약([#148](https://github.com/AI-HealthCare-05/AH_05_04/issues/148)) 범위이고, 이 결정은 그 목표 기반 위에서만 구현될 수 있다(§4-8) |

## 7. 승인 범위와 선행 조건

### 7-1. 승인 범위 — §4-3의 DTO 표를 포함한다

`#176`은 「후보 제시·추가 정보 요청·사용자 확인 action의 **정확한 DTO와 상태 전이는 선행 Product Decision에서 확정**한다」를 선행조건으로 걸었다. 따라서 이 문서의 승인은 §4 전체(§4-3의 요청·응답·오류 표 포함)에 대한 승인이어야 한다.

- 필드명·requiredness·오류 의미를 고칠 필요가 있으면 **이 Candidate를 고친 뒤 승인한다.** 구현 PR로 미루지 않는다.
- 원칙(§4-1·§4-2)만 합의하고 §4-3을 확정하지 않는 선택도 가능하다. 그 경우 **`#176`의 DTO·Router·persistence 구현 차단은 별도 DTO Decision 또는 계약 승인까지 그대로 유지된다.** 원칙 합의만으로는 Frontend와 Backend가 같은 계약으로 구현할 수 있다는 `#176`의 선행조건이 충족되지 않기 때문이다.

### 7-2. 결정 항목

§5의 D1~D5가 결정되고 이 문서에 반영된다. D2가 A안으로 결정되면 MFDS Source·Catalog 분류 수집을 별도 Issue로 분리한다.

### 7-3. 실행 연결의 선행 조건

이 결정은 `rag-runtime-v1.md`의 목표 `CHAT` Job 2단계 접수를 기반으로 한다(§4-8). 현재 Chat은 동기 `201`이므로, `#176`의 구현 착수 전에 [#148](https://github.com/AI-HealthCare-05/AH_05_04/issues/148)(OCR/Guide/Chat `202` 전환)이 선행되어야 한다. 이 문서의 승인이 그 전환을 승인하는 것은 아니다.

### 7-4. 승인 후 정렬

승인 후 `#176` 구현 PR이 Decision·OpenAPI·Pydantic DTO·계약 문서의 필드·enum·requiredness를 함께 정렬한다(`AGENTS.md` 공유 계약 규칙). 같은 PR에서 `safety-result-v1.md`의 「상세 전이는 아직 미정」 문구와 `post-mvp-1-document-authority.md`의 「OTC 질문의 안정 Identity」 항목을 해소 문구로 갱신한다.

### 7-5. 승인 전

`#176`은 `BLOCKED_BY_OTC_IDENTITY_DECISION`을 유지하고 DTO·Router·persistence를 구현하지 않는다.

## 8. 관련 문서

- `docs/governance/post-mvp-1-document-authority.md` — 「구현 전 재결정이 필요한 충돌」의 OTC 항목
- [`medication-identification-v1.md`](../../contracts/targets/post-mvp-1/medication-identification-v1.md) — 재사용하는 승인된 Single Candidate Gate
- [`safety-result-v1.md`](../../contracts/targets/post-mvp-1/safety-result-v1.md) — OTC가 기존 Chat 경로를 공유한다는 고정 문구
- [`idempotency-v1.md`](../../contracts/targets/post-mvp-1/idempotency-v1.md) — 확인·거절 멱등성
- `ai_worker/tasks/rag/catalog/types.py` — `CandidateEntityType`·`P0_IDENTITY_CODE_SYSTEMS`·`CatalogProduct`
- `rag_runtime/identification_preflight.py` — fail-closed typed decision 선례 ([#173](https://github.com/AI-HealthCare-05/AH_05_04/issues/173))
