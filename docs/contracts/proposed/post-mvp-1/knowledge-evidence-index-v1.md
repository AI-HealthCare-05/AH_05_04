# Knowledge Evidence Index 계약 v1

## 상태와 범위

- 상태: Proposed · 구현 브랜치 검증 중 · Current 아님
- 추적: Issue #178의 선행 저장 기반. 이 계약만으로 #178을 완료하지 않는다.
- 구현 담당: 정현우 (`@ceohwj`)
- 책임 리뷰어: Backend·DB·Security 송은영 (`@phina-io`) 1명
- 전문 검토 근거: Evidence·Scope·Safety 권가빈 (`@hazelnutflavoured`), Source provenance 김지혜
  (`@Jye-rookie`)의 의견 또는 승인 근거를 첨부하되 추가 필수 PR 리뷰어로 지정하지 않는다.
- 공개 상태: `PUBLIC_TRACK_F=false` 유지

이 계약은 승인된 Source Snapshot에 결속된 canonical Knowledge Chunk와 모델별 embedding을 PostgreSQL에
불변 버전으로 저장하는 내부 Worker 계약이다. 수집, 파싱, chunking, embedding provider 호출, lexical/dense
조회, RRF, rerank, Evidence Gate, Retrieval Run, 답변 생성과 공개 Citation은 범위 밖이다. OCR Candidate
Index는 별도 데이터 도메인이며 이 계약의 입력이나 저장소로 사용할 수 없다.

## 입력 계약

`KnowledgeIndexBuildRequest`는 다음 필드를 가진다.

| 필드 | 형식 | 조건 |
| --- | --- | --- |
| `index_code` | string | NFC, 비어 있지 않음, 최대 120자, 공백·제어문자 없음 |
| `index_version` | string | NFC, 비어 있지 않음, 최대 80자, 공백·제어문자 없음 |
| `embedding_model_ref` | string | NFC, 비어 있지 않음, 최대 255자, 공백·제어문자 없음 |
| `embedding_model_version` | string | NFC, 비어 있지 않음, 최대 80자, 공백·제어문자 없음 |
| `embedding_dimension` | integer | 1 이상 2000 이하 |
| `distance_metric` | enum | `COSINE`만 허용 |
| `members` | non-empty tuple | 동일 stable coordinate, Chunk UUID 또는 동일 `(source_snapshot_id, evidence_key)` 중복 금지 |

각 member는 다음을 포함한다.

- `knowledge_chunk_id`, `source_snapshot_id`, `source_snapshot_member_id`: UUID
- `evidence_key`: 외부 authoritative caller가 제공한 opaque 식별자. 비어 있지 않은 NFC 문자열, 최대 300자
- `source_code`, `source_version`, `external_document_id`: 비어 있지 않은 NFC 식별자
- `canonical_checksum`, `content_hash`: 64자리 lowercase hexadecimal SHA-256
- `chunk_index`: 0 이상의 정수
- `locator`: 비어 있지 않은 NFC 문자열, 최대 500자
- `content_text`: 일반 표현과 예외에서 항상 `<redacted>`인 민감값 래퍼
- `embedding`: 요청 dimension과 길이가 같은 tuple

`content_hash`는 `SHA-256(content_text UTF-8 bytes)`와 정확히 같아야 한다. 저장 경계는 텍스트를 묵시적으로
정규화하지 않는다. embedding의 각 값은 IEEE-754 binary32로 표현 가능한 finite number여야 하며 negative
zero를 허용하지 않고 전체 벡터의 Euclidean norm은 0보다 커야 한다.

stable coordinate는 아래 4-tuple이다.

```text
(source_code, source_version, external_document_id, chunk_index)
```

동일 coordinate의 content가 바뀌면 새 candidate가 아니라 binding 실패다. 바뀐 근거는 새
`source_version`으로 입력해야 한다.

## 정규 hash 계약

JSON hash는 저장소의 RFC 8785 호환 JCS 구현을 사용한다. 입력 문자열에 Unicode 정규화를 묵시적으로
적용하지 않고, object key는 UTF-16 순서로 정렬하며, float와 안전 범위 밖 integer를 거절한다. 별도 언급이
없는 manifest member는 stable coordinate의 UTF-8 byte 순으로 정렬한다.

### Corpus manifest

projection version은 `knowledge-evidence-corpus-manifest@1`이다. 아래 object의 배열을 JCS 직렬화한 UTF-8
bytes에 SHA-256을 적용한다. locator, UUID, Source Snapshot checksum, raw text, embedding은 포함하지 않는다.

```json
{
  "chunk_index": 0,
  "content_hash": "<64-lower-hex>",
  "evidence_key": "<opaque-authoritative-key>",
  "external_document_id": "<id>",
  "source_code": "<code>",
  "source_version": "<version>"
}
```

### 개별 embedding

projection version은 `knowledge-evidence-embedding@1`이다. 아래 bytes를 정확히 이어 붙여 SHA-256을
적용한다.

```text
UTF-8("knowledge-evidence-embedding@1\n")
+ uint32-big-endian(dimension)
+ 각 원소를 순서대로 IEEE-754 binary32 big-endian 인코딩한 bytes
```

### Embedding manifest

projection version은 `knowledge-evidence-embedding-manifest@1`이다. 각 stable coordinate와 해당
`embedding_sha256`을 담은 배열을 JCS 직렬화해 SHA-256을 적용한다.

