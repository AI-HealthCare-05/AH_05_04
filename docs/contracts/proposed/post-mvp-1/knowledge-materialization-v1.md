# Knowledge Materialization 계약 v1

## 상태와 범위

- 상태: Proposed · 구현 브랜치 검증 중 · Current 아님
- 추적: Issue #634 선행 저장 기반. 이 계약만으로 #634 또는 Track F를 완료하지 않는다.
- 구현 담당: 정현우 (`@ceohwj`)
- 단일 책임 리뷰어: 김지혜 (`@Jye-rookie`) 1명
- 전문 검토 근거: Backend·DB·Security 송은영 (`@phina-io`)의 의견 및 트랜잭션·DB 권한 검토 근거를 첨부하되 추가 필수 PR 리뷰어로 지정하지 않는다.
- 공개 상태: `PUBLIC_TRACK_F=false` 유지

이 계약은 승인된 Source Snapshot Member(MFDS 품목허가 상세 XML: 필수 EE·UD·NB 및 선택 NN)로부터 정규화된 `KnowledgeDocument` 및 `KnowledgeChunk`를 PostgreSQL에 원자적으로 구체화(Materialize)하여 불변 근거 데이터로 적재하는 내부 Worker 계약이다. 수집·다운로드, Index 구축, Embedding provider 호출, Lexical/Dense 검색, RRF, Citation 생성, 환자 대면 API 노출은 범위 밖이다.

---

## 좌표 및 자연 키 계약

| 엔티티 | 자연 키 (Natural Key) | 좌표 규약 (Coordinate Convention) |
|---|---|---|
| `KnowledgeDocument` | `(source_snapshot_member_id, external_document_id)` | `external_document_id = "mfds-label:{item_seq}:{section}"`<br>`locator = "mfds-label/{item_seq}/{section}"` |
| `KnowledgeChunk` | `(knowledge_document_id, chunk_index)` | `chunk_index`: 0 이상의 순차 정수 (0-indexed) |

- `item_seq`는 9자리 숫자(`^[0-9]{9}$`) 정규식과 완전히 일치해야 한다.
- `section`은 MFDS 상세 라벨 필수 섹션인 `EE`(효능효과), `UD`(용법용량), `NB`(사용상의주의사항) 또는 선택 섹션인 `NN`(e약은요 정보) 중 하나여야 한다 (`EE | UD | NB | NN`).
- **요청 형태 및 섹션 분리 규약 (허용 집합 고정)**:
  - 한 `KnowledgeMaterializationRequest`의 `member_ids`가 지시하는 section 집합은 정확히 다음 두 형태 중 하나여야 한다. 그 외 모든 형태는 `REQUEST_INVALID`로 거절한다.
    1. 동일 `expected_item_seq` 품목의 정확한 `{EE, UD, NB}` (3개, 중복 없음)
    2. 동일 `expected_item_seq` 품목의 정확한 `{NN}` 단독 (1개)
  - 따라서 다음은 모두 `REQUEST_INVALID`이다.
    - `EE`/`UD`/`NB` 중 하나 이상 누락 (예: `{EE, UD}`, `{NB}`)
    - 같은 section을 가리키는 member 중복 (예: `{EE, EE, UD, NB}`)
    - 필수 3개에 추가 section 결합 (예: `{EE, UD, NB, NN}`)
    - 필수 집합과 `NN`의 부분 혼합 (예: `{EE, NN}`)
    - 지원 목록(`EE | UD | NB | NN`) 밖의 section
    - 서로 다른 `item_seq` member 혼합, `expected_item_seq`와 불일치
  - 이 판정은 lifecycle gate와 artifact 읽기보다 먼저 수행하며, 실패 시 DB write transaction을 열지 않는다. `REQUEST_INVALID`는 요청 구조 결함만을 의미하고, 결속 위변조(`SOURCE_BINDING_INVALID`)나 정책 미지원(`CHUNK_POLICY_UNSUPPORTED`)과 혼용하지 않는다.
  - `NN` 섹션 처리 시, 7개 공식 ARTICLE 중 공식 빈 항목(`empty_article_titles`)이 1개라도 존재하는 NN member는 fail-closed 규칙에 따라 `CHUNK_POLICY_UNSUPPORTED` 예외로 거절/보류된다 (이 보류는 분리된 동일 품목의 EE/UD/NB 저장을 중단시키지 않음). 모든 ARTICLE에 본문이 존재하는 완전한 NN member인 경우에 한해 draft 생성 및 저장이 가능하다.

---

## 결정적 순서와 section별 exact page binding

### 결정적 순서 (`SECTION_ORDER` 재사용)

순서 기준을 새로 만들지 않고 기존 `ai_worker/tasks/rag/source_ingestion/mfds_label.py`의
`SECTION_ORDER = ("EE", "UD", "NB", "NN")`를 정본으로 재사용한다. 결과는 요청 `member_ids` 순서, DB 조회 순서,
SQL row 반환 순서에 의존하지 않는다.

다음 collection을 모두 `SECTION_ORDER` 인덱스 오름차순으로 정렬한다.

- `MaterializationSourceDocument` tuple
- `KnowledgeDocumentDraft` tuple
- 각 문서의 `KnowledgeChunkDraft` tuple (문서 내부는 `chunk_index` 오름차순)
- receipt의 `documents` 및 각 문서의 `chunks`
- exact replay 비교에 사용하는 기존 Document/Chunk collection

#### Receipt 동일성 비교 규칙

`KnowledgeMaterializationReceipt`는 영속화된 불변 canonical 영수증이며 `is_exact_replay` 필드를 포함하지 않는다.
실행 판정 결과는 상위 래퍼인 `KnowledgeMaterializationResult`의 `outcome: MaterializationOutcome` (`CREATED` 또는 `EXACT_REPLAY`) 및 헬퍼 프로퍼티 `is_exact_replay: bool`을 통해 제공된다.

따라서 동일 입력에 대한 반복 실행 시 `KnowledgeMaterializationReceipt`는 **모든 필드가 100% 완전히 동일**하다 (`res1.receipt == res2.receipt`).

| 필드 | 반복 실행 시 |
|---|---|
| `snapshot_id`, `source_code`, `source_version`, `snapshot_canonical_checksum`, `canonicalization_spec_version`, `item_seq`, `chunk_policy_version` | 동일 |
| `documents` 순서와 각 `MaterializedDocumentReceipt`의 `knowledge_document_id`, `source_snapshot_member_id`, `external_document_id`, `document_content_hash`, `locator` | 동일 (기존 UUID 재사용) |
| 각 `MaterializedChunkReceipt`의 `knowledge_chunk_id`, `chunk_index`, `content_hash`와 그 순서 | 동일 |

즉 "exact replay 시 영수증의 완전 일치(`result.receipt == previous_receipt`)"가 정본 계약이다.
순수 draft 단계(`KnowledgeDocumentDraft` / `KnowledgeChunkDraft`) 역시 전체 필드 동일성을 만족한다.

### section별 exact page binding

`RagSourceIngestionArtifact.page_number`는 MFDS ingestion이
`enumerate(plan.documents, start=1)`로 부여하며, `plan.documents`는 `SECTION_ORDER` 순서다. 따라서 page는 다음으로
고정된다.

| Section | Expected `page_number` |
|---|---|
| `EE` | 1 |
| `UD` | 2 |
| `NB` | 3 |
| `NN` | 4 |

