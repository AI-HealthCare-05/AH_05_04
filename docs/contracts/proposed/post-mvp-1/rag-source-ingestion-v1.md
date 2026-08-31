# RAG Source 수집·활성화 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Proposed Target · Not implemented — `proposed/`에서 RAG-00 팀 승인 대기 |
| 구현·리뷰 | Not implemented · Track F Source·RAG 구현과 지정 리뷰어·Privacy·외부 Source 승인 대기 |
| 외부 정본 | Manifest `post-mvp-rag-evaluation-contract@2026-08-29.8` (`PROPOSED_TARGET_NOT_IMPLEMENTED`) |
| Normative Source | `rag-source-management-policy-v1.0.md@1.16` · SHA-256 `d4c75ace2e7f4b89853fdff5e695a1c30f37f61f7abae58708c569b59046573d` |
| Last verified | 2026-08-31 |

## 목적과 범위

Track F가 사용하는 공식 의약품·의료정보를 재현 가능한 불변 Source Snapshot으로 수집하고, 승인·검증된 Snapshot만 Candidate Resolver, Rule과 Evidence Retrieval에 사용한다.

이 문서는 외부 RAG 문서 세트의 Source Governance를 저장소의 Local P0 Target으로 투영한다. 외부 문서와 이 문서가 충돌하면 RAG-00 승인 전에는 구현하지 않고 Manifest에 고정된 정본과 저장소 공유 계약을 함께 재검토한다. 문서 존재만으로 기존 Approved Contract Freeze v4를 변경하거나 현재 Runtime을 증명하지 않는다.

- P0 Source는 MFDS 공식 제품·성분·복합제 Component·승인 Alias, DUR 상호작용 근거와 환자용 승인 의료정보다.
- 실제 API Service ID, Operation·Path, 필수 파라미터, 응답 Content-Type, 성공 본문 code, pagination과 안정 Primary Key는 실응답 검증으로 확정한다. 문서나 구현에서 추정값을 만들지 않는다.
- HIRA 적용약가 데이터는 제품 Identity, 검색 정답 원장 또는 상호작용 근거로 사용하지 않는다.
- 승인되지 않은 Source 자동 편입, 열린 웹 검색, ChromaDB와 Graph DB는 범위 밖이다.

## 구현·검증 환경

- 실제 MFDS API 연결과 Source 수집 검증은 Local 환경에서만 수행한다.
- Development·Staging 서버는 구축하지 않는다. Git `develop` 브랜치는 협업 통합 브랜치이며 Development 서버를 의미하지 않는다.
- Local PostgreSQL·pgvector와 접근 통제된 Raw Artifact 저장소를 사용한다.
- API Key는 Local secret으로만 주입하고 `.env`, credential, 전체 인증 URL을 commit하지 않는다.
- 결정적 Parser·Normalization·Fixture 테스트는 CI에서 실행할 수 있지만 CI를 Development·Staging 서버로 간주하지 않는다.

## Source와 Snapshot

Source는 최소한 owner, license·재사용 조건, attribution, 목적, 상태와 승인 이력을 가진다. 신규 수집에 사용할 수 있는 상태는 `ACTIVE`, 신규 수집·검색에서 제외하는 상태는 `INACTIVE`다.

Snapshot은 다음 정보를 불변으로 보존한다.

- Source와 Source version
- 수집 시각과 importer·parser·normalization version
- Raw Artifact 위치와 checksum
- 정규화 결과 checksum, record count와 schema version
- 승인·검증 결과와 적용·유효 시각
- 이전 Snapshot과의 변경 계보

동일 Snapshot의 내용을 직접 수정하지 않는다. Source 응답이나 정규화 규칙이 바뀌면 새 Snapshot과 새 version을 만든다. `INACTIVE` Source와 과거 Snapshot은 신규 Candidate·Retrieval·Rule 평가에서 제외하지만 과거 Identification과 Citation provenance 재현을 위해 보존한다.

### Version·Checksum 규칙