### Index configuration

projection version은 `knowledge-evidence-index-configuration@1`이다. model ref/version, dimension,
`COSINE`, corpus·embedding projection version, 두 manifest hash를 JCS object로 직렬화해 SHA-256을
적용한다. raw text, vector, locator는 포함하지 않는다.

## 저장 계약

- `rag_source_snapshot_member`는 `ENDPOINT_OPERATION` 또는 `ARTIFACT` 중 한 origin shape만 가진다.
- Source Writer는 Snapshot이 아직 `PENDING`이고 verification seal이 없을 때만 member를 추가한다. Snapshot이
  `CURRENT`가 된 뒤에는 member를 추가하지 않으며, 기존 CURRENT Snapshot을 수용하려면 새 Snapshot version을
  수집·검증한다. Index build는 반대로 `CURRENT` Snapshot member만 허용한다.
- 기존 `knowledge_document`와 `knowledge_chunk`는 `LEGACY_V1`을 보존한다. migration은 기존 row를 승인된
  근거로 추론하거나 `KNOWLEDGE_EVIDENCE_V1`로 승격하지 않는다.
- `KNOWLEDGE_EVIDENCE_V1` document/chunk는 Source Snapshot member, external document ID, canonicalization
  version, content hash와 normalization version이 모두 있어야 한다.
- `rag_knowledge_index`는 완성된 불변 index receipt이며, `rag_knowledge_index_member`가 Chunk와 vector를
  결속한다. 같은 row의 `evidence_key`가 정확한 `(knowledge_index_id, knowledge_chunk_id)` binding을 소유한다.
  새로 materialize한 row의 `evidence_key`는 non-null/nonblank이고, 같은 index 안에서
  `(source_snapshot_id, evidence_key)`는 unique다. citation 정본 anchor의 scope를 넓히지 않으며, 서로 다른
  snapshot의 같은 opaque key는 별도 binding이다. draft 또는 부분 index row는 공개하지 않는다.
- builder는 `CHUNK_UUID=EVIDENCE_KEY` binding을 명시 입력으로 받아 exact chunk set과 snapshot/key anchor uniqueness를
  검증한다. chunk UUID, rank, hash 또는 Source coordinate에서 key를 생성하거나 보정하지 않는다.
- 기존 member row에 authoritative key가 없는 상태는 migration에서 추론·backfill하지 않는다. populated upgrade는
  legacy row를 nullable binding으로 보존하되 runtime reader가 그 row를 fail closed한다. authoritative rebuild가
  non-null binding을 적재한 뒤 zero-null을 확인하기 전에는 legacy row를 공개하지 않는다. binding을 잃는 populated
  downgrade는 fail closed한다.
- 한 transaction 안에서 parent chain을 재검증하고 index와 모든 member를 기록한 뒤 persisted receipt를
  재계산한다. 실패하면 전체 rollback한다.
- 동일 `index_code + index_version` 재요청은 모든 configuration과 세 hash가 같은 경우에만 idempotent다.
- 이 선행 slice의 builder adapter는 update/delete 인터페이스를 제공하지 않는다. 전용
  `KNOWLEDGE_INDEX_BUILDER_USER`는 Source·Knowledge parent SELECT, Knowledge document/chunk와 Index/member
  SELECT·INSERT, 그리고 `CHECK = 0` 고정 lock-marker 컬럼의 UPDATE만 가진다. Runtime은 Knowledge
  document/chunk와 Index/member에 SELECT만 갖는다. Builder 환경변수를 비워 두면 이 역할과 Runtime read
  권한은 활성화되지 않는다.
- PostgreSQL의 `SELECT ... FOR UPDATE` 권한 조건을 충족하는 lock-marker는 provenance, 상태, revision 또는
  공개 의미가 없는 고정 기술 컬럼이며 CHECK가 0 이외의 값을 거부한다.
- trigger, RLS policy, stored procedure, user-defined database function을 사용하지 않는다.

## 출력 계약

`KnowledgeIndexReceipt`는 다음만 반환한다.

- index code/version
- corpus manifest hash
- embedding manifest hash
- index configuration hash
- embedding model ref/version
- embedding dimension와 distance metric
- member count

receipt, 일반 로그, exception과 audit에는 raw text, vector, locator, endpoint URL, storage key, Source body,
환자 query·처방·OCR·chat content를 넣지 않는다.

## 실패 이유

외부 예외 표현은 상세 입력을 포함하지 않고 다음 고정 reason만 제공한다.

- `REQUEST_INVALID`
- `SOURCE_BINDING_INVALID`
- `CONTENT_HASH_MISMATCH`
- `EMBEDDING_INVALID`
- `RECEIPT_MISMATCH`
- `VERSION_CONFLICT`
- `DEPENDENCY_ERROR`

이 reason들은 build-time 결과이며 Retrieval, Evidence, Safety 또는 Release 상태를 대체하지 않는다.

## Current 승격과 후속 조건

이 Proposed 계약은 schema·migration·adapter·자동 테스트와 지정 리뷰 승인이 같은 PR에서 확인되기 전
Current로 승격할 수 없다. 승격 이후에도 #178의 lexical/dense Retrieval, deterministic RRF, reranker,
Evidence Gate, Retrieval Run과 RAG evaluation을 별도로 구현·검증해야 하며 외부 승인 게이트가 닫힌 동안
Track F를 공개할 수 없다.
