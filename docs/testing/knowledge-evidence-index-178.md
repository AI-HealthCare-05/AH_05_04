# #178 Knowledge Evidence Index 선행 기반 검증 기록

## 판정

- 범위: #178 Production Hybrid Retrieval의 선행 Knowledge Evidence Index 저장 기반
- 상태: 로컬 구현·검증 완료, 지정 리뷰·병합 대기
- 계약: `docs/contracts/proposed/post-mvp-1/knowledge-evidence-index-v1.md`
- migration: `178a1b2c3d4e` (`166f30415263` 다음 단일 head)
- 공개: `PUBLIC_TRACK_F=false`; Current 승격·Production 활성화 아님

권위 문서의 `PD-315` 리뷰 상태 표시는 이 구현 브랜치에서 변경하지 않았다. 해당 메타데이터 정리는 지정
리뷰어가 승인 근거와 함께 별도로 확정해야 하며, 이 검증 기록은 승인 상태를 대신하지 않는다.

## 구현 경계

- unsealed `PENDING` Source Snapshot member의 Endpoint/Operation·Artifact origin shape와 parent-chain 잠금;
  `CURRENT` 전이 뒤에는 추가 금지
- 기존 Knowledge row의 `LEGACY_V1` 보존과 production provenance 확장
- `rag_knowledge_index`·`rag_knowledge_index_member` 완성 artifact 원자 저장
- RFC 8785 호환 corpus/embedding/configuration receipt와 persisted 재계산
- PostgreSQL transaction advisory lock을 통한 동일 code/version 생성 직렬화
- pgvector 0.8.6 server와 `pgvector==0.5.0` SQLAlchemy `VECTOR` 변환
- Production admin bootstrap의 extension 선설치와 제한 Alembic migration role 분리
- 선택형 전용 Knowledge Index Builder와 Runtime SELECT-only 권한; 고정 lock-marker 외 업무 UPDATE 금지
- raw text/vector/locator를 receipt·일반 로그·고정 예외 reason에서 제외

SQLAlchemy `VECTOR` 타입이 bind/result 변환을 소유한다. 실제 PostgreSQL 검증에서 raw asyncpg codec을 함께
등록하면 문자열 변환이 중복되어 실패함을 확인했으므로 이 고정 조합은 codec을 별도로 등록하지 않는다.

## 2026-09-13 실행 증빙

| 검사 | 결과 |
| --- | --- |
| AI Worker 기본 lane (Core·OCR·RAG·Evaluation) | 3147 passed, 8 skipped |
| Backend·Contract·선별 RAG Integration 기본 lane | 1963 passed, 85 skipped |
| 전체 Migration suite | 212 passed, 4 skipped |
| 전체 Contract suite (Docker image build 포함) | 273 passed |
| 기존 DB 역할 bootstrap/provision/redeploy 회귀 | 1 passed |
| 독립 DB 전체 Alembic upgrade + vector round-trip/concurrency/conflict/dimension/downgrade/legacy/non-leakage | 3 passed |
| 전용 Builder 실행 + Runtime read-only + 업무 UPDATE/DELETE/TRUNCATE 거부 | 1 passed |
| #178 model/migration metadata와 PostgreSQL 통합 묶음 | 10 passed |
| Builder 미설정/설정 Production bootstrap SQL | 모두 성공 |
| 전체 빈 DB `alembic upgrade head` | 성공, head `178a1b2c3d4e` |
| populated Knowledge Evidence Index downgrade | 의도대로 중단: `downgrade would lose Knowledge Evidence Index data` |
| legacy row upgrade | `LEGACY_V1` backfill, publisher/source URL/document version와 external vector key 보존 |
| legacy-only row downgrade | 성공, 기존 row 보존 |

모든 DB fixture는 비식별 합성 데이터만 사용했다. 임시 PostgreSQL은 기존 개발 DB와 분리된 port와 database를
사용했다.

## 2026-09-14 PostgreSQL Evidence Search 및 결정적 RRF 검증 기록

- 범위: #178 PostgreSQL Knowledge Evidence Search (Exact, Trigram, FTS lexical 20 및 pgvector cosine dense 20) + `rrf-rank-fusion@1` 결정적 RRF 구현
- 계약: `docs/contracts/proposed/post-mvp-1/knowledge-evidence-search-rrf-v1.md`
- migration: `178b1c2d3e4f` (`knowledge_chunk`에 `ix_knowledge_chunk_fts_simple`, `ix_knowledge_chunk_chunk_text_trgm` GIN 인덱스 추가)
- 상태: 로컬 구현·검증 완료, 지정 리뷰·병합 대기 (`Part of #178`, `PUBLIC_TRACK_F=false`)

| 검사 | 대상 | 결과 |
| --- | --- | --- |
| 단위 테스트: 순수 RRF 연산 | `ai_worker/tests/rag/test_evidence_rank_fusion.py` | 9 passed (`rrf-rank-fusion@1`, exact Fraction, tie-break, bucketed fusion, Top 30/20) |
| 단위 테스트: 프로토콜·설정·검증 | `ai_worker/tests/rag/test_evidence_search.py` | 8 passed (쿼리 유효성, `SensitiveText`/`Vector` redaction, 18자리 점수 포맷, JCS hash) |
| 통합 테스트: PostgreSQL 실환경 | `tests/integration/rag/test_postgresql_evidence_search.py` | 7 passed (Exact 우선순위, Trigram/FTS 조회, Cosine dense, fail-closed 무결성, threshold cleanup, EXPLAIN plan) |

## 남은 #178 범위

이 구현은 Knowledge Evidence Search 및 RRF 융합 계층이며, 다음은 후속 #178 구현·검증으로 남는다.

- versioned reranker (`EvidenceRerankPort`) 구현 및 입력 20 연동
- Evidence Gate와 context 최대 5
- authoritative Retrieval Run/signal/hit persistence
- Runtime Bundle/currentness 연결, `hybrid_retrieve` node receipt
- `RET-L`, `RET-D`, `RET-H`, `RET-HR` Evaluation과 Recall@5/latency 증빙
- 단일 책임 리뷰어 송은영의 코드·DB·Security 승인과 Evidence/Safety·Source 전문 검토 근거
- Proposed 계약의 Target/Current 승격 판단