`page_number >= 1`만 검증하지 않고 `page_number == SECTION_ORDER.index(section) + 1`을 정확히 검증한다.

기존 ingestion 경로(`_validate_plan()`)는 문서 집합을 `REQUIRED_SECTIONS` 또는 `SECTION_ORDER`로만 허용한다.
`NN` 단독 ingestion run은 존재할 수 없으므로 `NN` 단독 materialization 요청의 대상 artifact도 항상 4-artifact run의
page 4다. `NN` member가 page 1이면 결속 위변조로 거절한다.

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

## 청킹 및 렌더링 규약 (`mfds-label-knowledge-chunk@1`)

1. **단위 분할 (Chunking)**:
   - top-level `ARTICLE`은 정확히 하나의 청크로 매핑한다. nested `ARTICLE`은 분리하지 않고 부모 청크 내에서 heading 계층과 원래 순서를 유지한다.
   - `DOC` 직하의 연속된 non-ARTICLE 블록은 인접 ARTICLE과 합치지 않고 독립된 top-level 청크로 분리한다.
2. **표 렌더링 (Table Rendering)**:
   - 표는 Markdown 변형이 아닌 탭 구분 텍스트로 렌더링한다: row마다 개행(`\n`), cell마다 탭(`\t`).
   - caption, header, unit, footnote의 텍스트와 순서를 누락 없이 보존한다.
3. **텍스트 정규화 (Text Normalization)** — 이 정규화는 renderer/materialization 계층의 책임이며 parser seam이
   수행하지 않는다 (아래 "정규화 책임 경계" 참조):
   - Unicode NFC로 통일한다.
   - 줄바꿈은 LF(`\n`)로 단일화한다 (CRLF, CR 치환).
   - 각 줄의 끝 공백(trailing space)을 제거한다.
   - 연속된 빈 줄은 최대 1개로 축약한다.
   - 청크 전체의 앞뒤 공백을 strip한다.
4. **Byte-exact 렌더링 규약 (hash 결정성)**:

   `content_hash`가 `chunk_text`의 정확한 UTF-8 bytes에서 계산되므로, 아래 항목이 구현자 선택으로 남으면
   hash가 달라진다. 다음을 계약으로 고정한다.

   - **heading marker**: ARTICLE title은 `"#" * level + " " + title.strip()` 한 줄로 렌더링한다. `level`은
     chunk 자신의 top-level ARTICLE에서 1로 시작하고 nested ARTICLE마다 1 증가한다. Markdown 호환을
     목적으로 하지 않고 계층을 hash-stable하게 표현하기 위한 고정 marker다.
   - **title과 본문의 구분**: heading 줄 다음에 빈 줄 하나를 두고 본문을 시작한다 (블록 경계 `\n\n`).
   - **블록 경계**: ARTICLE / PARAGRAPH / table / nested ARTICLE 등 블록 단위는 `\n\n`으로 연결한다.
     정규화 후 빈 문자열이 되는 블록은 연결 대상에서 제외한다.
   - **mixed `text` / child / `tail` 결합**: 인라인 위치에서는 구분자를 넣지 않고 document order로
     `element.text` → 각 child의 렌더링 결과 → 그 child의 `tail`을 그대로 이어 붙인다.
     따라서 `<PARAGRAPH>앞<b>강조</b>사이<i>기울임</i>뒤</PARAGRAPH>`는 `앞강조사이기울임뒤`가 된다.
   - **`DOC` 직하 연속 non-ARTICLE block**: 최대 연속 구간(maximal run) 하나를 하나의 top-level chunk로
     묶고, 그 안의 블록들은 `\n\n`으로 연결한다. 인접 ARTICLE과 합치지 않는다.
   - **table 배치**: 표는 **블록 위치에서만 허용되며 항상 블록**이다. `PARAGRAPH` 안에 있든 `ARTICLE`
     바로 아래에 있든 앞뒤를 `\n\n`으로 분리한다 (Task 2a 구현 중 확인: 표를 인라인으로 두면 같은 표가
     위치에 따라 `\n` 또는 `\n\n`으로 갈려 hash가 달라진다. 일관성을 위해 블록으로 고정했고 G6 벡터를
     그에 맞춰 갱신했다). **인라인 요소 내부의 표**(예: `<b>` 안, 다른 표의 cell 안)는 블록으로 표현할
     위치가 없으므로 렌더링하지 않고 `CHUNK_POLICY_UNSUPPORTED`로 **fail-closed** 한다 (Task 2a 검증 중
     추가 확인: 인라인으로 이어 붙이면 같은 표의 `content_hash`가 중첩 깊이에 따라 달라진다. 실제 MFDS
     허가사항에서 관측되지 않은 구조이므로 표현을 추정해 만들지 않는다). 블록 위치 표의 내부는 다음
     순서로 **한 줄씩** 배치한다 —
     `caption` → 그 밖의 행을 **source order 그대로**. 각 행은 cell을 `\t`로 연결하고 행 사이는 `\n`이다.
     header(`th`)·단위 행·footnote 행에 별도 marker를 붙이지 않는다 (source order가 유일한 구분이다).
     표 앞뒤로 추가 빈 줄을 넣지 않는다 (블록 경계 규칙이 이미 `\n\n`을 준다).
   - **title-only ARTICLE**: 본문이 없으면 heading 줄만 남는다. 이는 빈 chunk가 아니므로 정책 위반이
     아니다. `NN` 섹션의 공식 빈 항목은 별도 fail-closed 규칙(아래 5절)을 따른다.
   - **정규화 적용 순서**: 위 규칙으로 문자열을 조립한 **뒤** ① NFC ② CRLF/CR → LF ③ 각 줄 trailing
     whitespace 제거 ④ 연속 개행 3개 이상 → 2개 ⑤ 전체 strip 순서로 적용한다. 순서를 바꾸면 결과가
     달라지므로 고정한다.

   #### Golden vector (구현 전 확정, TDD 고정값)

   아래 8개 case는 Task 2a RED 단계에서 그대로 테스트 fixture로 사용한다. `sha256`은
   `hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()`이며, 모든 `chunk_text`는 NFC다.

   | # | 구조 | XML 입력 (`DOC` 내부) | `chunk_index` | `chunk_text` (Python repr) | `content_hash` |
   |---|---|---|---|---|---|
   | G1 | 단일 ARTICLE + 본문 | `<ARTICLE title="효능"><PARAGRAPH>본문 첫째 줄</PARAGRAPH></ARTICLE>` | 0 | `'# 효능\n\n본문 첫째 줄'` | `3fa3c97b576b011666974e758257c94362eb529c5224f1f4859a0e53c16db439` |
   | G2 | nested ARTICLE heading 계층 | `<ARTICLE title="성인"><PARAGRAPH>상위 본문</PARAGRAPH><ARTICLE title="신기능 저하"><PARAGRAPH>하위 본문</PARAGRAPH></ARTICLE></ARTICLE>` | 0 | `'# 성인\n\n상위 본문\n\n## 신기능 저하\n\n하위 본문'` | `25867d2ff87b6c698cdbbe62ecb485137d364a0153bc4dc1168795ec6ea31c8d` |
   | G3 | mixed text/child/tail | `<ARTICLE title="효능"><PARAGRAPH>앞<b>강조</b>사이<i>기울임</i>뒤</PARAGRAPH></ARTICLE>` | 0 | `'# 효능\n\n앞강조사이기울임뒤'` | `fb6230b1bfd854615f7906f7846211faa7345dacad4f12ccfc3b14c9bc9f91b0` |
   | G4 | 연속 PARAGRAPH 블록 경계 | `<ARTICLE title="주의"><PARAGRAPH>첫 문단</PARAGRAPH><PARAGRAPH>둘째 문단</PARAGRAPH></ARTICLE>` | 0 | `'# 주의\n\n첫 문단\n\n둘째 문단'` | `51a413105e8a9bff0668ad77839ed56be4beeeba163711f1401794f7a2f33856` |
   | G5 | `DOC` 직하 연속 non-ARTICLE block | `<PARAGRAPH>머리말 1</PARAGRAPH><PARAGRAPH>머리말 2</PARAGRAPH><ARTICLE title="효능"><PARAGRAPH>본문</PARAGRAPH></ARTICLE>` | 0 | `'머리말 1\n\n머리말 2'` | `1d4f729c07f1b7d7f71e477d74efd742bf40dfca48514a940dee600b38a7de35` |
   | G5 | (같은 입력의 두 번째 chunk) | — | 1 | `'# 효능\n\n본문'` | `f73db0621f6d87acfe4ef930458d995cb2b430c07d61db1b0e0e71045a4cd473` |
   | G6 | table caption/header/unit/footnote | `<ARTICLE title="용량"><PARAGRAPH>표 참조<table><caption>1일 용량</caption><tr><th>구분</th><th>용량</th></tr><tr><td>단위</td><td>mg</td></tr><tr><td>성인</td><td>5</td></tr><tr><td>비고</td><td>식후</td></tr></table></PARAGRAPH></ARTICLE>` | 0 | `'# 용량\n\n표 참조\n\n1일 용량\n구분\t용량\n단위\tmg\n성인\t5\n비고\t식후'` | `148e4531bfff30d04bdab89022762685e05710f7a13098d8019975624b554011` |
   | G7 | title-only ARTICLE | `<ARTICLE title="효능만 있는 항목"></ARTICLE><ARTICLE title="효능"><PARAGRAPH>본문</PARAGRAPH></ARTICLE>` | 0 | `'# 효능만 있는 항목'` | `af1cff2ddbb08a042a407897df483ae946620ec1665d115063ea9b273560fc39` |
   | G7 | (같은 입력의 두 번째 chunk) | — | 1 | `'# 효능\n\n본문'` | `f73db0621f6d87acfe4ef930458d995cb2b430c07d61db1b0e0e71045a4cd473` |
   | G8 | 정규화 (NFD·CRLF·trailing space·빈 줄) | `<ARTICLE title="효능"><PARAGRAPH>{NFD("효능")}␠␠CRLF CRLF CRLF둘째 줄␠␠</PARAGRAPH></ARTICLE>` | 0 | `'# 효능\n\n효능\n\n둘째 줄'` | `d1263414708b6f50578042a193e28dbfe761af5c8986848d6cc717f76ebb3ccd` |

   - G5와 G7의 두 번째 chunk가 같은 `content_hash`를 갖는 것은 정상이다. `content_hash`는 `chunk_text`만의
     함수이고 natural key는 `(knowledge_document_id, chunk_index)`이므로 충돌이 아니다.
   - G8의 `␠␠`는 공백 2개, `CRLF`는 `\r\n`이다. NFD 입력이 NFC로, CRLF가 LF로, trailing space가 제거되고
     연속 빈 줄이 1개로 줄어든 결과가 위 `chunk_text`다.
   - 이 표의 값이 바뀌면 `mfds-label-knowledge-chunk@1`이 아닌 새 policy version이다. 값 변경은 policy
     version bump와 책임 리뷰어 승인을 함께 요구한다.
   - **미확정 아님**: 위 8개 벡터는 계약 값으로 확정했다. 다만 heading marker(`#`)와 table 배치는 공유
     계약 값이므로 `@phina-io` 승인 시점에 최종 확정된다. 승인 전 구현은 이 표를 변경하지 않는다.

