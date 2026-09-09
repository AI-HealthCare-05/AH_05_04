# RAG Source 수집·활성화 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Approved Target · Partially implemented — #164·#165 / 2026-09-08 |
| 구현·리뷰 | Source·Snapshot DB, Parser·checksum, 원본 Artifact 저장, 수집 이력과 현재성 전이 구현 · Source approval·보존 정책·Catalog·Runtime 연결 대기 |
| 외부 정본 | Manifest `post-mvp-rag-evaluation-contract@2026-08-29.11`; 저장소 투영 상태는 `Approved Target · Partially implemented` |
| Normative Source | `rag-source-management-policy-v1.0.md@1.18` · SHA-256 `35842d2cbe54201ff9fb5580616055eda613fe4c16ac6d60daa7f8859d2f28e3` |
| Last verified | 2026-09-08 |

## 목적과 범위

Track F가 사용하는 공식 의약품·의료정보를 재현 가능한 불변 Source Snapshot으로 수집하고, 승인·검증된 Snapshot만 Candidate Resolver, Rule과 Evidence Retrieval에 사용한다.

이 문서는 외부 RAG 문서 세트의 Source Governance를 저장소의 Local P0 Target으로 투영한다. 외부 문서와 이 문서가 충돌하면 구현을 중단하고 Manifest에 고정된 정본과 저장소 공유 계약을 함께 재검토한다. 문서 존재만으로 기존 Current Runtime의 변경이나 Source 활성화를 증명하지 않는다.

- P0 Source는 MFDS 공식 제품·성분·복합제 Component·승인 Alias, DUR 상호작용 근거와 환자용 승인 의료정보다.
- 실제 API Service ID, Operation·Path, 필수 파라미터, 응답 Content-Type, 성공 본문 code, pagination과 안정 Primary Key는 실응답 검증으로 확정한다. 문서나 구현에서 추정값을 만들지 않는다.
- HIRA 적용약가 데이터는 제품 Identity, 검색 정답 원장 또는 상호작용 근거로 사용하지 않는다.
- 승인되지 않은 Source 자동 편입, 열린 웹 검색, ChromaDB와 Graph DB는 범위 밖이다.

### P0 Source 등록부

안정 Provenance Key는 `source_code / endpoint_code / operation_code` 세 값이다. 아래 공식 페이지 확인은 제공기관·데이터 존재 확인일 뿐 연결·Schema·License·의료·Runtime 승인을 대신하지 않는다.

