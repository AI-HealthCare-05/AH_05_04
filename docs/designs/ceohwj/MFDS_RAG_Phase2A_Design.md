# MFDS RAG Phase 2A Source → Knowledge materialization 설계

- 상태: **설계안 / 구현 전**
- 작성일: 2026-09-16 (Asia/Seoul)
- 범위: MFDS Source 참조와 기존 reader·parser를 재사용한 `KnowledgeDocument` / `KnowledgeChunk` 생성·저장
- 비범위: embedding, Index 생성, 검색·RRF·답변 생성, 실제 서버 적재, migration, 환경·권한·데이터 변경
- 구현 Issue: `#634`
- 관련 작업: `Related/Refs #591, #178`; server handoff `#593, #613`
- 구현 담당: 정현우 (`@ceohwj`)
- 단일 책임 reviewer: 송은영 (`@phina-io`) — Backend·DB·Security, Source lifecycle,
  transaction·권한·Index 호환
- specialist evidence: 김지혜 (`@Jye-rookie`) — Worker, parser, private artifact reader 경계

> 이 문서는 구현 지시서가 아니라 Phase 2A 구현 전에 합의할 설계 기준이다. 특히 아래의
> “보류 접점”은 현재 계약으로 추정하지 않는다. 보류 접점과 무관한 순수 변환·저장 설계와
> 로컬 합성 검증은 독립적으로 진행할 수 있다.

## 1. 확인 근거와 현재 상태

### 1.1 실제로 읽은 인계·로드맵 문서

- `/Users/junghyunwoo/Downloads/MFDS_RAG_Agent_Handoff_Final.md`
  - §0~§5 공통 지침과 §6 설계 전용 지시를 확인했다.
- `/Users/junghyunwoo/Downloads/MFDS_17products_Codex_RAG_execution_prompt_with_issues.md`
  - 실제 파일을 열어 전체 Phase 로드맵과 §0 Issue 연결 지도를 확인했다.
  - Phase B의 Source → `KnowledgeDocument` / `KnowledgeChunk` 방향과 제안 좌표를 확인했다.

첨부 문서의 내용은 조사·설계 입력으로 사용했으며, 현재 저장소 코드·계약·Issue/PR 상태보다
상위의 런타임 사실로 간주하지 않았다.

### 1.2 Git 기준점과 working tree

- 확인 시각 기준 원격 `develop`: `4a9a9bfab089e10a82bf2ca3b1364fd0e7f53d60`
  (`#624` 짧은 후속 질문 문맥·안전 응답 개선)
- 현재 로컬 브랜치: `feat/178-ret-h-actual-evaluation`
- 사용자 선행 변경: `tests/integration/rag/test_actual_retrieval_evaluation.py` 수정 상태
- 이 설계 작업은 위 선행 변경을 수정·정리·staging하지 않는다.
- `origin/develop`의 최신 `#624` 변경은 Phase 2A Source materialization과 직접 겹치지 않는다.

### 1.3 관련 Issue 확인 결과

| Issue | 확인 결과 | Phase 2A 판단 |
|---|---|---|
| `#634` | 2026-09-16 생성. 구현 담당 `@ceohwj`, 단일 책임 reviewer `@phina-io`, Worker/parser/artifact specialist evidence `@Jye-rookie`로 기록 | Phase 2A 구현의 주 Issue. reviewer 지정은 승인 완료를 의미하지 않음 |
| `#178` | Knowledge Index/Search/RRF 기반은 병합되었으나 Source materialization 완료 증거는 없음 | 기존 Index 호환 기준의 출처. Phase 2A의 자동 주 Issue로 추정하지 않음 |
| `#591` | 12개 전문의약품 Source 적재 범위. 본문상 Chunk는 별도 작업이며 실제 Source handoff 완료 아님 | 실제 Source 선행 조건과 좌표 후보의 출처 |
| `#593` | readiness·문서·parser command 준비. Source/Endpoint/Operation 실제 값, 실제 적재·재조회, Chunk에 충분한 Snapshot 참조가 미확정 | 서버 실행 blocker |
| `#609` | parser/normalization/Snapshot writer 준비 후 종료. 실제 staging 적재는 수행하지 않음 | 재사용 구현의 출처, 인계 완료 증거는 아님 |
| `#613` | cleanup/권한 경계 준비 중. 실제 mount, finalizer, journal I/O, E2E 미완료라는 코멘트 확인 | 서버 실행 blocker |
| `#180` | Guide evidence handoff 계약 작업 | Phase 2A 직접 범위 아님 |
| `#176`, `#177` | 제품/OTC 관련 열린 결정·작업이 있으나 현재 MFDS OTC Source 적재 계약으로 확정되지 않음 | OTC Source를 있다고 가정하지 않음 |

초기 검색에서는 Phase 2A materialization만을 명시적으로 소유하는 전용 Issue가 없었으며,
이에 따라 `#634`를 생성했다. 구현 PR은 `Part of #634`로 연결하고 `#178`, `#591`, `#593`,
`#613`은 의존성·관련 Issue로만 표기한다. `Part of #178` 또는 `Closes #178`로 쓰지 않는다.

### 1.4 관련 PR과 원격 브랜치 확인 결과