5. **Fail-Closed 제약**:
   - 정규화 후 `chunk_text`가 빈 문자열이면 청킹 정책 위반으로 간주하여 `CHUNK_POLICY_UNSUPPORTED`를 발생시킨다.
   - `NN`(e약은요) 섹션에 공식 빈 항목(`empty_article_titles`)이 1개라도 존재하는 경우 fail-closed 규칙에 따라 `CHUNK_POLICY_UNSUPPORTED`로 처리를 보류한다.

---

## 정규화(Unicode NFC · newline) 책임 경계

Parser seam은 텍스트 정규화를 수행하지 않는다. `parse_mfds_label_artifact()`는 `ElementTree` root와 구조 통계만
반환하며 `_canonical_text()`를 적용하지 않는다.

| 계층 | 책임 |
|---|---|
| Parser seam (`parse_mfds_label_artifact`) | XML 구문 분석, section/title/body 구조 검증, 구조 통계 추출, parsed root 제공. **정규화 없음** |
| Task 2 renderer/materialization | text projection, newline 정규화(CRLF·CR → LF), Unicode NFC 정규화, canonical document text 생성, 해당 canonical text를 입력으로 하는 hash 생성 |

### 정규화가 hash에 미치는 영향 (domain별로 다르다)

정규화는 **chunk text projection domain에만** 영향을 준다. raw bytes domain과 document domain에는 영향을 주지
않는다. 둘을 하나의 "정규화하면 hash가 같아진다" 규칙으로 묶지 않는다.

| 값 | 입력 domain | NFD 입력과 NFC 입력에서 |
|---|---|---|
| Artifact `raw_sha256` | 원문 XML raw bytes | **달라야 한다** (raw bytes가 다르므로) |
| `SnapshotMember.content_sha256` | 동일 raw bytes | **달라야 한다** |
| `KnowledgeDocument.document_content_hash` | `member.content_sha256`의 복사 | **달라야 한다** |
| Snapshot `raw_manifest_checksum` / `canonical_checksum` | Snapshot 전체 manifest (Phase 2A 비소유) | Phase 2A가 규정하지 않는다 |
| 정규화된 `chunk_text` | parsed text의 NFC projection | **같아야 한다** |
| `KnowledgeChunk.content_hash` | 위 `chunk_text`의 UTF-8 bytes | **같아야 한다** |

- 기존 Index 어댑터 계약이 `document_content_hash == SnapshotMember.content_sha256`을 요구하므로
  `document_content_hash`는 정의상 raw bytes를 따른다. 정규화 결과가 같다는 이유로 이 값을 같게 만들면 계약
  위반이다.
- 따라서 "NFD/NFC 입력이 동일한 hash를 만든다"는 요구는 **`chunk_text`와 `content_hash`에만** 적용한다.
- `KnowledgeChunk.content_hash`는 **정규화 완료된** `chunk_text`의 UTF-8 bytes에서 계산한다. 정규화 이전 원문
  bytes에서 계산하지 않는다.
- Source raw bytes와 `raw_sha256`, `content_sha256`에는 NFC·trim·재직렬화를 적용하지 않는다.
- 같은 품목의 NFD 판본과 NFC 판본은 서로 다른 Snapshot member이며, 동일 natural key로 들어오면
  `document_content_hash`가 달라 exact replay가 아니라 `CONTENT_CONFLICT`다.
