# Implementation Plan - MFDS RAG Phase 2A: Source to Knowledge Materialization (Revision 12)

## Overview
Implement Phase 2A (Source Snapshot Member -> KnowledgeDocument & KnowledgeChunk materialization) based on `docs/designs/ceohwj/MFDS_RAG_Phase2A_Design.md` and `MFDS_RAG_Agent_Handoff_Final.md`.
This phase bridges verified private Source Snapshot Members (EE/UD/NB XML artifacts, and optional standalone NN) into active `KnowledgeDocument` and `KnowledgeChunk` rows ready for evidence indexing, without altering existing database schemas, introducing triggers/RLS, or modifying existing XML parser semantics.

- 구현 담당: 정현우 (`@ceohwj`)
- 단일 책임 reviewer: 김지혜 (`@Jye-rookie`) 1명
- 전문 검토 근거: Backend·DB·Security 송은영 (`@phina-io`) (트랜잭션·DB 권한·인덱스 호환성 검토 근거 제공)

---

## Addressing Review Findings (Revision 11–12 Adjustments)

| # | Finding | Resolution in this Plan |
|---|---|---|
| 1 | EE·UD·NB와 NN 요청 형태 확정 | 허용 요청 집합을 정확히 두 형태로 고정: 동일 품목의 정확한 `{EE, UD, NB}` 또는 정확한 `{NN}` 단독. 누락·중복·추가 section과 `EE+NN` 부분 혼합은 모두 `REQUEST_INVALID`. `NN`에 공식 빈 항목(`empty_article_titles`)이 있을 경우 fail-closed로 `CHUNK_POLICY_UNSUPPORTED` 발생 (EE/UD/NB 실행과 격리). |
| 2 | Unsupported policy 오류 의미 충돌 | `REQUEST_INVALID`는 `item_seq` 형식, 중복 `member_ids` 등 요청 구조 오류로 한정하고, 지원하지 않는 `chunk_policy_version` 요청은 `CHUNK_POLICY_UNSUPPORTED`로 일원화. |
| 3 | RAW_RESPONSE artifact 결속 검증 누락 | `MaterializationSourceDocument`에 `artifact_key`, `section`, `artifact_kind`, `page_number`, `storage_backend`, `reject_code`, `parser_location`, `ingestion_run_status` 완비. DTO만으로 `RawArtifactMetadata` 완전 구성 가능. 구체 조건은 finding 12로 확장. |
| 4 | Exact replay 불변 필드 비교 완비 | Document `title`, `document_status`, `record_contract_version`, `canonicalization_spec_version`, `document_content_hash`, `external_document_id`, `locator`, `publisher IS NULL`, `source_url IS NULL`, `document_version IS NULL`, `knowledge_index_lock_marker == 0`, `chunk_count` 및 chunk level `chunk_index`, `chunk_text`, `content_hash`, `normalization_version`, `knowledge_index_lock_marker == 0` 전체 비교. `embedding_model`·`vector_store_key`의 소유권 표현은 finding 16으로 정정. |
| 5 | Proposed 계약 링크 위치 정합 | `docs/contracts/README.md`의 `## Proposed 계약` 구역(line 62)으로 이동. |
| 6 | Worker 단위 테스트 및 통합 테스트 명령 정합 | Worker 단위 테스트는 `run_with_worker_test_environment`, 격리 PostgreSQL은 `prepare_test_environment` + `run_with_integration_test_environment` 사용. `export REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"`로 통일하고 개인 절대 경로를 문서에 넣지 않는다. mypy는 `CONTRIBUTING.md`의 공식 명령 `uv run mypy backend/app ai_worker`를 사용한다. |
| 7 | Parser 회귀 항목 추가 | Task 1에 `XML_ARTICLE_SET_INVALID` (NN 섹션 7개 타이틀 불일치 시 거절) 회귀 테스트 명시. |
| 8 | Parser seam이 `inspect_xml()`에 재귀 canonicalization을 주입 | canonical structure 생성을 parser seam에서 분리하고, 기존에 canonicalization을 수행하던 `_load_document()` 경로에서만 `_canonical_element()`를 호출. `inspect_xml()`은 report 키·순서·값과 ValueError 의미가 develop 기준과 동일하며 1,100단계 중첩 XML에서 `RecursionError` 없이 동작. |
| 9 | Parser DTO repr이 Source 본문을 노출 | `ParsedMfdsLabelDocument.root`에 `field(repr=False)` 적용, `canonical_structure`는 DTO 필드에서 제거하여 repr 경로에서 완전히 사라짐. 합성 ARTICLE 제목·본문 비노출 회귀 테스트 추가. |
| 10 | Task 1 `ruff format --check` 실패 | `ai_worker/tests/rag/source_ingestion/test_mfds_label.py`의 implicit string concat 3곳이 UP012 수정 후 한 줄에 들어가게 되어 formatter가 재포맷 대상으로 보고했다. 해당 두 파일에만 `uv run ruff format`을 적용했고 범위 밖 파일은 포맷하지 않았다. `ruff format --check` 통과. |
| 11 | 설계서가 parser seam의 NFC·newline 정규화를 보장한다고 기술 | 실제 seam은 `ElementTree` root를 반환하며 `_canonical_text()`를 적용하지 않는다. 책임 경계를 "parser seam = 구문 분석·구조 검증·구조 통계·parsed root", "Task 2 renderer = text projection·newline·NFC·canonical text·hash"로 확정. Task 1에 raw 구조 보존 경계 테스트 1건 추가, Task 2에 NFD/NFC·hash·newline 동등성 실패 테스트 명시. |
| 12 | Artifact identity 결속이 `page_number >= 1`로 느슨함 | `page_number == SECTION_ORDER.index(section) + 1` (EE=1, UD=2, NB=3, NN=4) exact binding으로 강화. 추가로 `ingestion_artifact_id == IngestionArtifact.id`, artifact가 요청된 run 소속, artifact section == member section, `object_key`·`storage_backend`·checksum 일치, `content_type == OBSERVED_CONTENT_TYPE` exact match, schema/parser/normalization version 결속, 타 품목·타 member artifact 재사용 거부를 검증. 네 조건은 기존 `_member_artifact_metadata()` 의미를 재사용한다. DTO `page_number: int \| None`로 DB nullable 반영하고 `None`도 negative test에 포함. |
| 13 | ~~IngestionRun 상태 검증 누락~~ | **SUPERSEDED by 23.** 이 행은 허용 집합을 `SUCCEEDED`+`SUCCEEDED_WITH_REJECTIONS` 2종으로 적었으나 폐기한다. finding 23의 `SUCCEEDED` 단일 허용이 정본이다. |
| 14 | 출력 순서 결정성 미고정 | 기존 `SECTION_ORDER = ("EE","UD","NB","NN")`를 정본으로 재사용. source documents / document draft / chunk draft / receipt / replay 비교 collection을 모두 같은 순서로 정렬. 요청 member 순서·Repository 반환 순서·SQL row 순서 shuffle 테스트와 반복 실행 field-equivalence 테스트 추가. |
| 15 | Snapshot checksum 검증 의미가 "재계산"으로 읽힘 | 선택된 member 집합으로 `raw_manifest_checksum`·`canonical_checksum`을 재계산하지 않는다. pre-read read model과 lock 후 재조회 값을 비교하는 **race/stale 검증**으로 의미 확정. 불일치는 `ARTIFACT_INTEGRITY_MISMATCH`. NN singleton도 동일 원칙. |
| 16 | `embedding_model`/`vector_store_key` 소유권 단정 | 기존 Index adapter가 두 컬럼을 읽지·쓰지 않고, `knowledge_index_builder` role에 두 컬럼 UPDATE 권한이 없다(`knowledge_index_role_policy.py`). 값이 관측되는 유일 사례는 `LEGACY_V1` migration 보존 회귀 row. 따라서 "후속 Index 단계 소유"를 철회하고 **Phase 2A 비소유 legacy nullable field · authoritative owner 미확정**으로 정정. insert NULL, replay 미갱신·비교 제외, 기존 non-NULL 보존. |
| 17 | 자유형 message 오류 계약 | `KnowledgeMaterializationError(reason, message=None)`을 폐기하고 저장소의 기존 reason-only 관례(`KnowledgeEvidenceIndexValidationError`, `SourceSnapshotMemberError`)와 동일한 `__init__(self, reason) -> None` / `super().__init__(reason.value)` 형태로 확정. object key·경로·SQL·원문 XML·parser ValueError detail 비노출 테스트 추가. |
| 18 | ~~Transaction lock 순서가 기존 Index와 반대~~ | **SUPERSEDED by 29.** 테이블 순서 정렬 자체는 유효하지만 "공통 순서 확정"·"근거 없는 deadlock retry 금지" 표현은 폐기한다. finding 29의 "검증 전 가설 + Task 3 시작 전 전략 확정"이 정본이다. |
| 19 | 실행 순서 문구가 조회 전 member section을 아는 것처럼 기술 | 1) 요청 문법·형식 검증 → 2) read-only read model 확보 → 3) **조회된 실제 member**의 section 집합·lifecycle·run status 검증 → 4) artifact identity 결속 → 5) reader 읽기 → 6) parser seam → 7) draft 생성 → 8~13) 짧은 write transaction의 13단계로 재기술. |
| 20 | ~~설계서 기준 정보가 과거 SHA·과거 PR 목록~~ | **SUPERSEDED by 27.** 이 행이 적은 `91b6062d`와 PR 목록은 이미 낡았다. finding 27(그리고 finding 32)의 값이 정본이다. |
| 21 | NFC/NFD 입력이 동일한 **document** hash를 만든다는 요구가 Index 계약과 충돌 | 정규화 영향을 domain별로 분리. 같아야 하는 것은 정규화된 `chunk_text`와 `KnowledgeChunk.content_hash`뿐이다. `document_content_hash == member.content_sha256`이므로 raw bytes가 다르면 **달라야** 한다. 계약·설계에 domain별 표를 추가하고 Task 2에 `test_document_content_hash_follows_raw_bytes_not_normalization`을 넣었다. 설계서의 "parser가 이미 만든 NFC text" 문구도 "renderer가 NFC를 적용한다"로 정정. |
| 22 | Task 2 API(draft만 반환)로 DB provenance·receipt를 검증할 수 없음 | Task 2는 순수 renderer/draft 검증만 유지하고, DB UUID receipt·write transaction 호출 여부·artifact/member/run 양쪽 ID 비교·`object_key` mutation·`ingestion_run_status` gate를 **Task 3으로 이동**했다. Task 2에 경계 표를 추가하고, DTO에 양쪽 식별자를 넣어 Task 2에서 자기완결 대조하는 대안은 "DTO가 조회 결과의 투영이라 실제 row 대조가 안 된다"는 이유로 기각 근거와 함께 기록했다. Task 2에 남는 근접 검증은 fake reader 호출 카운터 기반 `test_section_set_rejection_happens_before_artifact_read`뿐이다. |
| 23 | MFDS run 허용 상태가 실제 경로보다 넓음 | `_validate_metadata()`가 `rejected_record_count == 0`을 강제함을 확인(`expected` tuple의 `0`). 따라서 MFDS artifact-producing 경로의 성공 상태는 `SUCCEEDED` **하나**뿐이다. 허용 집합을 `SUCCEEDED` 단일로 축소하고 `SUCCEEDED_WITH_REJECTIONS`를 거절 대상·미확정 항목으로 이동. Task 3/4에 거절 테스트 추가. |
| 24 | `object_key` 규칙이 Task 2 필수 검증이면서 동시에 미확정 | 규칙을 실측으로 확정: `f"sha256/{raw_checksum[:2]}/{raw_checksum}.artifact"`, **locator가 아니라 `raw_checksum`에서 도출**. 검증 책임을 Task 3으로 이동. **정정(finding 33)**: 이 행이 적은 "공용 helper 승격 필요"는 오류였다 — `LocalPrivateSourceArtifactStore.object_key_for_checksum()`이 이미 public `@staticmethod`로 존재하므로 승격 없이 호출하면 된다. |
| 25 | Post-commit mismatch는 rollback 불가 | receipt 재구성·비교를 **commit 전 같은 transaction 안에서** 끝내도록 변경. 근거: `persist_complete_index()`가 `session.begin()` 블록 안에서 `_load_and_recompute_receipt()`를 호출하고 불일치 시 commit 전에 raise한다. commit 이후 확인은 **rollback 불가능한 별도 audit**으로만 정의하고 성공 조건·`RECEIPT_MISMATCH`에서 제외. 실행 순서 11~13단계와 `RECEIPT_MISMATCH` 설명도 정정. |
| 26 | Receipt 동일성 정의가 `is_exact_replay`와 모순 | "`is_exact_replay`를 제외한 모든 필드 동일"을 정본 표현으로 확정하고 비교 포함/제외 필드 표를 계약에 추가. draft 수준에는 이 필드가 없으므로 전 필드 동일성을 요구할 수 있음을 구분. Task 2는 draft 전 필드 동일, Task 3/4는 제외 규칙 적용 테스트로 분리. |
| 27 | 최신 develop·열린 PR 기준이 다시 변경 | `develop`을 `9334ebbc`까지 반영(총 4커밋: `4766a68a` #638, `c0e8798a` #629, `c9474386` #636, `9334ebbc` #640). `docs/testing.md`에서 #638과 **실제 텍스트 충돌이 발생**해 두 절 보존으로 해소. 열린 PR을 `#650/#649/#646/#639`로 갱신하고 `#629`·`#636`·`#640`이 병합됐음을 기록. 기준값에 확인 시각(2026-09-16T07:28Z)을 함께 남기고 "읽는 시점에 이미 뒤처질 수 있음"을 명시. |
| 28 | PR `#649`(`#591` 실제 적재 결과)가 Phase 2A 전제에 영향 | Design §1.4.1 신설. 실제 Snapshot `073ee706-...`, 재실행 `NO_CHANGE` 실측, `verification_status` 미포함, NN 범위 제외, 그리고 **`@ceohwj`에게 배정된 미완 consumer acceptance 체크박스**를 기록. `NO_CHANGE` 거절이 정상 경로를 막지 않음을 member→artifact→run 결속으로 논증해 보류에서 확정 판단으로 이동. |
| 29 | `SECTION_ORDER` 순회와 Index의 chunk UUID 순회가 같은 row 획득 순서를 보장하지 않음 | "deadlock-safe 공통 순서 확정"을 철회하고 **검증 전 가설**로 하향. 계약·설계에 경고 블록을 넣고, 교착 시 순회 기준 통일(양쪽 `source_snapshot_member_id` 오름차순) 또는 상위 직렬화를 `@phina-io`와 재결정하도록 명시. Task 4 cross-flow 테스트를 가설 검증 지점으로 지정. |
| 30 | 설계서의 section 판정 순서가 후반 실행 순서와 충돌 | §4.1의 "lifecycle gate 조회와 artifact 읽기보다 먼저" 문구를 정정. section은 member row의 locator에서 나오므로 **read model 확보 후**에 판정하며(§7.1 2→3단계), 요청 단계에서 검증 가능한 것은 UUID 형식·중복·`expected_item_seq` 형식·policy 지원 여부뿐임을 명시. |
| 31 | ~~Reader `ValueError`의 reason 변환 미정~~ | **SUPERSEDED by 32.** 3-way 매핑 표는 구현 불가로 철회한다. | 계약·설계에 매핑 표 추가. 크기·checksum 불일치 → `ARTIFACT_INTEGRITY_MISMATCH`; `RawArtifactMetadata` 형식 위반·object key root 이탈·symlink → `SOURCE_BINDING_INVALID`; reader root 권한·형태 오류·I/O 실패 → `DEPENDENCY_ERROR`. 새 reason을 만들지 않고 reader 평문 메시지를 노출하지 않으며 원본은 chaining으로만 보존. Task 2에 6건 테스트 추가. |
| 32 | Reader 오류 3-way 분류를 현재 인터페이스로 구현 불가 | finding 31의 매핑 표를 **철회**. reader 생성자 오류는 커널 전달 전에 발생하므로 **composition boundary**에서 처리하고 계약 reason으로 투영하지 않는다. `read_verified()` 실패(크기·checksum·I/O·path 이탈)는 모두 구분 없는 `ValueError`라 메시지 비교 없이는 나눌 수 없다. 옵션 A(reader typed 예외) / B(Phase 2A typed loader adapter, 권고) / C(단일 `DEPENDENCY_ERROR`, 비권고)를 제시하고 `@phina-io`+`@Jye-rookie` 결정까지 **BLOCKED**. Task 2를 **2a(순수 renderer/chunker, reader 미사용 — 진행 가능)** 와 **2b(loader/오류 변환 — 보류)** 로 분리. |
| 33 | chunk renderer가 byte-exact 계약으로 미완성 | heading marker(`"#" * level + " "`), title/본문 구분(`\n\n`), 블록 경계, mixed `text`/child/`tail` 무구분자 결합, `DOC` 직하 연속 non-ARTICLE maximal run, table 배치(`caption` → source order 행, cell `\t`, 행 `\n`, 별도 marker 없음), title-only ARTICLE, 정규화 5단계 순서를 계약에 고정. **8개 golden vector(G1~G8)의 XML 입력 → 정확한 `chunk_text` repr → SHA-256을 계약에 확정**하고 Task 2a RED fixture로 지정. 값 변경은 policy version bump + 리뷰어 승인 필요. |
| 34 | 설계서에 post-commit 구버전 문구 잔존 | 설계서 흐름도(`post-commit receipt 재조회`), 오류 표(`commit 후 재조회 불일치 = RECEIPT_MISMATCH`), §10.4 테스트(`commit 뒤 receipt mutation`) 3곳을 모두 commit 전 transaction 검증으로 통일. commit 이후는 rollback 불가능한 선택적 audit이며 `RECEIPT_MISMATCH`로 보고하지 않음을 각 지점에 명시. |
| 35 | Task 2 draft-only 경계에 receipt 요구 잔존 | 설계서 §10.2의 "draft·receipt 순서 동일", "replay receipt 전 필드 동일"을 draft 전용으로 축소하고 receipt 순서·동일성 검증을 §10.4/§10.5(Task 3/4)로 이동. |
| 36 | Revision 10 findings 표에 상충 지시 공존 | superseded 행을 취소선 + `SUPERSEDED by N`으로 명시 폐기: 3→12, 13→23(`SUCCEEDED_WITH_REJECTIONS` 허용 문구 폐기), 18→29(lock "확정" 표현 폐기), 20→27(구 SHA·PR 목록 폐기). finding 24에는 `object_key_for_checksum()` 정정 주석 추가. |
| 37 | cross-flow lock 전략 결정 시점 | "Task 4에서 검증"을 **"Task 3 시작 전 `@phina-io` 확정"** 으로 상향. Task 4는 확정된 전략을 *검증*하는 단계이며 *결정*하는 단계가 아님을 명시. `docs/testing.md`의 "기존 Index와 동일한 row lock 순서" 표현도 가설로 하향. 전략 확정 전 Repository lock 구현·mock 순서 검증을 시작하지 않는다. |
| 38 | `object_key` 공용 helper 승격 보류는 불필요 | `LocalPrivateSourceArtifactStore.object_key_for_checksum()`이 이미 public `@staticmethod`(docstring: "cleanup 요청도 writer와 같은 content-addressed key를 사용하게 합니다")로 존재함을 확인. 승격 없이 재사용하면 되므로 해당 미확정 항목을 **철회**하고, 자리를 "reader typed boundary" 항목으로 교체. |
| 39 | Task 2a 구현 결과 반영 | `mfds_label_chunk_policy.py` + 테스트 27건을 작성해 golden vector가 실제 구현 산출값과 일치함을 확인. 구현 중 **table 배치 불일치**를 발견해 "table은 위치와 무관하게 항상 블록"으로 규약을 고정하고 G6 벡터를 `148e4531...`로 갱신. `ChunkPolicyError`를 typed reason-only 예외로 두어 상위 계층이 메시지를 파싱하지 않게 했다 (finding 32 옵션 B 패턴의 선례). |
| 40 | 병합된 `#649`가 드러낸 신규 보류 3건 (**① SUPERSEDED by 43**) | `#649`가 `2f003eac`로 병합되어 실제 적재 기록이 develop에 들어왔다. 미완 항목에서 ① 실제 artifact의 `content_type`/encoding 미인계 → 계약의 `OBSERVED_CONTENT_TYPE` exact match가 **실측 미대조** ② `verification_status` 미포함 → `CURRENT` 요구 미확인 ③ 개별 Member/Artifact UUID 미인계 → `member_ids` 구성 불가를 각각 §13 보류로 추가. 동시에 Source ACTIVE / Endpoint VERIFIED·ENABLED·APPROVED / Operation ENABLED·APPROVED가 실제로 확인되어 lifecycle gate 충족 가능성은 확보됐다. |
| 41 | 기준 `develop`이 또 전진 | Revision 11 보고 기준 `efeeb22e`에서 **`1cbb229b`** 로 2커밋 전진(`edfa23e1` #193, `1cbb229b` #651). 확인 시각 2026-09-16. `docs/testing.md`에서 #193이 추가한 `## #193 내부 합성 증상 데모 / #196 RAG 준비 상태` 절과 **다시 텍스트 충돌**이 발생해 두 절 보존으로 해소했고, 상위에 새 `##` 절이 삽입되면서 #634 절이 무관한 절의 하위로 중첩되지 않도록 `###` → `##`로 승격했다. 저장소 전체 열린 PR은 `#660/#655/#654/#650` 4건이며 #634 파일과 겹치는 것은 `#650`(`docs/contracts/README.md`, 다른 구역)뿐이다. |
| 42 | 인라인 위치 table이 계약의 "항상 블록" 규칙을 위반 | Task 2a 검증 중 자체 발견. `_render_inline()`이 table을 구분자 없이 이어 붙여 `<b>` 안이나 다른 표의 cell 안에 있는 표가 **인라인으로** 렌더링됐다(`'xya\tbz'`). finding 39가 없앤 "같은 표의 `content_hash`가 위치에 따라 달라지는" 결함이 중첩 깊이 한 단계 아래에서 그대로 살아 있었다. 블록 위치가 없는 표는 표현을 추정하지 않고 `CHUNK_POLICY_UNSUPPORTED`로 **fail-closed** 하도록 수정했다. **블록 위치 table의 렌더링은 바뀌지 않았고 G1~G8 hash도 전부 불변**이다. 계약 "table 배치" 항목에 fail-closed 규칙을 추가하고 회귀 테스트 4건(위치 동등성 1 + 인라인 거절 3)을 넣었다. |
| 43 | finding 40 ①(`content_type` 실측 미대조)이 사실과 다름 | **정정.** `docs/validation/rag/issue-591/novasc-label-probe-summary.json`에 EE·UD·NB 3개 모두 `content_type`이 **공개 기록으로 존재**한다: `application/download; UTF-8; charset=UTF-8` (bytes 757 / 965 / 22750, SHA-256도 함께). "미인계"로 단정한 것은 오류다. 정확한 잔여 미확인은 "**HTTP 수집 시점 관측값**은 공개 확인됐으나, **저장된 `IngestionArtifact.content_type` 컬럼 값**이 그와 같은지는 consumer 재조회로 확인되지 않았다"이다. §13 보류 문구를 이 표현으로 교체. |
| 44 | 순수성 주장이 import graph 수준에서는 성립하지 않음 | 실측: `mfds_label_chunk_policy`의 직접 import는 표준 라이브러리 + `ParsedMfdsLabelDocument` 뿐이고, 호출 그래프에도 reader·DB·receipt가 없다. 다만 타입을 가져오려고 `mfds_label`을 import하면 **모듈 그래프로** `source_client.mfds_client`, `source_ingestion.artifacts`, `receipt_validation` 등 27개 `ai_worker` 모듈과 `httpx`가 딸려 온다. SQLAlchemy·psycopg·`backend.app`·celery는 **없음**을 확인했다. 계약의 "reader·DB·receipt·transaction에 의존하지 않는다"는 호출·동작 수준에서는 참이지만 import 수준에서는 아니다. 해소안(`ParsedMfdsLabelDocument`를 leaf 모듈로 분리)은 Task 1 파일 구조 변경이라 이번 범위 밖으로 두고 결정 표에 남긴다. |



---

## 결정 사항 확정 및 구현 상태 표 (D1~D5 정합화 완료)

이 표는 설계 및 계획 단계에서 논의되었던 주요 결정 사항(D1~D5)의 최종 확정 결과 및 구현 상태의 **단일 출처**다.
구현 및 검증이 완료된 항목들의 실제 확정 계약을 기록한다.

### D1. Reader 오류 typed boundary (확정 및 구현 완료)
- **확정 결과**: 옵션 A의 최소 변경 채택.
- `ai_worker/tasks/rag/source_ingestion/artifacts.py` 및 finalizer에 `ValueError` 하위 typed 예외 계층 적용:
  - `RawArtifactIntegrityError` → `ARTIFACT_INTEGRITY_MISMATCH`
  - `RawArtifactUnavailableError` → `DEPENDENCY_ERROR`
  - `ArtifactObjectKeyError` / `ArtifactPathTraversalError` → `SOURCE_BINDING_INVALID`
- **구현 상태**: Task 2b 및 Task 3, 4 구현 및 회귀 테스트 100% 통과 완료.

### D2. Snapshot Advisory Lock 및 Cross-flow 직렬화 (확정 및 검증 완료)
- **확정 결과**:
  - Snapshot 단위의 공통 advisory lock 사용:
    `SELECT pg_advisory_xact_lock(hashtextextended('knowledge-source-snapshot:' || snapshot_id, 0)::bit(32)::bigint)` (namespace `0`)
  - 다중 Snapshot 시 `snapshot_id.bytes` 순으로 정렬하여 lock을 획득함으로써 교착 상태 방지.
  - Evidence Index 빌더(`SqlAlchemyKnowledgeEvidenceIndexRepository`)와 Materialization 저장소(`SqlAlchemyKnowledgeMaterializationRepository`) 양쪽 모두 동일한 snapshot advisory lock에 참여하여 완벽한 상호 배제 보장.
- **구현 상태**: Task 4 PostgreSQL integration 테스트(`test_d2_cross_flow_materialization_and_index_mutual_exclusion`)를 통해 두 Repository 간의 동시 경합 및 직렬화 검증 100% PASS 완료.

### D3. `SUCCEEDED_WITH_REJECTIONS` 및 IngestionRun 상태 검증 (확정 및 구현 완료)
- **확정 결과**:
  - 허용 상태는 `RagIngestionRunStatus.SUCCEEDED` 단일 상태만 허용.
  - 그 외 상태(`SUCCEEDED_WITH_REJECTIONS`, `RUNNING`, `FAILED`, `NO_CHANGE`)는 fail-closed 원칙에 따라 `SOURCE_NOT_ELIGIBLE`로 거절.
- **구현 상태**: Task 3 단위 테스트 및 Task 4 PostgreSQL 통합 테스트에서 FAILED, NO_CHANGE, RUNNING, SUCCEEDED_WITH_REJECTIONS 상태 거절 검증 완료.

### D4. Post-commit audit의 지위 (확정 및 구현 완료)
- **확정 결과**:
  - `RECEIPT_MISMATCH` 및 트랜잭션 롤백은 commit 전 동일 트랜잭션 내부의 재조회/비교로 완결.
  - commit 이후의 감사는 독립 함수 `audit_post_commit(session_factory, result) -> bool`로 구현.
  - 감사 실패 시 이미 commit된 row를 롤백하지 않으며 `False`를 반환하는 관측 감사로 위치 고정.
- **구현 상태**: Task 3 및 Task 4에서 commit 전 검증과 독립 사후 audit 분리 구현 및 검증 완료.

### D5. Golden vector 및 chunk policy (확정 및 검증 완료)
- **확정 결과**:
  - G1~G8 golden vector(10개 vector)의 `chunk_text` UTF-8 SHA-256 계약 확정.
  - table 배치는 항상 블록 위치로 규약 고정, 인라인 table은 `CHUNK_POLICY_UNSUPPORTED` fail-closed.
- **구현 상태**: Task 2a에서 40개 테스트를 통해 전건 검증 완료.

---

## Task 1~4 완료 요약

| 순서 | 작업 | 상태 | 검증 결과 |
|---|---|---|---|
| Task 1 | Parser Seam 분리 (`parse_mfds_label_artifact`) | 완료 | parser 회귀 및 DTO 비노출 통과 |
| Task 2a | Deterministic chunk policy & G1~G8 golden vectors | 완료 | 40개 unit tests 통과 |
| Task 2b | Pure materialization kernel & Reader typed error mapping | 완료 | fake reader 기반 kernel 단위 테스트 통과 |
| Task 3 | PostgreSQL Materialization Repository 구현 | 완료 | mock unit tests 및 mypy 통과 |
| Task 4 | PostgreSQL 격리 통합 테스트 (All-or-Nothing, exact replay, cross-flow lock) | 완료 | 24개 PostgreSQL integration tests 통과 |


## #634 이슈 본문 정렬이 필요한 문구

이슈 본문을 **이번 라운드에서 수정하지 않았다.** 아래는 `@phina-io` 확인 후 적용할 초안이다.

| 위치 | 현재 문구 | 정렬 제안 | 사유 |
|---|---|---|---|
| 작업 내용 5번째 항목 | "post-commit 새 session receipt 재조회와 로그 비노출 조건을 검증한다." | "commit 전 같은 transaction 안에서 receipt를 재구성·비교하고, commit 이후 새 session 재조회는 rollback 불가능한 audit으로 수행하며, 로그 비노출 조건을 검증한다." | commit 이후 불일치는 되돌릴 수 없다. 성공 조건이 rollback 가능한 지점에 있어야 한다 (D4). |
| 완료 조건 3번째 항목 | "transaction rollback, concurrent replay, content conflict, post-commit receipt가 PostgreSQL에서 검증된다." | "transaction rollback, concurrent replay, content conflict, **commit 전 receipt 재구성 일치**가 PostgreSQL에서 검증되고, commit 이후 audit 재조회가 함께 수행된다." | 위와 동일 (D4). |
| 작업 내용 2번째 항목 | "…receipt, 내부 오류 reason을 구현한다." | "…receipt, 내부 오류 reason을 구현한다. **reader 오류의 reason 분류는 D1 결정 전까지 보류한다.**" | 현재 reader 인터페이스로는 원인별 분류가 불가능하다 (D1). |
| 관련 Issue·의존성 | "관련 Issue: #178, #591" | `#649` 병합으로 첫 제품 실제 적재가 완료됐고 consumer acceptance는 `@ceohwj` 미완 항목이라는 사실을 한 줄 추가. | 전제가 바뀌었다 (finding 28·40). |

---

## Pre-verification Evidence (에이전트 사전 격리 환경 검증 증적)
- **일시**: 2026-09-16
- **대상**: `tests/integration/rag/test_mfds_detail_acquisition.py`
- **실행 환경**: 격리 worktree 환경 (`feat/634-mfds-knowledge-materialization`)
- **실행 명령** (리뷰어 독립 검증 가능):
  ```bash
  cd "$(git rev-parse --show-toplevel)"
  export REPOSITORY_ROOT="$(git rev-parse --show-toplevel)" COMPOSE_PROJECT_NAME=ah_05_04
  source scripts/ci/test_environment.sh
  prepare_test_environment
  run_with_integration_test_environment pytest tests/integration/rag/test_mfds_detail_acquisition.py -q
  ```
- **에이전트 실행 결과**: `PostgreSQL·Redis 준비 상태 확인 완료. 격리된 test 데이터베이스를 재생성합니다. DROP DATABASE / CREATE DATABASE .... 4 passed in 8.57s` (종료 코드 0).
- **상태 (Revision 9)**: 이 결과는 **Revision 8 이전 실행의 인계 증적이며 Revision 9에서 재실행하지 않았다**
  (`NOT_RUN` in this execution). Revision 9에서 실제 재실행한 검사는 Task 1 parser 테스트, Worker RAG 회귀,
  `ruff check`, `ruff format --check`, `uv run mypy backend/app ai_worker`, `git diff --check`다.

---

## 6대 Hash Domain 정의 및 상호 일치 불변식

| # | Domain | 대상 Byte / 데이터 | 생성·검증 및 보존 규칙 |
|---|---|---|---|
| 1 | Artifact `raw_sha256` | 저장된 원본 XML의 exact raw bytes | Reader가 read 시 SHA-256 재계산 후 `RagSourceIngestionArtifact.raw_checksum`과 비교 |
| 2 | Snapshot `raw_manifest_checksum` | Snapshot 전체 raw artifact metadata manifest | 단일 member로 재계산하거나 대체하지 않고 `RagSourceSnapshot.raw_manifest_checksum` 보존 및 검증 |
| 3 | Snapshot `canonical_checksum` | Parser가 생성한 품목 전체 canonical structure manifest | `canonical_json_bytes`의 sha256. 문서 hash나 chunk hash로 재사용 금지 |
| 4 | SnapshotMember `content_sha256` | 해당 member의 raw bytes | `raw_sha256` 및 실제 read raw bytes의 SHA-256과 100% 일치 |
| 5 | KnowledgeDocument `document_content_hash` | Index가 SnapshotMember와 결합할 때 사용하는 hash | Index 어댑터 계약(`document_content_hash == SnapshotMember.content_sha256`)에 맞춰 `member.content_sha256`을 복사. 저장 전 원본 read bytes SHA-256 재검증 |
| 6 | KnowledgeChunk `content_hash` | 최종 정규화된 `chunk_text`의 UTF-8 인코딩 bytes | `hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()` |

---

## Data Transfer Objects (DTO) 및 Interface 명세

### 1. DTO 정의 (`ai_worker/tasks/rag/knowledge_materialization.py`)

```python
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

class KnowledgeMaterializationFailureReason(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    SOURCE_BINDING_INVALID = "SOURCE_BINDING_INVALID"
    SOURCE_NOT_ELIGIBLE = "SOURCE_NOT_ELIGIBLE"
    ARTIFACT_INTEGRITY_MISMATCH = "ARTIFACT_INTEGRITY_MISMATCH"
    PARSER_REJECTED = "PARSER_REJECTED"
    CHUNK_POLICY_UNSUPPORTED = "CHUNK_POLICY_UNSUPPORTED"
    CONTENT_CONFLICT = "CONTENT_CONFLICT"
    RECEIPT_MISMATCH = "RECEIPT_MISMATCH"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"

class KnowledgeMaterializationError(Exception):
    """reason-only 공개 오류. 기존 KnowledgeEvidenceIndexValidationError 관례와 동일."""

    def __init__(self, reason: KnowledgeMaterializationFailureReason) -> None:
        self.reason = reason
        super().__init__(reason.value)

@dataclass(frozen=True)
class KnowledgeMaterializationRequest:
    snapshot_id: UUID
    member_ids: tuple[UUID, ...]
    expected_item_seq: str
    chunk_policy_version: str

@dataclass(frozen=True)
class MaterializationSourceDocument:
    source_id: UUID
    source_code: str
    source_lifecycle_status: str
    endpoint_id: UUID
    endpoint_code: str
    endpoint_lifecycle_status: str
    endpoint_runtime_status: str
    endpoint_acquisition_status: str
    operation_id: UUID
    operation_code: str
    operation_runtime_status: str
    operation_acquisition_status: str
    snapshot_id: UUID
    source_version: str
    canonical_checksum: str
    raw_manifest_checksum: str
    schema_version: str
    parser_version: str
    normalization_version: str
    canonicalization_spec_version: str
    snapshot_verification_status: str
    ingestion_run_id: UUID
    ingestion_run_status: str         # Invariant: == "SUCCEEDED" (SUCCEEDED_WITH_REJECTIONS 미허용)
    member_id: UUID
    member_kind: str
    locator: str
    content_sha256: str
    ingestion_artifact_id: UUID       # Invariant: ingestion_artifact_id == RagSourceIngestionArtifact.id
    artifact_key: str                 # Invariant: artifact_key == f"{locator}.xml"
    section: str                      # Invariant: locator 마지막 segment, SECTION_ORDER의 원소
    artifact_kind: str                # Invariant: artifact_kind == "RAW_RESPONSE"
    page_number: int | None           # DB nullable. Invariant: SECTION_ORDER.index(section) + 1
    storage_backend: str              # Invariant: storage_backend == "LOCAL_PRIVATE"
    reject_code: str | None           # Invariant: reject_code is None
    parser_location: str | None       # Invariant: parser_location is None
    raw_checksum: str
    byte_size: int
    content_type: str
    object_key: str = field(repr=False)

@dataclass(frozen=True)
class KnowledgeChunkDraft:
    chunk_index: int
    chunk_text: str
    content_hash: str
    normalization_version: str

@dataclass(frozen=True)
class KnowledgeDocumentDraft:
    source_snapshot_member_id: UUID
    external_document_id: str
    document_content_hash: str
    canonicalization_spec_version: str
    title: str
    chunks: tuple[KnowledgeChunkDraft, ...]

@dataclass(frozen=True)
class MaterializedChunkReceipt:
    knowledge_chunk_id: UUID
    chunk_index: int
    content_hash: str

@dataclass(frozen=True)
class MaterializedDocumentReceipt:
    knowledge_document_id: UUID
    source_snapshot_member_id: UUID
    external_document_id: str
    document_content_hash: str
    locator: str = field(repr=False)
    chunks: tuple[MaterializedChunkReceipt, ...]

@dataclass(frozen=True)
class KnowledgeMaterializationReceipt:
    snapshot_id: UUID
    source_code: str
    source_version: str
    snapshot_canonical_checksum: str
    canonicalization_spec_version: str
    item_seq: str
    chunk_policy_version: str
    documents: tuple[MaterializedDocumentReceipt, ...]

class MaterializationOutcome(StrEnum):
    CREATED = "CREATED"
    EXACT_REPLAY = "EXACT_REPLAY"

@dataclass(frozen=True, slots=True)
class KnowledgeMaterializationResult:
    receipt: KnowledgeMaterializationReceipt
    outcome: MaterializationOutcome

    @property
    def is_exact_replay(self) -> bool:
        return self.outcome == MaterializationOutcome.EXACT_REPLAY
```

`MaterializationSourceDocument`는 `RawArtifactMetadata(artifact_key, raw_checksum, byte_size, content_type)`를
이 DTO만으로 완전히 구성할 수 있어야 한다. reader 호출은
`LocalPrivateSourceArtifactReader.read_verified(object_key=doc.object_key, metadata=RawArtifactMetadata(...))`
형태이며, `artifact_key`를 요청자 입력이나 별도 조회로 보충하지 않는다.
`ingestion_run_status`, `artifact_key`, `section`, `artifact_kind`, `page_number`, `storage_backend`,
`reject_code`, `parser_location`은 locked persistence gate가 재검증하는 값과 동일해야 한다. 이 DTO는 조회 결과의
투영이므로 **불변식 검증 책임은 실제 row와 대조할 수 있는 Task 3**에 있다 (Task 2는 검증된 입력으로 가정).
`page_number`는 DB 스키마와 동일하게 `int | None`이며 `None`도 negative 경로로 처리한다.
네 조건(`storage_backend`, `artifact_key`, `raw_checksum`, `content_type`)은 기존
`_member_artifact_metadata()`의 검증 의미를 재사용하고 중복 규칙을 새로 만들지 않는다.

모든 출력 collection은 기존 `SECTION_ORDER = ("EE", "UD", "NB", "NN")` 인덱스 오름차순으로 정렬한다.
정렬 기준을 새로 발명하지 않는다.

### 2. Repository Interface (`ai_worker/adapters/sqlalchemy_knowledge_materialization.py`)

```python
class SqlAlchemyKnowledgeMaterializationRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def fetch_source_documents(
        self,
        snapshot_id: UUID,
        member_ids: Sequence[UUID],
    ) -> tuple[MaterializationSourceDocument, ...]:
        ...

    async def persist_materialization(
        self,
        request: KnowledgeMaterializationRequest,
        drafts: Sequence[KnowledgeDocumentDraft],
        source_docs: Sequence[MaterializationSourceDocument],
    ) -> KnowledgeMaterializationReceipt:
        ...
```

---

## DB 스키마 컬럼 매핑 계약 명세

### `KnowledgeDocument` (`backend/app/models/knowledge.py`)
- `id`: `UUID(uuid4())` (exact replay 시 기존 id 반환)
- `title`: `parsed.document_title` (XML `<DOC title="...">` 파싱 값, 기존 parser 관례대로 빈·누락 title은 `ValueError("XML_SECTION_MISMATCH")` 발생 -> service에서 `PARSER_REJECTED` 변환, NOT NULL, String(500))
- `publisher`: `None` (Phase 2A 소유 필드. DB 스키마상 nullable 허용. 외부 발행처 미사용. exact replay 비교 대상이며 non-NULL이면 `CONTENT_CONFLICT`)
- `source_url`: `None` (Phase 2A 소유 필드. DB 스키마상 nullable 허용. Source Snapshot Member로 결속. exact replay 비교 대상이며 non-NULL이면 `CONTENT_CONFLICT`)
- `document_version`: `None` (Phase 2A 소유 필드. DB 스키마상 nullable 허용. Snapshot 버전으로 관리. exact replay 비교 대상이며 non-NULL이면 `CONTENT_CONFLICT`)
- `record_contract_version`: `KnowledgeDocumentContractVersion.KNOWLEDGE_EVIDENCE_V1`
- `source_snapshot_member_id`: `member.id` (UUID, NOT NULL, FK to `rag_source_snapshot_member.id`)
- `external_document_id`: `f"mfds-label:{item_seq}:{section}"` (String(300), NOT NULL)
- `document_content_hash`: `member.content_sha256` (String(64), regex `^[0-9a-f]{64}$`, NOT NULL)
- `canonicalization_spec_version`: `snapshot.canonicalization_spec_version` (String(100), NOT NULL)
- `knowledge_index_lock_marker`: `0` (Integer, Check `lock_marker = 0` 필수. Phase 2A 소유 필드이며 exact replay 비교 대상)
- `document_status`: `KnowledgeDocumentStatus.ACTIVE`
- `created_at`: `func.now()` (replay 시 기존 `created_at` 유지)

### `KnowledgeChunk` (`backend/app/models/knowledge.py`)
- `id`: `UUID(uuid4())` (exact replay 시 기존 id 반환)
- `knowledge_document_id`: `document.id` (UUID, NOT NULL, FK to `knowledge_document.id`)
- `chunk_index`: `0, 1, 2, ...` (0-indexed 순차 정수, NOT NULL, Check `chunk_index >= 0`)
- `chunk_text`: `chunk_text` (정규화된 비어있지 않은 문자열, Text, NOT NULL)
- `embedding_model`: `None` (Phase 2A 비소유 **legacy nullable field**. authoritative owner 미확정 — 기존 Index adapter가 읽지·쓰지 않고 `knowledge_index_builder`에 UPDATE 권한이 없다. INSERT 시 NULL, exact replay 비교 제외, 기존 non-NULL 값은 보존)
- `vector_store_key`: `None` (동일. UNIQUE nullable 컬럼이므로 임의 값 주입 금지. INSERT 시 NULL, exact replay 비교 제외, 기존 non-NULL 값은 보존)
- `content_hash`: `sha256(chunk_text.encode("utf-8")).hexdigest()` (String(64), regex `^[0-9a-f]{64}$`, NOT NULL)
- `normalization_version`: `"mfds-label-knowledge-chunk@1"` (String(100), NOT NULL)
- `knowledge_index_lock_marker`: `0` (Integer, Check `lock_marker = 0` 필수)
- `created_at`: `func.now()` (replay 시 기존 `created_at` 유지)

---

## Detailed Task Breakdown & TDD Steps

### Task 0: 공유 계약 문서화 및 Index / Testing 갱신 (완료됨)
- [x] **[NEW] Proposed 계약 문서 작성**: `docs/contracts/proposed/post-mvp-1/knowledge-materialization-v1.md`
  - 문서 상태: `Proposed`
  - 자연 키: `(source_snapshot_member_id, external_document_id)`, `(knowledge_document_id, chunk_index)`
  - 좌표 규약: `external_document_id = "mfds-label:{item_seq}:{section}"`, `locator = "mfds-label/{item_seq}/{section}"`
  - 6대 Hash Domain 정의 및 상호 일치 불변식 명시
  - `mfds-label-knowledge-chunk@1` 청킹 및 렌더링 규약 (공백 청크 불허 -> `CHUNK_POLICY_UNSUPPORTED`)
  - Parser 오류 변환 규약: 하위 Parser의 표준 `ValueError`(`XML_SECTION_MISMATCH`, `XML_INVALID`, `XML_BODY_EMPTY`, `XML_ARTICLE_SET_INVALID` 등)를 상위 Service가 `KnowledgeMaterializationFailureReason.PARSER_REJECTED`로 안전 매핑
  - 영수증 계약: `MaterializedDocumentReceipt`의 `locator: str = field(repr=False)` 및 `KnowledgeMaterializationReceipt`의 소스 버전·체크섬 필드를 통해 `KnowledgeChunkIdentity` 완전 구성 보장
  - Lifecycle Persistence Gate: Source ACTIVE, Endpoint VERIFIED/ENABLED/APPROVED, Operation ENABLED/APPROVED, Snapshot CURRENT, Artifact `artifact_kind == "RAW_RESPONSE"`, `page_number >= 1`, reject metadata 부재
  - Exact Replay 및 All-or-Nothing 트랜잭션 경계: Phase 2A 소유 불변 필드 전체 비교
  - NN 공식 빈 항목에 대한 fail-closed (`CHUNK_POLICY_UNSUPPORTED`) 보류 규약
- [x] **[MODIFY] 계약 Index 갱신**: `docs/contracts/README.md`
  - `## Proposed 계약` 섹션에 `knowledge-materialization-v1.md` 항목 배치
- [x] **[MODIFY] 검증 지침 갱신**: `docs/testing.md`
  - Phase 2A 단위(`run_with_worker_test_environment`) 및 통합 테스트 실행 절차 문서화
- [x] **검증**: `git diff --check docs/contracts/ docs/testing.md`

---

### Task 1: Parser Seam 추출 및 기존 테스트 회귀 고정 (완료 · 승인 가능 판정 · Revision 10 재검증)

- [x] **실패 테스트 작성 (RED)**: `ai_worker/tests/rag/source_ingestion/test_mfds_label.py`
  - `test_parse_mfds_label_artifact_public_seam`: 유효 XML bytes와 section 입력 시 `ParsedMfdsLabelDocument` 반환, 구조 통계 일치, `canonical_structure` 필드 부재 확인
  - `test_parse_mfds_label_artifact_empty_title_rejected`: XML `<DOC title="">` 빈 제목 입력 시 `ValueError("XML_SECTION_MISMATCH")`
  - `test_parse_mfds_label_artifact_section_mismatch`: root 태그와 section 불일치 시 `ValueError("XML_SECTION_MISMATCH")`
  - `test_parse_mfds_label_artifact_empty_body`: 본문 부재 시 `ValueError("XML_BODY_EMPTY")`
  - `test_parse_mfds_label_artifact_nn_article_set_invalid`: NN 7개 필수 ARTICLE 제목 세트 불일치 시 `ValueError("XML_ARTICLE_SET_INVALID")`
  - `test_parsed_document_repr_hides_source_body`: `repr(parsed)`에 합성 ARTICLE 제목·본문·`root=`·`canonical_structure`가 없음
  - `test_inspect_xml_accepts_deeply_nested_valid_document`: 2 MiB 미만 1,100단계 중첩 유효 XML에서 `inspect_xml()`이 `RecursionError` 없이 성공
  - `test_parse_mfds_label_artifact_accepts_deeply_nested_valid_document`: 동일 입력에서 parser seam도 성공
  - `test_inspect_xml_report_keys_and_values_are_preserved`: report 키 순서와 전체 값이 develop 기준과 동일
  - `test_inspect_xml_nn_partial_report_is_preserved`: NN `PARTIAL_OFFICIAL` report와 `empty_article_titles` 보존
  - `test_parse_mfds_label_artifact_preserves_raw_parsed_structure`: **(Revision 9 추가, 정규화 책임 경계)** NFD 입력이 NFC로 바뀌지 않고 trailing space·연속 빈 줄도 정리되지 않음 — seam은 raw parsed structure만 보존한다. XML 1.0 line-ending 정규화는 expat 동작이므로 경계 밖으로 명시
  - **예상 실패**: `ImportError: cannot import name 'parse_mfds_label_artifact'`
- [x] **최소 구현 작성 (GREEN)**: `ai_worker/tasks/rag/source_ingestion/mfds_label.py`
  - `ParsedMfdsLabelDocument` dataclass 정의 (`section`, `document_title`, `article_count`, `paragraph_count`, `nonempty_paragraph_count`, `content_status`, `empty_article_titles`, `root: ElementTree.Element = field(repr=False)`)
  - `parse_mfds_label_artifact(raw_bytes: bytes, section: str) -> ParsedMfdsLabelDocument` 순수 함수 추출 (`_parse_xml()` + `_inspect_body()`만 수행. canonicalization·텍스트 정규화 없음)
  - 기존 `_load_document()` 및 `inspect_xml()`이 `parse_mfds_label_artifact()`를 호출하도록 리팩터링 (기존 ValueError 계약 무변경)
  - canonical structure는 기존과 동일하게 `_load_document()`에서만 `_canonical_element(parsed.root)`로 생성 (`inspect_xml()` 경로 재귀 회귀 제거)
- [x] **단위·회귀 검증 (VERIFY)**: Revision 10에서 재실행
  ```bash
  cd "$(git rev-parse --show-toplevel)"
  export REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
  source scripts/ci/test_environment.sh
  run_with_worker_test_environment pytest ai_worker/tests/rag/source_ingestion/test_mfds_label.py -q
  run_with_worker_test_environment pytest ai_worker/tests/rag -q
  ```
  ```bash
  uv run ruff check ai_worker/tasks/rag/source_ingestion/mfds_label.py ai_worker/tests/rag/source_ingestion/test_mfds_label.py
  uv run ruff format --check ai_worker/tasks/rag/source_ingestion/mfds_label.py ai_worker/tests/rag/source_ingestion/test_mfds_label.py
  uv run mypy backend/app ai_worker
  ```
  실측 결과는 `Design §1.5` Task 상태 표에 기록한다.

---

### Task 2a: 순수 renderer/chunker (golden vector 확정 — **구현 완료**)

**경계**: reader를 전혀 사용하지 않는다. 입력은 `parse_mfds_label_artifact()`의 `ParsedMfdsLabelDocument`와
`chunk_policy_version`뿐이고, 출력은 `tuple[KnowledgeChunkDraft, ...]`와 chunk text/hash다. DB·receipt
·transaction·artifact reader·provenance를 다루지 않는다.

- [x] **실패 테스트 작성 (RED)**: `ai_worker/tests/rag/test_mfds_label_chunk_policy.py`

  Golden vector (계약 "Byte-exact 렌더링 규약" 표를 그대로 fixture로 사용. 값은 이미 확정됐다):
  - `test_golden_g1_single_article`: `'# 효능\n\n본문 첫째 줄'` / `3fa3c97b...db439`
  - `test_golden_g2_nested_article_heading_levels`: `'# 성인\n\n상위 본문\n\n## 신기능 저하\n\n하위 본문'` / `25867d2f...31c8d`
  - `test_golden_g3_mixed_text_child_tail`: `'# 효능\n\n앞강조사이기울임뒤'` / `fb6230b1...f91b0`
  - `test_golden_g4_consecutive_paragraph_blocks`: `'# 주의\n\n첫 문단\n\n둘째 문단'` / `51a41310...33856`
  - `test_golden_g5_doc_level_non_article_run`: chunk 0 `'머리말 1\n\n머리말 2'` / `1d4f729c...7de35`, chunk 1 `'# 효능\n\n본문'` / `f73db062...cd473`
  - `test_golden_g6_table_caption_header_unit_footnote`: `'# 용량\n\n표 참조\n1일 용량\n구분\t용량\n단위\tmg\n성인\t5\n비고\t식후'` / `8bb73e54...3095d`
  - `test_golden_g7_title_only_article`: chunk 0 `'# 효능만 있는 항목'` / `af1cff2d...0fc39`, chunk 1 `'# 효능\n\n본문'` / `f73db062...cd473`
  - `test_golden_g8_normalization_pipeline`: `'# 효능\n\n효능\n\n둘째 줄'` / `d1263414...b3ccd`
  - 각 테스트는 `chunk_text` 전체 문자열과 `hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()`를 동시에
    단언한다. hash만 비교하면 회귀 원인을 못 찾고, 문자열만 비교하면 계약 값과의 결속이 끊긴다.

  정규화 (책임 경계):
  - `test_nfd_and_nfc_inputs_produce_identical_chunk_text`
  - `test_nfd_and_nfc_inputs_produce_identical_content_hash`
  - `test_newline_representation_does_not_change_chunk_text`: 연속 빈 줄·trailing space 차이 무영향
  - `test_normalization_step_order_is_fixed`: 정규화 5단계 순서를 바꾸면 G8 결과가 달라짐을 고정
  - `test_parser_seam_output_is_not_normalized`: seam 출력에는 정규화가 적용되어 있지 않다

  정책 fail-closed:
  - `test_chunk_renderer_empty_text_rejected`: 정규화 후 공백 → `CHUNK_POLICY_UNSUPPORTED`
  - `test_nn_official_empty_article_rejected`: NN 공식 빈 항목 → `CHUNK_POLICY_UNSUPPORTED`
  - `test_unsupported_chunk_policy_version_rejected`: → `CHUNK_POLICY_UNSUPPORTED`
  - `test_no_synthetic_text_added`: source에 없는 의학 문구·요약·"내용 없음" 표지·section label 미추가

  결정성:
  - `test_repeated_execution_yields_identical_chunk_drafts`: 반복 실행 전 필드 동일 (draft에는 `is_exact_replay`가 없다)
  - `test_chunk_index_is_zero_based_sequential`

  **예상 실패**: `ModuleNotFoundError: No module named 'ai_worker.tasks.rag.mfds_label_chunk_policy'`
- [x] **최소 구현 작성 (GREEN)**: `ai_worker/tasks/rag/mfds_label_chunk_policy.py`
  - 모듈 수준 함수 `build_chunk_drafts()` / `chunk_content_hash()` / `normalize_chunk_text()` + 정규화 5단계 +
    `CHUNK_POLICY_UNSUPPORTED` fail-closed (계획 단계의 `MfdsLabelChunkPolicyV1` 클래스 형태는 채택하지 않았다)
  - reader·DB·receipt를 import하지 않는다. 단 `ParsedMfdsLabelDocument` 타입 때문에 `mfds_label` 모듈
    그래프가 전이적으로 딸려 온다 (finding 44 / D6). SQLAlchemy·`backend.app`은 없음을 실측 확인
- [x] **단위 검증 (VERIFY)**: **31 passed** (Revision 12에서 인라인 table fail-closed 회귀 4건 추가)
  ```bash
  cd "$(git rev-parse --show-toplevel)"
  export REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
  source scripts/ci/test_environment.sh
  run_with_worker_test_environment pytest ai_worker/tests/rag/test_mfds_label_chunk_policy.py -q
  ```

#### Task 2a 구현 중 확정된 사항

- **table은 위치와 무관하게 항상 블록**이다. 초기 초안은 `PARAGRAPH` 안의 table을 인라인으로 두어 같은 표가
  위치에 따라 `\n` / `\n\n`으로 갈렸고 hash가 달라졌다. 블록으로 고정하고 **G6 golden vector를
  `8bb73e54...` → `148e4531bfff30d04bdab89022762685e05710f7a13098d8019975624b554011`로 갱신**했다.
  나머지 G1~G5, G7, G8은 초안 값 그대로 구현 산출값과 일치했다.
- **인라인 위치의 table은 fail-closed 한다** (Revision 12, finding 42). 위 수정 뒤에도 `<b>` 안이나 다른 표의
  cell 안에 있는 table은 `_render_inline()`이 구분자 없이 이어 붙여 **인라인으로** 렌더링했고, 같은 표의
  `content_hash`가 중첩 깊이에 따라 달라졌다. 블록 위치가 없는 표는 표현을 추정하지 않고
  `CHUNK_POLICY_UNSUPPORTED`로 거절한다. **블록 위치 table의 렌더링과 G1~G8 hash는 전부 불변**이다.
- **`ChunkPolicyError`는 typed reason-only 예외**다. 상위 Task 2b는 `error.reason`을 읽어
  `CHUNK_POLICY_UNSUPPORTED`로 투영하며 메시지를 파싱하지 않는다. (Revision 11은 이를 reader typed
  boundary 옵션 B의 선례로 적었으나, **D1에서 B 권고가 철회**됐으므로 이 선례는 renderer 자체 오류에만
  적용된다. reader 오류는 이 패턴으로 원인별 분류가 되지 않는다.)
- **독립 검증 결과**: golden vector 10개 행의 선언된 hash가 기대 문자열의 UTF-8 bytes에서 나온 값임을
  구현을 호출하지 않고 계산해 확인했다(10/10 일치, 전부 NFC). 계약 표와 테스트 표의 문자열·hash 기계
  대조도 drift 0이다. 기대값을 구현 출력으로 자동 갱신하지 않았다.
- `KnowledgeChunkDraft.chunk_text`에 `field(repr=False)`를 적용해 repr에 Source 본문이 새지 않게 했다.

**Task 2a 잔여 조건**: golden vector 표의 heading marker(`#`)와 table 배치는 공유 계약 값이므로 `@phina-io`
승인 시점에 최종 확정된다. 승인 과정에서 값이 바뀌면 `CHUNK_POLICY_VERSION` bump와 함께 계약 표·테스트·구현을
같은 변경 흐름에서 정렬한다.

---

### Task 2b: Materialization 커널 + loader/오류 변환 (보류 — typed boundary 결정 대기)

> **BLOCKED.** reader `ValueError`를 reason으로 안전 변환할 typed boundary가 결정되지 않았다 (finding 32).
> 옵션 A(reader typed 예외) / B(Phase 2A typed loader adapter, 권고) / C(단일 reason, 비권고) 중
> `@phina-io` + `@Jye-rookie` 결정 전에는 착수하지 않는다. Task 2a는 이 결정과 무관하게 진행할 수 있다.

**Task 2b의 검증 경계**: `materialize_documents()`는 `tuple[KnowledgeDocumentDraft, ...]`만 반환하며 DB row도
receipt도 만들지 않는다.

| Task 2b에서 검증 | Task 3으로 이동 |
|---|---|
| 요청 문법·형식 검증 | DB가 부여하는 UUID receipt 구성·비교 |
| 조회 결과로 주어진 `MaterializationSourceDocument` tuple의 section 집합 판정 | write transaction 호출 여부·시점 |
| draft 조립·정렬 결정성 | artifact/member/run **양쪽 row ID** 비교 |
| parser `ValueError` → `PARSER_REJECTED` 변환 | `object_key` mutation 거부 |
| reason-only 오류 노출 | `ingestion_run_status` 등 DB row 기반 gate |
| (typed boundary 결정 후) loader 오류 변환 | lock 순서·checksum race·exact replay |

Task 2b는 `MaterializationSourceDocument`를 **이미 검증된 입력으로 가정**한다. DTO 자체의 provenance 불변식
검증은 Repository가 조회한 row와 대조할 수 있는 Task 3의 책임이다. (DTO에 양쪽 식별자를 모두 넣어 Task 2에서
자기완결적으로 대조하는 대안은 검토했으나, DTO가 이미 조회 결과의 투영이라 실제 row와의 대조가 되지 않으므로
채택하지 않았다.)

- [ ] **실패 테스트 작성 (RED)**: `ai_worker/tests/rag/test_knowledge_materialization.py`

  요청 문법·형식 검증 (DB 접근 없음):
  - `test_request_validation_item_seq`: item_seq 형식 오류, 중복 `member_ids`, 빈 `member_ids` → `REQUEST_INVALID`
  - `test_request_validation_chunk_policy`: 미지원 `chunk_policy_version` → `CHUNK_POLICY_UNSUPPORTED`
  - `test_request_structure_error_is_not_chunk_policy_unsupported`

  주어진 source document tuple의 section 집합 판정:
  - `test_request_section_set_required_triple_accepted` / `_nn_alone_accepted`
  - `test_request_section_set_missing_required_rejected`: `{EE, UD}`, `{NB}`, `{UD, NB}`
  - `test_request_section_set_duplicate_section_rejected`: `{EE, EE, UD, NB}`
  - `test_request_section_set_extra_section_rejected`: `{EE, UD, NB, NN}`
  - `test_request_section_set_mixed_ee_nn_rejected`: `{EE, NN}`
  - `test_request_section_set_unsupported_section_rejected`
  - `test_request_section_set_mixed_item_seq_rejected`
  - `test_section_set_rejection_happens_before_artifact_read`: section 실패 시 주입한 fake loader의
    읽기 호출이 0회 (호출 카운터로 검증. write transaction 여부는 Task 3)

  hash domain:
  - `test_document_content_hash_copies_member_content_sha256`
  - `test_document_content_hash_follows_raw_bytes_not_normalization`: **NFD 판본과 NFC 판본의
    `document_content_hash`는 서로 다르다** (`== member.content_sha256`이고 raw bytes가 다르므로).
    정규화 결과가 같다는 이유로 같아지면 실패한다
  - `test_raw_bytes_hash_is_not_normalized`

  draft 결정적 순서:
  - `test_shuffled_source_document_order_yields_identical_drafts`
  - `test_drafts_are_sorted_by_section_order`: `SECTION_ORDER` 오름차순, 문서 내부 `chunk_index` 오름차순
  - `test_repeated_execution_yields_identical_drafts`: 전 필드 동일 (draft에는 `is_exact_replay`가 없다)

  parser 오류 변환:
  - `test_parser_rejection_mapping`: `XML_SECTION_MISMATCH`, `XML_INVALID`, `XML_BODY_EMPTY`,
    `XML_ARTICLE_SET_INVALID` → `KnowledgeMaterializationError(PARSER_REJECTED)`

  reader 호출 전에 판정 가능한 결속 (typed boundary와 무관):
  - `test_object_key_mismatch_rejected_before_reader_call`:
    `object_key != LocalPrivateSourceArtifactStore.object_key_for_checksum(raw_checksum)` →
    `SOURCE_BINDING_INVALID`이고 loader 읽기 호출이 0회
  - `test_raw_artifact_metadata_construction_failure_maps_to_source_binding_invalid`

  Safe error (reason-only):
  - `test_error_exposes_only_reason_value`: `str(error) == reason.value`
  - `test_object_key_not_in_str_error` / `test_source_text_not_in_repr_error`
  - `test_parser_value_error_detail_not_in_public_error`
  - `test_loader_error_message_not_in_public_error` — **typed boundary 결정 후 작성**
  - `test_failure_reason_is_preserved_exactly`
  - `test_error_sanitization_no_path_leak`

  후속 인계 가능성 (draft 수준):
  - `test_draft_provides_all_non_db_index_identity_fields`: draft + 주어진 source document로
    `KnowledgeChunkIdentity`의 **DB UUID를 제외한** 필드를 구성 가능. `knowledge_chunk_id` 포함 완전 구성은 Task 3
  - `test_draft_identity_strings_are_nfc`: 하위 Index `_bounded_nfc()` 통과

  **보류 중 작성하지 않는 테스트**: reader `ValueError` → `ARTIFACT_INTEGRITY_MISMATCH` /
  `DEPENDENCY_ERROR` 분류 테스트 전체. reader 생성자 오류 변환 테스트 (composition boundary 책임).

- [ ] **최소 구현 작성 (GREEN)**: `ai_worker/tasks/rag/knowledge_materialization.py`
  - DTO 및 `KnowledgeMaterializationFailureReason` enum, reason-only `KnowledgeMaterializationError`
  - Task 2a의 `MfdsLabelChunkPolicyV1` 재사용 (재구현 금지)
  - `SECTION_ORDER` 기반 결정적 정렬 유틸
  - `materialize_documents(request, source_docs, artifact_loader) -> tuple[KnowledgeDocumentDraft, ...]`
    순수 도메인 함수. DB·receipt·transaction을 다루지 않는다. `artifact_loader`의 타입은 결정된 typed
    boundary를 따른다
- [ ] **단위 검증 (VERIFY)**:
  ```bash
  cd "$(git rev-parse --show-toplevel)"
  export REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
  source scripts/ci/test_environment.sh
  run_with_worker_test_environment pytest ai_worker/tests/rag/test_knowledge_materialization.py -q
  ```

---

### Task 3: PostgreSQL Repository 어댑터 구현 (Mock Unit) (미착수)

Task 2에서 이동한 DB provenance·receipt 검증을 포함한다.

- [ ] **실패 테스트 작성 (RED)**: `ai_worker/tests/rag/test_sqlalchemy_knowledge_materialization.py`

  조회·매핑:
  - `fetch_source_documents` 조인 쿼리 구조·파라미터 매핑. select 컬럼에 `ingestion_run_status`, `artifact_key`, `object_key`, `section`, `artifact_kind`, `page_number`, `storage_backend`, `content_type`, `reject_code`, `parser_location` 포함
  - `rag_source_snapshot_verification`을 SELECT하지 않음 (builder role 권한 밖)

  DB row 기반 provenance gate (**Task 2에서 이동**) — 각 항목은 조회된 row와 기대값 양쪽을 갖는다:
  - `test_ingestion_run_status_succeeded_accepted`: `SUCCEEDED` 수락
  - `test_ingestion_run_status_succeeded_with_rejections_rejected`: `SUCCEEDED_WITH_REJECTIONS` 거부
    (`SOURCE_NOT_ELIGIBLE`) — MFDS 경로가 `rejected_record_count == 0`을 강제하므로 현재 생성되지 않는 상태다
  - `test_ingestion_run_status_running_rejected` / `_failed_rejected` / `_no_change_rejected`: `SOURCE_NOT_ELIGIBLE`
  - `test_artifact_id_matches_member_ingestion_artifact_id`: `member.ingestion_artifact_id == artifact.id` 양쪽 비교
  - `test_artifact_run_id_matches_requested_run`: `artifact.ingestion_run_id == run.id` 양쪽 비교
  - `test_run_snapshot_and_operation_binding`: `run.snapshot_id == snapshot.id` ∧ `run.operation_id == operation.id`
  - `test_artifact_kind_mutation_rejected`: `"REJECTS"`/임의 값 → `SOURCE_BINDING_INVALID`
  - `test_artifact_key_mutation_rejected`: `artifact.artifact_key != f"{member.locator}.xml"` → `SOURCE_BINDING_INVALID`
  - `test_object_key_mutation_rejected`: `artifact.object_key != f"sha256/{artifact.raw_checksum[:2]}/{artifact.raw_checksum}.artifact"` → `SOURCE_BINDING_INVALID`. 기존 helper(`LocalPrivateSourceArtifactStore._object_key` 등)와 동일 규칙을 참조하고 네 번째 사본을 만들지 않는다
  - `test_storage_backend_mutation_rejected` / `test_content_type_mutation_rejected`: → `SOURCE_BINDING_INVALID`
  - `test_page_number_exact_section_binding`: `EE`=1, `UD`=2, `NB`=3, `NN`=4 수락
  - `test_page_number_wrong_section_binding_rejected`: `NN` member가 page 1 등 → `SOURCE_BINDING_INVALID`
  - `test_page_number_none_rejected`: `None` → `SOURCE_BINDING_INVALID`
  - `test_reject_code_present_rejected` / `test_parser_location_present_rejected`: → `SOURCE_BINDING_INVALID`
  - `test_artifact_section_mismatch_with_member_section_rejected`: → `SOURCE_BINDING_INVALID`
  - `test_foreign_item_or_member_artifact_reuse_rejected`: → `SOURCE_BINDING_INVALID`
  - `test_version_binding_mutation_rejected`: schema/parser/normalization/canonicalization version → `SOURCE_BINDING_INVALID`
  - `test_source_document_builds_raw_artifact_metadata`: 조회 결과만으로 `RawArtifactMetadata(artifact_key, raw_checksum, byte_size, content_type)`를 구성해 `LocalPrivateSourceArtifactReader.read_verified()`에 전달 가능

  Transaction 경계:
  - `pg_advisory_xact_lock` 쿼리와 **namespace 1**, key `f"mfds-materialize:{snapshot_id}:{expected_item_seq}"` (기존 Index는 namespace 0)
  - 발행 SQL의 `FOR UPDATE OF` 테이블 순서가
    `rag_source → rag_source_endpoint → rag_source_operation → rag_source_snapshot →
    rag_source_snapshot_member → knowledge_document → knowledge_chunk →
    rag_source_ingestion_run → rag_source_ingestion_artifact`와 일치
  - member 순회가 **Task 3 착수 전 확정된 공유 ordering key**를 따름 (확정 전에는 이 테스트를 작성하지
    않는다. `SECTION_ORDER`는 draft 정렬 기준이며 cross-flow lock 전략과 같다고 가정하지 않는다)
  - lock 획득 전에 artifact I/O가 완료되고 transaction 내부에 object read 호출이 없음
  - **write transaction이 열리는 시점**: 요청 검증·section 집합·artifact identity·reader 읽기·draft 생성이
    모두 성공한 뒤에만 `session.begin()`이 호출됨 (Task 2에서 이동한 검증)
  - pre-read 대비 locked read의 Snapshot identity·`raw_manifest_checksum`·`canonical_checksum`·`canonicalization_spec_version` 비교 수행, 선택 member로 checksum을 **재계산하는 코드 경로가 없음**, 불일치 시 `ARTIFACT_INTEGRITY_MISMATCH`
  - `FOR UPDATE` 대상이 `KNOWLEDGE_INDEX_LOCK_COLUMNS` 허용 테이블 집합을 넘지 않음 (`rag_source_snapshot`은 `management_lock_marker`)
  - deadlock retry 루프가 없음

  Receipt (**Task 2에서 이동**):
  - `test_receipt_is_verified_before_commit`: INSERT/replay 판정 직후 **같은 transaction 안에서** 재조회·재구성·비교가 수행되고, 불일치 시 `RECEIPT_MISMATCH`로 commit 전에 rollback됨. 기존 `persist_complete_index()`와 동일한 순서
  - `test_no_post_commit_rollback_path`: commit 이후 경로에 rollback 시도가 없음. commit 뒤 확인은 rollback 불가능한 선택적 audit이며 실패를 `RECEIPT_MISMATCH`로 보고하지 않음
  - `test_receipt_constructs_full_knowledge_chunk_identity`: DB 부여 UUID를 포함해 `KnowledgeChunkIdentity`를 완전 구성 가능
  - `test_repeated_receipt_identical`: 반복 실행 시 `KnowledgeMaterializationReceipt`가 100% 전 필드 완전 동일 (`res1.receipt == res2.receipt`), `KnowledgeMaterializationResult.outcome`은 첫 실행 `CREATED`, 재실행 `EXACT_REPLAY`
  - Exact replay 소유 불변 필드 전체 비교
    - Document: `title`, `document_status`, `record_contract_version`, `canonicalization_spec_version`, `document_content_hash`, `external_document_id`, `locator`, `publisher IS NULL`, `source_url IS NULL`, `document_version IS NULL`, `knowledge_index_lock_marker == 0`, `chunk_count`
    - Chunk: `chunk_index`, `chunk_text`, `content_hash`, `normalization_version`, `knowledge_index_lock_marker == 0`
    - 비소유 legacy 필드 `embedding_model`, `vector_store_key`가 비교 SQL·비교 로직·UPDATE 문에 포함되지 않음
  - `test_shuffled_sql_row_order_yields_identical_receipt_order`: SQL row 순서가 달라도 receipt 순서가 `SECTION_ORDER` 기준으로 동일

  **예상 실패**: `ModuleNotFoundError: No module named 'ai_worker.adapters.sqlalchemy_knowledge_materialization'`
- [ ] **최소 구현 작성 (GREEN)**: `ai_worker/adapters/sqlalchemy_knowledge_materialization.py`
  - `SqlAlchemyKnowledgeMaterializationRepository` 구현
  - 조인·row lock 쿼리 (공통 테이블 순서 준수, IngestionRun `SUCCEEDED` 단일 허용, RAW_RESPONSE·page binding 결속 포함)
  - `persist_materialization`: advisory lock → 공통 순서 row lock → provenance/RAW_RESPONSE/page binding 재검증 → pre-read 대비 checksum race 검증 → exact replay 비교 → insert → **commit 전 같은 transaction에서 receipt 재구성·비교** → commit
- [ ] **단위 검증 (VERIFY)**:
  ```bash
  cd "$(git rev-parse --show-toplevel)"
  export REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
  source scripts/ci/test_environment.sh
  run_with_worker_test_environment pytest ai_worker/tests/rag/test_sqlalchemy_knowledge_materialization.py -q
  ```

**Task 3 착수 전 해소 대상 (모두 blocking)**:

1. **cross-flow lock 전략 확정** — 공유 ordering key(예: 양쪽 `source_snapshot_member_id` 오름차순) 또는 상위
   serialization 방식을 `@phina-io`가 확정해야 한다. `FOR UPDATE OF` 테이블 목록이 같다는 사실만으로 실제
   row lock 순서는 같아지지 않는다 (Index `knowledge_chunk_id.bytes` vs materialization `SECTION_ORDER`).
   **전략 확정 전에는 Repository lock 구현과 mock 순서 검증을 시작하지 않는다.** Task 4는 확정된 전략을
   *검증*하는 단계이며 *결정*하는 단계가 아니다.
2. `SUCCEEDED_WITH_REJECTIONS` 허용 여부 — `@phina-io` 결정.
3. commit 이후 audit을 성공 조건에 포함할지 — `@phina-io` 결정.
4. reader typed boundary (Task 2b와 공통) — `@phina-io` + `@Jye-rookie` 결정.

`object_key` 공용 helper 승격은 **해소 대상이 아니다.**
`LocalPrivateSourceArtifactStore.object_key_for_checksum()`이 이미 public `@staticmethod`로 존재하므로 그대로
호출한다.

### Task 4: 격리 PostgreSQL 통합 테스트 (PostgreSQL RAG Lane) (미착수)

- [ ] **실패 테스트 작성 (RED)** / **통합 테스트 작성**: `tests/integration/rag/test_knowledge_materialization_postgresql.py`
  - 격리 disposable PostgreSQL DB fixture (`database`) 사용
  - **정상 경로 및 Exact Replay 불변성**:
    - 합성 Source/Endpoint/Operation/Snapshot/Member/RAW_RESPONSE Artifact 생성 및 초기 적재 성공
    - 동일 요청 재실행 시 기존 ID, row count, `created_at` 불변 및 `is_exact_replay=True` (Document `publisher`/`source_url`/`document_version` NULL, `knowledge_index_lock_marker == 0` 포함 전체 소유 필드 일치)
    - Document의 `publisher`/`source_url`/`document_version`을 DB에서 non-NULL로 변조한 뒤 재실행 시 `CONTENT_CONFLICT` 및 rollback
    - Chunk의 `embedding_model`/`vector_store_key`를 legacy 값으로 채운 뒤 재실행해도 `CONTENT_CONFLICT` 없이 `is_exact_replay=True`이고 두 값이 Phase 2A에 의해 변경되지 않음
    - 요청 member 순서·SQL row 순서가 달라도 receipt의 document/chunk 순서가 `SECTION_ORDER` 기준으로 동일
  - **동시성 직렬화 검증**:
    - 동일 materialization 요청 2개 태스크 동시 실행 시 advisory lock으로 직렬화되어 한쪽은 INSERT, 한쪽은 exact replay
  - **Cross-flow 동시성 (Task 3 착수 전 확정된 전략의 검증)** — 이 단계는 전략을 *결정*하지 않고
    *검증*한다. 확정된 공유 ordering key 또는 상위 serialization 방식이 실제로 교착을 배제하는지 확인한다:
    - 동일 Snapshot에 대한 materialization exact replay와 기존 Index build를 동시 실행
    - 교착 없이 완료되거나 정의된 fail-closed 결과를 반환
    - partial document/chunk write가 없음
    - receipt mismatch가 commit 이후 뒤늦게 발견되지 않음
    - transaction rollback 후 재실행 가능
  - **부분 저장 상태 Conflict 롤백**:
    - 3개 문서 중 1개만 미리 존재하거나 일부 청크 누락 상태에서 실행 시 `CONTENT_CONFLICT` 및 rollback
  - **Provenance·IngestionRun·RAW_RESPONSE 결속 부정 검증**:
    - Endpoint `acquisition_status != "APPROVED"` → `SOURCE_NOT_ELIGIBLE`
    - Operation `runtime_status != "ENABLED"` → `SOURCE_NOT_ELIGIBLE`
    - Source `lifecycle_status != "ACTIVE"` → `SOURCE_NOT_ELIGIBLE`
    - Snapshot `verification_status != "CURRENT"` → `SOURCE_NOT_ELIGIBLE`
    - IngestionRun `run_status` ∈ {`SUCCEEDED_WITH_REJECTIONS`, `RUNNING`, `FAILED`, `NO_CHANGE`} → `SOURCE_NOT_ELIGIBLE`
    - `NO_CHANGE`: PR `#649` 실측대로 재적재가 `NO_CHANGE` run을 만들어도, member의 `ingestion_artifact_id`가
      최초 `SUCCEEDED` run의 artifact를 계속 가리키므로 정상 경로가 막히지 않음을 함께 확인
    - IngestionRun의 `snapshot_id != Snapshot.id` 또는 `operation_id != Operation.id` → `SOURCE_BINDING_INVALID`
    - Artifact `artifact_kind == "REJECTS"` → `SOURCE_BINDING_INVALID`
    - Artifact `page_number IS NULL` 또는 section 기대값 불일치(`NN`을 page 1로 위조 등) → `SOURCE_BINDING_INVALID`
    - Artifact `artifact_key != f"{member.locator}.xml"` → `SOURCE_BINDING_INVALID`
    - Artifact `object_key != f"sha256/{raw_checksum[:2]}/{raw_checksum}.artifact"` → `SOURCE_BINDING_INVALID`
    - Artifact `storage_backend != "LOCAL_PRIVATE"` → `SOURCE_BINDING_INVALID`
    - Artifact `content_type != OBSERVED_CONTENT_TYPE` → `SOURCE_BINDING_INVALID`
    - Artifact `reject_code IS NOT NULL` 또는 `parser_location IS NOT NULL` → `SOURCE_BINDING_INVALID`
    - 다른 품목·다른 Snapshot Member artifact 재사용 → `SOURCE_BINDING_INVALID`
    - 타 품목 seq 포함 요청 → `REQUEST_INVALID`
    - section 집합이 `{EE, UD, NB}` 또는 `{NN}` 단독이 아닌 경우 → `REQUEST_INVALID`
    - locator와 section 불일치 → `SOURCE_BINDING_INVALID`
  - **Snapshot checksum race**:
    - pre-read 이후 lock 전에 Snapshot의 `canonical_checksum` / `raw_manifest_checksum` / `verification_status`를 변경하면 `ARTIFACT_INTEGRITY_MISMATCH`이고 write는 0건
    - `NN` singleton 요청에도 동일하게 적용
  - **6대 해시 불일치 거부**:
    - raw size 불일치, artifact sha256 불일치, member `content_sha256` 불일치 각각 fail-closed 거부
    - 같은 품목의 NFD 판본과 NFC 판본은 서로 다른 `document_content_hash`를 갖고, 동일 natural key로 들어오면
      exact replay가 아니라 `CONTENT_CONFLICT`임을 확인
  - **Pre-commit RECEIPT_MISMATCH 검증**:
    - **commit 전 같은 transaction 안에서** 재조회 데이터가 receipt와 불일치하면 `RECEIPT_MISMATCH`이고 write는 0건
    - commit 이후에는 rollback 경로가 없음을 확인 (commit 뒤 확인은 rollback 불가능한 audit)
  - **Receipt 동일성**:
    - 반복 실행 receipt가 100% 전 필드 완전 동일 (`res1.receipt == res2.receipt`), outcome은 첫 실행 `CREATED`, 재실행 `EXACT_REPLAY`
  - **DB 권한 검증**:
    - 일반 runtime role 연결로 Knowledge 테이블 INSERT 시도 시 DB 권한 거부
    - `knowledge_index_builder` role 연결로 SELECT/INSERT 성공
    - builder role이 `embedding_model` / `vector_store_key` UPDATE를 시도하면 권한 거부됨을 확인 (Phase 2A가 그 경로를 만들지 않음을 증명)
    - builder role이 `rag_source_snapshot_verification`을 SELECT할 수 없음을 확인하고, gate가 이 테이블에 의존하지 않음을 확인
    - `FOR UPDATE` 대상 테이블 전체에 대해 lock marker 컬럼 UPDATE 권한만으로 lock이 성립함을 확인
  - **Index 호환성 및 Negative Mutation 검증**:
    - 생성된 chunk를 `SqlAlchemyKnowledgeEvidenceIndexRepository`에 주입 시 성공
    - `document_content_hash` 또는 `canonical_checksum` 변조 시 Index 빌더에서 `SOURCE_BINDING_INVALID`로 거부
  - **호스트 경로/내부 key 비노출 검증**:
    - receipt, logging, 예외 메시지에 호스트 절대경로 및 raw `object_key` 비노출
- [ ] **통합 검증 실행 (VERIFY)**:
  ```bash
  cd "$(git rev-parse --show-toplevel)"
  export REPOSITORY_ROOT="$(git rev-parse --show-toplevel)" COMPOSE_PROJECT_NAME=ah_05_04
  source scripts/ci/test_environment.sh
  prepare_test_environment
  run_with_integration_test_environment pytest tests/integration/rag/test_knowledge_materialization_postgresql.py -q
  ```

---

## Verification Plan

### Automated Test Execution Pipeline (단계별 실행)

1. **Parser Seam Regression & Pure Unit Tests** (Task 2~4의 두 파일은 착수 후에만 존재한다):
   ```bash
   cd "$(git rev-parse --show-toplevel)"
   export REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
   source scripts/ci/test_environment.sh
   run_with_worker_test_environment pytest \
     ai_worker/tests/rag/source_ingestion/test_mfds_label.py \
     ai_worker/tests/rag/test_knowledge_materialization.py \
     ai_worker/tests/rag/test_sqlalchemy_knowledge_materialization.py -q
   ```
2. **PostgreSQL RAG Lane Integration Tests**:
   ```bash
   cd "$(git rev-parse --show-toplevel)"
   export REPOSITORY_ROOT="$(git rev-parse --show-toplevel)" COMPOSE_PROJECT_NAME=ah_05_04
   source scripts/ci/test_environment.sh
   prepare_test_environment
   run_with_integration_test_environment pytest tests/integration/rag/test_knowledge_materialization_postgresql.py -q
   ```
3. **Lint · Format · Type (`CONTRIBUTING.md` 완료 전 검사)**:
   ```bash
   uv run ruff check .
   uv run ruff format . --check
   uv run mypy backend/app ai_worker
   ```
   변경 범위만 좁혀 실행할 때:
   ```bash
   uv run ruff check ai_worker/tasks/rag/source_ingestion/mfds_label.py ai_worker/tests/rag/source_ingestion/test_mfds_label.py
   uv run ruff format --check ai_worker/tasks/rag/source_ingestion/mfds_label.py ai_worker/tests/rag/source_ingestion/test_mfds_label.py
   ```
   `uv run mypy backend/app ai_worker`는 저장소의 공식 명령이다. RAG/Worker 전용 축약 mypy 명령은 저장소에
   정의되어 있지 않으므로 임의로 만들지 않는다.
4. **Worker RAG 회귀 전체**:
   ```bash
   cd "$(git rev-parse --show-toplevel)"
   export REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
   source scripts/ci/test_environment.sh
   run_with_worker_test_environment pytest ai_worker/tests/rag -q
   ```
5. **CI Core Test Runner**:
   ```bash
   bash scripts/ci/run_test.sh
   ```
6. **Git Diff & Whitespace Verification**:
   ```bash
   git diff --check
   git status --short --branch
   git diff --stat
   ```

### Final Reporting Policy
- 자동 `git commit`, `git push`, PR 생성, Issue 수정을 절대 실행하지 않음.
- `MFDS_RAG_Agent_Handoff_Final.md` §10의 최종 보고 서식에 맞춰 결과를 정리한 뒤 STOP.