| 단계 | 안정 Source / Endpoint / Operation code | 용도 | 공식 증빙 | 초기 상태 |
| --- | --- | --- | --- | --- |
| `P0_REQUIRED` | `MFDS_PRODUCT_APPROVAL / MFDS_PRODUCT_APPROVAL_API / LIST_APPROVED_PRODUCTS` | 제품·성분 Catalog, Candidate 식별, 선택 Document | [식약처 제품 허가정보](https://www.data.go.kr/data/15095677/openapi.do) | 실제 Service ID·Path·PK·Schema·Pagination 검증 전 Parser·Runtime 비활성 |
| `P0_REQUIRED` | `MFDS_DUR / MFDS_DUR_INGREDIENT_API / LIST_INGREDIENT_CONTRAINDICATIONS` | 처방약–사용자 확인 OTC Rule과 Evidence | [식약처 DUR 성분정보](https://www.data.go.kr/data/15056780/openapi.do) | 실제 관계성분 필드·Rule Fixture 검증 전 비활성 |
| `P0_REQUIRED` | `MFDS_PATIENT_MEDICATION_GUIDE / MFDS_PATIENT_GUIDE_API / LIST_PATIENT_MEDICATION_GUIDES` | 환자용 복약법·주의·부작용 Document와 Guideline 후보 | [식약처 e약은요](https://www.data.go.kr/data/15075057/openapi.do) | Coverage·Locator·운영 승인 검증 전 비활성 |
| `P1` | `MFDS_DUR / MFDS_DUR_PRODUCT_API / LIST_PRODUCT_CONTRAINDICATIONS` | 제품 단위 병용금기 보조 | [식약처 DUR 품목정보](https://www.data.go.kr/data/15059486/openapi.do) | P0 비활성, 성분 Rule과 중복·우선순위 검증 후 별도 편입 |
| `INTERNAL` | `TEAM_APPROVED_MEDICATION_ALIAS / null / null` | Candidate 검색용 팀 승인 Alias | 승인 Git Commit과 Fixture Manifest; tag·release 이름은 검토 metadata 한정 | `INTERNAL_CURATED_DATA`, 의료 Claim Citation 금지 |
| `INTERNAL` | `INTERNAL_REVIEWED_GUIDELINE / null / null` | 처방약 기반 짧은 음식 주의·활동 Card | 원문 Snapshot·Locator·팀 Review 기록 | Local 제한, Production 비활성 |

P0 세 외부 Endpoint의 실제 Service ID, Operation Path, Primary Key, Content-Type, 본문 성공 코드와 Pagination은 Endpoint별 연결 검증 Issue에서 실응답으로 Freeze한다. 한 Endpoint의 미검증 값은 그 Parser·Scheduler·Runtime 편입만 차단하며 공통 Source Client·DB 골격·합성 Fixture와 다른 Endpoint 작업까지 차단하지 않는다.

## 구현·검증 환경

- 실제 MFDS API 연결과 Source 수집 검증은 Local 환경에서만 수행한다.
- Development·Staging 서버는 구축하지 않는다. Git `develop` 브랜치는 협업 통합 브랜치이며 Development 서버를 의미하지 않는다.
- Local PostgreSQL·pgvector와 접근 통제된 Raw Artifact 저장소를 사용한다.
- API Key는 Local secret으로만 주입하고 `.env`, credential, 전체 인증 URL을 commit하지 않는다.
- 결정적 Parser·Normalization·Fixture 테스트는 CI에서 실행할 수 있지만 CI를 Development·Staging 서버로 간주하지 않는다.

## Source와 Snapshot

Source는 최소한 owner, license·재사용 조건, attribution, 목적, 상태와 승인 이력을 가진다. 단일 `ACTIVE | INACTIVE` 값으로 수집·검색·인용 허용을 함께 표현하지 않는다.

| 대상·상태축 | P0 의미 |
| --- | --- |
| Source lifecycle | `DRAFT | ACTIVE | RETIRED | REVOKED` |
| Endpoint lifecycle | 실제 요청·응답 계약이 검증된 `VERIFIED`만 Runtime 후보 |
| Endpoint runtime/acquisition | 각각 `ENABLED`, `APPROVED` 필요 |
| Operation runtime/acquisition | 각각 `ENABLED`, `APPROVED` 필요 |
| Source Use Approval | 목적·환경별 불변 Version과 append-only `SUBMITTED → EFFECTIVE → REVOKED` Decision |
| Snapshot Verification·Freshness | 최신 Verification과 승인 Freshness Policy로 `CURRENT`여야 함 |

`RETIRED`와 `REVOKED` Source, 비활성 Endpoint·Operation과 `REVOKED` Approval은 신규 수집·Candidate·Retrieval·Rule·Citation에서 제외한다. `REVOKED` Decision은 terminal이며 재승인은 검증된 새 Evidence Capture를 참조하는 새 Approval Version으로만 수행한다.

Snapshot은 다음 정보를 불변으로 보존한다.

- Source와 Source version
- 수집 시각과 importer·parser·normalization version
- Raw Artifact의 `storage_backend`, `object_key`, `byte_size`, `content_type`, 페이지별 checksum과 전체 Raw Manifest checksum
- 정규화 결과 checksum, record count와 schema version
- 승인·검증 결과와 적용·유효 시각
- 이전 Snapshot과의 변경 계보

동일 Snapshot의 내용을 직접 수정하지 않는다. Source 응답이나 정규화 규칙이 바뀌면 새 Snapshot과 새 version을 만든다. 현재 Runtime 적격성을 잃은 Source와 과거 Snapshot은 신규 Candidate·Retrieval·Rule·Citation에서 제외하지만 과거 Identification과 Citation provenance 재현을 위해 보존한다.

### Version·Checksum 규칙

| 대상 | 규칙 |
| --- | --- |
| 외부 불변 Version이 있는 Source | `external:<external_version>` |
| 외부 Version이 없는 API | `api:<RFC3339 UTC 6자리 소수초>:<canonical_checksum 64자>` |
| 내부 승인 Fixture | `internal:commit-<40 lowercase hex>-manifest-<64 lowercase hex>:<canonical_checksum 64자>` |
| `raw_checksum` | 각 원본 페이지 Byte의 무결성 검사용 SHA-256 |
| `raw_manifest_checksum` | 페이지 번호가 아니라 안정 Artifact Key로 정렬한 `(artifact key, raw_checksum, byte_size, content_type)` 목록의 Canonical SHA-256 |
| `canonical_checksum` | 모든 성공 페이지를 합친 뒤 Endpoint Primary Key로 정렬한 Canonical JSON 내용 SHA-256 |
| `canonicalization_spec_version` | Key 정렬·Unicode·숫자·null·Envelope 제외 규칙의 불변 Version. 규칙 변경 시 새 Version 사용 |

`canonical_checksum`은 Unique 제약으로 만들지 않는다. 같은 내용 재수집은 새 Snapshot을 만들지 않고 기존 Snapshot에 append-only `NO_CHANGE` Verification을 추가한다. 내용이 `A → B → A`로 원복되면 세 번째 수집은 새 시각과 새 `source_version`의 Snapshot으로 보존한다. 제공자 외부 Version이 같은데 Canonical 내용이 달라지면 `SOURCE_VERSION_CONFLICT`로 실패시키고 사람 검토 대상으로 보낸다. Pagination 번호·응답 시각처럼 내용과 무관한 Envelope 값은 Operation 계약의 명시적 제외 목록에 있을 때만 Canonical 입력에서 제외한다.

Internal `fixture_version`은 승인 Git tag를 식별자로 사용하지 않는다. tag는 같은 이름이 다른
Commit을 가리킬 수 있으므로, Source producer는 40자리 lowercase Commit SHA와 64자리
lowercase Fixture Manifest SHA-256을 함께 exact-bind한다. 승인 tag나 release 이름이 필요하면
Receipt의 별도 검토 metadata로 기록하며 `source_version` 정본에는 포함하지 않는다.

`schema_version`은 외부 응답 Envelope·필수 필드 계약이고 `parser_version`은 해당 구조를 읽는 코드·배포 Artifact 버전이다. `normalization_version`과 함께 각각 기록하며 같은 Raw Artifact 재처리도 기존 Run을 덮어쓰지 않는다.

### MFDS 제품 허가정보 canonicalization 계약

`MFDS_PRODUCT_APPROVAL / MFDS_PRODUCT_APPROVAL_API / LIST_APPROVED_PRODUCTS`의 제품 canonicalization은 `mfds-product-approval@1`을 사용한다.

- 원문 문자열의 Unicode 형태와 앞뒤 공백을 그대로 보존하며 NFC와 trim을 적용하지 않는다.
- 숫자형 문자열을 숫자로 변환하지 않고 문자열·정수·boolean·null·빈 문자열·필드 누락을 구분한다.
- 정수는 `-(2^53)+1`부터 `2^53-1`까지 허용하고 실수와 lone surrogate를 거부한다.
- 모든 중첩 객체 key는 아래 canonical 문자열 comparator로 정렬하고 객체 안의 배열 순서는 유지한다.
- 모든 성공 페이지의 제품 레코드를 합친 뒤 `ITEM_SEQ` 원문 값을 같은 canonical 문자열 comparator로 정렬한다. `ITEM_SEQ` 누락·타입 불일치·중복은 거부한다.
- Canonical 문자열 comparator는 문자열을 UTF-16 big-endian으로 인코딩한 바이트열의 사전식 비교, 즉 UTF-16 code unit 순서다. 객체 key, `ITEM_SEQ`, `raw_manifest_checksum`의 Artifact Key에 모두 같은 comparator를 적용한다. code point 순서로 정렬하면 non-BMP 문자에서 결과가 갈린다 — `U+FFFD`(0xFFFD)와 `U+10000`(surrogate pair 0xD800 0xDC00)의 경우 code point 순서는 `U+FFFD`가 앞이지만 UTF-16 code unit 순서는 0xD800 < 0xFFFD이므로 `U+10000`이 앞이다. 구현 언어의 기본 문자열 비교에 의존하지 않고 이 comparator를 명시적으로 사용해야 같은 입력에서 같은 checksum이 재현된다.
- MFDS JSON 응답에서 Parser 입력으로 포함하는 정확한 경로는 `response.body.items.item`이며, 최상위 `response` wrapper가 없는 응답에서는 `body.items.item`이다. `items`가 배열인 변형에서는 각 원소 또는 각 원소의 `item` 값만 같은 제품 레코드 목록으로 해석한다.
- Operation Envelope에서 canonical checksum 입력에 포함하는 값은 위 제품 레코드 목록뿐이다. `response.header` 전체와 확인된 pagination 필드인 `response.body.totalCount`, `response.body.pageNo`, `response.body.numOfRows`는 제외한다. 최상위 `response` wrapper가 없는 응답에도 같은 상대 경로 제외 규칙을 적용한다. `response.body`에 `items`, `totalCount`, `pageNo`, `numOfRows` 이외의 필드가 있으면 자동 제외하지 않고 schema drift로 거부한다.
- 제품 Operation의 `response.body`에서는 `items`, `pageNo`, `numOfRows`, `totalCount`를 모두 필수로 검증하고 pagination 세 필드는 boolean을 제외한 정수만 허용한다. `pageNo`는 1 이상, `totalCount`는 0 이상이어야 한다. 응답 `pageNo`는 요청한 `pageNo`와 정확히 같아야 하며 다르면 schema drift로 거부한다. `numOfRows`는 실응답 Receipt에서 값의 동일성 의미가 별도로 확정되지 않았으므로 존재와 타입만 검증하고 요청값과의 일치를 추정하지 않는다.
- `items`가 객체 wrapper인 경우 정확히 `item` 필드 하나만 허용한다. 원본 JSON 객체의 중복 key와 JSON 표준 밖의 `NaN`, `Infinity`, `-Infinity`는 마지막 값으로 덮어쓰거나 값으로 유지하지 않고 모든 깊이에서 파싱 실패로 처리한다. 거부된 key 이름과 원문 값은 오류 메시지나 일반 로그에 포함하지 않는다.
- 원본 Artifact의 크기와 SHA-256을 검증한 동일 바이트를 versioned MFDS decoder로 해석한다. 그 결과가 수집 중 기록된 page records·`totalCount`와 정확히 일치할 때만 canonical checksum을 계산한다.

`ProductIngestionResult`는 검증 완료 경계에서 다음 값을 제공한다.

| 필드 | 의미와 보장 |
| --- | --- |
| `identity` | 검증된 Source·Endpoint·Operation 식별자 |
| `endpoint_receipt_hash` | 사용한 Endpoint Receipt 내용의 SHA-256 |
| `raw_manifest_checksum` | 검증된 Raw Artifact 메타데이터 집합의 결정적 checksum |
| `canonical_checksum` | 위 경로에서 같은 원본 바이트로 해석한 전체 제품 레코드의 canonical checksum |
| `canonicalization_spec_version` | 적용한 불변 제품 canonicalization 규칙 version |
| `record_count` | 원본 바이트에서 해석하고 checksum에 포함한 전체 레코드 수 |
| `artifact_count` | 검증하고 수집 page와 일대일로 결속한 Raw Artifact 수 |

이 결과는 Receipt·Fixture·Raw Artifact 무결성, 전체 수집 적격성, page와 원본의 결속, 제품 checksum의 결정성을 보장한다. Source version 생성, schema·parser·normalization version 선택, 거부 레코드 집계, 수집 시각, DB 저장·트랜잭션, Snapshot 상태 전이·승인·활성화와 `NO_CHANGE` 판정은 보장하지 않는다.

## 수집 파이프라인

```text
Raw Artifact 수집
→ 원본 checksum·응답 메타데이터 기록
→ versioned Parser
→ 불변 Normalization
→ schema·행 수·중복·참조 무결성 검증
→ Source Snapshot 승인
→ Candidate Catalog / Rule / Knowledge Index 입력
```

### 현재 물리 상태 매핑

현재 구현은 Target의 논리 상태 일부를 다음 DB 상태와 Verification으로 표현한다.

| Target 논리 상태 | 현재 DB 표현 | 구현 경계 |
| --- | --- | --- |
| `VALIDATING` | `rag_source_snapshot.verification_status = PENDING` | 수집 무결성 검사를 통과해도 publication 승인을 의미하지 않는다. |
| `PUBLISHED` | `verification_status = CURRENT` + `snapshot-current-selection = PASSED` | 같은 Operation에서 하나만 허용한다. 거부 레코드가 있으면 `snapshot-publication-approval = PASSED`가 추가로 필요하다. |
| 이전 `PUBLISHED` | `verification_status = STALE` | 이력은 보존하며 이전 Snapshot 복원 시 다시 `CURRENT`로 전환할 수 있다. |
| 검증 실패 | `verification_status = FAILED` | 선택과 `NO_CHANGE` 재사용 대상에서 제외한다. 동일 Source version은 FAILED 이력과도 내용·계약을 비교하며, 일치할 때만 새 후보를 만들 수 있다. |

`source-ingestion-integrity = PASSED`는 Parser·checksum·Artifact 결속 검증 결과이며 사람의 publication 승인을 대신하지 않는다. `CURRENT`는 Source Snapshot 현재성만 뜻하고 Runtime Bundle 활성화를 뜻하지 않는다. Source approval, 보존 정책, Catalog 적재와 Runtime 연결은 아직 구현되지 않았다.

신규 Snapshot은 검증에 사용한 `endpoint_receipt_hash`를 불변 provenance로 저장한다. Migration 이전 Snapshot의 알 수 없는 hash는 `NULL`로 보존하고 `NO_CHANGE` 비교 대상으로 재사용하지 않는다. `NO_CHANGE`는 canonical checksum과 schema·parser·normalization·canonicalization version뿐 아니라 Endpoint Receipt hash와 거부 레코드 개수까지 모두 같고 비교 Snapshot이 `FAILED`가 아닐 때만 허용한다.

### PR #323 후속 리뷰 반영 경계

- `snapshot-publication-approval = PASSED`는 NULL 또는 공백인 `verified_by`를 허용하지 않는다. 일반 자동 무결성 검사의 nullable 승인자와는 구분한다. Verification 이력은 DB trigger로 UPDATE·DELETE를 차단하며 이력이 있으면 보호를 제거하는 downgrade도 거부한다. 기존 익명 publication 승인 데이터가 있으면 migration은 실패하며 임의 승인자 보정은 하지 않는다. 승인 주체의 존재 검사는 전체 Source Use Approval이나 권한 검증 구현을 대신하지 않는다.
- 동일 Source version의 FAILED 이력도 canonical checksum·schema/parser/normalization/canonicalization version·Endpoint Receipt hash·거부 건수 비교에 포함한다. 하나라도 다르면 `SOURCE_VERSION_CONFLICT`로 기록하고 Snapshot을 생성하지 않는다. 모두 같은 FAILED 재시도만 새 PENDING 후보를 허용한다. 유효 후보가 이미 있으면 그 후보를 우선 조회하여 중복 재시도를 `NO_CHANGE`로 처리한다.
- 현재 REJECTS는 거부 record 1개당 Artifact 1개다. `rejected_record_count`와 Artifact 개수의 일치를 파일 보존 전과 DB 저장 전에 모두 검사한다.
- #165 RAG 검토에 따라 FAILED 이력은 동일 version 충돌 비교에 포함하되 `supersedes_snapshot_id` 계보에서는 제외한다. 이전 비FAILED Snapshot이 없으면 NULL이다. 정본은 FAILED normalization에서 Snapshot을 생성하지 않으므로, 현재 FAILED Snapshot 모델과 최종 uniqueness는 #164에서 정렬한다.

- 모든 page와 필수 record가 성공한 경우에만 Snapshot 후보를 만든다.
- HTTP 성공 status라도 본문의 인증 실패·호출 한도·Provider 오류 code를 성공으로 처리하지 않는다.
- schema drift, 부분 적재, 필수값 누락, 중복 Identity, checksum 불일치와 참조 불일치는 활성화를 차단한다.
- Parser가 거부한 record는 원문 전체를 일반 로그에 남기지 않고 접근 통제된 수집 artifact에 비민감 오류 code와 함께 보존한다.
- API Key, 전체 인증 URL, Authorization header와 credential은 DB·fixture·로그·오류 응답에 저장하지 않는다.
- `max_rejected_records`와 `max_rejection_rate`는 자동 승인 허용치가 아니라 Source별 Hard Limit이다. 제한 Catalog의 초기값은 둘 다 0이다.
- 거부가 1건 이상이지만 두 Hard Limit 이하이면 `SUCCEEDED_WITH_REJECTIONS`로 기록하고 Snapshot을 `VALIDATING`에 멈춘다. 사람 승인과 `PUBLISHED` Verification 없이는 Runtime Bundle에 편입하지 않는다.
- 두 Hard Limit 중 하나라도 초과하면 `FAILED`로 끝내고 Snapshot을 만들지 않는다.
- Schema Drift는 `FAILED`로 끝내며 Snapshot을 만들지 않는다.
- 거부 레코드는 `artifact_kind=REJECTS`의 접근 통제 Object로 보존하고 안전한 고정 `reject_code`와 Parser 위치만 함께 기록한다.

### Endpoint 연결·승인 단계

```text
KEY_ACQUIRED
→ CONNECTIVITY_VERIFIED
→ SCHEMA_VERIFIED
→ LICENSE_VERIFIED
→ INGESTION_TESTED
→ SOURCE_RUNTIME_APPROVED
```

| 단계 | 완료 증빙 | 미완료 시 차단 |
| --- | --- | --- |
| `KEY_ACQUIRED` | Local Secret 주입 확인, 원문 비노출 | 실제 호출 금지 |
| `CONNECTIVITY_VERIFIED` | 성공·인증 오류·호출 한도·빈 결과 Smoke | Credential·Endpoint 승인 금지 |
| `SCHEMA_VERIFIED` | Request·Response·Pagination·본문 성공 코드·필수 ID 계약과 Fixture | Snapshot 생성 금지 |
| `LICENSE_VERIFIED` | 공식 이용조건 URL·확인일·내부 승인 기록 | `license_status=APPROVED` 금지 |
| `INGESTION_TESTED` | 전 페이지 완전성·Checksum·`NO_CHANGE`·거부·Schema Drift Test | Publication 금지 |
| `SOURCE_RUNTIME_APPROVED` | Endpoint·Operation, 목적별 Approval, Verification, Scope·Freshness와 Bundle 평가 통과 | Bundle 편입·활성화 금지 |

### Source Client 보안·오류 계약

- HTTPS만 허용하고 Source별 승인 Host Allowlist를 사용한다.
- DNS 해석 결과의 Loopback·Private·Link-local·Metadata IP를 차단한다.
- Redirect 매 Hop마다 Scheme·Host·해석 IP를 다시 검증한다.
- 연결·읽기·전체 실행 Timeout, 최대 Redirect 수, 응답 Body·압축 해제 크기와 최대 Page 수를 제한한다.
- Endpoint 계약에 고정된 Content-Type만 허용한다. XML Parser는 DTD·외부 Entity·네트워크 Entity 해석을 비활성화한다.
- 같은 `source_id`의 동시 Acquisition은 하나만 허용한다. Transaction-scoped PostgreSQL advisory lock 또는 동등한 단일 실행 제약을 사용한다.
- HTTP 200만으로 성공 처리하지 않고 versioned Response Contract의 본문 `resultCode`·성공 Envelope를 검증한다.
- 인증·키 오류는 `NOT_RETRYABLE`, 일일 한도 초과는 `RETRY_AT_RESET`, 일시적 Provider 장애는 제한된 `BACKOFF`로 분류한다. Retry Budget 소진 시 `FAILED`로 종료하고 Snapshot을 생성하지 않는다.
- 전체 성공 결과가 비어 있으면 Source별 `empty_result_policy`를 적용하며 초기 기본값은 `REJECT`다. 빈 결과를 정상 빈 Snapshot으로 자동 활성화하지 않는다.

## 정규화와 파생 데이터

- 원문 Artifact는 보존하고 정규화 결과를 원문에 덮어쓰지 않는다.
- 제품·성분·Alias·복합제 Component는 안정적인 공식 Identity와 Source provenance를 유지한다.
- DUR 행을 `interaction_rule`로 변환할 때 원 Source 행과 `rule_evidence`를 역추적할 수 있어야 한다.
- 의료 산문은 versioned document·chunk로 변환하며 chunk가 Source Snapshot과 locator를 잃지 않도록 한다.
- OCR Candidate Index와 의료 Evidence Index는 PostgreSQL 안에서 별도 version과 물리 경계를 사용한다. OCR Candidate용 pgvector 결과를 의료 근거로 인용하지 않는다.

## 활성화와 Rollback

- 승인·검증된 Snapshot과 그 Snapshot으로 재현된 Catalog·Rule·Knowledge Index만 Runtime Release Bundle에 포함할 수 있다. `source_code / endpoint_code / operation_code`를 안정 Provenance Key로 사용하고 수집별 UUID나 Object Key를 의미 식별자로 사용하지 않는다.
- Source Use Approval은 `PRODUCT_IDENTIFICATION | SAFETY_ROUTING | RULE_DERIVATION | RETRIEVAL | PATIENT_CITATION` 목적과 환경별 불변 Version으로 분리한다. 한 목적의 승인이 다른 목적을 대신하지 않으며 Citation에는 별도 `PATIENT_CITATION` 승인이 필요하다.
- 신규 Snapshot 적재가 완료되어도 평가·승인된 새 Runtime Release Bundle이 활성화되기 전에는 기존 활성 Bundle을 바꾸지 않는다.
- Source 비활성화 또는 만료 뒤 신규 Candidate·Retrieval·Rule 평가는 fail-closed로 차단한다.
- 과거 결과는 당시 Source version과 locator를 유지하지만 현재 답변으로 재사용하지 않는다.
- Local Runtime의 검증 실패 또는 회수 시 이전 승인 Bundle로 원자적으로 rollback할 수 있어야 한다.

### Runtime 신규 사용 허용 조건

다음 조건을 모두 만족할 때만 신규 Candidate·Rule·Retrieval·Citation Selection에 포함한다.

```text
runtime_environment.status = ACTIVE
AND request.bundle_id = runtime_environment.active_bundle_id
AND source.lifecycle_status = ACTIVE
AND endpoint.lifecycle_status = VERIFIED
AND endpoint.runtime_status = ENABLED
AND endpoint.acquisition_status = APPROVED
AND operation.runtime_status = ENABLED
AND operation.acquisition_status = APPROVED
AND bundle이 고정한 purpose·environment Approval의 최신 Decision = EFFECTIVE
AND license_status = APPROVED
AND clinical_status IN (APPROVED, LIMITED)
AND allowed_environment·allowed_scope 조건 충족
AND Snapshot Freshness = CURRENT
AND Source·Endpoint·Operation·Snapshot이 Bundle Member와 exact-match
AND 적용되는 미해결 Revocation Intent 없음
```

`clinical_status=LIMITED`는 승인된 Local·질문 Scope에서만 허용하고 Production에는 사용하지 않는다. Source lifecycle `ACTIVE`만으로 Runtime 적격성을 추론하지 않는다. Runtime Guard는 Bundle 전체 Target을 검사하고 실제 Operation이 사용할 Source·Member Selection을 그 PASS 집합의 부분집합으로 고정한다.

Runtime Release Bundle의 상세 구성과 현재성 검사는 [RAG Runtime 계약](./rag-runtime-v1.md)을 따른다.

## 개인정보와 보존

- Source 수집 artifact는 환자·사용자 데이터와 분리한다.
- 합성·비식별 fixture만 저장소에 commit한다.
- 의료 원문 전체를 Stream, 일반 애플리케이션 로그, quarantine 또는 DLQ에 복제하지 않는다.
- 저장 위치·보존기간·접근 권한은 Source license와 Privacy 승인 중 더 엄격한 조건을 따른다.

## 최소 검증

- 정상·인증 실패·호출 한도·빈 결과·pagination·schema drift fixture
- HTTPS·Host Allowlist·Redirect Hop·Private/Metadata IP·Timeout·응답/압축 크기·Page 제한과 XML DTD/XXE 차단
- 동일 Source 동시 Acquisition 단일 실행, 인증·한도·일시 오류 재시도 분류와 Retry Budget 소진 실패
- 같은 원본·같은 parser/normalization/canonicalization version의 결정적 checksum과 record count. 동일 Raw Artifact 집합의 열거 순서만 바뀌면 `raw_manifest_checksum`이 같고, Provider의 record/page 재배치로 Raw Byte가 달라져도 Canonical 내용이 같으면 동일 `canonical_checksum`의 `NO_CHANGE`
- 부분 page·부분 record 실패 시 Snapshot 비활성
- 제품·성분·Alias·Component 참조 무결성과 중복 Identity 차단
- DUR Source 행 → Rule → Evidence 역추적
- Candidate Index와 Knowledge Index의 version·물리 경계
- Source·Endpoint·Operation·목적별 Approval·Freshness 중 하나라도 부적격일 때 신규 사용 차단과 과거 Citation provenance 보존
- `RETRIEVAL` 승인만 있는 Source의 환자 Citation 차단과 `PATIENT_CITATION` 별도 승인
- credential·전체 인증 URL·실제 환자정보의 fixture·로그·오류 응답 미포함
- 이전 승인 Bundle rollback 재현

## 공개 게이트

`EXT-SOURCE-001`, `EXT-SOURCE-002`, `EXT-PRIV-001`과 필요한 의료·약학 검토가 완료되기 전에는 실제 사용자 Source를 활성화하거나 `PUBLIC_TRACK_F`를 켜지 않는다. Development·Staging 서버는 만들지 않으며, 승인 전에는 합성 fixture를 사용하는 접근 통제된 Local demo만 허용한다.


## #165 검토 결과와 후속 인계 (2026-09-08)

- [PM 결정](https://github.com/AI-HealthCare-05/AH_05_04/issues/165#issuecomment-5578317298): Artifact·REJECTS 보존·삭제 정책은 #335로 분리한다. 자동 삭제·실제 Source Runtime은 DISABLED로 유지하며, 코드 배포·합성 개발/테스트는 가능하다. 비활성 유지와 후속 이슈 연결 조건으로 해당 정책 미확정 자체는 #323 병합 차단이 아니다. 기술 리뷰와 기타 병합 조건은 별도다.
- [DB 인계](https://github.com/AI-HealthCare-05/AH_05_04/issues/165#issuecomment-5578311828): #319 이후 별도 #164 하위 PR에서 Snapshot uniqueness, normalization 저장 구조·FK, ingestion/normalization/Snapshot 관계와 충돌·재시도 호환성을 정렬한다. 실제 컬럼·FK·migration head 인계 전 공유 DB 구조를 추정 변경하지 않는다.
- [RAG 검토](https://github.com/AI-HealthCare-05/AH_05_04/issues/165#issuecomment-5578372722): 현재 operation_id/source_version의 비FAILED partial unique와 정본 source_id/source_version은 다르다. 세 충돌·재시도 시나리오의 현재 호환성 확인은 최종 schema 승인이 아니다.
- normalization run은 #164가 저장 구조·FK, #165가 실행 생성·완료·재실행 정책, #166이 확정된 (source_snapshot_id, normalization_run_id)를 소비하는 책임으로 나눈다. normalization_version이나 ingestion_run_id로 실행 ID를 대신하지 않는다. 물리 구조·실행 인터페이스 상세는 인계 대기다.
- reject_code 형식 검사는 승인 allowlist가 아니다. 별도 versioned 정본과 변경 절차가 필요하며 구체 목록·버전은 미확정이다. #335의 보존 정책 분리가 이 코드 계약의 승인을 의미하지 않는다.

## Snapshot 상태의 DB-owned 경계 (#323 추가 리뷰)

구현·재검토 대상 Decision: `docs/governance/decisions/2026-09-08-source-snapshot-db-transition.md`.

Revision `165e8f706152`는 비소유자 Runtime 역할의 Snapshot 상태·verified_at·effective_at 직접 변경을 trigger로 거부한다. INSERT는 PENDING·두 timestamp NULL만 허용한다. Runtime은 테이블/함수 소유자·superuser가 아니며 migration owner 역할을 상속하거나 전환할 권한이 없어야 한다. 기존 Runtime DML 권한이 있어도 trigger 검증을 우회하지 못한다.

`transition_rag_source_snapshot(snapshot_id, expected_status, next_status, verified_at, effective_at, selected_by)`는 migration owner의 SECURITY DEFINER 함수다. 함수 search_path는 migration schema와 pg_catalog로 고정하고 pg_temp는 마지막에 둔다. PUBLIC의 함수 실행 권한은 회수하고 migration 시점에 Snapshot UPDATE 권한이 있는 역할에만 EXECUTE를 부여한다. `configure-app-role.sql`은 함수가 이미 존재하면 신규 Runtime 역할에도 해당 함수의 EXECUTE만 인계한다. 역할을 먼저 만들면 migration이, migration 이후에 만들면 역할 프로비저닝이 인계한다. caller가 설정하는 GUC를 권한 근거로 쓰지 않는다. Operation lock과 expected-status 재검증 후 PENDING→CURRENT/FAILED, CURRENT→STALE, STALE→CURRENT만 허용한다. FAILED→CURRENT와 timestamp 단독 변경은 허용하지 않는다.

CURRENT 전이 시 rejection이 있으면 named publication PASSED가 필요하며, 상태 변경과 `snapshot-current-selection=PASSED` append는 같은 SQL 함수·transaction에서 수행한다. 증빙에는 selected_by와 실제 session_user를 기록한다. service는 별도로 같은 선택 이력을 중복 append하지 않는다. 함수 실패나 caller transaction rollback은 상태와 이력을 함께 되돌린다. 이 경계는 기존 최소 publication 검사를 DB에서 강제하며, 실제 Source Use Approval/승인자 권한 인증 전체를 구현했다는 의미는 아니다.

#324의 `169a1b2c3d4e` 뒤에 Source Artifact 첫 revision을 연결한다. 최종 normalization run·Snapshot 정렬은 여전히 #164 후속 범위다. Snapshot이 존재하면 상태 보호 downgrade는 거부하고 forward-fix를 사용한다.

PR #323 후속 검토에 따라 ingestion Run은 DB CHECK로 `FAILED → snapshot_id IS NULL`, `NO_CHANGE → snapshot_id IS NOT NULL`을 강제한다. 성공 Run은 생성한 Snapshot을 참조할 수 있다. CHECK는 참조 대상의 생성 시점이나 normalization run 구조를 확정하지 않으며, #164의 Snapshot·normalization provenance 정렬은 후속 범위로 유지한다.