- 기존 `_canonical_element()` / `_canonical_text()`는 Snapshot `canonical_checksum` 산출용 구조 canonicalization이며
  chunk text projection과 같은 경계가 아니다. 두 경계를 하나의 함수로 합치지 않는다.
- 하위 Index 계층(`KnowledgeChunkIdentity.__post_init__`의 `_bounded_nfc`)은 identity 문자열이 **이미 NFC임을
  검증**하고 정규화해 주지 않는다. 따라서 NFC 보장 책임은 생산자인 Phase 2A에 있다.

---

## Parser 계층 격리 및 오류 변환 계약

하위 계층인 Source XML Parser(`ai_worker/tasks/rag/source_ingestion/mfds_label.py`)와 상위 계층인 Materialization Service(`ai_worker/tasks/rag/knowledge_materialization.py`)는 다음과 같이 엄격히 분리된다:

1. **Parser Seam의 독립성**:
   - `parse_mfds_label_artifact(raw_bytes: bytes, section: str) -> ParsedMfdsLabelDocument`는 상위 Materialization 계층 모듈이나 예외에 일절 의존하지 않는다.
   - seam의 산출물은 `section`, `document_title`, 구조 통계(`article_count`, `paragraph_count`, `nonempty_paragraph_count`), `content_status`, `empty_article_titles`, 그리고 `root: ElementTree.Element`(`field(repr=False)`)다. canonical structure 생성과 텍스트 정규화는 seam의 책임이 아니다.
   - XML 파싱 오류 발생 시 기존 parser 관례대로 표준 `ValueError`(`XML_SECTION_MISMATCH`, `XML_INVALID`, `XML_BODY_EMPTY`, `XML_ARTICLE_SET_INVALID`, `XML_SIZE_INVALID`, `XML_ENCODING_UNSUPPORTED`, `XML_DECLARATION_UNSAFE`, `UNSUPPORTED_SECTION`)만을 발생시킨다. `ElementTree.ParseError`는 parser 내부에서 포착되어 `ValueError("XML_INVALID")`로 변환된다.
   - XML `<DOC title="...">`이 누락되거나 빈 문자열이거나 섹션 타이틀과 불일치하는 경우, 기존 관례에 따라 `ValueError("XML_SECTION_MISMATCH")`를 발생시킨다 (새로운 error reason을 도입하지 않음).
   - NN 섹션에서 7개 필수 제목 세트와 불일치하는 경우, 기존 관례에 따라 `ValueError("XML_ARTICLE_SET_INVALID")`를 발생시킨다.
2. **상위 Service의 변환 책임**:
   - `materialize_documents()`는 parser 호출 범위에서 발생하는 모든 `ValueError`를 포착하여 `KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.PARSER_REJECTED)`로 안전 변환한다.
3. **Artifact Reader Typed Error 계약 (확정 및 구현 완료)**:
   - reader 계층의 예외를 문자열 파싱 없이 typed exception으로 포착하여 상위 계약 failure reason으로 1:1 매핑한다:
     - `RawArtifactIntegrityError` (checksum/size mismatch 등 무결성 훼손) → `KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH`
     - `RawArtifactUnavailableError` (파일 부재, I/O 실패, 권한 장애 등) → `KnowledgeMaterializationFailureReason.DEPENDENCY_ERROR`
     - `ArtifactObjectKeyError` (object key 포맷 또는 root 이탈) → `KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID`
     - `ArtifactPathTraversalError` (path traversal 공격 시도) → `KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID`
   - reader 생성자 검증 실패(root 권한, 디렉터리 부재 등 환경 결함)는 composition boundary(DI/진입점)에서 발생하며 요청 failure reason으로 감싸지 않는다.


---

## Lifecycle Persistence Gate 및 RAW_RESPONSE 결속 계약

구체화 트랜잭션 진입 시 다음 조건을 재검증하며, 하나라도 불만족 시 `SOURCE_NOT_ELIGIBLE` 또는 `SOURCE_BINDING_INVALID`로 즉시 거절한다:

- `RagSource.lifecycle_status == "ACTIVE"`
- `RagSourceEndpoint.lifecycle_status == "VERIFIED"` AND `runtime_status == "ENABLED"` AND `acquisition_status == "APPROVED"`
- `RagSourceOperation.runtime_status == "ENABLED"` AND `acquisition_status == "APPROVED"`
- `RagSourceSnapshot.verification_status == "CURRENT"`
- `RagSourceSnapshotMember.member_kind == "ARTIFACT"` AND `endpoint_id IS NULL` AND `operation_id IS NULL`
- `RagSourceIngestionRun.snapshot_id == RagSourceSnapshot.id` AND `RagSourceIngestionRun.operation_id == RagSourceOperation.id` (결속 위변조 차단)
- `RagSourceIngestionArtifact.id == RagSourceSnapshotMember.ingestion_artifact_id` AND `RagSourceIngestionArtifact.ingestion_run_id == RagSourceIngestionRun.id`
- **IngestionRun terminal success gate**:
  - 허용 상태는 정확히 `RagIngestionRunStatus.SUCCEEDED` **하나**다.
  - 근거 1: `persist_source_ingestion_result()`는 `SnapshotIngestionDecision.CREATED` 경로에서 run을 생성할 때
    `run_status = "SUCCEEDED_WITH_REJECTIONS" if metadata.rejected_record_count else "SUCCEEDED"`를 **생성 시점에 그대로**
    기록한다. 이 경로에는 `RUNNING → SUCCEEDED` 사후 전이가 없다.
  - 근거 2: **MFDS label 경로는 `_validate_metadata()`에서 `metadata.rejected_record_count == 0`을 강제한다**
    (`expected` tuple의 `0`, 불일치 시 `ValueError("MFDS_LABEL_METADATA_MISMATCH")`). 따라서 현재 실제
    MFDS artifact-producing 경로가 만들 수 있는 성공 상태는 `SUCCEEDED`뿐이다.
  - 그러므로 `SUCCEEDED_WITH_REJECTIONS`는 **허용하지 않는다.** enum에 존재한다는 사실만으로 허용 입력으로
    넓히지 않는다. 허용이 필요해지면 실제 MFDS 생성 경로의 변경 또는 책임 리뷰어의 명시적 계약 결정이
    선행되어야 한다 (아래 "미확정 접점" 참조).
  - `RUNNING`은 fail-closed로 거절한다. `enum` 기본값으로만 존재하며 MFDS snapshot 경로가 저장하는 값이 아니다.
  - `FAILED`는 거절한다. `FAILED` run도 artifact를 생성하지만 `snapshot_id IS NULL`이며(`chk_rag_ingestion_run_snapshot_status`)
    Snapshot member의 근거가 되지 못한다.
  - `NO_CHANGE`는 거절한다. `NO_CHANGE` run도 artifact를 생성하지만 그 run의 `snapshot_id`는 **직전 비교 Snapshot**을
    가리키며, 해당 run이 새로 만든 Snapshot member의 원본 run이 아니다. 이 상태를 허용 입력으로 넣을지는
    **미확정 보류**다. Phase 2A는 보수적으로 거절한다.
  - 상태 불일치는 새 failure reason을 만들지 않고 기존 `SOURCE_NOT_ELIGIBLE`을 사용한다.