- 열린 PR: `#630`, `#629`, `#626`, `#614`; Phase 2A 구현 PR은 없음.
- `#596` 병합: retrieval runtime, embedding adapter, run/index 경계. Source materialization은 없음.
- `#610` 병합: MFDS label parser, Source Snapshot writer, post-commit requery. 실제 Source 적재는 없음.
- `#615` 병합: safety metric 전용. Phase 2A와 무관.
- `#616` 병합: Source artifact cleanup 경계. 실제 `#591` 적재·server mount·E2E는 없음.
- `#623` 병합: Guide evidence handoff kernel. Phase 2A와 무관하며 canonical hash 계약 의존성을 명시함.
- 관련 원격 브랜치:
  - `origin/feat/591-mfds-16-precheck`
  - `origin/feat/591-mfds-label-source`
  - `origin/feat/613-source-artifact-cleanup`
  - `origin/feat/178-postgresql-evidence-search-rrf`
  - `origin/docs/178-ret-h-handoff`
  - `origin/feat/180-guide-evidence-handoff-contract`
- materialization 또는 chunk 전용의 미병합 원격 구현 브랜치는 확인되지 않았다.

병합 이력이나 과거 감사 문서만으로 실제 Source 인계 완료를 판단하지 않는다. 실제 서버 실행에는
Snapshot 상태·member 좌표·artifact read 경계·DB role을 다시 검증해야 한다.

## 2. 설계 목표와 경계

### 2.1 목표

명시적으로 선택된 MFDS Source Snapshot member의 raw artifact를 기존 보안 reader로 읽고 기존
MFDS label parser로 구조화한 뒤, 결정적인 chunk policy를 적용하여 다음 행을 저장한다.

1. Source member 하나당 `KnowledgeDocument` 하나
2. 해당 문서의 의미 단위마다 순서가 안정적인 `KnowledgeChunk`
3. 기존 Knowledge Index adapter가 추가 변환 없이 검증할 수 있는 provenance와 hash
4. 동일 요청의 재실행은 같은 행·ID를 반환하고, 다른 내용은 덮어쓰지 않는 fail-closed 저장

### 2.2 비목표

- actual Source ingestion 또는 `#591` 완료 선언
- embedding 계산·외부 vector store 기록
- `RagKnowledgeIndex` / `RagKnowledgeIndexMember` 생성
- 검색·RRF·answer/citation 정책 변경
- Source/Endpoint/Operation lifecycle 변경
- 기존 migration, DB role policy, compose/env, 실제 artifact 데이터 변경
- OTC Source 또는 17개 전 제품의 준비 완료 추정
- 현재 Proposed 문서를 Current 계약으로 승격

### 2.3 단계 분리

```text
명시적 Snapshot/member 요청
        │
        ▼
참조 조회 ── raw artifact reader 검증 ── 기존 parser 재사용
        │                                  │
        └────────── 순수 MaterializationDraft ──────────┐
                                                        ▼
                                          짧은 DB transaction
                                 provenance 재검증 → exact replay/insert
                                                        │
                                                        ▼
                                             post-commit receipt 재조회
```

Raw I/O와 parsing은 DB transaction 밖에서 수행한다. 저장 직전에 같은 provenance를 transaction
안에서 다시 확인하여 조회와 저장 사이의 상태 변경을 탐지한다.

## 3. 기존 구현 재사용과 변경 파일

### 3.1 그대로 재사용할 구현

| 기존 파일 / symbol | 사용 목적 |
|---|---|
| `ai_worker/tasks/rag/source_ingestion/mfds_label.py`의 parser·canonical helpers | XML/DTD/entity 처리, NFC/newline 정규화, EE/UD/NB/NN 구조 검증 |
| `MfdsLabelArtifactReader` | materializer가 특정 storage 구현에 결합되지 않는 reader protocol |
| `ai_worker/adapters/local_private_source_artifact_finalizer.py`의 `LocalPrivateSourceArtifactReader` | 실제 byte read, path escape/symlink/world-writable/read-only 검증, raw hash·size 검증 |
| `ai_worker/adapters/sqlalchemy_source_snapshot_repository.py`의 Snapshot/artifact/member receipt 의미 | Source 참조와 post-commit handoff 의미의 기준 |
| `backend/app/models/knowledge.py` | 기존 Document/Chunk 스키마와 unique/check 제약 |
| `ai_worker/tasks/rag/knowledge_evidence_index.py` | `KnowledgeChunkIdentity`, stable coordinate, exact UTF-8 chunk hash 기준 |
| `ai_worker/adapters/sqlalchemy_knowledge_evidence_index.py` | Index가 요구하는 Source lifecycle와 hash/identity 조건 |
| `infra/python/knowledge_index_role_policy.py` | 기존 builder SELECT/INSERT 권한 경계 |

기존 private parser helper를 복사하거나 별도의 XML parser를 만들지 않는다.

### 3.2 구현 시 계획된 변경 파일과 symbol

아래는 설계상 변경 후보이며 이번 설계 작업에서는 수정하지 않는다.