| 대상 | 규칙 |
| --- | --- |
| 외부 불변 Version이 있는 Source | `external:<external_version>` |
| 외부 Version이 없는 API | `api:<RFC3339 UTC 6자리 소수초>:<canonical_checksum 64자>` |
| 내부 승인 Fixture | `internal:<승인 Git Tag 또는 Commit 기반 fixture_version>:<canonical_checksum 64자>` |
| `raw_checksum` | 각 원본 페이지 Byte의 무결성 검사용 SHA-256 |
| `canonical_checksum` | 모든 성공 페이지를 합친 뒤 Endpoint Primary Key로 정렬한 Canonical JSON 내용 SHA-256 |

`canonical_checksum`은 Unique 제약으로 만들지 않는다. 같은 내용 재수집은 새 Snapshot을 만들지 않고 기존 Snapshot에 append-only `NO_CHANGE` Verification을 추가한다. 내용이 `A → B → A`로 원복되면 세 번째 수집은 새 시각과 새 `source_version`의 Snapshot으로 보존한다. 제공자 외부 Version이 같은데 Canonical 내용이 달라지면 `SOURCE_VERSION_CONFLICT`로 실패시키고 사람 검토 대상으로 보낸다.

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

## 정규화와 파생 데이터

- 원문 Artifact는 보존하고 정규화 결과를 원문에 덮어쓰지 않는다.
- 제품·성분·Alias·복합제 Component는 안정적인 공식 Identity와 Source provenance를 유지한다.
- DUR 행을 `interaction_rule`로 변환할 때 원 Source 행과 `rule_evidence`를 역추적할 수 있어야 한다.
- 의료 산문은 versioned document·chunk로 변환하며 chunk가 Source Snapshot과 locator를 잃지 않도록 한다.
- OCR Candidate Index와 의료 Evidence Index는 PostgreSQL 안에서 별도 version과 물리 경계를 사용한다. OCR Candidate용 pgvector 결과를 의료 근거로 인용하지 않는다.

## 활성화와 Rollback

- 승인·검증된 Snapshot과 그 Snapshot으로 재현된 Catalog·Rule·Knowledge Index만 Runtime Release Bundle에 포함할 수 있다.
- 신규 Snapshot 적재가 완료되어도 평가·승인된 새 Runtime Release Bundle이 활성화되기 전에는 기존 활성 Bundle을 바꾸지 않는다.
- Source 비활성화 또는 만료 뒤 신규 Candidate·Retrieval·Rule 평가는 fail-closed로 차단한다.
- 과거 결과는 당시 Source version과 locator를 유지하지만 현재 답변으로 재사용하지 않는다.
- Local Runtime의 검증 실패 또는 회수 시 이전 승인 Bundle로 원자적으로 rollback할 수 있어야 한다.

Runtime Release Bundle의 상세 구성과 현재성 검사는 [RAG Runtime 계약](./rag-runtime-v1.md)을 따른다.

## 개인정보와 보존

- Source 수집 artifact는 환자·사용자 데이터와 분리한다.
- 합성·비식별 fixture만 저장소에 commit한다.
- 의료 원문 전체를 Stream, 일반 애플리케이션 로그, quarantine 또는 DLQ에 복제하지 않는다.
- 저장 위치·보존기간·접근 권한은 Source license와 Privacy 승인 중 더 엄격한 조건을 따른다.

## 최소 검증

- 정상·인증 실패·호출 한도·빈 결과·pagination·schema drift fixture
- 같은 원본·같은 parser/normalization version의 결정적 checksum과 record count
- 부분 page·부분 record 실패 시 Snapshot 비활성
- 제품·성분·Alias·Component 참조 무결성과 중복 Identity 차단
- DUR Source 행 → Rule → Evidence 역추적
- Candidate Index와 Knowledge Index의 version·물리 경계
- Source `INACTIVE`·만료 후 신규 사용 차단과 과거 Citation provenance 보존
- credential·전체 인증 URL·실제 환자정보의 fixture·로그·오류 응답 미포함
- 이전 승인 Bundle rollback 재현

## 공개 게이트

`EXT-SOURCE-001`, `EXT-SOURCE-002`, `EXT-PRIV-001`과 필요한 의료·약학 검토가 완료되기 전에는 실제 사용자 Source를 활성화하거나 `PUBLIC_TRACK_F`를 켜지 않는다. Development·Staging 서버는 만들지 않으며, 승인 전에는 합성 fixture를 사용하는 접근 통제된 Local demo만 허용한다.