- **RAW_RESPONSE 및 artifact identity 결속 검증 불변식**: 기존 `_member_artifact_metadata()`가 이미 강제하는 네 조건을
  재사용하고 중복 규칙을 새로 만들지 않는다.
  - (재사용) `RagSourceIngestionArtifact.storage_backend == "LOCAL_PRIVATE"` — `LOCAL_PRIVATE_STORAGE_BACKEND`
  - (재사용) `RagSourceIngestionArtifact.artifact_key == f"{RagSourceSnapshotMember.locator}.xml"`
  - (재사용) `RagSourceIngestionArtifact.raw_checksum == RagSourceSnapshotMember.content_sha256`
  - (재사용) `RagSourceIngestionArtifact.content_type == OBSERVED_CONTENT_TYPE`
    (= `"application/download; UTF-8; charset=UTF-8"`, 문자열 exact match)
  - (추가) `RagSourceIngestionArtifact.artifact_kind == "RAW_RESPONSE"`
  - (추가) `RagSourceIngestionArtifact.page_number == SECTION_ORDER.index(section) + 1` — `EE`=1, `UD`=2, `NB`=3, `NN`=4
  - (추가) `RagSourceIngestionArtifact.reject_code IS NULL AND RagSourceIngestionArtifact.parser_location IS NULL`
  - (추가) `RagSourceIngestionArtifact.object_key`가 기대 object key와 일치. `LOCAL_PRIVATE` backend의 규칙은
    저장소에 이미 존재하며 **`raw_checksum`에서 도출**된다 (locator가 아니다).

    ```text
    object_key == f"sha256/{raw_checksum[:2]}/{raw_checksum}.artifact"
    ```

    출처: `LocalPrivateSourceArtifactStore._object_key()`,
    `ai_worker/adapters/local_private_source_artifact_finalizer.py`의 동일 helper, 그리고
    `ai_worker/tasks/rag/source_cleanup/orphan_artifact.py`의 검증 정규식
    `re.compile(r"sha256/([0-9a-f]{2})/([0-9a-f]{64})\.artifact\Z")`.
    S3 backend(`S3PrivateSourceArtifactStore._object_key()`)는 `{prefix}/sha256/...` 형태로 prefix에
    의존하므로 규칙이 backend마다 다르다. Phase 2A는 `storage_backend == "LOCAL_PRIVATE"`만 허용하므로
    위 형태가 결정적이다.

    현재 이 규칙은 세 곳의 private staticmethod와 한 곳의 정규식에 **중복**되어 있다. 공용 pure helper로
    승격하는 것이 바람직하지만, 대상 파일이 Source writer·cleanup 소유 영역이므로 `AGENTS.md`의 소유권
    경계상 **owner 조율이 필요한 Task 3 리팩터 제안**으로 남긴다. Phase 2A가 네 번째 사본을 새로 만들지
    않는다.

    **검증 책임은 Task 3(DB provenance)에 둔다.** Task 2의 순수 도메인 커널은 DB row를 보지 않으므로
    `object_key` mutation을 거부할 위치가 아니다.
  - (추가) member locator의 section이 parser에 전달하는 section과 정확히 일치하고, locator의 `item_seq`가
    `expected_item_seq`와 일치
  - (추가) `RagSourceSnapshot.schema_version`, `parser_version`, `normalization_version`,
    `canonicalization_spec_version`이 read 시점 값과 lock 후 값에서 동일
  - 다른 품목(`item_seq`) 또는 다른 Snapshot Member의 artifact를 재사용하려는 요청은 거절한다.
  - 위 조건 중 하나라도 불만족이면 결속 위변조로 간주하여 `SOURCE_BINDING_INVALID`로 즉시 거절한다.
  - `page_number`는 DB에서 nullable(`RagSourceIngestionArtifact.page_number: int | None`)이다. `REJECTS` artifact는
    `page_number IS NULL`이므로 `None`도 negative 경로로 반드시 처리한다.

---

## 저장 및 트랜잭션 경계 계약

1. **동시성 직렬화 (Snapshot Advisory Lock — Materialization × Index 공통 참여)**:
   - 트랜잭션 시작 직후 Snapshot 단위의 advisory lock을 획득한다:
     `SELECT pg_advisory_xact_lock(hashtextextended(:key, 0)::bit(32)::bigint)`
   - `:key = f"knowledge-source-snapshot:{snapshot_id}"` (namespace `0`)
   - 복수 Snapshot을 다룰 경우 `snapshot_id`의 binary representation(`UUID.bytes`) 오름차순으로 정렬하여 lock을 획득함으로써 교착 상태(deadlock)를 원천 방지한다.
   - **Cross-flow 상호 배제**: 기존 Evidence Index 빌더(`SqlAlchemyKnowledgeEvidenceIndexRepository`)와 Materialization 저장소(`SqlAlchemyKnowledgeMaterializationRepository`) 양쪽 모두 동일한 snapshot advisory lock에 참여하므로, 동일 Snapshot에 대한 동시 쓰기/인덱스 구축이 완벽하게 직렬화된다. (Task 4 `test_d2_cross_flow_materialization_and_index_mutual_exclusion` 검증 완료)
2. **결속 잠금 (Row Lock) — Index flow와 정렬한 공통 순서 (확정 및 검증 완료)**:
   - Snapshot advisory lock으로 동일 Snapshot에 대한 cross-flow 동시 실행이 먼저 직렬화되며, 트랜잭션 내부의 row lock 순서는 Index flow와 일치시킨다:
   - 순서:
     1. `pg_advisory_xact_lock(hashtextextended('knowledge-source-snapshot:' || snapshot_id, 0)::bit(32)::bigint)` (UUID.bytes 순)
     2. member별 row lock:
        `FOR UPDATE OF rag_source, rag_source_endpoint, rag_source_operation, rag_source_snapshot,`
        `rag_source_snapshot_member, knowledge_document, knowledge_chunk`
     3. (ARTIFACT member) `_artifact_origin_lock_statement()`:
        `FOR UPDATE OF rag_source_ingestion_run, rag_source_ingestion_artifact`
     4. `rag_knowledge_index`: `FOR UPDATE OF rag_knowledge_index`
   - 따라서 두 flow가 공유하는 row의 **공통 순서**는 다음으로 고정한다.

     ```text
     rag_source
     → rag_source_endpoint
     → rag_source_operation
     → rag_source_snapshot
     → rag_source_snapshot_member
     → knowledge_document
     → knowledge_chunk
     → rag_source_ingestion_run
     → rag_source_ingestion_artifact
     ```

   - Phase 2A materialization write transaction은 이 테이블 순서를 그대로 따른다. Revision 8까지 기재했던
     `... snapshot → ingestion_run → ingestion_artifact → snapshot_member` 순서는 Index와 반대이므로 **폐기한다.**
   - 같은 request 안에서 여러 member를 다룰 때는 `SECTION_ORDER` 오름차순으로 member를 순회하여 **자기 자신의
     실행 간 순서만** 고정한다. 이것이 Index의 chunk-id 순회와 같은 row 획득 순서가 되지는 않는다.
   - lock 획득 전에 read-only artifact I/O(파일 읽기, checksum 재계산, draft 계산)를 **모두 완료**한다.
   - transaction 내부에서 원격/로컬 object read를 수행하지 않는다.
   - deadlock retry를 근거 없이 새로 도입하지 않는다. 순서 통일로 예방하고, 그래도 발생하면 fail-closed로 보고한다.
   - PostgreSQL은 `FOR UPDATE` 대상 테이블마다 UPDATE 권한을 요구한다. `knowledge_index_builder` role은
     `infra/python/knowledge_index_role_policy.py`의 `KNOWLEDGE_INDEX_LOCK_COLUMNS`에 따라 lock marker 컬럼만
     UPDATE 권한을 갖는다. 주의: `rag_source_snapshot`의 lock 전용 컬럼은 `knowledge_index_lock_marker`가 아니라
     `management_lock_marker`다. Phase 2A는 이 목록에 컬럼·테이블을 추가하지 않는다.
   - `knowledge_index_builder`는 `rag_source_snapshot_verification`에 SELECT 권한이 없다
     (`KNOWLEDGE_INDEX_SOURCE_READ_TABLES`에서 제외). 따라서 Snapshot 적격성은 verification 테이블이 아니라
     `RagSourceSnapshot.verification_status`로만 판정한다.