| 파일 | 변경 / 신규 symbol | 책임 |
|---|---|---|
| `ai_worker/tasks/rag/source_ingestion/mfds_label.py` | `ParsedMfdsLabelDocument`, `parse_mfds_label_artifact(raw_bytes, section)` 공개 seam | 현재 `load_mfds_label_plan()`과 materializer가 동일 parser 결과를 공유하게 함. parsing semantics는 변경하지 않음 |
| `ai_worker/tasks/rag/knowledge_materialization.py` (신규) | `KnowledgeMaterializationRequest`, `MaterializationSourceDocument`, `KnowledgeDocumentDraft`, `KnowledgeChunkDraft`, `KnowledgeMaterializationReceipt`, `MfdsLabelChunkPolicyV1`, `KnowledgeMaterializationService` | 입력 검증, 순수 parse/chunk/draft 생성, repository orchestration |
| `ai_worker/adapters/sqlalchemy_knowledge_materialization.py` (신규) | `SqlAlchemyKnowledgeMaterializationRepository` | Source 참조 조회, transaction 내 provenance 재검증, advisory lock, exact replay 또는 insert, receipt 재조회 |
| `ai_worker/admin/mfds_label_materializer.py` (신규, 서버 gate 이후) | standalone entrypoint/config | actual builder credential과 read-only artifact mount를 명시적으로 주입. 일반 AI worker runtime에 builder 권한을 넣지 않음 |
| `ai_worker/tests/rag/source_ingestion/test_mfds_label.py` | 공개 parser seam 회귀 테스트 | 기존 ingestion parser와 materializer parser의 동일성 보장 |
| `ai_worker/tests/rag/test_knowledge_materialization.py` (신규) | pure unit tests | chunking, hash, 입력/출력, fail-closed 동작 |
| `ai_worker/tests/rag/test_sqlalchemy_knowledge_materialization.py` (신규) | repository tests | query/result mapping, error sanitization, exact replay |
| `tests/integration/rag/test_knowledge_materialization_postgresql.py` (신규) | real PostgreSQL tests | transaction, concurrency, 제약, Index 호환 검증 |
| `docs/contracts/proposed/post-mvp-1/knowledge-materialization-v1.md` (신규, 합의 후) | materialization 계약 | identity·chunk policy·lifecycle gate를 공유 계약으로 기록 |
| `docs/contracts/README.md` | proposed 계약 index 추가 | 문서 authority 유지 |
| `docs/testing.md` 또는 기존 RAG testing 문서 | 새 targeted test command | 검증 경로 기록 |

### 3.3 변경하지 않을 파일

- `backend/app/models/knowledge.py`: 현재 필드와 제약으로 EE/UD/NB 기본 경로를 저장할 수 있다.
- `backend/alembic/versions/**`: migration 불필요.
- `infra/python/knowledge_index_role_policy.py`: 현재 builder role이 네 knowledge table의 SELECT/INSERT와
  Source read를 이미 가진다. 권한 확대를 설계하지 않는다.
- `.env*`, compose/deployment 파일, 실제 Source 데이터: 서버 gate가 해소되기 전 변경하지 않는다.
- 기존 Index/Search/Retrieval 구현: Phase 2A가 그 계약에 맞춘다.

## 4. 입력과 출력 계약

### 4.1 요청 입력

`KnowledgeMaterializationRequest`는 다음 값만 외부에서 받는다.

| 필드 | 타입 / 조건 | 의미 |
|---|---|---|
| `snapshot_id` | UUID | materialize할 정확한 Source Snapshot |
| `member_ids` | 중복 없는 UUID tuple | 요청 대상 Source Snapshot member들 |
| `expected_item_seq` | 정확히 9자리 문자열 | 서로 다른 품목의 member 혼합 방지 |
| `chunk_policy_version` | 고정 문자열 | 최초 구현은 `mfds-label-knowledge-chunk@1`만 허용 |

`source_code`, `source_version`, `canonical_checksum`, locator, artifact object key,
`external_document_id`, hash는 요청자가 신뢰 입력으로 제공하지 않는다. DB와 검증된 artifact에서
도출한다. EE/UD/NB는 필수 집합이고 NN은 별도 보류 조건을 만족할 때만 포함한다.

### 4.2 repository가 조회하는 Source 입력

`MaterializationSourceDocument`는 최소 다음 immutable 사실을 가진다.

- Source: `source_id`, `source_code`, lifecycle status
- Endpoint/Operation: verified/enabled/acquisition status
- Snapshot: `snapshot_id`, `source_version`, `raw_manifest_checksum`, `canonical_checksum`,
  parser/normalization/canonicalization versions, status
- Member: `member_id`, `member_kind`, `locator`, `content_sha256`, artifact binding
- Artifact receipt: `artifact_id`, private object reference, byte size, content type,
  `raw_sha256`, persistence status

Private object reference는 `repr=False`로 취급하고 로그·receipt·예외 메시지에 출력하지 않는다.

### 4.3 저장 출력

`KnowledgeMaterializationReceipt`는 raw text나 storage 경로 없이 다음을 반환한다.

- `snapshot_id`, `source_code`, `source_version`, `snapshot_canonical_checksum`
- policy/canonicalization version
- 문서별:
  - `source_snapshot_member_id`
  - `knowledge_document_id`
  - `external_document_id`
  - `document_content_hash`
  - `chunk_count`
  - 순서가 고정된 `(knowledge_chunk_id, chunk_index, content_hash)` tuple
- `created` 또는 `exact_replay`

Commit 뒤 새 session으로 receipt를 재조회하여 DB에 실제 저장된 identity와 hash가 반환값과
일치하는지 확인한다.

