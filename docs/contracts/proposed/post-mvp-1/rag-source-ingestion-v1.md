# RAG Source 수집·활성화 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Proposed Target · Not implemented — `proposed/`에서 RAG-00 팀 승인 대기 |
| 구현·리뷰 | Not implemented · Track F Source·RAG 구현과 지정 리뷰어·Privacy·외부 Source 승인 대기 |
| 외부 정본 | Manifest `post-mvp-rag-evaluation-contract@2026-08-29.11` (`PROPOSED_TARGET_NOT_IMPLEMENTED`) |
| Normative Source | `rag-source-management-policy-v1.0.md@1.18` · SHA-256 `35842d2cbe54201ff9fb5580616055eda613fe4c16ac6d60daa7f8859d2f28e3` |
| Last verified | 2026-09-01 |

## 목적과 범위

Track F가 사용하는 공식 의약품·의료정보를 재현 가능한 불변 Source Snapshot으로 수집하고, 승인·검증된 Snapshot만 Candidate Resolver, Rule과 Evidence Retrieval에 사용한다.

이 문서는 외부 RAG 문서 세트의 Source Governance를 저장소의 Local P0 Target으로 투영한다. 외부 문서와 이 문서가 충돌하면 RAG-00 승인 전에는 구현하지 않고 Manifest에 고정된 정본과 저장소 공유 계약을 함께 재검토한다. 문서 존재만으로 기존 Approved Contract Freeze v4를 변경하거나 현재 Runtime을 증명하지 않는다.

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
| `INTERNAL` | `TEAM_APPROVED_MEDICATION_ALIAS / null / null` | Candidate 검색용 팀 승인 Alias | 승인 Git Commit·Tag와 Fixture Manifest | `INTERNAL_CURATED_DATA`, 의료 Claim Citation 금지 |
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
| 내부 승인 Fixture | `internal:<승인 Git Tag 또는 Commit 기반 fixture_version>:<canonical_checksum 64자>` |
| `raw_checksum` | 각 원본 페이지 Byte의 무결성 검사용 SHA-256 |
| `raw_manifest_checksum` | 페이지 번호가 아니라 안정 Artifact Key로 정렬한 `(artifact key, raw_checksum, byte_size, content_type)` 목록의 Canonical SHA-256 |
| `canonical_checksum` | 모든 성공 페이지를 합친 뒤 Endpoint Primary Key로 정렬한 Canonical JSON 내용 SHA-256 |
| `canonicalization_spec_version` | Key 정렬·Unicode·숫자·null·Envelope 제외 규칙의 불변 Version. 규칙 변경 시 새 Version 사용 |

`canonical_checksum`은 Unique 제약으로 만들지 않는다. 같은 내용 재수집은 새 Snapshot을 만들지 않고 기존 Snapshot에 append-only `NO_CHANGE` Verification을 추가한다. 내용이 `A → B → A`로 원복되면 세 번째 수집은 새 시각과 새 `source_version`의 Snapshot으로 보존한다. 제공자 외부 Version이 같은데 Canonical 내용이 달라지면 `SOURCE_VERSION_CONFLICT`로 실패시키고 사람 검토 대상으로 보낸다. Pagination 번호·응답 시각처럼 내용과 무관한 Envelope 값은 Operation 계약의 명시적 제외 목록에 있을 때만 Canonical 입력에서 제외한다.

`schema_version`은 외부 응답 Envelope·필수 필드 계약이고 `parser_version`은 해당 구조를 읽는 코드·배포 Artifact 버전이다. `normalization_version`과 함께 각각 기록하며 같은 Raw Artifact 재처리도 기존 Run을 덮어쓰지 않는다.

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