3. **Exact Replay 및 All-or-Nothing 불변성**:
   - `(source_snapshot_member_id, external_document_id)`로 기존 Document 및 하위 Chunk를 조회한다.
   - **Phase 2A 소유 불변 필드 전체 비교**:
     - Document level: `title`, `document_status`, `record_contract_version`, `canonicalization_spec_version`, `document_content_hash`, `external_document_id`, `locator`, `publisher IS NULL`, `source_url IS NULL`, `document_version IS NULL`, `knowledge_index_lock_marker == 0`, 그리고 하위 chunk 개수(`chunk_count`).
     - Chunk level: `chunk_index`, `chunk_text`, `content_hash`, `normalization_version`, `knowledge_index_lock_marker == 0`.
   - **legacy nullable 필드의 허용 의미**: `KnowledgeChunk.embedding_model`과 `KnowledgeChunk.vector_store_key`는
     Phase 2A가 소유하지 않는 **legacy nullable field**다. 현재 authoritative owner는 **미확정**이다.
     - 확인된 사실: 기존 `SqlAlchemyKnowledgeEvidenceIndexRepository`는 두 컬럼을 읽거나 쓰지 않는다.
       `knowledge_index_builder` role은 `knowledge_chunk`에 대해 `GRANT SELECT, INSERT`와
       `GRANT UPDATE (knowledge_index_lock_marker)`만 받으므로 두 컬럼을 UPDATE할 권한 자체가 없다
       (`infra/python/knowledge_index_role_policy.py`). 두 컬럼에 값이 있는 유일한 확인 사례는 migration 보존
       회귀(`tests/integration/rag/test_knowledge_evidence_index_postgresql.py`)의 `LEGACY_V1` row다.
     - 따라서 "후속 Index/Embedding 단계가 소유한다"고 단정하지 않는다.
     - Phase 2A insert 시 두 필드는 `NULL`이다.
     - exact replay에서 두 필드를 **갱신하지 않는다.**
     - 기존 non-NULL 값이 있으면 **보존한다.** 두 필드의 차이만으로 `CONTENT_CONFLICT`를 발생시키지 않고,
       비교 대상에서 제외한다.
     - owner가 확정될 때까지 Phase 2A에서 두 필드의 의미를 추정하거나 확장하지 않는다.
       (`vector_store_key`는 UNIQUE nullable이므로 임의 값 주입은 후속 단계를 차단할 수 있다.)
     - 반면 Document의 `publisher`, `source_url`, `document_version`은 Phase 2A가 쓰는 값이므로 소유 필드로 다룬다.
       값은 항상 `NULL`이어야 하고, 비교 시 `NULL`이 아니면 `CONTENT_CONFLICT`로 거절한다.
   - 위 모든 문서와 청크의 불변 필드가 요청 draft와 100% 일치하면 UPDATE/DELETE 없이 기존 UUID와 `created_at`을 보존하며 `is_exact_replay=True` 영수증을 반환한다.
   - 기존 레코드가 부분적으로만 존재하거나(일부 문서/청크 누락), 위 불변 필드 중 하나라도 상이한 경우 `CONTENT_CONFLICT` 예외를 발생시키고 전체 rollback한다.
   - 기존 레코드가 전혀 없는 경우 `KnowledgeDocument` 및 `KnowledgeChunk`를 원자적으로 일괄 INSERT한다.
4. **Snapshot identity·checksum race 검증 (재계산 아님)**:
   - Phase 2A는 `raw_manifest_checksum`과 `canonical_checksum`의 **생성 알고리즘을 소유하지 않는다.** 선택된 member
     집합만으로 두 checksum을 다시 계산하지 않는다. 이들은 Snapshot 전체(품목 전체 artifact manifest / 품목 전체
     canonical structure manifest) domain의 값이고, 부분 집합으로 재계산하면 정의상 달라진다.
   - Phase 2A가 확인하는 것은 **기존 Snapshot identity의 불변성**이다.
     1. 읽기 단계에서 Snapshot row의 식별자, lifecycle/status, checksum들을 read model로 확보한다.
     2. artifact를 읽고(read-only) draft를 계산한다.
     3. write transaction에서 위 공통 순서대로 동일 Snapshot row를 lock한다.
     4. lock 후 다시 읽은 다음 값을 pre-read 값과 비교한다.
        - `snapshot_id`
        - `verification_status` (및 관련 lifecycle/status)
        - source/operation 결속 (`source_id`, `operation_id`, `source_version`)
        - `raw_manifest_checksum`
        - `canonical_checksum`
        - `canonicalization_spec_version`
     5. 하나라도 달라졌으면 race / stale input으로 판단하여 fail-closed 처리하고 write 없이 rollback한다.
     6. `NN` 단독 요청에도 동일 원칙을 적용한다. 단일 member 요청이라는 이유로 Snapshot 전체 checksum 검증을
        생략하거나 축소하지 않는다.
   - pre-read 대비 변경은 `ARTIFACT_INTEGRITY_MISMATCH`로 보고한다. 실제 raw bytes 불일치와 같은 fail-closed
     의미이며 새 reason을 만들지 않는다.
5. **Receipt 재구성·비교는 commit 전에 같은 transaction 안에서 끝낸다**:
   - INSERT 또는 exact replay 판정 직후, **같은 transaction 안에서** row를 재조회해 receipt를 재구성하고
     생성 receipt와 비교한다. 불일치 시 `RECEIPT_MISMATCH`를 발생시키고 transaction 전체를 rollback한다.
   - 이 순서는 기존 Index 어댑터와 동일하다. `persist_complete_index()`는
     `async with session_factory() as session, session.begin():` 안에서 `_load_and_recompute_receipt()`를
     호출하고 `observed != receipt`이면 `RECEIPT_MISMATCH`를 raise하여 **commit 전에** 중단한다.
   - **Post-commit 확인은 rollback할 수 없다.** commit 이후에 발견한 불일치는 되돌릴 수 없으므로 계약상
     rollback 경로로 기술하지 않는다.
   - commit 이후의 새 session 재조회가 필요하다면 그것은 **rollback 불가능한 별도 audit**으로만 정의한다.
     audit 실패는 이미 commit된 row를 지우거나 되돌리지 않고, 관측 사실로 보고하며 후속 조치는 운영 판단에
     맡긴다. Phase 2A는 이 audit을 성공 조건으로 쓰지 않고, audit 실패를 `RECEIPT_MISMATCH`와 같은 의미로
     보고하지 않는다.