## 5. identity와 hash 의미

### 5.1 identity

- Document natural key: `(source_snapshot_member_id, external_document_id)`
- Chunk natural key: `(knowledge_document_id, chunk_index)`
- 기존 Index stable coordinate:
  `(source_code, source_version, external_document_id, chunk_index)`
- `knowledge_document_id`, `knowledge_chunk_id`는 DB UUID이며 재실행 시 기존 값을 반환한다.
- `external_document_id` 후보: `mfds-label:{ITEM_SEQ}:{EE|UD|NB|NN}`
- Source member locator 후보: `mfds-label/{ITEM_SEQ}/{EE|UD|NB|NN}`

마지막 두 좌표는 `#591` validation 문서의 “적재 전 좌표 후보”이며 Current 계약이 아니다.
형식 승인과 구현 Issue 기록 전에는 persistence를 활성화하지 않는다. 순수 parse/chunk draft는
승인과 독립적으로 구현·검증할 수 있다.

### 5.2 hash domain 분리

| 값 | 입력 byte / 의미 | 생성·검증 규칙 |
|---|---|---|
| Artifact `raw_sha256` | 저장된 원본 artifact의 정확한 raw bytes | 기존 reader가 read 시 재계산하고 artifact receipt와 비교 |
| Snapshot `raw_manifest_checksum` | Snapshot 전체 raw artifact metadata manifest | member 하나의 hash로 재계산하거나 대체하지 않음 |
| Snapshot `canonical_checksum` | 기존 MFDS parser가 만든 품목 전체 canonical structure manifest | 문서 hash나 chunk hash로 재사용하지 않음 |
| `SnapshotMember.content_sha256` | 해당 member raw bytes | `raw_sha256` 및 실제 read bytes SHA-256과 모두 같아야 함 |
| `KnowledgeDocument.document_content_hash` | Index가 Source member와 결합할 때 쓰는 hash | 현재 스키마/Index 계약에 맞춰 member `content_sha256`을 그대로 복사. 저장 전 실제 raw bytes 재검증 필수 |
| `KnowledgeChunk.content_hash` | 최종 저장 `chunk_text`의 정확한 UTF-8 bytes | `sha256(chunk_text.encode("utf-8")).hexdigest()` |

`document_content_hash`라는 이름을 canonical text hash로 재해석하지 않는다. 현재 Index adapter는
`document_content_hash == SnapshotMember.content_sha256`를 요구한다. 별도의 문서 canonical hash가
필요해지면 새 계약·필드·migration 문제이므로 Phase 2A에서 추정하지 않는다.

## 6. parser 재사용과 chunking policy

### 6.1 parser seam

현재 `load_mfds_label_plan()` 내부의 실제 parsing 경로를 public pure function으로 추출한다.

```python
parse_mfds_label_artifact(raw_bytes: bytes, section: str) -> ParsedMfdsLabelDocument
```

이 함수는 기존과 동일하게 다음을 보장한다.

- XML declaration/encoding, 허용 DTD/entity 처리
- NFC 및 newline 정규화
- element/attribute/text/tail/source order 보존
- `data-*` attribute 제외라는 기존 canonicalization 규칙
- EE/UD/NB 필수 구조와 NN의 정확한 7개 ARTICLE title 검증
- title-only ARTICLE을 parser 오류나 누락으로 바꾸지 않음

`load_mfds_label_plan()`도 이 public seam을 호출하게 하여 Source ingestion과 materialization이
서로 다른 parser 사본을 갖지 않게 한다.

### 6.2 `mfds-label-knowledge-chunk@1` 제안

이 절은 구현 가능한 구체안이지만 아직 승인된 공유 계약은 아니다.

1. Source member 하나를 Document 하나로 매핑한다.
2. parser의 `DOC` 바로 아래 자식 element를 source order로 순회한다.
3. 각 top-level `ARTICLE`을 하나의 의미 chunk로 만든다. nested ARTICLE은 부모 chunk 안에서
   원래 순서와 heading hierarchy를 유지한다.
4. `DOC` 바로 아래의 연속된 non-ARTICLE content는 인접 ARTICLE과 합치지 않고 하나의 별도
   top-level block chunk로 만든다. 공백만 있는 block은 만들지 않는다.
5. v1은 token/character 수에 따른 2차 분할과 overlap을 하지 않는다. source semantic boundary를
   임의 길이보다 우선한다.
6. `chunk_index`는 위 top-level block의 source order를 따라 0부터 연속 증가한다.
7. chunk text renderer:
   - ARTICLE title은 첫 줄 heading으로 그대로 출력한다.
   - paragraph/block 경계는 `\n`, 서로 다른 의미 block 경계는 `\n\n`으로 표현한다.
   - inline element는 text와 tail을 source order로 연결한다.
   - table은 row마다 한 줄, cell마다 `\t`로 표현하며 caption/header/unit/footnote의 source order를
     보존한다.
   - parser가 이미 만든 NFC text를 사용하며, CRLF/CR은 LF로 통일한다.
   - 각 line의 trailing whitespace를 제거하고 연속된 빈 line은 최대 1개로 줄인다.
   - 전체 결과의 leading/trailing whitespace를 제거한다.
   - source에 없는 의학적 문구, 요약, “내용 없음” 표지, section label을 추가하지 않는다.
8. 최종 `chunk_text`가 빈 문자열이면 저장하지 않고 정책 오류로 처리한다.
9. Document `canonicalization_spec_version`은 Source Snapshot의 기존 canonicalization version을
   기록한다. Chunk `normalization_version`은 `mfds-label-knowledge-chunk@1`을 기록한다.
10. 모든 chunk hash는 renderer 완료 후 최종 문자열에서 계산한다.

### 6.3 NN 공식 빈 항목 보류

NN parser는 공식 문서의 title-only ARTICLE을 합법적인 `PARTIAL_OFFICIAL` 상태로 보존한다.
그러나 현재 Knowledge schema에는 “공식 빈 본문”과 “실수로 누락된 chunk”를 구분할 metadata가
없다. 임의의 `[공식 원문 본문 없음]` 문구를 chunk에 넣으면 Source에 없는 text가 검색 근거가 되고,
빈 chunk를 저장하면 기존 exact content 검증의 의미가 약해진다.

따라서 다음처럼 범위를 나눈다.

- EE/UD/NB: v1 독립 구현·저장 가능
- 본문이 있는 NN ARTICLE: 위 renderer로 draft 생성 가능
- 하나라도 공식 빈 NN ARTICLE이 있는 NN member: Document/Chunk persistence를
  `CHUNK_POLICY_UNSUPPORTED`로 보류
- 이 보류는 동일 품목의 EE/UD/NB 설계를 중단시키지 않는다. 실행 request에서 NN을 분리한다.

NN representation은 계약 owner가 다음 중 하나를 명시적으로 결정해야 한다: metadata 필드 추가,
별도 상태 행, source text가 아닌 marker의 명시적 허용, 또는 빈 ARTICLE의 Document-only 표현.

### 6.4 policy 변경 규칙

현재 Index stable coordinate에는 chunk policy version이 포함되지 않는다. 같은 `source_version`과
`external_document_id`에 다른 분할 결과를 쓰면 같은 coordinate의 content 의미가 바뀐다.
따라서 v1으로 materialize한 Source version을 in-place rechunk하지 않는다. 향후 size split/overlap/
renderer 변경은 coordinate versioning 방식을 먼저 공유 계약으로 결정한 뒤 새 policy로 진행한다.

## 7. 저장 transaction과 동시성

### 7.1 transaction 전

1. 요청 UUID, 중복, item sequence, policy version을 검증한다.
2. read-only query로 Snapshot/member/artifact receipt를 가져온다.
3. 모든 member가 요청 Snapshot 소속이고 `ARTIFACT` kind이며 locator 품목이 같고 EE/UD/NB 집합이
   완전한지 확인한다.
4. 기존 `LocalPrivateSourceArtifactReader.read_verified()`로 raw bytes를 읽는다.
5. 실제 byte size와 SHA-256을 artifact receipt 및 member hash와 비교한다.
6. 기존 parser seam과 v1 renderer로 모든 Document/Chunk draft를 메모리에서 만든다.

어느 한 단계라도 실패하면 DB write transaction을 열지 않는다.

### 7.2 원자적 저장

한 request의 EE/UD/NB 문서와 chunk를 하나의 짧은 transaction으로 저장한다.

1. `snapshot_id + expected_item_seq`에서 안정적으로 만든 PostgreSQL transaction advisory lock을
   획득한다. 기존 Index builder와 같은 namespace를 재사용하지 않도록 별도 상수 namespace를 둔다.
2. member UUID 오름차순으로 Source/Endpoint/Operation/Snapshot/Member/Artifact를 다시 조회하고
   필요한 row lock을 획득한다.
3. transaction 전 조회와 동일한 snapshot version/checksum, member locator/hash, artifact binding인지
   재검증한다.
4. persistence lifecycle gate를 검증한다.
5. natural key로 기존 Document/Chunk를 조회한다.
6. 행이 없으면 Document를 먼저 insert하고 그 ID로 Chunk를 `chunk_index` 순서대로 insert한다.
7. 행이 있으면 모든 immutable field, chunk count/index/text hash/normalization version을 비교한다.
8. exact match이면 update 없이 기존 ID를 반환한다.
9. 하나라도 다르면 `CONTENT_CONFLICT`로 전체 rollback한다. 기존 행을 update/delete하지 않는다.
10. 새 행과 exact replay가 섞인 부분 적재 상태도 허용하지 않는다. 기존 집합이 요청 전체와 정확히
    같거나, 요청 전체가 새로 insert되는 경우만 성공한다.

Unique constraint race는 advisory lock이 1차로 직렬화한다. 방어적으로 insert는 conflict 후 동일
transaction에서 재조회하여 exact replay만 허용한다. unexpected exception, cancellation,
parser/hash mismatch 모두 요청 전체 rollback이다.

### 7.3 lifecycle gate

순수 parse/chunk draft는 명시적 Snapshot/member와 검증된 raw bytes가 있으면 CURRENT 여부와 분리해
테스트할 수 있다. 반면 `ACTIVE` KnowledgeDocument를 실제 저장하면 즉시 Index 후보가 되므로,
기본 제안은 기존 Index source binding과 같은 조건을 persistence 시점에 요구하는 것이다.