6. **DB 제약 준수**:
   - `KnowledgeDocument`: `record_contract_version = KNOWLEDGE_EVIDENCE_V1`, `document_status = ACTIVE`, `knowledge_index_lock_marker = 0`, `source_url = None`, `publisher = None`, `document_version = None` (nullable 선택값).
   - `KnowledgeChunk`: `normalization_version = "mfds-label-knowledge-chunk@1"`, `chunk_index >= 0`, `knowledge_index_lock_marker = 0`.
   - trigger, RLS policy, stored procedure, user-defined database function을 사용하지 않는다.

---

## 입력 및 출력 DTO 계약

### 입력 DTO (`KnowledgeMaterializationRequest`)
- `snapshot_id: UUID`
- `member_ids: tuple[UUID, ...]`
- `expected_item_seq: str` (9자리 숫자)
- `chunk_policy_version: str` (`"mfds-label-knowledge-chunk@1"`)

### 내부 출처 DTO (`MaterializationSourceDocument`)
```python
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
    ingestion_run_snapshot_id: UUID   # Invariant: == snapshot_id (SOURCE_BINDING_INVALID)
    ingestion_run_operation_id: UUID  # Invariant: == operation_id (SOURCE_BINDING_INVALID)
    ingestion_run_status: str         # Invariant: == "SUCCEEDED" (SUCCEEDED_WITH_REJECTIONS는 미허용)
    member_id: UUID
    member_kind: str
    locator: str
    content_sha256: str
    ingestion_artifact_id: UUID       # Invariant: ingestion_artifact_id == RagSourceIngestionArtifact.id
    artifact_key: str                 # Invariant: artifact_key == f"{locator}.xml"
    section: str                      # Invariant: locator의 마지막 segment와 동일, SECTION_ORDER의 원소
    artifact_kind: str                # Invariant: artifact_kind == "RAW_RESPONSE"
    page_number: int | None           # DB nullable. Invariant: SECTION_ORDER.index(section) + 1
    storage_backend: str              # Invariant: storage_backend == "LOCAL_PRIVATE"
    reject_code: str | None           # Invariant: reject_code is None
    parser_location: str | None       # Invariant: parser_location is None
    raw_checksum: str
    byte_size: int
    content_type: str
    object_key: str = field(repr=False)
```

- 위 provenance 필드(`ingestion_run_status`, `artifact_key`, `section`, `artifact_kind`, `page_number`, `storage_backend`, `reject_code`, `parser_location`)는 locked persistence gate가 재검증하는 값과 동일해야 하며, 하나라도 불만족 시 `SOURCE_BINDING_INVALID`(상태 부적격은 `SOURCE_NOT_ELIGIBLE`)로 거절한다.
- `page_number`는 DB 스키마와 동일하게 `int | None`으로 선언한다. `None`을 타입 수준에서 배제하지 않고 gate에서 fail-closed로 거절하며, negative test에 `None`을 포함한다.
- `artifact_key`를 포함함으로써 기존 `LocalPrivateSourceArtifactReader.read_verified(object_key=..., metadata=...)` 호출에 필요한 `RawArtifactMetadata(artifact_key, raw_checksum, byte_size, content_type)`를 이 DTO만으로 완전히 구성할 수 있다. Materialization 계층은 `RawArtifactMetadata` 필드를 요청자 입력이나 별도 조회로 보충하지 않는다.
- `object_key`는 `repr=False`를 유지하며, `artifact_key`는 member locator에서 도출되는 공개 좌표이므로 repr에 남긴다.

### 출력 영수증 (`KnowledgeMaterializationReceipt`)
```python
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

- `KnowledgeMaterializationReceipt`는 영속화된 canonical 영수증이며 실행 판정(`is_exact_replay`)을 포함하지 않는다. 실행 결과는 `KnowledgeMaterializationResult`를 통해 반환된다.
- `MaterializedDocumentReceipt`의 `locator: str = field(repr=False)` 및 영수증의 메타데이터 필드를 통해 후속 인덱스 단계의 `KnowledgeChunkIdentity`를 독립적으로 완전 구성할 수 있다.
- 영수증, 로그, 예외 메시지에는 로컬 호스트 경로, 내부 S3 `object_key`, 원본 비정규 XML 텍스트를 절대 노출하지 않는다.

---

## 공개 오류 계약 (reason-only)

자유형 message 인자를 갖는 `KnowledgeMaterializationError(reason, message=None)` 형태를 사용하지 않는다. 저장소의
기존 reason-only 관례(`KnowledgeEvidenceIndexValidationError`,
`SourceSnapshotMemberError` — 모두 `__init__(self, reason) -> None` / `super().__init__(reason.value)`)와 동일한 형태로
설계한다.

```python
class KnowledgeMaterializationError(Exception):
    def __init__(self, reason: KnowledgeMaterializationFailureReason) -> None:
        self.reason = reason
        super().__init__(reason.value)