- Source `ACTIVE`
- Endpoint `VERIFIED`, runtime `ENABLED`, acquisition `APPROVED`
- Operation runtime `ENABLED`, acquisition `APPROVED`
- Snapshot `CURRENT`
- member/artifact origin과 checksum 유효

이 조건은 안전한 제안이지 현재 materialization 전용 계약으로 확정된 사실은 아니다. 계약 owner가
“materialize는 가능하지만 Index 불가” 중간 상태를 원한다면 현재 `KnowledgeDocumentStatus`만으로
표현 가능한지 먼저 결정해야 한다. 합의 전에는 pure draft·synthetic DB 검증까지만 진행하고 actual
persistence entrypoint를 활성화하지 않는다.

## 8. 멱등성과 실패 의미

### 8.1 멱등성

동일한 Snapshot/member/policy 요청은 다음을 보장한다.

- Document/Chunk row count 불변
- 기존 UUID 불변
- 같은 receipt identity/hash 반환
- `created_at` 변경 없음
- update/delete 수행 없음
- 두 concurrent request 중 하나가 insert한 뒤 다른 하나는 exact replay로 종료

멱등성은 natural key가 같다는 사실만으로 인정하지 않는다. 문서 hash, external ID,
canonicalization version, chunk index/text/hash/normalization version의 완전한 동일성이 필요하다.

### 8.2 내부 오류 분류

초기 구현은 public API를 추가하지 않고 다음 내부 reason을 사용한다.

| reason | 조건 |
|---|---|
| `REQUEST_INVALID` | UUID/중복/item sequence/member 집합 오류 |
| `SOURCE_BINDING_INVALID` | Snapshot/member/artifact 소속·locator·origin 불일치 |
| `SOURCE_NOT_ELIGIBLE` | 합의된 persistence lifecycle gate 불충족 |
| `ARTIFACT_INTEGRITY_MISMATCH` | byte size/raw/member checksum 불일치 |
| `PARSER_REJECTED` | 기존 parser가 공식 구조를 수용하지 못함 |
| `CHUNK_POLICY_UNSUPPORTED` | NN 공식 빈 항목 또는 지원하지 않는 policy/구조 |
| `CONTENT_CONFLICT` | 같은 natural key에 다른 immutable content가 존재 |
| `RECEIPT_MISMATCH` | commit 후 재조회 결과가 저장 receipt와 불일치 |
| `DEPENDENCY_ERROR` | DB/storage의 분류되지 않은 안전한 실패 |

예외와 로그에는 raw content, object key/path, DB URL, credential을 포함하지 않는다. UUID·source code·
안전한 reason·hash의 짧은 prefix만 structured log allowlist로 허용한다.

## 9. 기존 Index 호환 검증

저장 결과는 별도 변환 없이 `KnowledgeChunkIdentity`를 구성할 수 있어야 한다.

| Index identity 필드 | Phase 2A 출처 |
|---|---|
| `knowledge_chunk_id` | 저장된 Chunk UUID |
| `source_snapshot_id` | request/검증된 Snapshot |
| `source_snapshot_member_id` | Document의 Source member FK |
| `source_code`, `source_version`, `snapshot_canonical_checksum` | transaction에서 재검증한 Source/Snapshot |
| `external_document_id` | 승인된 deterministic document identity |
| `chunk_index`, `chunk_content_hash` | 저장된 Chunk |
| `source_member_locator` | 검증된 member locator |

호환 acceptance 조건:

- Document contract version은 `KNOWLEDGE_EVIDENCE_V1`, status는 `ACTIVE`.
- `document_content_hash == SnapshotMember.content_sha256`.
- `sha256(chunk_text UTF-8) == KnowledgeChunk.content_hash`.
- Snapshot/member/source lifecycle 조건이 Index adapter 검증과 일치.
- stable coordinate 중복이 없고 순서가 결정적.
- 합성 embedding/vector key로 기존 `persist_complete_index()` integration test를 통과.

Phase 2A는 embedding 값을 Document/Chunk에 기록하지 않는다. Index build 단계가 별도 adapter를 통해
embedding을 만들고 완전한 Index transaction을 소유한다.

## 10. 테스트 설계

### 10.1 parser 회귀

- 기존 ingestion fixture를 public parser seam으로 읽은 결과가 기존 plan 결과와 동일하다.
- XML encoding/DTD/entity/NFC/newline/data attribute 처리 결과가 바뀌지 않는다.
- EE/UD/NB 구조 및 NN 7개 exact title 검증을 그대로 유지한다.
- malformed XML, DOCTYPE/entity 위반, section mismatch는 기존 reason을 유지한다.

### 10.2 pure chunking

- 같은 parsed tree + 같은 policy는 byte-identical chunk text/index/hash를 만든다.
- ARTICLE source order와 nested heading order를 보존한다.
- paragraph 및 inline text/tail이 중복·누락되지 않는다.
- table caption/header/unit/row/cell/footnote 순서를 보존하고 `\t`/`\n` 규칙이 고정된다.
- title-only ARTICLE은 source title만 있는 결정적 결과를 만들되, 빈 최종 text는 거부한다.
- source에 없는 설명·요약·marker가 추가되지 않는다.
- NN 공식 빈 항목은 `CHUNK_POLICY_UNSUPPORTED`; EE/UD/NB draft는 독립적으로 성공한다.
- unsupported policy version은 fail-closed.

### 10.3 reader와 provenance

- snapshot에 속하지 않는 member, 다른 품목 혼합, 중복 member, 필수 section 누락을 거부한다.
- member locator의 section과 parser section 불일치를 거부한다.
- artifact size/raw hash/member hash 중 하나라도 다르면 write가 0건이다.
- path escape, symlink, writable artifact, root outside는 기존 reader가 거부하며 path가 로그에 새지 않는다.
- snapshot canonical checksum을 document/chunk hash로 잘못 사용하는 mutation test가 실패한다.

### 10.4 repository와 transaction

- fresh request는 3개 Document와 기대 Chunk를 한 transaction으로 insert한다.
- 두 번째 동일 request는 row count/UUID/created_at 불변인 exact replay다.
- 같은 natural key의 다른 document hash, chunk text/hash/count/index/policy는 `CONTENT_CONFLICT`다.
- 중간 Chunk insert 실패 시 그 request의 Document/Chunk가 모두 rollback된다.
- 기존 부분 집합만 있는 비정상 DB 상태는 나머지를 채우지 않고 conflict다.
- 두 concurrent request는 한 집합만 만들고 두 receipt의 UUID/hash가 같다.
- transaction 전 조회 후 Snapshot/member 상태나 hash를 바꾼 race는 저장 전 재검증에서 실패한다.
- lifecycle gate 각 조건의 실패가 `SOURCE_NOT_ELIGIBLE`이고 write는 0건이다.
- commit 뒤 receipt mutation/누락은 `RECEIPT_MISMATCH`다.

### 10.5 Index compatibility integration

합성 PostgreSQL fixture에서 Phase 2A repository로 rows를 만든 뒤 그 receipt로
`KnowledgeChunkIdentity`와 기존 Index draft를 구성한다.

- 정상 결과는 기존 Index repository의 full source binding 검증과 commit을 통과한다.
- Document hash를 member hash와 다르게 mutation하면 실패한다.
- Chunk text 또는 hash만 mutation하면 exact UTF-8 검증에서 실패한다.
- locator/external ID/chunk index/snapshot checksum mutation이 각각 실패한다.
- 일반 runtime role은 materialization insert를 못 하고 builder role만 기존 허용 범위에서 성공한다.

### 10.6 검증 명령 계획

구현 PR에서는 가장 작은 순서로 실행한다.

1. `uv run pytest ai_worker/tests/rag/source_ingestion/test_mfds_label.py -q`
2. `uv run pytest ai_worker/tests/rag/test_knowledge_materialization.py -q`
3. repository unit tests
4. repository가 정의한 PostgreSQL RAG integration lane
5. `bash scripts/ci/run_test.sh`
6. 필요 시 `bash scripts/ci/run_integration_test.sh`
7. `git diff --check`와 전체 diff 검토

실제 명령·경로는 구현 시 `docs/testing.md`의 최신값을 다시 확인한다. 외부 MFDS API나 실제
credential은 unit/integration test에 사용하지 않는다.

## 11. 로컬 합성 구현과 서버 권한 단계

### 11.1 지금 독립적으로 가능한 로컬 합성 범위

- public parser seam 추출과 기존 parser 회귀 고정
- pure request/draft/receipt 타입과 v1 renderer
- in-memory verified reader fixture를 이용한 hash·chunk unit tests
- SQLAlchemy repository와 isolated PostgreSQL transaction/concurrency tests
- synthetic Source/Endpoint/Operation/Snapshot/Member/Artifact rows를 이용한 Index 호환 test
- proposed materialization contract 초안 작성

위 범위는 실제 MFDS artifact, 서버 mount, production credential 없이 구현 가능하다.

### 11.2 서버 권한·실데이터가 필요한 범위

- `#591` actual Source ingestion 및 새 session requery로 확정된 Snapshot/member receipt 확보
- `#593`의 실제 Source/Endpoint/Operation ID와 lifecycle/acquisition 상태 승인
- `#613`의 read-only artifact consumer mount, OS identity, journal/finalizer/cleanup E2E 확인
- standalone materializer 실행 주체가 기존 knowledge builder DB credential을 안전하게 받는지 확인
- reader root와 DB builder role이 일반 AI worker runtime에 함께 노출되지 않는 실행 경계 승인
- actual artifact read → parse → draft의 dry-run evidence
- 합의된 lifecycle gate 하의 actual persistence 승인
- persistence 뒤 별도 session 재조회와 Index compatibility evidence

서버 조건이 해소되기 전에는 actual 데이터 행을 만들지 않으며, 합성 성공을 Source handoff 완료로
보고하지 않는다.

## 12. 구현 순서와 각 단계의 종료 조건

### 단계 0 — governance와 계약 접점 고정

- `#634`에 구현 담당 `@ceohwj`, 단일 책임 reviewer `@phina-io`, specialist evidence
  `@Jye-rookie`를 기록했다. reviewer 지정은 실제 review 승인과 구분한다.
- `external_document_id`/locator 후보, lifecycle persistence gate, v1 chunk policy를 Proposed 계약에
  기록하고 affected owner 검토를 받는다.
- NN 공식 빈 항목 표현은 별도 결정으로 남겨도 EE/UD/NB 구현은 진행한다.

종료 조건: 구현 PR이 참조할 Issue와 reviewer, EE/UD/NB 좌표·policy가 문서에 명시됨.