```

보장 조건:

- 외부에 노출되는 문자열은 enum reason 값으로 한정한다.
- `object_key`, 로컬/호스트 파일 경로, DB detail, SQL text, 원문 XML, source text를 노출하지 않는다.
- `repr(error)`에도 민감정보가 없어야 한다.
- 하위 parser의 `ValueError` 메시지(`XML_SECTION_MISMATCH` 등)를 공개 오류 문자열로 그대로 전달하지 않는다.
  parser detail은 `PARSER_REJECTED`로만 투영한다.
- 내부 원인이 필요하면 exception chaining(`raise ... from exc`) 또는 내부 로깅 정책으로만 보존하고, 그 로깅에도
  민감정보를 넣지 않는다.

---

## 실패 이유 (Failure Reasons)

- `REQUEST_INVALID`: `item_seq` 형식 위반, `member_ids` 중복 또는 빈 튜플/형식 오류, 그리고 요청 section 집합이 정확한 `{EE, UD, NB}` 또는 정확한 `{NN}` 단독이 아닌 모든 경우(필수 section 누락, section 중복, 추가 section 결합, `EE+NN` 등 부분 혼합, 지원 밖 section, 서로 다른 품목 혼합) 등 요청 자체의 파라미터 구조 결함
- `SOURCE_BINDING_INVALID`: member–artifact–run–snapshot–operation 간 FK/식별자 결속 위변조, `artifact_kind != "RAW_RESPONSE"`, `page_number`가 section 기대값과 불일치하거나 `NULL`, reject metadata 존재, `artifact_key`/`object_key`/`storage_backend`/`content_type` 불일치, locator section·품목 불일치, 타 품목·타 Snapshot Member artifact 재사용
- `SOURCE_NOT_ELIGIBLE`: Source non-ACTIVE, Endpoint non-VERIFIED/ENABLED/APPROVED, Operation non-ENABLED/APPROVED, Snapshot non-CURRENT, IngestionRun `run_status`가 `SUCCEEDED` 이외(`SUCCEEDED_WITH_REJECTIONS`, `RUNNING`, `FAILED`, `NO_CHANGE`)
- `ARTIFACT_INTEGRITY_MISMATCH`: raw_checksum, raw size, member content_sha256 불일치, 그리고 pre-read 대비 lock 후 Snapshot identity·`raw_manifest_checksum`·`canonical_checksum`·`canonicalization_spec_version` 변경(race / stale input)
- `PARSER_REJECTED`: XML 파싱 불가, XML 선언 오류, 섹션/제목 불일치, 본문 비어있음, NN article set 불일치
- `CHUNK_POLICY_UNSUPPORTED`: 지원하지 않거나 인식할 수 없는 `chunk_policy_version` 요청, 정규화 후 공백 chunk 텍스트 발생, NN 공식 빈 항목(`empty_article_titles`) 존재 등 청킹 정책 미지원. 요청 구조·section 집합 오류는 이 코드가 아니라 `REQUEST_INVALID`로 유지한다.
- `CONTENT_CONFLICT`: 동일 자연 키에 상이한 내용/해시/버전/청크수 발견 또는 부분 저장 상태 발견
- `RECEIPT_MISMATCH`: **커밋 전 같은 transaction 안에서** 재조회한 결과와 생성 영수증 간 데이터/건수 불일치. 커밋 이후 audit에서 발견된 불일치는 rollback할 수 없으므로 이 reason으로 보고하지 않는다
- `DEPENDENCY_ERROR`: 데이터베이스 연결 오류 등 인프라 장애

---

## 실행 순서 (fail-closed 경계)

요청 검증 단계에서 아직 조회하지 않은 member의 section 집합을 이미 아는 것처럼 취급하지 않는다. 요청 자체의
문법·형식 검증과, 조회된 실제 member로 판정하는 section 집합 검증은 서로 다른 단계다.

1. 요청의 문법적·형식적 검증 — `snapshot_id`/`member_ids` UUID 형식, `member_ids` 비어있지 않음·중복 UUID 없음,
   `expected_item_seq`가 `^[0-9]{9}$`, `chunk_policy_version`이 지원 목록에 존재
2. read-only query로 Snapshot, Member, IngestionRun, Artifact read model 확보
3. **조회된 실제 member**의 section 집합 검증 — 정확한 `{EE, UD, NB}` 또는 정확한 `{NN}` 단독인지, 모든 member의
   locator 품목이 `expected_item_seq`와 같은지, 그리고 lifecycle/status·IngestionRun terminal status 검증
4. artifact locator와 identity 결속 검증 — `artifact_kind`, `artifact_key`, `object_key`, `storage_backend`,
   `content_type`, section별 exact `page_number`, `reject_code`/`parser_location` 부재
5. 기존 `LocalPrivateSourceArtifactReader.read_verified()`로 artifact 읽기 및 raw bytes 무결성 재확인
6. parser seam `parse_mfds_label_artifact()` 호출
7. deterministic renderer/chunker로 정규화·chunking을 수행해 draft 생성 (`SECTION_ORDER` 정렬)
8. 짧은 write transaction 시작 (advisory lock namespace 1)
9. 정해진 공통 순서로 row lock — `rag_source → rag_source_endpoint → rag_source_operation → rag_source_snapshot →
   rag_source_snapshot_member → knowledge_document → knowledge_chunk → rag_source_ingestion_run →
   rag_source_ingestion_artifact`
10. pre-read identity/checksum과 locked row 재검증
11. exact replay 또는 원자적 일괄 insert, 그리고 **같은 transaction 안에서** receipt 재구성·비교
    (불일치 → `RECEIPT_MISMATCH` + rollback)
12. commit
13. 11단계에서 검증된 receipt 반환. commit 이후의 새 session 재조회는 rollback 불가능한 선택적 audit이며
    성공 조건이 아니다

1~7 단계에서 실패하면 write transaction을 열지 않는다. 8~11 단계(receipt 재구성·비교 포함)의 실패는 전체
rollback한다. **12단계 commit 이후에는 rollback이 불가능하므로**, commit 이후에 수행하는 확인은 13단계의
rollback 불가능한 audit이며 성공 조건이 아니다.

---

## 미확정 접점 (명시적 보류)

다음은 이 계약이 값을 추정하지 않고 보류하는 항목이다. 해당 접점을 구현·활성화하기 전에 결정권자 확인이 필요하다.

| 항목 | 확인된 사실 | 추정하지 않은 내용 | 필요한 결정 | 해소 시한 |
|---|---|---|---|---|
| `KnowledgeChunk.embedding_model` / `vector_store_key` authoritative owner | 기존 Index adapter가 읽지·쓰지 않고, `knowledge_index_builder`에 UPDATE 권한이 없다. `LEGACY_V1` row에만 값이 관측된다 | 후속 Index/Embedding 단계가 소유한다는 단정 | owner와 write 경로 확정 (Backend·DB 책임 리뷰어) | Phase 2B 착수 전 |
| Snapshot Advisory Lock 동시성 직렬화 | Snapshot 단위 advisory lock(`knowledge-source-snapshot:{snapshot_id}`, namespace 0) 도입 및 Materialization × Index 공통 참여 | 상이한 namespace 분리 | **해결 완료**: 동일 lock 참여 및 cross-flow 상호 배제 검증 완료 | Task 4 완료 |
| Artifact Reader Typed Error 계층 | `RawArtifactIntegrityError`, `RawArtifactUnavailableError`, `ArtifactObjectKeyError` 도입 | 문자열 파싱 기반 오류 판정 | **해결 완료**: typed exception 1:1 매핑 구현 및 검증 완료 | Task 3 완료 |
| Commit 이후 audit의 위치 | commit 후 재조회는 독립 audit 함수 `audit_post_commit() -> bool`로 구현, rollback 불가능 | audit 실패 시 commit된 row rollback | **해결 완료**: boolean 독립 감사로 확정 완료 | Task 3 완료 |
| `NO_CHANGE` run 및 `SUCCEEDED_WITH_REJECTIONS` | MFDS label 경로는 단일 성공 상태 `SUCCEEDED`만을 생성하므로 그 외 상태는 부적격 | 허용 입력으로의 확장 | **해결 완료**: fail-closed 거절 유지 | Task 3 완료 |
| `NN` 공식 빈 ARTICLE의 저장 표현 | parser가 `PARTIAL_OFFICIAL` / `empty_article_titles`로 보존한다 | 합성 문구·빈 chunk 저장 | **해결 완료**: `CHUNK_POLICY_UNSUPPORTED` fail-closed 보류 | Task 2 완료 |
| 실제 서버 artifact mount·builder credential 적용 | #593/#613 runbook에 목표 경로가 기록되어 있다 | 공용 환경에 실제 provision 완료 | 환경 담당 확인 (#591/#593/#613 gate) | 실제 persistence 실행 전 |

---

## Current 승격과 후속 조건

이 Proposed 계약은 해당되는 schema·migration·adapter·자동 테스트와 단일 책임 리뷰어(`@Jye-rookie`) 승인 및 Backend·DB·Security(`@phina-io`) 전문 검토 근거가 같은 PR에서 확인되기 전 `Current`로 승격할 수 없다. (Phase 2A는 기존 Knowledge 스키마를 재사용하므로 신규 migration을 포함하지 않는다.) 승격 이후에도 후속 Knowledge Evidence Index 및 Search, Release Gate 통과 전까지 Track F를 외부에 공개할 수 없다.