### 단계 1 — parser seam과 pure materializer

- 기존 parser를 public seam으로만 추출하고 회귀 test를 먼저 고정한다.
- pure input validation, renderer, draft/hash/receipt 타입을 구현한다.
- storage/DB import가 없는 unit test로 결정성과 fail-closed 동작을 증명한다.

종료 조건: 기존 ingestion 결과 무변경, pure tests 통과, NN 보류가 명시적 reason으로 분리됨.

### 단계 2 — synthetic PostgreSQL persistence

- Source read model과 transaction repository를 구현한다.
- advisory lock, provenance revalidation, all-or-nothing insert, exact replay/conflict를 검증한다.
- update/delete 경로를 만들지 않는다.

종료 조건: transaction·rollback·concurrency·idempotency integration tests 통과.

### 단계 3 — 기존 Index 호환 검증

- materialization receipt로 기존 `KnowledgeChunkIdentity`를 구성한다.
- 기존 Index adapter의 source/hash/identity 검증을 그대로 통과시키고 negative mutation tests를
  추가한다.

종료 조건: 별도 compatibility shim 없이 정상/negative integration tests 통과.

### 단계 4 — 서버 gate 해소와 entrypoint

- `#591/#593/#613` actual handoff·mount·role 증거를 확인한다.
- 일반 runtime과 분리된 standalone entrypoint를 활성화한다.
- actual dry-run은 read/parse/hash/계획 출력만 하고 DB write를 하지 않는다.

종료 조건: private 경로·raw text를 노출하지 않는 dry-run receipt와 권한 증거 확보.

### 단계 5 — 승인된 actual persistence

- 승인된 정확한 Snapshot/member 요청만 실행한다.
- commit 후 새 session requery, row count/hash/identity receipt, Index compatibility를 확인한다.
- actual data·server evidence는 지정된 private/approved 위치에만 기록한다.

종료 조건: 지정 reviewer가 Source handoff와 materialization receipt를 승인. 이는 embedding/Index build
완료나 Phase 2 전체 완료를 의미하지 않는다.

각 단계는 별도 작은 PR로 나눌 수 있으나 migration·환경·실데이터를 끼워 넣지 않는다. 구현 중에도
자동 commit/push/원격 게시를 전제로 하지 않는다.

## 13. 미확정 결정과 보류 접점

| 결정 | 현재 근거 | 보류 범위 | 독립 진행 가능 범위 |
|---|---|---|---|
| Phase 2A의 주 Issue와 reviewer | `#634`; 구현 `@ceohwj`, 단일 책임 reviewer `@phina-io`, specialist `@Jye-rookie` 지정. 실제 review 승인은 아직 없음 | reviewer 승인 전 병합 및 실제 서버 persistence | 설계·로컬 구현·검증 |
| `external_document_id`/locator의 계약 지위 | `#591` precheck의 후보 | DB persistence 활성화 | parser/chunk draft |
| persistence lifecycle gate | Index gate는 존재, materialization 전용 계약 없음 | actual/ACTIVE 저장 | pure draft, synthetic 양방향 test |
| NN 공식 빈 ARTICLE 표현 | parser는 보존, Knowledge schema는 상태 표현 없음 | 해당 NN member 저장 | EE/UD/NB 전체, nonempty NN draft |
| chunk policy 향후 versioning | stable coordinate에 policy version 없음 | 같은 Source version의 rechunk | 고정 v1 및 mutation tests |
| actual 12 Rx/OTC Source handoff | `#591/#593/#613` 미완료 | actual run | 합성 Source fixture |
| standalone server execution identity | builder role policy는 있으나 mount/실행 경계 미확정 | actual entrypoint 배포 | DI 기반 service/repository |
| Proposed 계약의 Current 승격 | 구현·migration/OpenAPI/test/evidence 동반 원칙 | 승격 | Proposed 문서와 구현 증거 준비 |

보류 접점은 fail-open 기본값으로 채우지 않는다. 특히 실제 Source가 있다고 가정하거나,
`document_content_hash`를 canonical hash로 바꾸거나, NN 빈 본문에 합성 문구를 넣지 않는다.

## 14. Phase 2A 완료 정의

Phase 2A 구현 완료는 다음 모두가 fresh evidence로 충족될 때만 주장할 수 있다.

- `#634`에 구현 담당과 단일 책임 reviewer가 명시되고, Proposed materialization 계약에 대한 실제
  reviewer 승인이 있다.
- Source ingestion과 동일 parser·보안 reader를 실제로 재사용한다.
- EE/UD/NB의 Document/Chunk가 결정적으로 생성되고 한 transaction으로 저장된다.
- exact replay, content conflict, rollback, concurrency가 검증된다.
- Document/member hash와 Chunk/text hash가 기존 Index adapter 검증을 통과한다.
- 실제 서버 실행을 주장한다면 `#591/#593/#613` handoff·mount·role 증거와 post-commit requery가 있다.
- NN 미확정 경로가 조용히 누락되지 않고 명시적 보류로 남는다.
- embedding/Index/Search 완료를 Phase 2A 완료와 혼동하지 않는다.

현재 시점의 결론은 **설계 및 로컬 합성 구현 준비 완료, actual Source handoff와 공유 계약 접점은
미확정**이다.
